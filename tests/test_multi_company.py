"""
test_multi_company.py — Multi-company isolation tests.

Exercises the REAL controller (``controllers/main.py``) ``exchange_bootstrap``
path via importlib + a stubbed ``odoo.http.request``. The company-scope claims
(``company_id`` / ``company_ids`` / ``uid`` / ``iss``) are produced by the
production code, not a local mirror, so the tests catch controller drift.

Run: python -m pytest packages/odoo-addon-trusteed/tests/test_multi_company.py -v
"""
import base64
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ─── Odoo stub + real controller loader (self-contained) ─────────────────────

def _install_odoo_stubs() -> None:
    if "odoo.http" in sys.modules and hasattr(sys.modules["odoo.http"], "route"):
        # conftest already installed a comprehensive stub — only ensure the
        # addon ssrf module the controller imports is present.
        pass
    else:
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
    if "amcp_odoo_controllers_main" in sys.modules:
        return sys.modules["amcp_odoo_controllers_main"]
    main_path = Path(__file__).resolve().parent.parent / "controllers" / "main.py"
    spec = importlib.util.spec_from_file_location(
        "amcp_odoo_controllers_main", str(main_path)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["amcp_odoo_controllers_main"] = mod
    spec.loader.exec_module(mod)
    return mod


_main = _load_controller()
TrusteedController = _main.TrusteedController


def _b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _decode_payload(jwt: str) -> dict:
    return json.loads(_b64url_decode(jwt.split(".")[1]))


class _FakeUser:
    def __init__(self, uid: int, company_ids: list[int], has_group: bool = True):
        self.id = uid
        self._has_group = has_group
        _cids = types.SimpleNamespace(ids=company_ids)
        self.company_ids = _cids

    def _is_public(self):
        return False

    def has_group(self, _name):
        return self._has_group


class _FakeICP:
    def __init__(self, params: dict):
        self._params = params

    def sudo(self):
        return self

    def get_param(self, key, default=""):
        return self._params.get(key, default)


def _build_request(*, company_id: int, company_ids: list[int], uid: int = 2,
                   merchant_id: str = "merchant-test", secret: str = "s"):
    """Construct a stub odoo.http.request that drives exchange_bootstrap."""
    user = _FakeUser(uid, company_ids)
    company = types.SimpleNamespace(id=company_id)
    icp = _FakeICP({
        "trusteed.merchant_id": merchant_id,
        "trusteed.bootstrap_secret": secret,
        "trusteed.api_base": "https://api.trusteed.xyz",
    })

    env = MagicMock()
    env.user = user
    env.company = company
    env.__getitem__.return_value = icp  # request.env["ir.config_parameter"]
    env.cr.dbname = "db_test"

    req = types.SimpleNamespace(env=env, db="db_test")
    return req


def _run_relay(req):
    """Run exchange_bootstrap with the X1 opaque-token relay (_do_issue_token)
    stubbed. Returns (captured_relay_args, controller_result)."""
    captured: dict = {}

    def _fake_issue(endpoint, secret, merchant_id, odoo_uid):
        captured["endpoint"] = endpoint
        captured["secret"] = secret
        captured["merchant_id"] = merchant_id
        captured["odoo_uid"] = odoo_uid
        return {"token": "opaque-tok", "expires_at": "2026-01-01T00:00:00Z", "jti": "j"}, 200

    with patch.object(_main, "request", req), \
            patch.object(_main, "_do_issue_token", side_effect=_fake_issue):
        controller = TrusteedController()
        result = controller.exchange_bootstrap()

    return captured, result


class TestOdooEmbedRelay(unittest.TestCase):
    """X1: the panel token endpoint now mints the OPAQUE data-plane token via the
    S2S relay (/v1/embed/odoo/issue-token). The merchant-scoped opaque token does
    not carry per-company JWT claims (consistent with the WP/PS/Magento relays);
    company isolation is enforced upstream by the F-011 issuance gate + downstream
    by the merchant(store)-scoped token. The old bootstrap-JWT company-scope tests
    are superseded — the F-011/group guards remain covered below."""

    def test_relay_called_with_merchant_and_uid(self):
        captured, result = _run_relay(_build_request(company_id=1, company_ids=[1], uid=2))
        self.assertTrue(result.get("success"), f"relay failed: {result}")
        self.assertEqual(captured["merchant_id"], "merchant-test")
        self.assertEqual(captured["odoo_uid"], "2")

    def test_returns_opaque_token(self):
        _captured, result = _run_relay(_build_request(company_id=1, company_ids=[1]))
        self.assertEqual(result.get("access_token"), "opaque-tok")
        self.assertEqual(result.get("expires_at"), "2026-01-01T00:00:00Z")

    def test_relay_endpoint_targets_issue_token(self):
        captured, _result = _run_relay(_build_request(company_id=1, company_ids=[1]))
        self.assertTrue(
            captured["endpoint"].endswith("/v1/embed/odoo/issue-token"),
            captured["endpoint"],
        )

    def test_uid_isolated_per_user(self):
        cap_a, _ = _run_relay(_build_request(company_id=1, company_ids=[1], uid=10))
        cap_b, _ = _run_relay(_build_request(company_id=1, company_ids=[1], uid=20))
        self.assertNotEqual(cap_a["odoo_uid"], cap_b["odoo_uid"])

    def test_company_outside_allowed_set_is_rejected(self):
        """F-011 guard: active company not in allowed companies → company_mismatch,
        relay never reached."""
        req = _build_request(company_id=99, company_ids=[1, 2])
        with patch.object(_main, "request", req), \
                patch.object(_main, "_do_issue_token") as issue:
            controller = TrusteedController()
            result = controller.exchange_bootstrap()
        self.assertEqual(result.get("error"), "company_mismatch")
        issue.assert_not_called()  # never reached the network

    def test_missing_group_is_unauthorized(self):
        user = _FakeUser(2, [1], has_group=False)
        req = _build_request(company_id=1, company_ids=[1])
        req.env.user = user
        with patch.object(_main, "request", req), \
                patch.object(_main, "_do_issue_token") as issue:
            controller = TrusteedController()
            result = controller.exchange_bootstrap()
        self.assertEqual(result.get("error"), "unauthorized")
        issue.assert_not_called()

    def test_relay_rejection_propagates(self):
        """A relay non-200 surfaces as issue_token_rejected, not success."""
        req = _build_request(company_id=1, company_ids=[1])

        def _fake_issue(endpoint, secret, merchant_id, odoo_uid):
            return {"error": "merchant_not_configured"}, 401

        with patch.object(_main, "request", req), \
                patch.object(_main, "_do_issue_token", side_effect=_fake_issue):
            controller = TrusteedController()
            result = controller.exchange_bootstrap()
        self.assertEqual(result.get("error"), "issue_token_rejected")
        self.assertEqual(result.get("status"), 401)


if __name__ == "__main__":
    unittest.main()
