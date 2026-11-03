"""Endurecimiento del flujo de snapshot y del guardián de errores (Task 29a).

- L1: un token con REPLAY viaja al servidor como agente anónimo (sin DID ni
  score), en todos los modos; en ``strict`` el REPLAY sigue bloqueando (R002).
- L3: caché negativa (~60 s, reloj falso) del reintento forzado del JWKS.
- L6a: ``validUntil`` vencido ⇒ snapshot no disponible (anti-rollback).
- L6b: tope del cuerpo HTTP del snapshot (streaming, sin cargarlo entero).
- L6c: cabecera JWS ``crit`` rechazada (RFC 7515 §4.1.11).
- L6d: un error de base de datos (``psycopg2.Error``, ``ConcurrencyError``) no
  se convierte en veredicto: se relanza para que el ORM reintente.

Estos tests EJECUTAN el flujo real con el arnés de ``test_platform_flow_wiring``
(``_trusteed_cel_enforce``, ``EnforcementSnapshotService``, ``pull_snapshot_result``,
``verify_jws_snapshot_result``, ``verify_agent_token``, ``consume_nonce``); sólo
se simulan la red y el ORM. Lo que depende del ORM/psycopg2 reales queda sin
verificar (``psycopg2`` no está instalado aquí: se inyecta un módulo mínimo).

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_snapshot_hardening_l1_l6.py \\
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import base64
import importlib.util
import json
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from odoo.exceptions import ValidationError

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("l1l6_wiring_harness", _HERE / "test_platform_flow_wiring.py")
_w = importlib.util.module_from_spec(_spec)
sys.modules["l1l6_wiring_harness"] = _w
_spec.loader.exec_module(_w)

_snapshot = _w._snapshot_mod
_verifier = _w._verifier_mod
_enforcement = _w._enforcement_mod
_API = _w._API
_AGENT_DID = _w._AGENT_DID


@pytest.fixture(autouse=True)
def _clear_caches():
    caches = (_snapshot._SNAPSHOT_CACHE, _snapshot._JWKS_CACHE, _snapshot._JWKS_EXPIRE,
              _snapshot._NEGATIVE_RETRY, _verifier._NONCE_CACHE)
    for cache in caches:
        cache.clear()
    yield
    for cache in caches:
        cache.clear()


def _evaluate_body(server):
    return server.posts(_w._EVALUATE_URL)[0][3]


# ── L1: un token reutilizado viaja como agente anónimo ─────────────────────


class TestReplayedTokenTravelsAnonymous:
    @pytest.mark.parametrize("mode", ["balanced", "permissive"])
    def test_replay_sends_no_did_and_no_score(self, mode):
        server = _w._Server(_w._payload(mode=mode), nonce=409)
        order = _w._make_order(token=_w._agent_token())
        _w._enforce(order, server)
        body = _evaluate_body(server)
        assert body["agentId"] is None
        assert body["orderContext"]["agentTrustScore"] is None
        assert body["endpointClass"] == "agentic_endpoint"
        assert body["orderContext"]["cartAttributes"]["_agent_token_replay"] == "true"
        assert _w._did_writes(order) == []

    def test_replay_in_strict_still_blocks_with_r002_and_sends_nothing(self):
        server = _w._Server(_w._payload(mode="strict"), nonce=409)
        with pytest.raises(ValidationError, match="R002"):
            _w._enforce(_w._make_order(token=_w._agent_token()), server)
        assert server.posts(_w._EVALUATE_URL) == []

    def test_replay_through_action_confirm_is_anonymous_too(self):
        server = _w._Server(_w._payload(mode="balanced"), nonce=409)
        order = _w._make_order(token=_w._agent_token())
        g, p = _w._net(server)
        with g, p:
            order.action_confirm()
        assert _evaluate_body(server)["agentId"] is None
        assert _w._did_writes(order) == []

    def test_an_accepted_token_still_sends_its_did(self):
        server = _w._Server(_w._payload(mode="balanced"), nonce=200)
        order = _w._make_order(token=_w._agent_token())
        _w._enforce(order, server)
        assert _evaluate_body(server)["agentId"] == _AGENT_DID
        assert len(_w._did_writes(order)) == 1

    def test_indeterminate_nonce_keeps_the_verified_identity_as_in_magento_observe(self):
        # Decisión abierta (informe): con el nonce-store caído el token SÍ verificó
        # su firma; sólo se marca ``_agent_token_nonce_unavailable`` y no se atribuye.
        server = _w._Server(_w._payload(mode="balanced"), nonce=503)
        order = _w._make_order(token=_w._agent_token())
        _w._enforce(order, server)
        body = _evaluate_body(server)
        assert body["agentId"] == _AGENT_DID
        assert body["orderContext"]["cartAttributes"]["_agent_token_nonce_unavailable"] == "true"
        assert _w._did_writes(order) == []

    def test_a_forged_token_is_still_rejected_before_anything_is_sent(self):
        server = _w._Server(_w._payload(mode="balanced"))
        forged = _w._agent_token(key=_w._FORGER_KEY)
        with pytest.raises(ValidationError, match="R001"):
            _w._enforce(_w._make_order(token=forged), server)
        assert server.posts(_w._EVALUATE_URL) == []


# ── L3: caché negativa del reintento forzado del JWKS ──────────────────────


class _Clock:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def time(self):
        return self.now


def _foreign_signed_jws(payload=None):
    """JWS bien formado con el ``kid`` publicado pero firmado por OTRA clave."""
    return _w._sign_jws(payload or _w._payload(), _w._FORGER_KEY)


def _prime_jwks_cache():
    _snapshot._JWKS_CACHE[_API] = {_w._SNAP_KID: _w._raw_pub(_w._SNAP_KEY)}
    _snapshot._JWKS_EXPIRE[_API] = time.time() + 3600


class TestNegativeCacheOfTheForcedJwksRetry:
    def _verify(self, server, jws, clock):
        with patch.object(_snapshot, "time", clock), patch("requests.get", side_effect=server.get):
            return _snapshot.verify_jws_snapshot_result(jws, _API)

    def test_a_tampered_snapshot_does_not_redownload_the_jwks_within_the_window(self):
        clock = _Clock(time.time())
        _prime_jwks_cache()
        _snapshot._JWKS_EXPIRE[_API] = clock.now + 3600
        server = _w._Server(_w._payload())
        jws = _foreign_signed_jws()
        assert self._verify(server, jws, clock) == ("tampered", None)
        assert len(server.gets(_w._JWKS_URL)) == 1, "el primer reintento sí descarga"
        for _ in range(5):
            clock.now += 5
            assert self._verify(server, jws, clock) == ("tampered", None)
        assert len(server.gets(_w._JWKS_URL)) == 1, "dentro de los 60 s no hay más descargas"

    def test_after_the_window_the_retry_downloads_again(self):
        clock = _Clock(time.time())
        _prime_jwks_cache()
        _snapshot._JWKS_EXPIRE[_API] = clock.now + 7200
        server = _w._Server(_w._payload())
        jws = _foreign_signed_jws()
        self._verify(server, jws, clock)
        clock.now += 59.9
        self._verify(server, jws, clock)
        assert len(server.gets(_w._JWKS_URL)) == 1
        clock.now += 0.2
        self._verify(server, jws, clock)
        assert len(server.gets(_w._JWKS_URL)) == 2

    def test_the_skipped_retry_does_not_extend_the_window(self):
        clock = _Clock(time.time())
        _prime_jwks_cache()
        _snapshot._JWKS_EXPIRE[_API] = clock.now + 7200
        server = _w._Server(_w._payload())
        jws = _foreign_signed_jws()
        self._verify(server, jws, clock)
        for _ in range(4):
            clock.now += 20
            self._verify(server, jws, clock)
        # 80 s desde el primero: la ventana (60 s) ya venció aunque hubo hits.
        assert len(server.gets(_w._JWKS_URL)) == 2

    def test_jwks_down_is_unavailable_and_the_retry_is_not_repeated(self):
        clock = _Clock(time.time())
        server = _w._Server(_w._payload(), jwks="down")
        jws = _w._sign_jws(_w._payload(), _w._SNAP_KEY)
        assert self._verify(server, jws, clock) == ("unavailable", None)
        first = len(server.gets(_w._JWKS_URL))
        assert first == 2, "descarga normal + reintento forzado"
        clock.now += 10
        assert self._verify(server, jws, clock) == ("unavailable", None)
        # Sin caché JWKS la descarga normal se repite; el reintento forzado no.
        assert len(server.gets(_w._JWKS_URL)) == first + 1

    def test_a_different_kid_is_not_blocked_by_anothers_window(self):
        clock = _Clock(time.time())
        _prime_jwks_cache()
        _snapshot._JWKS_EXPIRE[_API] = clock.now + 7200
        server = _w._Server(_w._payload())
        self._verify(server, _foreign_signed_jws(), clock)
        other = _w._sign_jws(_w._payload(), _w._FORGER_KEY, kid="another-kid-example")
        self._verify(server, other, clock)
        assert len(server.gets(_w._JWKS_URL)) == 2

    def test_a_successful_retry_after_rotation_leaves_no_negative_entry(self):
        clock = _Clock(time.time())
        _prime_jwks_cache()
        _snapshot._JWKS_EXPIRE[_API] = clock.now + 7200
        server = _w._Server(_w._payload(), snapshot="rotated", jwks_key=_w._ROTATED_SNAP_KEY)
        status, payload = self._verify(server, server._jws(), clock)
        assert status == "ok" and payload is not None
        assert _snapshot._NEGATIVE_RETRY == {}

    def test_the_registry_is_bounded_and_thread_safe(self):
        errors = []

        def worker(n):
            try:
                for i in range(200):
                    _snapshot._remember_failed_retry(_API, f"kid-{n}-{i}")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(_snapshot._NEGATIVE_RETRY) <= _snapshot._NEGATIVE_RETRY_MAX

    def test_expired_entries_are_dropped_before_live_ones(self):
        clock = _Clock(time.time())
        with patch.object(_snapshot, "time", clock):
            for i in range(_snapshot._NEGATIVE_RETRY_MAX):
                _snapshot._remember_failed_retry(_API, f"old-{i}")
            clock.now += _snapshot._NEGATIVE_RETRY_TTL_SECONDS + 1
            _snapshot._remember_failed_retry(_API, "fresh")
        assert list(_snapshot._NEGATIVE_RETRY) == [(_API, "fresh")]

    def test_the_full_flow_with_a_persistent_tampered_snapshot_downloads_once_per_window(self):
        _prime_jwks_cache()
        server = _w._Server(_w._payload(), snapshot="tampered")
        for _ in range(4):
            order = _w._make_order(token=_w._agent_token())
            with pytest.raises(ValidationError, match="R000-SNAPSHOT-INVALID"):
                _w._enforce(order, server)
        assert len(server.gets(_w._JWKS_URL)) == 1


# ── L6a: validUntil vencido ⇒ no disponible ────────────────────────────────


def _jws_with_valid_until(value, *, drop=False):
    payload = _w._payload()
    if drop:
        payload.pop("validUntil", None)
    else:
        payload["validUntil"] = value
    return _w._sign_jws(payload, _w._SNAP_KEY), payload


def _verify_plain(jws):
    server = _w._Server(_w._payload())
    with patch("requests.get", side_effect=server.get):
        return _snapshot.verify_jws_snapshot_result(jws, _API)


class TestSnapshotValidUntil:
    def test_an_expired_snapshot_is_unavailable_not_tampered(self):
        jws, _ = _jws_with_valid_until("2020-01-01T00:00:00.000Z")
        assert _verify_plain(jws) == ("unavailable", None)

    def test_a_future_snapshot_is_ok(self):
        jws, _ = _jws_with_valid_until("2099-01-01T00:00:00.000Z")
        status, payload = _verify_plain(jws)
        assert status == "ok" and payload["validUntil"] == "2099-01-01T00:00:00.000Z"

    # Task 38d: sustituye a ``test_absent_or_unreadable_valid_until_is_not_stale``
    # y ``test_a_missing_valid_until_is_not_stale``, que fijaban «ausente o
    # ilegible no vence». Un snapshot firmado por nuestro servidor SIEMPRE trae
    # ``validUntil``: ver ``tests/test_valid_until_and_human_create_38d.py``.

    def test_a_naive_timestamp_is_read_as_utc(self):
        assert _snapshot._snapshot_is_stale({"validUntil": "2020-01-01T00:00:00"}) is True
        assert _snapshot._snapshot_is_stale({"validUntil": "2099-01-01T00:00:00"}) is False

    def test_the_boundary_instant_is_stale(self):
        clock = _Clock(1_800_000_000.0)  # 2027-01-15T08:00:00Z
        with patch.object(_snapshot, "time", clock):
            assert _snapshot._snapshot_is_stale({"validUntil": "2027-01-15T08:00:00.000Z"}) is True
            assert _snapshot._snapshot_is_stale({"validUntil": "2027-01-15T08:00:01.000Z"}) is False

    def test_a_rolled_back_snapshot_served_by_the_network_is_never_cached(self):
        server = _w._Server(_w._payload())
        server.payload = {**_w._payload(), "validUntil": "2020-01-01T00:00:00.000Z"}
        g, _p = _w._net(server)
        with g:
            status, payload = _snapshot.pull_snapshot_result(
                _API, _w._MERCHANT, _w._INSTALLATION, _w._HMAC, 1
            )
        assert (status, payload) == ("unavailable", None)
        assert _snapshot._SNAPSHOT_CACHE == {}

    def test_a_cached_snapshot_past_its_valid_until_is_refetched(self):
        stale_jws, _ = _jws_with_valid_until("2020-01-01T00:00:00.000Z")
        _snapshot._SNAPSHOT_CACHE[(_w._MERCHANT, 1)] = (stale_jws, time.time() + 300)
        server = _w._Server(_w._payload())
        g, _p = _w._net(server)
        with g:
            status, payload = _snapshot.pull_snapshot_result(
                _API, _w._MERCHANT, _w._INSTALLATION, _w._HMAC, 1
            )
        assert status == "ok" and payload["validUntil"] == _w._FUTURE_VALID_UNTIL
        assert len(server.gets(_w._SNAP_URL)) == 1

    def test_the_flow_holds_an_agent_and_lets_a_person_through_on_a_stale_snapshot(self):
        payload = {**_w._payload(mode="strict"), "validUntil": "2020-01-01T00:00:00.000Z"}
        server = _w._Server(payload)
        with pytest.raises(ValidationError, match="R000-SNAPSHOT-UNAVAILABLE"):
            _w._enforce(_w._make_order(token=_w._agent_token()), server)
        _w._enforce(_w._make_order(token=None, amount_total=9999.0), _w._Server(payload))


# ── L6b: tope del cuerpo HTTP ──────────────────────────────────────────────


class _BigResponse:
    """Respuesta 200 en streaming; cuenta lo que se le lee."""

    def __init__(self, *, content_length=None, chunk=b"A" * 65536, total=None):
        self.status_code = 200
        self.headers = {} if content_length is None else {"Content-Length": str(content_length)}
        self._chunk = chunk
        self._total = total
        self.served = 0
        self.closed = False
        self.iterated = False

    def iter_content(self, chunk_size=None):
        self.iterated = True
        while self._total is None or self.served < self._total:
            self.served += len(self._chunk)
            yield self._chunk

    def close(self):
        self.closed = True

    @property
    def text(self):  # pragma: no cover - leerlo entero es justo lo que se prohíbe
        raise AssertionError("el cuerpo no puede leerse entero con .text")


def _pull_with(resp, *, cached_jws=None):
    if cached_jws:
        _snapshot._SNAPSHOT_CACHE[(_w._MERCHANT, 1)] = (cached_jws, time.time() - 1)
    seen = {}

    def _get(url, headers=None, timeout=None, verify=None, allow_redirects=None, stream=None):
        seen["stream"] = stream
        seen["allow_redirects"] = allow_redirects
        return resp

    with patch("requests.get", side_effect=_get):
        result = _snapshot.pull_snapshot_result(_API, _w._MERCHANT, _w._INSTALLATION, _w._HMAC, 1)
    return result, seen


class TestSnapshotBodyCap:
    def test_the_cap_is_four_mib_and_covers_the_worst_legit_jws(self):
        assert _snapshot.MAX_SNAPSHOT_BODY_BYTES == 4 * 1024 * 1024
        worst_legit = (2 * 1024 * 1024 * 4 + 2) // 3 + 400  # base64 de 2 MiB + cabecera/firma
        assert worst_legit < _snapshot.MAX_SNAPSHOT_BODY_BYTES

    def test_the_request_is_streamed_and_still_refuses_redirects(self):
        (_status, _payload), seen = _pull_with(_BigResponse(content_length=10, total=0))
        assert seen == {"stream": True, "allow_redirects": False}

    def test_a_declared_length_over_the_cap_is_unavailable_without_reading(self):
        resp = _BigResponse(content_length=_snapshot.MAX_SNAPSHOT_BODY_BYTES + 1, total=None)
        (status, payload), _ = _pull_with(resp)
        assert (status, payload) == ("unavailable", None)
        assert resp.iterated is False and resp.closed is True

    def test_a_lying_or_chunked_body_is_cut_at_the_cap(self):
        resp = _BigResponse(content_length=None, total=3 * _snapshot.MAX_SNAPSHOT_BODY_BYTES)  # sin Content-Length
        (status, payload), _ = _pull_with(resp)
        assert (status, payload) == ("unavailable", None)
        assert resp.closed is True
        assert resp.served <= _snapshot.MAX_SNAPSHOT_BODY_BYTES + 65536, "no se lee más allá del tope + un trozo"

    def test_a_small_length_that_lies_is_still_cut_by_the_byte_count(self):
        resp = _BigResponse(content_length=100, total=3 * _snapshot.MAX_SNAPSHOT_BODY_BYTES)
        (status, _payload), _ = _pull_with(resp)
        assert status == "unavailable"
        assert resp.served <= _snapshot.MAX_SNAPSHOT_BODY_BYTES + 65536

    def test_over_the_cap_falls_back_to_the_last_known_good_copy(self):
        good = _w._sign_jws(_w._payload(), _w._SNAP_KEY)
        _prime_jwks_cache()
        resp = _BigResponse(content_length=_snapshot.MAX_SNAPSHOT_BODY_BYTES + 1)
        (status, payload), _ = _pull_with(resp, cached_jws=good)
        assert status == "ok" and payload["merchantId"] == _w._MERCHANT

    def test_over_the_cap_without_a_copy_holds_the_agent(self):
        server = _w._Server(_w._payload(mode="balanced"))
        big = _BigResponse(content_length=_snapshot.MAX_SNAPSHOT_BODY_BYTES + 1)

        def _get(url, **kw):
            return big if url == _w._SNAP_URL else server.get(url, **kw)

        with patch("requests.get", side_effect=_get), patch("requests.post", side_effect=server.post):
            with pytest.raises(ValidationError, match="R000-SNAPSHOT-UNAVAILABLE"):
                _w._make_order(token=_w._agent_token())._trusteed_cel_enforce()

    def test_read_body_capped_boundaries(self):
        cap = 1000

        def resp_of(data):
            return SimpleNamespace(headers={}, iter_content=lambda chunk_size=None: iter([data[:400], data[400:]]),
                                   close=lambda: None)

        assert _snapshot.read_body_capped(resp_of(b"x" * cap), cap) == "x" * cap
        assert _snapshot.read_body_capped(resp_of(b"x" * (cap + 1)), cap) is None

    def test_a_normal_snapshot_still_flows(self):
        server = _w._Server(_w._payload())
        _w._enforce(_w._make_order(token=_w._agent_token()), server)
        assert len(server.posts(_w._EVALUATE_URL)) == 1


# ── L6c: cabecera crit ─────────────────────────────────────────────────────


def _jws_with_header(extra, key=None):
    key = key or _w._SNAP_KEY
    header = {"alg": "EdDSA", "kid": _w._SNAP_KID, **extra}
    h = _w._b64u(json.dumps(header, separators=(",", ":")).encode())
    p = _w._b64u(json.dumps(_w._payload(), separators=(",", ":")).encode())
    sig = key.sign(f"{h}.{p}".encode("ascii"))
    return f"{h}.{p}.{_w._b64u(sig)}"


class TestCritHeader:
    @pytest.mark.parametrize("crit", [["exp"], [], ["b64"], "exp", None])
    def test_a_validly_signed_jws_with_crit_is_rejected_without_touching_the_network(self, crit):
        server = _w._Server(_w._payload())
        with patch("requests.get", side_effect=server.get):
            result = _snapshot.verify_jws_snapshot_result(_jws_with_header({"crit": crit, "exp": 1}), _API)
        assert result == ("tampered", None)
        assert server.calls == []

    def test_the_same_jws_without_crit_verifies(self):
        server = _w._Server(_w._payload())
        with patch("requests.get", side_effect=server.get):
            status, _ = _snapshot.verify_jws_snapshot_result(_jws_with_header({}), _API)
        assert status == "ok"

    def test_the_flow_holds_the_agent_as_an_invalid_signature(self):
        server = _w._Server(_w._payload(mode="permissive"))
        jws = _jws_with_header({"crit": ["exp"], "exp": 1})

        def _get(url, **kw):
            if url == _w._SNAP_URL:
                return _w._resp(200, text=jws)
            return server.get(url, **kw)

        with patch("requests.get", side_effect=_get), patch("requests.post", side_effect=server.post):
            with pytest.raises(ValidationError, match="R000-SNAPSHOT-INVALID"):
                _w._make_order(token=_w._agent_token())._trusteed_cel_enforce()

    def test_a_non_ascii_jws_is_tampered_not_a_crash(self):
        jws = _w._sign_jws(_w._payload(), _w._SNAP_KEY) + "ñ"
        assert _verify_plain(jws) == ("tampered", None)


# ── L6d: errores de base de datos ──────────────────────────────────────────


class _FakePsycopg2Error(Exception):
    pass


class _SerializationFailure(_FakePsycopg2Error):
    """Como ``psycopg2.errors.SerializationFailure`` (subclase de ``psycopg2.Error``)."""


class _ConcurrencyError(Exception):
    pass


@pytest.fixture
def db_errors(monkeypatch):
    fake = types.ModuleType("psycopg2")
    fake.Error = _FakePsycopg2Error
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setattr(sys.modules["odoo.exceptions"], "ConcurrencyError", _ConcurrencyError, raising=False)
    return fake


def _order_whose_snapshot_read_raises(exc, *, token):
    order = _w._make_order(token=token)
    order.env.svc.get_snapshot_result = lambda company_id=None: (_ for _ in ()).throw(exc)
    return order


class TestDatabaseErrorsAreNotVerdicts:
    @pytest.mark.parametrize("exc", [_FakePsycopg2Error("db"), _SerializationFailure("could not serialize access"),
                                     _ConcurrencyError("concurrent update")])
    @pytest.mark.parametrize("token", [None, "a.b.c"])
    def test_a_db_error_reading_the_snapshot_propagates_for_a_person_and_an_agent(self, db_errors, exc, token):
        order = _order_whose_snapshot_read_raises(exc, token=token)
        with pytest.raises(type(exc)) as got:
            order._trusteed_cel_enforce()
        assert got.value is exc

    @pytest.mark.parametrize("exc", [_FakePsycopg2Error("db"), _SerializationFailure("x"), _ConcurrencyError("c")])
    def test_a_db_error_in_the_headless_guard_propagates(self, db_errors, exc):
        order = _w._make_order()
        with patch.object(_enforcement.SaleOrderEnforcement, "_trusteed_cel_enforce_vals_unguarded",
                          side_effect=exc):
            with pytest.raises(type(exc)):
                order._trusteed_cel_enforce_vals({"company_id": 1, "state": "sale", "x_trusteed_agent_token": "a.b.c"})

    @pytest.mark.parametrize("exc", [_FakePsycopg2Error("db"), _SerializationFailure("x"), _ConcurrencyError("c")])
    def test_a_db_error_inside_the_unguarded_evaluation_propagates(self, db_errors, exc):
        order = _w._make_order(token=_w._agent_token())
        server = _w._Server(_w._payload())
        g, p = _w._net(server)
        with g, p, patch.object(_verifier, "verify_agent_token", side_effect=exc):
            with pytest.raises(type(exc)):
                order._trusteed_cel_enforce()

    def test_a_db_error_writing_the_verified_did_propagates(self, db_errors):
        order = _w._make_order(token=_w._agent_token())
        server = _w._Server(_w._payload())

        def _write(self_, vals):
            if "x_trusteed_agent_did" in vals:
                raise _SerializationFailure("could not serialize access")
            return True

        g, p = _w._net(server)
        with g, p, patch.object(_w._OrmBase, "write", _write):
            with pytest.raises(_SerializationFailure):
                order._trusteed_cel_enforce()

    def test_the_did_write_still_fails_open_on_a_non_db_error(self, db_errors):
        order = _w._make_order(token=_w._agent_token())
        server = _w._Server(_w._payload())

        def _write(self_, vals):
            raise RuntimeError("not a db error")

        g, p = _w._net(server)
        with g, p, patch.object(_w._OrmBase, "write", _write):
            order._trusteed_cel_enforce()  # R023 falla abierto: no lanza

    def test_our_own_exception_still_holds_an_agent_and_lets_a_person_through(self, db_errors):
        agent = _order_whose_snapshot_read_raises(RuntimeError("bug"), token="a.b.c")
        # el snapshot devuelve «no disponible» ⇒ agente con token retenido
        with pytest.raises(ValidationError, match="R000-SNAPSHOT-UNAVAILABLE"):
            agent._trusteed_cel_enforce()
        person = _w._make_order(token=None)
        with patch.object(_enforcement.SaleOrderEnforcement, "_trusteed_cel_enforce_unguarded",
                          side_effect=RuntimeError("bug")):
            person._trusteed_cel_enforce()
        agent2 = _w._make_order(token="a.b.c")
        with patch.object(_enforcement.SaleOrderEnforcement, "_trusteed_cel_enforce_unguarded",
                          side_effect=RuntimeError("bug")):
            with pytest.raises(ValidationError, match="CEL-INTERNAL-ERROR"):
                agent2._trusteed_cel_enforce()

    def test_a_validation_error_is_still_the_verdict(self, db_errors):
        server = _w._Server(_w._payload(mode="strict"), nonce=409)
        with pytest.raises(ValidationError, match="R002"):
            _w._enforce(_w._make_order(token=_w._agent_token()), server)

    def test_without_psycopg2_nor_concurrency_error_nothing_is_reraised(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "psycopg2", None)  # ``import psycopg2`` ⇒ ImportError
        monkeypatch.delattr(sys.modules["odoo.exceptions"], "ConcurrencyError", raising=False)
        assert _enforcement._db_error_types() == ()
        person = _w._make_order(token=None)
        with patch.object(_enforcement.SaleOrderEnforcement, "_trusteed_cel_enforce_unguarded",
                          side_effect=RuntimeError("bug")):
            person._trusteed_cel_enforce()

    def test_the_types_are_psycopg2_error_and_concurrency_error(self, db_errors):
        assert set(_enforcement._db_error_types()) == {_FakePsycopg2Error, _ConcurrencyError}
