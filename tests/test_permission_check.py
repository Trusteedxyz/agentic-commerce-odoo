"""T083 — Permission and argument validation tests.

The implementation does not have a _check_permission method; access control
is enforced by Odoo's ir.model.access.csv (ORM-level, not testable without DB).
This file covers the equivalent guards that ARE testable in pure Python:

  1. _validate_dispatch_args — the static input guard for all payment tools
  2. ir.model.access.csv — structural assertions on declared ACL rows
  3. _get_api_client — raises UserError when no token is configured (fail-closed)

Each section corresponds to a T083 sub-task.

Run:
    python -m pytest packages/odoo-addon-trusteed/tests/ \
        --rootdir=packages/odoo-addon-trusteed/tests -v
"""

import csv
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Odoo + ssrf stubs (idempotent)
# ---------------------------------------------------------------------------

def _ensure_stubs():
    class _UserError(Exception):
        pass

    class AbstractModel:
        _name = ""
        _description = ""

    defaults = {
        "odoo": {},
        "odoo.api": {"model": lambda fn: fn, "constrains": lambda *a, **kw: (lambda fn: fn)},
        "odoo.models": {"AbstractModel": AbstractModel},
        "odoo.exceptions": {"UserError": _UserError, "AccessError": Exception},
    }
    for name, attrs in defaults.items():
        if name not in sys.modules:
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[name] = mod

    if "odoo.addons.trusteed.utils.ssrf" not in sys.modules:
        ssrf = types.ModuleType("odoo.addons.trusteed.utils.ssrf")
        ssrf.validate_api_base = lambda url: url.startswith("https://")
        for n, m in [
            ("odoo.addons", types.ModuleType("odoo.addons")),
            ("odoo.addons.trusteed", types.ModuleType("odoo.addons.trusteed")),
            ("odoo.addons.trusteed.utils", types.ModuleType("odoo.addons.trusteed.utils")),
            ("odoo.addons.trusteed.utils.ssrf", ssrf),
        ]:
            sys.modules.setdefault(n, m)

    return sys.modules["odoo.exceptions"].UserError


_UserError = _ensure_stubs()

_ADDON_ROOT = Path(__file__).parent.parent


