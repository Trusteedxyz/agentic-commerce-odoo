"""Spec-048 Sprint E (T-E41, ADR-054) — Agentic Starter Kit signals for Odoo.

Mirror of WooCommerce `class-cart-signals.php`, Magento and PrestaShop
`CartSignals` Sprint E helpers.

  R032 (blocked-category) — sale.order.line.product_id.categ_id lineage.
  R034 (blocked-sku-list) — product.product.default_code (SKU canonical).
  R038 (max-items-per-order) — Σ product_uom_qty.
  R039 (max-quantity-per-sku) — per-line product_uom_qty max.
  R048 (no-digital-goods-for-agents) — product.product.type == "service" + giftcard.

R031 (kill-switch) + R042 (history) + R043 (HITL) live outside this module.

Source mapping for Odoo:
  - R032: product.product.categ_id.complete_name lineage (e.g. "All / Saleable / Misc")
  - R034: product.product.default_code (canonical SKU); barcode is fallback only
  - R048: product.product.type == "service" → "downloadable"
          giftcard via product_tmpl_id.name / default_code substring needle

Items are wrapped by the dispatcher into primitive dicts so these are
pure functions testable without an Odoo registry.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

# PrestaShop-style giftcard reference needles (lower-case substring match).
GIFT_CARD_NEEDLES: tuple[str, ...] = (
    "giftcard",
    "gift-card",
    "gift_card",
    "vale-regalo",
    "tarjeta-regalo",
)


def r032_has_blocked_category(
    items: Iterable[Mapping[str, object]],
    blocked: Sequence[object],
) -> bool:
    """Return True iff any cart item has a category id in *blocked*.

    Item shape: ``{"categoryIds": list[int|str]}`` — pre-projected by dispatcher
    so this function doesn't touch the Odoo registry. ``categoryIds`` should
    contain the complete lineage (categ_id + parent ids) so nested category
    blocks behave as merchants expect.
    """
    if not blocked:
        return False
    blocked_set = {str(b) for b in blocked if str(b) != ""}
    if not blocked_set:
        return False
    for item in items:
        cats = item.get("categoryIds") or ()
        if not isinstance(cats, (list, tuple)):
            continue
        for cat in cats:
            if str(cat) in blocked_set:
                return True
    return False


def r034_has_blocked_sku(
    items: Iterable[Mapping[str, object]],
    blocked: Sequence[str],
) -> bool:
    """Return True iff any cart item SKU (default_code) is in *blocked*."""
    if not blocked:
        return False
    blocked_set = {b for b in blocked if isinstance(b, str) and b != ""}
    if not blocked_set:
        return False
    for item in items:
        sku = item.get("sku") or ""
        if isinstance(sku, str) and sku in blocked_set:
            return True
    return False


def r038_item_count(items: Iterable[Mapping[str, object]]) -> int:
    """Return Σ quantity across cart items (negative-qty guard).

    Item shape: ``{"quantity": int|float}``. Floats are floor-truncated to int
    after the positivity guard so partial-uom lines (Odoo allows 1.5x kg)
    don't double-count.
    """
    total = 0
    for item in items:
        qty_raw = item.get("quantity", 0)
        try:
            qty = int(qty_raw)
        except (TypeError, ValueError):
            continue
        if qty > 0:
            total += qty
    return total


def r039_max_line_quantity(items: Iterable[Mapping[str, object]]) -> int:
    """Return max per-line quantity (0 if cart empty)."""
    best = 0
    for item in items:
        qty_raw = item.get("quantity", 0)
        try:
            qty = int(qty_raw)
        except (TypeError, ValueError):
            continue
        if qty > best:
            best = qty
    return best


def r048_digital_good_types(items: Iterable[Mapping[str, object]]) -> str:
    """Return comma-separated digital good types present in cart.

    Source mapping for Odoo:
      - ``type == "service"`` → "downloadable"
        (Odoo conflates digital products + services under "service"; agents
         buying services follow the same restriction model as downloads.)
      - name / default_code contains giftcard needle → "gift_card"

    Item shape: ``{"type": str, "name": str, "sku": str}``.
    """
    types: dict[str, bool] = {}
    for item in items:
        name = str(item.get("name") or "").lower()
        sku = str(item.get("sku") or "").lower()
        is_giftcard = any(
            (sku and needle in sku) or (name and needle in name)
            for needle in GIFT_CARD_NEEDLES
        )
        if is_giftcard:
            types["gift_card"] = True
            continue
        ptype = str(item.get("type") or "").lower()
        if ptype == "service":
            types["downloadable"] = True
    return ",".join(types.keys())


def evaluate_r035(order, params: dict) -> dict:
    """Spec-048 Sprint E.3 — R035 max-order-value (Odoo sale.order).

    Mirrors `evaluateR035` in `packages/shared/src/enforcement/rule-catalog.ts`:
    strict-`>` boundary on `cart_total_cents`. Currency derived from store
    config (NOT param).

    Param shape: ``{"maxCents": int}``. Missing → no-op (no hit, no reason).
    Stub-graceful: if `order` has no `amount_total` (Odoo registry missing
    field on a partially-projected record) returns no hit silently.
    """
    if not hasattr(order, "amount_total"):
        return {"hit": False}
    cap = params.get("maxCents") if isinstance(params, dict) else None
    if cap is None:
        return {"hit": False}
    total = int(round(float(order.amount_total) * 100))
    if total > cap:
        return {"hit": True, "reason": f"cart total {total} exceeds cap {cap}"}
    return {"hit": False}


def evaluate_r036(order, params: dict) -> dict:
    """Spec-048 Sprint E.3 — R036 max-line-item-value (Odoo sale.order.line).

    Mirrors `evaluateR036`: iterates lines, first line whose subtotal exceeds
    the cap wins (`>` strict). Uses `line.price_subtotal` (qty * unit_price
    sans tax) — matches WC/PS semantics. Tax-inclusive variants are handled
    upstream by projecting `price_subtotal_incl` into `price_subtotal` for
    stores configured tax-inclusive.

    Param shape: ``{"maxCents": int}``. Missing → no-op.
    Stub-graceful: if `order` has no `order_line` returns no hit silently.
    """
    if not hasattr(order, "order_line"):
        return {"hit": False}
    cap = params.get("maxCents") if isinstance(params, dict) else None
    if cap is None:
        return {"hit": False}
    for line in order.order_line:
        try:
            line_cents = int(round(float(line.price_subtotal) * 100))
        except (TypeError, ValueError, AttributeError):
            continue
        if line_cents > cap:
            product_id = getattr(getattr(line, "product_id", None), "id", "?")
            return {
                "hit": True,
                "reason": f"line {product_id} value {line_cents} exceeds cap {cap}",
            }
    return {"hit": False}


__all__ = (
    "GIFT_CARD_NEEDLES",
    "evaluate_r035",
    "evaluate_r036",
    "r032_has_blocked_category",
    "r034_has_blocked_sku",
    "r038_item_count",
    "r039_max_line_quantity",
    "r048_digital_good_types",
)
