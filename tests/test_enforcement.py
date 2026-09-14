"""pytest tests — spec-048 CEL enforcement functions (Odoo 18, standalone).

Tests verify_jws_snapshot, verify_agent_token, pull_snapshot, and
validate_api_base without requiring a live Odoo runtime.  All crypto
uses the real cryptography library for authentic Ed25519 key material.

Run: pytest packages/odoo-addon-trusteed/tests/test_enforcement.py -v
"""

import base64
import importlib.util
import json
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# ---------------------------------------------------------------------------
# Odoo stub — install before importing any addon module
# ---------------------------------------------------------------------------

def _install_odoo_stubs() -> None:
    """Inject minimal odoo.* stubs so model imports resolve without Odoo."""

    class _AbstractModel:
        _name = ""
        _description = ""

    odoo_mod = types.ModuleType("odoo")
    odoo_api = types.ModuleType("odoo.api")
    odoo_fields = types.ModuleType("odoo.fields")
    odoo_models = types.ModuleType("odoo.models")

    odoo_api.model = lambda fn: fn
    odoo_models.AbstractModel = _AbstractModel

    odoo_mod.api = odoo_api
    odoo_mod.fields = odoo_fields
    odoo_mod.models = odoo_models

    for name, mod in [
        ("odoo", odoo_mod),
        ("odoo.api", odoo_api),
        ("odoo.fields", odoo_fields),
        ("odoo.models", odoo_models),
    ]:
        sys.modules.setdefault(name, mod)


_install_odoo_stubs()

# ---------------------------------------------------------------------------
# Load source modules via importlib so relative imports resolve correctly.
#
# enforcement_snapshot.py uses ``from ..utils.ssrf import validate_api_base``
# which means its __package__ must be "<root>.models" so that ".." resolves
# to "<root>" and "..utils.ssrf" resolves to "<root>.utils.ssrf".
# ---------------------------------------------------------------------------

_ADDON_ROOT = Path(__file__).parent.parent

# Root package alias for the addon — all sub-packages live underneath it.
_ROOT = "addon_t088"
_MODELS_PKG = f"{_ROOT}.models"
_UTILS_PKG = f"{_ROOT}.utils"


def _ensure_pkg(key: str) -> types.ModuleType:
    if key not in sys.modules:
        sys.modules[key] = types.ModuleType(key)
    return sys.modules[key]


