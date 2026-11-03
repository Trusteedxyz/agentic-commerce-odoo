"""Cableado del flujo principal en ``SaleOrderEnforcement`` (plan 04, Tasks 13 y 21).

A8 / FR-070a / D1: el kill-switch del comerciante y su ``fallbackMode`` sólo
gobiernan a los pedidos AGÉNTICOS; un pedido humano (sin token) nunca se
bloquea por una avería nuestra ni por el kill-switch. Sólo una regla
``appliesTo: ALL`` de un snapshot verificado (el tope de R030…) lo bloquea.

Task 21 (I1-Odoo): sin snapshot verificado se decide por la CAUSA
(``decide_on_degraded_enforcement``): firma inválida ⇒ agente retenido en todo
modo; snapshot ausente + token ⇒ agente retenido en todo modo; CEL apagado ⇒
nadie retenido. El resolver acepta la LISTA real del servidor y el ``jti`` se
consume con las credenciales de los ajustes (el snapshot no las trae).

Estos tests EJECUTAN el flujo real: ``_trusteed_cel_enforce``,
``_trusteed_cel_enforce_vals``, ``action_confirm``/``create``/``write``, el
``EnforcementSnapshotService`` real (``get_credentials`` →
``pull_snapshot_result`` → verificación Ed25519 del JWS contra el JWKS),
``verify_agent_token`` real con tokens firmados y ``consume_nonce`` real. Sólo
se simula la red (``requests.get``/``requests.post``) y el ORM (stubs). El
snapshot servido es el ``snapshotPayload`` del fixture compartido (forma que
valida contra ``RuleSnapshotPayloadSchema``, resolver en LISTA). Lo que depende
del ORM real de Odoo (``super()``, contexto real, transacciones) queda sin
verificar.

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_platform_flow_wiring.py \\
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import base64
import copy
import hashlib
import importlib.util
import json
import re
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from odoo.exceptions import ValidationError

_ADDON_ROOT = Path(__file__).parent.parent
_ROOT_PKG = "addon_pflow"
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
_load_module("utils/agent_reason.py", f"{_UTILS_PKG}.agent_reason", _UTILS_PKG)
sys.modules[_UTILS_PKG].ssrf = _ssrf_mod  # type: ignore[attr-defined]
_verifier_mod = _load_module(
    "models/enforcement_token_verifier.py", f"{_MODELS_PKG}.enforcement_token_verifier", _MODELS_PKG
)
_load_module("models/offline_safety_valve_evaluator.py", f"{_MODELS_PKG}.offline_safety_valve_evaluator", _MODELS_PKG)
_nonce_mod = _load_module("models/nonce_consumer.py", f"{_MODELS_PKG}.nonce_consumer", _MODELS_PKG)
sys.modules[_MODELS_PKG].nonce_consumer = _nonce_mod  # type: ignore[attr-defined]
_load_module("models/platform_flow.py", f"{_MODELS_PKG}.platform_flow", _MODELS_PKG)
_load_module("models/agent_did_guard.py", f"{_MODELS_PKG}.agent_did_guard", _MODELS_PKG)
_load_module("models/headless_human_valve.py", f"{_MODELS_PKG}.headless_human_valve", _MODELS_PKG)  # Task 38d
_load_module("models/snapshot_payload_decoder.py", f"{_MODELS_PKG}.snapshot_payload_decoder", _MODELS_PKG)  # Task 24
_snapshot_mod = _load_module("models/enforcement_snapshot.py", f"{_MODELS_PKG}.enforcement_snapshot", _MODELS_PKG)
_enforcement_mod = _load_module("models/sale_order_enforcement.py", f"{_MODELS_PKG}.sale_order_enforcement", _MODELS_PKG)

SaleOrderEnforcement = _enforcement_mod.SaleOrderEnforcement
EnforcementSnapshotService = _snapshot_mod.EnforcementSnapshotService

_FIXTURE = json.loads(
    (_ADDON_ROOT.parent / "shared" / "src" / "enforcement" / "__fixtures__" / "platform-flow-conformance.json").read_text(
        encoding="utf-8"
    )
)
_REAL_PAYLOAD = _FIXTURE["snapshotResolverShape"]["snapshotPayload"]

_API = "https://api.trusteed.xyz"
_MERCHANT = _REAL_PAYLOAD["merchantId"]
_INSTALLATION = "22222222-3333-4444-5555-666666666666"
_HMAC = "example-hmac-secret-not-real-0000"
_SNAP_URL = f"{_API}/v1/rules/snapshot/{_MERCHANT}"
_JWKS_URL = f"{_API}/.well-known/jwks.json"
_EVALUATE_URL = f"{_API}/v1/rules/evaluate"
_NONCE_URL = f"{_API}/v1/agent-events/nonce-consume"
_SNAP_KID = _REAL_PAYLOAD["snapshotKeyAnchors"]["currentKid"]

_AGENT_DID = "did:web:agent-odoo-test.example.com"
_FUTURE_VALID_UNTIL = "2099-01-01T00:00:00.000Z"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _raw_pub(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


# Claves de prueba generadas en cada ejecución (nada versionado).
_SNAP_KEY = Ed25519PrivateKey.generate()
_ROTATED_SNAP_KEY = Ed25519PrivateKey.generate()
_AGENT_KEY = Ed25519PrivateKey.generate()
_FORGER_KEY = Ed25519PrivateKey.generate()

# Valores que emite producción: el snapshot firmado trae SIEMPRE `appliesTo`.
_R030_CAP_100 = {"ruleCode": "R030.simple-controls", "enabled": True, "mode": "enforce", "appliesTo": "ALL",
                 "tier": 2, "params": {"maxAmountCents": 10000}}
_R029_ESTRICTO = {"ruleCode": "R029.merchant-preset", "enabled": True, "mode": "enforce", "appliesTo": "AGENT",
                  "tier": 2, "params": {"preset": "estricto"}}


def _payload(*, kill_switch=False, mode="balanced", rules=None, drop_mode=False, resolver_extra=True):
    """``snapshotPayload`` del fixture (forma real) + la clave del agente de
    prueba añadida a la LISTA del resolver."""
    p = copy.deepcopy(_REAL_PAYLOAD)
    # El fixture compartido lleva un ``validUntil`` fijo ya pasado; producción
    # emite uno futuro y este puerto lo comprueba (L6a).
    p["validUntil"] = _FUTURE_VALID_UNTIL
    p["killSwitch"] = kill_switch
    p["fallbackMode"] = mode
    p["rules"] = [_R030_CAP_100] if rules is None else rules
    if resolver_extra:
        p["agentDidResolver"] = p["agentDidResolver"] + [
            {"did": _AGENT_DID, "publicKeyJwk": {"kty": "OKP", "crv": "Ed25519", "x": _b64u(_raw_pub(_AGENT_KEY)),
                                                  "kid": f"{_AGENT_DID}#key-1"}}
        ]
    if drop_mode:
        del p["fallbackMode"]
    assert "installationId" not in p and "hmacSecret" not in p  # forma real
    return p


def _sign_jws(payload: dict, key: Ed25519PrivateKey, kid: str = _SNAP_KID, typ=None) -> str:
    header = {"alg": "EdDSA", "kid": kid}
    if typ:
        header["typ"] = typ
    h = _b64u(json.dumps(header, separators=(",", ":")).encode())
    p = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = key.sign(f"{h}.{p}".encode("ascii"))
    return f"{h}.{p}.{_b64u(sig)}"


def _tamper(jws: str) -> str:
    """Mismo JWS con el payload cambiado (reglas vaciadas, modo permissive) y la firma vieja."""
    h, p, s = jws.split(".")
    body = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    body["fallbackMode"] = "permissive"
    body["killSwitch"] = False
    body["rules"] = []
    body["ruleSetVersion"] = body["ruleSetVersion"] + 1
    tampered = f"{h}.{_b64u(json.dumps(body, separators=(',', ':')).encode())}.{s}"
    assert tampered != jws
    return tampered


def _intent_hash(order_id: str, amount: str) -> str:
    canonical = json.dumps({"orderId": order_id, "merchantId": _MERCHANT, "amount": amount}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_TOKEN_SEQ = [0]


_DROP = object()


def _agent_token(*, key=_AGENT_KEY, order_id="7", amount="50.0", claims=None) -> str:
    """Token con la estructura EXACTA de ``apps/api/scripts/generate-test-agent-token.ts``:
    `nonce` (hex de 32) y SIN `jti`. ``claims`` sobrescribe claims; ``_DROP`` los quita."""
    _TOKEN_SEQ[0] += 1
    now = int(time.time())
    payload = {
        "iss": _AGENT_DID,
        "aud": "trusteed",
        "iat": now,
        "exp": now + 120,
        "nonce": f"{_TOKEN_SEQ[0]:08x}" + "ab" * 12,
        "checkoutIntentHash": _intent_hash(order_id, amount),
        "merchantId": _MERCHANT,
    }
    for name, value in (claims or {}).items():
        if value is _DROP:
            payload.pop(name, None)
        else:
            payload[name] = value
    return _sign_jws(payload, key, kid=f"{_AGENT_DID}#key-1", typ="trusteed-agent-token+jwt")


def _resp(status, *, text="", body=None):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = body if body is not None else {}
    # El snapshot se lee con ``stream=True`` y tope (L6b): ``iter_content``.
    r.headers = {}
    r.iter_content = lambda chunk_size=None: iter([text.encode("utf-8")] if text else [])
    return r


class _Server:
    """Simula la API: snapshot + JWKS (GET), evaluate + nonce-consume (POST)."""

    def __init__(self, payload, *, snapshot="ok", jwks="ok", evaluate="allow", nonce=200, jwks_key=None):
        self.payload = payload
        self.snapshot = snapshot
        self.jwks = jwks
        self.evaluate = evaluate
        self.nonce = nonce
        self.jwks_key = jwks_key or _SNAP_KEY
        self.calls = []
        self.unexpected = []

    def _jws(self):
        if self.snapshot == "rotated":
            return _sign_jws(self.payload, _ROTATED_SNAP_KEY)
        jws = _sign_jws(self.payload, _SNAP_KEY)
        return _tamper(jws) if self.snapshot == "tampered" else jws

    def get(self, url, headers=None, timeout=None, verify=None, allow_redirects=None, stream=None):
        self.calls.append(("GET", url, dict(headers or {}), None))
        if url == _JWKS_URL:
            if self.jwks == "down":
                raise ConnectionError("jwks down (test)")
            keys = [{"kty": "OKP", "crv": "Ed25519", "kid": _SNAP_KID, "x": _b64u(_raw_pub(self.jwks_key))}]
            return _resp(200, body={"keys": keys})
        if url == _SNAP_URL:
            if self.snapshot == "down":
                raise ConnectionError("snapshot down (test)")
            if self.snapshot == "http500":
                return _resp(500)
            return _resp(200, text=self._jws())
        self.unexpected.append(url)
        raise AssertionError(f"unexpected GET {url!r}")

    def post(self, url, data=None, headers=None, timeout=None, allow_redirects=True):
        body = json.loads(data)
        self.calls.append(("POST", url, dict(headers or {}), body))
        if url == _NONCE_URL:
            return _resp(self.nonce)
        if url == _EVALUATE_URL:
            if self.evaluate == "down":
                raise ConnectionError("rules service down (test)")
            if self.evaluate == "block":
                return _resp(200, body={"decision": "BLOCK", "ruleCode": "R010", "reason": "example"})
            return _resp(200, body={"decision": "ALLOW"})
        self.unexpected.append(url)
        raise AssertionError(f"unexpected POST {url!r}")

    def posts(self, url):
        return [c for c in self.calls if c[0] == "POST" and c[1] == url]

    def gets(self, url):
        return [c for c in self.calls if c[0] == "GET" and c[1] == url]


# ── ORM stubs ───────────────────────────────────────────────────────────────


class _ICP:
    def __init__(self, params):
        self.params = params

    def sudo(self):
        return self

    def get_param(self, key, default=""):
        return self.params.get(key, default)

    def set_param(self, key, value):
        self.params[key] = value


class _CompanyMap:
    def __init__(self, mapping):
        self.mapping = mapping

    def get_for_company(self, company_id):
        if company_id != 1:
            raise AssertionError(f"unexpected company {company_id!r}")
        return self.mapping


class _Svc(EnforcementSnapshotService):
    def sudo(self):
        return self


class _Env:
    def __init__(self, *, params, mapping=None, context=None):
        self.icp = _ICP(params)
        self.map = _CompanyMap(mapping)
        self.company = SimpleNamespace(id=1)
        self.context = dict(context or {})
        self.svc = _Svc()
        self.svc.env = self

    def __getitem__(self, key):
        models = {
            "ir.config_parameter": self.icp,
            "trusteed.company.enforcement.map": self.map,
            "trusteed.enforcement.snapshot": self.svc,
        }
        if key not in models:
            raise AssertionError(f"unexpected env model {key!r}")
        return models[key]

    def with_context(self, extra):
        clone = copy.copy(self)
        clone.context = {**self.context, **extra}
        return clone


def _params(configured=True):
    if not configured:
        return {"trusteed.api_base": _API}
    return {
        "trusteed.api_base": _API,
        "trusteed.merchant_id": _MERCHANT,
        "trusteed.cel_installation_id": _INSTALLATION,
        "trusteed.cel_hmac_secret": _HMAC,
    }


class _OrmBase:
    """Lo que ``super()`` encuentra tras ``SaleOrderEnforcement`` (el ORM)."""

    def action_confirm(self):
        # Odoo 18: confirma con write({'state': 'sale', ...}).
        self.write({"state": "sale"})
        return True

    def write(self, vals):
        self.base_writes.append((dict(vals), dict(self.env.context)))
        return True

    def create(self, vals_list):
        # El ORM real rellena los campos ausentes con ``default_<campo>`` del
        # contexto (``_add_missing_default_values``): se emula para que un
        # test vea el valor que llegaría al registro.
        ctx = self.env.context
        filled = []
        for vals in vals_list:
            row = dict(vals)
            for key, value in ctx.items():
                if isinstance(key, str) and key.startswith("default_") and key[8:] not in row:
                    row[key[8:]] = value
            filled.append(row)
        self.base_creates.append(filled)
        return self  # el ORM real devuelve el recordset creado (Task 38d lo recorre)


class _Order(SaleOrderEnforcement, _OrmBase):
    def __iter__(self):
        return iter([self])

    @property
    def ids(self):
        return [self.id] if getattr(self, "id", None) else []

    def sudo(self):
        return self

    def with_context(self, *args, **kw):
        # Semántica de Odoo: un dict posicional REEMPLAZA el contexto; los
        # kwargs se añaden encima.
        ctx = dict(args[0]) if args else dict(self.env.context)
        ctx.update(kw)
        clone = copy.copy(self)
        clone.env = copy.copy(self.env)
        clone.env.context = ctx
        return clone


class _FakeCurrency:
    name = "EUR"


def _make_order(*, token=None, amount_total=50.0, mapping=None, configured=True, context=None, company_id=1):
    order = object.__new__(_Order)
    order.id = 7
    order.company_id = SimpleNamespace(id=company_id) if company_id is not None else SimpleNamespace(id=False)
    order.amount_total = amount_total
    order.currency_id = _FakeCurrency()
    order.order_line = []
    order.env = _Env(params=_params(configured), mapping=mapping, context=context)
    order.x_trusteed_agent_token = token
    order.x_trusteed_payment_method = ""
    order.partner_id = None
    order.partner_shipping_id = None
    order.partner_invoice_id = None
    order.base_writes = []
    order.base_creates = []
    return order


def _mapping(mode="balanced", cel_enabled=True):
    return {"merchantId": _MERCHANT, "installationId": _INSTALLATION, "fallbackMode": mode, "celEnabled": cel_enabled}


@pytest.fixture(autouse=True)
def _clear_caches():
    for cache in (_snapshot_mod._SNAPSHOT_CACHE, _snapshot_mod._JWKS_CACHE, _snapshot_mod._JWKS_EXPIRE,
                  _snapshot_mod._NEGATIVE_RETRY, _verifier_mod._NONCE_CACHE):
        cache.clear()
    yield
    for cache in (_snapshot_mod._SNAPSHOT_CACHE, _snapshot_mod._JWKS_CACHE, _snapshot_mod._JWKS_EXPIRE,
                  _snapshot_mod._NEGATIVE_RETRY, _verifier_mod._NONCE_CACHE):
        cache.clear()


def _net(server):
    return patch("requests.get", side_effect=server.get), patch("requests.post", side_effect=server.post)


def _enforce(order, server):
    g, p = _net(server)
    with g, p:
        order._trusteed_cel_enforce()
    assert server.unexpected == []


def _did_writes(order):
    return [v for v, _ctx in order.base_writes if "x_trusteed_agent_did" in v]


# ── Resolver en LISTA + token real ─────────────────────────────────────────


class TestLegitimateTokenIsVerifiedAndConsumed:
    def test_list_resolver_verifies_the_token_and_the_nonce_is_consumed(self):
        server = _Server(_payload())
        order = _make_order(token=_agent_token())
        _enforce(order, server)
        nonce_posts = server.posts(_NONCE_URL)
        assert len(nonce_posts) == 1, "el nonce debe consumirse una vez"
        _, _, headers, body = nonce_posts[0]
        assert headers["X-Trusteed-Installation-Id"] == _INSTALLATION
        assert headers["X-Trusteed-Signature"].startswith("t=") and ",s=" in headers["X-Trusteed-Signature"]
        assert body["installationId"] == _INSTALLATION
        assert body["agentId"] == _AGENT_DID
        # B5: sustituye a ``startswith("jti-example-")`` (el token llevaba `jti`
        # y el consumo lo leía de ahí). El token canónico no lleva `jti`.
        assert re.fullmatch(r"[0-9a-f]{8}abababababababababababab", body["nonce"])
        evaluate = server.posts(_EVALUATE_URL)
        assert len(evaluate) == 1
        assert evaluate[0][3]["agentId"] == _AGENT_DID
        assert evaluate[0][2]["X-Trusteed-Installation-Id"] == _INSTALLATION
        assert _did_writes(order) == [{"x_trusteed_agent_did": _AGENT_DID}]

    def test_forged_token_with_the_real_resolver_is_rejected(self):
        server = _Server(_payload())
        order = _make_order(token=_agent_token(key=_FORGER_KEY))
        with pytest.raises(ValidationError, match="R001"):
            _enforce(order, server)
        assert server.posts(_NONCE_URL) == []
        assert _did_writes(order) == []

    def test_a_did_absent_from_the_list_is_rejected(self):
        server = _Server(_payload(resolver_extra=False))
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=_agent_token()), server)

    def test_a_resolver_key_declared_as_another_type_is_rejected(self):
        payload = _payload()
        payload["agentDidResolver"][-1]["publicKeyJwk"]["kty"] = "EC"
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=_agent_token()), _Server(payload))

    def test_headless_create_in_sale_does_not_consume_the_jti(self):
        # Task 27a (M3): sustituye a ``test_headless_create_consumes_the_jti``,
        # que fijaba el camino sin capa 2 ni válvula; ahora se rechaza y el
        # ``jti`` queda intacto para ``action_confirm``.
        server = _Server(_payload())
        model = _make_order()
        token = _agent_token(order_id="pending", amount="")
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="CEL-HEADLESS-CONFIRM-REQUIRED"):
            model._trusteed_cel_enforce_vals({"company_id": 1, "state": "sale", "x_trusteed_agent_token": token})
        assert server.posts(_NONCE_URL) == []


class TestNonceOutcomes:
    def test_replay_in_balanced_passes_but_never_attributes_the_did(self):
        server = _Server(_payload(mode="balanced"), nonce=409)
        order = _make_order(token=_agent_token())
        _enforce(order, server)
        attrs = server.posts(_EVALUATE_URL)[0][3]["orderContext"]["cartAttributes"]
        assert attrs["_agent_token_replay"] == "true"
        assert _did_writes(order) == []

    def test_replay_in_strict_blocks(self):
        server = _Server(_payload(mode="strict"), nonce=409)
        with pytest.raises(ValidationError, match="R002"):
            _enforce(_make_order(token=_agent_token()), server)

    def test_indeterminate_in_balanced_flags_and_does_not_attribute(self):
        server = _Server(_payload(mode="balanced"), nonce=503)
        order = _make_order(token=_agent_token())
        _enforce(order, server)
        attrs = server.posts(_EVALUATE_URL)[0][3]["orderContext"]["cartAttributes"]
        assert attrs["_agent_token_nonce_unavailable"] == "true"
        assert _did_writes(order) == []

    def test_indeterminate_nonce_with_an_unresolved_mode_blocks_the_agent(self):
        server = _Server(_payload(mode="observe"), nonce=503)
        with pytest.raises(ValidationError, match="CEL-NONCE-UNAVAILABLE"):
            _enforce(_make_order(token=_agent_token()), server)


# ── I1-Odoo: sin snapshot verificado se decide por la causa ────────────────


class TestTamperedSnapshot:
    @pytest.mark.parametrize("local", [None, "strict", "balanced", "permissive"])
    def test_agent_with_token_is_held_in_every_mode(self, local):
        server = _Server(_payload(mode="permissive"), snapshot="tampered")
        order = _make_order(token=_agent_token(), mapping=None if local is None else _mapping(local))
        with pytest.raises(ValidationError, match="R000-SNAPSHOT-INVALID") as exc:
            _enforce(order, server)
        assert "R001" not in str(exc.value)
        assert server.posts(_NONCE_URL) == [] and server.posts(_EVALUATE_URL) == []

    def test_headless_agent_is_held(self):
        server = _Server(_payload(mode="permissive"), snapshot="tampered")
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="R000-SNAPSHOT-INVALID"):
            _make_order()._trusteed_cel_enforce_vals({"company_id": 1, "state": "sale", "x_trusteed_agent_token": _agent_token()})

    @pytest.mark.parametrize("local", [None, "strict"])
    def test_human_is_never_blocked(self, local):
        server = _Server(_payload(mode="strict", kill_switch=True), snapshot="tampered")
        order = _make_order(token=None, amount_total=500.0, mapping=None if local is None else _mapping(local))
        _enforce(order, server)  # ni el tope R030 de un snapshot sin verificar
        assert server.posts(_EVALUATE_URL) == []

    def test_a_snapshot_signed_by_another_key_is_tampered_after_the_jwks_retry(self):
        # El servidor firma con una clave que su JWKS no publica.
        server = _Server(_payload(), snapshot="rotated")
        with pytest.raises(ValidationError, match="R000-SNAPSHOT-INVALID"):
            _enforce(_make_order(token=_agent_token()), server)
        assert len(server.gets(_JWKS_URL)) == 2, "debe reintentar el JWKS una vez antes de declararla inválida"


class TestJwksRotation:
    def test_a_rotated_key_under_a_cached_jwks_is_retried_and_verifies(self):
        # JWKS en caché (1 h) con la clave vieja; el servidor ya firma con la nueva.
        _snapshot_mod._JWKS_CACHE[_API] = {_SNAP_KID: _raw_pub(_SNAP_KEY)}
        _snapshot_mod._JWKS_EXPIRE[_API] = time.time() + 3600
        server = _Server(_payload(), snapshot="rotated", jwks_key=_ROTATED_SNAP_KEY)
        order = _make_order(token=_agent_token())
        _enforce(order, server)
        assert len(server.gets(_JWKS_URL)) == 1
        assert len(server.posts(_NONCE_URL)) == 1

    def test_a_cached_snapshot_that_no_longer_verifies_is_refetched(self):
        # Caché viva con un JWS firmado por la clave vieja; el JWKS ya sólo publica la nueva.
        key = (_MERCHANT, 1)
        _snapshot_mod._SNAPSHOT_CACHE[key] = (_sign_jws(_payload(), _SNAP_KEY), time.time() + 300)
        server = _Server(_payload(), snapshot="rotated", jwks_key=_ROTATED_SNAP_KEY)
        order = _make_order(token=_agent_token())
        _enforce(order, server)
        assert len(server.gets(_SNAP_URL)) == 1, "la caché que no verifica se descarta y se pide de nuevo"
        assert len(server.posts(_NONCE_URL)) == 1

    def test_jwks_unreachable_is_unavailable_not_tampered(self):
        server = _Server(_payload(), jwks="down")
        with pytest.raises(ValidationError, match="R000-SNAPSHOT-UNAVAILABLE"):
            _enforce(_make_order(token=_agent_token()), server)


class TestMissingSnapshot:
    @pytest.mark.parametrize("local", [None, "strict", "balanced", "permissive"])
    @pytest.mark.parametrize("cause", ["down", "http500"])
    def test_agent_with_token_is_held_in_every_mode(self, local, cause):
        server = _Server(_payload(), snapshot=cause)
        order = _make_order(token=_agent_token(), mapping=None if local is None else _mapping(local))
        with pytest.raises(ValidationError, match="R000-SNAPSHOT-UNAVAILABLE") as exc:
            _enforce(order, server)
        assert "R001" not in str(exc.value)

    @pytest.mark.parametrize("local", [None, "strict", "balanced", "permissive"])
    def test_human_is_never_blocked(self, local):
        server = _Server(_payload(), snapshot="down")
        _enforce(_make_order(token=None, amount_total=500.0, mapping=None if local is None else _mapping(local)), server)

    @pytest.mark.parametrize("local", [None, "strict", "balanced"])
    def test_headless_human_is_never_blocked(self, local):
        server = _Server(_payload(), snapshot="down")
        g, p = _net(server)
        with g, p:
            _make_order(mapping=None if local is None else _mapping(local))._trusteed_cel_enforce_vals({"company_id": 1, "state": "sale"})

    def test_our_exception_reading_the_snapshot_is_missing_snapshot(self):
        order = _make_order(token=_agent_token())

        def _boom(company_id=None):
            raise RuntimeError("internal failure (test)")

        order.env.svc.get_snapshot_result = _boom
        with pytest.raises(ValidationError, match="R000-SNAPSHOT-UNAVAILABLE"):
            order._trusteed_cel_enforce()


class TestLayerDisabled:
    def test_cel_disabled_lets_an_agent_through_without_touching_the_network(self):
        server = _Server(_payload())
        order = _make_order(token=_agent_token(), mapping=_mapping("strict", cel_enabled=False))
        _enforce(order, server)
        assert server.calls == []
        assert _did_writes(order) == []

    def test_not_configured_is_layer_disabled(self):
        server = _Server(_payload())
        _enforce(_make_order(token=_agent_token(), configured=False), server)
        assert server.calls == []

    def test_cel_disabled_human(self):
        server = _Server(_payload())
        _enforce(_make_order(token=None, mapping=_mapping("strict", cel_enabled=False)), server)
        assert server.calls == []


class TestCachedSnapshotAndLayer2Down:
    def _warm(self, payload):
        warm = _Server(payload)
        _enforce(_make_order(token=None), warm)
        assert _snapshot_mod._SNAPSHOT_CACHE, "la caché debe quedar caliente"

    def test_stale_cache_with_server_down_is_used_and_strict_holds_the_agent(self):
        payload = _payload(mode="strict")
        self._warm(payload)
        for key, (jws, _exp) in list(_snapshot_mod._SNAPSHOT_CACHE.items()):
            _snapshot_mod._SNAPSHOT_CACHE[key] = (jws, 0.0)  # caducada
        server = _Server(payload, snapshot="down", evaluate="down")
        with pytest.raises(ValidationError, match="CEL-TIMEOUT"):
            _enforce(_make_order(token=_agent_token()), server)
        assert len(server.posts(_NONCE_URL)) == 1, "con snapshot verificado el token se verifica y consume"

    @pytest.mark.parametrize("mode", ["balanced", "permissive", " Balanced "])
    def test_agent_in_an_opening_mode_passes(self, mode):
        _enforce(_make_order(token=_agent_token()), _Server(_payload(mode=mode), evaluate="down"))

    def test_agent_with_mode_absent_uses_the_balanced_default(self):
        _enforce(_make_order(token=_agent_token()), _Server(_payload(drop_mode=True), evaluate="down"))

    @pytest.mark.parametrize("mode", ["observe", "", "unknown", "enforce"])
    def test_agent_with_an_unresolved_mode_is_blocked(self, mode):
        # nonce ACCEPTED (200): el modo no resuelto sólo cierra en la caída de capa 2.
        with pytest.raises(ValidationError, match="CEL-TIMEOUT"):
            _enforce(_make_order(token=_agent_token()), _Server(_payload(mode=mode), evaluate="down"))

    @pytest.mark.parametrize("mode", ["strict", "observe", "", "unknown"])
    def test_human_is_not_blocked_by_our_outage(self, mode):
        _enforce(_make_order(token=None), _Server(_payload(mode=mode), evaluate="down"))

    def test_human_own_cap_still_blocks_offline(self):
        server = _Server(_payload(mode="permissive"), evaluate="down")
        with pytest.raises(ValidationError, match="merchant_policy"):
            _enforce(_make_order(token=None, amount_total=180.0), server)

    def test_human_is_not_blocked_offline_by_an_agent_only_rule(self):
        """Antes de la Task 13: R029 «estricto» bloqueaba TODO pedido humano durante una caída."""
        _enforce(_make_order(token=None), _Server(_payload(rules=[_R030_CAP_100, _R029_ESTRICTO]), evaluate="down"))

    def test_agent_is_blocked_offline_by_an_agent_only_rule(self):
        server = _Server(_payload(rules=[_R030_CAP_100, _R029_ESTRICTO]), evaluate="down")
        with pytest.raises(ValidationError, match="merchant_policy"):
            _enforce(_make_order(token=_agent_token()), server)


# ── Kill-switch: sólo agéntico (FR-070a) ────────────────────────────────────


class TestKillSwitchOnlyAgentic:
    def test_human_is_not_stopped_by_the_kill_switch_and_still_gets_evaluated(self):
        server = _Server(_payload(kill_switch=True))
        _enforce(_make_order(token=None), server)
        assert len(server.posts(_EVALUATE_URL)) == 1

    def test_agent_is_stopped_by_the_kill_switch(self):
        with pytest.raises(ValidationError, match="kill_switch"):
            _enforce(_make_order(token=_agent_token()), _Server(_payload(kill_switch=True, mode="permissive")))

    def test_agent_with_an_invalid_token_is_stopped_by_the_kill_switch(self):
        with pytest.raises(ValidationError, match="kill_switch"):
            _enforce(_make_order(token="not-a-valid-token"), _Server(_payload(kill_switch=True)))

    def test_headless_create_human_is_not_stopped_by_the_kill_switch(self):
        g, p = _net(_Server(_payload(kill_switch=True, mode="strict")))
        with g, p:
            _make_order()._trusteed_cel_enforce_vals({"company_id": 1, "state": "sale"})

    def test_headless_create_agent_is_stopped_by_the_kill_switch(self):
        g, p = _net(_Server(_payload(kill_switch=True, mode="permissive")))
        with g, p, pytest.raises(ValidationError, match="kill_switch"):
            _make_order()._trusteed_cel_enforce_vals({"company_id": 1, "state": "sale", "x_trusteed_agent_token": "a.b.c"})

    def test_headless_create_agent_via_context_is_stopped_by_the_kill_switch(self):
        g, p = _net(_Server(_payload(kill_switch=True)))
        with g, p, pytest.raises(ValidationError, match="kill_switch"):
            _make_order(context={"trusteed_agent_token": "a.b.c"})._trusteed_cel_enforce_vals({"company_id": 1, "state": "sale"})


class TestHeadlessCreateHuman:
    @pytest.mark.parametrize("mode", ["strict", "balanced", "permissive", "unknown"])
    def test_human_create_is_never_blocked_by_the_fallback_mode(self, mode):
        g, p = _net(_Server(_payload(mode=mode)))
        with g, p:
            _make_order()._trusteed_cel_enforce_vals({"company_id": 1, "state": "sale"})


# ── Guarda de errores internos ──────────────────────────────────────────────


def _boom(*a, **k):
    raise RuntimeError("internal failure (test)")


class TestInternalErrorGuard:
    def test_human_is_never_blocked_by_our_own_exception(self):
        order = _make_order(token=None)
        order._trusteed_resolve_payment_method = _boom
        _enforce(order, _Server(_payload(mode="strict")))

    @pytest.mark.parametrize("mode", ["strict", "balanced", "permissive"])
    def test_agent_is_blocked_by_our_own_exception_in_every_mode(self, mode):
        order = _make_order(token=_agent_token())
        order._trusteed_resolve_payment_method = _boom
        with pytest.raises(ValidationError, match="CEL-INTERNAL-ERROR") as exc:
            _enforce(order, _Server(_payload(mode=mode)))
        assert "R001" not in str(exc.value)

    @pytest.mark.parametrize("mode", ["strict", "balanced", "permissive"])
    def test_verifier_crash_blocks_an_agent_in_every_mode_without_blaming_a_rule(self, mode):
        with patch.object(_verifier_mod, "verify_agent_token", side_effect=_boom):
            with pytest.raises(ValidationError, match="CEL-INTERNAL-ERROR") as exc:
                _enforce(_make_order(token=_agent_token()), _Server(_payload(mode=mode)))
        assert "R001" not in str(exc.value)

    @pytest.mark.parametrize("mode", ["strict", "balanced", "permissive"])
    def test_headless_agent_never_reaches_the_verifier_so_its_crash_cannot_open_it(self, mode):
        # Task 27a (M3): sustituye a ``test_headless_verifier_crash_blocks_…``;
        # el create con token en 'sale' se rechaza antes de verificar nada.
        g, p = _net(_Server(_payload(mode=mode)))
        with g, p, patch.object(_verifier_mod, "verify_agent_token", side_effect=_boom):
            with pytest.raises(ValidationError, match="CEL-HEADLESS-CONFIRM-REQUIRED"):
                _make_order()._trusteed_cel_enforce_vals({"company_id": 1, "state": "sale", "x_trusteed_agent_token": _agent_token()})

    def test_a_rule_block_is_not_swallowed_by_the_guard(self):
        with pytest.raises(ValidationError, match="merchant_policy"):
            _enforce(_make_order(token=None, amount_total=180.0), _Server(_payload(), evaluate="down"))


# ── Doble evaluación action_confirm → write ────────────────────────────────


class TestSingleEvaluationPerConfirmation:
    def test_action_confirm_evaluates_once_and_the_legit_token_is_not_a_replay(self):
        server = _Server(_payload(mode="strict"))
        order = _make_order(token=_agent_token())
        g, p = _net(server)
        with g, p:
            assert order.action_confirm() is True
        assert server.unexpected == []
        assert len(server.posts(_NONCE_URL)) == 1
        assert len(server.posts(_EVALUATE_URL)) == 1
        state_writes = [v for v, _c in order.base_writes if v.get("state") == "sale"]
        assert state_writes == [{"state": "sale"}]

    def test_a_plain_write_to_sale_is_still_evaluated(self):
        server = _Server(_payload(kill_switch=True))
        order = _make_order(token=_agent_token())
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="kill_switch"):
            order.write({"state": "sale"})

    def test_a_forged_marker_in_the_context_does_not_skip_the_evaluation(self):
        server = _Server(_payload(kill_switch=True))
        order = _make_order(token=_agent_token(), context={"trusteed_cel_evaluated_orders": [7]})
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="kill_switch"):
            order.write({"state": "sale"})


# ── x_trusteed_agent_did no se acepta por RPC ──────────────────────────────


class TestAgentDidNotWritableByRpc:
    def test_write_drops_the_did(self):
        order = _make_order()
        order.write({"x_trusteed_agent_did": "did:web:someone-else.example.com", "note": "x"})
        assert order.base_writes[0][0] == {"note": "x"}

    def test_create_drops_the_did(self):
        model = _make_order()
        model.create([{"x_trusteed_agent_did": "did:web:someone-else.example.com", "name": "SO1"}])
        assert model.base_creates == [[{"name": "SO1"}]]

    def test_a_json_context_key_cannot_forge_the_internal_permission(self):
        order = _make_order(context={"trusteed_internal_agent_did_write": True})
        order.write({"x_trusteed_agent_did": "did:web:someone-else.example.com"})
        assert order.base_writes[0][0] == {}

    def test_the_addon_internal_write_keeps_it(self):
        server = _Server(_payload())
        order = _make_order(token=_agent_token())
        _enforce(order, server)
        assert _did_writes(order) == [{"x_trusteed_agent_did": _AGENT_DID}]


# ── Payload: endpointClass sólo si agéntico ────────────────────────────────


class TestPayload:
    def test_organic_payload_has_no_endpoint_class_and_the_same_keys(self):
        server = _Server(_payload())
        _enforce(_make_order(token=None), server)
        body = server.posts(_EVALUATE_URL)[0][3]
        assert list(body.keys()) == ["merchantId", "agentId", "orderContext", "platform", "installationId", "timestamp"]
        assert body["agentId"] is None
        assert body["installationId"] == _INSTALLATION

    def test_agentic_payload_declares_the_agentic_endpoint(self):
        server = _Server(_payload())
        _enforce(_make_order(token=_agent_token()), server)
        body = server.posts(_EVALUATE_URL)[0][3]
        assert body["agentId"] == _AGENT_DID
        assert body["endpointClass"] == "agentic_endpoint"
        assert list(body.keys())[-1] == "endpointClass"


# ── Revisión final (Task 27a): el contexto de un cliente RPC no decide nada ──
# Un cliente de execute_kw controla el ``context`` entero. Ninguna clave suya
# puede cambiar lo que se manda a las reglas, quién es el agente ni si se evalúa.

_FORGED_SIGNALS = {
    "_stored_value_cents": "0",
    "_product_categories": "",
    "_agent_revoked": "false",
    "_agent_token_replay": "false",
}


def _evaluate_attrs(server):
    return server.posts(_EVALUATE_URL)[0][3]["orderContext"].get("cartAttributes", {})


class TestClientContextCannotForgeRuleSignals:
    def test_forged_nonce_attrs_do_not_reach_the_rules_with_an_accepted_nonce(self):
        server = _Server(_payload())
        order = _make_order(token=_agent_token(), context={"trusteed_nonce_attrs": dict(_FORGED_SIGNALS)})
        _enforce(order, server)
        attrs = _evaluate_attrs(server)
        for key in _FORGED_SIGNALS:
            assert key not in attrs, key

    def test_forged_nonce_attrs_do_not_reach_the_rules_without_a_token(self):
        server = _Server(_payload())
        order = _make_order(token=None, context={"trusteed_nonce_attrs": dict(_FORGED_SIGNALS)})
        _enforce(order, server)
        attrs = _evaluate_attrs(server)
        for key in _FORGED_SIGNALS:
            assert key not in attrs, key

    def test_forged_nonce_attrs_cannot_overwrite_a_computed_signal(self):
        server = _Server(_payload())
        order = _make_order(token=None, context={"trusteed_nonce_attrs": {"_shipping_po_box": "false"}})
        order.partner_shipping_id = SimpleNamespace(street="P.O. Box 12", street2="")
        _enforce(order, server)
        assert _evaluate_attrs(server)["_shipping_po_box"] == "true"

    def test_the_forged_context_does_not_reach_the_rules_through_action_confirm(self):
        server = _Server(_payload())
        order = _make_order(token=_agent_token(), context={"trusteed_nonce_attrs": dict(_FORGED_SIGNALS)})
        g, p = _net(server)
        with g, p:
            order.action_confirm()
        attrs = _evaluate_attrs(server)
        for key in _FORGED_SIGNALS:
            assert key not in attrs, key

    def test_the_addons_own_replay_flag_still_reaches_the_rules(self):
        server = _Server(_payload(mode="balanced"), nonce=409)
        order = _make_order(token=_agent_token(), context={"trusteed_nonce_attrs": {"_agent_token_replay": "false"}})
        _enforce(order, server)
        assert _evaluate_attrs(server)["_agent_token_replay"] == "true"

    def test_layer2_takes_the_nonce_attrs_as_an_argument_only(self):
        server = _Server(_payload())
        order = _make_order(token=None, context={"trusteed_nonce_attrs": dict(_FORGED_SIGNALS)})
        g, p = _net(server)
        with g, p:
            order._trusteed_cel_call_layer2(None, None, _payload(), "ev-x", nonce_attrs={"_agent_token_replay": "true"})
        attrs = _evaluate_attrs(server)
        assert attrs["_agent_token_replay"] == "true"
        assert "_stored_value_cents" not in attrs


# ── x_trusteed_agent_did tampoco entra por ``default_*`` del contexto ──────


class TestAgentDidNotWritableThroughContextDefaults:
    def test_create_with_a_default_did_in_the_context_drops_it(self):
        model = _make_order(context={"default_x_trusteed_agent_did": "did:web:victim.example.com"})
        model.create([{"name": "SO1"}])
        assert model.base_creates == [[{"name": "SO1"}]]

    def test_create_keeps_the_rest_of_the_context(self):
        model = _make_order(context={"default_x_trusteed_agent_did": "did:web:v.example", "default_note": "n", "lang": "es_ES"})
        model.create([{"name": "SO1"}])
        assert model.base_creates == [[{"name": "SO1", "note": "n"}]]

    def test_a_json_true_marker_does_not_allow_the_default(self):
        model = _make_order(context={
            "default_x_trusteed_agent_did": "did:web:victim.example.com",
            "trusteed_internal_agent_did_write": True,
        })
        model.create([{"name": "SO1"}])
        assert model.base_creates == [[{"name": "SO1"}]]

    def test_the_internal_marker_keeps_the_default(self):
        internal_did_write_context = sys.modules[f"{_MODELS_PKG}.agent_did_guard"].internal_did_write_context
        model = _make_order(context={**internal_did_write_context(), "default_x_trusteed_agent_did": "did:web:agent.example.com"})
        model.create([{"name": "SO1"}])
        assert model.base_creates[0][0]["x_trusteed_agent_did"] == "did:web:agent.example.com"

    def test_a_default_did_plus_a_vals_did_are_both_dropped(self):
        model = _make_order(context={"default_x_trusteed_agent_did": "did:web:victim.example.com"})
        model.create([{"name": "SO1", "x_trusteed_agent_did": "did:web:other.example.com"}])
        assert model.base_creates == [[{"name": "SO1"}]]

    def test_the_context_of_the_caller_object_is_not_mutated(self):
        ctx = {"default_x_trusteed_agent_did": "did:web:victim.example.com"}
        model = _make_order(context=ctx)
        model.create([{"name": "SO1"}])
        assert model.env.context == ctx


# ── create headless: ``default_*`` y token en un create con state=sale ─────


class TestHeadlessCreateWithToken:
    @pytest.mark.parametrize("mode", ["strict", "balanced", "permissive"])
    def test_agent_with_a_token_creating_in_sale_is_told_to_use_action_confirm(self, mode):
        server = _Server(_payload(mode=mode))
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="CEL-HEADLESS-CONFIRM-REQUIRED") as exc:
            _make_order().create([{"company_id": 1, "state": "sale", "x_trusteed_agent_token": _agent_token(order_id="pending", amount="")}])
        assert "R001" not in str(exc.value)
        assert server.posts(_NONCE_URL) == [] and server.posts(_EVALUATE_URL) == []

    def test_the_token_is_not_consumed_by_the_rejection(self):
        server = _Server(_payload())
        model = _make_order()
        g, p = _net(server)
        with g, p, patch.object(_verifier_mod, "verify_agent_token", side_effect=AssertionError("no debe verificarse")):
            with pytest.raises(ValidationError, match="CEL-HEADLESS-CONFIRM-REQUIRED"):
                model.create([{"company_id": 1, "state": "sale", "x_trusteed_agent_token": _agent_token()}])
        assert model.base_creates == []

    def test_a_token_in_the_context_is_rejected_too(self):
        g, p = _net(_Server(_payload()))
        with g, p, pytest.raises(ValidationError, match="CEL-HEADLESS-CONFIRM-REQUIRED"):
            _make_order(context={"trusteed_agent_token": _agent_token()}).create([{"company_id": 1, "state": "sale"}])

    def test_default_state_sale_in_the_context_is_evaluated_like_vals(self):
        g, p = _net(_Server(_payload()))
        with g, p, pytest.raises(ValidationError, match="CEL-HEADLESS-CONFIRM-REQUIRED"):
            _make_order(context={"default_state": "sale"}).create(
                [{"company_id": 1, "x_trusteed_agent_token": _agent_token()}]
            )

    def test_default_state_and_default_token_in_the_context_are_rejected(self):
        g, p = _net(_Server(_payload()))
        with g, p, pytest.raises(ValidationError, match="CEL-HEADLESS-CONFIRM-REQUIRED"):
            _make_order(context={"default_state": "sale", "default_x_trusteed_agent_token": _agent_token()}).create(
                [{"company_id": 1, "name": "SO1"}]
            )

    def test_default_state_sale_still_stops_an_agent_with_the_kill_switch(self):
        g, p = _net(_Server(_payload(kill_switch=True)))
        with g, p, pytest.raises(ValidationError, match="kill_switch"):
            _make_order(context={"default_state": "sale"}).create([{"company_id": 1, "x_trusteed_agent_token": "a.b.c"}])

    def test_a_human_create_in_sale_is_never_blocked(self):
        g, p = _net(_Server(_payload(mode="strict")))
        model = _make_order()
        with g, p:
            model.create([{"state": "sale", "name": "SO1"}])
        assert model.base_creates == [[{"state": "sale", "name": "SO1"}]]

    def test_a_draft_create_with_a_token_is_not_evaluated_and_action_confirm_binds_the_real_order(self):
        server = _Server(_payload(mode="strict"))
        order = _make_order(token=_agent_token(order_id="7", amount="50.0"))
        g, p = _net(server)
        with g, p:
            order.create([{"name": "SO1", "x_trusteed_agent_token": order.x_trusteed_agent_token}])
            assert server.calls == []
            assert order.action_confirm() is True
        assert len(server.posts(_NONCE_URL)) == 1 and len(server.posts(_EVALUATE_URL)) == 1

    def test_a_token_bound_to_pending_does_not_pass_action_confirm(self):
        server = _Server(_payload(mode="strict"))
        order = _make_order(token=_agent_token(order_id="pending", amount=""))
        g, p = _net(server)
        with g, p, pytest.raises(ValidationError, match="R001"):
            order.action_confirm()


# ── B5: el identificador de un solo uso es `nonce`; `jti` sólo es alias ─────

_NONCE_OK = "canonical-nonce-0123456789"  # 26 caracteres


def _consumed(server):
    return [body["nonce"] for _, _, _, body in server.posts(_NONCE_URL)]


class TestSingleUseIdentifierIsTheCanonicalNonce:
    def test_canonical_token_without_jti_is_valid_and_attributed(self):
        token = _agent_token()
        assert "jti" not in json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==="))
        order = _make_order(token=token)
        server = _Server(_payload())
        _enforce(order, server)
        assert len(_consumed(server)) == 1
        assert _did_writes(order) == [{"x_trusteed_agent_did": _AGENT_DID}]

    def test_replay_of_the_canonical_token_is_a_replay_in_strict(self):
        server = _Server(_payload(mode="strict"), nonce=409)
        with pytest.raises(ValidationError, match="R002"):
            _enforce(_make_order(token=_agent_token()), server)
        assert len(_consumed(server)) == 1

    def test_second_use_of_the_same_canonical_token_is_rejected_offline(self):
        token = _agent_token()
        _enforce(_make_order(token=token), _Server(_payload()))
        server = _Server(_payload())
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=token), server)
        assert _consumed(server) == []

    def test_jti_alone_is_accepted_as_legacy_alias_and_is_what_gets_consumed(self):
        token = _agent_token(claims={"nonce": _DROP, "jti": _NONCE_OK})
        server = _Server(_payload())
        _enforce(_make_order(token=token), server)
        assert _consumed(server) == [_NONCE_OK]

    def test_neither_nonce_nor_jti_is_invalid(self):
        token = _agent_token(claims={"nonce": _DROP})
        server = _Server(_payload())
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=token), server)
        assert _consumed(server) == []

    @pytest.mark.parametrize("length", [15, 65])
    def test_nonce_out_of_range_is_invalid(self, length):
        token = _agent_token(claims={"nonce": "n" * length})
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=token), _Server(_payload()))

    @pytest.mark.parametrize("length", [15, 65])
    def test_jti_alias_out_of_range_is_invalid(self, length):
        token = _agent_token(claims={"nonce": _DROP, "jti": "j" * length})
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=token), _Server(_payload()))

    @pytest.mark.parametrize("length", [16, 64])
    def test_limits_are_inclusive(self, length):
        token = _agent_token(claims={"nonce": "n" * length})
        server = _Server(_payload())
        _enforce(_make_order(token=token), server)
        assert _consumed(server) == ["n" * length]

    def test_malformed_nonce_is_not_rescued_by_a_valid_jti(self):
        token = _agent_token(claims={"nonce": "short", "jti": _NONCE_OK})
        server = _Server(_payload())
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=token), server)
        assert _consumed(server) == []

    @pytest.mark.parametrize("bad", ["a" * 15 + "+", "nonce with spaces 0123", "nonce.with.dots.0123456"])
    def test_identifier_outside_the_server_charset_is_invalid(self, bad):
        # El endpoint de consumo del servidor exige ^[A-Za-z0-9_-]{16,128}$.
        token = _agent_token(claims={"nonce": bad})
        server = _Server(_payload())
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=token), server)
        assert _consumed(server) == []

    def test_both_present_and_different_uses_the_nonce(self):
        token = _agent_token(claims={"nonce": _NONCE_OK, "jti": "other-jti-0123456789ab"})
        server = _Server(_payload())
        _enforce(_make_order(token=token), server)
        assert _consumed(server) == [_NONCE_OK]

    def test_the_identifier_comes_from_the_signed_payload(self):
        # Un JWS cuyo payload se cambió tras firmar (nonce nuevo) no verifica.
        token = _agent_token()
        h, p, sig = token.split(".")
        body = json.loads(base64.urlsafe_b64decode(p + "==="))
        body["nonce"] = _NONCE_OK
        forged = f"{h}.{_b64u(json.dumps(body, separators=(',', ':')).encode())}.{sig}"
        server = _Server(_payload())
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=forged), server)
        assert _consumed(server) == []


def _iat_token(iat_offset: int, *, window: int = 120, claims=None) -> str:
    """Token canónico con `iat = ahora + iat_offset` y `exp = iat + window`."""
    iat = int(time.time()) + iat_offset
    return _agent_token(claims={"iat": iat, "exp": iat + window, **(claims or {})})


class TestIatPolicyMatchesWooCommerce:
    """L2 (Task 43b): `iat` futuro con tolerancia de 30 s, antigüedad máxima 300 s
    (decisión 48: el mismo número que los demás verificadores) y
    ventana exp-iat ≤ 330 s, como `class-token-verifier.php` de WooCommerce."""

    def test_a_fresh_token_is_valid(self):
        server = _Server(_payload())
        _enforce(_make_order(token=_iat_token(0)), server)
        assert len(_consumed(server)) == 1

    def test_an_iat_inside_the_clock_tolerance_is_valid(self):
        server = _Server(_payload())
        _enforce(_make_order(token=_iat_token(20)), server)
        assert len(_consumed(server)) == 1

    @pytest.mark.parametrize("offset", [45, 3600])
    def test_a_future_iat_is_invalid_and_burns_no_nonce(self, offset):
        server = _Server(_payload())
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=_iat_token(offset)), server)
        assert _consumed(server) == []

    def test_a_token_still_inside_the_max_age_is_valid(self):
        server = _Server(_payload())
        _enforce(_make_order(token=_iat_token(-100)), server)
        assert len(_consumed(server)) == 1

    @pytest.mark.parametrize("age", [301, 320])
    def test_a_token_older_than_the_max_age_is_invalid_even_if_exp_is_still_valid(self, age):
        # exp = iat + 330: dentro de la ventana y sin caducar (gracia de 30 s),
        # pero demasiado viejo. 301 s es el primer valor fuera del tope de 300.
        server = _Server(_payload())
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=_iat_token(-age, window=330)), server)
        assert _consumed(server) == []

    @pytest.mark.parametrize("age", [121, 200, 299])
    def test_ages_between_the_old_cap_and_the_contract_are_valid(self, age):
        # Decisión 48: con 120 s estos tokens, legítimos (los agentes emiten con
        # hasta 300 s de vida), se rechazaban. `age` sin fracción de segundo
        # (la edad real es float: 300 exactos sólo valen en el instante entero).
        server = _Server(_payload())
        _enforce(_make_order(token=_iat_token(-age, window=300)), server)
        assert len(_consumed(server)) == 1

    def test_the_max_age_constant_is_the_contract_300(self):
        assert _verifier_mod._MAX_TOKEN_AGE_SECONDS == 300

    def test_a_missing_iat_is_invalid(self):
        server = _Server(_payload())
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=_agent_token(claims={"iat": _DROP})), server)
        assert _consumed(server) == []

    def test_the_exp_iat_window_is_still_capped_at_330(self):
        server = _Server(_payload())
        _enforce(_make_order(token=_iat_token(0, window=330)), server)
        assert len(_consumed(server)) == 1
        server = _Server(_payload())
        with pytest.raises(ValidationError, match="R001"):
            _enforce(_make_order(token=_iat_token(0, window=340)), server)
        assert _consumed(server) == []

    def test_the_canonical_generator_shape_is_untouched(self):
        payload = json.loads(base64.urlsafe_b64decode(_agent_token().split(".")[1] + "==="))
        assert re.fullmatch(r"[0-9a-f]{32}", payload["nonce"])
        assert "jti" not in payload
        assert payload["exp"] - payload["iat"] == 120
