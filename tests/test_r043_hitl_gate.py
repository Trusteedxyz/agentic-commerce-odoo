"""Spec-048 Sprint E.2 T-E45 — R043 HITL Gate (Odoo).

Pure-Python tests — no Odoo registry required.
"""

import importlib.util
import sys
import unittest
from pathlib import Path


def _load_module(name: str, relpath: str):
    mod_path = Path(__file__).resolve().parent.parent / relpath
    spec = importlib.util.spec_from_file_location(name, str(mod_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_gate = _load_module("amcp_odoo_r043_hitl_gate", "utils/r043_hitl_gate.py")

HITL_PENDING_STATE = _gate.HITL_PENDING_STATE
META_EVALUATION_ID = _gate.META_EVALUATION_ID
META_HITL_PENDING = _gate.META_HITL_PENDING
META_REASON = _gate.META_REASON
META_RULE_CODE = _gate.META_RULE_CODE
R043_REASON_PREFIX = _gate.R043_REASON_PREFIX
build_freeze_payload = _gate.build_freeze_payload
is_hitl_response = _gate.is_hitl_response
requires_freeze = _gate.requires_freeze
rule_code_from = _gate.rule_code_from


class TestR043HitlGate(unittest.TestCase):
    def test_detects_canonical_hitl_response(self):
        resp = {
            "decision": "BLOCK",
            "ucp": {
                "state": "requires_escalation",
                "reason_code": "trusteed:R043.agent-checkout-approval-required",
            },
        }
        self.assertTrue(is_hitl_response(resp))

    def test_rejects_plain_block(self):
        resp = {
            "decision": "BLOCK",
            "ucp": {"state": "failed", "reason_code": "trusteed:R001"},
        }
        self.assertFalse(is_hitl_response(resp))

    def test_rejects_allow_with_escalation(self):
        resp = {
            "decision": "ALLOW",
            "ucp": {"state": "requires_escalation", "reason_code": "trusteed:R043"},
        }
        self.assertFalse(is_hitl_response(resp))

    def test_rejects_other_rule_codes(self):
        resp = {
            "decision": "BLOCK",
            "ucp": {"state": "requires_escalation", "reason_code": "trusteed:R031"},
        }
        self.assertFalse(is_hitl_response(resp))

    def test_handles_missing_ucp(self):
        self.assertFalse(is_hitl_response({"decision": "BLOCK"}))

    def test_handles_none(self):
        self.assertFalse(is_hitl_response(None))
        self.assertFalse(is_hitl_response("not-a-dict"))

    def test_rule_code_from_strips_prefix(self):
        resp = {"ucp": {"reason_code": "trusteed:R043.agent-checkout-approval-required"}}
        self.assertEqual(
            "R043.agent-checkout-approval-required", rule_code_from(resp)
        )

    def test_rule_code_from_returns_empty_on_missing_prefix(self):
        resp = {"ucp": {"reason_code": "R043.no-prefix"}}
        self.assertEqual("", rule_code_from(resp))

    def test_build_freeze_payload_marks_state(self):
        resp = {
            "decision": "BLOCK",
            "reason": "agent checkout requires merchant approval",
            "evaluationId": "eval-1",
            "ucp": {
                "state": "requires_escalation",
                "reason_code": "trusteed:R043.agent-checkout-approval-required",
            },
        }
        payload = build_freeze_payload(resp)
        self.assertTrue(payload["freeze"])
        self.assertEqual(HITL_PENDING_STATE, payload["state"])
        self.assertEqual(
            "R043.agent-checkout-approval-required", payload[META_RULE_CODE]
        )
        self.assertEqual("agent checkout requires merchant approval", payload[META_REASON])
        self.assertEqual("eval-1", payload[META_EVALUATION_ID])
        self.assertTrue(payload[META_HITL_PENDING])
        self.assertTrue(requires_freeze(payload))

    def test_build_freeze_payload_no_freeze_for_allow(self):
        payload = build_freeze_payload({"decision": "ALLOW"})
        self.assertFalse(payload["freeze"])
        self.assertIsNone(payload["state"])
        self.assertFalse(requires_freeze(payload))

    def test_reason_prefix_constant(self):
        self.assertEqual("trusteed:R043", R043_REASON_PREFIX)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
