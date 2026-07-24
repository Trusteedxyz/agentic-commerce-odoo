"""Performance benchmark — spec-048 CEL agent token verification.

Asserts p95 ≤ 8ms per verification (offline Ed25519, no network).

Run: pytest packages/odoo-addon-trusteed/tests/test_enforcement_perf.py \
         --rootdir=packages/odoo-addon-trusteed/tests \
         --import-mode=importlib -v -s
"""

import base64
import importlib.util
import json
import statistics
import sys
import time
import types
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# ---------------------------------------------------------------------------
# Odoo stub — must be installed before importing any addon module
# ---------------------------------------------------------------------------

def _install_odoo_stubs() -> None:
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
# Load enforcement_token_verifier via importlib (no Odoo deps)
# ---------------------------------------------------------------------------

_ADDON_ROOT = Path(__file__).parent.parent
_ROOT = "addon_t089a"
_MODELS_PKG = f"{_ROOT}.models"

for _pkg in (_ROOT, _MODELS_PKG):
    if _pkg not in sys.modules:
        sys.modules[_pkg] = types.ModuleType(_pkg)

_spec = importlib.util.spec_from_file_location(
    f"{_MODELS_PKG}.enforcement_token_verifier",
    _ADDON_ROOT / "models" / "enforcement_token_verifier.py",
)
_verifier_mod = importlib.util.module_from_spec(_spec)
_verifier_mod.__package__ = _MODELS_PKG
sys.modules[f"{_MODELS_PKG}.enforcement_token_verifier"] = _verifier_mod
_spec.loader.exec_module(_verifier_mod)

verify_agent_token = _verifier_mod.verify_agent_token
compute_intent_hash = _verifier_mod.compute_intent_hash

# ---------------------------------------------------------------------------
# Key material — generated once at import time for all benchmark iterations
# ---------------------------------------------------------------------------

def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


_SK: Ed25519PrivateKey = Ed25519PrivateKey.generate()
_PK = _SK.public_key()
_PUB_BYTES: bytes = _PK.public_bytes_raw()
_PUB_B64URL: str = _b64url(_PUB_BYTES)
_AGENT_DID = "did:key:perf-agent-001"
_MERCHANT_ID = "m-perf-merchant"
_CANONICAL_JSON = '{"amount":100,"currency":"USD","orderId":"ord-perf-001"}'
_INTENT_HASH: str = compute_intent_hash(_CANONICAL_JSON)

_RESOLVER: dict = {_AGENT_DID: {"x": _PUB_B64URL}}

# Pre-sign one JWS per unique nonce up to 1000 tokens.  To avoid nonce-replay
# rejection (which caches for 300 s) each iteration uses a distinct nonce, so
# we pre-generate all 1000 tokens before timing begins.
_N_ITER = 1000


def _sign_token(nonce: str) -> str:
    now = int(time.time())
    header = {
        "alg": "EdDSA",
        "typ": "trusteed-agent-token+jwt",
        "kid": f"{_AGENT_DID}#key-1",
    }
    payload = {
        "iss": _AGENT_DID,
        "aud": "trusteed",
        "merchantId": _MERCHANT_ID,
        "checkoutIntentHash": _INTENT_HASH,
        "nonce": nonce,
        # `jti` is required by verify_agent_token (spec-048 P2.8 single-use
        # replay gate) and must match ^[A-Za-z0-9_-]{16,128}$.
        "jti": f"perf-jti-{nonce}",
        "exp": now + 300,
        "iat": now,
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode("ascii")
    sig = _SK.sign(signing_input)
    return f"{h}.{p}.{_b64url(sig)}"


# Pre-generate all tokens (signing time is excluded from the benchmark)
_TOKENS = [_sign_token(f"perf-nonce-{i:05d}") for i in range(_N_ITER)]


# ---------------------------------------------------------------------------
# Benchmark test
# ---------------------------------------------------------------------------

class TestAgentTokenVerificationPerformance:
    """T089a — verify_agent_token offline Ed25519 p95 ≤ 8ms."""

    def test_p95_verification_under_8ms(self) -> None:
        """1000 offline verifications must achieve p95 ≤ 8ms per call."""
        durations: list[float] = []

        for token in _TOKENS:
            t0 = time.perf_counter()
            result = verify_agent_token(token, _INTENT_HASH, _MERCHANT_ID, _RESOLVER)
            t1 = time.perf_counter()
            assert result is not None, "Token verification failed during benchmark"
            durations.append(t1 - t0)

        # p95: the 95th-percentile value from the sorted durations list
        sorted_d = sorted(durations)
        p95_idx = int(0.95 * len(sorted_d))
        p95 = sorted_d[p95_idx]
        mean = statistics.mean(durations)
        p50 = statistics.median(durations)

        print(
            f"\n[T089a] verify_agent_token ({_N_ITER} iterations) — "
            f"p50: {p50 * 1000:.3f}ms  "
            f"mean: {mean * 1000:.3f}ms  "
            f"p95: {p95 * 1000:.2f}ms"
        )

        assert p95 <= 0.008, (
            f"p95 {p95 * 1000:.2f}ms exceeds 8ms SLO — "
            "Ed25519 verification is unexpectedly slow on this host"
        )
