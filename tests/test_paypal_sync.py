"""F4.2 — Sincronizar la cuenta PayPal de ``payment_paypal`` (Odoo 18) con la plataforma.

Pruebas de Python puro (sin registro de Odoo) sobre ``utils/paypal_sync.py``:
lector, huella, allowlist, entrega y decisión connect/disconnect/noop.

La integración con el ORM (``models/payment_provider_paypal_sync.py``) NO se
ejecuta aquí: requiere Odoo real.
"""

import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path


def _load(name: str, relpath: str):
    path = Path(__file__).resolve().parent.parent / relpath
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ps = _load("amcp_odoo_paypal_sync", "utils/paypal_sync.py")

# Valores falsos por concatenación (nada con forma de secreto en el fuente).
CLIENT_ID = "AaBb" + "Cc1234567890_-" + "Zz9y"        # 22 chars
SECRET = "EsEc" + "Rr" + "x" * 20 + "_-Q9"            # 28 chars
API = "https://api.trusteed.xyz"


class FakeResp:
    def __init__(self, status=200, body=None, redirect=False):
        self.status_code = status
        self._body = body
        self.is_redirect = redirect

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class Recorder:
    def __init__(self, resp=None, exc=None):
        self.calls = []
        self.resp = resp or FakeResp(200, {"success": True})
        self.exc = exc

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if self.exc:
            raise self.exc
        return self.resp


def snap(**over):
    base = {
        "enabled": True,
        "state": "test",
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "stored_hash": "",
        "merchant_id": "11111111-1111-1111-1111-111111111111",
        "api_base": API,
    }
    base.update(over)
    return base


class TestReadCredentials(unittest.TestCase):
    def test_test_state_is_sandbox(self):
        c = ps.read_credentials(CLIENT_ID, SECRET, "test")
        self.assertEqual(c["mode"], "sandbox")
        self.assertEqual(c["client_id"], CLIENT_ID)

    def test_enabled_state_is_production(self):
        self.assertEqual(ps.read_credentials(CLIENT_ID, SECRET, "enabled")["mode"], "production")

    def test_disabled_gives_none(self):
        self.assertIsNone(ps.read_credentials(CLIENT_ID, SECRET, "disabled"))

    def test_unknown_state_gives_none(self):
        self.assertIsNone(ps.read_credentials(CLIENT_ID, SECRET, "weird"))

    def test_empty_or_false_values_give_none(self):
        # En Odoo un Char vacío se lee como False.
        self.assertIsNone(ps.read_credentials(False, SECRET, "test"))
        self.assertIsNone(ps.read_credentials(CLIENT_ID, False, "test"))
        self.assertIsNone(ps.read_credentials("", "", "test"))

    def test_length_and_charset_bounds(self):
        self.assertIsNone(ps.read_credentials("a" * 19, SECRET, "test"))
        self.assertIsNone(ps.read_credentials("a" * 201, SECRET, "test"))
        self.assertIsNotNone(ps.read_credentials("a" * 20, "b" * 200, "test"))
        self.assertIsNone(ps.read_credentials(CLIENT_ID + " ", SECRET, "test"))
        self.assertIsNone(ps.read_credentials(CLIENT_ID, SECRET + "\n", "test"))
        self.assertIsNone(ps.read_credentials(CLIENT_ID, "sec!ret" + "x" * 20, "test"))
        self.assertIsNone(ps.read_credentials(12345, SECRET, "test"))


class TestFingerprintAndDescribe(unittest.TestCase):
    def test_fingerprint_is_sha256_of_id_secret_mode(self):
        c = ps.read_credentials(CLIENT_ID, SECRET, "test")
        want = hashlib.sha256(f"{CLIENT_ID}|{SECRET}|sandbox".encode()).hexdigest()
        self.assertEqual(ps.fingerprint(c), want)

    def test_fingerprint_changes_with_mode(self):
        a = ps.fingerprint(ps.read_credentials(CLIENT_ID, SECRET, "test"))
        b = ps.fingerprint(ps.read_credentials(CLIENT_ID, SECRET, "enabled"))
        self.assertNotEqual(a, b)

    def test_describe_exposes_only_mode_and_last4(self):
        d = ps.describe(CLIENT_ID, SECRET, "test", True)
        self.assertEqual(d, {"available": True, "enabled": True, "mode": "sandbox", "last4": CLIENT_ID[-4:]})
        self.assertNotIn(SECRET, json.dumps(d))
        self.assertNotIn(CLIENT_ID, json.dumps(d))

    def test_describe_unavailable(self):
        d = ps.describe(False, False, "disabled", False)
        self.assertEqual(d, {"available": False, "enabled": False, "mode": None, "last4": None})


