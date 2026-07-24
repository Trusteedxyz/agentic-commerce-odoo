"""T080 — Structural tests for the TrusteedAiTool AbstractModel.

Verifies class-level declarations, method presence, XML record structure,
and credential-reading logic without requiring a live Odoo database.

Run:
    python -m pytest packages/odoo-addon-trusteed/tests/ \
        --rootdir=packages/odoo-addon-trusteed/tests -v
"""

import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Odoo runtime stub — must be installed before importing the model module
# ---------------------------------------------------------------------------

def _install_odoo_stubs():
    """Inject minimal odoo.* stubs into sys.modules so model imports succeed."""

    class _AccessError(Exception):
        pass

    class _UserError(Exception):
        pass

    class AbstractModel:
        _name = ""
        _description = ""

    odoo_mod = types.ModuleType("odoo")
    odoo_api = types.ModuleType("odoo.api")
    odoo_models = types.ModuleType("odoo.models")
    odoo_exc = types.ModuleType("odoo.exceptions")

    # api.model is a no-op decorator in tests
    odoo_api.model = lambda fn: fn
    odoo_api.constrains = lambda *a, **kw: (lambda fn: fn)

    odoo_models.AbstractModel = AbstractModel

    odoo_exc.AccessError = _AccessError
    odoo_exc.UserError = _UserError

    odoo_mod.api = odoo_api
    odoo_mod.models = odoo_models
    odoo_mod.exceptions = odoo_exc

    for name, mod in [
        ("odoo", odoo_mod),
        ("odoo.api", odoo_api),
        ("odoo.models", odoo_models),
        ("odoo.exceptions", odoo_exc),
    ]:
        sys.modules.setdefault(name, mod)

    return (
        sys.modules["odoo.exceptions"].UserError,
        sys.modules["odoo.exceptions"].AccessError,
    )


_UserError, _AccessError = _install_odoo_stubs()

# Stub ssrf dependency referenced by api_client
_ssrf_mod = types.ModuleType("odoo.addons.trusteed.utils.ssrf")
_ssrf_mod.validate_api_base = lambda url: url.startswith("https://")
sys.modules.setdefault("odoo.addons", types.ModuleType("odoo.addons"))
sys.modules.setdefault("odoo.addons.trusteed", types.ModuleType("odoo.addons.trusteed"))
sys.modules.setdefault("odoo.addons.trusteed.utils", types.ModuleType("odoo.addons.trusteed.utils"))
sys.modules.setdefault("odoo.addons.trusteed.utils.ssrf", _ssrf_mod)

_ADDON_ROOT = Path(__file__).parent.parent


def _load_ai_tool_module():
    """Import ai_tool_invocation as a plain module using importlib.

    Uses a hierarchical package alias ('addon_t080.models'/'addon_t080.utils')
    so the module's relative imports resolve:
      from .api_client import ...           → addon_t080.models.api_client
      from ..utils.tool_toggles import ...  → addon_t080.utils.tool_toggles
    """
    import importlib.util

    root_pkg = "addon_t080"
    models_pkg = f"{root_pkg}.models"
    utils_pkg = f"{root_pkg}.utils"
    for pkg in (root_pkg, models_pkg, utils_pkg):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

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
    return _load(
        f"{models_pkg}.ai_tool_invocation",
        models_pkg,
        "models",
        "ai_tool_invocation.py",
    )


_ai_tool_mod = _load_ai_tool_module()
TrusteedAiTool = _ai_tool_mod.TrusteedAiTool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAiToolModelDeclaration(unittest.TestCase):
    """T080-1: model class-level metadata."""

    def test_model_name_is_trusteed_ai_tool(self):
        self.assertEqual(TrusteedAiTool._name, "trusteed.ai.tool")

    def test_model_inherits_abstract_model(self):
        from odoo.models import AbstractModel
        self.assertTrue(issubclass(TrusteedAiTool, AbstractModel))

    def test_description_is_set(self):
        self.assertTrue(
            TrusteedAiTool._description,
            "Model must declare a non-empty _description",
        )


class TestAiToolMethodPresence(unittest.TestCase):
    """T080-2: all 5 public run_* methods must exist and be callable."""

    EXPECTED_METHODS = [
        "run_sign_trust_receipt",
        "run_verify_agent_signature",
        "run_dispatch_payment_acp",
        "run_dispatch_payment_x402",
        "run_dispatch_payment_ap2",
    ]

    def test_all_run_methods_present(self):
        for method in self.EXPECTED_METHODS:
            with self.subTest(method=method):
                self.assertTrue(
                    callable(getattr(TrusteedAiTool, method, None)),
                    f"Expected callable method '{method}' on TrusteedAiTool",
                )

    def test_private_helpers_present(self):
        self.assertTrue(callable(getattr(TrusteedAiTool, "_get_api_client", None)))
        self.assertTrue(callable(getattr(TrusteedAiTool, "_validate_dispatch_args", None)))


