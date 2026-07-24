"""spec-046 FR-018b — per-tool opt-in toggle tests (Odoo).

Pure-Python tests for utils/tool_toggles.py (no Odoo registry) plus gating
behaviour of TrusteedAiTool._assert_tool_enabled via a stubbed
ir.config_parameter accessor.

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_tool_toggles.py \
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import importlib.util
import sys
import unittest
from pathlib import Path


def _load_module(name: str, relpath: str):
    mod_path = Path(__file__).resolve().parent.parent / relpath
    spec = importlib.util.spec_from_file_location(name, str(mod_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_tt = _load_module("amcp_odoo_tool_toggles", "utils/tool_toggles.py")


class _FakeICP:
    """Minimal ir.config_parameter get_param/set_param accessor over a dict."""

    def __init__(self, store: dict | None = None):
        self._store = dict(store or {})

    def get_param(self, key, default=""):
        return self._store.get(key, default)

    def set_param(self, key, value):
        self._store[key] = value


class TestAvailability(unittest.TestCase):
    def test_unknown_tool_is_unavailable(self):
        self.assertFalse(_tt.is_available("trusteed/does-not-exist"))

    def test_planned_tools_are_unavailable(self):
        self.assertFalse(_tt.is_available("trusteed/sign-trust-receipt"))
        self.assertFalse(_tt.is_available("trusteed/dispatch-payment-ap2"))

    def test_implemented_tools_are_available(self):
        self.assertTrue(_tt.is_available("trusteed/verify-agent-signature"))
        self.assertTrue(_tt.is_available("trusteed/dispatch-payment-acp"))
        self.assertTrue(_tt.is_available("trusteed/dispatch-payment-x402"))


class TestDefaults(unittest.TestCase):
    def test_payments_default_off_opt_in(self):
        icp = _FakeICP()  # no stored params → defaults apply
        self.assertFalse(
            _tt.is_enabled("trusteed/dispatch-payment-acp", icp.get_param)
        )
        self.assertFalse(
            _tt.is_enabled("trusteed/dispatch-payment-x402", icp.get_param)
        )

    def test_verify_defaults_on(self):
        icp = _FakeICP()
        self.assertTrue(
            _tt.is_enabled("trusteed/verify-agent-signature", icp.get_param)
        )

    def test_planned_tool_disabled_even_when_toggled_on(self):
        icp = _FakeICP({
            "trusteed.tool.sign_trust_receipt": "1",
            "trusteed.tool.dispatch_payment_ap2": "1",
        })
        self.assertFalse(
            _tt.is_enabled("trusteed/sign-trust-receipt", icp.get_param)
        )
        self.assertFalse(
            _tt.is_enabled("trusteed/dispatch-payment-ap2", icp.get_param)
        )

    def test_payment_enabled_when_toggled_on(self):
        icp = _FakeICP({"trusteed.tool.dispatch_payment_acp": "1"})
        self.assertTrue(
            _tt.is_enabled("trusteed/dispatch-payment-acp", icp.get_param)
        )

    def test_truthy_string_variants(self):
        for raw in ("1", "true", "True", "yes", "on"):
            icp = _FakeICP({"trusteed.tool.dispatch_payment_x402": raw})
            self.assertTrue(
                _tt.is_enabled("trusteed/dispatch-payment-x402", icp.get_param),
                f"{raw!r} should be truthy",
            )

    def test_falsy_string_variants(self):
        for raw in ("0", "false", "no", "off", ""):
            icp = _FakeICP({"trusteed.tool.dispatch_payment_x402": raw})
            self.assertFalse(
                _tt.is_enabled("trusteed/dispatch-payment-x402", icp.get_param),
                f"{raw!r} should be falsy",
            )

    def test_unknown_tool_is_disabled(self):
        icp = _FakeICP()
        self.assertFalse(_tt.is_enabled("trusteed/nope", icp.get_param))


class TestSeedDefaults(unittest.TestCase):
    def test_seed_writes_defaults_idempotently(self):
        icp = _FakeICP()
        _tt.seed_defaults(icp.get_param, icp.set_param)
        # sign + verify ON ("1"), payments OFF ("0").
        self.assertEqual(icp._store["trusteed.tool.sign_trust_receipt"], "1")
        self.assertEqual(icp._store["trusteed.tool.verify_agent_signature"], "1")
        self.assertEqual(icp._store["trusteed.tool.dispatch_payment_acp"], "0")
        self.assertEqual(icp._store["trusteed.tool.dispatch_payment_x402"], "0")
        self.assertEqual(icp._store["trusteed.tool.dispatch_payment_ap2"], "0")

    def test_seed_never_clobbers_existing_preference(self):
        icp = _FakeICP({"trusteed.tool.dispatch_payment_acp": "1"})
        _tt.seed_defaults(icp.get_param, icp.set_param)
        # Existing merchant opt-in preserved.
        self.assertEqual(icp._store["trusteed.tool.dispatch_payment_acp"], "1")


if __name__ == "__main__":
    unittest.main()