class TestAllowlist(unittest.TestCase):
    def test_allowed_hosts(self):
        for u in (
            "https://api.trusteed.xyz",
            "https://staging-api.trusteed.xyz/",
            "https://agenticmcpstores-production.up.railway.app",
        ):
            self.assertTrue(ps.is_allowed_api_base(u), u)

    def test_rejected_hosts(self):
        for u in (
            "http://api.trusteed.xyz",
            "https://evil.example.com",
            "https://api.trusteed.xyz.evil.com",
            "https://other.trusteed.xyz",
            "https://localhost",
            "https://127.0.0.1",
            "",
            "not a url",
        ):
            self.assertFalse(ps.is_allowed_api_base(u), u)


class TestClient(unittest.TestCase):
    def setUp(self):
        self.creds = ps.read_credentials(CLIENT_ID, SECRET, "test")

    def test_connect_request_shape(self):
        rec = Recorder()
        out = ps.connect(API, "tok-1", self.creds, post=rec)
        self.assertEqual(out, {"ok": True, "code": "connected", "mode": "sandbox"})
        url, kw = rec.calls[0]
        self.assertEqual(url, API + "/v1/embed/merchant/payment-methods/paypal/connect")
        self.assertEqual(kw["headers"]["Authorization"], "Bearer tok-1")
        self.assertEqual(kw["headers"]["X-Embed-Source"], "odoo-embed")
        self.assertEqual(kw["timeout"], (2, 4))
        self.assertIs(kw["allow_redirects"], False)
        self.assertEqual(
            json.loads(kw["data"]),
            {"flow": "apiKey", "accountId": CLIENT_ID, "apiKey": SECRET, "mode": "sandbox", "force": True},
        )

    def test_disallowed_host_sends_nothing(self):
        rec = Recorder()
        out = ps.connect("https://evil.example.com", "tok", self.creds, post=rec)
        self.assertEqual(out, {"ok": False, "code": "api_host_not_allowed"})
        self.assertEqual(rec.calls, [])

    def test_4xx_reflects_only_error_code_never_secret(self):
        rec = Recorder(FakeResp(409, {"error": "paypal_mode_mismatch", "echo": SECRET}))
        out = ps.connect(API, "tok", self.creds, post=rec)
        self.assertEqual(out, {"ok": False, "code": "platform_rejected", "reason": "paypal_mode_mismatch"})
        self.assertNotIn(SECRET, json.dumps(out))

    def test_non_json_error_falls_back_to_http_status(self):
        out = ps.connect(API, "tok", self.creds, post=Recorder(FakeResp(502, None)))
        self.assertEqual(out["reason"], "http_502")

    def test_redirect_is_rejected(self):
        out = ps.connect(API, "tok", self.creds, post=Recorder(FakeResp(302, {}, redirect=True)))
        self.assertEqual(out, {"ok": False, "code": "platform_rejected", "reason": "redirect_rejected"})

    def test_network_error_does_not_leak_message_and_does_not_retry(self):
        import requests

        rec = Recorder(exc=requests.exceptions.ConnectionError("boom " + SECRET))
        out = ps.connect(API, "tok", self.creds, post=rec)
        self.assertEqual(out, {"ok": False, "code": "platform_unreachable"})
        self.assertEqual(len(rec.calls), 1)
        self.assertNotIn(SECRET, json.dumps(out))

    def test_timeout_maps_to_unreachable(self):
        import requests

        out = ps.connect(API, "tok", self.creds, post=Recorder(exc=requests.exceptions.Timeout("t")))
        self.assertEqual(out["code"], "platform_unreachable")

    def test_disconnect_request_shape(self):
        rec = Recorder()
        out = ps.disconnect(API, "tok-2", post=rec)
        self.assertEqual(out, {"ok": True, "code": "disconnected"})
        url, kw = rec.calls[0]
        self.assertEqual(url, API + "/v1/embed/merchant/payment-methods/paypal/disconnect")
        self.assertEqual(kw["headers"]["X-Embed-Source"], "odoo-embed")
        self.assertEqual(kw["timeout"], (2, 4))
        self.assertEqual(json.loads(kw["data"]), {})


