"""Trusteed settings — extends Odoo General Settings form."""

import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..utils import paypal_sync as ps
from ..utils.ssrf import validate_api_base as _validate_api_base
from . import payment_provider_paypal_sync as pp

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    trusteed_merchant_id = fields.Char(
        string="Merchant ID",
        config_parameter="trusteed.merchant_id",
        help="UUID provided by Trusteed onboarding portal.",
    )
    trusteed_bootstrap_secret = fields.Char(
        string="Bootstrap Secret",
        config_parameter="trusteed.bootstrap_secret",
        help="Hex secret from Trusteed portal. Keep confidential.",
        groups="trusteed.group_admin",
    )
    trusteed_api_base = fields.Char(
        string="API Base URL",
        config_parameter="trusteed.api_base",
        help="Default: https://api.trusteed.xyz",
    )

    # F4.2: opt-in para entregar a Trusteed las credenciales REST de PayPal que
    # el comercio ya tiene en ``payment_paypal``. Apagado por defecto.
    trusteed_paypal_sync_enabled = fields.Boolean(
        string="Sync PayPal account",
        default=False,
        help=(
            "Sends the PayPal REST client id and secret configured in Odoo's "
            "PayPal provider to Trusteed so agent payments settle in YOUR PayPal "
            "account. Off by default. Applies to the CURRENT company only "
            "(parameter trusteed.paypal_sync_enabled.<company_id>)."
        ),
    )

    # ── FR-018b: per-tool opt-in toggles ──────────────────────────────────────
    # Defaults: sign + verify ON; the three payment rails OFF (opt-in). The two
    # `planned`-backend tools (sign_trust_receipt, dispatch_payment_ap2) keep a
    # toggle so the preference survives, but remain UNAVAILABLE at invocation
    # time until their backend ships (see utils/tool_toggles.py).
    trusteed_tool_sign_trust_receipt = fields.Boolean(
        string="Tool: Sign Trust Receipt",
        config_parameter="trusteed.tool.sign_trust_receipt",
        default=True,
        help="Backend not yet deployed (planned) — tool stays unavailable even when on.",
    )
    trusteed_tool_verify_agent_signature = fields.Boolean(
        string="Tool: Verify Agent Signature",
        config_parameter="trusteed.tool.verify_agent_signature",
        default=True,
    )
    trusteed_tool_dispatch_payment_acp = fields.Boolean(
        string="Tool: Dispatch Payment (ACP)",
        config_parameter="trusteed.tool.dispatch_payment_acp",
        default=False,
        help="Agent-initiated payment — opt-in (default off).",
    )
    trusteed_tool_dispatch_payment_x402 = fields.Boolean(
        string="Tool: Dispatch Payment (x402)",
        config_parameter="trusteed.tool.dispatch_payment_x402",
        default=False,
        help="Agent-initiated payment — opt-in (default off).",
    )
    trusteed_tool_dispatch_payment_ap2 = fields.Boolean(
        string="Tool: Dispatch Payment (AP2)",
        config_parameter="trusteed.tool.dispatch_payment_ap2",
        default=False,
        help="Experimental + backend not deployed (planned) — stays unavailable even when on.",
    )

    def set_values(self):
        """S043-001: Validate api_base and merchant_id before persisting."""
        api_base = (self.trusteed_api_base or "").strip()
        if api_base and not _validate_api_base(api_base):
            raise ValidationError(
                "API Base URL must be an external HTTPS URL on a trusteed.xyz domain "
                "(e.g. https://api.trusteed.xyz)."
            )
        merchant_id = (self.trusteed_merchant_id or "").strip()
        if merchant_id and not _UUID_RE.match(merchant_id):
            raise ValidationError(
                "Merchant ID must be a valid UUID "
                "(e.g. xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)."
            )
        icp = self.env["ir.config_parameter"].sudo()
        company = self.env.company
        company_count = self.env["res.company"].sudo().search_count([])
        was_enabled = ps.resolve_enabled(
            icp.get_param(pp.enabled_param(company.id)),
            icp.get_param(pp.PARAM_ENABLED),
            company_count,
        )
        super().set_values()
        # H2: el opt-in es de la compañía actual (parámetro explícito "1"/"0").
        now_enabled = bool(self.trusteed_paypal_sync_enabled)
        icp.set_param(pp.enabled_param(company.id), ps.flag_param(now_enabled))
        transition = ps.opt_in_transition(was_enabled, now_enabled)
        if transition == "enable":
            # Al activar el opt-in se entrega lo que ya haya configurado.
            self.env["payment.provider"]._trusteed_paypal_schedule_sync_all(company.id)
        elif transition == "disable":
            # L5: sin huella, reactivar vuelve a enviar.
            icp.set_param(pp.hash_param(company.id), False)

    @api.model
    def get_values(self):
        res = super().get_values()
        icp = self.env["ir.config_parameter"].sudo()
        res["trusteed_paypal_sync_enabled"] = ps.resolve_enabled(
            icp.get_param(pp.enabled_param(self.env.company.id)),
            icp.get_param(pp.PARAM_ENABLED),
            self.env["res.company"].sudo().search_count([]),
        )
        return res
