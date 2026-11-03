"""F4.2 — Sincroniza ``payment_paypal`` (Odoo 18) con la plataforma Trusteed.

Herencia de ``payment.provider``. ``payment`` ya viene por ``sale`` (depende de
``payment``), así que el modelo existe sin tocar ``depends``. Los campos
``paypal_client_id``/``paypal_client_secret`` los añade ``payment_paypal`` y NO
se declaran aquí: no se añade esa dependencia al manifest (obligaría a instalar
PayPal en todo comercio). Se comprueba en ejecución con ``self._fields``.

Toda la lógica de decisión, validación y red vive en ``utils/paypal_sync.py``
(Python puro, con tests). Este fichero sólo une el ORM con ella.

NO EJECUTADO en este entorno (no hay Odoo): ver notas de verificación abajo.

Sin verificar contra el código fuente de Odoo 18.0:
  - ``cr.postcommit.add(fn)`` (si no existe, no se hace red dentro de la
    transacción: se omite y se registra un aviso).
  - Que el callback post-commit pueda abrir ``registry.cursor()``.
  - Nombres/grupos de ``paypal_client_id`` y ``paypal_client_secret``
    (la nota de diseño los cita de ``payment_paypal`` 18.0 L27-46).

El secreto de PayPal está en claro en ``payment_provider``: aquí se lee con
``sudo()`` en un cursor propio, viaja sólo al POST de entrega y NO se copia a
logs, mensajes ni a ningún parámetro del sistema (sólo su huella sha256).
"""

import functools
import logging

from odoo import SUPERUSER_ID, api, models

from ..utils import paypal_sync as ps
from ..utils.ssrf import validate_api_base

_logger = logging.getLogger(__name__)

PARAM_ENABLED = "trusteed.paypal_sync_enabled"  # global heredado; vale con 1 compañía
PARAM_ENABLED_PREFIX = "trusteed.paypal_sync_enabled."  # opt-in por compañía
PARAM_HASH_PREFIX = "trusteed.paypal_sync_hash."
_DEFAULT_API_BASE = "https://api.trusteed.xyz"
_ISSUE_TOKEN_ENDPOINT = "/v1/embed/odoo/issue-token"


def hash_param(company_id: int) -> str:
    return f"{PARAM_HASH_PREFIX}{int(company_id)}"


def enabled_param(company_id: int) -> str:
    return f"{PARAM_ENABLED_PREFIX}{int(company_id)}"


def _collect(registry, company_id: int, written_ids=()):
    """Lee, en un cursor propio y corto, todo lo necesario. Sin red aquí."""
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        icp = env["ir.config_parameter"].sudo()
        company_count = env["res.company"].sudo().search_count([])
        providers = env["payment.provider"].sudo().search(
            [("code", "=", "paypal"), ("company_id", "=", company_id)]
        )
        has_fields = bool(providers) and "paypal_client_id" in providers._fields \
            and "paypal_client_secret" in providers._fields
        # M1: se lee el proveedor escrito/activo, no «el primero» que devuelva search.
        view = ps.select_provider(
            [
                {
                    "id": p.id,
                    "state": p.state,
                    "client_id": p.paypal_client_id if has_fields else False,
                    "client_secret": p.paypal_client_secret if has_fields else False,
                }
                for p in providers
            ],
            written_ids,
        )
        map_model = env["trusteed.company.enforcement.map"]
        mapping = map_model.get_for_company(company_id)
        # H2: sin mapa explícito NO se cae al merchant global (salvo 1 compañía).
        merchant_id = ps.resolve_merchant_id(
            (mapping or {}).get("merchantId"), icp.get_param("trusteed.merchant_id"), company_count
        )
        peer_hashes = []
        if merchant_id:
            peers = map_model.sudo().search(
                [("merchant_id", "=", merchant_id), ("company_id", "!=", company_id)]
            )
            peer_hashes = [icp.get_param(hash_param(peer.company_id.id)) or "" for peer in peers]
        snapshot = {
            "enabled": ps.resolve_enabled(
                icp.get_param(enabled_param(company_id)),
                icp.get_param(PARAM_ENABLED),
                company_count,
            ),
            "state": view["state"],
            "client_id": view["client_id"],
            "client_secret": view["client_secret"],
            "provider_error": view["error"],
            "stored_hash": icp.get_param(hash_param(company_id)) or "",
            "merchant_id": merchant_id,
            "peer_hashes": peer_hashes,
            "api_base": (icp.get_param("trusteed.api_base") or _DEFAULT_API_BASE).strip(),
        }
        bootstrap_secret = icp.get_param("trusteed.bootstrap_secret") or ""
    return snapshot, bootstrap_secret


