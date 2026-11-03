"""Snapshot comprimido (``zip: "DEF"``, payload > 50 KB) — plan 04, Task 24.

Consume la sección ``compressedSnapshot`` del fixture compartido: el JWS lo
produjeron el builder y el firmador REALES del servidor, con claves derivadas
de etiquetas públicas (semilla Ed25519 = SHA-256 de la etiqueta).

- Los 12 vectores pasan por ``verify_jws_snapshot_result`` REAL (firma primero,
  con el JWKS del fixture servido por la red simulada) y los 11 cuya firma
  verifica, por ``snapshot_payload_decoder.decode`` con su motivo (conteos
  asertados).
- El flujo real de ``SaleOrderEnforcement`` (arnés de
  ``test_platform_flow_wiring``) con el snapshot comprimido servido: el agente
  con token no queda retenido por el tamaño, su token verifica contra el
  resolver de 300 DIDs y su ``jti`` se consume, la persona pasa, la válvula lee
  el tope R030 del payload comprimido, y la bomba se maneja sin reservar más
  que el tope.

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_compressed_snapshot.py \\
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import base64
import hashlib
import importlib.util
import json
import sys
import time
import tracemalloc
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from odoo.exceptions import ValidationError

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("t24_wiring_harness", _HERE / "test_platform_flow_wiring.py")
_w = importlib.util.module_from_spec(_spec)
sys.modules["t24_wiring_harness"] = _w
_spec.loader.exec_module(_w)

_decoder = _w._load_module(
    "models/snapshot_payload_decoder.py", f"{_w._MODELS_PKG}.snapshot_payload_decoder", _w._MODELS_PKG
)
_snapshot_mod = _w._snapshot_mod
_verifier_mod = _w._verifier_mod

_SECTION = _w._FIXTURE["compressedSnapshot"]
_VECTORS = {v["id"]: v for v in _SECTION["vectors"]}
_AGENT = _SECTION["agent"]
_AGENT_KEY = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(_AGENT["seedLabel"].encode("utf-8")).digest())
_MERCHANT = _VECTORS["valid.deflated-by-real-signer"]["expected"]["merchantId"]
assert _MERCHANT == _w._MERCHANT  # el arnés sirve y firma para el mismo comercio


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class _CompressedServer(_w._Server):
    """La API simulada del arnés, sirviendo un vector del fixture y su JWKS."""

    def __init__(self, vector_id, **kw):
        super().__init__(payload=None, **kw)
        self.vector_id = vector_id

    def _jws(self):
        return _VECTORS[self.vector_id]["jws"]

    def get(self, url, headers=None, timeout=None, verify=None, allow_redirects=None, stream=None):
        if url == _w._JWKS_URL:
            self.calls.append(("GET", url, dict(headers or {}), None))
            return _w._resp(200, body=_SECTION["jwks"])
        return super().get(url, headers=headers, timeout=timeout, verify=verify, allow_redirects=allow_redirects,
                           stream=stream)


_SEQ = [0]


def _token(*, key=_AGENT_KEY, order_id="7", amount="50.0") -> str:
    _SEQ[0] += 1
    now = int(time.time())
    payload = {
        "aud": "trusteed",
        "merchantId": _MERCHANT,
        "iss": _AGENT["did"],
        "iat": now,
        "exp": now + 120,
        "checkoutIntentHash": _w._intent_hash(order_id, amount),
        "nonce": f"nonce-t24-{_SEQ[0]:08d}-abcdef",
        "jti": f"jti-t24-{_SEQ[0]:08d}-abcdef",
    }
    return _w._sign_jws(payload, key, kid=_AGENT["kid"], typ="trusteed-agent-token+jwt")


@pytest.fixture(autouse=True)
def _clear_caches():
    for cache in (_snapshot_mod._SNAPSHOT_CACHE, _snapshot_mod._JWKS_CACHE, _snapshot_mod._JWKS_EXPIRE,
                  _snapshot_mod._NEGATIVE_RETRY, _verifier_mod._NONCE_CACHE):
        cache.clear()
    yield
    for cache in (_snapshot_mod._SNAPSHOT_CACHE, _snapshot_mod._JWKS_CACHE, _snapshot_mod._JWKS_EXPIRE,
                  _snapshot_mod._NEGATIVE_RETRY, _verifier_mod._NONCE_CACHE):
        cache.clear()


# ── Conformidad ─────────────────────────────────────────────────────────────


def test_the_fixture_cap_is_the_port_cap():
    assert _decoder.MAX_INFLATED_BYTES == _SECTION["maxInflatedBytes"]
    assert _decoder.ZIP_DEFLATE == "DEF"


def test_every_vector_through_the_real_verifier():
    run = 0
    for v in _SECTION["vectors"]:
        _snapshot_mod._JWKS_CACHE.clear()
        _snapshot_mod._JWKS_EXPIRE.clear()
        server = _CompressedServer(v["id"])
        with patch("requests.get", side_effect=server.get):
            status, payload = _snapshot_mod.verify_jws_snapshot_result(v["jws"], _w._API)
        assert status == v["expected"]["status"], v["id"]
        if status == "ok":
            assert payload["ruleSetVersion"] == v["expected"]["ruleSetVersion"], v["id"]
            assert payload["merchantId"] == v["expected"]["merchantId"], v["id"]
            assert len(payload["agentDidResolver"]) == v["expected"]["agentDidResolverCount"], v["id"]
        else:
            assert payload is None, v["id"]
        run += 1
    assert run == 12


def test_every_signature_valid_vector_through_the_decoder_with_its_reason():
    run = 0
    for v in _SECTION["vectors"]:
        if v["expected"]["status"] == "tampered":
            continue
        h, p, _s = v["jws"].split(".")
        reason, payload = _decoder.decode(json.loads(_b64d(h)), _b64d(p))
        if v["expected"]["status"] == "ok":
            assert reason == _decoder.OK, v["id"]
            assert payload["ruleSetVersion"] == v["expected"]["ruleSetVersion"], v["id"]
        else:
            assert reason == v["expected"]["reason"], v["id"]
            assert payload is None, v["id"]
        run += 1
    assert run == 11


def test_the_bomb_is_rejected_without_reserving_more_than_the_cap():
    h, p, _s = _VECTORS["bomb.over-cap"]["jws"].split(".")
    header, body = json.loads(_b64d(h)), _b64d(p)
    tracemalloc.start()
    try:
        reason, _payload = _decoder.decode(header, body)
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert reason == _decoder.INFLATE_OVER_CAP
    assert peak < 6 * 1024 * 1024, "la bomba (8 MiB) no se infla entera"


# ── Flujo real de SaleOrderEnforcement ─────────────────────────────────────


@pytest.mark.parametrize("local", [None, "strict", "balanced", "permissive"])
def test_a_legitimate_agent_is_not_held_by_the_size_and_its_token_verifies(local):
    server = _CompressedServer("valid.deflated-by-real-signer")
    order = _w._make_order(token=_token(), mapping=None if local is None else _w._mapping(local))
    _w._enforce(order, server)  # antes: zip DEF = manipulado ⇒ R000-SNAPSHOT-INVALID
    nonce_posts = server.posts(_w._NONCE_URL)
    assert len(nonce_posts) == 1, "el token verificó: su jti se consume"
    assert nonce_posts[0][3]["agentId"] == _AGENT["did"]
    assert server.posts(_w._EVALUATE_URL)[0][3]["agentId"] == _AGENT["did"]


def test_a_forged_token_is_still_rejected_against_the_compressed_resolver():
    server = _CompressedServer("valid.deflated-by-real-signer")
    order = _w._make_order(token=_token(key=Ed25519PrivateKey.generate()))
    with pytest.raises(ValidationError, match="R001"):
        _w._enforce(order, server)


def test_a_person_passes_and_the_valve_reads_the_cap_of_the_compressed_payload():
    _w._enforce(_w._make_order(token=None), _CompressedServer("valid.deflated-by-real-signer"))
    server = _CompressedServer("valid.deflated-by-real-signer", evaluate="down")
    with pytest.raises(ValidationError, match="merchant_policy"):
        _w._enforce(_w._make_order(token=None, amount_total=6000.0), server)


@pytest.mark.parametrize("local", [None, "strict", "balanced", "permissive"])
def test_the_bomb_holds_an_agent_with_a_token_and_lets_a_person_through(local):
    server = _CompressedServer("bomb.over-cap")
    order = _w._make_order(token=_token(), mapping=None if local is None else _w._mapping(local))
    with pytest.raises(ValidationError, match="R000-SNAPSHOT-UNAVAILABLE"):
        _w._enforce(order, server)
    assert server.posts(_w._NONCE_URL) == [] and server.posts(_w._EVALUATE_URL) == []
    _w._enforce(_w._make_order(token=None), _CompressedServer("bomb.over-cap"))


def test_an_unknown_zip_is_unavailable_and_a_bad_signature_is_tampered():
    with pytest.raises(ValidationError, match="R000-SNAPSHOT-UNAVAILABLE"):
        _w._enforce(_w._make_order(token=_token()), _CompressedServer("zip.unknown"))
    _snapshot_mod._SNAPSHOT_CACHE.clear()
    with pytest.raises(ValidationError, match="R000-SNAPSHOT-INVALID"):
        _w._enforce(_w._make_order(token=_token()), _CompressedServer("bomb.bad-signature"))
