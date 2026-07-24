"""Trust score functional field on sale.order — T043-062.

Adds two computed (non-stored) fields to sale.order:
  - trusteed_trust_score  (Float): the Trusteed trust score 0.0–100.0.
  - trusteed_trust_level  (Selection): categorical level ('high'/'medium'/'low'/'none').

MVP stub: always returns 0.0 / 'none'.  Production wiring will call the Trusteed API
via ir.config_parameter (trusteed.api_base + trusteed.merchant_id) with a
per-partner cache. See spec 040 / spec 041 for the backend endpoint.
"""

from odoo import api, fields, models


class SaleOrderTrust(models.Model):
    _inherit = "sale.order"

    trusteed_trust_score = fields.Float(
        string="Trust Score",
        compute="_compute_trust_score",
        store=False,
        digits=(6, 2),
        help="Trusteed trust score (0–100). Computed from partner data via Trusteed API.",
    )
    trusteed_trust_level = fields.Selection(
        selection=[
            ("high", "High"),
            ("medium", "Medium"),
            ("low", "Low"),
            ("none", "N/A"),
        ],
        string="Trust Level",
        compute="_compute_trust_score",
        store=False,
        help="Categorical trust level derived from trusteed_trust_score.",
    )

    @api.depends("partner_id")
    def _compute_trust_score(self):
        """Compute trust score and level for each order.

        MVP: always returns 0.0 / 'none' (no-op stub).
        Production: call Trusteed API with partner_id external identifier,
        cache results in a transient model or Redis (via ir.config_parameter
        + requests with a ≤1s timeout), and map score to level:
          score >= 80  → 'high'
          score >= 50  → 'medium'
          score >= 1   → 'low'
          score == 0   → 'none'
        """
        for order in self:
            order.trusteed_trust_score = 0.0
            order.trusteed_trust_level = "none"