def _store_hash(registry, company_id: int, value: str) -> None:
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        # set_param con valor vacío borra el parámetro (huella limpia tras D5).
        env["ir.config_parameter"].sudo().set_param(hash_param(company_id), value or False)


def _make_token_provider(snapshot: dict, bootstrap_secret: str, odoo_uid: int):
    """Mismo relé S2S que ``/trusteed/token`` (reutiliza ``_do_issue_token``)."""

    def provider():
        if not bootstrap_secret or not validate_api_base(snapshot["api_base"]):
            return None
        # Import tardío: el controlador depende de odoo.http y no de este modelo.
        from ..controllers.main import _do_issue_token

        endpoint = snapshot["api_base"].rstrip("/") + _ISSUE_TOKEN_ENDPOINT
        decoded, status = _do_issue_token(
            endpoint, bootstrap_secret, snapshot["merchant_id"], str(int(odoo_uid))
        )
        return decoded.get("token") if status == 200 else None

    return provider


def run_company_sync(registry, company_id: int, odoo_uid: int, written_ids=()) -> None:
    """Callback post-commit. Nunca propaga excepciones ni registra secretos."""
    try:
        snapshot, bootstrap_secret = _collect(registry, company_id, written_ids)
        outcome = ps.run_sync(snapshot, _make_token_provider(snapshot, bootstrap_secret, odoo_uid))
        del snapshot, bootstrap_secret  # el secreto no se queda en la pila
        if outcome["new_hash"] is not None:
            _store_hash(registry, company_id, outcome["new_hash"])
        _logger.info(
            "trusteed paypal sync: company=%s action=%s ok=%s code=%s reason=%s",
            company_id, outcome["action"], outcome["ok"], outcome["code"], outcome.get("reason"),
        )
    except Exception as exc:  # noqa: BLE001 — sólo el tipo: el mensaje podría arrastrar datos
        _logger.error("trusteed paypal sync failed: company=%s error=%s", company_id, type(exc).__name__)


class PaymentProviderPayPalSync(models.Model):
    _inherit = "payment.provider"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # L5: un proveedor nuevo con credenciales y opt-in activo se sincroniza
        # (run_sync decide; sin credenciales o sin opt-in es un noop).
        records._trusteed_paypal_schedule_sync()
        return records

    def write(self, vals):
        result = super().write(vals)
        if any(field in vals for field in ps.TRIGGER_FIELDS):
            self._trusteed_paypal_schedule_sync()
        return result

    def unlink(self):
        # L5: se captura ANTES de borrar; D5 desconecta si no queda proveedor.
        pending = self._trusteed_paypal_pending()
        result = super().unlink()
        self._trusteed_paypal_enqueue(pending)
        return result

    def _trusteed_paypal_pending(self):
        """``{company_id: (ids de proveedores PayPal tocados,)}``."""
        if "paypal_client_id" not in self._fields:
            return {}  # payment_paypal no está instalado
        pending = {}
        for p in self.sudo():
            if p.code == "paypal" and p.company_id:
                pending.setdefault(p.company_id.id, []).append(p.id)
        return {cid: tuple(ids) for cid, ids in pending.items()}

    def _trusteed_paypal_enqueue(self, pending):
        if not pending:
            return
        postcommit = getattr(self.env.cr, "postcommit", None)
        if postcommit is None:
            _logger.warning("trusteed paypal sync omitted: cr.postcommit not available")
            return
        for company_id in sorted(pending):
            postcommit.add(
                functools.partial(
                    run_company_sync, self.env.registry, company_id, self.env.uid, pending[company_id]
                )
            )

    def _trusteed_paypal_schedule_sync(self):
        """Encola la entrega tras el commit (nada de red dentro de la transacción)."""
        self._trusteed_paypal_enqueue(self._trusteed_paypal_pending())

    def _trusteed_paypal_schedule_sync_all(self, company_id=None):
        """Usado al activar el opt-in: sincroniza los proveedores PayPal de UNA compañía."""
        domain = [("code", "=", "paypal")]
        if company_id:
            domain.append(("company_id", "=", int(company_id)))
        self.sudo().search(domain)._trusteed_paypal_schedule_sync()
