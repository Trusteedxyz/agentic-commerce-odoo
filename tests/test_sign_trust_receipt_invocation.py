"""T081 — run_sign_trust_receipt invocation tests.

Verifies API path, payload construction, response passthrough, and error
wrapping — all without a real Odoo database or HTTP requests.

Run:
    python -m pytest packages/odoo-addon-trusteed/tests/ \
        --rootdir=packages/odoo-addon-trusteed/tests -v
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Ensure Odoo stubs are present (safe to call multiple times via setdefault)
# ---------------------------------------------------------------------------

def _ensure_odoo_stubs():
    class _UserError(Exception):
        pass

    class AbstractModel:
        _name = ""
        _description = ""

    for name, attrs in [
        ("odoo", {"api": None, "models": None, "exceptions": None}),
        ("odoo.api", {"model": lambda fn: fn, "constrains": lambda *a, **kw: (lambda fn: fn)}),
        ("odoo.models", {"AbstractModel": AbstractModel}),
        ("odoo.exceptions", {"UserError": _UserError, "AccessError": Exception}),
    ]:
        if name not in sys.modules:
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                if v is not None:
                    setattr(mod, k, v)
            sys.modules[name] = mod

    return sys.modules["odoo.exceptions"].UserError


_UserError = _ensure_odoo_stubs()

if "odoo.addons.trusteed.utils.ssrf" not in sys.modules:
    _ssrf = types.ModuleType("odoo.addons.trusteed.utils.ssrf")
    _ssrf.validate_api_base = lambda url: url.startswith("https://")
    for mod_name, mod in [
        ("odoo.addons", types.ModuleType("odoo.addons")),
        ("odoo.addons.trusteed", types.ModuleType("odoo.addons.trusteed")),
        ("odoo.addons.trusteed.utils", types.ModuleType("odoo.addons.trusteed.utils")),
        ("odoo.addons.trusteed.utils.ssrf", _ssrf),
    ]:
        sys.modules.setdefault(mod_name, mod)


def _load_modules():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).parent.parent
    # Hierarchical package so ai_tool_invocation's relative imports resolve
    # (`from .api_client`, `from ..utils.tool_toggles`).
    root_pkg = "addon_t081"
    models_pkg = f"{root_pkg}.models"
    utils_pkg = f"{root_pkg}.utils"
    for pkg in (root_pkg, models_pkg, utils_pkg):
        sys.modules.setdefault(pkg, types.ModuleType(pkg))

    def _load(key, parent_pkg, subdir, filename):
        if key in sys.modules:
            return sys.modules[key]
        spec = importlib.util.spec_from_file_location(
            key, root / subdir / filename
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = parent_pkg
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
        return mod

    api_client = _load(
        f"{models_pkg}.api_client", models_pkg, "models", "api_client.py"
    )
    _load(f"{utils_pkg}.tool_toggles", utils_pkg, "utils", "tool_toggles.py")
    ai_mod = _load(
        f"{models_pkg}.ai_tool_invocation",
        models_pkg,
        "models",
        "ai_tool_invocation.py",
    )
    return ai_mod, api_client


_ai_tool_mod, _api_client_mod = _load_modules()
TrusteedAiTool = _ai_tool_mod.TrusteedAiTool
TrusteedApiError = _api_client_mod.TrusteedApiError


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

class _FakeICP:
    """ir.config_parameter accessor with every FR-018b toggle ON."""

    def sudo(self):
        return self

    def get_param(self, key, default=""):
        if str(key).startswith("trusteed.tool."):
            return "1"
        return default


class _FakeEnv:
    def __getitem__(self, _key):
        return _FakeICP()


def _make_instance(api_client_mock):
    """Build a TrusteedAiTool instance whose _get_api_client returns the mock."""
    instance = TrusteedAiTool.__new__(TrusteedAiTool)
    instance._get_api_client = MagicMock(return_value=api_client_mock)
    instance.env = _FakeEnv()
    return instance


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
# sign-trust-receipt is `backendStatus: "planned"` in the canonical catalog
# (no standalone sign endpoint — receipts are emitted by the checkout pipeline,
# spec-040). Per honest-gate the tool is UNAVAILABLE and must never reach the
# API client, even when its toggle is on.


class TestRunSignTrustReceiptIsPlanned(unittest.TestCase):
    """run_sign_trust_receipt is gated off — its backend is not deployed."""

    def setUp(self):
        self.client_mock = MagicMock()
        self.client_mock.call.return_value = {"jws": "eyJ...", "receiptId": "rc-001"}
        self.instance = _make_instance(self.client_mock)

    def test_raises_user_error_before_client_call(self):
        with self.assertRaises(_UserError):
            self.instance.run_sign_trust_receipt("SO123")
        self.client_mock.call.assert_not_called()

    def test_unavailable_even_with_agent_id(self):
        with self.assertRaises(_UserError):
            self.instance.run_sign_trust_receipt(
                "SO123", agent_id="did:web:agent.example"
            )
        self.client_mock.call.assert_not_called()

    def test_error_message_mentions_unavailability(self):
        try:
            self.instance.run_sign_trust_receipt("SO123")
            self.fail("Expected UserError")
        except _UserError as exc:
            self.assertIn("not available", str(exc).lower())

    def test_unavailable_takes_precedence_over_empty_order_id(self):
        # The planned-gate fires before argument validation.
        with self.assertRaises(_UserError):
            self.instance.run_sign_trust_receipt("")
        self.client_mock.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