def _load_module(relative_path: str, module_key: str, package: str) -> types.ModuleType:
    """Load a source file as module_key with __package__ = package."""
    if module_key in sys.modules:
        return sys.modules[module_key]
    _ensure_pkg(package)
    spec = importlib.util.spec_from_file_location(
        module_key, _ADDON_ROOT / relative_path
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = package
    sys.modules[module_key] = mod
    spec.loader.exec_module(mod)
    return mod


# Create root + sub-package stubs first
_ensure_pkg(_ROOT)
_ensure_pkg(_MODELS_PKG)
_ensure_pkg(_UTILS_PKG)

# Load utils.ssrf (no Odoo deps, no relative imports)
_ssrf_mod = _load_module("utils/ssrf.py", f"{_UTILS_PKG}.ssrf", _UTILS_PKG)
# Make it accessible as _ROOT.utils.ssrf for relative resolution
sys.modules[_UTILS_PKG].ssrf = _ssrf_mod  # type: ignore[attr-defined]
# Also register under the plain key that the relative import will synthesise
# when __package__ = "addon_t088.models": ``from ..utils.ssrf`` → "addon_t088.utils.ssrf"
sys.modules.setdefault(f"{_ROOT}.utils.ssrf", _ssrf_mod)

# Load enforcement_token_verifier (stdlib + cryptography only, no Odoo deps)
_verifier_mod = _load_module(
    "models/enforcement_token_verifier.py",
    f"{_MODELS_PKG}.enforcement_token_verifier",
    _MODELS_PKG,
)

# Load enforcement_snapshot (__package__=_MODELS_PKG so ``..utils.ssrf`` → _ROOT.utils.ssrf)
_snapshot_mod = _load_module(
    "models/enforcement_snapshot.py",
    f"{_MODELS_PKG}.enforcement_snapshot",
    _MODELS_PKG,
)

verify_jws_snapshot = _snapshot_mod.verify_jws_snapshot
pull_snapshot = _snapshot_mod.pull_snapshot
base64url_encode = _snapshot_mod.base64url_encode
base64url_decode = _snapshot_mod.base64url_decode
EnforcementSnapshotService = _snapshot_mod.EnforcementSnapshotService

verify_agent_token = _verifier_mod.verify_agent_token
compute_intent_hash = _verifier_mod.compute_intent_hash

validate_api_base = _ssrf_mod.validate_api_base

# ---------------------------------------------------------------------------
# Shared key material (module-level fixture, generated once per session)
# ---------------------------------------------------------------------------

_SK: Ed25519PrivateKey = Ed25519PrivateKey.generate()
_PK = _SK.public_key()
_PUB_BYTES: bytes = _PK.public_bytes_raw()  # 32 bytes
_PUB_B64URL: str = base64url_encode(_PUB_BYTES)
_KID = "test-key-001"

# Agent keypair (separate from snapshot keypair for isolation)
_AGENT_SK: Ed25519PrivateKey = Ed25519PrivateKey.generate()
_AGENT_PK = _AGENT_SK.public_key()
_AGENT_PUB_BYTES: bytes = _AGENT_PK.public_bytes_raw()
_AGENT_PUB_B64URL: str = base64url_encode(_AGENT_PUB_BYTES)
_AGENT_DID = "did:key:agent-test-001"
_AGENT_KID = f"{_AGENT_DID}#key-1"

_MERCHANT_ID = "m-test-merchant"
_API_BASE = "https://api.trusteed.xyz"


# ---------------------------------------------------------------------------
# JWS helpers
# ---------------------------------------------------------------------------

def _sign_jws(payload: dict, private_key: Ed25519PrivateKey, header: dict) -> str:
    """Produce a real EdDSA JWS Compact Serialization."""
    header_b64 = base64url_encode(json.dumps(header, separators=(",", ":")).encode())
    body_b64 = base64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{body_b64}".encode("ascii")
    sig = private_key.sign(signing_input)
    return f"{header_b64}.{body_b64}.{base64url_encode(sig)}"


def _make_snapshot_jws(
    *,
    private_key: Ed25519PrivateKey = _SK,
    pub_b64url: str = _PUB_B64URL,
    kid: str = _KID,
    merchant_id: str = _MERCHANT_ID,
    kill_switch: bool = False,
    valid_offset: int = 300,
) -> str:
    """Build and sign a valid snapshot JWS."""
    now = int(time.time())
    payload = {
        "merchantId": merchant_id,
        "ruleSetVersion": "1.0",
        "validUntil": str(now + valid_offset),
        "killSwitch": kill_switch,
        "fallbackMode": "balanced",
        "rules": [{"id": "R001", "tier": 1}],
        "allowlistAgentIds": [],
        "agentDidResolver": {},
        "snapshotKeyAnchors": [{"kid": kid, "x": pub_b64url}],
    }
    header = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    return _sign_jws(payload, private_key, header)


def _make_agent_token_jws(
    *,
    private_key: Ed25519PrivateKey = _AGENT_SK,
    kid: str = _AGENT_KID,
    merchant_id: str = _MERCHANT_ID,
    checkout_intent_hash: str,
    nonce: str = "nonce-unique-001",
    exp_offset: int = 300,
    iat_offset: int = 0,
    jti: str = "fixturejti0123456789",
) -> str:
    """Build and sign a valid agent token JWS."""
    now = int(time.time())
    payload = {
        "iss": _AGENT_DID,
        "aud": "trusteed",
        "merchantId": merchant_id,
        "checkoutIntentHash": checkout_intent_hash,
        "nonce": nonce,
        "exp": now + exp_offset,
        "iat": now + iat_offset,
        # Spec-048 P2.8 — required by verify_agent_token (16-128 base64url chars).
        "jti": jti,
    }
    header = {"alg": "EdDSA", "typ": "trusteed-agent-token+jwt", "kid": kid}
    return _sign_jws(payload, private_key, header)


def _jwks_response_for(pub_b64url: str = _PUB_B64URL, kid: str = _KID) -> dict:
    """Minimal JWKS document with one Ed25519 key."""
    return {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "kid": kid,
                "x": pub_b64url,
            }
        ]
    }


