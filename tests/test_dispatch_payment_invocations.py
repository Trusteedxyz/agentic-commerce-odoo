"""T082 — Dispatch payment tool invocation tests.

Covers run_verify_agent_signature, run_dispatch_payment_acp,
run_dispatch_payment_x402, and run_dispatch_payment_ap2:
correct API paths, argument mapping, and UserError wrapping.

Run:
    python -m pytest packages/odoo-addon-trusteed/tests/ \
        --rootdir=packages/odoo-addon-trusteed/tests -v
"""

import sys
import types
import unittest
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
        "odoo": {"api": None, "models": None, "exceptions": None},
        "odoo.api": {"model": lambda fn: fn, "constrains": lambda *a, **kw: (lambda fn: fn)},
        "odoo.models": {"AbstractModel": AbstractModel},
        "odoo.exceptions": {"UserError": _UserError, "AccessError": Exception},
    }
    for name, attrs in defaults.items():
        if name not in sys.modules:
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                if v is not None:
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


def _load_modules():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).parent.parent
    # Hierarchical package so ai_tool_invocation's relative imports resolve:
    #   from .api_client import ...          → addon_t082.models.api_client
    #   from ..utils.tool_toggles import ... → addon_t082.utils.tool_toggles
    root_pkg = "addon_t082"
    models_pkg = f"{root_pkg}.models"
    utils_pkg = f"{root_pkg}.utils"
    for pkg in (root_pkg, models_pkg, utils_pkg):
        sys.modules.setdefault(pkg, types.ModuleType(pkg))

    def _load(key, parent_pkg, subdir, filename):
        if key in sys.modules:
            return sys.modules[key]
        spec = importlib.util.spec_from_file_location(key, root / subdir / filename)
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = parent_pkg
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
        return mod

    api_client = _load(
        f"{models_pkg}.api_client", models_pkg, "models", "api_client.py"
    )
    _load(
        f"{utils_pkg}.tool_toggles", utils_pkg, "utils", "tool_toggles.py"
    )

    ai_mod = _load(
        f"{models_pkg}.ai_tool_invocation",
        models_pkg,
        "models",
        "ai_tool_invocation.py",
    )
    return ai_mod, api_client


_ai_mod, _client_mod = _load_modules()
TrusteedAiTool = _ai_mod.TrusteedAiTool
TrusteedApiError = _client_mod.TrusteedApiError

_CART = "cart-uuid-0001"
_IK = "ik-uuid-0002"
_MID = "merchant-uuid-0003"


class _FakeICP:
    """ir.config_parameter accessor with all FR-018b toggles enabled.

    Planned-backend tools (sign / ap2) still fail via is_available(), so this
    only exercises the implemented+enabled path for the dispatch tools.
    """

    def sudo(self):
        return self

    def get_param(self, key, default=""):
        if str(key).startswith("trusteed.tool."):
            return "1"
        # LANE 046-F1: the merchant-facing S2S route authenticates by the
        # authoritative per-store merchant id read from this system parameter.
        if str(key) == "trusteed.merchant_id":
            return _MID
        return default


class _FakeEnv:
    def __getitem__(self, _key):
        return _FakeICP()


def _instance(return_value=None, side_effect=None):
    inst = TrusteedAiTool.__new__(TrusteedAiTool)
    client = MagicMock()
    if side_effect:
        client.call.side_effect = side_effect
        # LANE 046-F1: verify + x402 now use the per-store S2S call path.
        client.call_with_s2s.side_effect = side_effect
    else:
        client.call.return_value = return_value or {}
        client.call_with_s2s.return_value = return_value or {}
    inst._get_api_client = MagicMock(return_value=client)
    inst.env = _FakeEnv()
    return inst, client


# ---------------------------------------------------------------------------
# run_verify_agent_signature
# ---------------------------------------------------------------------------

class TestVerifyAgentSignature(unittest.TestCase):

    def test_calls_correct_endpoint(self):
        inst, client = _instance({"verified": True, "agentId": "did:web:a"})
        inst.run_verify_agent_signature("POST", "https://shop.example/checkout", {"Signature": "sig"})
        # LANE 046-F1 Option A — merchant-facing per-store S2S route.
        self.assertEqual(
            client.call_with_s2s.call_args.args[0],
            "/api/v1/embed/agentic-tools/verify-agent-signature",
        )
        # merchant_id (3rd positional) authenticates the store-scoped route.
        self.assertEqual(client.call_with_s2s.call_args.args[2], _MID)
        client.call.assert_not_called()

    def test_body_maps_method_url_headers(self):
        inst, client = _instance({"verified": True})
        hdrs = {"Signature": "s1", "Signature-Input": "s2"}
        inst.run_verify_agent_signature("POST", "https://shop.example/pay", hdrs)
        body = client.call_with_s2s.call_args.args[1]
        self.assertEqual(body["method"], "POST")
        self.assertEqual(body["url"], "https://shop.example/pay")
        self.assertEqual(body["headers"], hdrs)

    def test_returns_response(self):
        inst, _ = _instance({"verified": True, "verificationStatus": "verified"})
        result = inst.run_verify_agent_signature("GET", "https://x.trusteed.xyz/", {})
        self.assertTrue(result["verified"])

    def test_missing_method_raises_user_error(self):
        inst, _ = _instance()
        with self.assertRaises(_UserError):
            inst.run_verify_agent_signature("", "https://x.trusteed.xyz/", {})

    def test_missing_url_raises_user_error(self):
        inst, _ = _instance()
        with self.assertRaises(_UserError):
            inst.run_verify_agent_signature("POST", "", {})

    def test_non_dict_headers_raises_user_error(self):
        inst, _ = _instance()
        with self.assertRaises(_UserError):
            inst.run_verify_agent_signature("POST", "https://x.trusteed.xyz/", "not-a-dict")

    def test_api_error_wrapped_in_user_error(self):
        inst, _ = _instance(side_effect=TrusteedApiError("timeout"))
        with self.assertRaises(_UserError):
            inst.run_verify_agent_signature("POST", "https://x.trusteed.xyz/", {})


