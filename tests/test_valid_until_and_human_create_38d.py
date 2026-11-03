"""Cierre Odoo de la auditoría (Task 38d).

1. ``validUntil`` ausente o ilegible ⇒ el snapshot es MALFORMADO ⇒ «no
   disponible» (como caducado; NO «manipulado»), también en la caché propia. Un
   snapshot firmado por nuestro servidor siempre lo lleva.
2. El ``create`` headless de una PERSONA (sin token) directamente en ``sale``
   recibe la válvula offline (reglas ALL, tope R030) que ``action_confirm`` ya
   le aplica: sin capa 2, sin bloquear por avería. El agente sigue como antes.
3. La cabecera JWS ``crit`` invalida un token de agente (RFC 7515 §4.1.11).

Ejecutan el flujo real con el arnés de ``test_platform_flow_wiring``; sólo se
simulan la red y el ORM. Lo que depende del ORM de Odoo real queda sin verificar.

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_valid_until_and_human_create_38d.py \\
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from odoo.exceptions import ValidationError

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("t38d_wiring_harness", _HERE / "test_platform_flow_wiring.py")
_w = importlib.util.module_from_spec(_spec)
sys.modules["t38d_wiring_harness"] = _w
_spec.loader.exec_module(_w)

_snapshot = _w._snapshot_mod
_verifier = _w._verifier_mod
_enforcement = _w._enforcement_mod
_valve = sys.modules[f"{_w._MODELS_PKG}.headless_human_valve"]
_API = _w._API


@pytest.fixture(autouse=True)
def _clear_caches():
    caches = (_snapshot._SNAPSHOT_CACHE, _snapshot._JWKS_CACHE, _snapshot._JWKS_EXPIRE,
              _snapshot._NEGATIVE_RETRY, _verifier._NONCE_CACHE)
    for cache in caches:
        cache.clear()
    yield
    for cache in caches:
        cache.clear()


# ── (1) validUntil ausente o ilegible ⇒ malformado ⇒ no disponible ─────────

_MISSING = object()
_UNREADABLE = [None, "", "   ", "not-a-date", "2020-13-45T00:00:00Z", "2099-01-01T25:61:61Z", "99999-01-01",
               12345, 1.5, 1e30, -1, True, False, ["2099-01-01T00:00:00Z"], {"a": 1}, "1e30", "4102444800"]


def _jws_with_valid_until(value):
    payload = _w._payload()
    if value is _MISSING:
        payload.pop("validUntil", None)
    else:
        payload["validUntil"] = value
    return _w._sign_jws(payload, _w._SNAP_KEY)


def _verify_plain(jws):
    server = _w._Server(_w._payload())
    with patch("requests.get", side_effect=server.get):
        return _snapshot.verify_jws_snapshot_result(jws, _API)


class TestSnapshotWithoutAReadableValidUntil:
    def test_a_missing_valid_until_is_unavailable_not_tampered(self):
        assert _verify_plain(_jws_with_valid_until(_MISSING)) == ("unavailable", None)

    @pytest.mark.parametrize("value", _UNREADABLE, ids=repr)
    def test_an_unreadable_valid_until_is_unavailable_not_tampered(self, value):
        assert _verify_plain(_jws_with_valid_until(value)) == ("unavailable", None)

    def test_the_problem_is_named(self):
        assert _snapshot._valid_until_problem({}) == "missing"
        assert _snapshot._valid_until_problem({"validUntil": None}) == "missing"
        assert _snapshot._valid_until_problem({"validUntil": "nope"}) == "unreadable"
        assert _snapshot._valid_until_problem({"validUntil": 12345}) == "unreadable"
        assert _snapshot._valid_until_problem({"validUntil": "2020-01-01T00:00:00Z"}) == "expired"
        assert _snapshot._valid_until_problem({"validUntil": "2099-01-01T00:00:00Z"}) is None

    def test_the_compat_predicate_agrees(self):
        assert _snapshot._snapshot_is_stale({}) is True
        assert _snapshot._snapshot_is_stale({"validUntil": "garbage"}) is True
        assert _snapshot._snapshot_is_stale({"validUntil": "2099-01-01T00:00:00Z"}) is False

    def test_a_timezone_less_date_is_read_as_utc(self):
        assert _verify_plain(_jws_with_valid_until("2099-01-01T00:00:00"))[0] == "ok"
        assert _verify_plain(_jws_with_valid_until("2020-01-01T00:00:00")) == ("unavailable", None)

    @pytest.mark.parametrize("value", ["2099-01-01T00:00:00Z", "2099-01-01T00:00:00.000Z", "2099-01-01T01:00:00+01:00"])
    def test_a_readable_future_date_is_ok(self, value):
        assert _verify_plain(_jws_with_valid_until(value))[0] == "ok"

    def test_a_malformed_snapshot_served_by_the_network_is_never_cached(self):
        for value in (_MISSING, "garbage", 12345):
            payload = _w._payload()
            if value is _MISSING:
                payload.pop("validUntil")
            else:
                payload["validUntil"] = value
            server = _w._Server(payload)
            with patch("requests.get", side_effect=server.get):
                result = _snapshot.pull_snapshot_result(_API, _w._MERCHANT, _w._INSTALLATION, _w._HMAC, 1)
            assert result == ("unavailable", None)
            assert _snapshot._SNAPSHOT_CACHE == {}

    @pytest.mark.parametrize("value", [_MISSING, "garbage", None])
    def test_a_malformed_copy_in_our_own_cache_is_discarded_and_refetched(self, value):
        _snapshot._SNAPSHOT_CACHE[(_w._MERCHANT, 1)] = (_jws_with_valid_until(value), time.time() + 300)
        server = _w._Server(_w._payload())
        with patch("requests.get", side_effect=server.get):
            status, payload = _snapshot.pull_snapshot_result(_API, _w._MERCHANT, _w._INSTALLATION, _w._HMAC, 1)
        assert status == "ok" and payload["validUntil"] == _w._FUTURE_VALID_UNTIL
        assert len(server.gets(_w._SNAP_URL)) == 1

    def test_a_malformed_cached_copy_is_not_served_when_the_refetch_fails(self):
        _snapshot._SNAPSHOT_CACHE[(_w._MERCHANT, 1)] = (_jws_with_valid_until(_MISSING), time.time() + 300)
        server = _w._Server(_w._payload(), snapshot="down")
        with patch("requests.get", side_effect=server.get):
            result = _snapshot.pull_snapshot_result(_API, _w._MERCHANT, _w._INSTALLATION, _w._HMAC, 1)
        assert result == ("unavailable", None)

    def test_the_flow_holds_an_agent_and_lets_a_person_through(self):
        payload = _w._payload(mode="strict")
        payload.pop("validUntil")
        with pytest.raises(ValidationError, match="R000-SNAPSHOT-UNAVAILABLE"):
            _w._enforce(_w._make_order(token=_w._agent_token()), _w._Server(payload))
        server = _w._Server(payload)
        _w._enforce(_w._make_order(token=None, amount_total=9999.0), server)
        assert server.posts(_w._EVALUATE_URL) == []

    def test_the_flow_never_names_it_a_signature_failure(self):
        payload = _w._payload(mode="strict")
        payload["validUntil"] = "garbage"
        with pytest.raises(ValidationError) as got:
            _w._enforce(_w._make_order(token=_w._agent_token()), _w._Server(payload))
        assert "SNAPSHOT-INVALID" not in str(got.value)


# ── (2) create headless de una PERSONA en 'sale' ───────────────────────────

_HUMAN_VALS = {"company_id": 1, "state": "sale"}


def _created(*, amount=180.0, state="sale", token=None, company_id=1, context=None):
    """El modelo sobre el que se llama a ``create``; ``self`` hace de recordset creado."""
    model = _w._make_order(token=token, amount_total=amount, company_id=company_id, context=context)
    model.state = state
    return model


def _create(model, server, vals=None):
    g, p = _w._net(server)
    with g, p:
        result = model.create([dict(vals or _HUMAN_VALS)])
    assert server.unexpected == []
    return result


class TestHeadlessCreateOfAPersonGetsTheOfflineValve:
    def test_a_person_over_the_r030_cap_is_blocked(self):
        server = _w._Server(_w._payload(mode="permissive"))
        with pytest.raises(ValidationError, match="merchant_policy"):
            _create(_created(amount=180.0), server)

    def test_the_valve_never_calls_layer2_nor_the_nonce_service(self):
        server = _w._Server(_w._payload())
        with pytest.raises(ValidationError, match="merchant_policy"):
            _create(_created(amount=180.0), server)
        assert server.posts(_w._EVALUATE_URL) == []
        assert server.posts(_w._NONCE_URL) == []

    def test_a_person_under_the_cap_passes_and_the_order_is_created(self):
        server = _w._Server(_w._payload())
        model = _created(amount=50.0)
        assert _create(model, server) is model
        assert len(model.base_creates) == 1
        assert server.posts(_w._EVALUATE_URL) == []

    def test_the_message_names_the_rule_and_the_evaluation(self):
        with pytest.raises(ValidationError) as got:
            _create(_created(amount=180.0), _w._Server(_w._payload()))
        assert "trusteed:merchant_policy" in str(got.value) and "evaluationId=" in str(got.value)

    @pytest.mark.parametrize("snapshot", ["down", "http500", "tampered"])
    def test_a_missing_or_tampered_snapshot_lets_the_person_through(self, snapshot):
        server = _w._Server(_w._payload(mode="strict"), snapshot=snapshot)
        _create(_created(amount=9999.0), server)

    @pytest.mark.parametrize("valid_until", ["2020-01-01T00:00:00.000Z", "garbage", None])
    def test_an_expired_or_malformed_snapshot_lets_the_person_through(self, valid_until):
        payload = _w._payload(mode="strict")
        if valid_until is None:
            payload.pop("validUntil")
        else:
            payload["validUntil"] = valid_until
        _create(_created(amount=9999.0), _w._Server(payload))

    def test_cel_disabled_for_the_company_lets_the_person_through(self):
        model = _created(amount=9999.0)
        model.env.map = _w._CompanyMap(_w._mapping("strict", cel_enabled=False))
        _create(model, _w._Server(_w._payload()))

    def test_an_unconfigured_addon_lets_the_person_through(self):
        model = _w._make_order(amount_total=9999.0, configured=False)
        model.state = "sale"
        _create(model, _w._Server(_w._payload()))

    def test_agent_only_rules_do_not_block_a_person(self):
        payload = _w._payload(rules=[_w._R030_CAP_100, _w._R029_ESTRICTO])
        _create(_created(amount=50.0), _w._Server(payload))

    def test_a_non_confirmed_order_is_not_evaluated(self):
        server = _w._Server(_w._payload())
        _create(_created(amount=9999.0, state="draft"), server, {"company_id": 1, "state": "draft"})
        assert server.calls == []

    def test_a_record_without_a_determinable_company_passes(self):
        _create(_created(amount=9999.0, company_id=None), _w._Server(_w._payload()))

    def test_it_is_the_company_of_the_created_record_that_decides(self):
        model = _created(amount=180.0, company_id=2)
        asked = []
        original = _w._Order._trusteed_cel_snapshot

        def _spy(self_, eval_id, company_id):
            asked.append(company_id)
            return original(self_, eval_id, company_id)

        with patch.object(_w._Order, "_trusteed_cel_snapshot", _spy):
            _create(model, _w._Server(_w._payload(rules=[])), {"company_id": 2, "state": "sale"})
        assert 2 in asked and 1 not in asked[1:]

    def test_a_single_company_install_keeps_working(self):
        _create(_created(amount=50.0), _w._Server(_w._payload()))

    def test_a_hostile_context_cannot_choose_the_company(self):
        ctx = {"allowed_company_ids": [1], "default_company_id": 1, "force_company": 1}
        model = _created(amount=180.0, company_id=2, context=ctx)
        seen = []
        original = _w._Order._trusteed_cel_snapshot

        def _spy(self_, eval_id, company_id):
            seen.append(company_id)
            return original(self_, eval_id, company_id)

        with patch.object(_w._Order, "_trusteed_cel_snapshot", _spy):
            _create(model, _w._Server(_w._payload(rules=[])), {"company_id": 2, "state": "sale"})
        assert seen[-1] == 2

    def test_an_internal_error_lets_the_person_through(self):
        model = _created(amount=9999.0)
        with patch.object(_w._Order, "_trusteed_offline_context", side_effect=RuntimeError("bug")):
            _create(model, _w._Server(_w._payload()))

    def test_an_internal_error_in_the_snapshot_read_lets_the_person_through(self):
        model = _created(amount=9999.0)
        with patch.object(_w._Order, "_trusteed_cel_snapshot", side_effect=RuntimeError("bug")):
            with patch.object(_w._Order, "_trusteed_cel_enforce_vals_unguarded"):
                _create(model, _w._Server(_w._payload()))

    def test_a_db_error_is_not_swallowed(self):
        class _Db(Exception):
            pass

        model = _created(amount=9999.0)
        with patch.object(_enforcement, "_db_error_types", return_value=(_Db,)):
            with patch.object(_w._Order, "_trusteed_offline_context", side_effect=_Db("serialization")):
                with pytest.raises(_Db):
                    _create(model, _w._Server(_w._payload()))

    def test_every_created_record_is_checked(self):
        first = _created(amount=50.0)
        second = _created(amount=500.0)

        class _Many(list):
            env = first.env

        many = _Many([first, second])
        with patch.object(_w._OrmBase, "create", lambda self_, vals_list: many):
            g, p = _w._net(_w._Server(_w._payload()))
            with g, p, pytest.raises(ValidationError, match="merchant_policy"):
                first.create([dict(_HUMAN_VALS), dict(_HUMAN_VALS)])

    def test_the_context_token_makes_it_an_agent_not_a_person(self):
        """Con token en el contexto se rechaza antes (el agente no pasa por la válvula)."""
        model = _created(amount=50.0, context={"trusteed_agent_token": _w._agent_token()})
        with pytest.raises(ValidationError, match="CEL-HEADLESS-CONFIRM-REQUIRED"):
            _create(model, _w._Server(_w._payload()))
        assert model.base_creates == []

    def test_the_valve_skips_a_record_that_carries_a_token(self):
        model = _created(amount=9999.0, token="a.b.c")
        calls = []
        with patch.object(_w._Order, "_trusteed_cel_snapshot", lambda *a, **k: calls.append(a) or (None, None, None)):
            _valve.enforce_created_human_orders([model], _enforcement._company_id_of, _enforcement._reraise_if_db_error)
        assert calls == []


class TestAgentsKeepTheirCurrentPath:
    def test_an_agent_in_sale_is_still_rejected_before_creating(self):
        model = _created(amount=50.0)
        vals = {"company_id": 1, "state": "sale", "x_trusteed_agent_token": _w._agent_token()}
        with pytest.raises(ValidationError, match="CEL-HEADLESS-CONFIRM-REQUIRED"):
            _create(model, _w._Server(_w._payload()), vals)
        assert model.base_creates == []

    def test_an_agent_without_company_is_still_held(self):
        model = _created(amount=50.0)
        vals = {"state": "sale", "x_trusteed_agent_token": _w._agent_token()}
        with pytest.raises(ValidationError, match="R000-COMPANY-UNRESOLVED"):
            _create(model, _w._Server(_w._payload()), vals)

    def test_an_agent_by_default_context_is_still_rejected(self):
        model = _created(amount=50.0, context={"default_x_trusteed_agent_token": _w._agent_token()})
        with pytest.raises(ValidationError, match="CEL-HEADLESS-CONFIRM-REQUIRED"):
            _create(model, _w._Server(_w._payload()), {"company_id": 1, "state": "sale"})

    def test_the_kill_switch_still_stops_an_agent_create_but_not_a_person(self):
        payload = _w._payload(kill_switch=True)
        vals = {"company_id": 1, "state": "sale", "x_trusteed_agent_token": _w._agent_token()}
        with pytest.raises(ValidationError, match="kill_switch"):
            _create(_created(amount=50.0), _w._Server(payload), vals)
        _create(_created(amount=50.0), _w._Server(payload))

    def test_action_confirm_of_a_person_is_unchanged(self):
        server = _w._Server(_w._payload(), evaluate="down")
        order = _w._make_order(token=None, amount_total=180.0)
        g, p = _w._net(server)
        with g, p, pytest.raises(ValidationError, match="merchant_policy"):
            order.action_confirm()


# ── (3) cabecera `crit` en el token de agente ──────────────────────────────


def _token_with_header(extra, *, drop=()):
    _w._TOKEN_SEQ[0] += 1
    now = int(time.time())
    canonical = json.dumps({"orderId": "7", "merchantId": _w._MERCHANT, "amount": "50.0"}, sort_keys=True)
    payload = {
        "aud": "trusteed", "merchantId": _w._MERCHANT, "iss": _w._AGENT_DID, "iat": now, "exp": now + 120,
        "checkoutIntentHash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "nonce": f"nonce-38d-{_w._TOKEN_SEQ[0]:08d}-abcdef", "jti": f"jti-38d-{_w._TOKEN_SEQ[0]:08d}-abcdef",
    }
    header = {"alg": "EdDSA", "kid": f"{_w._AGENT_DID}#key-1", "typ": "trusteed-agent-token+jwt", **extra}
    for key in drop:
        header.pop(key, None)
    h = _w._b64u(json.dumps(header, separators=(",", ":")).encode())
    p = _w._b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = _w._AGENT_KEY.sign(f"{h}.{p}".encode("ascii"))
    return f"{h}.{p}.{_w._b64u(sig)}", payload


def _verify_token(token):
    canonical = json.dumps({"orderId": "7", "merchantId": _w._MERCHANT, "amount": "50.0"}, sort_keys=True)
    return _verifier.verify_agent_token(
        token, hashlib.sha256(canonical.encode("utf-8")).hexdigest(), _w._MERCHANT,
        _enforcement._build_did_resolver(_w._payload()),
    )


class TestAgentTokenCritHeader:
    def test_control_the_same_token_without_crit_verifies(self):
        token, _ = _token_with_header({})
        assert _verify_token(token)["agentId"] == _w._AGENT_DID

    @pytest.mark.parametrize("crit", [["exp"], [], ["b64"], ["unknown-ext"], "exp", None, {}, 0, False],
                             ids=repr)
    def test_a_validly_signed_token_with_crit_is_invalid(self, crit):
        token, _ = _token_with_header({"crit": crit})
        assert _verify_token(token) is None

    def test_crit_is_rejected_before_the_key_is_resolved(self):
        token, _ = _token_with_header({"crit": ["exp"]})
        canonical = "x"
        assert _verifier.verify_agent_token(token, canonical, _w._MERCHANT, {}) is None

    def test_a_rejected_crit_token_does_not_burn_its_nonce(self):
        token, payload = _token_with_header({"crit": ["exp"]})
        _verify_token(token)
        assert _verifier._NONCE_CACHE == {}

    def test_the_flow_holds_the_agent_with_r001(self):
        token, _ = _token_with_header({"crit": []})
        server = _w._Server(_w._payload(mode="permissive"))
        with pytest.raises(ValidationError, match="R001"):
            _w._enforce(_w._make_order(token=token), server)
        assert server.posts(_w._EVALUATE_URL) == []
        assert server.posts(_w._NONCE_URL) == []

    def test_the_flow_without_crit_still_allows(self):
        token, _ = _token_with_header({})
        _w._enforce(_w._make_order(token=token), _w._Server(_w._payload(mode="permissive")))