# ---------------------------------------------------------------------------
# Tests — verify_jws_snapshot
# ---------------------------------------------------------------------------

class TestVerifyJwsSnapshot:
    """verify_jws_snapshot validates EdDSA signature and returns payload."""

    def test_valid_snapshot_returns_merchant_id(self):
        """A correctly signed snapshot JWS returns its payload with merchantId."""
        jws = _make_snapshot_jws()
        # Clear cache to prevent cross-test pollution from cached JWKS entries
        _snapshot_mod._JWKS_CACHE.clear()
        _snapshot_mod._JWKS_EXPIRE.clear()
        # Patch _fetch_jwks to return known public key without hitting network
        with patch.object(_snapshot_mod, "_fetch_jwks",
                          return_value={_KID: _PUB_BYTES}):
            result = verify_jws_snapshot(jws, _API_BASE)

        assert result is not None
        assert result["merchantId"] == _MERCHANT_ID
        assert result["killSwitch"] is False

    def test_invalid_signature_returns_none(self):
        """A JWS with a tampered signature byte must return None."""
        jws = _make_snapshot_jws()
        # Flip one byte in the signature to break it deterministically.
        # We XOR the raw signature bytes rather than flipping a base64 char,
        # because base64url encoding may tolerate certain last-char changes
        # (padding is stripped, so the last character may be unused bits).
        header_b64, payload_b64, sig_b64 = jws.split(".")
        sig_bytes = bytearray(base64url_decode(sig_b64))
        sig_bytes[0] ^= 0xFF  # flip the first byte
        corrupted_sig = base64url_encode(bytes(sig_bytes))
        bad_jws = f"{header_b64}.{payload_b64}.{corrupted_sig}"

        # Clear the JWKS cache so the patch is guaranteed to be called
        _snapshot_mod._JWKS_CACHE.clear()
        _snapshot_mod._JWKS_EXPIRE.clear()

        with patch.object(_snapshot_mod, "_fetch_jwks",
                          return_value={_KID: _PUB_BYTES}):
            result = verify_jws_snapshot(bad_jws, _API_BASE)

        assert result is None


# ---------------------------------------------------------------------------
# Tests — verify_agent_token
# ---------------------------------------------------------------------------

class TestVerifyAgentToken:
    """verify_agent_token enforces Ed25519 signature + JWT claims."""

    def _resolver(self) -> dict:
        return {_AGENT_DID: {"x": _AGENT_PUB_B64URL}}

    def _intent_hash(self, canonical: str = '{"amount":100}') -> str:
        return compute_intent_hash(canonical)

    def test_valid_token_returns_claims(self):
        """A correctly signed token with matching intentHash returns agentId."""
        intent_hash = self._intent_hash()
        jws = _make_agent_token_jws(
            checkout_intent_hash=intent_hash,
            nonce="nonce-valid-001",
        )
        result = verify_agent_token(jws, intent_hash, _MERCHANT_ID, self._resolver())

        assert result is not None
        assert result["agentId"] == _AGENT_DID

    def test_wrong_intent_hash_returns_none(self):
        """A token where checkoutIntentHash does not match computed hash returns None."""
        real_hash = self._intent_hash('{"amount":100}')
        wrong_hash = self._intent_hash('{"amount":999}')
        jws = _make_agent_token_jws(
            checkout_intent_hash=real_hash,
            nonce="nonce-wrong-hash-001",
        )
        result = verify_agent_token(jws, wrong_hash, _MERCHANT_ID, self._resolver())

        assert result is None

    def test_expired_token_returns_none(self):
        """A token with exp in the past (beyond clock skew) must return None."""
        intent_hash = self._intent_hash('{"amount":50}')
        # exp = now - 60, iat = now - 360 so window stays ≤ 330 s
        # But exp + skew(30) < now, so it is expired
        jws = _make_agent_token_jws(
            checkout_intent_hash=intent_hash,
            nonce="nonce-expired-001",
            exp_offset=-60,
            iat_offset=-360,
        )
        result = verify_agent_token(jws, intent_hash, _MERCHANT_ID, self._resolver())

        assert result is None


