"""US3 — Trust Receipt chatter posting (Odoo).

Exercises the REAL AccountMoveJws._post_receipt_to_chatter and
_resolve_sale_orders_for_move (no Odoo runtime — odoo.* is stubbed and the
model loaded via importlib). Confirms the JWS receipt is surfaced in the
invoice chatter and propagated to the linked sale.order(s).

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_account_move_chatter.py \
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock


def _ensure_odoo_stubs() -> None:
    if not (
        "odoo.models" in sys.modules
        and hasattr(sys.modules["odoo.models"], "Model")
    ):
        odoo_mod = types.ModuleType("odoo")
        odoo_api = types.ModuleType("odoo.api")
        odoo_fields = types.ModuleType("odoo.fields")
        odoo_models = types.ModuleType("odoo.models")

        odoo_api.model = lambda fn: fn
        odoo_api.depends = lambda *a: (lambda fn: fn)

        class _AbstractModel:
            _name = ""
            _description = ""

        odoo_models.AbstractModel = _AbstractModel
        odoo_models.Model = _AbstractModel
        odoo_mod.api = odoo_api
        odoo_mod.fields = odoo_fields
        odoo_mod.models = odoo_models

        for key, mod in [
            ("odoo", odoo_mod),
            ("odoo.api", odoo_api),
            ("odoo.fields", odoo_fields),
            ("odoo.models", odoo_models),
        ]:
            sys.modules.setdefault(key, mod)

    # account_move_jws imports the addon ssrf module by absolute path.
    for key in (
        "odoo.addons",
        "odoo.addons.trusteed",
        "odoo.addons.trusteed.utils",
    ):
        sys.modules.setdefault(key, types.ModuleType(key))
    if "odoo.addons.trusteed.utils.ssrf" not in sys.modules:
        ssrf_path = (
            Path(__file__).resolve().parent.parent / "utils" / "ssrf.py"
        )
        spec = importlib.util.spec_from_file_location(
            "odoo.addons.trusteed.utils.ssrf", str(ssrf_path)
        )
        ssrf_mod = importlib.util.module_from_spec(spec)
        sys.modules["odoo.addons.trusteed.utils.ssrf"] = ssrf_mod
        spec.loader.exec_module(ssrf_mod)
        sys.modules["odoo.addons.trusteed.utils"].ssrf = ssrf_mod


_ensure_odoo_stubs()


def _load_module() -> types.ModuleType:
    key = "amcp_account_move_jws"
    if key in sys.modules:
        return sys.modules[key]
    path = Path(__file__).resolve().parent.parent / "models" / "account_move_jws.py"
    spec = importlib.util.spec_from_file_location(key, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
AccountMoveJws = _mod.AccountMoveJws


class TestPostReceiptToChatter(unittest.TestCase):
    def _make_self(self):
        inst = AccountMoveJws.__new__(AccountMoveJws)
        return inst

    def test_posts_to_invoice_chatter_with_attachment(self):
        inst = self._make_self()
        move = MagicMock()
        attachment = MagicMock()
        attachment.id = 77
        # No linked sale orders.
        move.invoice_line_ids = None

        inst._post_receipt_to_chatter(move, attachment)

        move.message_post.assert_called_once()
        kwargs = move.message_post.call_args.kwargs
        self.assertIn("Trusteed Trust Receipt", kwargs["body"])
        self.assertEqual(kwargs["attachment_ids"], [77])

    def test_propagates_to_linked_sale_orders(self):
        inst = self._make_self()
        attachment = MagicMock()
        attachment.id = 88

        order = MagicMock()
        sale_lines = MagicMock()
        sale_lines.order_id = [order]
        inv_lines = MagicMock()
        inv_lines.sale_line_ids = sale_lines

        move = MagicMock()
        move.invoice_line_ids = inv_lines

        inst._post_receipt_to_chatter(move, attachment)

        order.message_post.assert_called_once()
        self.assertEqual(
            order.message_post.call_args.kwargs["attachment_ids"], [88]
        )

    def test_chatter_failure_is_swallowed(self):
        inst = self._make_self()
        move = MagicMock()
        move.message_post.side_effect = RuntimeError("mail thread missing")
        move.invoice_line_ids = None
        attachment = MagicMock()
        attachment.id = 1
        # Must not raise.
        inst._post_receipt_to_chatter(move, attachment)


class TestResolveSaleOrders(unittest.TestCase):
    def test_no_invoice_lines_returns_empty(self):
        move = MagicMock()
        move.invoice_line_ids = None
        self.assertEqual(AccountMoveJws._resolve_sale_orders_for_move(move), [])

    def test_no_sale_bridge_returns_empty(self):
        move = MagicMock()
        inv_lines = MagicMock()
        inv_lines.sale_line_ids = None
        move.invoice_line_ids = inv_lines
        self.assertEqual(AccountMoveJws._resolve_sale_orders_for_move(move), [])

    def test_resolves_orders(self):
        order = object()
        sale_lines = MagicMock()
        sale_lines.order_id = [order]
        inv_lines = MagicMock()
        inv_lines.sale_line_ids = sale_lines
        move = MagicMock()
        move.invoice_line_ids = inv_lines
        self.assertEqual(
            AccountMoveJws._resolve_sale_orders_for_move(move), [order]
        )


if __name__ == "__main__":
    unittest.main()
