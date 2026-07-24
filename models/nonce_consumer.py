"""Trusteed CEL — spec-048 P2.8 backend nonce consumption client (Odoo 18).

Calls POST /v1/agent-events/nonce-consume to record a single-use agent-token
``jti`` and detect replay. Mirrors:
  - WC:  packages/wp-plugin/trusteed-for-woocommerce/includes/
         class-enforcement-api-client.php::consume_nonce()
  - PS:  packages/prestashop-module-trusteed/src/Enforcement/
         SnapshotClient.php::consumeNonce()

Tri-state outcome — caller (sale_order_enforcement) maps INDETERMINATE to
strict (raise) or balanced/permissive (allow + telemetry attribute):

  ACCEPTED       → HTTP 200, nonce stored, token may be used.
  REPLAY         → HTTP 409, nonce already seen — caller must reject token.
  INDETERMINATE  → network / 5xx / 4xx / bad response.

Signing uses the same Stripe-style HMAC-SHA256 scheme as snapshot pull:
  X-Trusteed-Signature: t=<unix>,s=<hex>
  signature = HMAC-SHA256("<ts>.<rawBody>", secret)

Backend route: apps/api/src/routes/agent-events-nonce.routes.ts
"""

import datetime
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import requests

_logger = logging.getLogger(__name__)

# Outcome constants — keep as plain strings (mirrors WC PHP Amcp_Nonce_Outcome).
ACCEPTED = "ACCEPTED"
REPLAY = "REPLAY"
INDETERMINATE = "INDETERMINATE"

_TIMEOUT_SECONDS = 5


def _validate_api_base(url: str) -> bool:
    """Resolve utils.ssrf.validate_api_base across runtime contexts.

    Defense-in-depth: the nonce-consume POST must not be coerced into an SSRF to
    an internal host. Works under the Odoo runtime (package-relative import) and
    when this module is loaded standalone by tests (path-based fallback).
    """
    try:
        from ..utils.ssrf import validate_api_base as _v  # type: ignore
        return _v(url)
    except Exception:
        pass
    try:  # standalone fallback — load utils/ssrf.py by path
        import importlib.util
        from pathlib import Path

        ssrf_path = Path(__file__).resolve().parent.parent / "utils" / "ssrf.py"
        spec = importlib.util.spec_from_file_location(
            "trusteed_nonce_ssrf", str(ssrf_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return bool(mod.validate_api_base(url))
    except Exception as exc:  # pragma: no cover - extremely defensive
        _logger.warning("consume_nonce: SSRF validator unavailable: %s", exc)
        # Fail closed — never proceed if the guard cannot be evaluated.
        return False


def _hmac_sign(raw_body: str, hmac_secret: str) -> str:
    """Return ``t=<ts>,s=<hex>`` signature header value."""
    ts = str(int(time.time()))
    if not hmac_secret:
        # Dev-bypass parity with WC client (never used in prod — secret is required).
        return f"t={ts},s=dev-bypass"
    to_sign = (ts + "." + raw_body).encode("utf-8")
    mac = hmac.new(hmac_secret.encode("utf-8"), to_sign, hashlib.sha256).hexdigest()
    return f"t={ts},s={mac}"


def _gmt_iso(ts: int) -> str:
    """ISO-8601 UTC timestamp ``YYYY-MM-DDTHH:MM:SSZ`` for expiresAt."""
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def consume_nonce(
    api_base: str,
    merchant_id: str,
    installation_id: str,
    hmac_secret: str,
    agent_did: str,
    jti: str,
    exp: int,
) -> dict:
    """Consume an agent-token ``jti`` against the backend.

    Returns a dict ``{"outcome": str, "reason": str, "httpStatus": int|None}``.
    Never raises — all failures surface as INDETERMINATE (network/5xx/4xx).

    The caller (sale_order_enforcement) maps INDETERMINATE to strict-block or
    balanced-pass per snapshot ``fallbackMode``.
    """
    base = (api_base or "").rstrip("/")
    if not base or not merchant_id or not installation_id:
        return {"outcome": INDETERMINATE, "reason": "missing_config", "httpStatus": None}

    # SSRF guard (defense-in-depth) — refuse any non-trusteed.xyz / private host.
    if not _validate_api_base(base):
        _logger.warning("consume_nonce: SSRF check failed for api_base")
        return {"outcome": INDETERMINATE, "reason": "ssrf_blocked", "httpStatus": None}

    expires_at = _gmt_iso(exp if exp and exp > 0 else int(time.time()) + 300)

    payload = {
        "merchantId": merchant_id,
        "installationId": installation_id,
        "agentId": agent_did,
        "nonce": jti,
        "expiresAt": expires_at,
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    headers = {
        "Content-Type": "application/json",
        "X-Trusteed-Installation-Id": installation_id,
        "X-Trusteed-Signature": _hmac_sign(raw_body, hmac_secret),
    }
    url = base + "/v1/agent-events/nonce-consume"

    try:
        resp = requests.post(
            url,
            data=raw_body.encode("utf-8"),
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
            allow_redirects=False,  # prevent redirect-based SSRF to internal hosts
        )
    except requests.exceptions.Timeout:
        return {"outcome": INDETERMINATE, "reason": "timeout", "httpStatus": None}
    except requests.exceptions.RequestException as exc:
        _logger.debug("consume_nonce network error: %s", type(exc).__name__)
        return {"outcome": INDETERMINATE, "reason": "network_error", "httpStatus": None}

    status = int(resp.status_code)
    if status == 200:
        return {"outcome": ACCEPTED, "reason": "ok", "httpStatus": status}
    if status == 409:
        return {"outcome": REPLAY, "reason": "replay_detected", "httpStatus": status}
    if status >= 500:
        return {"outcome": INDETERMINATE, "reason": "http_5xx", "httpStatus": status}
    return {"outcome": INDETERMINATE, "reason": "http_4xx", "httpStatus": status}


def map_outcome_to_action(outcome: str, fallback_mode: str) -> str:
    """Reduce a consume_nonce() outcome + snapshot fallbackMode to a coarse action.

    Returns one of:
      "continue"   — token is unique, proceed.
      "reject"     — replay detected (or strict-fail-closed INDETERMINATE).
      "observe"    — INDETERMINATE in balanced/permissive — annotate + allow.
    """
    if outcome == ACCEPTED:
        return "continue"
    strict = fallback_mode == "strict"
    if outcome == REPLAY:
        return "reject"
    # INDETERMINATE
    return "reject" if strict else "observe"