class TestRunSync(unittest.TestCase):
    def tok(self, token="tok"):
        calls = []

        def provider():
            calls.append(1)
            return token

        provider.calls = calls
        return provider

    def test_opt_out_is_noop_and_touches_nothing(self):
        rec = Recorder()
        tp = self.tok()
        out = ps.run_sync(snap(enabled=False), tp, post=rec)
        self.assertEqual(out["action"], "noop")
        self.assertEqual(rec.calls, [])
        self.assertEqual(tp.calls, [])

    def test_connect_returns_new_hash_on_success(self):
        rec = Recorder()
        out = ps.run_sync(snap(), self.tok(), post=rec)
        self.assertEqual(out["action"], "connect")
        self.assertTrue(out["ok"])
        self.assertEqual(out["new_hash"], ps.fingerprint(ps.read_credentials(CLIENT_ID, SECRET, "test")))
        self.assertEqual(len(rec.calls), 1)

    def test_same_hash_does_not_resend(self):
        h = ps.fingerprint(ps.read_credentials(CLIENT_ID, SECRET, "test"))
        rec = Recorder()
        tp = self.tok()
        out = ps.run_sync(snap(stored_hash=h), tp, post=rec)
        self.assertEqual(out["action"], "noop")
        self.assertEqual(out["code"], "unchanged")
        self.assertEqual(rec.calls, [])
        self.assertEqual(tp.calls, [])

    def test_mode_change_resends(self):
        h = ps.fingerprint(ps.read_credentials(CLIENT_ID, SECRET, "test"))
        rec = Recorder()
        out = ps.run_sync(snap(state="enabled", stored_hash=h), self.tok(), post=rec)
        self.assertEqual(out["action"], "connect")
        self.assertEqual(json.loads(rec.calls[0][1]["data"])["mode"], "production")

    def test_failure_keeps_old_hash_semantics(self):
        rec = Recorder(FakeResp(409, {"error": "paypal_account_changed"}))
        out = ps.run_sync(snap(), self.tok(), post=rec)
        self.assertFalse(out["ok"])
        self.assertIsNone(out["new_hash"])

    def test_disabled_with_previous_sync_disconnects_and_clears_hash(self):
        rec = Recorder()
        out = ps.run_sync(snap(state="disabled", stored_hash="abc"), self.tok(), post=rec)
        self.assertEqual(out["action"], "disconnect")
        self.assertTrue(out["ok"])
        self.assertEqual(out["new_hash"], "")
        self.assertTrue(rec.calls[0][0].endswith("/paypal/disconnect"))

    def test_emptied_credentials_with_previous_sync_disconnects(self):
        rec = Recorder()
        out = ps.run_sync(snap(client_secret=False, stored_hash="abc"), self.tok(), post=rec)
        self.assertEqual(out["action"], "disconnect")

    def test_disabled_without_previous_sync_is_noop(self):
        # Nunca sincronizamos nada: no hay nada que desconectar.
        rec = Recorder()
        out = ps.run_sync(snap(state="disabled", stored_hash=""), self.tok(), post=rec)
        self.assertEqual(out["action"], "noop")
        self.assertEqual(rec.calls, [])

    def test_failed_disconnect_keeps_hash(self):
        rec = Recorder(FakeResp(500, None))
        out = ps.run_sync(snap(state="disabled", stored_hash="abc"), self.tok(), post=rec)
        self.assertEqual(out["action"], "disconnect")
        self.assertFalse(out["ok"])
        self.assertIsNone(out["new_hash"])

    def test_missing_token_sends_nothing(self):
        rec = Recorder()
        out = ps.run_sync(snap(), lambda: None, post=rec)
        self.assertEqual(out["code"], "token_unavailable")
        self.assertFalse(out["ok"])
        self.assertEqual(rec.calls, [])

    def test_token_provider_exception_is_contained(self):
        def boom():
            raise RuntimeError("x " + SECRET)

        out = ps.run_sync(snap(), boom, post=Recorder())
        self.assertEqual(out["code"], "token_unavailable")
        self.assertNotIn(SECRET, json.dumps(out))

    def test_no_merchant_is_noop(self):
        rec = Recorder()
        out = ps.run_sync(snap(merchant_id=""), self.tok(), post=rec)
        self.assertEqual(out["code"], "no_merchant")
        self.assertEqual(rec.calls, [])

    def test_outcome_never_contains_secret(self):
        for kw in ({}, {"state": "disabled", "stored_hash": "x"}):
            out = ps.run_sync(snap(**kw), self.tok(), post=Recorder())
            self.assertNotIn(SECRET, json.dumps(out))

    def test_triggering_fields_constant(self):
        self.assertEqual(
            set(ps.TRIGGER_FIELDS), {"paypal_client_id", "paypal_client_secret", "state"}
        )


