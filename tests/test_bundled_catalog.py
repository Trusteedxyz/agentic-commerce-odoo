"""spec-046 FR-018a — bundled offline catalog integrity (Odoo).

The Odoo addon registers its 5 tools declaratively (data/ai_tools.xml +
models/ai_tool_invocation.py) — there is NO runtime catalog fetch, so the tools
never depend on the network. FR-018a (bundled fallback) is satisfied by shipping
``data/agentic-tools-catalog.json`` as the offline source of truth, kept in sync
with the canonical SSOT (``packages/shared/src/agentic-tools/catalog.ts``) via
``scripts/export_tool_schemas.py``.

These tests assert the bundled file is well-formed, uses the OLA-1 `routing`
shape, and that its `planned`-backend tools agree with utils/tool_toggles.py
(so the catalog and the runtime gate cannot drift).

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_bundled_catalog.py \
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

_ADDON_ROOT = Path(__file__).resolve().parent.parent
_CATALOG_PATH = _ADDON_ROOT / "data" / "agentic-tools-catalog.json"


def _load_toggles():
    key = "amcp_odoo_tool_toggles_catalogtest"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(
        key, str(_ADDON_ROOT / "utils" / "tool_toggles.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


_tt = _load_toggles()

_VALID_KINDS = {"rest", "mcp-tool"}
_VALID_STATUS = {"implemented", "planned"}
_EXPECTED_IDS = {
    "trusteed/sign-trust-receipt",
    "trusteed/verify-agent-signature",
    "trusteed/dispatch-payment-acp",
    "trusteed/dispatch-payment-x402",
    "trusteed/dispatch-payment-ap2",
}


class TestBundledCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        cls.tools = cls.catalog["tools"]
        cls.by_id = {t["id"]: t for t in cls.tools}

    def test_file_exists_and_parses(self):
        self.assertTrue(_CATALOG_PATH.is_file())
        self.assertEqual(self.catalog["version"], "1.0.0")

    def test_has_all_five_tools(self):
        self.assertEqual(set(self.by_id), _EXPECTED_IDS)

    def test_every_tool_has_valid_routing(self):
        for tool in self.tools:
            routing = tool["routing"]
            self.assertIn(routing["kind"], _VALID_KINDS, tool["id"])
            self.assertIn(routing["backendStatus"], _VALID_STATUS, tool["id"])
            self.assertTrue(routing["target"], tool["id"])
            self.assertTrue(routing["notes"], tool["id"])
            # Legacy mcpToolName must be gone (OLA-1 contract).
            self.assertNotIn("mcpToolName", tool, tool["id"])

    def test_planned_tools_match_runtime_gate(self):
        """The catalog's `planned` set must equal the runtime PLANNED_TOOL_IDS so
        the offline advertisement and the gate never drift."""
        planned_in_catalog = {
            t["id"]
            for t in self.tools
            if t["routing"]["backendStatus"] == "planned"
        }
        self.assertEqual(planned_in_catalog, set(_tt.PLANNED_TOOL_IDS))

    def test_acp_routes_via_mcp_tool(self):
        routing = self.by_id["trusteed/dispatch-payment-acp"]["routing"]
        self.assertEqual(routing["kind"], "mcp-tool")
        self.assertEqual(routing["target"], "process_agent_payment")

    def test_verify_uses_s2s_endpoint(self):
        routing = self.by_id["trusteed/verify-agent-signature"]["routing"]
        # LANE 046-F1 Option A — merchant-facing per-store S2S route.
        self.assertEqual(
            routing["target"], "/api/v1/embed/agentic-tools/verify-agent-signature"
        )
        self.assertEqual(routing["backendStatus"], "implemented")

    def test_x402_uses_s2s_endpoint(self):
        routing = self.by_id["trusteed/dispatch-payment-x402"]["routing"]
        self.assertEqual(
            routing["target"], "/api/v1/embed/agentic-tools/dispatch-payment-x402"
        )


if __name__ == "__main__":
    unittest.main()