class TestVerifyAgentTokenMalformedInput:
    """GHSA-2j2x-5q52-g48m companion hardening (D2).

    Same defect *class* as the PrestaShop/WordPress report, different shape:
    a syntactically valid JWS whose header/payload decode to the wrong JSON
    TYPE (not the wrong signature) raised AttributeError/TypeError/ValueError
    instead of returning None. The caller (sale_order_enforcement.py) treats
    any exception here as *our* infrastructure failing and applies the
    merchant's fallback mode — the same fail-open class reached a different
    way. Every branch below must return None (an ordinary invalid token,
    forcing R001), never raise.
    """

    def _resolver(self) -> dict:
        return {_AGENT_DID: {"x": _AGENT_PUB_B64URL}}

    def _valid_payload(self, **overrides) -> dict:
        now = int(time.time())
        payload = {
            "iss": _AGENT_DID,
            "aud": "trusteed",
            "merchantId": _MERCHANT_ID,
            "checkoutIntentHash": "malformed-input-test-hash",
            "nonce": "nonce-malformed-input-001",
            "iat": now,
            "exp": now + 300,
            "jti": "fixturejti0123456789",
        }
        payload.update(overrides)
        return payload

    def test_header_decodes_to_a_list_returns_none(self):
        """A JWS whose header segment decodes to a JSON array, not an object."""
        header_b64 = base64url_encode(json.dumps([]).encode())
        payload_b64 = base64url_encode(json.dumps(self._valid_payload()).encode())
        sig_b64 = base64url_encode(b"\x00" * 64)
        jws = f"{header_b64}.{payload_b64}.{sig_b64}"

        result = verify_agent_token(jws, "malformed-input-test-hash", _MERCHANT_ID, self._resolver())

        assert result is None

    def test_payload_decodes_to_a_scalar_returns_none(self):
        """A JWS whose payload segment decodes to a bare number, not an object.

        Signed for real (not a dummy signature) — a scalar payload only
        exercises the `payload.get(...)` calls in Step 5+ if the token first
        clears Step 4's real Ed25519 verification.
        """
        header = {"alg": "EdDSA", "typ": "trusteed-agent-token+jwt", "kid": _AGENT_KID}
        header_b64 = base64url_encode(json.dumps(header).encode())
        payload_b64 = base64url_encode(json.dumps(123).encode())
        sig = _AGENT_SK.sign(f"{header_b64}.{payload_b64}".encode("ascii"))
        jws = f"{header_b64}.{payload_b64}.{base64url_encode(sig)}"

        result = verify_agent_token(jws, "x", _MERCHANT_ID, self._resolver())

        assert result is None

    def test_kid_non_string_returns_none(self):
        """A JWS whose header `kid` claim is a number instead of a string."""
        header = {"alg": "EdDSA", "typ": "trusteed-agent-token+jwt", "kid": 12345}
        header_b64 = base64url_encode(json.dumps(header).encode())
        payload_b64 = base64url_encode(json.dumps(self._valid_payload()).encode())
        sig_b64 = base64url_encode(b"\x00" * 64)
        jws = f"{header_b64}.{payload_b64}.{sig_b64}"

        result = verify_agent_token(jws, "malformed-input-test-hash", _MERCHANT_ID, self._resolver())

        assert result is None

    def test_resolver_entry_not_a_dict_returns_none(self):
        """agent_did_resolver[agent_did] is caller-supplied and may not be a dict."""
        intent_hash = compute_intent_hash('{"amount":1}')
        jws = _make_agent_token_jws(
            checkout_intent_hash=intent_hash,
            nonce="nonce-resolver-shape-001",
        )

        result = verify_agent_token(jws, intent_hash, _MERCHANT_ID, {_AGENT_DID: "not-a-dict"})

        assert result is None

    def test_exp_non_numeric_returns_none(self):
        """A real, validly-signed token whose `exp` claim is a non-numeric string."""
        header = {"alg": "EdDSA", "typ": "trusteed-agent-token+jwt", "kid": _AGENT_KID}
        payload = self._valid_payload(exp="not-a-number")
        jws = _sign_jws(payload, _AGENT_SK, header)

        result = verify_agent_token(jws, payload["checkoutIntentHash"], _MERCHANT_ID, self._resolver())

        assert result is None

    def test_nonce_non_string_returns_none(self):
        """A real, validly-signed token whose `nonce` claim is a number, not a string."""
        header = {"alg": "EdDSA", "typ": "trusteed-agent-token+jwt", "kid": _AGENT_KID}
        payload = self._valid_payload(nonce=12345)
        jws = _sign_jws(payload, _AGENT_SK, header)

        result = verify_agent_token(jws, payload["checkoutIntentHash"], _MERCHANT_ID, self._resolver())

        assert result is None


