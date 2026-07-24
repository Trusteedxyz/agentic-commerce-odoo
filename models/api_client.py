"""Trusteed HTTP API client — used by TrusteedAiTool invocations.

Design decisions:
- HTTPS-only enforcement (SSRF prevention) via ssrf.validate_api_base.
- No redirect following (allow_redirects=False) to block redirect-based SSRF.
- Timeout: (connect=3s, read=10s) — generous read for payment dispatch.
- 1 automatic retry on transient 5xx / connection error (idempotent endpoints only).
- Idempotency-Key header forwarded when caller supplies one (ACP/x402/AP2 dispatch).
- Raises TrusteedApiError (subclass of ValueError) so callers can catch precisely.

Ref: specs/046-cross-platform-agentic-tools/plan.md
"""

import logging
import time
import urllib.parse

import requests
import requests.exceptions

from odoo.addons.trusteed.utils.ssrf import validate_api_base

_logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 3
_READ_TIMEOUT = 10
_ADDON_UA = "TrusteedAddon-Odoo/1.0 (ai_tool)"
_MAX_RETRIES = 1


class TrusteedApiError(ValueError):
    """Raised when the Trusteed API returns a non-2xx response or is unreachable."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TrusteedApiClient:
    """Lightweight synchronous HTTP client for the Trusteed API.

    Instantiated per-request by TrusteedAiTool._get_api_client().
    Intentionally stateless — no session/cookie persistence.
    """

    def __init__(self, api_base: str, access_token: str | None) -> None:
        if not api_base:
            raise TrusteedApiError("trusteed.api_base system parameter is not configured.")
        if not validate_api_base(api_base):
            raise TrusteedApiError(
                f"api_base '{api_base}' is not a valid external HTTPS trusteed.xyz URL."
            )
        self._api_base = api_base.rstrip("/")
        self._token = access_token or ""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def call(
        self,
        endpoint: str,
        payload: dict,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
    ) -> dict:
        """Call endpoint with JSON payload, return parsed response dict.

        Args:
            endpoint: Path starting with '/' (e.g. '/api/v1/trust/receipts/sign').
            payload:  JSON-serialisable dict sent as request body.
            idempotency_key: Optional RFC-7231-compatible idempotency key header.
            method:   HTTP verb — default POST; GET not supported via this helper
                      (use _get() for read-only endpoints if needed).

        Returns:
            The ``data`` sub-dict from a ``{success: true, data: {...}}`` envelope,
            or the raw parsed body if the envelope pattern is absent.

        Raises:
            TrusteedApiError: on any non-2xx response or network failure.
        """
        url = self._build_url(endpoint)
        headers = self._build_headers(idempotency_key=idempotency_key)
        return self._request_with_retry(method, url, headers, payload)

    def call_with_s2s(
        self,
        endpoint: str,
        payload: dict,
        merchant_id: str,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
    ) -> dict:
        """Call a merchant-facing agentic-tools endpoint with per-store S2S auth.

        LANE 046-F1 Option A: sends the ``X-Trusteed-S2S-Secret`` header (the
        per-store secret persisted at install — same value as the bootstrap
        token) and injects ``merchant_id`` into the body, so the
        ``/api/v1/embed/agentic-tools/*`` routes (NOT agent-key-gated) can
        authenticate and scope ownership by store. The Bearer is NOT sent.

        Args:
            endpoint:    Path under /api/v1/embed/agentic-tools/.
            payload:     JSON-serialisable body (merchant_id is added/overwritten).
            merchant_id: Authoritative per-store merchant id.
            idempotency_key: Optional idempotency key header.
            method:      HTTP verb (default POST).

        Raises:
            TrusteedApiError: on missing secret/merchant id or any non-2xx.
        """
        if not self._token:
            raise TrusteedApiError("Trusteed S2S secret is not configured.")
        if not merchant_id or not str(merchant_id).strip():
            raise TrusteedApiError("merchant_id is required for an S2S call.")

        url = self._build_url(endpoint)
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": _ADDON_UA,
            "X-Trusteed-S2S-Secret": self._token,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        # Authoritative store context — overwrite any caller value.
        body = dict(payload)
        body["merchant_id"] = merchant_id

        return self._request_with_retry(method, url, headers, body)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_url(self, endpoint: str) -> str:
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        # Guard against path traversal — reject any encoded sequences
        decoded = urllib.parse.unquote(endpoint)
        if ".." in decoded or "\x00" in decoded:
            raise TrusteedApiError(f"Unsafe endpoint path: {endpoint!r}")
        return self._api_base + endpoint

    def _build_headers(self, *, idempotency_key: str | None = None) -> dict:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": _ADDON_UA,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _request_with_retry(
        self,
        method: str,
        url: str,
        headers: dict,
        payload: dict,
    ) -> dict:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0:
                time.sleep(0.5)
                _logger.debug("trusteed api_client: retry attempt %s for %s", attempt, url)
            try:
                resp = requests.request(
                    method,
                    url,
                    json=payload,
                    headers=headers,
                    timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                    allow_redirects=False,
                )
            except requests.exceptions.Timeout as exc:
                last_exc = exc
                _logger.debug("trusteed api_client: timeout on %s (attempt %s)", url, attempt)
                continue
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                _logger.debug(
                    "trusteed api_client: connection error on %s (attempt %s): %s",
                    url,
                    attempt,
                    type(exc).__name__,
                )
                continue

            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                raise TrusteedApiError(
                    f"Unexpected redirect from {url} — blocked for SSRF prevention.",
                    status_code=resp.status_code,
                )

            if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                _logger.debug(
                    "trusteed api_client: HTTP %s from %s — will retry", resp.status_code, url
                )
                last_exc = TrusteedApiError("Server error", status_code=resp.status_code)
                continue

            if not resp.ok:
                body_preview = (resp.text or "")[:200]
                raise TrusteedApiError(
                    f"API error {resp.status_code} from {url}: {body_preview}",
                    status_code=resp.status_code,
                )

            try:
                body: dict = resp.json() if resp.content else {}
            except ValueError as exc:
                raise TrusteedApiError(f"Non-JSON response from {url}") from exc

            # Unwrap standard envelope {success: true, data: {...}}
            if isinstance(body, dict) and body.get("success") and "data" in body:
                return body["data"] or {}
            return body

        # All attempts exhausted
        raise TrusteedApiError(
            f"All {_MAX_RETRIES + 1} attempts failed for {url}",
        ) from last_exc
