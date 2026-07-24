"""account.move JWS attachment — T043-061.

Hooks into account.move.action_post() to attach a Trusteed JWS Trust Receipt
as ir.attachment (mimetype application/jose) on customer invoices and refunds.

Architecture:
  - Override action_post via _inherit — canonical Odoo pattern (precedent: account_edi).
  - _fetch_jws_for_move performs: bootstrap exchange → GET /v1/trust/receipts/by-order/:orderId
  - Non-blocking: any error (network, unconfigured, 4xx) returns None and is logged.
  - .sudo() on ir.attachment creation is standard for system-level hooks.
  - Idempotency: skips attach if a .jose attachment already exists for this invoice.

Known limitation: synchronous HTTP call inside action_post adds ≤6s worst-case latency.
Production recommendation: move _attach_trusteed_jws to a background BullMQ-style job
once async workers are available in this addon context.

Ref: specs/043-odoo-embed-shell/SPIKE-F6-ACCOUNT-MOVE.md
     Spec 040 — GET /api/v1/trust/receipts/by-order/:orderId (FR-003b)
     ADR-022 — embed bootstrap two-step
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse

import requests
import requests.exceptions

from odoo import models
from odoo.addons.trusteed.utils.ssrf import validate_api_base

_logger = logging.getLogger(__name__)

_BOOTSTRAP_ENDPOINT = "/api/v1/auth/embed-bootstrap"
_RECEIPTS_BY_ORDER  = "/api/v1/trust/receipts/by-order"
_ADDON_USER_AGENT   = "TrusteedAddon-Odoo/1.0"
# (connect_s, read_s) — bootstrap + fetch must not block action_post more than ~6s total.
_BOOTSTRAP_TIMEOUT = (2, 3)
_RECEIPTS_TIMEOUT  = (1, 3)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sign_bootstrap_for_move(
    merchant_id: str,
    secret: str,
    db_name: str,
    company_id: int = 0,
    company_ids: list | None = None,
) -> str:
    """Issue a short-lived HS256 JWT for the receipts fetch call.

    S043-002: includes company_id / company_ids so the backend can enforce the
    same company-isolation semantics as the OWL-initiated bootstrap flow.
    """
    now = int(time.time())
    payload = {
        "merchant_id": merchant_id,
        "iss": f"odoo:{db_name}",
        "iat": now,
        "exp": now + 30,
        "jti": secrets.token_hex(16),
        "scope": {
            "source": "account_move_jws",
            "company_id": company_id,
            "company_ids": company_ids if company_ids is not None else ([company_id] if company_id else []),
        },
    }
    header = {"alg": "HS256", "typ": "JWT", "kid": merchant_id}
    h64 = _b64url(json.dumps(header,  separators=(",", ":")).encode("utf-8"))
    p64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    msg = f"{h64}.{p64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return f"{h64}.{p64}.{_b64url(sig)}"


def _exchange_bootstrap(api_base: str, bootstrap_jwt: str) -> str | None:
    """Exchange a bootstrap JWT for an Ed25519 access token. Returns None on any error."""
    try:
        resp = requests.post(
            api_base.rstrip("/") + _BOOTSTRAP_ENDPOINT,
            json={"source": "odoo-account-move"},
            headers={
                "Authorization": f"Bearer {bootstrap_jwt}",
                "User-Agent": _ADDON_USER_AGENT,
                "Content-Type": "application/json",
            },
            timeout=_BOOTSTRAP_TIMEOUT,
            allow_redirects=False,  # prevent redirect-based SSRF
        )
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            _logger.debug("trusteed account_move_jws: unexpected redirect on bootstrap")
            return None
        if not resp.ok:
            _logger.debug(
                "trusteed account_move_jws: bootstrap returned HTTP %s", resp.status_code
            )
            return None
        body = resp.json() if resp.content else {}
        data = (body.get("data") or {}) if body.get("success") else {}
        return data.get("access_token") or None
    except requests.exceptions.Timeout:
        _logger.debug("trusteed account_move_jws: bootstrap timeout")
        return None
    except Exception as exc:
        _logger.debug(
            "trusteed account_move_jws: bootstrap exchange failed: %s", type(exc).__name__
        )
        return None


class AccountMoveJws(models.Model):
    _inherit = "account.move"

    def action_post(self):
        result = super().action_post()
        for move in self.filtered(
            lambda m: m.move_type in ("out_invoice", "out_refund")
        ):
            self._attach_trusteed_jws(move)
        return result

    def _attach_trusteed_jws(self, move) -> None:
        """Fetch JWS receipt from Trusteed API and attach to the invoice."""
        try:
            # Idempotency: skip if a .jose attachment already exists.
            # Known limitation: non-atomic check — concurrent action_post calls on the
            # same invoice can both pass this guard and create duplicate attachments.
            # Risk is low (concurrent re-post of the same invoice is rare) and the
            # consequence is harmless duplication. A proper fix requires a DB-level
            # unique constraint on (res_model, res_id, mimetype) — needs a migration.
            existing = self.env["ir.attachment"].sudo().search_count([
                ("res_model", "=", "account.move"),
                ("res_id", "=", move.id),
                ("mimetype", "=", "application/jose"),
            ])
            if existing:
                return

            jws = self._fetch_jws_for_move(move)
            if not jws:
                return

            safe_name = (move.name or str(move.id)).replace("/", "-").replace(" ", "_")
            attachment = self.env["ir.attachment"].sudo().create({
                "name": f"trusteed-receipt-{safe_name}.jose",
                "res_model": "account.move",
                "res_id": move.id,
                "mimetype": "application/jose",
                "datas": base64.b64encode(jws.encode("ascii")).decode("ascii"),
                "description": "Trusteed JWS Trust Receipt — auto-attached on post.",
            })
            _logger.info(
                "trusteed account_move_jws: attached JWS receipt to %s (id=%s)",
                move.name,
                move.id,
            )

            # US3 acceptance criterion: surface the receipt in the chatter so the
            # merchant sees cryptographic evidence of the agentic transaction.
            self._post_receipt_to_chatter(move, attachment)
        except Exception as exc:
            # Non-blocking: log and continue. Must never raise out of action_post.
            _logger.warning(
                "trusteed account_move_jws: failed to attach receipt to %s: %s",
                getattr(move, "name", move.id),
                type(exc).__name__,
            )

    def _post_receipt_to_chatter(self, move, attachment) -> None:
        """US3: post the JWS Trust Receipt to the invoice chatter and to the
        chatter of any linked sale.order(s), attaching the .jose file.

        Best-effort and non-blocking — chatter posting must never raise out of
        action_post. `message_post` is the mail.thread mixin method present on
        both account.move and sale.order.
        """
        body = (
            "<p><strong>Trusteed Trust Receipt</strong> attached "
            "(Ed25519 JWS — spec-040). Cryptographic evidence of this "
            "agentic transaction.</p>"
        )
        try:
            attachment_ids = [attachment.id] if attachment else []
            if hasattr(move, "message_post"):
                move.message_post(body=body, attachment_ids=attachment_ids)
        except Exception as exc:  # pragma: no cover - defensive
            _logger.debug(
                "trusteed account_move_jws: chatter post on move %s failed: %s",
                getattr(move, "id", "?"),
                type(exc).__name__,
            )

        # Propagate to the originating sale order(s), when resolvable. The link
        # field differs across Odoo editions; resolve defensively.
        try:
            orders = self._resolve_sale_orders_for_move(move)
            for order in orders:
                if hasattr(order, "message_post"):
                    order.message_post(
                        body=body,
                        attachment_ids=[attachment.id] if attachment else [],
                    )
        except Exception as exc:  # pragma: no cover - defensive
            _logger.debug(
                "trusteed account_move_jws: chatter post on sale order failed: %s",
                type(exc).__name__,
            )

    @staticmethod
    def _resolve_sale_orders_for_move(move):
        """Return the sale.order recordset linked to an invoice, or an empty list.

        Uses ``invoice_line_ids.sale_line_ids.order_id`` (sale module) when
        available; degrades to an empty list when the sale bridge is absent.
        """
        lines = getattr(move, "invoice_line_ids", None)
        if not lines:
            return []
        sale_lines = getattr(lines, "sale_line_ids", None)
        if not sale_lines:
            return []
        orders = getattr(sale_lines, "order_id", None)
        return orders or []

    def _fetch_jws_for_move(self, move) -> str | None:
        """Call Trusteed API to get a JWS Trust Receipt for this invoice.

        Returns the JWS compact string, or None if unconfigured / unavailable / no receipt.
        Uses spec-040 endpoint: GET /api/v1/trust/receipts/by-order/:orderId (FR-003b).
        """
        ICP = self.env["ir.config_parameter"].sudo()
        merchant_id = ICP.get_param("trusteed.merchant_id", "")
        secret      = ICP.get_param("trusteed.bootstrap_secret", "")
        api_base    = ICP.get_param("trusteed.api_base", "https://api.trusteed.xyz")

        if not merchant_id or not secret:
            return None

        # SSRF prevention — validate api_base before any outbound request
        if not validate_api_base(api_base):
            _logger.error(
                "trusteed account_move_jws: blocked outbound call — invalid api_base"
            )
            return None

        # Order reference for the receipts endpoint — prefer sale.order origin.
        # URL-encode to prevent path injection (invoice_origin may contain /, ?, #).
        order_ref = urllib.parse.quote(
            move.invoice_origin
            or (move.name and move.name.replace("/", "-"))
            or str(move.id),
            safe="",
        )

        try:
            db_name = self.env.cr.dbname or "unknown"
        except Exception:
            db_name = "unknown"

        company_id = int(move.company_id.id) if move.company_id else 0
        company_ids = [company_id] if company_id else []
        bootstrap_jwt = _sign_bootstrap_for_move(merchant_id, secret, db_name, company_id, company_ids)
        del secret  # do not retain secret beyond signing

        access_token = _exchange_bootstrap(api_base, bootstrap_jwt)
        if not access_token:
            return None

        try:
            resp = requests.get(
                f"{api_base.rstrip('/')}{_RECEIPTS_BY_ORDER}/{order_ref}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": _ADDON_USER_AGENT,
                },
                timeout=_RECEIPTS_TIMEOUT,
                allow_redirects=False,  # prevent redirect-based SSRF
            )
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                _logger.debug(
                    "trusteed account_move_jws: unexpected redirect on receipt fetch for %s",
                    order_ref,
                )
                return None
            if resp.status_code == 404:
                # No receipt exists yet for this order — normal state before MCP checkout.
                return None
            if not resp.ok:
                _logger.debug(
                    "trusteed account_move_jws: receipts endpoint %s for order %s",
                    resp.status_code,
                    order_ref,
                )
                return None
            body = resp.json() if resp.content else {}
            # S043-003: actual spec-040 /by-order response shape is
            # { orderId, platform, receipts: [{jws, callId, ...}] }
            receipts_list = body.get("receipts") or []
            if receipts_list and isinstance(receipts_list, list):
                return receipts_list[0].get("jws") or None
            return None
        except requests.exceptions.Timeout:
            _logger.debug(
                "trusteed account_move_jws: receipts fetch timeout for %s", order_ref
            )
            return None
        except Exception as exc:
            _logger.debug(
                "trusteed account_move_jws: receipts fetch error for %s: %s",
                order_ref,
                type(exc).__name__,
            )
            return None
