#!/usr/bin/env python3
"""export_tool_schemas.py — Odoo platform schema exporter.

Standalone script: NO Odoo runtime required.
Emits the 5 canonical Trusteed tool schemas as JSON to stdout.

Output format mirrors the canonical SSOT
``packages/shared/src/agentic-tools/catalog.ts`` (OLA-1 `routing` form):

  {
    "version": "1.0.0",
    "tools": [{
      "id", "name", "category", "requiredCapability",
      "routing": {"kind", "target", "backendStatus", "notes"},
      "inputSchema", "outputSchema", "experimental"?, "featureFlag"?
    }]
  }

HONESTY CONTRACT: each `routing.target` references a REAL backend, or is marked
`backendStatus: "planned"` when no backend is deployed. The previous revision of
this script emitted a single `mcpToolName` pointing at MCP tools that were never
registered — that field has been removed in favour of `routing` (see
`catalog-types.ts ToolRoutingSchema`).

Keys are sorted alphabetically at every level for stable diffs.

Usage:
    python3 packages/odoo-addon-trusteed/scripts/export_tool_schemas.py
"""

from __future__ import annotations

import json
import sys
from typing import Any

CATALOG_VERSION = "1.0.0"


def sort_keys(value: Any) -> Any:
    """Recursively sort all dict keys alphabetically for deterministic output."""
    if isinstance(value, list):
        return [sort_keys(item) for item in value]
    if isinstance(value, dict):
        return {k: sort_keys(v) for k, v in sorted(value.items())}
    return value