# ---------------------------------------------------------------------------
# Tests — pull_snapshot (network mocked)
# ---------------------------------------------------------------------------

class TestPullSnapshot:
    """pull_snapshot fetches from API and returns verified payload."""

    def test_pull_snapshot_returns_payload_on_200(self):
        """When the server responds 200 with a valid JWS, payload is returned."""
        # Clear any cached state for this merchant to force a real fetch
        cache_key = (_MERCHANT_ID, 1)
        _snapshot_mod._SNAPSHOT_CACHE.pop(cache_key, None)

        jws = _make_snapshot_jws()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = jws

        # Mock both requests.get and _fetch_jwks to avoid all network calls
        with patch.object(_snapshot_mod, "_fetch_jwks",
                          return_value={_KID: _PUB_BYTES}), \
             patch.object(_snapshot_mod.requests, "get", return_value=mock_resp):

            result = pull_snapshot(
                api_base=_API_BASE,
                merchant_id=_MERCHANT_ID,
                installation_id="install-001",
                hmac_secret="super-secret-hmac",
                company_id=1,
            )

        assert result is not None
        assert result["merchantId"] == _MERCHANT_ID

    def test_pull_snapshot_negotiates_application_jose(self):
        """Spec-048 FR-008: the request must send Accept: application/jose so
        the API returns a BARE JWS Compact body (read via resp.text), not a
        JSON envelope. Locks the contract against wire-format drift."""
        cache_key = (_MERCHANT_ID, 1)
        _snapshot_mod._SNAPSHOT_CACHE.pop(cache_key, None)

        jws = _make_snapshot_jws()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = jws

        captured = {}

        def _capture_get(url, headers=None, **kwargs):
            captured["headers"] = headers or {}
            return mock_resp

        with patch.object(_snapshot_mod, "_fetch_jwks",
                          return_value={_KID: _PUB_BYTES}), \
             patch.object(_snapshot_mod.requests, "get", side_effect=_capture_get):

            result = pull_snapshot(
                api_base=_API_BASE,
                merchant_id=_MERCHANT_ID,
                installation_id="install-001",
                hmac_secret="super-secret-hmac",
                company_id=1,
            )

        assert result is not None
        assert captured["headers"].get("Accept") == "application/jose"


# ---------------------------------------------------------------------------
# Tests — validate_api_base (SSRF guard)
# ---------------------------------------------------------------------------

class TestValidateApiBase:
    """validate_api_base blocks private / non-HTTPS URLs and allows trusteed."""

    def test_private_ip_192_168_is_blocked(self):
        """RFC-1918 addresses must return False."""
        assert validate_api_base("http://192.168.1.1/api") is False

    def test_https_api_trusteed_xyz_is_allowed(self):
        """The canonical Trusteed API base must return True."""
        assert validate_api_base("https://api.trusteed.xyz") is True

    def test_http_scheme_is_blocked(self):
        """Plain HTTP to any host must return False."""
        assert validate_api_base("http://api.trusteed.xyz/api") is False

    def test_loopback_is_blocked(self):
        """Loopback addresses must return False."""
        assert validate_api_base("https://127.0.0.1/api") is False

    def test_external_non_trusteed_hostname_is_blocked(self):
        """Hostnames outside the *.trusteed.xyz allowlist must return False."""
        assert validate_api_base("https://evil.example.com/api") is False


# ---------------------------------------------------------------------------
# H8 — EnforcementSnapshotService.get_snapshot multi-company IDOR fix
# ---------------------------------------------------------------------------


class _FakeICP:
    """ir.config_parameter accessor over a dict (sudo() returns self)."""

    def __init__(self, params):
        self._p = dict(params)

    def sudo(self):
        return self

    def get_param(self, key, default=""):
        return self._p.get(key, default)


