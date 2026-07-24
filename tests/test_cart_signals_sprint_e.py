"""Spec-048 Sprint E (T-E41) — Odoo cart_signals pure-helper tests.

These tests exercise the Sprint E starter-kit rule helpers without an Odoo
registry — they operate on primitive dicts pre-projected by the dispatcher.
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


_cs = _load_module("amcp_odoo_cart_signals", "utils/cart_signals.py")
_ahf = _load_module("amcp_odoo_agent_history_fetcher", "utils/agent_history_fetcher.py")

evaluate_r035 = _cs.evaluate_r035
evaluate_r036 = _cs.evaluate_r036
r032_has_blocked_category = _cs.r032_has_blocked_category
r034_has_blocked_sku = _cs.r034_has_blocked_sku
r038_item_count = _cs.r038_item_count
r039_max_line_quantity = _cs.r039_max_line_quantity
r048_digital_good_types = _cs.r048_digital_good_types
AgentHistoryFetcher = _ahf.AgentHistoryFetcher


class TestCartSignalsR032(unittest.TestCase):
    def test_hit(self):
        items = [{"categoryIds": [10, 20]}, {"categoryIds": [30]}]
        self.assertTrue(r032_has_blocked_category(items, [20]))
        self.assertTrue(r032_has_blocked_category(items, ["20"]))

    def test_miss(self):
        items = [{"categoryIds": [10]}]
        self.assertFalse(r032_has_blocked_category(items, [99]))
        self.assertFalse(r032_has_blocked_category(items, []))
        self.assertFalse(r032_has_blocked_category([], [10]))


class TestCartSignalsR034(unittest.TestCase):
    def test_hit_and_miss(self):
        items = [{"sku": "REF-A"}, {"sku": "REF-B"}]
        self.assertTrue(r034_has_blocked_sku(items, ["REF-B"]))
        self.assertFalse(r034_has_blocked_sku(items, ["REF-Z"]))
        self.assertFalse(r034_has_blocked_sku(items, []))


class TestCartSignalsR038(unittest.TestCase):
    def test_sum_positive(self):
        self.assertEqual(0, r038_item_count([]))
        self.assertEqual(5, r038_item_count([{"quantity": 2}, {"quantity": 3}]))

    def test_negative_qty_guard(self):
        self.assertEqual(2, r038_item_count([{"quantity": 2}, {"quantity": -10}]))

    def test_non_numeric_qty(self):
        self.assertEqual(2, r038_item_count([{"quantity": 2}, {"quantity": "junk"}]))


class TestCartSignalsR039(unittest.TestCase):
    def test_max(self):
        self.assertEqual(0, r039_max_line_quantity([]))
        self.assertEqual(7, r039_max_line_quantity(
            [{"quantity": 3}, {"quantity": 7}, {"quantity": 1}]
        ))


class TestCartSignalsR048(unittest.TestCase):
    def test_service_maps_to_downloadable(self):
        items = [{"type": "service", "name": "Hosting", "sku": "HOST"}]
        self.assertEqual("downloadable", r048_digital_good_types(items))

    def test_giftcard_via_sku(self):
        items = [{"type": "service", "name": "VR", "sku": "giftcard-50"}]
        self.assertEqual("gift_card", r048_digital_good_types(items))

    def test_giftcard_via_name(self):
        items = [{"type": "service", "name": "Tarjeta-Regalo", "sku": ""}]
        self.assertEqual("gift_card", r048_digital_good_types(items))

    def test_physical_is_empty(self):
        items = [{"type": "consu", "name": "Mug", "sku": "MUG-1"}]
        self.assertEqual("", r048_digital_good_types(items))

    def test_mixed_giftcard_and_downloadable(self):
        items = [
            {"type": "service", "name": "Hosting", "sku": "HOST"},
            {"type": "service", "name": "Giftcard 100", "sku": "GC-100"},
        ]
        out = r048_digital_good_types(items)
        self.assertIn("downloadable", out)
        self.assertIn("gift_card", out)


class TestEvaluateR035(unittest.TestCase):
    """Sprint E.3 (T-E40+) — Odoo R035 max-order-value evaluator."""

    def _order(self, amount_total):
        o = MagicMock(spec=["amount_total"])
        o.amount_total = amount_total
        return o

    def test_hit_when_total_exceeds_cap(self):
        # 150.50 EUR → 15050 cents, cap 10000 → HIT
        out = evaluate_r035(self._order(150.50), {"maxCents": 10000})
        self.assertTrue(out["hit"])
        self.assertIn("15050", out["reason"])
        self.assertIn("10000", out["reason"])

    def test_pass_when_total_under_or_equal_cap(self):
        # boundary inclusive: total == cap → PASS (strict >)
        out = evaluate_r035(self._order(100.00), {"maxCents": 10000})
        self.assertFalse(out["hit"])
        out2 = evaluate_r035(self._order(50.00), {"maxCents": 10000})
        self.assertFalse(out2["hit"])

    def test_missing_param_returns_no_hit(self):
        self.assertEqual({"hit": False}, evaluate_r035(self._order(999.99), {}))
        self.assertEqual({"hit": False}, evaluate_r035(self._order(999.99), {"maxCents": None}))

    def test_stub_graceful_without_amount_total(self):
        # order shaped without amount_total (Odoo registry missing field)
        bare = object()
        self.assertEqual({"hit": False}, evaluate_r035(bare, {"maxCents": 1000}))


class TestEvaluateR036(unittest.TestCase):
    """Sprint E.3 (T-E40+) — Odoo R036 max-line-item-value evaluator."""

    def _order_with_lines(self, lines):
        o = MagicMock(spec=["order_line"])
        o.order_line = lines
        return o

    def _line(self, product_id, price_subtotal):
        line = MagicMock(spec=["product_id", "price_subtotal"])
        line.product_id = MagicMock()
        line.product_id.id = product_id
        line.price_subtotal = price_subtotal
        return line

    def test_hit_first_line_over_cap(self):
        lines = [
            self._line(42, 50.00),   # 5000 cents, PASS
            self._line(99, 200.00),  # 20000 cents, HIT
            self._line(7, 500.00),   # would also hit, but first-over wins
        ]
        out = evaluate_r036(self._order_with_lines(lines), {"maxCents": 10000})
        self.assertTrue(out["hit"])
        self.assertIn("99", out["reason"])
        self.assertIn("20000", out["reason"])
        self.assertIn("10000", out["reason"])

    def test_pass_when_all_lines_under_cap(self):
        lines = [self._line(1, 10.00), self._line(2, 25.50)]
        out = evaluate_r036(self._order_with_lines(lines), {"maxCents": 10000})
        self.assertFalse(out["hit"])

    def test_missing_param_returns_no_hit(self):
        lines = [self._line(1, 9999.99)]
        self.assertEqual({"hit": False}, evaluate_r036(self._order_with_lines(lines), {}))
        self.assertEqual(
            {"hit": False},
            evaluate_r036(self._order_with_lines(lines), {"maxCents": None}),
        )

    def test_stub_graceful_without_order_line(self):
        bare = object()
        self.assertEqual({"hit": False}, evaluate_r036(bare, {"maxCents": 1000}))

    def test_empty_order_lines_returns_no_hit(self):
        out = evaluate_r036(self._order_with_lines([]), {"maxCents": 10000})
        self.assertFalse(out["hit"])


class TestAgentHistoryFetcherStub(unittest.TestCase):
    def test_returns_none_for_empty_hash(self):
        f = AgentHistoryFetcher(env=None, logger=lambda _msg: None)
        self.assertIsNone(f.completed_order_count_in_window("", 3600))

    def test_returns_none_for_non_positive_window(self):
        f = AgentHistoryFetcher(env=None, logger=lambda _msg: None)
        self.assertIsNone(f.completed_order_count_in_window("hash", 0))
        self.assertIsNone(f.completed_order_count_in_window("hash", -1))

    def test_returns_none_and_warns_once(self):
        logged = []
        f = AgentHistoryFetcher(env=None, logger=lambda m: logged.append(m))
        f.reset_warning()
        self.assertIsNone(f.completed_order_count_in_window("hash", 3600))
        self.assertIsNone(f.completed_order_count_in_window("hash", 3600))
        # one-shot warning
        self.assertEqual(1, len(logged))
        self.assertIn("R042", logged[0])


if __name__ == "__main__":
    unittest.main()
