"""Trusteed CEL — válvula offline sobre el ``create`` headless de una PERSONA (Task 38d).

Un ``create`` RPC de un pedido ya en ``sale`` y SIN token de agente es una
persona (política D1: nunca se la bloquea por una avería nuestra). Hasta aquí
ese camino no evaluaba nada, ni siquiera las reglas ``ALL`` (tope universal
R030) que sí se le aplican a la misma persona en ``action_confirm``.

Aquí se aplica LA MISMA válvula offline que ``action_confirm`` (reglas ``ALL``/
``HUMAN`` del snapshot firmado y verificado de la compañía del PEDIDO, contra el
contexto de ese pedido), sin llamar a la capa 2 y sin bloquear por avería:

- snapshot ausente, manipulado, caducado o caído ⇒ la persona pasa;
- compañía no determinable ⇒ la persona pasa;
- cualquier excepción propia ⇒ la persona pasa (un error de BD se relanza para
  que el ORM reintente, igual que en el resto del flujo).

Se evalúa DESPUÉS de ``super().create``: el importe del pedido (``amount_total``,
con impuestos) es un campo calculado que sólo existe en el registro, y es el
mismo que ve ``action_confirm``. Si la válvula bloquea, la excepción propaga y
el ORM deshace la transacción. Un agente (token) no pasa por aquí: se rechaza
antes de crear (``CEL-HEADLESS-CONFIRM-REQUIRED``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable, Optional

from odoo.exceptions import ValidationError

from ..utils.agent_reason import block_message

_logger = logging.getLogger(__name__)

_TOKEN_CONTEXT_KEY = "trusteed_agent_token"


def enforce_created_human_orders(
    orders,
    company_id_of: Callable[[object], Optional[int]],
    reraise_if_db_error: Callable[[Exception], None],
) -> None:
    """Aplica la válvula offline a cada pedido recién creado en ``sale`` sin token."""
    for order in orders:
        if not _is_human_sale_order(order):
            continue
        try:
            _enforce_one(order, company_id_of(order))
        except ValidationError:
            raise
        except Exception as exc:
            reraise_if_db_error(exc)
            # D1: una avería nuestra no bloquea a una persona.
            _logger.warning("CEL headless human valve error (order %s): %s",
                            getattr(order, "id", None), exc)


def _is_human_sale_order(order) -> bool:
    if getattr(order, "state", None) != "sale":
        return False
    token = getattr(order, "x_trusteed_agent_token", None) or order.env.context.get(
        _TOKEN_CONTEXT_KEY
    )
    return not token


def _enforce_one(order, company_id: Optional[int]) -> None:
    from .offline_safety_valve_evaluator import evaluate as offline_evaluate
    from .platform_flow import offline_valve_rules_for

    eval_id = str(uuid.uuid4())[:8]
    if company_id is None:
        _logger.warning("CEL[%s] headless human create: company not determinable — not evaluated (D1)", eval_id)
        return
    snapshot, _kind, _local_mode = order._trusteed_cel_snapshot(eval_id, company_id)
    if snapshot is None:
        _logger.warning("CEL[%s] headless human create: no verified snapshot — person passes (D1)", eval_id)
        return
    rules = offline_valve_rules_for(snapshot.get("rules", []), False)
    order_context, cart_attributes = order._trusteed_offline_context()
    # M1: this path only evaluates persons (D1) => currency-not-comparable skips the amount.
    order_context = {**order_context, "agentCheckout": False}
    block = offline_evaluate(rules, order_context, cart_attributes)
    if block is None:
        return
    _logger.warning(
        "CEL[%s] headless human create: offline safety valve BLOCK ruleCode=%s reason=%s",
        eval_id, block["ruleCode"], block["reason"],
    )
    raise ValidationError(block_message(None, eval_id))