class _FakeCompanyMap:
    """Stub of trusteed.company.enforcement.map exposing get_for_company()."""

    def __init__(self, mapping):
        self._mapping = mapping

    def get_for_company(self, company_id):
        return self._mapping


class _FakeEnv:
    def __init__(self, icp, company_map, company_id):
        self._icp = icp
        self._company_map = company_map
        self.company = type("C", (), {"id": company_id})()

    def __getitem__(self, model):
        if model == "ir.config_parameter":
            return self._icp
        if model == "trusteed.company.enforcement.map":
            return self._company_map
        raise KeyError(model)


def _make_service(*, icp_params, mapping, company_id=7):
    svc = EnforcementSnapshotService.__new__(EnforcementSnapshotService)
    svc.env = _FakeEnv(_FakeICP(icp_params), _FakeCompanyMap(mapping), company_id)
    return svc


class TestGetSnapshotMultiCompany:
    """get_snapshot derives merchantId from the company context (H8)."""

    def test_per_company_mapping_overrides_global(self, monkeypatch):
        captured = {}

        def _fake_pull(api_base, merchant_id, installation_id, hmac_secret, company_id):
            captured.update(
                api_base=api_base,
                merchant_id=merchant_id,
                installation_id=installation_id,
                hmac_secret=hmac_secret,
                company_id=company_id,
            )
            return {"merchantId": merchant_id}

        monkeypatch.setattr(_snapshot_mod, "pull_snapshot", _fake_pull)

        svc = _make_service(
            icp_params={
                "trusteed.api_base": "https://api.trusteed.xyz",
                # Global params present but MUST be ignored when a mapping exists.
                "trusteed.merchant_id": "GLOBAL-merchant",
                "trusteed.cel_installation_id": "GLOBAL-install",
                "trusteed.cel_hmac_secret": "global-secret",
                "trusteed.cel_hmac_secret.7": "company-7-secret",
            },
            mapping={
                "merchantId": "COMPANY7-merchant",
                "installationId": "COMPANY7-install",
                "fallbackMode": "balanced",
                "celEnabled": True,
            },
            company_id=7,
        )
        result = svc.get_snapshot()
        assert result == {"merchantId": "COMPANY7-merchant"}
        # Derived from the company mapping — NOT the global params.
        assert captured["merchant_id"] == "COMPANY7-merchant"
        assert captured["installation_id"] == "COMPANY7-install"
        assert captured["hmac_secret"] == "company-7-secret"
        assert captured["company_id"] == 7

    def test_cel_disabled_for_company_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            _snapshot_mod, "pull_snapshot",
            lambda *a, **k: pytest.fail("pull_snapshot must not be called"),
        )
        svc = _make_service(
            icp_params={"trusteed.api_base": "https://api.trusteed.xyz"},
            mapping={
                "merchantId": "m",
                "installationId": "i",
                "fallbackMode": "strict",
                "celEnabled": False,
            },
        )
        assert svc.get_snapshot() is None

    def test_falls_back_to_global_when_no_mapping(self, monkeypatch):
        captured = {}

        def _fake_pull(api_base, merchant_id, installation_id, hmac_secret, company_id):
            captured.update(merchant_id=merchant_id, installation_id=installation_id)
            return {"merchantId": merchant_id}

        monkeypatch.setattr(_snapshot_mod, "pull_snapshot", _fake_pull)
        svc = _make_service(
            icp_params={
                "trusteed.api_base": "https://api.trusteed.xyz",
                "trusteed.merchant_id": "GLOBAL-merchant",
                "trusteed.cel_installation_id": "GLOBAL-install",
                "trusteed.cel_hmac_secret": "global-secret",
            },
            mapping=None,  # no per-company mapping
        )
        result = svc.get_snapshot()
        assert result == {"merchantId": "GLOBAL-merchant"}
        assert captured["merchant_id"] == "GLOBAL-merchant"

    def test_missing_credentials_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            _snapshot_mod, "pull_snapshot",
            lambda *a, **k: pytest.fail("pull_snapshot must not be called"),
        )
        svc = _make_service(
            icp_params={"trusteed.api_base": "https://api.trusteed.xyz"},
            mapping=None,
        )
        assert svc.get_snapshot() is None