class TestResolveMerchant(unittest.TestCase):
    """H2: sin mapa por compañía no se cae al merchant global (salvo 1 compañía)."""

    def test_explicit_mapping_wins(self):
        self.assertEqual(ps.resolve_merchant_id("m-a", "m-global", 3), "m-a")

    def test_multicompany_without_mapping_has_no_merchant(self):
        self.assertEqual(ps.resolve_merchant_id("", "m-global", 2), "")
        self.assertEqual(ps.resolve_merchant_id(None, "m-global", 2), "")

    def test_single_company_falls_back_to_global(self):
        self.assertEqual(ps.resolve_merchant_id("", "m-global", 1), "m-global")
        self.assertEqual(ps.resolve_merchant_id(None, "m-global", 0), "m-global")

    def test_no_global_no_mapping(self):
        self.assertEqual(ps.resolve_merchant_id("", "", 1), "")


class TestOptInPerCompany(unittest.TestCase):
    def test_company_value_wins(self):
        self.assertTrue(ps.resolve_enabled("1", "", 3))
        self.assertFalse(ps.resolve_enabled("0", "1", 1))

    def test_global_legacy_only_counts_in_single_company(self):
        self.assertTrue(ps.resolve_enabled(None, "1", 1))
        self.assertFalse(ps.resolve_enabled(None, "1", 2))
        self.assertFalse(ps.resolve_enabled(False, False, 1))

    def test_flag_param_values(self):
        self.assertEqual(ps.flag_param(True), "1")
        self.assertEqual(ps.flag_param(False), "0")

    def test_transition(self):
        self.assertEqual(ps.opt_in_transition(False, True), "enable")
        self.assertEqual(ps.opt_in_transition(True, False), "disable")
        self.assertIsNone(ps.opt_in_transition(True, True))
        self.assertIsNone(ps.opt_in_transition(False, False))


