"""Spec-048 4.9 — reporta a Trusteed qué señales de carrito sabe proyectar
ESTA instalación de Odoo.

POR QUÉ EXISTE. El servidor sabe qué señal lee cada regla
(``RULE_SIGNALS_READ``), pero no sabía qué aporta cada instalación. Sin ese
cruce, una regla cuya señal no llega devuelve ``NO_SIGNAL`` en cada checkout:
pasa en silencio, y el comerciante ve una regla en ENFORCE que no bloquea nada.
Con el reporte, el panel puede avisarle al activarla.

CUÁNDO SE MANDA. Al cambiar la versión del addon. No va por checkout — es un
dato que cambia una vez por release.

FIRMA. HMAC-SHA256 sobre el JSON canónico del cuerpo sin ``signature``. La
canonicalización (claves ordenadas, sin espacios, ``ensure_ascii=False``) es
byte-equivalente a la RFC 8785 que usa el servidor para cuerpos de cadenas y
listas planas como este.

La lista de señales sale de ``SaleOrder._trusteed_signals_provided()``, que un
gate en ``packages/shared`` mantiene pegada a lo que el constructor de contexto
escribe de verdad — y que corre siempre, a diferencia de esta suite, que en el
entorno de desarrollo ni se recolecta por faltar el módulo ``odoo``.
"""

import hashlib
import hmac
import json
import logging
from typing import Optional

import requests

_logger = logging.getLogger(__name__)

ENDPOINT_PATH = "/api/v1/enforcement/capabilities"

#: Parámetro donde se recuerda la última versión reportada.
PARAM_REPORTED_VERSION = "trusteed.cel_caps_reported_version"


def canonical_json(body: dict) -> str:
    """JSON canónico: claves ordenadas, separadores sin espacios.

    Las listas conservan su orden a propósito — el servidor verifica la firma
    contra el cuerpo TAL COMO LLEGA, no contra el que él normaliza, así que
    reordenar aquí sólo cambiaría la firma sin ganar nada.
    """
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def sign_body(body: dict, hmac_secret: str) -> str:
    return hmac.new(
        hmac_secret.encode("utf-8"),
        canonical_json(body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def report_capabilities(
    api_base: str,
    merchant_id: str,
    installation_id: str,
    hmac_secret: str,
    addon_version: str,
    signals_provided: list,
) -> bool:
    """Manda el reporte. Devuelve True si el servidor lo aceptó (202).

    Nunca propaga: que el diagnóstico no llegue no puede romper nada del flujo
    del comprador.
    """
    if not (api_base and merchant_id and installation_id and hmac_secret):
        return False

    body = {
        "installationId": installation_id,
        "merchantId": merchant_id,
        "platform": "ODOO",
        "pluginVersion": addon_version,
        # El vocabulario del servidor prefija con `cartAttr.`; el addon trabaja
        # con la clave cruda del atributo de carrito.
        "signalsProvided": ["cartAttr." + attr for attr in signals_provided],
    }
    body["signature"] = sign_body(body, hmac_secret)

    url = f"{api_base.rstrip('/')}{ENDPOINT_PATH}"
    try:
        resp = requests.post(
            url,
            json=body,
            timeout=5,
            verify=True,
            allow_redirects=False,
        )
    except Exception as exc:  # noqa: BLE001 — nunca propagar
        _logger.warning("[spec-048 4.9] capabilities report error: %s", exc)
        return False

    if resp.status_code != 202:
        _logger.warning(
            "[spec-048 4.9] capabilities report HTTP %s from %s",
            resp.status_code,
            url,
        )
        return False
    return True


def maybe_report(env, addon_version: str) -> Optional[bool]:
    """Reporta si la versión del addon cambió desde el último envío.

    El servidor también deduplica (responde ``unchanged: true`` sin escribir),
    pero comprobarlo aquí evita una petición HTTP innecesaria.
    """
    cfg = env["ir.config_parameter"].sudo()
    if cfg.get_param(PARAM_REPORTED_VERSION) == addon_version:
        return None

    order_model = env["sale.order"]
    ok = report_capabilities(
        api_base=cfg.get_param("trusteed.api_base", ""),
        merchant_id=cfg.get_param("trusteed.merchant_id", ""),
        installation_id=cfg.get_param("trusteed.cel_installation_id", ""),
        hmac_secret=cfg.get_param("trusteed.cel_hmac_secret", ""),
        addon_version=addon_version,
        signals_provided=order_model._trusteed_signals_provided(),
    )
    if ok:
        cfg.set_param(PARAM_REPORTED_VERSION, addon_version)
    return ok
