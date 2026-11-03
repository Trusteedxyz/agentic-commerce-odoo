"""Trusteed CEL — Agent token verifier for Odoo 18.

Verifies the ``_trusteed_agent_token`` JWS Compact attached to sale.order
records created by an agentic checkout flow.

Verification order:
  1. Parse JWS header + payload (fail fast on malformed input).
  2. Enforce alg=EdDSA, typ=trusteed-agent-token+jwt.
  3. Resolve signing key from caller-supplied agent_did_resolver dict.
  4. Verify Ed25519 signature.
  5. Validate JWT claims: aud, merchantId, exp (+30s skew), window ≤330s, iat (≤30s future, ≤300s old),
     checkoutIntentHash binding.
  6. Nonce replay detection (in-process dict, TTL 300s).

Thread-safety: Odoo workers use gevent; plain dict mutations are safe.
"""

import base64
import hashlib
import json
import logging
import re
import time
from typing import Optional

_logger = logging.getLogger(__name__)

# B5 (2026-10-01) — formato del identificador de un solo uso. El contrato
# CANÓNICO es el claim `nonce` (`AgentTokenPayloadSchema`: string 16..64;
# el verificador del servidor consume `payload.nonce`). `jti` sólo se acepta
# como ALIAS HEREDADO cuando `nonce` está ausente, con los MISMOS límites. El
# juego de caracteres es el que exige el endpoint de consumo del servidor
# (`agent-events-nonce.routes.ts`), así un identificador que éste rechazaría
# con 400 se descarta aquí en vez de acabar como INDETERMINADO.
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# sha256(agentDid + nonce) → expire_ts
_NONCE_CACHE: dict[str, float] = {}

_MAX_TOKEN_WINDOW_SECONDS = 330
_EXP_CLOCK_SKEW_SECONDS = 30
# L2 (Task 43b) — misma política de `iat` que el verificador de WooCommerce
# (`class-token-verifier.php`): un `iat` más de 30 s en el futuro es inválido
# (con un tope de ventana, un `iat` adelantado daría un token de vida deslizante)
# y el token no puede ser más viejo que `_MAX_TOKEN_AGE_SECONDS` desde su `iat`.
#
# Decisión 48 (2026-10-02): el MISMO número en todos los verificadores — los
# 300 s que declara el contrato (`AgentTokenPayloadSchema`: `exp - iat <= 300`).
# Aquí y en WooCommerce eran 120 s, y los agentes emiten sus propios tokens con
# hasta 300 s de vida: un tope menor rechazaba tokens legítimos que el servidor
# y la Shopify Function aceptan. Atado entre lenguajes por
# packages/shared/src/enforcement/__tests__/agent-token-max-age-parity.test.ts.
_IAT_FUTURE_SKEW_SECONDS = 30
_MAX_TOKEN_AGE_SECONDS = 300
_NONCE_TTL_SECONDS = 300


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _evict_expired_nonces(now: float) -> None:
    expired = [k for k, exp in _NONCE_CACHE.items() if exp <= now]
    for k in expired:
        del _NONCE_CACHE[k]


