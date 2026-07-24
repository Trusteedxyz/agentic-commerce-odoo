"""Offline safety-valve evaluator — local Python port of the "universal
merchant policy" rule subset from
`packages/shared/src/enforcement/rule-catalog.ts`
(R014-country-only, R018, R019, R020, R025, R027, R028, R029, R030).

Why this exists: `SaleOrderEnforcement._trusteed_cel_call_layer2()` is the
ONLY place a rule verdict is decided in this addon — everything else just
projects signals. If that HTTP call fails (network error, timeout, non-2xx),
the merchant's own safety rules (max order amount, blocked countries,
business hours, PO-box block, gift-card cap...) never get a chance to fire —
`_trusteed_cel_enforce()` falls through to the blunt `fallback_mode` policy
(block everything in strict / allow everything in balanced-permissive)
instead.

This module is a FALLBACK, not a replacement: the remote evaluator stays
primary (it has richer history/DB signal for rules outside this subset).
It only runs when the remote call is unavailable, evaluating the last
fetched signed RuleSnapshot's `rules[]` (already cached in-process by
`enforcement_snapshot.py`'s `_SNAPSHOT_CACHE`) against the current cart —
pure functions, no network, no DB.

CONFORMANCE: every function here MUST match the behavior of its TypeScript
counterpart in rule-catalog.ts EXACTLY, and the WooCommerce/PrestaShop/
Magento plugins' equivalents (same rule subset, mirrored independently).
All are tested against the same fixture:
`packages/shared/src/enforcement/__fixtures__/
offline-safety-valve-conformance.json`. If you change one, update the
fixture and re-run every consumer's test suite before shipping.
"""

from __future__ import annotations

import datetime
from typing import Optional
from zoneinfo import ZoneInfo

DEFAULT_HIGH_RISK_COUNTRIES = ("KP", "IR", "SY", "CU")


def _short_code(rule_code: str) -> str:
    dot = rule_code.find(".")
    return rule_code if dot == -1 else rule_code[:dot]


def _order_country(order_context: dict) -> Optional[str]:
    return order_context.get("billingCountry") or order_context.get("shippingCountry")


def _attr_bool(cart_attributes: dict, key: str) -> bool:
    return cart_attributes.get(key) in ("true", "1")


def _attr_number(cart_attributes: dict, key: str) -> Optional[float]:
    raw = cart_attributes.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# ── R014 (country dimension only — cancellation-history needs a DB lookup) ──


def _eval_r014(params: dict, order_context: dict, cart_attributes: dict) -> Optional[str]:
    high_risk = params.get("highRiskCountries") or list(DEFAULT_HIGH_RISK_COUNTRIES)
    country = _order_country(order_context)
    if country is not None and country in high_risk:
        return f"delivery country {country} is high-risk"
    return None


# ── R018 cart-composition-guard ──


def _eval_r018(params: dict, order_context: dict, cart_attributes: dict) -> Optional[str]:
    avg = params.get("merchantAvgOrderCents")
    mult = params.get("spikeMultiplier", 5.0)
    cart_total_cents = int(order_context.get("cartTotalCents", 0))

    if avg is not None and avg > 0:
        spike = cart_total_cents / avg
        if spike > mult:
            return f"cart {cart_total_cents} is {spike:.1f}x avg {avg}"

    max_item_count = params.get("maxItemCount")
    item_count = int(order_context.get("itemCount", 0))
    if max_item_count is not None and item_count > max_item_count:
        return f"item count {item_count} > {max_item_count}"

    max_qty = params.get("maxSingleSkuQty")
    if max_qty is not None:
        for line in order_context.get("lineItems", []) or []:
            if int(line.get("qty", 0)) > max_qty:
                return f"line item quantity exceeds {max_qty}"

    return None


# ── R019 country-jurisdiction ──


def _eval_r019(params: dict, order_context: dict, cart_attributes: dict) -> Optional[str]:
    country = _order_country(order_context)
    if country is None:
        return None
    blocked = params.get("blockedCountries") or []
    if country in blocked:
        return f"country {country} is blocked"
    allowed = params.get("allowedCountries") or []
    if allowed and country not in allowed:
        return f"country {country} is not allowed"
    return None


# ── R020 business-hours ──


