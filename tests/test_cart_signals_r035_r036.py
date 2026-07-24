"""Spec-048 Sprint E.3 — R035 + R036 evaluator unit tests (Odoo).

Mirrors `evaluateR035`/`evaluateR036` in
`packages/shared/src/enforcement/rule-catalog.ts`.

Strict-`>` boundary semantics; stub-graceful for missing Odoo registry fields.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock


def _load_module(name: str, relpath: str):
    mod_path = Path(__file__).resolve().parent.parent / relpath
    spec = importlib.util.spec_from_file_location(name, str(mod_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_cs = _load_module("amcp_odoo_cart_signals_r035_r036", "utils/cart_signals.py")
evaluate_r035 = _cs.evaluate_r035
evaluate_r036 = _cs.evaluate_r036


class EvaluateR035TestCase(unittest.TestCase):
    """R035 max-order-value — `cart_total_cents > cap` strict boundary."""

    def test_hit_when_total_exceeds_cap(self) -> None:
        order = MagicMock(spec=["amount_total"])
        order.amount_total = 150.00  # 15000 cents
        result = evaluate_r035(order, {"maxCents": 10000})
        self.assertTrue(result["hit"])
        self.assertIn("15000", result["reason"])
        self.assertIn("10000", result["reason"])

    def test_no_hit_when_total_equal_to_cap(self) -> None:
        # Strict `>` — equal must NOT hit.
        order = MagicMock(spec=["amount_total"])
        order.amount_total = 100.00  # 10000 cents
        result = evaluate_r035(order, {"maxCents": 10000})
        self.assertFalse(result["hit"])

    def test_no_hit_when_total_below_cap(self) -> None:
        order = MagicMock(spec=["amount_total"])
        order.amount_total = 50.00
        result = evaluate_r035(order, {"maxCents": 10000})
        self.assertFalse(result["hit"])

    def test_missing_param_no_op(self) -> None:
        order = MagicMock(spec=["amount_total"])
        order.amount_total = 999999.99
        self.assertFalse(evaluate_r035(order, {})["hit"])

    def test_none_cap_no_op(self) -> None:
        order = MagicMock(spec=["amount_total"])
        order.amount_total = 999999.99
        self.assertFalse(evaluate_r035(order, {"maxCents": None})["hit"])

    def test_stub_graceful_missing_amount_total(self) -> None:
        # Partial projection — no `amount_total` attr at all.
        order = MagicMock(spec=[])
        self.assertFalse(evaluate_r035(order, {"maxCents": 100})["hit"])

    def test_rounding_half_up(self) -> None:
        # 100.005 → 10000 or 10001 (Python banker's rounding); ensure no crash
        # and strict boundary still respected.
        order = MagicMock(spec=["amount_total"])
        order.amount_total = 100.01  # 10001 cents
        result = evaluate_r035(order, {"maxCents": 10000})
        self.assertTrue(result["hit"])


class EvaluateR036TestCase(unittest.TestCase):
    """R036 max-line-item-value — first line exceeding cap wins."""

    def _line(self, subtotal: float, product_id: int = 42):
        line = MagicMock(spec=["price_subtotal", "product_id"])
        line.price_subtotal = subtotal
        line.product_id = MagicMock(spec=["id"])
        line.product_id.id = product_id
        return line

    def test_hit_when_line_exceeds_cap(self) -> None:
        order = MagicMock(spec=["order_line"])
        order.order_line = [self._line(200.00, product_id=7)]  # 20000 cents
        result = evaluate_r036(order, {"maxCents": 15000})
        self.assertTrue(result["hit"])
        self.assertIn("7", result["reason"])
        self.assertIn("20000", result["reason"])
        self.assertIn("15000", result["reason"])

    def test_no_hit_when_all_lines_below_cap(self) -> None:
        order = MagicMock(spec=["order_line"])
        order.order_line = [self._line(50.00), self._line(75.00)]
        self.assertFalse(evaluate_r036(order, {"maxCents": 10000})["hit"])

    def test_first_offending_line_wins(self) -> None:
        order = MagicMock(spec=["order_line"])
        order.order_line = [
            self._line(50.00, product_id=1),  # 5000 — pass
            self._line(200.00, product_id=2),  # 20000 — hit
            self._line(500.00, product_id=3),  # 50000 — would also hit
        ]
        result = evaluate_r036(order, {"maxCents": 10000})
        self.assertTrue(result["hit"])
        # Reason cites the FIRST offending line (product_id=2).
        self.assertIn("line 2 ", result["reason"])

    def test_missing_param_no_op(self) -> None:
        order = MagicMock(spec=["order_line"])
        order.order_line = [self._line(999999.99)]
        self.assertFalse(evaluate_r036(order, {})["hit"])

    def test_stub_graceful_missing_order_line(self) -> None:
        order = MagicMock(spec=[])
        self.assertFalse(evaluate_r036(order, {"maxCents": 100})["hit"])

    def test_skips_malformed_line_continues_scanning(self) -> None:
        bad = MagicMock(spec=["price_subtotal"])
        bad.price_subtotal = "not-a-number"
        good = self._line(200.00, product_id=99)
        order = MagicMock(spec=["order_line"])
        order.order_line = [bad, good]
        result = evaluate_r036(order, {"maxCents": 10000})
        self.assertTrue(result["hit"])
        self.assertIn("99", result["reason"])

    def test_equal_boundary_no_hit(self) -> None:
        order = MagicMock(spec=["order_line"])
        order.order_line = [self._line(100.00)]  # 10000 cents
        self.assertFalse(evaluate_r036(order, {"maxCents": 10000})["hit"])


if __name__ == "__main__":
    unittest.main()