def verify_agent_token(
    jws: str,
    checkout_intent_hash: str,
    merchant_id: str,
    agent_did_resolver: dict[str, dict],
) -> Optional[dict]:
    """Verify a _trusteed_agent_token JWS Compact Serialization.

    Parameters
    ----------
    jws:
        Raw JWS Compact (header.payload.signature).
    checkout_intent_hash:
        SHA-256 hex of the RFC 8785 canonical order context.
        Must match the checkoutIntentHash claim.
    merchant_id:
        Trusteed merchant UUID for this Odoo company.
    agent_did_resolver:
        {agent_did: {'x': '<base64url-ed25519-pubkey>'}}.
        No remote resolution fallback — callers must pre-populate from
        the rule snapshot or JWKS endpoint.

    Returns
    -------
    dict or None
        Success: {'agentId': str, 'trustScore': float|None, 'platform': str|None}.
        Failure: None (caller applies fallback policy).
    """
    # Step 1 — Parse compact JWS
    parts = jws.split(".")
    if len(parts) != 3:
        _logger.debug("CEL token verify: not a 3-part JWS")
        return None
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        _logger.debug("CEL token verify: header/payload decode failed")
        return None

    # GHSA-2j2x-5q52-g48m companion hardening (D2): json.loads() above only
    # guarantees VALID JSON, not an object. A syntactically valid JWS whose
    # header/payload decode to a JSON array or a bare scalar (attacker
    # controls both — no signature check has run yet) made the .get() calls
    # below raise AttributeError instead of this function returning None.
    # The caller (sale_order_enforcement.py) treats any exception here as
    # *our* code breaking and applies the merchant's most lenient configured
    # fallback mode — the same fail-open class as a malformed signature,
    # reached a different way.
    if not isinstance(header, dict) or not isinstance(payload, dict):
        _logger.debug("CEL token verify: header/payload is not a JSON object")
        return None

    # Step 2 — Header claim checks
    # RFC 7515 §4.1.11: quien no entiende una extensión crítica debe rechazar el
    # JWS, y este verificador no entiende ninguna: ``crit`` presente (incluso
    # ``[]``, que el RFC prohíbe) invalida el token. Antes de resolver la clave.
    if "crit" in header:
        _logger.debug("CEL token verify: crit header present")
        return None
    if header.get("alg") != "EdDSA":
        _logger.debug("CEL token verify: unexpected alg=%s", header.get("alg"))
        return None
    if header.get("typ") != "trusteed-agent-token+jwt":
        _logger.debug("CEL token verify: unexpected typ=%s", header.get("typ"))
        return None
    kid = header.get("kid", "")
    if not isinstance(kid, str):
        _logger.debug("CEL token verify: kid claim is not a string")
        return None
    agent_did = kid.split("#")[0] if "#" in kid else kid
    if not agent_did:
        _logger.debug("CEL token verify: empty kid/agent_did")
        return None

    # Step 3 — Key resolution
    jwk = agent_did_resolver.get(agent_did)
    if not isinstance(jwk, dict) or not jwk.get("x"):
        _logger.debug("CEL token verify: agent DID %s not in resolver", agent_did)
        return None
    # Task 21: the resolver now carries kty/crv when the snapshot does; a key
    # that declares another type is not an Ed25519 key, whatever its length.
    if jwk.get("kty", "OKP") != "OKP" or jwk.get("crv", "Ed25519") != "Ed25519":
        _logger.debug("CEL token verify: agent DID %s key is not OKP/Ed25519", agent_did)
        return None
    try:
        pubkey_bytes = _b64url_decode(jwk["x"])
    except Exception:
        return None
    if len(pubkey_bytes) != 32:
        _logger.warning("CEL token verify: unexpected pubkey length %d for %s",
                        len(pubkey_bytes), agent_did)
        return None

    # Step 4 — Signature verification
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        sig = _b64url_decode(sig_b64)
    except Exception:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
        pub.verify(sig, signing_input)
    except Exception:
        _logger.warning("CEL token verify: Ed25519 invalid for agent %s", agent_did)
        return None

    # Step 5 — JWT claims
    if payload.get("aud") != "trusteed":
        _logger.debug("CEL token verify: aud mismatch: %s", payload.get("aud"))
        return None
    if payload.get("merchantId") != merchant_id:
        _logger.debug("CEL token verify: merchantId mismatch: %s != %s",
                      payload.get("merchantId"), merchant_id)
        return None
    now = time.time()
    exp_raw = payload.get("exp", 0)
    iat_raw = payload.get("iat", 0)
    # bool is an int subclass in Python — exclude it explicitly so a stray
    # `"exp": true` isn't silently coerced to 1.
    if isinstance(exp_raw, bool) or not isinstance(exp_raw, (int, float)):
        _logger.debug("CEL token verify: exp claim is not numeric")
        return None
    if isinstance(iat_raw, bool) or not isinstance(iat_raw, (int, float)):
        _logger.debug("CEL token verify: iat claim is not numeric")
        return None
    exp: float = float(exp_raw)
    iat: float = float(iat_raw)
    if exp + _EXP_CLOCK_SKEW_SECONDS < now:
        _logger.debug("CEL token verify: token expired (exp=%s, now=%s)", exp, now)
        return None
    if exp - iat > _MAX_TOKEN_WINDOW_SECONDS:
        _logger.debug("CEL token verify: token window too wide (%ss)", exp - iat)
        return None
    if iat - _IAT_FUTURE_SKEW_SECONDS > now:
        _logger.debug("CEL token verify: iat in the future (iat=%s, now=%s)", iat, now)
        return None
    if now - iat > _MAX_TOKEN_AGE_SECONDS:
        _logger.debug("CEL token verify: token too old (age=%ss)", now - iat)
        return None
    token_hash = payload.get("checkoutIntentHash", "")
    if token_hash != checkout_intent_hash:
        _logger.debug("CEL token verify: checkoutIntentHash mismatch "
                      "(token=%s, computed=%s)", token_hash, checkout_intent_hash)
        return None

    # Step 6 — Nonce replay detection
    #
    # El identificador de un solo uso sale del payload FIRMADO: `nonce`
    # (canónico) o, sólo si `nonce` está AUSENTE, `jti` (alias heredado). Si
    # `nonce` viene y está malformado el token es inválido aunque `jti` sea
    # correcto (no se «rescata» con el alias). Ambos presentes y distintos ⇒
    # manda `nonce`. Obligatorio y fail-closed (H4, 2026-07-28): sin
    # identificador válido no hay detección de replay y el token no vale.
    nonce_raw = payload["nonce"] if "nonce" in payload else payload.get("jti")
    if not isinstance(nonce_raw, str):
        _logger.debug("CEL token verify: nonce/jti claim missing or not a string")
        return None
    if not _NONCE_RE.fullmatch(nonce_raw):
        _logger.debug(
            "CEL token verify: nonce missing or malformed (len=%d)", len(nonce_raw)
        )
        return None
    nonce = nonce_raw
    _evict_expired_nonces(now)
    nonce_key = hashlib.sha256(f"{agent_did}{nonce}".encode("utf-8")).hexdigest()
    if nonce_key in _NONCE_CACHE and _NONCE_CACHE[nonce_key] > now:
        _logger.warning("CEL token verify: replay detected for agent %s", agent_did)
        return None
    _NONCE_CACHE[nonce_key] = now + _NONCE_TTL_SECONDS

    # HIGH-3 fix: iss MUST match the kid-derived DID to prevent key-confusion.
    # An attacker with any resolver-listed DID could sign with their key but
    # claim a trusted iss. Reject when they diverge.
    iss: str = payload.get("iss", "")
    if not iss or iss != agent_did:
        _logger.warning(
            "CEL token verify: iss/kid mismatch (iss=%s, kid-did=%s)", iss, agent_did
        )
        return None

    return {
        "agentId": iss,
        "trustScore": payload.get("agentTrustScore"),
        "platform": payload.get("platform"),
        # Identificador de un solo uso que se consume en el backend. `jti` se
        # conserva como alias del mismo valor por compatibilidad con los
        # llamadores/tests anteriores.
        "nonce": nonce,
        "jti": nonce,
        "exp": int(exp) if exp else 0,
    }


def compute_intent_hash(canonical_json: str) -> str:
    """SHA-256 hex digest of the RFC 8785 canonical JSON string.

    Returns a 64-character lowercase hex string.  Callers must apply RFC 8785
    key ordering and Unicode normalisation before calling this function.
    """
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