def _eval_r020(params: dict, order_context: dict, cart_attributes: dict) -> Optional[str]:
    start = params.get("startHour")
    end = params.get("endHour")
    if start is None or end is None:
        return None

    hour = _attr_number(cart_attributes, "_merchant_local_hour")
    if hour is None:
        tz_name = params.get("timezone", "UTC")
        try:
            now = datetime.datetime.now(ZoneInfo(tz_name))
            hour = now.hour
        except Exception:
            return None

    inside = (start <= hour < end) if start <= end else (hour >= start or hour < end)
    return None if inside else f"local hour {int(hour)} outside {start}-{end}"


# ── R025 sensitive-delivery-address ──


def _eval_r025(params: dict, order_context: dict, cart_attributes: dict) -> Optional[str]:
    if params.get("blockPoBox", True) and _attr_bool(cart_attributes, "_shipping_po_box"):
        return "sensitive PO box delivery address"
    if params.get("blockFreightForwarder", True) and _attr_bool(
        cart_attributes, "_shipping_freight_forwarder"
    ):
        return "freight-forwarder delivery address"
    return None


# ── R027 gift-card-stored-value ──


def _eval_r027(params: dict, order_context: dict, cart_attributes: dict) -> Optional[str]:
    raw = _attr_number(cart_attributes, "_stored_value_cents")
    if raw is None:
        return None
    max_cents = params.get("maxStoredValueCents", 0)
    return f"stored value {raw} > {max_cents}" if raw > max_cents else None


# ── R028 b2b-po-guard ──


def _eval_r028(params: dict, order_context: dict, cart_attributes: dict) -> Optional[str]:
    if params.get("requirePurchaseOrder") is False:
        return None
    is_b2b = _attr_bool(cart_attributes, "_b2b_order")
    has_po_hash = "_purchase_order_hash" in cart_attributes
    return "B2B purchase order evidence missing" if (is_b2b and not has_po_hash) else None


# ── R029 merchant-preset ──


def _eval_r029(params: dict, order_context: dict, cart_attributes: dict) -> Optional[str]:
    preset = params.get("preset", "equilibrado")
    if preset == "abierto":
        return None
    if preset == "regulado" and not _attr_bool(cart_attributes, "_regulated_evidence_present"):
        return "regulated preset requires additional merchant evidence"
    if preset == "estricto":
        # Offline evaluator has no agent context (organic checkout by
        # definition here) — "estricto" REQUIRES a verified high-trust
        # agent by design, so this correctly HITs for every organic cart.
        return "strict preset requires verified high-trust agent"
    return None


# ── R030 simple-controls ──


def _eval_r030(params: dict, order_context: dict, cart_attributes: dict) -> Optional[str]:
    max_cents = params.get("maxAmountCents")
    cart_total_cents = int(order_context.get("cartTotalCents", 0))
    if max_cents is not None and cart_total_cents > max_cents:
        return f"cart total {cart_total_cents} > merchant max {max_cents}"
    country = _order_country(order_context)
    allowed = params.get("allowedCountries") or []
    if country is not None and allowed and country not in allowed:
        return f"country {country} is outside merchant controls"
    return None


_DISPATCH = {
    "R014": _eval_r014,
    "R018": _eval_r018,
    "R019": _eval_r019,
    "R020": _eval_r020,
    "R025": _eval_r025,
    "R027": _eval_r027,
    "R028": _eval_r028,
    "R029": _eval_r029,
    "R030": _eval_r030,
}


def evaluate(
    rules: list[dict], order_context: dict, cart_attributes: dict
) -> Optional[dict]:
    """Evaluate the offline-capable rule subset against a pulled snapshot's
    rules array + the current cart. Returns the FIRST rule that blocks
    (first-match-wins), or None when everything passes.

    :param rules: snapshot ``rules[]`` — each ``{ruleCode, enabled, params}``.
    :param order_context: ``{cartTotalCents, itemCount, billingCountry?,
        shippingCountry?, lineItems?}``.
    :param cart_attributes: string-keyed map (already-stringified values,
        matching the wire cart-attribute contract).
    :return: ``{"ruleCode": str, "reason": str}`` or ``None``.
    """
    for rule in rules:
        if not isinstance(rule, dict) or not rule.get("enabled"):
            continue
        rule_code = _short_code(str(rule.get("ruleCode", "")))
        fn = _DISPATCH.get(rule_code)
        if fn is None:
            continue
        params = rule.get("params") or {}
        reason = fn(params, order_context, cart_attributes)
        if reason is not None:
            return {"ruleCode": rule_code, "reason": reason}
    return None
