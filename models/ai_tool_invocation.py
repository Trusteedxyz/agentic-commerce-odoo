"""Trusteed AI Tool invocations — AbstractModel backing ir.actions.server ai_tools.

Each public method maps 1-to-1 to an ``ir.actions.server`` record with
``usage='ai_tool'``.  The server action's ``code`` field calls the method and
writes the return value into ``ai['result']`` — the Odoo 19 AI App output slot.

Usage from server-action code field (Odoo 18/19)::

    result = env['trusteed.ai.tool'].run_sign_trust_receipt(orderId, agentId)
    ai['result'] = result

AbstractModel — no database table is created (no _auto = True).

Security:
- All outbound calls go through TrusteedApiClient which enforces HTTPS-only
  and blocks private/reserved IP ranges (utils/ssrf.py).
- Credentials read from ir.config_parameter via sudo() — minimum scope.
- Input arguments validated with Odoo @api.constrains-style checks inline.

Ref: specs/046-cross-platform-agentic-tools/plan.md  §canonical-catalog
"""

import logging
import uuid

from odoo import api, models
from odoo.exceptions import UserError

from .api_client import TrusteedApiClient, TrusteedApiError
from ..utils.tool_toggles import (
    PLANNED_TOOL_IDS,
    is_available as _tool_is_available,
    is_enabled as _tool_is_enabled,
)

_logger = logging.getLogger(__name__)


