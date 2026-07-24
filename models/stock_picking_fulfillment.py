"""Spec 043 T043-064 — Hook stock.picking.button_validate.

Captura el momento en el que un picking outgoing pasa a `state='done'` para
emitir un evento FULFILLED firmado al backend Trusteed.

Internal transfers (`picking_type_code != 'outgoing'`) NO emiten evento.

Multi-company: el merchantId se resuelve por `picking.company_id` vía
`enforcement.company.map` (mismo mecanismo que sale_order_enforcement).

Diseño:
- Override defensivo: el `super().button_validate()` se llama PRIMERO; sólo
  emitimos si el resultado es exitoso y el estado final es 'done'.
- Fire-and-forget: el HTTP POST se ejecuta tras commit; errores quedan
  loggeados pero no rompen el flujo de stock.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone

from odoo import api, models

from .api_client import TrusteedApiClient, TrusteedApiError
from ..utils.jcs import canonicalize as jcs_canonicalize

_logger = logging.getLogger(__name__)

# R5b (codex P1#4): Shared namespace UUID across PS+Odoo+backend for deterministic jti.
TRUSTEED_NAMESPACE = uuid.UUID("6ba7b815-9dad-11d1-80b4-00c04fd430c8")


def _deterministic_jti(
    platform: str,
    merchant_id: str,
    order_id: str,
    event_type: str,
    occurred_at_iso: str,
) -> str:
    """Compute deterministic jti via uuidv5 to enable retry-safe dedup.

    Floors occurred_at to the minute so retries within the same minute collapse
    to the same jti. Namespace is shared across PS/Odoo/backend emitters.
    """
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


class StockPickingFulfillment(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        """Override Odoo button_validate to capture outgoing fulfillment events."""
        result = super().button_validate()

        for picking in self:
            try:
                self._maybe_emit_fulfillment(picking)
            except Exception as exc:  # noqa: BLE001 — defensive
                _logger.warning(
                    "stock_picking_fulfillment emit failed picking=%s: %s",
                    picking.id,
                    exc,
                )

        return result

    @api.model
    def _maybe_emit_fulfillment(self, picking) -> None:
        if picking.picking_type_code != "outgoing":
            return
        if picking.state != "done":
            return

        sale = picking.sale_id
        if not sale:
            return

        merchant_id = self._resolve_merchant_id(picking.company_id.id)
        if not merchant_id:
            return

        config = self.env["ir.config_parameter"].sudo()
        api_base = config.get_param("trusteed.api_base", "")
        bootstrap = config.get_param("trusteed.access_token", "") or ""
        hmac_secret = config.get_param("trusteed.hmac_secret", "") or ""
        installation_id = config.get_param("trusteed.installation_id", "") or ""

        if not api_base or not bootstrap or not hmac_secret or not installation_id:
            return

        date_order = sale.date_order.isoformat() if sale.date_order else None
        date_done = picking.date_done.isoformat() if picking.date_done else None

        hours_to_fulfill: float | None = None
        if sale.date_order and picking.date_done:
            delta = picking.date_done - sale.date_order
            hours_to_fulfill = max(0.0, delta.total_seconds() / 3600.0)

        agent_did = getattr(sale, "x_trusteed_agent_did", None) or None

        payload = {
            "externalOrderId": str(sale.id),
            "orderName": sale.name or "",
            "pickingId": picking.id,
            "pickingName": picking.name or "",
            "companyId": picking.company_id.id,
            "dateOrder": date_order,
            "dateDone": date_done,
            "hoursToFulfill": hours_to_fulfill,
            "totalPaid": float(sale.amount_total or 0.0),
            "currency": (sale.currency_id and sale.currency_id.name) or "EUR",
            "dataQuality": "measured",
            **({"agentDid": agent_did} if agent_did else {}),
        }

        # R1 (codex P1#1): RFC 8785 JCS for cross-language byte-equal hash.
        payload_hash = "sha256:" + hashlib.sha256(
            jcs_canonicalize(payload).encode("utf-8")
        ).hexdigest()

        # Compute occurredAt first (required by deterministic jti).
        occurred_dt = picking.date_done or sale.date_order
        if not occurred_dt:
            return
        occurred_at_iso = occurred_dt.isoformat() + "Z"

        # R5b/R9 (codex P1#4 + P2#4): deterministic jti per (platform, merchant,
        # order|picking, event, minute). order_id includes picking_id so each
        # picking of the same sale.order gets a unique jti (preserves audit
        # granularity), while cancel+revalidate of the same picking dedups.
        event_order_id = f"{sale.id}|picking-{picking.id}"
        jti = _deterministic_jti(
            "ODOO", merchant_id, event_order_id, "FULFILLED", occurred_at_iso
        )
        body = {
            "jti": jti,
            "merchantId": merchant_id,
            "installationId": installation_id,
            "platform": "ODOO",
            "eventType": "FULFILLED",
            "orderId": str(sale.id),
            "occurredAt": occurred_at_iso,
            "payloadHash": payload_hash,
            "payload": payload,
        }

        # R1 (codex P1#1): JCS canonical of body minus signature for HMAC.
        body_no_sig = {k: v for k, v in body.items() if k != "signature"}
        canonical = jcs_canonicalize(body_no_sig)
        signature = hmac.new(
            hmac_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        body["signature"] = signature

        # POST to backend — fire-and-forget.
        try:
            client = TrusteedApiClient(api_base, bootstrap)
            client.call(
                endpoint="/api/v1/ucp/webhooks/odoo/order-events",
                payload=body,
                idempotency_key=jti,
            )
        except TrusteedApiError as exc:
            _logger.warning(
                "trusteed odoo fulfillment emit failed picking=%s: %s",
                picking.id,
                exc,
            )

    def _resolve_merchant_id(self, company_id: int) -> str | None:
        """Resolve merchantId from company_id via enforcement.company.map."""
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
                "unmapped_company_id company_id=%s company_name=%s — "
                "configure enforcement.company.map to enable event emission",
                company_id,
                company_name,
            )
            try:
                import sentry_sdk  # type: ignore
                sentry_sdk.add_breadcrumb(
                    category="odoo.fulfillment",
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
