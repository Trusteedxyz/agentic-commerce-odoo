"""Trusteed CEL — Snapshot client for Odoo 18.

Pulls the RuleSnapshot JWS, verifies the Ed25519 signature against the public
JWKS, and caches the result in-process for 5 min (bounded by validUntil claim).

Thread-safety: Odoo workers run under gevent (cooperative), plain dict
mutations are safe.  Each worker keeps its own cache; up to 5 min drift
between workers is acceptable for rule snapshot freshness.
"""

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from odoo import api, models

from ..utils.ssrf import validate_api_base
from . import snapshot_payload_decoder

_logger = logging.getLogger(__name__)

# (merchant_id, company_id) → (jws_compact, expire_ts)
_SNAPSHOT_CACHE: dict[tuple[str, int], tuple[str, float]] = {}
# api_base → {kid: raw_32_bytes}
_JWKS_CACHE: dict[str, dict[str, bytes]] = {}
# api_base → expire_ts
_JWKS_EXPIRE: dict[str, float] = {}

# L3 — caché NEGATIVA del reintento forzado del JWKS: (api_base, kid) → ts hasta
# el que NO se vuelve a descargar. Sin ella, cada checkout con un snapshot
# inverificable (un MITM, un fallo propio persistente) hacía otra descarga
# hacia el servidor. El ``kid`` sale de una cabecera SIN verificar, así que el
# registro está acotado (``_NEGATIVE_RETRY_MAX``) para que no crezca con kids
# inventados.
_NEGATIVE_RETRY_TTL_SECONDS = 60.0
_NEGATIVE_RETRY_MAX = 128
_NEGATIVE_RETRY: dict[tuple[str, str], float] = {}
_NEGATIVE_RETRY_LOCK = threading.Lock()

# L6b — tope del cuerpo HTTP del snapshot ANTES de verificar. Un JWS legítimo es
# base64url de un payload que, una vez inflado, no pasa de 2 MiB
# (``snapshot_payload_decoder.MAX_INFLATED_BYTES``): el peor caso (2 MiB
# incompresibles) son ~2,8 MB de texto; 4 MiB da margen sin dejar que un
# servidor hostil o un MITM nos haga cargar en memoria un cuerpo arbitrario.
MAX_SNAPSHOT_BODY_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


def base64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def base64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


# Motivo de ``pull_snapshot_result`` / ``get_snapshot_result`` (plan 04,
# Task 21). Se traduce a ``PlatformDegradedKind`` en sale_order_enforcement:
#   ok             → snapshot verificado
#   tampered       → invalid_signature (JWS descargado AHORA que no verifica)
#   unavailable    → missing_snapshot (sin snapshot utilizable)
#   layer_disabled → layer_disabled (celEnabled=false o addon sin configurar)
SNAPSHOT_OK = "ok"
SNAPSHOT_TAMPERED = "tampered"
SNAPSHOT_UNAVAILABLE = "unavailable"
SNAPSHOT_LAYER_DISABLED = "layer_disabled"


def _ed25519_verifies(pubkey_bytes: Optional[bytes], sig: bytes, signing_input: bytes) -> bool:
    if pubkey_bytes is None or len(pubkey_bytes) != 32:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(pubkey_bytes).verify(sig, signing_input)
        return True
    except Exception:
        return False