class TestSelectProvider(unittest.TestCase):
    """M1: el proveedor leído es el escrito; ambigüedad => no enviar."""

    def p(self, pid, state="test", cid=CLIENT_ID, sec=SECRET):
        return {"id": pid, "state": state, "client_id": cid, "client_secret": sec}

    def test_single_active_provider(self):
        v = ps.select_provider([self.p(1)], (1,))
        self.assertEqual(v["error"], None)
        self.assertEqual(v["client_id"], CLIENT_ID)

    def test_two_active_is_ambiguous(self):
        v = ps.select_provider([self.p(1), self.p(2)], (2,))
        self.assertEqual(v["error"], "ambiguous_provider")
        self.assertFalse(v["client_secret"])

    def test_disabled_extra_does_not_make_ambiguity(self):
        v = ps.select_provider([self.p(1), self.p(2, state="disabled")], (2,))
        self.assertIsNone(v["error"])
        self.assertEqual(v["state"], "test")

    def test_written_provider_is_the_one_read_when_none_active(self):
        v = ps.select_provider([self.p(1, "disabled"), self.p(2, "disabled")], (2,))
        self.assertEqual(v["state"], "disabled")

    def test_no_providers_is_disabled(self):
        v = ps.select_provider([], ())
        self.assertEqual(v["state"], "disabled")
        self.assertIsNone(v["error"])

    def test_view_has_no_extra_keys_with_secret_copy(self):
        v = ps.select_provider([self.p(1)], (1,))
        self.assertEqual(set(v), {"state", "client_id", "client_secret", "error"})


class TestRunSyncHardening(unittest.TestCase):
    def tok(self):
        calls = []

        def provider():
            calls.append(1)
            return "tok"

        provider.calls = calls
        return provider

    def test_provider_error_sends_nothing(self):
        rec = Recorder()
        tp = self.tok()
        out = ps.run_sync(snap(provider_error="ambiguous_provider"), tp, post=rec)
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "ambiguous_provider")
        self.assertIsNone(out["new_hash"])
        self.assertEqual((rec.calls, tp.calls), ([], []))

    def test_conflicting_peer_blocks_connect(self):
        rec = Recorder()
        tp = self.tok()
        out = ps.run_sync(snap(peer_hashes=["f" * 64]), tp, post=rec)
        self.assertEqual(out["code"], "shared_merchant_conflict")
        self.assertFalse(out["ok"])
        self.assertIsNone(out["new_hash"])
        self.assertEqual((rec.calls, tp.calls), ([], []))

    def test_peer_with_same_fingerprint_is_not_conflict(self):
        h = ps.fingerprint(ps.read_credentials(CLIENT_ID, SECRET, "test"))
        out = ps.run_sync(snap(peer_hashes=[h]), self.tok(), post=Recorder())
        self.assertTrue(out["ok"])

    def test_peers_without_hash_are_not_conflict(self):
        out = ps.run_sync(snap(peer_hashes=["", None]), self.tok(), post=Recorder())
        self.assertTrue(out["ok"])

    def test_peer_blocks_disconnect(self):
        rec = Recorder()
        out = ps.run_sync(
            snap(state="disabled", stored_hash="abc", peer_hashes=["f" * 64]),
            self.tok(),
            post=rec,
        )
        self.assertEqual(out["code"], "shared_merchant_conflict")
        self.assertEqual(rec.calls, [])
        self.assertIsNone(out["new_hash"])

    def test_foreign_api_base_does_not_request_token(self):
        rec = Recorder()
        tp = self.tok()
        out = ps.run_sync(snap(api_base="https://evil.example"), tp, post=rec)
        self.assertEqual(out["code"], "api_host_not_allowed")
        self.assertEqual((rec.calls, tp.calls), ([], []))

    def test_http_api_base_does_not_request_token(self):
        tp = self.tok()
        out = ps.run_sync(snap(api_base="http://api.trusteed.xyz"), tp, post=Recorder())
        self.assertEqual(out["code"], "api_host_not_allowed")
        self.assertEqual(tp.calls, [])

    def test_foreign_api_base_on_disconnect_does_not_request_token(self):
        tp = self.tok()
        out = ps.run_sync(
            snap(state="disabled", stored_hash="abc", api_base="https://evil.example"),
            tp,
            post=Recorder(),
        )
        self.assertEqual(out["code"], "api_host_not_allowed")
        self.assertEqual(tp.calls, [])

    def test_reactivation_after_clearing_hash_resends(self):
        # L5: con la huella borrada al desactivar el opt-in, reactivar reenvía.
        rec = Recorder()
        out = ps.run_sync(snap(stored_hash=""), self.tok(), post=rec)
        self.assertEqual(out["action"], "connect")
        self.assertEqual(len(rec.calls), 1)


if __name__ == "__main__":
    unittest.main()
