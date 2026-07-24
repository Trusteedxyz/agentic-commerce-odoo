"""pytest — spec-048 P2.8 single-use replay protection (Odoo).

Covers ``models.nonce_consumer.consume_nonce()`` with the same four cases
exercised by the WooCommerce / PrestaShop counterparts:

  1. Happy path     — HTTP 200 → ACCEPTED
  2. Replay         — HTTP 409 → REPLAY
  3. Network fail   — requests.ConnectionError → INDETERMINATE
                      (caller maps to BLOCK when fallback_mode=strict)
  4. Network fail (observe mode) → still INDETERMINATE at this layer; the
     ``map_outcome_to_action()`` helper produces the ``observe`` action.

The module is pure-Python (no Odoo runtime imports) so we load it directly
via importlib without the full Odoo stubs.

Run:
  python -m pytest packages/odoo-addon-trusteed/tests/test_nonce_consume.py -v
"""

import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests.exceptions


def _load_nonce_consumer():
    """Import ``models/nonce_consumer.py`` standalone (no odoo runtime required)."""
    mod_path = (
        Path(__file__).resolve().parent.parent / "models" / "nonce_consumer.py"
    )
    spec = importlib.util.spec_from_file_location(
        "trusteed_nonce_consumer", str(mod_path)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["trusteed_nonce_consumer"] = module
    spec.loader.exec_module(module)
    return module


nonce_consumer = _load_nonce_consumer()


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def base_kwargs():
    return {
        "api_base": "https://api.trusteed.xyz",
        "merchant_id": "merch-odoo-nonce-001",
        "installation_id": "install-odoo-nonce-001",
        "hmac_secret": "test-hmac-secret-32bytesxxxxxxxx",
        "agent_did": "did:web:agent.example",
        "jti": "abcdefghijklmnop1234",  # 20 chars — passes JTI_RE upstream
        "exp": int(time.time()) + 60,
    }


def _mock_response(status: int, body: str = "{}") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = body
    return resp


# ── Case 1 — happy path ─────────────────────────────────────────────────────


def test_consume_nonce_accepted_on_200(base_kwargs):
    with patch.object(
        nonce_consumer.requests, "post", return_value=_mock_response(200)
    ) as mock_post:
        result = nonce_consumer.consume_nonce(**base_kwargs)

    assert result["outcome"] == nonce_consumer.ACCEPTED
    assert result["httpStatus"] == 200

    # Verify request was correctly composed (URL + HMAC header + payload shape).
    assert mock_post.call_count == 1
    args, kwargs = mock_post.call_args
    url = args[0] if args else kwargs.get("url")
    assert url.endswith("/v1/agent-events/nonce-consume")
    headers = kwargs["headers"]
    assert headers["X-Trusteed-Installation-Id"] == base_kwargs["installation_id"]
    assert headers["X-Trusteed-Signature"].startswith("t=")
    assert ",s=" in headers["X-Trusteed-Signature"]

    body_bytes = kwargs["data"]
    import json as _json
    payload = _json.loads(body_bytes.decode("utf-8"))
    assert payload["merchantId"] == base_kwargs["merchant_id"]
    assert payload["nonce"] == base_kwargs["jti"]
    assert payload["agentId"] == base_kwargs["agent_did"]
    assert "expiresAt" in payload

    # And map_outcome_to_action surfaces "continue" so the caller proceeds.
    assert nonce_consumer.map_outcome_to_action(result["outcome"], "strict") == "continue"
    assert nonce_consumer.map_outcome_to_action(result["outcome"], "balanced") == "continue"


# ── Case 2 — replay ─────────────────────────────────────────────────────────


def test_consume_nonce_replay_on_409(base_kwargs):
    with patch.object(
        nonce_consumer.requests, "post", return_value=_mock_response(409, '{"error":"replay"}')
    ):
        result = nonce_consumer.consume_nonce(**base_kwargs)

    assert result["outcome"] == nonce_consumer.REPLAY
    assert result["httpStatus"] == 409
    assert result["reason"] == "replay_detected"

    # Both modes reject a replay.
    assert nonce_consumer.map_outcome_to_action(result["outcome"], "strict") == "reject"
    assert nonce_consumer.map_outcome_to_action(result["outcome"], "balanced") == "reject"


# ── Case 3 — network failure under strict (enforce) ─────────────────────────


def test_consume_nonce_indeterminate_on_network_error_strict(base_kwargs):
    with patch.object(
        nonce_consumer.requests,
        "post",
        side_effect=requests.exceptions.ConnectionError("connection refused"),
    ):
        result = nonce_consumer.consume_nonce(**base_kwargs)

    assert result["outcome"] == nonce_consumer.INDETERMINATE
    assert result["reason"] == "network_error"
    assert result["httpStatus"] is None

    # Strict fallback → caller must reject (BLOCK / raise ValidationError).
    assert nonce_consumer.map_outcome_to_action(result["outcome"], "strict") == "reject"


# ── Case 4 — network failure under balanced (observe) ──────────────────────


def test_consume_nonce_indeterminate_observe(base_kwargs):
    # Use timeout to exercise both ConnectionError and Timeout branches.
    with patch.object(
        nonce_consumer.requests,
        "post",
        side_effect=requests.exceptions.Timeout("read timeout"),
    ):
        result = nonce_consumer.consume_nonce(**base_kwargs)

    assert result["outcome"] == nonce_consumer.INDETERMINATE
    assert result["reason"] == "timeout"

    # Balanced fallback → caller annotates cart attr and allows.
    assert nonce_consumer.map_outcome_to_action(result["outcome"], "balanced") == "observe"
    assert nonce_consumer.map_outcome_to_action(result["outcome"], "permissive") == "observe"


# ── Bonus — exp=0 falls back to now+300 for expiresAt ──────────────────────


def test_consume_nonce_exp_zero_uses_default_ttl(base_kwargs):
    kwargs = dict(base_kwargs)
    kwargs["exp"] = 0
    captured = {}

    def _capture(*args, **kwargs_):
        captured["data"] = kwargs_.get("data")
        return _mock_response(200)

    with patch.object(nonce_consumer.requests, "post", side_effect=_capture):
        result = nonce_consumer.consume_nonce(**kwargs)

    assert result["outcome"] == nonce_consumer.ACCEPTED
    import json as _json
    payload = _json.loads(captured["data"].decode("utf-8"))
    # Should be a valid ISO-8601 Z timestamp.
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", payload["expiresAt"])


# ── SSRF defense-in-depth — private / non-trusteed host is blocked ──────────


def test_consume_nonce_blocks_private_api_base(base_kwargs):
    kwargs = dict(base_kwargs)
    kwargs["api_base"] = "https://192.168.1.1/evil"
    with patch.object(nonce_consumer.requests, "post") as mock_post:
        result = nonce_consumer.consume_nonce(**kwargs)
    assert result["outcome"] == nonce_consumer.INDETERMINATE
    assert result["reason"] == "ssrf_blocked"
    mock_post.assert_not_called()  # never performs the request


def test_consume_nonce_blocks_non_trusteed_host(base_kwargs):
    kwargs = dict(base_kwargs)
    kwargs["api_base"] = "https://evil.example.com"
    with patch.object(nonce_consumer.requests, "post") as mock_post:
        result = nonce_consumer.consume_nonce(**kwargs)
    assert result["outcome"] == nonce_consumer.INDETERMINATE
    assert result["reason"] == "ssrf_blocked"
    mock_post.assert_not_called()


def test_consume_nonce_passes_redirect_guard(base_kwargs):
    captured = {}

    def _capture(*args, **kw):
        captured.update(kw)
        return _mock_response(200)

    with patch.object(nonce_consumer.requests, "post", side_effect=_capture):
        nonce_consumer.consume_nonce(**base_kwargs)
    # allow_redirects must be disabled (redirect-based SSRF defense).
    assert captured.get("allow_redirects") is False
