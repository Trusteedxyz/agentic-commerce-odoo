"""Trusteed embed bootstrap — Odoo controller (production).

Signs an HS256 JWT with the per-tenant ``embedBootstrapSecret`` and exchanges
it with ``apps/api`` for an Ed25519 access token.

Reuses Odoo's session ``request.env.user`` + group check for auth gating.
``request.env.company`` is captured at *issuance* — see analysis v2 §4.3 for
the company-switch race mitigation.

Production improvements over spike F0:
  - proper ``_logger`` via ``logging.getLogger`` (no print/stdout leaks)
  - ``requests`` library with (connect_timeout, read_timeout) tuple
  - no retry — single attempt (DoS prevention)
  - bootstrap secret never appears in any log statement
  - group check ``trusteed.group_user`` before any processing
  - multi-company scope includes ``company_ids`` (all allowed companies)
  - ``User-Agent`` header identifies production addon version
  - SSRF guard on api_base (via utils.ssrf.validate_api_base)
  - allow_redirects=False to prevent redirect-based SSRF
"""

import base64
import hashlib
import hmac
import json
import logging

import requests
import requests.exceptions

from odoo import http
from odoo.http import request
from odoo.addons.trusteed.utils.ssrf import validate_api_base

_logger = logging.getLogger(__name__)