def _load_ai_tool():
    import importlib.util

    root_pkg = "addon_t083"
    models_pkg = f"{root_pkg}.models"
    utils_pkg = f"{root_pkg}.utils"
    for pkg in (root_pkg, models_pkg, utils_pkg):
        sys.modules.setdefault(pkg, types.ModuleType(pkg))

    def _load(key, parent_pkg, subdir, filename):
        if key in sys.modules:
            return sys.modules[key]
        spec = importlib.util.spec_from_file_location(
            key, _ADDON_ROOT / subdir / filename
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = parent_pkg
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
        return mod

    _load(f"{models_pkg}.api_client", models_pkg, "models", "api_client.py")
    _load(f"{utils_pkg}.tool_toggles", utils_pkg, "utils", "tool_toggles.py")
    ai_mod = _load(
        f"{models_pkg}.ai_tool_invocation",
        models_pkg,
        "models",
        "ai_tool_invocation.py",
    )
    return ai_mod.TrusteedAiTool


TrusteedAiTool = _load_ai_tool()

_CART = "cart-abc"
_IK = "ik-abc"
_MID = "merchant-abc"


# ---------------------------------------------------------------------------
# T083-1: _validate_dispatch_args — the real fail-closed guard
# ---------------------------------------------------------------------------

class TestValidateDispatchArgs(unittest.TestCase):
    """_validate_dispatch_args raises UserError for any missing required arg."""

    def test_valid_args_do_not_raise(self):
        # Should complete without exception
        TrusteedAiTool._validate_dispatch_args(_CART, _IK, _MID)

    def test_empty_cart_id_raises(self):
        with self.assertRaises(_UserError):
            TrusteedAiTool._validate_dispatch_args("", _IK, _MID)

    def test_whitespace_cart_id_raises(self):
        with self.assertRaises(_UserError):
            TrusteedAiTool._validate_dispatch_args("   ", _IK, _MID)

    def test_empty_idempotency_key_raises(self):
        with self.assertRaises(_UserError):
            TrusteedAiTool._validate_dispatch_args(_CART, "", _MID)

    def test_empty_merchant_id_raises(self):
        with self.assertRaises(_UserError):
            TrusteedAiTool._validate_dispatch_args(_CART, _IK, "")

    def test_non_uuid_idempotency_key_is_accepted(self):
        """Non-UUID idempotency keys are accepted with a debug log (not rejected)."""
        TrusteedAiTool._validate_dispatch_args(_CART, "not-a-uuid", _MID)

    def test_valid_uuid_idempotency_key_accepted(self):
        TrusteedAiTool._validate_dispatch_args(
            _CART, "550e8400-e29b-41d4-a716-446655440000", _MID
        )


# ---------------------------------------------------------------------------
# T083-2: _get_api_client fail-closed when token is absent
# ---------------------------------------------------------------------------

class TestApiClientFailClosed(unittest.TestCase):
    """_get_api_client raises UserError rather than silently using empty credentials."""

    def _make_instance(self, token="", secret=""):
        inst = TrusteedAiTool.__new__(TrusteedAiTool)
        icp = MagicMock()

        def get_param(key, default=""):
            if key == "trusteed.api_base":
                return "https://api.trusteed.xyz"
            if key == "trusteed.bootstrap_token":
                return token
            if key == "trusteed.bootstrap_secret":
                return secret
            return default

        icp.get_param.side_effect = get_param
        env = MagicMock()
        env_icp = MagicMock()
        env_icp.sudo.return_value = icp
        env.__getitem__ = lambda self, k: env_icp
        inst.env = env
        return inst

    def test_raises_when_both_token_and_secret_absent(self):
        inst = self._make_instance(token="", secret="")
        with self.assertRaises(_UserError):
            inst._get_api_client()

    def test_succeeds_with_canonical_bootstrap_secret(self):
        # Canonical key — written by Settings + seeded by system_parameters.xml.
        inst = self._make_instance(token="", secret="sec-live")
        client = inst._get_api_client()
        self.assertIsNotNone(client)

    def test_succeeds_with_legacy_bootstrap_token_fallback(self):
        # Legacy key is still honoured as a backwards-compat fallback only.
        inst = self._make_instance(token="tok-live", secret="")
        client = inst._get_api_client()
        self.assertIsNotNone(client)

    def test_canonical_secret_wins_over_legacy_token(self):
        inst = self._make_instance(token="tok-legacy", secret="sec-canonical")
        client = inst._get_api_client()
        # bootstrap_secret is primary — the client must carry it, not the legacy token.
        self.assertEqual(client._token, "sec-canonical")

    def test_error_message_mentions_canonical_parameter_name(self):
        inst = self._make_instance()
        try:
            inst._get_api_client()
            self.fail("Expected UserError")
        except _UserError as exc:
            self.assertIn("trusteed.bootstrap_secret", str(exc))


# ---------------------------------------------------------------------------
# T083-3: ir.model.access.csv structural assertions
# ---------------------------------------------------------------------------

class TestIrModelAccessCsv(unittest.TestCase):
    """Security CSV declares the expected ACL rows for trusteed.ai.tool."""

    CSV_PATH = _ADDON_ROOT / "security" / "ir.model.access.csv"

    def _parse_rows(self):
        with open(self.CSV_PATH, newline="") as f:
            return list(csv.DictReader(f))

    def test_csv_file_exists(self):
        self.assertTrue(self.CSV_PATH.exists())

    def test_group_user_has_read_only_on_ai_tool(self):
        rows = self._parse_rows()
        user_row = next(
            (r for r in rows if "ai_tool" in r["model_id:id"] and "group_user" in r["group_id:id"]),
            None,
        )
        self.assertIsNotNone(user_row, "No read-only row for base.group_user on trusteed.ai.tool")
        self.assertEqual(user_row["perm_read"], "1")
        self.assertEqual(user_row["perm_write"], "0")
        self.assertEqual(user_row["perm_create"], "0")
        self.assertEqual(user_row["perm_unlink"], "0")

    def test_group_admin_has_full_access_on_ai_tool(self):
        rows = self._parse_rows()
        admin_row = next(
            (r for r in rows if "ai_tool" in r["model_id:id"] and "group_admin" in r["group_id:id"]),
            None,
        )
        self.assertIsNotNone(admin_row, "No admin row for group_admin on trusteed.ai.tool")
        self.assertEqual(admin_row["perm_read"], "1")
        self.assertEqual(admin_row["perm_write"], "1")
        self.assertEqual(admin_row["perm_create"], "1")
        self.assertEqual(admin_row["perm_unlink"], "1")

    def test_all_rows_have_required_columns(self):
        required = {"id", "name", "model_id:id", "group_id:id", "perm_read"}
        rows = self._parse_rows()
        for row in rows:
            missing = required - set(row.keys())
            self.assertFalse(missing, f"Row '{row.get('id')}' is missing columns: {missing}")


if __name__ == "__main__":
    unittest.main()
