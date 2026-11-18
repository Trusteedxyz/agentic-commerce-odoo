"""Comunica a Trusteed la página de devoluciones que escribió el comercio.

Lógica pura en ``utils/policy_pages.py``. Fallos: sólo log, nunca fatales.
No depende del módulo ``website``: sólo lo usa si existe en el entorno.
"""

import functools
import logging

from odoo import SUPERUSER_ID, api, models

from ..utils import paypal_sync as ps
from ..utils import policy_pages as pp_utils
from . import payment_provider_paypal_sync as pp

_logger = logging.getLogger(__name__)

PARAM_RETURNS_URL = "trusteed.returns_page_url"


def _collect(registry, company_id: int):
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        icp = env["ir.config_parameter"].sudo()
        company_count = env["res.company"].sudo().search_count([])
        mapping = env["trusteed.company.enforcement.map"].get_for_company(company_id)
        merchant_id = ps.resolve_merchant_id(
            (mapping or {}).get("merchantId"), icp.get_param("trusteed.merchant_id"), company_count
        )
        param = icp.search([("key", "=", PARAM_RETURNS_URL)], limit=1)
        website_model = env.get("website")  # None si el módulo no está instalado
        domains = website_model.sudo().search([]).mapped("domain") if website_model is not None else []
        snapshot = {
            "merchant_id": merchant_id,
            "api_base": (icp.get_param("trusteed.api_base") or pp._DEFAULT_API_BASE).strip(),
            "pages": pp_utils.build_policy_pages(
                param.value if param else "",
                icp.get_param("web.base.url"),
                domains,
                param.write_date if param else None,  # cuándo lo fijó el comercio
            ),
        }
        bootstrap_secret = icp.get_param("trusteed.bootstrap_secret") or ""
    return snapshot, bootstrap_secret


def run_company_sync(registry, company_id: int, odoo_uid: int) -> None:
    """Un intento por compañía; nunca propaga excepciones ni registra secretos."""
    try:
        snapshot, bootstrap_secret = _collect(registry, company_id)
        if not snapshot["merchant_id"]:
            return
        token = pp._make_token_provider(snapshot, bootstrap_secret, odoo_uid)()
        del bootstrap_secret
        if not token:
            _logger.warning("trusteed policy pages: company=%s token_unavailable", company_id)
            return
        res = pp_utils.send_policy_pages(snapshot["api_base"], token, snapshot["pages"])
        _logger.info(
            "trusteed policy pages: company=%s sent=%s ok=%s code=%s reason=%s",
            company_id, len(snapshot["pages"]), res["ok"], res["code"], res.get("reason"),
        )
    except Exception as exc:  # noqa: BLE001 — sólo el tipo, el mensaje podría arrastrar datos
        _logger.error("trusteed policy pages failed: company=%s error=%s", company_id, type(exc).__name__)


class TrusteedPolicyPagesSync(models.AbstractModel):
    _name = "trusteed.policy.pages.sync"
    _description = "Trusteed policy pages sync"

    @api.model
    def schedule_current_company(self):
        """Tras el commit (sin red dentro de la transacción)."""
        postcommit = getattr(self.env.cr, "postcommit", None)
        if postcommit is None:
            _logger.warning("trusteed policy pages sync omitted: cr.postcommit not available")
            return
        postcommit.add(
            functools.partial(run_company_sync, self.env.registry, self.env.company.id, self.env.uid)
        )

    @api.model
    def cron_sync(self):
        for company in self.env["res.company"].sudo().search([]):
            run_company_sync(self.env.registry, company.id, SUPERUSER_ID)
