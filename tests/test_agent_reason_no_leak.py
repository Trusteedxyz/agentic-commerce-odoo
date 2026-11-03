"""El motivo de un bloqueo no filtra política (umbrales, reason crudo, rule_code crudo).

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_agent_reason_no_leak.py \\
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from odoo.exceptions import ValidationError

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("noleak_wiring_harness", _HERE / "test_platform_flow_wiring.py")
_w = importlib.util.module_from_spec(_spec)
sys.modules["noleak_wiring_harness"] = _w
_spec.loader.exec_module(_w)

_ar_spec = importlib.util.spec_from_file_location("noleak_agent_reason", _HERE.parent / "utils" / "agent_reason.py")
_ar = importlib.util.module_from_spec(_ar_spec)
_ar_spec.loader.exec_module(_ar)

_R010 = {"ruleCode": "R010.agent-history", "enabled": True, "mode": "enforce", "appliesTo": "AGENT", "tier": 2, "params": {}}
RAW = "order 18000 > merchant cap 10000"
CATEGORY = "Checkout declined: the order is outside the merchant's accepted limits. Adjust the order and retry."


_clear_caches = _w._clear_caches


class _BlockServer(_w._Server):
    def __init__(self, payload, body, **kw):
        super().__init__(payload, **kw)
        self.body = body

    def post(self, url, data=None, headers=None, timeout=None, allow_redirects=True):
        if url == _w._EVALUATE_URL:
            self.calls.append(("POST", url, dict(headers or {}), None))
            return _w._resp(200, body=self.body)
        return super().post(url, data=data, headers=headers, timeout=timeout, allow_redirects=allow_redirects)


def _block(body):
    server = _BlockServer(_w._payload(rules=[_R010]), {"decision": "BLOCK", "evaluationId": "ev-1", **body})
    with pytest.raises(ValidationError) as got:
        _w._enforce(_w._make_order(token=_w._agent_token()), server)
    return str(got.value)


class TestSanitize:
    def test_keeps_only_the_four_keys_as_strings(self):
        got = _ar.sanitize_agent_reason(
            {"reason_category": "order_limits", "reason": CATEGORY, "next_action": "retry", "rule_code": "R030.simple-controls", "x": "y"}
        )
        assert set(got) == {"reason_category", "reason", "next_action", "rule_code"}

    @pytest.mark.parametrize("bad", [None, "x", [], {"reason": 5}, {"reason": ""}, {"reason": "a" * 401},
                                     {"reason": "hi‮evil"}, {"reason": "hi\x00"}])
    def test_unusable_reason_drops_everything(self, bad):
        assert _ar.sanitize_agent_reason(bad) is None

    @pytest.mark.parametrize("code", ["r030", "R03", "R030.UP", "R030 ", "R" + "0" * 70, 30, "R030\n"])
    def test_bad_rule_code_is_dropped_but_reason_survives(self, code):
        got = _ar.sanitize_agent_reason({"reason": CATEGORY, "rule_code": code})
        assert got is not None and "rule_code" not in got


_HC = {"available": True, "url": "https://shop.example/checkout", "message": "Buy as a person."}
_BI = {"kind": "sku", "value": "SKU-1"}
_PL = {"kind": "max_cart_value", "amount_cents": 12345, "unit": "minor_units_of_store_currency", "source": "merchant"}


class TestNewKeys:
    def _s(self, **extra):
        return _ar.sanitize_agent_reason({"reason": CATEGORY, **extra})

    def test_valid_keys_pass(self):
        got = self._s(human_checkout=_HC, blocked_item=_BI, published_limit=_PL)
        assert got["human_checkout"] == _HC and got["blocked_item"] == _BI and got["published_limit"] == _PL
        units = {"kind": "max_units_per_line", "units": 0, "source": "m"}
        assert self._s(published_limit=units)["published_limit"] == units

    @pytest.mark.parametrize("key,bad", [
        ("human_checkout", {**_HC, "url": "javascript:alert(1)"}),
        ("human_checkout", {**_HC, "message": "a<b"}),
        ("human_checkout", {**_HC, "message": "x &amp; y"}),
        ("human_checkout", {"available": "yes", "message": "m"}),
        ("blocked_item", {"kind": "other", "value": "x"}),
        ("blocked_item", {"kind": "sku", "value": "<b>"}),
        ("blocked_item", {"kind": "sku", "value": "a" * 65}),
        ("published_limit", {**_PL, "amount_cents": -1}),
        ("published_limit", {**_PL, "amount_cents": True}),
        ("published_limit", {"kind": "max_units_per_line", "units": "3", "source": "m"}),
        ("published_limit", "x"),
    ])
    def test_invalid_key_dropped_alone(self, key, bad):
        good = {"human_checkout": _HC, "blocked_item": _BI, "published_limit": _PL}
        got = self._s(**{**good, key: bad})
        assert got is not None and key not in got
        assert all(k in got for k in good if k != key)

    def test_message_keeps_digits_but_reason_filter_unchanged(self):
        assert self._s(human_checkout={**_HC, "message": "Call 24h line"})["human_checkout"]
        assert _ar.sanitize_agent_reason({"reason": "cap 100"}) is None

    def test_no_new_keys_same_message(self):
        ar = {"reason": CATEGORY}
        assert _ar.block_message(ar, "e", "EUR") == _ar.block_message(ar, "e")
        assert " | " not in _ar.block_message(ar, "e")

    def test_message_shows_item_limit_and_link(self):
        msg = _ar.block_message({"reason": CATEGORY, "human_checkout": _HC, "blocked_item": _BI,
                                 "published_limit": _PL}, "e", "EUR")
        assert "SKU-1" in msg and "123.45 EUR" in msg and "https://shop.example/checkout" in msg

    def test_unavailable_human_checkout_shows_message_only(self):
        hc = {"available": False, "message": "Contact the store."}
        msg = _ar.block_message({"reason": CATEGORY, "human_checkout": hc}, "e")
        assert "Contact the store." in msg and "http" not in msg.split("Contact")[1]

    def test_units_limit_text(self):
        msg = _ar.block_message({"reason": CATEGORY, "published_limit":
                                 {"kind": "max_units_per_line", "units": 3, "source": "m"}}, "e")
        assert "max. 3 units per line" in msg


_LE = {"rule": "R038", "rule_code": "R038.max-items-per-order", "rule_name": "Max items", "kind": "max_items_per_order",
       "limit": 5, "observed": 7, "unit": "units", "message": "El pedido tiene 7 articulos y el maximo es 5."}


class TestLimitExceeded:
    def test_valid_replaces_digit_reason_with_message(self):
        got = _ar.sanitize_agent_reason({"reason": "order 7 > 5", "limit_exceeded": _LE})
        assert got["limit_exceeded"] == _LE and got["reason"] == _LE["message"]
        assert _LE["message"] in _ar.block_message({"reason": "order 7 > 5", "limit_exceeded": _LE}, "e")

    @pytest.mark.parametrize("patch", [
        {"rule": "R38"}, {"rule_code": "R038.UP"}, {"rule_code": "R038"}, {"rule_name": ""}, {"kind": "x"},
        {"unit": "eur"}, {"limit": -1}, {"observed": True}, {"limit": "5"}, {"message": ""},
        {"message": "a<b 5"}, {"message": "x" * 401}, {"message": "ok\u202e5"},
    ])
    def test_invalid_field_drops_whole_object_only(self, patch):
        got = _ar.sanitize_agent_reason({"reason": CATEGORY, "blocked_item": _BI, "limit_exceeded": {**_LE, "unit": "units", **patch}})
        assert got["reason"] == CATEGORY and "limit_exceeded" not in got and got["blocked_item"] == _BI

    def test_invalid_with_digit_reason_still_drops_all(self):
        assert _ar.sanitize_agent_reason({"reason": "cap 100", "limit_exceeded": "x"}) is None


class TestLayer2Block:
    def test_raw_reason_and_thresholds_never_reach_the_message(self):
        msg = _block({"ruleCode": "R030.simple-controls", "reason": RAW})
        assert RAW not in msg and "18000" not in msg and "10000" not in msg
        assert "R030" not in msg
        assert _ar.REASON_MERCHANT_POLICY in msg and "evaluationId=ev-1" in msg
        assert msg.startswith("trusteed:")

    def test_agent_reason_phrase_is_used(self):
        msg = _block({"ruleCode": "R030.simple-controls", "reason": RAW,
                      "agent_reason": {"reason_category": "order_limits", "reason": CATEGORY, "next_action": "x"}})
        assert CATEGORY in msg and RAW not in msg and "18000" not in msg
        assert "R030" not in msg

    def test_publishable_rule_code_from_agent_reason_is_shown(self):
        msg = _block({"ruleCode": "R030.simple-controls", "reason": RAW,
                      "agent_reason": {"reason": CATEGORY, "rule_code": "R034.sku-blocklist"}})
        assert "trusteed:R034.sku-blocklist" in msg and CATEGORY in msg

    def test_malicious_agent_reason_falls_back_to_generic(self):
        msg = _block({"ruleCode": "R030", "reason": RAW, "agent_reason": {"reason": "x‮" + RAW}})
        assert RAW not in msg and _ar.REASON_MERCHANT_POLICY in msg

    def test_no_digits_in_generic_message_apart_from_eval_id(self):
        msg = _block({"ruleCode": "R030", "reason": RAW})
        assert not re.search(r"\d", msg.split("evaluationId=")[0])


class TestOfflineValve:
    def _cap_block(self, order_kwargs=None):
        server = _w._Server(_w._payload(mode="permissive"), evaluate="down")
        with pytest.raises(ValidationError) as got:
            _w._enforce(_w._make_order(token=None, amount_total=180.0), server)
        return str(got.value)

    def test_valve_message_is_generic_and_has_no_rule_code_or_numbers(self):
        msg = self._cap_block()
        assert _ar.REASON_MERCHANT_POLICY in msg and "R030" not in msg and not re.search(r"\d", msg.split("evaluationId=")[0])
        assert "evaluationId=" in msg and msg.startswith("trusteed:")

    def test_item_count_never_leaks(self):
        rule = {"ruleCode": "R018.cart-composition-guard", "enabled": True, "mode": "enforce", "appliesTo": "ALL",
                "params": {"maxItemCount": -1}}
        server = _w._Server(_w._payload(mode="permissive", rules=[rule, _R010]), evaluate="down")
        order = _w._make_order(token=None)
        with pytest.raises(ValidationError) as got:
            _w._enforce(order, server)
        assert "item count" not in str(got.value) and "R018" not in str(got.value)


class TestTimeout:
    def test_timeout_uses_checks_unavailable_phrase(self):
        server = _w._Server(_w._payload(mode="strict"), evaluate="down")
        with pytest.raises(ValidationError) as got:
            _w._enforce(_w._make_order(token=_w._agent_token()), server)
        assert "CEL-TIMEOUT" in str(got.value) and _ar.REASON_CHECKS_UNAVAILABLE in str(got.value)


@pytest.mark.parametrize(
    "reason",
    ["Limit of 5000 items", "Limit \u0665 items", "Remove <b>items</b>", "Items > limit",
     "Remove &lt;items", "a\u200bb", "a\u061cb", "a\u202eb"],
)
def test_reason_outside_contract_drops_whole_agent_reason(reason):
    assert _ar.sanitize_agent_reason({"reason": reason, "rule_code": "R012", "next_action": "x"}) is None


def test_contract_sentence_passes():
    s = "Checkout declined: remove the restricted items and retry. Nothing was charged."
    assert _ar.sanitize_agent_reason({"reason": s})["reason"] == s
