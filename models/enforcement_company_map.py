"""Trusteed CEL — Company → MerchantId mapping for multi-company enforcement.

Maps each Odoo res.company to a Trusteed merchantId so that multi-company
deployments (e.g. Odoo.sh with several legal entities) can apply per-company
CEL configuration independently.

Usage in enforcement code:

    mapping = TrusteedCompanyEnforcementMap.get_for_company(env, company_id)
    if mapping and mapping['celEnabled']:
        merchant_id = mapping['merchantId']
        fallback_mode = mapping['fallbackMode']

When no mapping exists for the current company the enforcement layer falls
back to the global ir.config_parameter values read by
``EnforcementSnapshotService.get_snapshot()``.
"""

import logging
from typing import Optional

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class TrusteedCompanyEnforcementMap(models.Model):
    """Per-company Trusteed merchant configuration for CEL.

    One record per company.  The admin sets the merchantId and optionally an
    installationId (used for HMAC-signed snapshot requests).  The fallbackMode
    controls the fail-open/fail-closed behaviour when a snapshot is unavailable
    or a kill-switch is active.
    """

    _name = "trusteed.company.enforcement.map"
    _description = "Trusteed Company to MerchantId mapping for multi-company CEL"
    _rec_name = "merchant_id"

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        ondelete="cascade",
        index=True,
        help="Odoo company this mapping applies to.",
    )
    merchant_id = fields.Char(
        string="Merchant ID",
        required=True,
        help="Trusteed merchant UUID assigned to this company.",
    )
    installation_id = fields.Char(
        string="Installation ID",
        help="Trusteed installation identifier used for HMAC snapshot auth.",
    )
    fallback_mode = fields.Selection(
        selection=[
            ("strict", "Strict — block on any enforcement failure"),
            ("balanced", "Balanced — fail-open with warning log"),
            ("permissive", "Permissive — always pass"),
        ],
        string="Fallback Mode",
        default="balanced",
        required=True,
        help=(
            "Controls behaviour when the snapshot is unavailable or the "
            "kill-switch is active. 'strict' blocks the order; 'balanced' "
            "logs a warning and allows it; 'permissive' allows it silently."
        ),
    )
    cel_enabled = fields.Boolean(
        string="CEL Enabled",
        default=True,
        help="When unchecked, Trusteed CEL is completely disabled for this company.",
    )

    _sql_constraints = [
        (
            "company_unique",
            "UNIQUE(company_id)",
            "There can only be one Trusteed mapping per company.",
        )
    ]

    # ------------------------------------------------------------------
    # Class-level helper
    # ------------------------------------------------------------------

    @api.model
    def get_for_company(self, company_id: int) -> Optional[dict]:
        """Return CEL config dict for the given company_id, or None if absent.

        The returned dict has the following keys (all str unless noted):
          - merchantId   (str)
          - installationId (str | None)
          - fallbackMode   ('strict' | 'balanced' | 'permissive')
          - celEnabled     (bool)

        This method uses sudo() to read the mapping regardless of the current
        user's access rights, since CEL enforcement runs in a system context.

        Parameters
        ----------
        company_id:
            The ``res.company`` integer ID to look up.
        """
        if not isinstance(company_id, int) or company_id <= 0:
            _logger.warning(
                "CEL get_for_company: invalid company_id=%r", company_id
            )
            return None

        record = (
            self.sudo()
            .search([("company_id", "=", company_id)], limit=1)
        )
        if not record:
            return None

        return {
            "merchantId": record.merchant_id or "",
            "installationId": record.installation_id or None,
            "fallbackMode": record.fallback_mode or "balanced",
            "celEnabled": bool(record.cel_enabled),
        }
