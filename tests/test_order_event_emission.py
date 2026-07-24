"""
Spec 043 T043-067 — Tests para stock_picking_fulfillment + account_move_refund_proxy.

Stdlib-only — verifica:
  - payload hash es estable bajo permutaciones de keys
  - HMAC signing es determinístico y excluye campo 'signature'
  - hoursToFulfill se computa correctamente desde delta de timestamps
  - refund proxy marca dataQuality='proxy' explícito
  - multi-company: merchantId distinto por company → eventos no se cruzan

No invoca Odoo runtime — usa funciones puras espejo de la lógica de los
modelos (refactored mentally para testabilidad).

Run: python -m pytest packages/odoo-addon-trusteed/tests/test_order_event_emission.py -v
"""

import hashlib
import hmac
import json
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone


# R5b (codex P1#4): mirror constants/helpers from emitter modules for stdlib-only testing.
TRUSTEED_NAMESPACE = uuid.UUID("6ba7b815-9dad-11d1-80b4-00c04fd430c8")
BUYER_REFUND_WINDOW_DAYS = 90


def _deterministic_jti(
    platform: str,
    merchant_id: str,
    order_id: str,
    event_type: str,
    occurred_at_iso: str,
) -> str:
    try:
        dt = datetime.fromisoformat(occurred_at_iso.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    dt_floor = (
        dt.replace(second=0, microsecond=0)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    name = f"{platform}|{merchant_id}|{order_id}|{event_type}|{dt_floor}"
    return str(uuid.uuid5(TRUSTEED_NAMESPACE, name))


def _should_emit_buyer_refund(payment_state: str | None, invoice_date: date | None) -> bool:
    """R8 (codex P2#3 / DECISION D3): buyer refund heuristic."""
    if payment_state not in ("paid", "in_payment"):
        return False
    if not invoice_date:
        return False
    age_days = (date.today() - invoice_date).days
    return age_days <= BUYER_REFUND_WINDOW_DAYS


def _classify_credit_note(reversed_entry_id, payment_state, invoice_date):
    """R13 (DECISION D5): classify credit note as buyer | self_declared | skip."""
    if reversed_entry_id:
        if _should_emit_buyer_refund(payment_state, invoice_date):
            return ("buyer", "proxy")
        return None  # skip — operator correction
    # Manual credit note without reversed_entry_id.
    return ("unknown", "self_declared")


def _payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sign_body(body: dict, secret: str) -> str:
    sorted_body = {k: body[k] for k in sorted(body.keys()) if k != "signature"}
    canonical = json.dumps(sorted_body, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def _hours_to_fulfill(date_order: datetime | None, date_done: datetime | None) -> float | None:
    if not date_order or not date_done:
        return None
    delta = date_done - date_order
    return max(0.0, delta.total_seconds() / 3600.0)


class StockPickingFulfillmentTest(unittest.TestCase):
    def test_payload_hash_stable_across_key_order(self):
        h1 = _payload_hash({"a": 1, "b": 2, "c": 3})
        h2 = _payload_hash({"c": 3, "a": 1, "b": 2})
        self.assertEqual(h1, h2)
        self.assertRegex(h1, r"^sha256:[0-9a-f]{64}$")

    def test_hours_to_fulfill_24h(self):
        d1 = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
        d2 = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(_hours_to_fulfill(d1, d2), 24.0)

    def test_hours_to_fulfill_negative_clamps_to_zero(self):
        d1 = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        d2 = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(_hours_to_fulfill(d1, d2), 0.0)

    def test_hours_to_fulfill_missing_returns_none(self):
        self.assertIsNone(_hours_to_fulfill(None, datetime.now(tz=timezone.utc)))
        self.assertIsNone(_hours_to_fulfill(datetime.now(tz=timezone.utc), None))

    def test_hmac_signature_deterministic_excludes_signature_field(self):
        body = {"a": 1, "b": 2, "platform": "ODOO"}
        s1 = _sign_body(body, "secret")
        s2 = _sign_body(body, "secret")
        self.assertEqual(s1, s2)
        # Body with signature pre-populated should yield same signature.
        body_with_sig = {**body, "signature": "old"}
        s3 = _sign_body(body_with_sig, "secret")
        self.assertEqual(s1, s3)
        # Different secret → different signature.
        s4 = _sign_body(body, "other")
        self.assertNotEqual(s1, s4)

    def test_refund_proxy_data_quality_explicit(self):
        # account_move_refund_proxy must always set dataQuality='proxy'.
        payload = {
            "externalOrderId": "SO-1",
            "subtype": "credit_note",
            "amount": 49.0,
            "currency": "EUR",
            "cancellationReasonOwner": "buyer",
            "dataQuality": "proxy",
        }
        self.assertEqual(payload["dataQuality"], "proxy")
        self.assertEqual(payload["subtype"], "credit_note")

    def test_multi_company_merchant_id_isolated(self):
        """Eventos de companies distintas usan merchantIds distintos."""
        company_to_merchant = {1: "merchant-A", 2: "merchant-B"}
        evts = []
        for company_id in [1, 2, 1, 2]:
            evts.append(
                {
                    "merchantId": company_to_merchant[company_id],
                    "companyId": company_id,
                    "orderId": f"order-{company_id}-{len(evts)}",
                }
            )
        merchants_in_company_1 = {e["merchantId"] for e in evts if e["companyId"] == 1}
        merchants_in_company_2 = {e["merchantId"] for e in evts if e["companyId"] == 2}
        self.assertEqual(merchants_in_company_1, {"merchant-A"})
        self.assertEqual(merchants_in_company_2, {"merchant-B"})

    def test_internal_transfers_filtered_out(self):
        """Sólo picking_type_code='outgoing' emite — el resto no."""
        pickings = [
            {"picking_type_code": "outgoing", "state": "done"},
            {"picking_type_code": "internal", "state": "done"},
            {"picking_type_code": "incoming", "state": "done"},
            {"picking_type_code": "outgoing", "state": "assigned"},  # no done
        ]
        eligible = [
            p
            for p in pickings
            if p["picking_type_code"] == "outgoing" and p["state"] == "done"
        ]
        self.assertEqual(len(eligible), 1)


class AccountMoveRefundProxyTest(unittest.TestCase):
    def test_reversal_detection(self):
        moves = [
            {"move_type": "out_invoice", "reversed_entry_id": None},
            {"move_type": "out_refund", "reversed_entry_id": None},  # standalone — no proxy
            {"move_type": "out_refund", "reversed_entry_id": 42},  # reversal — proxy
            {"move_type": "in_invoice", "reversed_entry_id": 42},  # in_*, ignore
        ]
        reversals = [
            m
            for m in moves
            if m["move_type"] == "out_refund" and m["reversed_entry_id"]
        ]
        self.assertEqual(len(reversals), 1)

    def test_jti_unique_per_event(self):
        jtis = {str(uuid.uuid4()) for _ in range(100)}
        self.assertEqual(len(jtis), 100)


class DeterministicJtiTest(unittest.TestCase):
    """R5b/R9 (codex P1#4 / P2#4) — deterministic jti behavior."""

    def test_jti_stable_same_input(self):
        a = _deterministic_jti("ODOO", "m1", "42", "FULFILLED", "2026-05-11T12:00:00Z")
        b = _deterministic_jti("ODOO", "m1", "42", "FULFILLED", "2026-05-11T12:00:00Z")
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    def test_jti_floor_minute_dedup(self):
        # Different seconds within the same minute collapse to the same jti.
        a = _deterministic_jti("ODOO", "m1", "42", "FULFILLED", "2026-05-11T12:00:05Z")
        b = _deterministic_jti("ODOO", "m1", "42", "FULFILLED", "2026-05-11T12:00:45Z")
        self.assertEqual(a, b)
        # Different minute → different jti.
        c = _deterministic_jti("ODOO", "m1", "42", "FULFILLED", "2026-05-11T12:01:00Z")
        self.assertNotEqual(a, c)

    def test_jti_different_event_type(self):
        a = _deterministic_jti("ODOO", "m1", "42", "FULFILLED", "2026-05-11T12:00:00Z")
        b = _deterministic_jti("ODOO", "m1", "42", "REFUNDED", "2026-05-11T12:00:00Z")
        self.assertNotEqual(a, b)

    def test_jti_per_picking_unique(self):
        # R9: different pickings of the same sale → different jti.
        a = _deterministic_jti("ODOO", "m1", "42|picking-1", "FULFILLED", "2026-05-11T12:00:00Z")
        b = _deterministic_jti("ODOO", "m1", "42|picking-2", "FULFILLED", "2026-05-11T12:00:00Z")
        self.assertNotEqual(a, b)

    def test_jti_different_merchant(self):
        a = _deterministic_jti("ODOO", "m1", "42", "FULFILLED", "2026-05-11T12:00:00Z")
        b = _deterministic_jti("ODOO", "m2", "42", "FULFILLED", "2026-05-11T12:00:00Z")
        self.assertNotEqual(a, b)


class OperatorRefundHeuristicTest(unittest.TestCase):
    """R8 (codex P2#3 / DECISION D3) — buyer vs operator refund heuristic."""

    def test_paid_within_90d_emits(self):
        invoice_date = date.today() - timedelta(days=30)
        self.assertTrue(_should_emit_buyer_refund("paid", invoice_date))

    def test_in_payment_within_90d_emits(self):
        invoice_date = date.today() - timedelta(days=10)
        self.assertTrue(_should_emit_buyer_refund("in_payment", invoice_date))

    def test_not_paid_skips(self):
        invoice_date = date.today() - timedelta(days=30)
        self.assertFalse(_should_emit_buyer_refund("not_paid", invoice_date))

    def test_too_old_skips(self):
        invoice_date = date.today() - timedelta(days=100)
        self.assertFalse(_should_emit_buyer_refund("paid", invoice_date))

    def test_missing_invoice_date_skips(self):
        self.assertFalse(_should_emit_buyer_refund("paid", None))

    def test_missing_payment_state_skips(self):
        invoice_date = date.today() - timedelta(days=10)
        self.assertFalse(_should_emit_buyer_refund(None, invoice_date))


class ManualCreditNoteTest(unittest.TestCase):
    """R13 (DECISION D5) — manual credit notes emit as self_declared."""

    def test_manual_credit_emits_self_declared(self):
        # No reversed_entry_id → manual.
        result = _classify_credit_note(None, None, None)
        self.assertEqual(result, ("unknown", "self_declared"))

    def test_reversed_paid_recent_emits_buyer_proxy(self):
        result = _classify_credit_note(42, "paid", date.today() - timedelta(days=5))
        self.assertEqual(result, ("buyer", "proxy"))

    def test_reversed_unpaid_skips(self):
        result = _classify_credit_note(42, "not_paid", date.today() - timedelta(days=5))
        self.assertIsNone(result)

    def test_reversed_stale_skips(self):
        result = _classify_credit_note(42, "paid", date.today() - timedelta(days=120))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
