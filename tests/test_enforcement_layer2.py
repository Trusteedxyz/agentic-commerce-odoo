"""pytest tests — spec-048 Capa 2 layer (_trusteed_cel_call_layer2).

Tests verify the Capa-2 HTTP call behaviour in SaleOrderEnforcement without
requiring a live Odoo ORM.  We stub the ORM surface (env, currency_id,
order_line, amount_total) and patch requests.post to control HTTP responses.

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_enforcement_layer2.py \\
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Odoo stubs (must come before any addon import)
# ---------------------------------------------------------------------------

def _ensure_odoo_stubs() -> None:
    if "odoo" in sys.modules and hasattr(sys.modules["odoo"], "http"):
        return

    import datetime

    class _FakeField:
        def __init__(self, *a, **kw): pass

    class _FakeDatetime(_FakeField):
        @staticmethod
        def now():
            return datetime.datetime(2026, 5, 7, 12, 0, 0)

    odoo_mod       = types.ModuleType("odoo")
    odoo_api       = types.ModuleType("odoo.api")
    odoo_fields    = types.ModuleType("odoo.fields")
    odoo_models    = types.ModuleType("odoo.models")
    odoo_exceptions = types.ModuleType("odoo.exceptions")
    odoo_http      = types.ModuleType("odoo.http")

    odoo_api.model = lambda fn: fn
    odoo_api.model_create_multi = lambda fn: fn

    for name in ("Char", "Integer", "Float", "Boolean", "Selection", "Text", "Many2one",
                 "One2many", "Many2many", "Date", "Monetary"):
        setattr(odoo_fields, name, _FakeField)
    odoo_fields.Datetime = _FakeDatetime

    class _AbstractModel:
        _name = ""
        _description = ""

    odoo_models.AbstractModel = _AbstractModel
    odoo_models.Model = _AbstractModel

    class ValidationError(Exception): pass
    odoo_exceptions.ValidationError = ValidationError

    odoo_http.route = lambda *a, **kw: (lambda fn: fn)
    odoo_http.Controller = object
    odoo_http.request = None

    odoo_mod.api        = odoo_api
    odoo_mod.fields     = odoo_fields
    odoo_mod.models     = odoo_models
    odoo_mod.exceptions = odoo_exceptions
    odoo_mod.http       = odoo_http

    for key, mod in [
        ("odoo", odoo_mod), ("odoo.api", odoo_api), ("odoo.fields", odoo_fields),
        ("odoo.models", odoo_models), ("odoo.exceptions", odoo_exceptions),
        ("odoo.http", odoo_http),
    ]:
        sys.modules.setdefault(key, mod)


_ensure_odoo_stubs()

# ---------------------------------------------------------------------------
# Load addon modules via importlib
# ---------------------------------------------------------------------------

_ADDON_ROOT = Path(__file__).parent.parent
_ROOT_PKG   = "addon_l2"
_MODELS_PKG = f"{_ROOT_PKG}.models"
_UTILS_PKG  = f"{_ROOT_PKG}.utils"


def _ensure_pkg(key: str) -> types.ModuleType:
    if key not in sys.modules:
        sys.modules[key] = types.ModuleType(key)
    return sys.modules[key]


def _load_module(rel: str, key: str, pkg: str) -> types.ModuleType:
    if key in sys.modules:
        return sys.modules[key]
    _ensure_pkg(pkg)
    spec = importlib.util.spec_from_file_location(key, _ADDON_ROOT / rel)
    mod  = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


_ensure_pkg(_ROOT_PKG)
_ensure_pkg(_MODELS_PKG)
_ensure_pkg(_UTILS_PKG)

_ssrf_mod = _load_module("utils/ssrf.py", f"{_UTILS_PKG}.ssrf", _UTILS_PKG)
sys.modules[_UTILS_PKG].ssrf = _ssrf_mod  # type: ignore
sys.modules.setdefault(f"{_ROOT_PKG}.utils.ssrf", _ssrf_mod)

_verifier_mod = _load_module(
    "models/enforcement_token_verifier.py",
    f"{_MODELS_PKG}.enforcement_token_verifier",
    _MODELS_PKG,
)

_enforcement_mod = _load_module(
    "models/sale_order_enforcement.py",
    f"{_MODELS_PKG}.sale_order_enforcement",
    _MODELS_PKG,
)

SaleOrderEnforcement = _enforcement_mod.SaleOrderEnforcement

# ---------------------------------------------------------------------------
# Minimal ORM stub to instantiate SaleOrderEnforcement
# ---------------------------------------------------------------------------

_BASE_SNAPSHOT = {
    "merchantId":    "merch-odoo-test",
    "installationId": "install-odoo-001",
    "hmacSecret":    "super-secret-hmac-key-32bytesxx",
}


def _make_order(*, amount_total: float = 100.0, item_count: int = 2):
    """Return a minimal SaleOrderEnforcement instance with stubbed ORM."""

    class _FakeCurrency:
        name = "EUR"

    class _FakeEnv(dict):
        def __getitem__(self, key):
            return self

        def sudo(self):
            return self

        def get_param(self, key, default=""):
            return "https://api.trusteed.xyz"

    order = object.__new__(SaleOrderEnforcement)
    order.id           = 42
    order.amount_total = amount_total
    order.currency_id  = _FakeCurrency()
    order.order_line   = [object()] * item_count
    order.env          = _FakeEnv()
    order.x_trusteed_agent_token = None
    return order


# ---------------------------------------------------------------------------
# Tests — _trusteed_cel_call_layer2
# ---------------------------------------------------------------------------

class TestCallLayer2:
    """Unit tests for SaleOrderEnforcement._trusteed_cel_call_layer2()."""

    def _call(self, order, mock_resp=None, exc=None, snapshot=None):
        snap = {**_BASE_SNAPSHOT, **(snapshot or {})}
        if exc is not None:
            with patch("requests.post", side_effect=exc):
                return order._trusteed_cel_call_layer2("did:web:agent.test", 80.0, snap, "ev-test")
        if mock_resp is not None:
            with patch("requests.post", return_value=mock_resp):
                return order._trusteed_cel_call_layer2("did:web:agent.test", 80.0, snap, "ev-test")
        return order._trusteed_cel_call_layer2("did:web:agent.test", 80.0, snap, "ev-test")

    # T088-1: network exception → None
    def test_returns_none_on_network_exception(self):
        order = _make_order()
        result = self._call(order, exc=ConnectionError("refused"))
        assert result is None, "Network exception must return None"

    # T088-2: non-200 HTTP status → None
    def test_returns_none_on_non_200(self):
        order = _make_order()
        mock = MagicMock()
        mock.status_code = 500
        result = self._call(order, mock_resp=mock)
        assert result is None, "HTTP 500 must return None"

    # T088-3: ALLOW decision
    def test_returns_allow_decision(self):
        order = _make_order()
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {
            "decision": "ALLOW",
            "ruleCode": "",
            "reason": "",
            "evaluationId": "eval-odoo-allow-001",
        }
        result = self._call(order, mock_resp=mock)
        assert result is not None
        assert result["decision"] == "ALLOW"
        assert result["evaluationId"] == "eval-odoo-allow-001"

    # T088-4: BLOCK decision with ruleCode
    def test_returns_block_decision_with_rule_code(self):
        order = _make_order(amount_total=500.0, item_count=10)
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {
            "decision": "BLOCK",
            "ruleCode": "trusteed:R010",
            "reason": "Stripe risk score exceeded threshold",
            "evaluationId": "eval-odoo-block-002",
        }
        result = self._call(order, mock_resp=mock)
        assert result is not None
        assert result["decision"] == "BLOCK"
        assert result["ruleCode"] == "trusteed:R010"
        assert result["reason"] == "Stripe risk score exceeded threshold"

    # T088-5: missing merchantId/installationId/hmacSecret → None (skip)
    def test_returns_none_when_credentials_missing(self):
        order = _make_order()
        result = self._call(order, snapshot={"merchantId": "", "installationId": "", "hmacSecret": ""})
        assert result is None, "Missing credentials must return None without HTTP call"

    # T088-6: SSRF guard — private IP base → None
    def test_returns_none_on_ssrf_blocked_api_base(self):
        order = _make_order()

        class _FakeEnv(dict):
            def __getitem__(self, key): return self
            def sudo(self): return self
            def get_param(self, key, default=""): return "https://192.168.1.1/evil"

        order.env = _FakeEnv()
        result = self._call(order)
        assert result is None, "SSRF-blocked api_base must return None"

    # T088-7: response body missing 'decision' key → None
    def test_returns_none_when_decision_key_missing(self):
        order = _make_order()
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"status": "ok"}
        result = self._call(order, mock_resp=mock)
        assert result is None, "Response without 'decision' key must return None"

    # T088-8: verify HMAC signature header is present in the POST call
    def test_hmac_signature_header_is_set(self):
        order = _make_order()
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"decision": "ALLOW", "ruleCode": "", "reason": "", "evaluationId": "x"}

        captured_kwargs: dict = {}

        def _capture_post(url, **kwargs):
            captured_kwargs.update(kwargs)
            return mock

        with patch("requests.post", side_effect=_capture_post):
            order._trusteed_cel_call_layer2("did:web:agent.test", 80.0, _BASE_SNAPSHOT, "ev-sig")

        headers = captured_kwargs.get("headers", {})
        assert "X-Trusteed-Signature" in headers, "HMAC signature header must be set"
        sig = headers["X-Trusteed-Signature"]
        assert sig.startswith("t=") and ",s=" in sig, f"Signature format must be t={{ts}},s={{mac}}: got {sig}"
