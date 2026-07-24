"""Trusteed settings — extends Odoo General Settings form."""

import re

from odoo import fields, models
from odoo.exceptions import ValidationError

from ..utils.ssrf import validate_api_base as _validate_api_base

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
        super().set_values()