class TestGetApiClientCredentials(unittest.TestCase):
    """T080-3: _get_api_client credential resolution.

    Canonical key is ``trusteed.bootstrap_secret`` (written by Settings +
    seeded by system_parameters.xml). ``trusteed.bootstrap_token`` is honoured
    only as a backwards-compat fallback.
    """

    def _make_instance(self, token_value=None, fallback_value=None):
        instance = TrusteedAiTool.__new__(TrusteedAiTool)
        icp = MagicMock()

        def get_param_side(key, default=""):
            if key == "trusteed.api_base":
                return "https://api.trusteed.xyz"
            if key == "trusteed.bootstrap_token":
                return token_value or ""
            if key == "trusteed.bootstrap_secret":
                return fallback_value or ""
            return default

        icp.get_param.side_effect = get_param_side
        icp_sudo = MagicMock()
        icp_sudo.__getitem__ = lambda self, k: icp
        env = MagicMock()
        env.__getitem__ = lambda self, k: icp if "ir.config_parameter" in k else MagicMock()
        env_icp = MagicMock()
        env_icp.sudo.return_value = icp
        env.__getitem__ = lambda self, k: env_icp
        instance.env = env
        return instance, icp

    def test_reads_canonical_bootstrap_secret_param(self):
        instance, icp = self._make_instance(fallback_value="sec-xyz")
        client = instance._get_api_client()
        self.assertIsNotNone(client)
        # Canonical bootstrap_secret was queried.
        calls = [call.args[0] for call in icp.get_param.call_args_list]
        self.assertIn("trusteed.bootstrap_secret", calls)

    def test_falls_back_to_legacy_bootstrap_token(self):
        instance, icp = self._make_instance(token_value="tok-abc", fallback_value=None)
        client = instance._get_api_client()
        self.assertIsNotNone(client)
        calls = [call.args[0] for call in icp.get_param.call_args_list]
        self.assertIn("trusteed.bootstrap_token", calls)

    def test_raises_user_error_when_no_token(self):
        instance, _ = self._make_instance(token_value=None, fallback_value=None)
        with self.assertRaises(_UserError):
            instance._get_api_client()


class TestAiToolsXmlStructure(unittest.TestCase):
    """T080-4: ai_tools.xml declares exactly 5 records with usage='ai_tool'."""

    XML_PATH = _ADDON_ROOT / "data" / "ai_tools.xml"

    EXPECTED_IDS = {
        "action_trusteed_sign_trust_receipt",
        "action_trusteed_verify_agent_signature",
        "action_trusteed_dispatch_payment_acp",
        "action_trusteed_dispatch_payment_x402",
        "action_trusteed_dispatch_payment_ap2",
    }

    def _parse_records(self):
        tree = ET.parse(self.XML_PATH)
        root = tree.getroot()
        return root.findall(".//record[@model='ir.actions.server']")

    def test_xml_file_exists(self):
        self.assertTrue(self.XML_PATH.exists(), f"Missing: {self.XML_PATH}")

    def test_exactly_five_server_action_records(self):
        records = self._parse_records()
        self.assertEqual(len(records), 5, f"Expected 5 ai_tool records, found {len(records)}")

    def test_all_records_have_usage_ai_tool(self):
        records = self._parse_records()
        for rec in records:
            usage_field = rec.find("field[@name='usage']")
            self.assertIsNotNone(
                usage_field,
                f"Record '{rec.get('id')}' is missing <field name='usage'>",
            )
            self.assertEqual(
                usage_field.text,
                "ai_tool",
                f"Record '{rec.get('id')}' has wrong usage: '{usage_field.text}'",
            )

    def test_all_expected_xml_ids_present(self):
        records = self._parse_records()
        found_ids = {rec.get("id") for rec in records}
        self.assertEqual(found_ids, self.EXPECTED_IDS)

    def test_all_records_have_state_code(self):
        records = self._parse_records()
        for rec in records:
            state_field = rec.find("field[@name='state']")
            self.assertIsNotNone(state_field, f"Record '{rec.get('id')}' missing state field")
            self.assertEqual(state_field.text, "code")


if __name__ == "__main__":
    unittest.main()
