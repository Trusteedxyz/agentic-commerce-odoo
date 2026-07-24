"""Spec 043 T043-065 — Refund proxy emit en account.move.

Detecta reversal/refund invoices (`move_type='out_refund'` con
`reversal_move_id != False`) y emite evento REFUNDED al backend Trusteed
con `dataQuality='proxy'` (Odoo eCommerce no tiene dispute nativo).

Diseña:
- Override de `action_post` (mismo punto que el JWS receipt) — sólo se
  emite TRAS confirmar el post.
- Multi-company: merchantId vía enforcement.company.map.
- Fire-and-forget: errores loggeados, nunca rompen action_post.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone

from odoo import models

from .api_client import TrusteedApiClient, TrusteedApiError
from ..utils.jcs import canonicalize as jcs_canonicalize

_logger = logging.getLogger(__name__)

# R5b (codex P1#4): Shared namespace UUID across PS+Odoo+backend for deterministic jti.
TRUSTEED_NAMESPACE = uuid.UUID("6ba7b815-9dad-11d1-80b4-00c04fd430c8")

# R8 (codex P2#3): Refund window for buyer-attributed refunds. Older reversals
# are assumed to be operator corrections (accounting cleanup) and skipped.
BUYER_REFUND_WINDOW_DAYS = 90


def _deterministic_jti(
    platform: str,
    merchant_id: str,
    order_id: str,
    event_type: str,
    occurred_at_iso: str,
) -> str:
    """Compute deterministic jti via uuidv5 to enable retry-safe dedup."""
    try:
        dt = datetime.fromisoformat(occurred_at_iso.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    dt_floor = (
        dt.replace(second=0, microsecond=0)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    name = f"{platform}|{merchant_id}|{order_id}|{event_type}|{dt_floor}"
    return str(uuid.uuid5(TRUSTEED_NAMESPACE, name))


class AccountMoveRefundProxy(models.Model):
    _inherit = "account.move"

    def action_post(self):
        result = super().action_post()
        # R13 (codex P2#8 / DECISION D5): accept manual credit notes (no reversed_entry_id)
        # as self_declared. Reversed credit notes go through R8 heuristic below.
        for move in self.filtered(
            lambda m: m.move_type == "out_refund" and m.state == "posted"
        ):
            try:
                self._maybe_emit_refund_proxy(move)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "account_move_refund_proxy emit failed move=%s: %s",
                    move.id,
                    exc,
                )
        return result

    def _maybe_emit_refund_proxy(self, move) -> None:
        merchant_id = self._resolve_merchant_id(move.company_id.id)
        if not merchant_id:
            return

        config = self.env["ir.config_parameter"].sudo()
        api_base = config.get_param("trusteed.api_base", "")
        bootstrap = config.get_param("trusteed.access_token", "") or ""
        hmac_secret = config.get_param("trusteed.hmac_secret", "") or ""
        installation_id = config.get_param("trusteed.installation_id", "") or ""

        if not api_base or not bootstrap or not hmac_secret or not installation_id:
            return

        reversed_invoice = move.reversed_entry_id
        sale = None

        # R8 (codex P2#3 / DECISION D3): for reversal flow, only attribute to buyer
        # when the reversed invoice was actually paid AND recent (<=90d). Older or
        # unpaid reversals are accounting corrections, not buyer refunds — skip.
        # R13 (DECISION D5): manual credit notes (no reversed_entry_id) emit as
        # self_declared with cancellationReasonOwner='unknown'.
        if reversed_invoice:
            payment_state = getattr(reversed_invoice, "payment_state", None)
            if payment_state not in ("paid", "in_payment"):
                # Operator correction or in-progress — skip emit.
                return
            invoice_date = getattr(reversed_invoice, "invoice_date", None)
            if not invoice_date:
                # Can't tell age → assume operator cleanup, skip.
                return
            try:
                age_days = (datetime.now(timezone.utc).date() - invoice_date).days
            except TypeError:
                # invoice_date already date-only; compute against today.
                from datetime import date as _date
                age_days = (_date.today() - invoice_date).days
            if age_days > BUYER_REFUND_WINDOW_DAYS:
                # Stale → operator cleanup, skip.
                return
            sale = reversed_invoice.line_ids.mapped("sale_line_ids.order_id")[:1]
            cancellation_owner = "buyer"
            data_quality = "proxy"
        else:
            # R13: manual credit note — accept with weaker confidence.
            cancellation_owner = "unknown"
            data_quality = "self_declared"

        external_order_id = (
            str(sale.id)
            if sale
            else (move.invoice_origin or move.name or str(move.id))
        )

        agent_did = (
            getattr(sale, "x_trusteed_agent_did", None) or None
            if sale
            else None
        )

        payload = {
            "externalOrderId": str(external_order_id),
            "moveId": move.id,
            "moveName": move.name or "",
            "reversedMoveId": reversed_invoice.id if reversed_invoice else None,
            "amount": float(move.amount_total or 0.0),
            "currency": (move.currency_id and move.currency_id.name) or "EUR",
            "companyId": move.company_id.id,
            "cancellationReasonOwner": cancellation_owner,
            "dataQuality": data_quality,
            "subtype": "credit_note",
            **({"agentDid": agent_did} if agent_did else {}),
        }
        # R1 (codex P1#1): RFC 8785 JCS for cross-language byte-equal hash.
        payload_hash = "sha256:" + hashlib.sha256(
            jcs_canonicalize(payload).encode("utf-8")
        ).hexdigest()

        occurred_dt = move.date or move.invoice_date
        if not occurred_dt:
            return
        occurred_at_iso = occurred_dt.isoformat() + "T00:00:00Z"

        # R5b (codex P1#4): deterministic jti — retry-safe dedup.
        jti = _deterministic_jti(
            "ODOO", merchant_id, str(external_order_id), "REFUNDED", occurred_at_iso
        )
        body = {
            "jti": jti,
            "merchantId": merchant_id,
            "installationId": installation_id,
            "platform": "ODOO",
            "eventType": "REFUNDED",
            "orderId": str(external_order_id),
            "occurredAt": occurred_at_iso,
            "payloadHash": payload_hash,
            "payload": payload,
        }

        # R1 (codex P1#1): JCS canonical of body minus signature for HMAC.
        body_no_sig = {k: v for k, v in body.items() if k != "signature"}
        canonical = jcs_canonicalize(body_no_sig)
        body["signature"] = hmac.new(
            hmac_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        try:
            client = TrusteedApiClient(api_base, bootstrap)
            client.call(
                endpoint="/api/v1/ucp/webhooks/odoo/order-events",
                payload=body,
                idempotency_key=jti,
            )
        except TrusteedApiError as exc:
            _logger.warning(
                "trusteed odoo refund proxy emit failed move=%s: %s",
                move.id,
                exc,
            )

    def _resolve_merchant_id(self, company_id: int) -> str | None:
        mapping = self.env["enforcement.company.map"].sudo().search(
            [("company_id", "=", company_id)], limit=1
        )
        if not mapping:
            # R10 (codex P2#5): observable failure mode for unmapped company.
            try:
                company_name = self.env["res.company"].sudo().browse(company_id).name
            except Exception:  # noqa: BLE001
                company_name = "<unknown>"
            _logger.warning(
                "unmapped_company_id (refund_proxy) company_id=%s company_name=%s — "
                "configure enforcement.company.map to enable event emission",
                company_id,
                company_name,
            )
            try:
                import sentry_sdk  # type: ignore
                sentry_sdk.add_breadcrumb(
                    category="odoo.refund_proxy",
                    level="warning",
                    message="unmapped_company_id",
                    data={
                        "company_id": company_id,
                        "company_name": company_name,
                        "no_mapping_found": True,
                    },
                )
            except ImportError:
                pass
            # TODO(R10): expose Prometheus counter once Odoo has prom-client plugin.
            return None
        return mapping.merchant_id