def verify_jws_snapshot_result(jws: str, api_base: str) -> tuple[str, Optional[dict]]:
    """Verify an EdDSA JWS Compact against the JWKS: ``(motivo, payload)``.

    - Forma inválida (no es JWS compacto, cabecera ilegible, ``alg`` distinto
      de EdDSA, sin ``kid``, payload sin ``zip`` que no es un objeto JSON) ⇒
      ``tampered``.
    - Firma válida con ``zip`` (Task 24): se infla con tope
      (:mod:`snapshot_payload_decoder`); si no se puede leer ⇒ ``unavailable``.
    - Firma: se prueba con el JWKS en caché (1 h); si el ``kid`` no está o la
      firma no cuadra, se DESCARGA el JWKS una vez más (rotación de clave con
      la caché aún viva) y se reintenta. Si tras ese reintento no hay NINGUNA
      clave (JWKS inalcanzable y sin caché) ⇒ ``unavailable``: no podemos
      juzgar la firma, no es prueba de manipulación. Con claves y sin
      verificar ⇒ ``tampered``.
    """
    parts = jws.split(".") if isinstance(jws, str) else []
    if len(parts) != 3:
        return SNAPSHOT_TAMPERED, None
    header_b64, payload_b64, sig_b64 = parts
    if not jws.isascii():
        return SNAPSHOT_TAMPERED, None
    try:
        header = json.loads(base64url_decode(header_b64))
        sig = base64url_decode(sig_b64)
    except Exception:
        return SNAPSHOT_TAMPERED, None
    if not isinstance(header, dict) or header.get("alg") != "EdDSA":
        return SNAPSHOT_TAMPERED, None
    # L6c — RFC 7515 §4.1.11: quien no entiende una extensión crítica debe
    # rechazar el JWS, y este receptor no entiende ninguna. Antes de tocar red.
    if "crit" in header:
        return SNAPSHOT_TAMPERED, None
    kid = header.get("kid", "")
    if not isinstance(kid, str) or not kid:
        return SNAPSHOT_TAMPERED, None
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    if not _ed25519_verifies(_fetch_jwks(api_base).get(kid), sig, signing_input):
        # Reintento único: rotación con la caché JWKS de 1 h todavía viva. L3:
        # con un reintento fallido reciente para este kid no se vuelve a
        # descargar; se clasifica con lo que ya se sabe (la caché JWKS).
        fresh, downloaded = _forced_jwks_retry(api_base, kid)
        if not fresh:
            _logger.warning("CEL verify_jws: JWKS unavailable, cannot judge kid %s", kid)
            if downloaded:
                _remember_failed_retry(api_base, kid)
            return SNAPSHOT_UNAVAILABLE, None
        if not _ed25519_verifies(fresh.get(kid), sig, signing_input):
            _logger.warning("CEL verify_jws: signature/kid %s invalid after JWKS refresh", kid)
            if downloaded:
                # Sólo un reintento REAL abre la ventana: si la abriera también
                # el que se salta, se prolongaría sin fin mientras dure el fallo.
                _remember_failed_retry(api_base, kid)
            return SNAPSHOT_TAMPERED, None
        _forget_failed_retry(api_base, kid)
    # Sólo tras verificar la firma: inflar (si ``zip``) con tope y leer (Task 24).
    try:
        payload_bytes = base64url_decode(payload_b64)
    except Exception:
        payload_bytes = b""
    decoded, payload = snapshot_payload_decoder.decode(header, payload_bytes)
    if decoded != snapshot_payload_decoder.OK:
        if snapshot_payload_decoder.declares_zip(header):
            # Firma de la plataforma válida pero ilegible (zip desconocido,
            # bomba, flujo roto): no utilizable, no manipulación.
            _logger.warning("CEL verify_jws: signed payload unreadable (%s)", decoded)
            return SNAPSHOT_UNAVAILABLE, None
        return SNAPSHOT_TAMPERED, None
    # L6a — anti-rollback: un snapshot firmado y verificado pero pasado de su
    # ``validUntil`` (un MITM TLS puede reservir uno viejo) es «no disponible»,
    # como en Magento/PrestaShop/WooCommerce. Task 38d: un snapshot que nuestro
    # servidor firma SIEMPRE lleva ``validUntil``; ausente o ilegible es un
    # documento malformado y se clasifica igual (no disponible, NO manipulado).
    problem = _valid_until_problem(payload)
    if problem:
        _logger.warning("CEL verify_jws: snapshot not usable (validUntil %s)", problem)
        return SNAPSHOT_UNAVAILABLE, None
    return SNAPSHOT_OK, payload


def _valid_until_problem(payload: dict) -> Optional[str]:
    """``None`` si ``validUntil`` es una fecha ISO 8601 legible y futura; si no,
    ``expired`` | ``missing`` | ``unreadable``. Sin zona se lee como UTC."""
    valid_until = payload.get("validUntil")
    if valid_until is None or valid_until == "":
        return "missing"
    if not isinstance(valid_until, str):
        return "unreadable"
    try:
        parsed = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        expires_at = parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return "unreadable"
    return "expired" if expires_at <= time.time() else None


