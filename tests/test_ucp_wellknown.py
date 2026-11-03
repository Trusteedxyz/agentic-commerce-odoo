"""
test_ucp_wellknown.py — Task 9 (UCP-1 biversion publication), Odoo side.

Exercises the REAL controller (``controllers/main.py``) via importlib, with a
minimal Odoo stub installed first — mirrors the pattern already used by
test_token_broker.py. No local reimplementation of the redirect logic: the
assertions run against the production ``TrusteedController.ucp_wellknown``
so the tests fail if the controller drifts.

Covers the two paths the contract promises (mirrors WooCommerce/PrestaShop):
  1. No `trusteed.merchant_id` configured → fail closed, 404.
  2. `trusteed.merchant_id` configured     → 302 to the composed central URL.

Run: python -m pytest packages/odoo-addon-trusteed/tests/test_ucp_wellknown.py -v
"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock


# ─── Odoo stub (must precede controller import) ──────────────────────────────

def _install_odoo_stubs() -> None:
    if not ("odoo.http" in sys.modules and hasattr(sys.modules["odoo.http"], "route")):
        odoo_mod = types.ModuleType("odoo")
        odoo_http = types.ModuleType("odoo.http")
        odoo_http.route = lambda *a, **kw: (lambda fn: fn)
        odoo_http.Controller = object
        odoo_http.request = None
        odoo_mod.http = odoo_http
        sys.modules.setdefault("odoo", odoo_mod)
        sys.modules.setdefault("odoo.http", odoo_http)

    for key in (
        "odoo.addons",
        "odoo.addons.trusteed",
        "odoo.addons.trusteed.utils",
    ):
        sys.modules.setdefault(key, types.ModuleType(key))

    # Load the real ssrf module the controller imports (controller does
    # `from odoo.addons.trusteed.utils.ssrf import validate_api_base`).
    if "odoo.addons.trusteed.utils.ssrf" not in sys.modules:
        ssrf_path = Path(__file__).resolve().parent.parent / "utils" / "ssrf.py"
        spec = importlib.util.spec_from_file_location(
            "odoo.addons.trusteed.utils.ssrf", str(ssrf_path)
        )
        ssrf_mod = importlib.util.module_from_spec(spec)
        sys.modules["odoo.addons.trusteed.utils.ssrf"] = ssrf_mod
        spec.loader.exec_module(ssrf_mod)
        sys.modules["odoo.addons.trusteed.utils"].ssrf = ssrf_mod


_install_odoo_stubs()


def _load_controller() -> types.ModuleType:
    main_path = Path(__file__).resolve().parent.parent / "controllers" / "main.py"
    spec = importlib.util.spec_from_file_location(
        "amcp_odoo_controllers_main_ucp", str(main_path)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["amcp_odoo_controllers_main_ucp"] = mod
    spec.loader.exec_module(mod)
    return mod


_main = _load_controller()
TrusteedController = _main.TrusteedController


# ─── Fake `request` (odoo.http.request) ──────────────────────────────────────

class _FakeConfigParameter:
    """Stands in for `request.env["ir.config_parameter"].sudo()`."""

    def __init__(self, params: dict):
        self._params = params

    def sudo(self):
        return self

    def get_param(self, key, default=None):
        return self._params.get(key, default)


class _FakeEnv:
    def __init__(self, params: dict):
        self._icp = _FakeConfigParameter(params)

    def __getitem__(self, model):
        assert model == "ir.config_parameter"
        return self._icp


class _FakeRequest:
    """Records what the controller asked for; mirrors odoo.http.WebRequest
    just enough for ucp_wellknown() — `.env`, `.not_found()`, `.redirect()`."""

    def __init__(self, params: dict):
        self.env = _FakeEnv(params)
        self.redirect_calls = []
        self.not_found_calls = 0

    def not_found(self):
        self.not_found_calls += 1
        return MagicMock(status_code=404)

    def redirect(self, location, code=303, local=True):
        self.redirect_calls.append({"location": location, "code": code, "local": local})
        return MagicMock(status_code=code, location=location)


def _invoke(params: dict):
    """Swap the module-level `request` binding, invoke the route, restore it."""
    fake_request = _FakeRequest(params)
    original = _main.request
    _main.request = fake_request
    try:
        controller = TrusteedController()
        result = controller.ucp_wellknown()
    finally:
        _main.request = original
    return fake_request, result


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestUcpWellknown(unittest.TestCase):
    def test_missing_merchant_id_fails_closed_to_404(self):
        fake_request, result = _invoke({"trusteed.api_base": "https://api.trusteed.xyz"})
        self.assertEqual(fake_request.not_found_calls, 1)
        self.assertEqual(len(fake_request.redirect_calls), 0)

    def test_blank_merchant_id_fails_closed_to_404(self):
        fake_request, _ = _invoke({
            "trusteed.merchant_id": "   ",
            "trusteed.api_base": "https://api.trusteed.xyz",
        })
        self.assertEqual(fake_request.not_found_calls, 1)

    def test_configured_merchant_id_redirects_to_central_ucp_profile(self):
        fake_request, _ = _invoke({
            "trusteed.merchant_id": "merchant-odoo-001",
            "trusteed.api_base": "https://api.trusteed.xyz",
        })
        self.assertEqual(fake_request.not_found_calls, 0)
        self.assertEqual(len(fake_request.redirect_calls), 1)
        call = fake_request.redirect_calls[0]
        self.assertEqual(
            call["location"],
            "https://api.trusteed.xyz/.well-known/ucp?store=merchant-odoo-001",
        )
        self.assertEqual(call["code"], 302)
        self.assertFalse(call["local"], "must allow an absolute external redirect target")

    def test_default_api_base_used_when_unconfigured(self):
        fake_request, _ = _invoke({"trusteed.merchant_id": "merchant-odoo-002"})
        call = fake_request.redirect_calls[0]
        self.assertEqual(
            call["location"],
            "https://api.trusteed.xyz/.well-known/ucp?store=merchant-odoo-002",
        )

    def test_merchant_id_is_url_encoded(self):
        fake_request, _ = _invoke({
            "trusteed.merchant_id": "m/1 &2",
            "trusteed.api_base": "https://api.trusteed.xyz",
        })
        call = fake_request.redirect_calls[0]
        self.assertEqual(
            call["location"],
            "https://api.trusteed.xyz/.well-known/ucp?store=m%2F1%20%262",
        )

    def test_ssrf_guard_blocks_non_trusteed_api_base(self):
        """api_base is admin-configurable — a rewritten host must fail closed,
        exactly like exchange_bootstrap()'s validate_api_base() gate."""
        fake_request, _ = _invoke({
            "trusteed.merchant_id": "merchant-odoo-003",
            "trusteed.api_base": "https://evil.example.com",
        })
        self.assertEqual(fake_request.not_found_calls, 1)
        self.assertEqual(len(fake_request.redirect_calls), 0)

    def test_ssrf_guard_blocks_private_ip_api_base(self):
        fake_request, _ = _invoke({
            "trusteed.merchant_id": "merchant-odoo-004",
            "trusteed.api_base": "https://192.168.1.1",
        })
        self.assertEqual(fake_request.not_found_calls, 1)

    def test_ssrf_guard_blocks_non_https_api_base(self):
        fake_request, _ = _invoke({
            "trusteed.merchant_id": "merchant-odoo-005",
            "trusteed.api_base": "http://api.trusteed.xyz",
        })
        self.assertEqual(fake_request.not_found_calls, 1)

    def test_trailing_slash_on_api_base_is_normalized(self):
        fake_request, _ = _invoke({
            "trusteed.merchant_id": "merchant-odoo-006",
            "trusteed.api_base": "https://api.trusteed.xyz/",
        })
        call = fake_request.redirect_calls[0]
        self.assertEqual(
            call["location"],
            "https://api.trusteed.xyz/.well-known/ucp?store=merchant-odoo-006",
        )


if __name__ == "__main__":
    unittest.main()
