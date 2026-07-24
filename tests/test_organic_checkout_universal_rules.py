"""pytest tests — App Store remediation (2026-07-11).

R030 (and every other merchant-wide "appliesTo:ALL" policy rule:
R014/R018/R019/R020/R025/R027) must apply to a normal human (non-agentic)
Odoo sale order, not just agentic ones.

Root cause fixed in SaleOrderEnforcement._trusteed_cel_enforce(): the method
used to call `_apply_fallback()` + `return` immediately whenever no agent
token was resolvable, BEFORE ever reaching the tier2/Layer-2 dispatch — so a
merchant's universal safety-valve rules never applied to a real sale order.

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_organic_checkout_universal_rules.py \\
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ADDON_ROOT = Path(__file__).parent.parent
_ROOT_PKG = "addon_organic"
_MODELS_PKG = f"{_ROOT_PKG}.models"
_UTILS_PKG = f"{_ROOT_PKG}.utils"


def _ensure_pkg(key: str) -> types.ModuleType:
    if key not in sys.modules:
        sys.modules[key] = types.ModuleType(key)
    return sys.modules[key]


def _load_module(rel: str, key: str, pkg: str) -> types.ModuleType:
    if key in sys.modules:
        return sys.modules[key]
    _ensure_pkg(pkg)
    spec = importlib.util.spec_from_file_location(key, _ADDON_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
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

_offline_valve_mod = _load_module(
    "models/offline_safety_valve_evaluator.py",
    f"{_MODELS_PKG}.offline_safety_valve_evaluator",
    _MODELS_PKG,
)

_enforcement_mod = _load_module(
    "models/sale_order_enforcement.py",
    f"{_MODELS_PKG}.sale_order_enforcement",
    _MODELS_PKG,
)

SaleOrderEnforcement = _enforcement_mod.SaleOrderEnforcement

_SNAPSHOT_WITH_R030 = {
    "merchantId": "merch-odoo-organic-test",
    "installationId": "install-odoo-organic-001",
    "hmacSecret": "super-secret-hmac-key-32bytesxx",
    "killSwitch": False,
    "fallbackMode": "balanced",
    "rules": [
        {"ruleCode": "R030", "enabled": True, "params": {"maxAmountCents": 10000}},
    ],
    "agentDidResolver": {},
}


class _FakeCurrency:
    name = "EUR"


class _FakeEnvContext(dict):
    """Minimal stand-in for Odoo's env.context (plain dict with .get)."""


class _FakeEnv(dict):
    def __init__(self, snapshot):
        super().__init__()
        self._snapshot = snapshot
        self.context = _FakeEnvContext()

    def __getitem__(self, key):
        return self

    def sudo(self):
        return self

    def get_param(self, key, default=""):
        return "https://api.trusteed.xyz"

    def get_snapshot(self):
        return self._snapshot


def _make_order(*, token=None, snapshot=None, amount_total=180.0, item_count=1):
    """Return a minimal SaleOrderEnforcement instance with stubbed ORM."""
    order = object.__new__(SaleOrderEnforcement)
    order.id = 42
    order.amount_total = amount_total
    order.currency_id = _FakeCurrency()
    order.order_line = [object()] * item_count
    order.env = _FakeEnv(snapshot if snapshot is not None else _SNAPSHOT_WITH_R030)
    order.x_trusteed_agent_token = token
    order.partner_shipping_id = None
    order.partner_invoice_id = None

    def _with_context(**kwargs):
        return order

    def _write(vals):
        return True

    order.with_context = _with_context
    order.sudo = lambda: order
    order.write = _write
    return order


class TestOrganicCheckoutReachesLayer2:
    """SaleOrderEnforcement._trusteed_cel_enforce() must call Layer-2 for a
    sale order with NO agent token, so merchant-wide policy rules (R030 etc.)
    actually get a chance to fire."""

    def test_no_token_still_calls_layer2_with_null_agent_id(self):
        order = _make_order(token=None)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"decision": "ALLOW"}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            order._trusteed_cel_enforce()

        assert mock_post.called, (
            "Layer-2 /v1/rules/evaluate must be called even without an agent "
            "token — otherwise R030/R014/R018/R019/R020/R025/R027 never apply "
            "to a normal (organic) sale order."
        )

        import json as _json

        sent_body = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1].get("data")
        payload = _json.loads(sent_body)
        assert payload["agentId"] is None, (
            "agentId must be JSON null (not 'unknown' or any placeholder string) "
            "for an organic checkout — the shared evaluator's AgentDidSchema "
            "would otherwise reject a non-DID string."
        )

    def test_no_token_block_decision_still_raises(self):
        """The whole point of the fix: a BLOCK from Layer-2 (e.g. R030 cap
        breached) must actually abort the sale order even with no agent."""
        order = _make_order(token=None, amount_total=180.0)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "decision": "BLOCK",
            "ruleCode": "R030.simple-controls",
            "reason": "cart total 18000 > merchant max 10000",
            "evaluationId": "eval-organic-test",
        }

        from odoo.exceptions import ValidationError

        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(ValidationError, match="R030"):
                order._trusteed_cel_enforce()

    def test_layer2_network_failure_falls_back_to_offline_safety_valve_block(self):
        """App Store remediation follow-up (2026-07-11/12): when Layer-2 is
        unreachable (network error), the offline safety-valve evaluator must
        still catch an R030 cap breach instead of silently applying the
        blunt balanced fallback (which would ALLOW)."""
        order = _make_order(token=None, amount_total=180.0)

        with patch("requests.post", side_effect=ConnectionError("boom")):
            from odoo.exceptions import ValidationError

            with pytest.raises(ValidationError, match="R030"):
                order._trusteed_cel_enforce()

    def test_layer2_network_failure_with_balanced_mode_and_no_breach_allows(self):
        """Offline evaluator finds nothing wrong (cart under cap) → falls
        through to the existing balanced-mode ALLOW, no exception raised."""
        order = _make_order(token=None, amount_total=50.0)

        with patch("requests.post", side_effect=ConnectionError("boom")):
            order._trusteed_cel_enforce()  # must not raise

    def test_no_token_no_tier2_rules_enabled_does_not_call_layer2(self):
        """No tier-2 rule enabled at all → nothing to evaluate, Layer-2 must
        not be called (keeps the no-op fast path for merchants with zero
        universal rules configured)."""
        empty_snapshot = {**_SNAPSHOT_WITH_R030, "rules": []}
        order = _make_order(token=None, snapshot=empty_snapshot)

        with patch("requests.post") as mock_post:
            order._trusteed_cel_enforce()

        assert not mock_post.called

    def test_kill_switch_still_blocks_with_no_token(self):
        killswitch_snapshot = {**_SNAPSHOT_WITH_R030, "killSwitch": True}
        order = _make_order(token=None, snapshot=killswitch_snapshot)

        from odoo.exceptions import ValidationError

        with pytest.raises(ValidationError, match="kill_switch"):
            order._trusteed_cel_enforce()