def _snapshot_is_stale(payload: dict) -> bool:
    """No utilizable por su ``validUntil``: vencido, ausente o ilegible."""
    return _valid_until_problem(payload) is not None


def _forced_jwks_retry(api_base: str, kid: str) -> tuple[dict[str, bytes], bool]:
    """``(JWKS, descargado)``: tras un reintento forzado, o el ya conocido
    (sin descargar) dentro de la ventana negativa."""
    now = time.time()
    with _NEGATIVE_RETRY_LOCK:
        blocked_until = _NEGATIVE_RETRY.get((api_base, kid), 0.0)
    if blocked_until > now:
        _logger.info("CEL verify_jws: forced JWKS retry for kid %s skipped (negative cache)", kid)
        return _JWKS_CACHE.get(api_base, {}), False
    return _fetch_jwks(api_base, force_refresh=True), True


def _remember_failed_retry(api_base: str, kid: str) -> None:
    now = time.time()
    with _NEGATIVE_RETRY_LOCK:
        if len(_NEGATIVE_RETRY) >= _NEGATIVE_RETRY_MAX:
            for key in [k for k, until in _NEGATIVE_RETRY.items() if until <= now]:
                del _NEGATIVE_RETRY[key]
            if len(_NEGATIVE_RETRY) >= _NEGATIVE_RETRY_MAX:
                # Lleno de entradas vivas: cae la más antigua (la de menor caducidad).
                del _NEGATIVE_RETRY[min(_NEGATIVE_RETRY, key=_NEGATIVE_RETRY.get)]
        _NEGATIVE_RETRY[(api_base, kid)] = now + _NEGATIVE_RETRY_TTL_SECONDS


def _forget_failed_retry(api_base: str, kid: str) -> None:
    with _NEGATIVE_RETRY_LOCK:
        _NEGATIVE_RETRY.pop((api_base, kid), None)


def read_body_capped(resp, max_bytes: int = MAX_SNAPSHOT_BODY_BYTES) -> Optional[str]:
    """Cuerpo de una respuesta ``stream=True`` como texto, o ``None`` si pasa del tope.

    Comprueba ``Content-Length`` y, como puede mentir o faltar (chunked), cuenta
    los bytes realmente leídos y corta al pasar del tope: nunca acumula más de
    ``max_bytes`` + un trozo.
    """
    try:
        declared = int(resp.headers.get("Content-Length"))
    except (TypeError, ValueError, AttributeError):
        declared = None
    try:
        if declared is not None and declared > max_bytes:
            return None
        received = bytearray()
        for chunk in resp.iter_content(chunk_size=_READ_CHUNK_BYTES):
            received.extend(chunk)
            if len(received) > max_bytes:
                return None
        return bytes(received).decode("utf-8", errors="replace")
    finally:
        _release(resp)


def _release(resp) -> None:
    close = getattr(resp, "close", None)
    if callable(close):
        close()


def verify_jws_snapshot(jws: str, api_base: str) -> Optional[dict]:
    """Verify an EdDSA JWS Compact against the JWKS; return payload or None."""
    status, payload = verify_jws_snapshot_result(jws, api_base)
    return payload if status == SNAPSHOT_OK else None


def _fetch_jwks(api_base: str, *, force_refresh: bool = False) -> dict[str, bytes]:
    """Fetch JWKS with 1-hour cache; stale-while-revalidate on error."""
    now = time.time()
    cached = _JWKS_CACHE.get(api_base, {})
    if not force_refresh and cached and _JWKS_EXPIRE.get(api_base, 0) > now:
        return cached
    url = f"{api_base.rstrip('/')}/.well-known/jwks.json"
    try:
        resp = requests.get(url, timeout=5, verify=True, allow_redirects=False)
        if resp.status_code != 200:
            return cached
        data = resp.json()
    except Exception as exc:
        _logger.warning("CEL JWKS fetch error: %s", exc)
        return cached
    keys: dict[str, bytes] = {}
    for k in data.get("keys", []):
        if (k.get("kty") == "OKP" and k.get("crv") == "Ed25519"
                and k.get("kid") and k.get("x")):
            try:
                raw = base64url_decode(k["x"])
                if len(raw) == 32:
                    keys[k["kid"]] = raw
            except Exception:
                continue
    _JWKS_CACHE[api_base] = keys
    _JWKS_EXPIRE[api_base] = now + 3600
    return keys


