"""Trusteed CEL — guardas de escritura del DID verificado (plan 04, Task 21).

Funciones puras (sin Odoo) que usa ``sale_order_enforcement``:

1. ``x_trusteed_agent_did`` alimenta R023 (``account_move_refund_proxy``) y la
   atribución de envíos (``stock_picking_fulfillment``). Sólo lo escribe el
   propio addon tras verificar un token y consumir su ``jti``. Un
   ``create``/``write`` por RPC podía escribirlo directamente (``readonly`` es
   sólo de la vista): ``strip_agent_did`` lo quita de los ``vals`` salvo que el
   contexto lleve el marcador interno.

2. ``action_confirm`` confirma con ``write({'state': 'sale', …})`` dentro de
   ``super().action_confirm()`` (Odoo 18, ``_prepare_confirmation_values``), y
   nuestro ``write`` volvía a evaluar el mismo pedido: con el token ya visto,
   la segunda pasada es un «replay» (caché de nonces del verificador y
   ``nonce-consume`` del servidor). ``evaluated_marker`` marca los pedidos ya
   evaluados en esta llamada para que ``write`` no los repita.

3. ``create`` rellena los campos ausentes con ``default_<campo>`` del contexto
   (``default_get``), y un cliente RPC controla el contexto: ``create(vals,
   context={'default_x_trusteed_agent_did': ...})`` esquivaba la guarda de
   (1). ``context_without_did_default`` devuelve el contexto sin esa clave.
   Del mismo modo ``default_state='sale'`` y ``default_x_trusteed_agent_token``
   entraban DESPUÉS de mirar ``vals``: ``headless_view`` proyecta lo que el
   ORM va a escribir de verdad.

Los marcadores son OBJETOS Python, no cadenas: un llamante por XML-RPC o
JSON-RPC puede poner cualquier clave en el contexto, pero sólo valores JSON,
así que no puede fabricar un objeto que pase la comprobación de identidad o de
tipo. (Comprobado sólo con stubs: sin Odoo real.)
"""

from typing import Iterable

AGENT_DID_FIELD = "x_trusteed_agent_did"
DEFAULT_DID_KEY = "default_" + AGENT_DID_FIELD

# Clave de contexto con el permiso de escribir el DID; el VALOR debe ser este
# objeto exacto.
INTERNAL_DID_WRITE_KEY = "trusteed_internal_agent_did_write"
INTERNAL_DID_WRITE = object()

# Clave de contexto con los ids ya evaluados en este ``action_confirm``.
EVALUATED_KEY = "trusteed_cel_evaluated_orders"


class _EvaluatedOrders:
    """Conjunto inmutable de ids evaluados; sólo este módulo lo construye."""

    __slots__ = ("ids",)

    def __init__(self, ids: Iterable):
        self.ids = frozenset(i for i in ids if i)

    def __hash__(self):
        return hash(self.ids)

    def __eq__(self, other):
        return isinstance(other, _EvaluatedOrders) and other.ids == self.ids


def internal_did_write_context() -> dict:
    """Contexto con el que el addon escribe el DID verificado."""
    return {INTERNAL_DID_WRITE_KEY: INTERNAL_DID_WRITE}


def strip_agent_did(vals, context) -> dict:
    """``vals`` sin ``x_trusteed_agent_did`` salvo escritura interna del addon.

    Devuelve un diccionario NUEVO (no muta el recibido).
    """
    if not isinstance(vals, dict) or AGENT_DID_FIELD not in vals:
        return vals
    if (context or {}).get(INTERNAL_DID_WRITE_KEY) is INTERNAL_DID_WRITE:
        return vals
    return {k: v for k, v in vals.items() if k != AGENT_DID_FIELD}


def context_without_did_default(context):
    """Contexto NUEVO sin ``default_x_trusteed_agent_did``, o ``None`` si no hay
    nada que quitar o la escritura es interna del addon.

    Quien lo use debe REEMPLAZAR el contexto (``with_context(<dict>)``).
    """
    ctx = context or {}
    key = DEFAULT_DID_KEY
    if key not in ctx or ctx.get(INTERNAL_DID_WRITE_KEY) is INTERNAL_DID_WRITE:
        return None
    return {k: v for k, v in ctx.items() if k != key}


def headless_view(vals, context) -> dict:
    """``vals`` tal como los verá el ORM: los ``default_*`` del contexto rellenan
    ``state`` y ``x_trusteed_agent_token`` cuando ``vals`` no los trae.

    Devuelve un diccionario NUEVO con sólo esas dos claves resueltas (la
    decisión de evaluar mira el estado y el token EFECTIVOS, no los del
    llamante).
    """
    ctx = context or {}
    src = vals if isinstance(vals, dict) else {}
    out = dict(src)
    for field in ("state", "x_trusteed_agent_token"):
        if field not in src and ("default_" + field) in ctx:
            out[field] = ctx["default_" + field]
    return out


def evaluated_marker(ids: Iterable) -> dict:
    """Contexto que marca ``ids`` como ya evaluados en esta confirmación."""
    return {EVALUATED_KEY: _EvaluatedOrders(ids)}


def already_evaluated(context, order_id) -> bool:
    """¿Se evaluó ya ``order_id`` en el ``action_confirm`` en curso?"""
    marker = (context or {}).get(EVALUATED_KEY)
    return isinstance(marker, _EvaluatedOrders) and bool(order_id) and order_id in marker.ids
