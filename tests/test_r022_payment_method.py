"""pytest tests — R022 payment-rail-restriction: _trusteed_resolve_payment_method().

Tests verify:
  - T-R022-1..6: unit coverage of _trusteed_resolve_payment_method()
  - T-R022-7..8: integration of paymentMethod into _trusteed_cel_call_layer2() payload

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_r022_payment_method.py \\
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
# Odoo stubs (duplicated from test_enforcement_layer2.py — kept autonomous)
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

    odoo_mod        = types.ModuleType("odoo")
    odoo_api        = types.ModuleType("odoo.api")
    odoo_fields     = types.ModuleType("odoo.fields")
    odoo_models     = types.ModuleType("odoo.models")
    odoo_exceptions = types.ModuleType("odoo.exceptions")
    odoo_http       = types.ModuleType("odoo.http")

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
_ROOT_PKG   = "addon_r022"
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
# Minimal ORM stub helpers
# ---------------------------------------------------------------------------

_BASE_SNAPSHOT = {
    "merchantId":     "merch-r022-test",
    "installationId": "install-r022-001",
    "hmacSecret":     "super-secret-hmac-key-32bytesxx",
}


def _make_order(*, amount_total: float = 100.0, item_count: int = 2):
    """Return a minimal SaleOrderEnforcement instance with stubbed ORM.

    x_trusteed_payment_method is initialised to None so each test starts
    with no payment method set (matches the fail-open default behaviour).

    Additional stubs cover fields accessed inside _trusteed_cel_call_layer2:
    partner_shipping_id, partner_invoice_id, partner_id, coupon_ids.
    """

    class _FakeCurrency:
        name = "EUR"

    class _FakeEnv(dict):
        def __getitem__(self, key):
            return self

        def sudo(self):
            return self

        def get_param(self, key, default=""):
            return "https://api.trusteed.xyz"

        def set_param(self, key, value):
            pass

        @property
        def context(self):
            return {}

    class _FakePartner:
        country_id = None
        is_company = False
        parent_id = None

    order = object.__new__(SaleOrderEnforcement)
    order.id                         = 42
    order.amount_total               = amount_total
    order.currency_id                = _FakeCurrency()
    order.order_line                 = []          # empty avoids product_id attr errors
    order.env                        = _FakeEnv()
    order.x_trusteed_agent_token     = None
    order.x_trusteed_payment_method  = None
    order.partner_id                 = _FakePartner()
    order.partner_shipping_id        = None
    order.partner_invoice_id         = None
    return order


# ---------------------------------------------------------------------------
# Helper: capture the POST body sent by _trusteed_cel_call_layer2
# ---------------------------------------------------------------------------

def _call_layer2_capture_body(order, mock_resp=None, snapshot=None):
    """Call _trusteed_cel_call_layer2 and return (result, captured_body_dict).

    If mock_resp is None a default ALLOW response is used.
    """
    snap = {**_BASE_SNAPSHOT, **(snapshot or {})}
    if mock_resp is None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "decision": "ALLOW",
            "ruleCode": "",
            "reason": "",
            "evaluationId": "eval-r022-capture",
        }

    captured: dict = {}

    def _capture(url, **kwargs):
        body_bytes = kwargs.get("data", b"{}")
        captured["body"] = json.loads(body_bytes)
        return mock_resp

    with patch("requests.post", side_effect=_capture):
        result = order._trusteed_cel_call_layer2(
            "did:web:agent.r022", 80.0, snap, "ev-r022"
        )

    return result, captured.get("body", {})


# ---------------------------------------------------------------------------
# Tests — _trusteed_resolve_payment_method
# ---------------------------------------------------------------------------

class TestResolvePaymentMethod:
    """Unit tests for SaleOrderEnforcement._trusteed_resolve_payment_method()."""

    # T-R022-1: x_trusteed_payment_method has priority
    def test_returns_x_trusteed_payment_method_when_set(self):
        order = _make_order()
        order.x_trusteed_payment_method = "Stripe"
        assert order._trusteed_resolve_payment_method() == "stripe"

    # T-R022-2: normalises to lowercase and strips surrounding whitespace
    def test_normalizes_to_lowercase_and_strips(self):
        order = _make_order()
        order.x_trusteed_payment_method = "  PayPal  "
        assert order._trusteed_resolve_payment_method() == "paypal"

    # T-R022-3: no x_trusteed_payment_method, no transaction_ids → empty string
    def test_returns_empty_when_no_payment_method_set(self):
        order = _make_order()
        order.x_trusteed_payment_method = None
        # Make sure there is no transaction_ids attribute present
        if "transaction_ids" in order.__dict__:
            del order.__dict__["transaction_ids"]
        assert order._trusteed_resolve_payment_method() == ""

    # T-R022-4: falls back to transaction provider.code when x field is absent
    def test_uses_transaction_provider_code_when_x_field_absent(self):
        order = _make_order()
        order.x_trusteed_payment_method = None

        mock_provider = MagicMock()
        mock_provider.code = "stripe"
        mock_provider.name = "Stripe"

        mock_tx = MagicMock()
        mock_tx.state = "done"
        mock_tx.create_date = "2026-05-01"
        mock_tx.provider_id = mock_provider

        # The production code does: .filtered(...).sorted(..., reverse=True)[:1]
        # [:1] on a MagicMock returns another MagicMock (not a list), so
        # done_tx.provider_id works fine as an attribute lookup on MagicMock.
        mock_txs = MagicMock()
        mock_txs.filtered.return_value = mock_txs
        # After [:1] the slice result needs to be truthy and have provider_id.
        mock_slice = MagicMock()
        mock_slice.provider_id = mock_provider
        mock_sorted = MagicMock()
        mock_sorted.__getitem__ = lambda s, k: mock_slice
        mock_sorted.__bool__ = lambda s: True
        mock_txs.sorted.return_value = mock_sorted

        order.transaction_ids = mock_txs

        assert order._trusteed_resolve_payment_method() == "stripe"

    # T-R022-5: transaction_ids attribute absent (payment module not installed) → ""
    def test_returns_empty_when_no_transaction_ids_attr(self):
        order = _make_order()
        order.x_trusteed_payment_method = None
        # Remove the attribute entirely — simulates absence of payment module
        if "transaction_ids" in order.__dict__:
            del order.__dict__["transaction_ids"]
        assert order._trusteed_resolve_payment_method() == ""

    # T-R022-6: x_trusteed_payment_method takes precedence over transaction_ids
    def test_x_field_takes_precedence_over_transaction_ids(self):
        order = _make_order()
        order.x_trusteed_payment_method = "x402"
        order.transaction_ids = MagicMock()  # must never be consulted

        result = order._trusteed_resolve_payment_method()

        assert result == "x402"
        order.transaction_ids.filtered.assert_not_called()

    # Extra: provider.name used when provider.code is absent/empty
    def test_falls_back_to_provider_name_when_code_absent(self):
        order = _make_order()
        order.x_trusteed_payment_method = None

        mock_provider = MagicMock()
        mock_provider.code = None   # code absent
        mock_provider.name = "PayPal"

        mock_tx = MagicMock()
        mock_tx.state = "authorized"
        mock_tx.create_date = "2026-05-01"
        mock_tx.provider_id = mock_provider

        mock_txs = MagicMock()
        mock_txs.filtered.return_value = mock_txs
        mock_slice = MagicMock()
        mock_slice.provider_id = mock_provider
        mock_sorted = MagicMock()
        mock_sorted.__getitem__ = lambda s, k: mock_slice
        mock_sorted.__bool__ = lambda s: True
        mock_txs.sorted.return_value = mock_sorted

        order.transaction_ids = mock_txs

        assert order._trusteed_resolve_payment_method() == "paypal"

    # Extra: transaction_ids exists but filtered() returns empty list → ""
    def test_returns_empty_when_no_done_transactions(self):
        order = _make_order()
        order.x_trusteed_payment_method = None

        mock_txs = MagicMock()
        mock_txs.filtered.return_value = mock_txs
        mock_txs.sorted.return_value = []   # no done/authorized tx
        mock_txs.__len__ = lambda s: 1      # non-empty before filter

        order.transaction_ids = mock_txs

        assert order._trusteed_resolve_payment_method() == ""


# ---------------------------------------------------------------------------
# Tests — paymentMethod in _trusteed_cel_call_layer2 payload
# ---------------------------------------------------------------------------

class TestLayer2IncludesPaymentMethod:
    """Verify paymentMethod presence/absence in the HTTP body sent by layer2."""

    # T-R022-7: paymentMethod included in orderContext when x_trusteed_payment_method is set
    def test_payment_method_in_order_context_when_set(self):
        order = _make_order()
        order.x_trusteed_payment_method = "Stripe"

        _result, body = _call_layer2_capture_body(order)

        order_context = body.get("orderContext", {})
        assert "paymentMethod" in order_context, (
            "orderContext must contain 'paymentMethod' when x_trusteed_payment_method is set"
        )
        assert order_context["paymentMethod"] == "stripe"

    # T-R022-8: paymentMethod absent from orderContext when not set
    def test_payment_method_absent_from_order_context_when_not_set(self):
        order = _make_order()
        order.x_trusteed_payment_method = None
        # No transaction_ids attribute
        if "transaction_ids" in order.__dict__:
            del order.__dict__["transaction_ids"]

        _result, body = _call_layer2_capture_body(order)

        order_context = body.get("orderContext", {})
        assert "paymentMethod" not in order_context, (
            "orderContext must NOT contain 'paymentMethod' when no payment method is resolvable"
        )

    # Extra: paymentMethod from transaction fallback also reaches orderContext
    def test_payment_method_from_transaction_reaches_order_context(self):
        order = _make_order()
        order.x_trusteed_payment_method = None

        mock_provider = MagicMock()
        mock_provider.code = "adyen"
        mock_provider.name = "Adyen"

        mock_tx = MagicMock()
        mock_tx.state = "done"
        mock_tx.create_date = "2026-05-01"
        mock_tx.provider_id = mock_provider

        # Production code: .filtered(...).sorted(..., reverse=True)[:1]
        # [:1] on a MagicMock returns another MagicMock — provider_id works.
        mock_txs = MagicMock()
        mock_txs.filtered.return_value = mock_txs
        mock_slice = MagicMock()
        mock_slice.provider_id = mock_provider
        mock_sorted = MagicMock()
        mock_sorted.__getitem__ = lambda s, k: mock_slice
        mock_sorted.__bool__ = lambda s: True
        mock_txs.sorted.return_value = mock_sorted

        order.transaction_ids = mock_txs

        _result, body = _call_layer2_capture_body(order)

        order_context = body.get("orderContext", {})
        assert order_context.get("paymentMethod") == "adyen", (
            "paymentMethod resolved from transaction_ids must appear in orderContext"
        )