def _verify_cached(jws: str, api_base: str) -> tuple[str, Optional[dict]]:
    """Re-verify a JWS from OUR in-process cache.

    Ya se verificó al guardarse y un tercero no puede escribir en la memoria
    del proceso: si ahora no verifica es rotación de clave o JWKS caído, no
    manipulación ⇒ ``unavailable``, nunca ``tampered``.
    """
    status, payload = verify_jws_snapshot_result(jws, api_base)
    if status == SNAPSHOT_OK:
        return status, payload
    return SNAPSHOT_UNAVAILABLE, None


def pull_snapshot_result(
    api_base: str,
    merchant_id: str,
    installation_id: str,
    hmac_secret: str,
    company_id: int,
) -> tuple[str, Optional[dict]]:
    """Pull RuleSnapshot JWS from Trusteed: ``(motivo, payload verificado)``.

    Caches raw JWS for TTL from validUntil claim (clamped to [60, 300] s).
    Serves the last-known-good cache on transient network errors.

    Motivo (plan 04, Task 21): ``ok``; ``tampered`` sólo cuando el JWS que el
    servidor acaba de servir no verifica (nunca se cachea); ``unavailable``
    cuando no hay snapshot utilizable (SSRF, red, HTTP ≠ 200 sin caché, JWKS
    inalcanzable, caché propia que ya no verifica).
    """
    if not validate_api_base(api_base):
        _logger.warning("CEL pull_snapshot: api_base SSRF check failed: %s", api_base)
        return SNAPSHOT_UNAVAILABLE, None
    cache_key = (merchant_id, company_id)
    now = time.time()
    cached_entry = _SNAPSHOT_CACHE.get(cache_key)
    cached_jws: Optional[str] = cached_entry[0] if cached_entry else None
    cached_expire: float = cached_entry[1] if cached_entry else 0.0
    if cached_jws and cached_expire > now:
        status, payload = _verify_cached(cached_jws, api_base)
        if status == SNAPSHOT_OK:
            return status, payload
        # La caché ya no verifica (p. ej. clave rotada): se descarta y se pide
        # uno nuevo en vez de servir «sin snapshot» hasta que caduque.
        _SNAPSHOT_CACHE.pop(cache_key, None)
        cached_jws = None
    url = f"{api_base.rstrip('/')}/v1/rules/snapshot/{merchant_id}"
    ts = int(now)
    mac = hmac.new(
        hmac_secret.encode("utf-8"),
        f"{ts}.".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers: dict[str, str] = {
        "X-Trusteed-Installation-Id": installation_id,
        "X-Trusteed-Signature": f"t={ts},s={mac}",
        "Accept": "application/jose",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=8, verify=True,
                            allow_redirects=False, stream=True)
    except Exception as exc:
        _logger.warning("CEL pull_snapshot: request error: %s", exc)
        if cached_jws:
            return _verify_cached(cached_jws, api_base)
        return SNAPSHOT_UNAVAILABLE, None
    if resp.status_code != 200:
        _release(resp)  # stream=True: sin leer el cuerpo, se libera la conexión
    if resp.status_code == 304 and cached_jws:
        _SNAPSHOT_CACHE[cache_key] = (cached_jws, now + 300)
        return _verify_cached(cached_jws, api_base)
    if resp.status_code != 200:
        _logger.warning("CEL pull_snapshot: HTTP %s from %s", resp.status_code, url)
        if cached_jws:
            return _verify_cached(cached_jws, api_base)
        return SNAPSHOT_UNAVAILABLE, None
    body = read_body_capped(resp)
    if body is None:
        # L6b: cuerpo por encima del tope ⇒ como cualquier fallo de transporte
        # (última copia conocida y aún válida, si la hay; si no, «no disponible»).
        _logger.warning("CEL pull_snapshot: body over %d bytes; discarded", MAX_SNAPSHOT_BODY_BYTES)
        if cached_jws:
            return _verify_cached(cached_jws, api_base)
        return SNAPSHOT_UNAVAILABLE, None
    jws = body.strip()
    status, payload = verify_jws_snapshot_result(jws, api_base)
    if status != SNAPSHOT_OK:
        _logger.error(
            "CEL pull_snapshot: JWS verification failed (%s); refusing to cache", status
        )
        return status, None
    # Derive TTL from validUntil, clamp to [60, 300] seconds
    ttl = 300
    valid_until = payload.get("validUntil", "")
    if valid_until:
        try:
            from datetime import datetime
            vu_ts = datetime.fromisoformat(
                valid_until.replace("Z", "+00:00")
            ).timestamp()
            ttl = max(60, min(300, int(vu_ts - now)))
        except Exception:
            pass
    _SNAPSHOT_CACHE[cache_key] = (jws, now + ttl)
    return SNAPSHOT_OK, payload


def pull_snapshot(
    api_base: str,
    merchant_id: str,
    installation_id: str,
    hmac_secret: str,
    company_id: int,
) -> Optional[dict]:
    """Verified payload or None (compat wrapper over ``pull_snapshot_result``)."""
    status, payload = pull_snapshot_result(
        api_base, merchant_id, installation_id, hmac_secret, company_id
    )
    return payload if status == SNAPSHOT_OK else None


class EnforcementSnapshotService(models.AbstractModel):
    """Thin Odoo wrapper around pull_snapshot, bound to the current company."""

    _name = "trusteed.enforcement.snapshot"
    _description = "Trusteed CEL Snapshot Service"

    def _resolve_company_id(self, company_id=None) -> int:
        """Company whose settings apply (Task 35b, M3).

        The enforcement flow ALWAYS passes the company of the ORDER
        (``order.company_id``): ``env.company`` comes from
        ``allowed_company_ids`` of the context, which an RPC client chooses
        among its own companies, so it must never decide which company's
        mode / credentials / snapshot govern an order. ``None`` (cron warm-up,
        UI) falls back to ``env.company``. An explicit value that is not a
        positive int raises ``ValueError`` (the guards turn our own exception
        into: agent held / person passes).
        """
        if company_id is None:
            return self.env.company.id
        if isinstance(company_id, bool) or not isinstance(company_id, int) or company_id <= 0:
            raise ValueError(f"invalid company_id {company_id!r}")
        return company_id

    @api.model
    def get_credentials(self, company_id=None) -> dict:
        """CEL credentials of the company asked for — the only source of them.

        ``company_id``: the ORDER's company in the enforcement flow (Task 35b);
        ``None`` = ``env.company`` (cron / UI only).

        H8 multi-company IDOR fix: the ``merchantId`` (and installationId) are
        derived from the *current Odoo company* — never from caller-supplied
        input. When a per-company mapping exists
        (``trusteed.company.enforcement.map``) it is authoritative, so a B-company
        order can never inherit A-company's merchant credentials. When no
        per-company mapping exists we fall back to the global
        ir.config_parameter values (single-company deployments).

        Plan 04, Task 21: the same installation id + HMAC secret authenticate
        the snapshot pull, ``POST /v1/rules/evaluate`` and
        ``POST /v1/agent-events/nonce-consume`` (all three behind the
        per-installation HMAC middleware of the API, like the PrestaShop and
        WooCommerce settings). Before, the last two read them from the signed
        snapshot payload, which never carries them (``RuleSnapshotPayloadSchema``
        has no ``installationId`` nor ``hmacSecret``): they were always skipped.

        Keys: ``apiBase``, ``companyId``, ``merchantId``, ``installationId``,
        ``hmacSecret``, ``celEnabled`` (bool), ``configured`` (bool: the three
        credentials present) and ``localFallbackMode`` (the company map's
        ``fallbackMode``, NOT signed — see ``get_snapshot_result``; None
        without a mapping).
        """
        cfg = self.env["ir.config_parameter"].sudo()
        api_base = cfg.get_param("trusteed.api_base", "https://api.trusteed.xyz")
        company_id = self._resolve_company_id(company_id)

        # Per-company mapping takes precedence (multi-company isolation).
        mapping = None
        try:
            mapping = self.env["trusteed.company.enforcement.map"].get_for_company(
                company_id
            )
        except Exception as exc:  # pragma: no cover  # defensive — model may be absent
            _logger.debug("CEL get_credentials: company-map lookup failed: %s", exc)

        cel_enabled = True
        local_fallback_mode = None
        if mapping is not None:
            cel_enabled = bool(mapping.get("celEnabled", True))
            local_fallback_mode = mapping.get("fallbackMode")
            merchant_id = mapping.get("merchantId", "")
            installation_id = mapping.get("installationId") or ""
            # hmacSecret is sensitive and not stored on the map record; it is
            # always read from the secured ir.config_parameter store, keyed by
            # the per-company installation when available.
            hmac_secret = cfg.get_param(
                f"trusteed.cel_hmac_secret.{company_id}",
                cfg.get_param("trusteed.cel_hmac_secret", ""),
            )
        else:
            merchant_id = cfg.get_param("trusteed.merchant_id", "")
            installation_id = cfg.get_param("trusteed.cel_installation_id", "")
            hmac_secret = cfg.get_param("trusteed.cel_hmac_secret", "")

        return {
            "apiBase": api_base,
            "companyId": company_id,
            "merchantId": merchant_id or "",
            "installationId": installation_id or "",
            "hmacSecret": hmac_secret or "",
            "celEnabled": cel_enabled,
            "configured": bool(merchant_id and installation_id and hmac_secret),
            "localFallbackMode": local_fallback_mode,
        }

    @api.model
    def get_snapshot_result(self, company_id=None) -> dict:
        """Snapshot of the current company WITH the reason (plan 04, Task 21).

        Returns ``{"status", "payload", "localFallbackMode"}`` where status is
        one of ``ok`` / ``tampered`` / ``unavailable`` / ``layer_disabled``:

        - ``layer_disabled``: CEL disabled for the company (``celEnabled`` false)
          OR the addon is not configured (merchant, installation or secret
          missing). Configuration, not an outage: nothing is evaluated and
          nobody is held. Decision taken here, documented in the README.
        - otherwise the reason of ``pull_snapshot_result``.
        """
        creds = self.get_credentials(company_id)
        local_mode = creds["localFallbackMode"]
        if not creds["celEnabled"]:
            _logger.debug(
                "CEL get_snapshot: CEL disabled for company %s — skipping",
                creds["companyId"],
            )
            return {"status": SNAPSHOT_LAYER_DISABLED, "payload": None, "localFallbackMode": local_mode}
        if not creds["configured"]:
            return {"status": SNAPSHOT_LAYER_DISABLED, "payload": None, "localFallbackMode": local_mode}
        status, payload = pull_snapshot_result(
            creds["apiBase"],
            creds["merchantId"],
            creds["installationId"],
            creds["hmacSecret"],
            creds["companyId"],
        )
        return {"status": status, "payload": payload, "localFallbackMode": local_mode}

    @api.model
    def get_snapshot(self, company_id=None) -> Optional[dict]:
        """Return verified RuleSnapshot payload for the current company.

        Returns None when any required credential is absent, when the
        per-company mapping has CEL disabled (``celEnabled == False``), or when
        no verified snapshot is available (used by the warm-up cron; the
        enforcement path uses ``get_snapshot_result`` to know WHY).
        """
        creds = self.get_credentials(company_id)
        if not creds["celEnabled"]:
            _logger.debug(
                "CEL get_snapshot: CEL disabled for company %s — skipping",
                creds["companyId"],
            )
            return None
        if not creds["configured"]:
            return None
        return pull_snapshot(
            creds["apiBase"],
            creds["merchantId"],
            creds["installationId"],
            creds["hmacSecret"],
            creds["companyId"],
        )