class TrusteedAiTool(models.AbstractModel):
    _name = "trusteed.ai.tool"
    _description = "Trusteed AI Tool Invocations"

    # ------------------------------------------------------------------
    # FR-018b — per-tool opt-in gating
    # ------------------------------------------------------------------

    def _assert_tool_enabled(self, tool_id: str) -> None:
        """Raise UserError unless the tool is available AND toggled on.

        `planned` tools (no deployed backend — sign-trust-receipt,
        dispatch-payment-ap2) are always unavailable; payment rails default OFF
        (opt-in). The toggle is read from ir.config_parameter.
        """
        icp = self.env["ir.config_parameter"].sudo()
        if not _tool_is_available(tool_id):
            if tool_id in PLANNED_TOOL_IDS:
                raise UserError(
                    f"Tool '{tool_id}' is not available: its backend is not yet "
                    "deployed (planned). It cannot be invoked."
                )
            raise UserError(f"Unknown tool '{tool_id}'.")
        if not _tool_is_enabled(tool_id, icp.get_param):
            raise UserError(
                f"Tool '{tool_id}' is disabled. Enable it in Settings → "
                "General Settings (Trusteed Agentic Tools)."
            )

    # ------------------------------------------------------------------
    # Tool 1 — sign-trust-receipt
    # ------------------------------------------------------------------

    @api.model
    def run_sign_trust_receipt(
        self,
        order_id: str,
        agent_id: str | None = None,
    ) -> dict:
        """Sign a Trusteed Trust Receipt for a completed order.

        Args:
            order_id:  Platform order identifier (required).
            agent_id:  Originating agent DID or identifier (optional).

        Returns:
            dict with ``jws`` (compact JWS string) and ``receiptId``.
        """
        self._assert_tool_enabled("trusteed/sign-trust-receipt")
        if not order_id or not str(order_id).strip():
            raise UserError("orderId is required for sign_trust_receipt.")
        client = self._get_api_client()
        payload: dict = {"orderId": str(order_id).strip()}
        if agent_id:
            payload["agentId"] = str(agent_id).strip()
        try:
            # Canonical catalog target (planned/read surface). Gated unavailable
            # by _assert_tool_enabled above; this literal is never reached at
            # runtime (no standalone POST-sign endpoint exists — receipts are a
            # side effect of the checkout pipeline, read via GET).
            return client.call("/v1/embed/trust/receipts", payload)
        except TrusteedApiError as exc:
            _logger.warning("trusteed ai_tool sign_trust_receipt failed: %s", exc)
            raise UserError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Tool 2 — verify-agent-signature
    # ------------------------------------------------------------------

    @api.model
    def run_verify_agent_signature(
        self,
        method: str,
        url: str,
        headers: dict,
    ) -> dict:
        """Verify an RFC 9421 HTTP Message Signature from an agent request.

        Args:
            method:   HTTP method of the signed request (e.g. 'POST').
            url:      Full URL of the signed request.
            headers:  Dict of HTTP headers (must include Signature and Signature-Input).

        Returns:
            dict with ``verified`` (bool), ``agentId``, and ``verificationStatus``.
        """
        self._assert_tool_enabled("trusteed/verify-agent-signature")
        if not method or not url:
            raise UserError("method and url are required for verify_agent_signature.")
        if not isinstance(headers, dict):
            raise UserError("headers must be a dict for verify_agent_signature.")
        client = self._get_api_client()
        merchant_id = self._authoritative_merchant_id()
        try:
            # LANE 046-F1 Option A: the merchant-facing per-store S2S route
            # /api/v1/embed/agentic-tools/verify-agent-signature replaces the
            # staging-only /api/v1/internal/agent-identity/verify route (which the
            # module's bootstrap token could never authenticate). Mirrors the WP +
            # PrestaShop modules.
            return client.call_with_s2s(
                "/api/v1/embed/agentic-tools/verify-agent-signature",
                {"method": str(method).upper(), "url": url, "headers": headers},
                merchant_id,
            )
        except TrusteedApiError as exc:
            _logger.warning("trusteed ai_tool verify_agent_signature failed: %s", exc)
            raise UserError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Tool 3 — dispatch-payment-acp
    # ------------------------------------------------------------------

    @api.model
    def run_dispatch_payment_acp(
        self,
        cart_id: str,
        idempotency_key: str,
        merchant_id: str,
    ) -> dict:
        """Dispatch a payment via the ACP (Agent Commerce Protocol) rail.

        Args:
            cart_id:          UUID of the ACP checkout session / cart.
            idempotency_key:  Caller-supplied idempotency key (UUID recommended).
            merchant_id:      Trusteed merchant UUID.

        Raises:
            UserError: ACP is routed through the MCP checkout bucket tool
                ``process_agent_payment`` (POST /:storeSlug/mcp), not a REST
                route. The Odoo module does not provision the store-slug MCP
                gateway, so this rail is not fulfillable here (fail-closed,
                mirroring the PrestaShop module).
        """
        self._assert_tool_enabled("trusteed/dispatch-payment-acp")
        self._validate_dispatch_args(cart_id, idempotency_key, merchant_id)
        # OLA-1 honesty contract: the canonical ACP backend is the MCP checkout
        # bucket tool `process_agent_payment`, reached via POST /:storeSlug/mcp —
        # NOT a REST route. There is no /api/v1/checkout/dispatch/acp endpoint.
        # The Odoo addon does not provision a store-slug MCP JSON-RPC gateway, so
        # this rail is not fulfillable from the module. Mirror PrestaShop
        # DispatchPaymentAcpTool: fail-closed with an explicit, structured error
        # instead of issuing a guaranteed-404 REST call.
        raise UserError(
            "ACP dispatch is not fulfillable from the Odoo module: the "
            "process_agent_payment MCP tool requires a store-slug MCP gateway "
            "invocation that the module does not provision. Use the x402 rail or "
            "the per-store MCP checkout bucket directly."
        )

    # ------------------------------------------------------------------
    # Tool 4 — dispatch-payment-x402
    # ------------------------------------------------------------------

    @api.model
    def run_dispatch_payment_x402(
        self,
        cart_id: str,
        idempotency_key: str,
        merchant_id: str,
        payment_payload: dict,
    ) -> dict:
        """Dispatch a payment via the x402 (HTTP 402) stablecoin rail.

        Args:
            cart_id:          UUID of the checkout session.
            idempotency_key:  Caller-supplied idempotency key.
            merchant_id:      Trusteed merchant UUID.
            payment_payload:  x402 payment object (network, token, amount, signature).

        Returns:
            dict with ``status``, ``txHash``, ``network``, and ``receiptUri``.
        """
        self._assert_tool_enabled("trusteed/dispatch-payment-x402")
        self._validate_dispatch_args(cart_id, idempotency_key, merchant_id)
        if not isinstance(payment_payload, dict) or not payment_payload:
            raise UserError("payment_payload must be a non-empty dict for dispatch_payment_x402.")
        client = self._get_api_client()
        # LANE 046-F1: resolve the authoritative merchant id server-side and
        # cross-check the caller value (fail-closed on cross-store mismatch).
        authoritative_merchant_id = self._authoritative_merchant_id(merchant_id)
        try:
            # LANE 046-F1 Option A: the merchant-facing per-store S2S route
            # /api/v1/embed/agentic-tools/dispatch-payment-x402 replaces the
            # agent-key-gated /api/v1/agent/checkout/x402/verify route (the module
            # is a server, not an agent — it has no agent key). The backend scopes
            # ownership by store (order.storeId === merchant store). Mirrors the
            # WP + PrestaShop modules.
            return client.call_with_s2s(
                "/api/v1/embed/agentic-tools/dispatch-payment-x402",
                {
                    "cartId": cart_id,
                    "paymentPayload": payment_payload,
                },
                authoritative_merchant_id,
                idempotency_key=idempotency_key,
            )
        except TrusteedApiError as exc:
            _logger.warning("trusteed ai_tool dispatch_payment_x402 failed: %s", exc)
            raise UserError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Tool 5 — dispatch-payment-ap2
    # ------------------------------------------------------------------

    @api.model
    def run_dispatch_payment_ap2(
        self,
        cart_id: str,
        idempotency_key: str,
        merchant_id: str,
        mandate_jwt: str,
    ) -> dict:
        """Dispatch a payment via the AP2 (Agent Payment Protocol v0.2) rail.

        Args:
            cart_id:          UUID of the checkout session.
            idempotency_key:  Caller-supplied idempotency key.
            merchant_id:      Trusteed merchant UUID.
            mandate_jwt:      Signed AP2 mandate JWT from the agent's wallet.

        Returns:
            dict with ``status``, ``paymentId``, ``mandateId``, and ``receiptUri``.
        """
        self._assert_tool_enabled("trusteed/dispatch-payment-ap2")
        self._validate_dispatch_args(cart_id, idempotency_key, merchant_id)
        if not mandate_jwt or not str(mandate_jwt).strip():
            raise UserError("mandate_jwt is required for dispatch_payment_ap2.")
        client = self._get_api_client()
        try:
            # Canonical catalog target (planned). Gated unavailable by
            # _assert_tool_enabled above (AP2 v0.2 is dormant, spec-044); this
            # literal is never reached at runtime — the dispatch route does not
            # exist yet (AP2 adapter only exposes a dormant 503 guard).
            return client.call(
                "/api/v1/protocols/ap2/dispatch",
                {
                    "cartId": cart_id,
                    "merchantId": merchant_id,
                    "mandateJwt": mandate_jwt,
                },
                idempotency_key=idempotency_key,
            )
        except TrusteedApiError as exc:
            _logger.warning("trusteed ai_tool dispatch_payment_ap2 failed: %s", exc)
            raise UserError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_api_client(self) -> TrusteedApiClient:
        """Build an API client from ir.config_parameter credentials."""
        ICP = self.env["ir.config_parameter"].sudo()
        api_base = ICP.get_param("trusteed.api_base", "https://api.trusteed.xyz")
        # Single canonical credential key: trusteed.bootstrap_secret. This is
        # the key written by Settings (res_config_settings.py) and seeded by
        # data/system_parameters.xml. The legacy `bootstrap_token` key is read
        # as a backwards-compat fallback only.
        token = ICP.get_param("trusteed.bootstrap_secret") or ICP.get_param(
            "trusteed.bootstrap_token", ""
        )
        if not token:
            raise UserError(
                "Trusteed API token is not configured. "
                "Set the Bootstrap Secret in Settings → General Settings "
                "(trusteed.bootstrap_secret)."
            )
        return TrusteedApiClient(api_base, token)

    def _authoritative_merchant_id(self, caller_supplied: str | None = None) -> str:
        """Resolve the authoritative per-store merchant id from Odoo config.

        LANE 046-F1 Option A: the merchant id authenticates the merchant-facing
        S2S route and scopes order ownership server-side. It is read from the
        ``trusteed.merchant_id`` system parameter (provisioned in Settings),
        NEVER trusted from caller input. A caller-supplied value is honoured only
        when it MATCHES the configured id (fail-closed on mismatch — mirrors the
        WP MerchantContext / PS MerchantContextGuard IDOR guards).
        """
        ICP = self.env["ir.config_parameter"].sudo()
        configured = str(ICP.get_param("trusteed.merchant_id", "") or "").strip()
        if not configured:
            raise UserError(
                "Trusteed merchant id is not configured. "
                "Set the Merchant ID in Settings → General Settings "
                "(trusteed.merchant_id)."
            )
        if caller_supplied and str(caller_supplied).strip() != configured:
            raise UserError(
                "merchantId does not match this store's configured merchant id "
                "(cross-store access rejected)."
            )
        return configured

    @staticmethod
    def _validate_dispatch_args(cart_id: str, idempotency_key: str, merchant_id: str) -> None:
        """Shared argument guard for payment dispatch tools."""
        if not cart_id or not str(cart_id).strip():
            raise UserError("cartId is required for payment dispatch.")
        if not idempotency_key or not str(idempotency_key).strip():
            raise UserError("idempotency_key is required for payment dispatch.")
        if not merchant_id or not str(merchant_id).strip():
            raise UserError("merchantId is required for payment dispatch.")
        try:
            uuid.UUID(str(idempotency_key))
        except ValueError:
            _logger.debug(
                "trusteed ai_tool: idempotency_key '%s' is not a UUID — accepted anyway",
                idempotency_key,
            )
