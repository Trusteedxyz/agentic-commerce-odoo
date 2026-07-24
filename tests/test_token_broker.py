"""
test_token_broker.py — Unit tests for the bootstrap JWT signing logic.

Exercises the REAL controller (``controllers/main.py``) via importlib, with a
minimal Odoo stub installed first. No local reimplementation of the crypto —
the assertions run against the production ``_sign_bootstrap`` / ``_do_exchange``
helpers so the tests fail if the controller drifts.

Run: python -m pytest packages/odoo-addon-trusteed/tests/test_token_broker.py -v
"""
import base64
import hashlib
import hmac
import importlib.util
import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


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
        "amcp_odoo_controllers_main", str(main_path)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["amcp_odoo_controllers_main"] = mod
    spec.loader.exec_module(mod)
    return mod


_main = _load_controller()

# Real production symbols under test.
_sign_bootstrap = _main._sign_bootstrap
_do_exchange = _main._do_exchange
TrusteedController = _main.TrusteedController


# ─── Local decode helpers (verification only, never re-signing) ──────────────

def _b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _make_payload(merchant_id: str, company_id: int = 1, uid: int = 2) -> dict:
    now = int(time.time())
    return {
        "merchant_id": merchant_id,
        "iss": f"odoo:{company_id}",
        "iat": now,
        "exp": now + 30,
        "jti": hashlib.sha256(f"{now}-{merchant_id}".encode()).hexdigest()[:32],
        "scope": {
            "company_id": company_id,
            "company_ids": [company_id],
            "uid": uid,
        },
    }


def _make_jwt(merchant_id: str, secret: str, company_id: int = 1) -> str:
    return _sign_bootstrap(_make_payload(merchant_id, company_id), secret)


# ─── Tests — REAL _sign_bootstrap structure ──────────────────────────────────

class TestBootstrapJwtStructure(unittest.TestCase):
    """Verify JWT structure produced by the production _sign_bootstrap."""

    def test_jwt_has_three_parts(self):
        jwt = _make_jwt("merchant-123", "test-secret")
        self.assertEqual(len(jwt.split(".")), 3)

    def test_header_contains_kid(self):
        jwt = _make_jwt("merchant-abc", "secret")
        header = json.loads(_b64url_decode(jwt.split(".")[0]))
        self.assertEqual(header["kid"], "merchant-abc")
        self.assertEqual(header["alg"], "HS256")
        self.assertEqual(header["typ"], "JWT")

    def test_payload_scope_contains_company_id(self):
        jwt = _make_jwt("m-1", "s", company_id=7)
        payload = json.loads(_b64url_decode(jwt.split(".")[1]))
        self.assertEqual(payload["scope"]["company_id"], 7)

    def test_payload_scope_contains_company_ids_list(self):
        jwt = _make_jwt("m-1", "s", company_id=3)
        payload = json.loads(_b64url_decode(jwt.split(".")[1]))
        self.assertIn(3, payload["scope"]["company_ids"])

    def test_secret_not_in_jwt_string(self):
        secret = "super-secret-do-not-expose"
        jwt = _make_jwt("m-1", secret)
        self.assertNotIn(secret, jwt)

    def test_signature_verifiable_with_correct_secret(self):
        secret = "verify-me-correct"
        jwt = _make_jwt("m-2", secret)
        parts = jwt.split(".")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        expected_sig = hmac.new(
            secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(parts[2])
        self.assertEqual(actual_sig, expected_sig)

    def test_signature_fails_with_wrong_secret(self):
        jwt = _make_jwt("m-2", "correct-secret")
        parts = jwt.split(".")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        wrong_sig = hmac.new(
            b"wrong-secret", signing_input, hashlib.sha256
        ).digest()
        actual_sig = _b64url_decode(parts[2])
        self.assertNotEqual(actual_sig, wrong_sig)

    def test_merchant_id_drives_kid(self):
        """kid must be derived from payload merchant_id (real contract)."""
        jwt = _sign_bootstrap(_make_payload("merchant-xyz"), "s")
        header = json.loads(_b64url_decode(jwt.split(".")[0]))
        self.assertEqual(header["kid"], "merchant-xyz")


# ─── Tests — REAL _do_exchange HTTP behaviour ────────────────────────────────

class TestDoExchange(unittest.TestCase):
    """Exercise the production _do_exchange error/redirect handling."""

    def test_redirect_is_rejected(self):
        resp = MagicMock()
        resp.is_redirect = True
        resp.status_code = 302
        with patch.object(_main.requests, "post", return_value=resp):
            body, status = _do_exchange("https://api.trusteed.xyz/x", "jwt")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bootstrap_redirect_rejected")

    def test_timeout_returns_504(self):
        with patch.object(
            _main.requests, "post",
            side_effect=_main.requests.exceptions.Timeout(),
        ):
            body, status = _do_exchange("https://api.trusteed.xyz/x", "jwt")
        self.assertEqual(status, 504)
        self.assertEqual(body["error"], "bootstrap_timeout")

    def test_connection_error_returns_503(self):
        with patch.object(
            _main.requests, "post",
            side_effect=_main.requests.exceptions.ConnectionError("refused"),
        ):
            body, status = _do_exchange("https://api.trusteed.xyz/x", "jwt")
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "bootstrap_connection_error")

    def test_success_passthrough(self):
        resp = MagicMock()
        resp.is_redirect = False
        resp.status_code = 200
        resp.json.return_value = {"success": True, "data": {"access_token": "tok"}}
        with patch.object(_main.requests, "post", return_value=resp):
            body, status = _do_exchange("https://api.trusteed.xyz/x", "jwt")
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])

    def test_bootstrap_jwt_sent_in_authorization_header(self):
        """The signed JWT must be forwarded as a Bearer token, never the secret."""
        captured: dict = {}

        def _capture(url, **kwargs):
            captured.update(kwargs)
            resp = MagicMock()
            resp.is_redirect = False
            resp.status_code = 200
            resp.json.return_value = {"success": True}
            return resp

        with patch.object(_main.requests, "post", side_effect=_capture):
            _do_exchange("https://api.trusteed.xyz/x", "signed-jwt-value")

        auth = captured["headers"]["Authorization"]
        self.assertEqual(auth, "Bearer signed-jwt-value")
        # allow_redirects must be disabled (redirect-SSRF defense).
        self.assertFalse(captured["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
