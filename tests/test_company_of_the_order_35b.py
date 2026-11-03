"""La configuración que gobierna un pedido es la de SU compañía (Task 35b, M3).

Un cliente RPC elige con ``allowed_company_ids`` (contexto de ``execute_kw``) cuál
es ``env.company``; antes de esta tarea esa compañía decidía el modo de fallo, las
credenciales y el snapshot de un pedido que pertenece a OTRA. Ahora:

- ``action_confirm``/``write``: la compañía es ``order.company_id``;
- ``create`` headless: la compañía sale de ``vals['company_id']`` (entero);
- sin compañía determinable: un agente (token presente) se retiene con
  ``R000-COMPANY-UNRESOLVED`` (avería, no una regla); una persona pasa (D1).

Estos tests EJECUTAN el flujo real con el arnés de ``test_platform_flow_wiring``
(dos compañías: A = ``env.company`` en ``balanced``; B = la del pedido en
``strict``); sólo se simulan la red y el ORM. Lo que depende del ORM de Odoo real
(``with_company``, ``company_id`` por defecto, validación de ``allowed_company_ids``
y reglas de acceso multicompañía) queda sin verificar.

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_company_of_the_order_35b.py \\
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
_spec = importlib.util.spec_from_file_location("c35b_wiring_harness", _HERE / "test_platform_flow_wiring.py")
_w = importlib.util.module_from_spec(_spec)
sys.modules["c35b_wiring_harness"] = _w
_spec.loader.exec_module(_w)

_snapshot = _w._snapshot_mod
_enforcement = _w._enforcement_mod
_API = _w._API

_MERCH_A, _INST_A, _HMAC_A = "merchant-company-a", "inst-company-a", "example-hmac-a-not-real-0000"
_MERCH_B, _INST_B, _HMAC_B = "merchant-company-b", "inst-company-b", "example-hmac-b-not-real-0000"
_COMPANY_A, _COMPANY_B = 1, 2

# Contextos con los que un cliente RPC intenta cambiar la compañía evaluada.
_HOSTILE_CONTEXTS = [
    {},
    {"allowed_company_ids": [_COMPANY_A]},
    {"allowed_company_ids": [_COMPANY_A, _COMPANY_B]},
    {"allowed_company_ids": [_COMPANY_B, _COMPANY_A]},
    {"force_company": _COMPANY_A, "company_id": _COMPANY_A, "default_company_id": _COMPANY_A},
]


@pytest.fixture(autouse=True)
def _clear_caches():
    caches = (_snapshot._SNAPSHOT_CACHE, _snapshot._JWKS_CACHE, _snapshot._JWKS_EXPIRE,
              _snapshot._NEGATIVE_RETRY, _w._verifier_mod._NONCE_CACHE)
    for cache in caches:
        cache.clear()
    yield
    for cache in caches:
        cache.clear()


def _snap_url(merchant):
    return f"{_API}/v1/rules/snapshot/{merchant}"


class _CompaniesServer(_w._Server):
    """Un servidor, dos comercios: cada merchantId sirve SU snapshot firmado."""

    def __init__(self, payloads, *, down=(), **kw):
        super().__init__(next(iter(payloads.values())), **kw)
        self.payloads = payloads
        self.down = set(down)
        self.raw = {}

    def post(self, url, data=None, headers=None, timeout=None, allow_redirects=True):
        self.raw[url] = data
        return super().post(url, data=data, headers=headers, timeout=timeout, allow_redirects=allow_redirects)

    def get(self, url, headers=None, timeout=None, verify=None, allow_redirects=None, stream=None):
        prefix = f"{_API}/v1/rules/snapshot/"
        if url.startswith(prefix):
            merchant = url[len(prefix):]
            self.calls.append(("GET", url, dict(headers or {}), None))
            if merchant not in self.payloads:
                self.unexpected.append(url)
                raise AssertionError(f"unexpected snapshot {url!r}")
            if merchant in self.down:
                raise ConnectionError("snapshot down (test)")
            return _w._resp(200, text=_w._sign_jws(self.payloads[merchant], _w._SNAP_KEY))
        return super().get(url, headers=headers, timeout=timeout, verify=verify,
                           allow_redirects=allow_redirects, stream=stream)

    def snapshot_gets(self, merchant):
        return self.gets(_snap_url(merchant))


def _payloads(*, mode_a="balanced", mode_b="strict", kill_a=False, kill_b=False):
    pa = _w._payload(mode=mode_a, kill_switch=kill_a, rules=[])
    pa["merchantId"] = _MERCH_A
    pb = _w._payload(mode=mode_b, kill_switch=kill_b, rules=[])
    pb["merchantId"] = _MERCH_B
    return {_MERCH_A: pa, _MERCH_B: pb}


class _TwoCompanyMap:
    def __init__(self):
        self.asked = []

    def get_for_company(self, company_id):
        self.asked.append(company_id)
        table = {
            _COMPANY_A: {"merchantId": _MERCH_A, "installationId": _INST_A, "fallbackMode": "balanced", "celEnabled": True},
            _COMPANY_B: {"merchantId": _MERCH_B, "installationId": _INST_B, "fallbackMode": "strict", "celEnabled": True},
        }
        if company_id not in table:
            raise AssertionError(f"unexpected company {company_id!r}")
        return table[company_id]


def _token(merchant, *, order_id="7", amount="50.0"):
    _w._TOKEN_SEQ[0] += 1
    now = int(time.time())
    canonical = json.dumps({"orderId": order_id, "merchantId": merchant, "amount": amount}, sort_keys=True)
    payload = {
        "aud": "trusteed", "merchantId": merchant, "iss": _w._AGENT_DID, "iat": now, "exp": now + 120,
        "checkoutIntentHash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "nonce": f"nonce-35b-{_w._TOKEN_SEQ[0]:08d}-abcdef",
        "jti": f"jti-35b-{_w._TOKEN_SEQ[0]:08d}-abcdef",
    }
    return _w._sign_jws(payload, _w._AGENT_KEY, kid=f"{_w._AGENT_DID}#key-1", typ="trusteed-agent-token+jwt")


def _order(*, token=None, order_company=_COMPANY_B, env_company=_COMPANY_A, context=None):
    """Pedido de ``order_company`` evaluado por un usuario cuyo ``env.company`` es
    ``env_company`` (A) y cuyo contexto puede ser hostil."""
    order = _w._make_order(token=token, context=context, company_id=order_company)
    env = order.env
    env.company = SimpleNamespace(id=env_company)
    env.map = _TwoCompanyMap()
    env.icp.params.update({
        "trusteed.merchant_id": _MERCH_A,
        "trusteed.cel_installation_id": _INST_A,
        "trusteed.cel_hmac_secret": _HMAC_A,
        f"trusteed.cel_hmac_secret.{_COMPANY_A}": _HMAC_A,
        f"trusteed.cel_hmac_secret.{_COMPANY_B}": _HMAC_B,
    })
    return order


def _net(server):
    return patch("requests.get", side_effect=server.get), patch("requests.post", side_effect=server.post)


def _run(call, server):
    g, p = _net(server)
    with g, p:
        call()
    assert server.unexpected == []


def _installations(server, url):
    return {c[2].get("X-Trusteed-Installation-Id") for c in server.calls if c[1] == url}


# ── (1) Se aplica la configuración de la compañía del PEDIDO ──────────────


class TestOrderCompanyDecidesTheConfiguration:
    @pytest.mark.parametrize("ctx", _HOSTILE_CONTEXTS)
    def test_strict_company_b_holds_a_replayed_agent_even_if_env_company_is_balanced_a(self, ctx):
        server = _CompaniesServer(_payloads(), nonce=409)  # nonce ya consumido ⇒ REPLAY
        order = _order(token=_token(_MERCH_B), context=ctx)
        with pytest.raises(ValidationError, match="R002"):
            _run(order._trusteed_cel_enforce, server)
        assert server.snapshot_gets(_MERCH_A) == []
        assert len(server.snapshot_gets(_MERCH_B)) == 1

    def test_the_same_replay_in_balanced_company_a_passes(self):
        """Control: con A como compañía del pedido el mismo REPLAY NO bloquea."""
        server = _CompaniesServer(_payloads(), nonce=409)
        order = _order(token=_token(_MERCH_A), order_company=_COMPANY_A)
        _run(order._trusteed_cel_enforce, server)
        assert len(server.snapshot_gets(_MERCH_A)) == 1
        assert server.snapshot_gets(_MERCH_B) == []

    @pytest.mark.parametrize("ctx", _HOSTILE_CONTEXTS)
    def test_kill_switch_of_company_b_stops_its_agent_order(self, ctx):
        server = _CompaniesServer(_payloads(kill_a=False, kill_b=True))
        order = _order(token=_token(_MERCH_B), context=ctx)
        with pytest.raises(ValidationError, match="kill_switch"):
            _run(order._trusteed_cel_enforce, server)
        assert server.snapshot_gets(_MERCH_A) == []

    def test_kill_switch_of_company_a_does_not_stop_an_order_of_company_b(self):
        server = _CompaniesServer(_payloads(kill_a=True, kill_b=False), nonce=200)
        order = _order(token=_token(_MERCH_B))
        _run(order._trusteed_cel_enforce, server)

    def test_company_b_snapshot_down_holds_its_agent(self):
        server = _CompaniesServer(_payloads(), down={_MERCH_B})
        order = _order(token=_token(_MERCH_B))
        with pytest.raises(ValidationError, match="R000-SNAPSHOT-UNAVAILABLE"):
            _run(order._trusteed_cel_enforce, server)
        assert server.snapshot_gets(_MERCH_A) == []

    def test_company_map_is_asked_for_the_order_company_never_for_env_company(self):
        server = _CompaniesServer(_payloads(), nonce=200)
        order = _order(token=_token(_MERCH_B))
        _run(order._trusteed_cel_enforce, server)
        assert set(order.env.map.asked) == {_COMPANY_B}

    def test_write_to_sale_uses_the_same_company_as_action_confirm(self):
        server = _CompaniesServer(_payloads(), nonce=409)
        order = _order(token=_token(_MERCH_B))
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="R002"):
            order.write({"state": "sale"})
        assert server.snapshot_gets(_MERCH_A) == []

    def test_headless_create_uses_the_company_of_vals(self):
        server = _CompaniesServer(_payloads(kill_a=False, kill_b=True))
        order = _order(token=None)
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="kill_switch"):
            order._trusteed_cel_enforce_vals(
                {"company_id": _COMPANY_B, "state": "sale", "x_trusteed_agent_token": _token(_MERCH_B)}
            )
        assert server.snapshot_gets(_MERCH_A) == []

    @pytest.mark.parametrize("ctx", _HOSTILE_CONTEXTS)
    def test_headless_create_ignores_default_company_and_allowed_ids_of_the_context(self, ctx):
        server = _CompaniesServer(_payloads(kill_a=False, kill_b=True))
        order = _order(token=None, context=ctx)
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="kill_switch"):
            order._trusteed_cel_enforce_vals(
                {"company_id": _COMPANY_B, "state": "sale", "x_trusteed_agent_token": _token(_MERCH_B)}
            )
        assert server.snapshot_gets(_MERCH_A) == []


# ── (1b) Las credenciales son las de la compañía del pedido ───────────────


class TestCredentialsOfTheOrderCompany:
    def test_layer2_and_nonce_use_company_b_installation_not_a(self):
        rules = [dict(_w._R030_CAP_100)]
        pl = _payloads(mode_b="balanced")
        pl[_MERCH_B]["rules"] = rules
        server = _CompaniesServer(pl, nonce=200)
        order = _order(token=_token(_MERCH_B))
        _run(order._trusteed_cel_enforce, server)
        assert len(server.posts(_w._NONCE_URL)) == 1 and len(server.posts(_w._EVALUATE_URL)) == 1
        for url in (_w._NONCE_URL, _w._EVALUATE_URL, _snap_url(_MERCH_B)):
            assert _installations(server, url) == {_INST_B}, url
        assert _INST_A not in {c[2].get("X-Trusteed-Installation-Id") for c in server.calls}

    def test_hmac_secret_of_company_b_signs_the_nonce_and_the_snapshot_pull(self):
        import hmac as _hm
        server = _CompaniesServer(_payloads(mode_b="balanced"), nonce=200)
        order = _order(token=_token(_MERCH_B))
        _run(order._trusteed_cel_enforce, server)

        def _mac(secret, signed):
            return _hm.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()

        nonce = server.posts(_w._NONCE_URL)[0]
        t, s_ = (kv.split("=", 1)[1] for kv in nonce[2]["X-Trusteed-Signature"].split(","))
        raw = server.raw[_w._NONCE_URL].decode("utf-8")
        assert s_ == _mac(_HMAC_B, f"{t}.{raw}")
        assert s_ != _mac(_HMAC_A, f"{t}.{raw}")
        pull = server.snapshot_gets(_MERCH_B)[0]
        t2, s2 = (kv.split("=", 1)[1] for kv in pull[2]["X-Trusteed-Signature"].split(","))
        assert s2 == _mac(_HMAC_B, f"{t2}.") and s2 != _mac(_HMAC_A, f"{t2}.")

    def test_get_credentials_takes_the_explicit_company_not_env_company(self):
        order = _order(token=None)
        creds = order.env.svc.get_credentials(_COMPANY_B)
        assert (creds["merchantId"], creds["installationId"], creds["hmacSecret"], creds["companyId"]) == (
            _MERCH_B, _INST_B, _HMAC_B, _COMPANY_B)
        default = order.env.svc.get_credentials()  # cron / UI: env.company
        assert (default["merchantId"], default["companyId"]) == (_MERCH_A, _COMPANY_A)

    @pytest.mark.parametrize("bad", [0, -1, True, "2", 2.0, [2]])
    def test_an_invalid_explicit_company_id_is_rejected(self, bad):
        order = _order(token=None)
        with pytest.raises(ValueError):
            order.env.svc.get_credentials(bad)


# ── (1c) Sin compañía determinable ────────────────────────────────────────


class TestCompanyNotDeterminable:
    @pytest.mark.parametrize("company", [None, 0, False])
    def test_agent_order_without_company_is_held_with_an_outage_decision(self, company):
        server = _CompaniesServer(_payloads())
        order = _order(token=_token(_MERCH_B), order_company=company)
        with pytest.raises(ValidationError, match="R000-COMPANY-UNRESOLVED") as got:
            _run(order._trusteed_cel_enforce, server)
        assert not any(f"trusteed:R00{n}" in str(got.value) for n in (1, 2, 3))
        assert server.calls == []

    @pytest.mark.parametrize("company", [None, 0, False])
    def test_human_order_without_company_is_never_blocked(self, company):
        server = _CompaniesServer(_payloads(mode_a="strict", kill_a=True))
        order = _order(token=None, order_company=company)
        _run(order._trusteed_cel_enforce, server)
        assert server.calls == []

    def test_an_order_object_without_company_attribute_counts_as_unresolved(self):
        server = _CompaniesServer(_payloads())
        order = _order(token=_token(_MERCH_B))
        del order.company_id
        with pytest.raises(ValidationError, match="R000-COMPANY-UNRESOLVED"):
            _run(order._trusteed_cel_enforce, server)

    @pytest.mark.parametrize("vals_company", [None, False, 0, -3, True, "2", 2.0, [2]])
    def test_headless_agent_with_a_bad_company_in_vals_is_held(self, vals_company):
        server = _CompaniesServer(_payloads())
        order = _order(token=None)
        vals = {"state": "sale", "x_trusteed_agent_token": _token(_MERCH_B), "company_id": vals_company}
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="R000-COMPANY-UNRESOLVED"):
            order._trusteed_cel_enforce_vals(vals)
        assert server.calls == []

    def test_headless_agent_without_company_in_vals_is_held_even_with_a_default_in_context(self):
        server = _CompaniesServer(_payloads())
        order = _order(token=None, context={"default_company_id": _COMPANY_B})
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="R000-COMPANY-UNRESOLVED"):
            order._trusteed_cel_enforce_vals({"state": "sale", "x_trusteed_agent_token": _token(_MERCH_B)})
        assert server.calls == []

    def test_headless_agent_token_in_context_without_company_is_held(self):
        server = _CompaniesServer(_payloads())
        order = _order(token=None, context={"trusteed_agent_token": _token(_MERCH_B)})
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="R000-COMPANY-UNRESOLVED"):
            order._trusteed_cel_enforce_vals({"state": "sale"})

    def test_headless_human_without_company_passes(self):
        server = _CompaniesServer(_payloads(mode_a="strict", kill_a=True))
        order = _order(token=None)
        g, p = _net(server)
        with g, p:
            order._trusteed_cel_enforce_vals({"state": "sale", "name": "SO1"})
        assert server.calls == []

    def test_a_company_resolution_helper_never_reads_env_company(self):
        order = _order(token=None)
        order.env.company = SimpleNamespace(id=_COMPANY_A)
        assert _enforcement._company_id_of(order) == _COMPANY_B
        assert _enforcement._company_id_from_vals({"company_id": _COMPANY_B}) == _COMPANY_B
        assert _enforcement._company_id_from_vals({}) is None
        assert _enforcement._company_id_from_vals(None) is None


# ── (3) Cachés por compañía ───────────────────────────────────────────────


class TestPerCompanyCaches:
    def test_snapshot_cache_is_keyed_by_merchant_and_company(self):
        server = _CompaniesServer(_payloads(), nonce=200)
        _run(_order(token=_token(_MERCH_A), order_company=_COMPANY_A)._trusteed_cel_enforce, server)
        _run(_order(token=_token(_MERCH_B))._trusteed_cel_enforce, server)
        assert set(_snapshot._SNAPSHOT_CACHE) == {(_MERCH_A, _COMPANY_A), (_MERCH_B, _COMPANY_B)}

    def test_company_a_warm_cache_is_never_served_to_company_b(self):
        server = _CompaniesServer(_payloads(), nonce=200)
        _run(_order(token=_token(_MERCH_A), order_company=_COMPANY_A)._trusteed_cel_enforce, server)
        assert len(server.snapshot_gets(_MERCH_A)) == 1
        _run(_order(token=_token(_MERCH_B))._trusteed_cel_enforce, server)
        assert len(server.snapshot_gets(_MERCH_B)) == 1  # B pidió el suyo, no leyó el de A

    def test_same_merchant_in_two_companies_keeps_two_cache_entries(self):
        server = _CompaniesServer(_payloads(), nonce=200)
        g, p = _net(server)
        with g, p:
            for company in (_COMPANY_A, _COMPANY_B):
                status, payload = _snapshot.pull_snapshot_result(
                    _API, _MERCH_A, _INST_A, _HMAC_A, company)
                assert status == "ok" and payload["merchantId"] == _MERCH_A
        assert set(_snapshot._SNAPSHOT_CACHE) == {(_MERCH_A, _COMPANY_A), (_MERCH_A, _COMPANY_B)}
        assert len(server.snapshot_gets(_MERCH_A)) == 2

    def test_negative_jwks_window_is_public_material_not_company_state(self):
        """L3: ``(api_base, kid)`` — el JWKS y el ``kid`` son de la plataforma,
        iguales para todas las compañías; el registro no guarda nada de la
        configuración de ninguna (documentado: no es una mezcla)."""
        server = _w._Server(_w._payload(), snapshot="tampered")
        g, p = _net(server)
        with g, p:
            for company in (_COMPANY_A, _COMPANY_B):
                status, _ = _snapshot.pull_snapshot_result(_API, _w._MERCHANT, _INST_A, _HMAC_A, company)
                assert status == "tampered"
        assert list(_snapshot._NEGATIVE_RETRY) == [(_API, _w._SNAP_KID)]


# ── (4) Regresión: una sola compañía = comportamiento de hoy ──────────────


class TestSingleCompanyIsUnchanged:
    def test_single_company_order_enforces_exactly_like_before(self):
        server = _w._Server(_w._payload(mode="strict"), nonce=409)
        order = _w._make_order(token=_w._agent_token())  # company 1 = env.company
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="R002"):
            order._trusteed_cel_enforce()
        assert server.unexpected == []

    def test_single_company_default_get_credentials_still_uses_env_company(self):
        order = _w._make_order(token=None)
        creds = order.env.svc.get_credentials()
        assert creds["companyId"] == 1 and creds["merchantId"] == _w._MERCHANT
        assert order.env.svc.get_credentials(1) == creds