# ---------------------------------------------------------------------------
# Canonical schema definitions — mirror of
# packages/shared/src/agentic-tools/catalog.ts (SSOT). The `routing` object
# records the REAL execution target + production-readiness for each tool.
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "id": "trusteed/sign-trust-receipt",
        "name": "Sign Trust Receipt",
        "category": "trust",
        "requiredCapability": "sign_trust_receipt",
        "routing": {
            "kind": "rest",
            "target": "/v1/embed/trust/receipts",
            "backendStatus": "planned",
            "notes": (
                "Read surface (GET /v1/embed/trust/receipts) is live, but a "
                "standalone POST to sign an arbitrary order's receipt does not "
                "exist. Receipts are produced by the checkout tools per "
                "spec-040. Register UNAVAILABLE until a write endpoint ships."
            ),
        },
        "experimental": False,
        "inputSchema": {
            "type": "object",
            "properties": {
                "agentId": {
                    "description": "Optional DID or identifier of the acting agent",
                    "type": "string",
                },
                "amount": {
                    "description": "Transaction amount in currency minor units",
                    "type": "number",
                },
                "currency": {
                    "description": "ISO 4217 currency code (e.g. EUR, USD)",
                    "type": "string",
                },
                "orderId": {
                    "description": (
                        "Platform order identifier "
                        "(e.g. WooCommerce order ID, PS cart reference, "
                        "Odoo sale.order.id)"
                    ),
                    "type": "string",
                },
            },
            "required": ["orderId"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "issuedAt": {"format": "date-time", "type": "string"},
                "jws": {
                    "description": "Compact JWS (header.payload.signature) — Ed25519 signed",
                    "type": "string",
                },
                "jws_url": {
                    "description": "Public URL where the JWS can be verified",
                    "type": "string",
                },
                "receiptId": {"description": "Opaque receipt UUID", "type": "string"},
            },
            "required": ["receiptId", "jws"],
        },
    },
    {
        "id": "trusteed/verify-agent-signature",
        "name": "Verify Agent Signature",
        "category": "identity",
        "requiredCapability": "verify_agent_signature",
        "routing": {
            "kind": "rest",
            "target": "/api/v1/embed/agentic-tools/verify-agent-signature",
            "backendStatus": "implemented",
            "notes": (
                "Merchant-facing per-store S2S route (LANE 046-F1 Option A). "
                "Authenticated by X-Trusteed-S2S-Secret, not the staging-only "
                "internal token."
            ),
        },
        "experimental": False,
        "inputSchema": {
            "type": "object",
            "properties": {
                "body": {
                    "description": "Request body if Content-Digest covers it",
                    "type": "string",
                },
                "headers": {
                    "additionalProperties": {"type": "string"},
                    "description": (
                        "Headers map including Signature, Signature-Input, Content-Digest"
                    ),
                    "type": "object",
                },
                "method": {
                    "description": "HTTP method of the signed request",
                    "type": "string",
                },
                "url": {
                    "description": "Full URL of the signed request",
                    "type": "string",
                },
            },
            "required": ["method", "url", "headers"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "keyId": {"type": "string"},
                "provider": {"type": "string"},
                "source": {"type": "string"},
                "verificationStatus": {
                    "enum": ["verified", "unverified", "spoofed", "skipped"],
                    "type": "string",
                },
                "verified": {"type": "boolean"},
                "verifiedAt": {"format": "date-time", "type": "string"},
            },
            "required": ["verified", "verificationStatus"],
        },
    },
    {
        "id": "trusteed/dispatch-payment-acp",
        "name": "Dispatch Payment (ACP)",
        "category": "payment",
        "requiredCapability": "pay_with_agent",
        "routing": {
            "kind": "mcp-tool",
            "target": "process_agent_payment",
            "backendStatus": "implemented",
            "notes": (
                "Routed through POST /:storeSlug/mcp (checkout bucket). "
                "process_agent_payment is registered in tool-bucket-registry.ts."
            ),
        },
        "experimental": False,
        "inputSchema": {
            "type": "object",
            "properties": {
                "cartId": {"description": "Cart or order reference", "type": "string"},
                "idempotencyKey": {
                    "description": (
                        "Caller-supplied idempotency key "
                        "(required to prevent double-charge)"
                    ),
                    "type": "string",
                },
                "merchantId": {
                    "description": "Trusteed merchant identifier",
                    "type": "string",
                },
            },
            "required": ["cartId", "idempotencyKey", "merchantId"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "jws": {"type": "string"},
                "paymentId": {"type": "string"},
                "receiptId": {"type": "string"},
                "status": {
                    "enum": ["succeeded", "pending", "failed"],
                    "type": "string",
                },
            },
            "required": ["paymentId", "status"],
        },
    },
    {
        "id": "trusteed/dispatch-payment-x402",
        "name": "Dispatch Payment (x402)",
        "category": "payment",
        "requiredCapability": "pay_with_agent",
        "routing": {
            "kind": "rest",
            "target": "/api/v1/embed/agentic-tools/dispatch-payment-x402",
            "backendStatus": "implemented",
            "notes": (
                "Merchant-facing per-store S2S route (LANE 046-F1 Option A). "
                "Authenticated by X-Trusteed-S2S-Secret; store-scoped ownership. "
                "Accepts a V1 paymentPayload body or a PAYMENT-SIGNATURE (V2) header."
            ),
        },
        "experimental": False,
        "inputSchema": {
            "type": "object",
            "properties": {
                "cartId": {"type": "string"},
                "idempotencyKey": {"type": "string"},
                "merchantId": {"type": "string"},
                "paymentPayload": {
                    "additionalProperties": True,
                    "description": (
                        "x402 payment payload including signature and chain details"
                    ),
                    "type": "object",
                },
            },
            "required": ["cartId", "idempotencyKey", "merchantId", "paymentPayload"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "jws": {"type": "string"},
                "paymentId": {"type": "string"},
                "receiptId": {"type": "string"},
                "status": {
                    "enum": ["succeeded", "pending", "failed"],
                    "type": "string",
                },
                "txHash": {"type": "string"},
            },
            "required": ["paymentId", "status"],
        },
    },
    {
        "id": "trusteed/dispatch-payment-ap2",
        "name": "Dispatch Payment (AP2 v0.2)",
        "category": "payment-experimental",
        "requiredCapability": "pay_with_agent",
        "experimental": True,
        "featureFlag": "SD_JWT_ENABLED",
        "routing": {
            "kind": "rest",
            "target": "/api/v1/protocols/ap2/dispatch",
            "backendStatus": "planned",
            "notes": (
                "AP2 v0.2 is dormant (spec-044, behind SD_JWT_ENABLED). The "
                "dispatch route does NOT exist yet. Register UNAVAILABLE until "
                "spec-044 ships a dispatch route."
            ),
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "cartId": {"type": "string"},
                "idempotencyKey": {"type": "string"},
                "mandateJwt": {
                    "description": "AP2 SD-JWT compact mandate from the agent wallet",
                    "type": "string",
                },
                "merchantId": {"type": "string"},
            },
            "required": ["cartId", "idempotencyKey", "merchantId", "mandateJwt"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "jws": {"type": "string"},
                "mandateJwt": {
                    "description": "Signed checkout mandate returned by server",
                    "type": "string",
                },
                "paymentId": {"type": "string"},
                "receiptId": {"type": "string"},
                "status": {
                    "enum": ["succeeded", "pending", "failed"],
                    "type": "string",
                },
            },
            "required": ["paymentId", "status"],
        },
    },
]

# ---------------------------------------------------------------------------
# Build and emit canonical output
# ---------------------------------------------------------------------------

canonical = sort_keys({"version": CATALOG_VERSION, "tools": TOOLS})
sys.stdout.write(json.dumps(canonical, indent=2, ensure_ascii=False) + "\n")