# ---------------------------------------------------------------------------
# run_dispatch_payment_acp
# ---------------------------------------------------------------------------

class TestDispatchPaymentAcp(unittest.TestCase):
    """ACP (dispatch-payment-acp) routes through the MCP checkout-bucket tool
    `process_agent_payment` (POST /:storeSlug/mcp), NOT a REST route. The Odoo
    module does not provision a store-slug MCP gateway, so the rail is not
    fulfillable here: it MUST fail-closed with a UserError and never reach the
    REST API client (no guaranteed-404 dispatch). Mirrors PrestaShop
    DispatchPaymentAcpTool.
    """

    def test_not_fulfillable_raises_before_client_call(self):
        inst, client = _instance({"status": "ok", "paymentId": "p1"})
        with self.assertRaises(_UserError):
            inst.run_dispatch_payment_acp(_CART, _IK, _MID)
        client.call.assert_not_called()

    def test_error_mentions_mcp_gateway(self):
        inst, _ = _instance()
        try:
            inst.run_dispatch_payment_acp(_CART, _IK, _MID)
            self.fail("Expected UserError")
        except _UserError as exc:
            self.assertIn("mcp", str(exc).lower())

    def test_empty_cart_id_raises_user_error(self):
        inst, client = _instance()
        with self.assertRaises(_UserError):
            inst.run_dispatch_payment_acp("", _IK, _MID)
        client.call.assert_not_called()

    def test_empty_idempotency_key_raises_user_error(self):
        inst, client = _instance()
        with self.assertRaises(_UserError):
            inst.run_dispatch_payment_acp(_CART, "", _MID)
        client.call.assert_not_called()


# ---------------------------------------------------------------------------
# run_dispatch_payment_x402
# ---------------------------------------------------------------------------

class TestDispatchPaymentX402(unittest.TestCase):

    _PAYLOAD = {"network": "base", "token": "USDC", "amount": "10.00", "signature": "0xabc"}

    def test_calls_correct_endpoint(self):
        inst, client = _instance({"status": "ok", "txHash": "0xdeadbeef"})
        inst.run_dispatch_payment_x402(_CART, _IK, _MID, self._PAYLOAD)
        # LANE 046-F1 Option A — merchant-facing per-store S2S route.
        self.assertEqual(
            client.call_with_s2s.call_args.args[0],
            "/api/v1/embed/agentic-tools/dispatch-payment-x402",
        )
        self.assertEqual(client.call_with_s2s.call_args.args[2], _MID)
        client.call.assert_not_called()

    def test_body_includes_payment_payload(self):
        inst, client = _instance({"status": "ok"})
        inst.run_dispatch_payment_x402(_CART, _IK, _MID, self._PAYLOAD)
        body = client.call_with_s2s.call_args.args[1]
        self.assertEqual(body["paymentPayload"], self._PAYLOAD)
        self.assertEqual(body["cartId"], _CART)

    def test_empty_payment_payload_raises_user_error(self):
        inst, _ = _instance()
        with self.assertRaises(_UserError):
            inst.run_dispatch_payment_x402(_CART, _IK, _MID, {})

    def test_non_dict_payment_payload_raises_user_error(self):
        inst, _ = _instance()
        with self.assertRaises(_UserError):
            inst.run_dispatch_payment_x402(_CART, _IK, _MID, "not-a-dict")

    def test_api_error_wrapped_in_user_error(self):
        inst, _ = _instance(side_effect=TrusteedApiError("403"))
        with self.assertRaises(_UserError):
            inst.run_dispatch_payment_x402(_CART, _IK, _MID, self._PAYLOAD)


# ---------------------------------------------------------------------------
# run_dispatch_payment_ap2
# ---------------------------------------------------------------------------

class TestDispatchPaymentAp2(unittest.TestCase):
    """AP2 (dispatch-payment-ap2) is `backendStatus: "planned"` in the canonical
    catalog (no deployed dispatch route — spec-044 dormant). Per honest-gate it
    is UNAVAILABLE and must never reach the API client, regardless of args or
    toggle state.
    """

    _JWT = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJhZ2VudCJ9.sig"

    def test_planned_tool_raises_before_client_call(self):
        inst, client = _instance({"status": "ok", "paymentId": "p99"})
        with self.assertRaises(_UserError):
            inst.run_dispatch_payment_ap2(_CART, _IK, _MID, self._JWT)
        client.call.assert_not_called()

    def test_planned_tool_unavailable_even_with_valid_args(self):
        inst, client = _instance({"status": "settled", "mandateId": "m-001"})
        with self.assertRaises(_UserError):
            inst.run_dispatch_payment_ap2(_CART, _IK, _MID, self._JWT)
        client.call.assert_not_called()

    def test_planned_tool_error_mentions_unavailability(self):
        inst, _ = _instance()
        try:
            inst.run_dispatch_payment_ap2(_CART, _IK, _MID, self._JWT)
            self.fail("Expected UserError")
        except _UserError as exc:
            self.assertIn("not available", str(exc).lower())


if __name__ == "__main__":
    unittest.main()
