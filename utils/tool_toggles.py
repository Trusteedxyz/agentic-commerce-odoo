"""Per-tool enable/disable switches — spec-046 FR-018b (Odoo).

FR-018b requires ONE toggle per agentic tool persisted in native platform
storage so a merchant can opt in/out of each tool independently. Mirrors the
WordPress ``ToolToggles`` and PrestaShop equivalents, keeping cross-platform
parity.

Native storage on Odoo is ``ir.config_parameter`` (key/value, system-scoped).
Defaults (FR-018b):

  - ``sign_trust_receipt``     → ON  (non-mutating trust tool)
  - ``verify_agent_signature`` → ON  (non-mutating identity tool)
  - ``dispatch_payment_acp``   → OFF (opt-in — agent-initiated payment)
  - ``dispatch_payment_x402``  → OFF (opt-in — agent-initiated payment)
  - ``dispatch_payment_ap2``   → OFF (opt-in — agent-initiated payment)

Installing the addon therefore NEVER silently enables agent payments.

HONESTY (catalog ``backendStatus``): ``sign-trust-receipt`` and
``dispatch-payment-ap2`` are ``backendStatus: "planned"`` in the canonical
catalog (no deployed backend). They are reported UNAVAILABLE regardless of the
toggle — the toggle merely preserves the merchant's preference for when those
backends ship.

Pure helpers (no Odoo ORM import) so they're unit-testable; the caller passes a
``get_param``/``set_param``-capable ``ir.config_parameter`` accessor.
"""

from __future__ import annotations

from typing import Callable, Mapping

# catalog tool id → (ir.config_parameter key, default-enabled)
TOGGLES: Mapping[str, tuple[str, bool]] = {
    "trusteed/sign-trust-receipt": ("trusteed.tool.sign_trust_receipt", True),
    "trusteed/verify-agent-signature": (
        "trusteed.tool.verify_agent_signature",
        True,
    ),
    "trusteed/dispatch-payment-acp": (
        "trusteed.tool.dispatch_payment_acp",
        False,
    ),
    "trusteed/dispatch-payment-x402": (
        "trusteed.tool.dispatch_payment_x402",
        False,
    ),
    "trusteed/dispatch-payment-ap2": (
        "trusteed.tool.dispatch_payment_ap2",
        False,
    ),
}

# Catalog ids whose backend is `planned` (not deployed) — always unavailable.
PLANNED_TOOL_IDS: frozenset[str] = frozenset(
    {
        "trusteed/sign-trust-receipt",
        "trusteed/dispatch-payment-ap2",
    }
)

_TRUTHY = {"1", "true", "yes", "on"}


def _coerce_bool(value: object, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY


def is_available(tool_id: str) -> bool:
    """Return False for unknown ids or `planned` (no-backend) tools.

    A `planned` tool is never invocable regardless of its toggle (honest-gate).
    """
    if tool_id not in TOGGLES:
        return False
    return tool_id not in PLANNED_TOOL_IDS


def is_enabled(tool_id: str, get_param: Callable[[str, object], object]) -> bool:
    """Return True iff the tool is available AND toggled on.

    Unknown ids fail-closed. `planned` tools fail-closed regardless of toggle.

    Parameters
    ----------
    tool_id:
        Canonical catalog id (e.g. ``trusteed/dispatch-payment-acp``).
    get_param:
        ``ir.config_parameter.get_param``-compatible callable
        ``(key, default) -> value``.
    """
    if not is_available(tool_id):
        return False
    key, default = TOGGLES[tool_id]
    return _coerce_bool(get_param(key, default), default)


def seed_defaults(
    get_param: Callable[[str, object], object],
    set_param: Callable[[str, str], object],
) -> None:
    """Seed the 5 toggle params with their defaults, idempotently.

    Only writes a key that is currently absent so a merchant's saved preference
    is never clobbered on upgrade.
    """
    sentinel = object()
    for key, default in TOGGLES.values():
        if get_param(key, sentinel) is sentinel:
            set_param(key, "1" if default else "0")
