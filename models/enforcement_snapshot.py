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
import time
from typing import Optional

import requests

from odoo import api, models

from ..utils.ssrf import validate_api_base

_logger = logging.getLogger(__name__)

# (merchant_id, company_id) → (jws_compact, expire_ts)
_SNAPSHOT_CACHE: dict[tuple[str, int], tuple[str, float]] = {}
# api_base → {kid: raw_32_bytes}
_JWKS_CACHE: dict[str, dict[str, bytes]] = {}
# api_base → expire_ts
_JWKS_EXPIRE: dict[str, float] = {}


def base64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def base64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def verify_jws_snapshot(jws: str, api_base: str) -> Optional[dict]:
    """Verify an EdDSA JWS Compact against the JWKS; return payload or None."""
    parts = jws.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(base64url_decode(header_b64))
    except Exception:
        return None
    if header.get("alg") != "EdDSA":
        return None
    kid = header.get("kid", "")
    if not kid:
        return None
    jwks = _fetch_jwks(api_base)
    pubkey_bytes = jwks.get(kid)
    if pubkey_bytes is None or len(pubkey_bytes) != 32:
        # Force-refresh on kid miss (once per verification)
        jwks = _fetch_jwks(api_base, force_refresh=True)
        pubkey_bytes = jwks.get(kid)
        if pubkey_bytes is None or len(pubkey_bytes) != 32:
            _logger.warning("CEL verify_jws: kid %s unknown after refresh", kid)
            return None
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        sig = base64url_decode(sig_b64)
    except Exception:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
        pub.verify(sig, signing_input)
    except Exception:
        _logger.warning("CEL verify_jws: signature failed for kid %s", kid)
        return None
    try:
        return json.loads(base64url_decode(payload_b64))
    except Exception:
        return None


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


def pull_snapshot(
    api_base: str,
    merchant_id: str,
    installation_id: str,
    hmac_secret: str,
    company_id: int,
) -> Optional[dict]:
    """Pull RuleSnapshot JWS from Trusteed and return verified payload.

    Caches raw JWS for TTL from validUntil claim (clamped to [60, 300] s).
    Fails open on transient network errors using the last-known-good cache.
    Returns None if SSRF check fails, server unavailable with no cache, or
    JWS signature verification fails (never caches invalid snapshots).
    """
    if not validate_api_base(api_base):
        _logger.warning("CEL pull_snapshot: api_base SSRF check failed: %s", api_base)
        return None
    cache_key = (merchant_id, company_id)
    now = time.time()
    cached_entry = _SNAPSHOT_CACHE.get(cache_key)
    cached_jws: Optional[str] = cached_entry[0] if cached_entry else None
    cached_expire: float = cached_entry[1] if cached_entry else 0.0
    if cached_jws and cached_expire > now:
        return verify_jws_snapshot(cached_jws, api_base)
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
                            allow_redirects=False)
    except Exception as exc:
        _logger.warning("CEL pull_snapshot: request error: %s", exc)
        if cached_jws:
            return verify_jws_snapshot(cached_jws, api_base)
        return None
    if resp.status_code == 304 and cached_jws:
        _SNAPSHOT_CACHE[cache_key] = (cached_jws, now + 300)
        return verify_jws_snapshot(cached_jws, api_base)
    if resp.status_code != 200:
        _logger.warning("CEL pull_snapshot: HTTP %s from %s", resp.status_code, url)
        if cached_jws:
            return verify_jws_snapshot(cached_jws, api_base)
        return None
    jws = resp.text.strip()
    payload = verify_jws_snapshot(jws, api_base)
    if payload is None:
        _logger.error("CEL pull_snapshot: JWS verification failed; refusing to cache")
        return None
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
    return payload


class EnforcementSnapshotService(models.AbstractModel):
    """Thin Odoo wrapper around pull_snapshot, bound to the current company."""

    _name = "trusteed.enforcement.snapshot"
    _description = "Trusteed CEL Snapshot Service"

    @api.model
    def get_snapshot(self) -> Optional[dict]:
        """Return verified RuleSnapshot payload for the current company.

        H8 multi-company IDOR fix: the ``merchantId`` (and installationId) are
        derived from the *current Odoo company* — never from caller-supplied
        input. When a per-company mapping exists
        (``trusteed.company.enforcement.map``) it is authoritative, so a B-company
        order can never inherit A-company's merchant credentials. When no
        per-company mapping exists we fall back to the global
        ir.config_parameter values (single-company deployments).

        Returns None when any required credential is absent, or when the
        per-company mapping has CEL disabled (``celEnabled == False``).
        """
        cfg = self.env["ir.config_parameter"].sudo()
        api_base = cfg.get_param("trusteed.api_base", "https://api.trusteed.xyz")
        company_id = self.env.company.id

        # Per-company mapping takes precedence (multi-company isolation).
        mapping = None
        try:
            mapping = self.env["trusteed.company.enforcement.map"].get_for_company(
                company_id
            )
        except Exception as exc:  # pragma: no cover  # defensive — model may be absent
            _logger.debug("CEL get_snapshot: company-map lookup failed: %s", exc)

        if mapping is not None:
            if not mapping.get("celEnabled", True):
                _logger.info(
                    "CEL get_snapshot: CEL disabled for company %s — skipping",
                    company_id,
                )
                return None
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

        if not (merchant_id and installation_id and hmac_secret):
            return None
        return pull_snapshot(
            api_base, merchant_id, installation_id, hmac_secret, company_id
        )
