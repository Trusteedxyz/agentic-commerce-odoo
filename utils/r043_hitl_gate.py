"""Spec-048 Sprint E.2 T-E45 — R043 HITL Gate (Odoo).

Pure-Python helpers (no Odoo registry deps) for detecting R043 HITL outcomes
from /v1/rules/evaluate responses. The wiring into `sale.order.action_confirm`
lives in `models/sale_order_enforcement.py` — these helpers stay pure so
they're testable with stdlib unittest without an Odoo kernel.

Freeze semantics (applied by the caller in `sale_order_enforcement.py`):
  - state = 'hitl_pending' (custom state via selection_add) — order parked,
    no fulfilment, no invoice. Merchant resolves via dashboard, which
    transitions to 'sale' (approve) or 'cancel' (reject).
  - Selection extension: `state = fields.Selection(selection_add=[('hitl_pending', 'Awaiting Merchant Approval')], ondelete={'hitl_pending': 'set default'})`
  - Audit fields: `x_amcp_hitl_pending`, `x_amcp_hitl_rule_code`,
    `x_amcp_hitl_reason`, `x_amcp_hitl_evaluation_id`.

Detection contract (matches buildHitlResponse in
packages/shared/src/enforcement/rule-evaluator.service.ts):
  - response["decision"] == "BLOCK"
  - response["ucp"]["state"] == "requires_escalation"
  - response["ucp"]["reason_code"] startswith "trusteed:R043"

Compuerta HITL R043 para Odoo — congela sale.order en estado custom
`hitl_pending` hasta resolución del comerciante.
"""

from __future__ import annotations

from typing import Any, Mapping

R043_REASON_PREFIX = "trusteed:R043"

# Custom state code used in `state = fields.Selection(selection_add=[...])`.
HITL_PENDING_STATE = "hitl_pending"

# Audit-trail meta keys stamped on the sale.order record (caller writes via ORM).
META_HITL_PENDING = "x_amcp_hitl_pending"
META_RULE_CODE = "x_amcp_hitl_rule_code"
META_REASON = "x_amcp_hitl_reason"
META_EVALUATION_ID = "x_amcp_hitl_evaluation_id"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    # Dataclass / namespace-like objects expose `__dict__`. Be defensive.
    if hasattr(value, "__dict__"):
        return vars(value)
    return {}


def is_hitl_response(response: Any) -> bool:
    """Return True iff the evaluation response signals an R043 HITL outcome."""
    arr = _as_mapping(response)
    if arr.get("decision") != "BLOCK":
        return False
    ucp = _as_mapping(arr.get("ucp"))
    if ucp.get("state") != "requires_escalation":
        return False
    code = ucp.get("reason_code", "")
    return isinstance(code, str) and code.startswith(R043_REASON_PREFIX)


def rule_code_from(response: Any) -> str:
    """Extract canonical rule code (e.g. ``R043.agent-checkout-approval-required``)."""
    arr = _as_mapping(response)
    ucp = _as_mapping(arr.get("ucp"))
    code = ucp.get("reason_code", "") or ""
    if isinstance(code, str) and code.startswith("trusteed:"):
        return code[len("trusteed:") :]
    return ""


def build_freeze_payload(response: Any) -> dict[str, Any]:
    """Build the ORM-write payload for parking a sale.order in HITL.

    Returns
    -------
    dict
        Mapping suitable for ``sale_order.write({...})``. Includes the
        custom state, the audit-meta fields, and a ``freeze`` boolean
        the caller can short-circuit on.
    """
    arr = _as_mapping(response)
    freeze = is_hitl_response(response)
    return {
        "freeze": freeze,
        "state": HITL_PENDING_STATE if freeze else None,
        META_HITL_PENDING: freeze,
        META_RULE_CODE: rule_code_from(response),
        META_REASON: str(arr.get("reason") or ""),
        META_EVALUATION_ID: str(arr.get("evaluationId") or ""),
    }


def requires_freeze(payload: Mapping[str, Any]) -> bool:
    """Return True when the freeze payload triggers a state override.

    Pure helper so the model layer stays one-liner thin.
    """
    return bool(payload.get("freeze", False))