_ADDON_USER_AGENT = "TrusteedAddon-Odoo/1.0"
_BOOTSTRAP_ENDPOINT = "/api/v1/auth/embed-bootstrap"
# X1 (embed data-plane unlock): the SPA data routes use `requireAnyEmbedAuth`,
# which validates the OPAQUE EmbedTokenService token — NOT the Ed25519 bootstrap
# token. The Odoo addon mints that opaque token via the S2S relay below, exactly
# like the PrestaShop/Magento relays. Auth = X-Embed-Odoo-Secret (the same
# per-merchant bootstrap secret the module already stores).
_ISSUE_TOKEN_ENDPOINT = "/v1/embed/odoo/issue-token"
_CONNECT_TIMEOUT = 2   # seconds — TCP handshake
_READ_TIMEOUT = 4      # seconds — response body


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sign_bootstrap(payload: dict, secret: str) -> str:
    """Return a compact HS256 JWT.  Secret is consumed in-memory only."""
    header = {"alg": "HS256", "typ": "JWT", "kid": payload["merchant_id"]}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def _do_exchange(endpoint: str, bootstrap_jwt: str) -> tuple[dict, int]:
    """POST the bootstrap JWT to apps/api.  Returns (body_dict, http_status).

    Single attempt — no retry (F-008: retry caused up to 22s Odoo worker block).
    Bootstrap secret is NEVER included in log messages.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {bootstrap_jwt}",
        "User-Agent": _ADDON_USER_AGENT,
    }
    body = json.dumps({"source": "odoo-embed"}).encode("utf-8")
    timeout = (_CONNECT_TIMEOUT, _READ_TIMEOUT)

    try:
        # allow_redirects=False: prevent redirect-based SSRF to internal hosts
        resp = requests.post(endpoint, data=body, headers=headers, timeout=timeout, allow_redirects=False)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            _logger.error("trusteed bootstrap: unexpected redirect from %s", endpoint)
            return {"error": "bootstrap_redirect_rejected"}, 400
        try:
            decoded = resp.json()
        except ValueError:
            decoded = {}
        return decoded, resp.status_code
    except requests.exceptions.Timeout:
        _logger.error("trusteed bootstrap timeout")
        return {"error": "bootstrap_timeout"}, 504
    except requests.exceptions.ConnectionError as exc:
        _logger.error("trusteed bootstrap connection error: %s", exc)
        return {"error": "bootstrap_connection_error"}, 503
    except Exception as exc:  # pragma: no cover  # defensive
        _logger.exception("trusteed bootstrap unexpected error: %s", type(exc).__name__)
        return {"error": "bootstrap_failed"}, 500


def _do_issue_token(
    endpoint: str, secret: str, merchant_id: str, odoo_uid: str
) -> tuple[dict, int]:
    """POST to the Odoo embed relay to mint an opaque data-plane token.

    S2S auth via the X-Embed-Odoo-Secret header (the per-merchant bootstrap
    secret). Single attempt — no retry (F-008: retry caused Odoo worker block).
    The secret is NEVER included in any log message. Returns (body, http_status).
    """
    headers = {
        "Content-Type": "application/json",
        "X-Embed-Odoo-Secret": secret,
        "User-Agent": _ADDON_USER_AGENT,
    }
    body = json.dumps(
        {
            "merchant_id": merchant_id,
            "odoo_uid": odoo_uid,
            "capability_attestation": "admin_trusteed",
        }
    ).encode("utf-8")
    timeout = (_CONNECT_TIMEOUT, _READ_TIMEOUT)

    try:
        # allow_redirects=False: prevent redirect-based SSRF to internal hosts
        resp = requests.post(
            endpoint, data=body, headers=headers, timeout=timeout, allow_redirects=False
        )
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            _logger.error("trusteed issue-token: unexpected redirect from %s", endpoint)
            return {"error": "issue_token_redirect_rejected"}, 400
        try:
            decoded = resp.json()
        except ValueError:
            decoded = {}
        return decoded, resp.status_code
    except requests.exceptions.Timeout:
        _logger.error("trusteed issue-token timeout")
        return {"error": "issue_token_timeout"}, 504
    except requests.exceptions.ConnectionError as exc:
        _logger.error("trusteed issue-token connection error: %s", exc)
        return {"error": "issue_token_connection_error"}, 503
    except Exception as exc:  # pragma: no cover  # defensive
        _logger.exception("trusteed issue-token unexpected error: %s", type(exc).__name__)
        return {"error": "issue_token_failed"}, 500


class TrusteedController(http.Controller):

    @http.route(
        "/trusteed/token",
        type="json",
        auth="user",
        csrf=True,
        methods=["POST"],
    )
    def exchange_bootstrap(self):
        """Issue a bootstrap JWT and exchange it for an Ed25519 access token."""
        user = request.env.user
        if not user or user._is_public():
            return {"error": "unauthorized"}

        # Group check — must hold group_user before any ICP reads
        if not request.env.user.has_group("trusteed.group_user"):
            return {"error": "unauthorized"}

        ICP = request.env["ir.config_parameter"].sudo()
        merchant_id = ICP.get_param("trusteed.merchant_id", "")
        secret = ICP.get_param("trusteed.bootstrap_secret", "")
        api_base = ICP.get_param("trusteed.api_base", "https://api.trusteed.xyz")

        if not merchant_id or not secret:
            _logger.warning(
                "trusteed bootstrap skipped — merchant_id or secret not configured"
            )
            return {"error": "configure_module_first"}

        # SSRF prevention — validate api_base before constructing endpoint
        if not validate_api_base(api_base):
            _logger.error("trusteed bootstrap blocked — invalid api_base (SSRF guard)")
            return {"error": "invalid_api_base"}

        # Capture current company AT ISSUANCE — analysis v2 §4.3 race fix
        company = request.env.company
        company_id = int(company.id) if company else 0
        company_ids = request.env.user.company_ids.ids  # all allowed companies

        # F-011: guard against edge cases where active company is outside allowed set
        if company_id and company_id not in company_ids:
            _logger.warning("trusteed bootstrap: company_id %s not in allowed companies", company_id)
            return {"error": "company_mismatch"}

        # X1: mint the OPAQUE data-plane token via the S2S relay (the SPA data
        # routes validate this token, not the Ed25519 bootstrap token). Company
        # scope is captured above purely as an issuance gate; the opaque token is
        # merchant-scoped, so no company/db claims are forwarded.
        odoo_uid = str(int(user.id))
        endpoint = api_base.rstrip("/") + _ISSUE_TOKEN_ENDPOINT
        # Secret consumed here — not stored in any variable after this call
        decoded, status = _do_issue_token(endpoint, secret, merchant_id, odoo_uid)
        del secret  # help GC; secret must not linger on the call stack

        if status != 200 or not decoded.get("token"):
            _logger.warning(
                "trusteed issue-token rejected — status=%s reason=%s",
                status,
                decoded.get("error") or decoded.get("message"),
            )
            return {
                "error": "issue_token_rejected",
                "status": status,
                "reason": decoded.get("error") or decoded.get("message"),
            }

        access_token = decoded.get("token")
        # merchant_id intentionally omitted — group_user gate allows all employees,
        # exposing it would leak a credential identifier.
        # api_base IS safe to return: it is the same value the SPA needs to
        # know where to send its own browser-side requests (mirrors the
        # WooCommerce/PrestaShop admin-spa loaders, which localize this same
        # value to the browser). Without it the SPA falls back to its
        # bundled default (production api.trusteed.xyz), which cannot
        # validate a token minted by any other api_base.
        return {
            "success": True,
            "expires_at": decoded.get("expires_at"),
            "access_token": access_token,
            "api_base": api_base,
        }
