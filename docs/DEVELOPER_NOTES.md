# Trusteed — Odoo Addon

Embeds the Trusteed Trust Center and Merchant Center inside the Odoo 18 Back Office via the embed bootstrap two-step auth flow (ADR-022).

> **Odoo Online (SaaS) disclaimer**: Cannot install custom third-party modules on Odoo Online (SaaS). Use Odoo.sh or an on-premise installation.

## Features

- Trust Center: receipts, signing keys, audit log, trust score
- Merchant Center: orders, payment methods, agents, checkout config, certification + NLWeb
- Onboarding wizard (4 steps — `trusteed.setup.wizard` TransientModel)
- Multi-company support: OWL `useEffect` on `activeCompanyId`, silent re-bootstrap on company switch + toast notification
- F6 features: `sale.order` kanban trust score badge, `account.move` JWS receipt auto-attachment on post
- Settings integration: Trusteed Settings in standard Odoo Settings menu

## Requirements

- Odoo 18.0 (Community or Enterprise)
- Python 3.10+
- A Trusteed account at app.trusteed.xyz
- Odoo.sh or on-premise deployment

## Installation

### Option 1: Odoo Apps Store (recommended)

[Coming soon]

### Option 2: Manual install (on-premise)

1. Download or clone the addon into your Odoo addons path.
2. Restart Odoo: `systemctl restart odoo` (or equivalent).
3. In Odoo Back Office: **Settings → Activate developer mode**.
4. **Settings → Apps → Update Apps List**.
5. Search for "Trusteed" → **Install**.
6. Navigate to the new **Trusteed** menu — the Setup Wizard opens automatically.

### Option 3: Docker / dev

```bash
docker compose -f e2e/docker/odoo-staging.yml up -d
```

Then in Odoo: **Settings → Apps → Trusteed → Install**.

## Quick Setup Wizard

On first install (or when `trusteed.merchant_id` config param is empty), the addon opens the 4-step wizard (`trusteed.setup.wizard`):

1. **Welcome** — confirm prerequisites (account + HTTPS outbound from Odoo host)
2. **Connect** — open the Trusteed Portal, navigate to "Connect a Store → Odoo"
3. **Credentials** — paste your Merchant ID and Bootstrap Secret (must be exactly 64 hex chars)
4. **Test** — click "Check credentials" to verify the connection

## Multi-company

- Each company has a separate embed context (`company_id` + `company_ids[]` JWT claims).
- The OWL mount component detects `activeCompanyId` changes via `useEffect` and triggers a silent re-bootstrap automatically.
- A toast notification ("Reconectando con nueva compañía…") is shown during the switch.
- If re-bootstrap fails (network error), the user receives an error toast and can manually reload the page.

## Addon Files

```
odoo-addon-trusteed/
├── __manifest__.py               — Version 18.0.1.0.0 + dependencies
├── hooks.py                      — pre_init_hook (SaaS detection blocks install)
├── controllers/
│   └── main.py                   — Python bootstrap broker: HS256 sign → exchange → Ed25519 token
├── models/
│   ├── res_config_settings.py    — Trusteed Settings in Odoo config (password=True for secret)
│   ├── setup_wizard.py           — TransientModel 4-step onboarding wizard
│   ├── account_move_jws.py       — F6: account.move hook → JWS receipt ir.attachment on post
│   └── sale_order_trust.py       — F6: sale.order computed trust score fields (kanban badge)
├── utils/
│   ├── ssrf.py                   — Shared SSRF validation (is_global + CGN/multicast blocklist)
│   └── saas_detector.py          — 3-signal Odoo SaaS detector (blocks install on Odoo Online)
├── security/
│   ├── groups.xml                — group_user + group_admin (with implied_ids)
│   └── ir.model.access.csv       — ACL
├── views/
│   ├── menu.xml                  — Trusteed top-level menu
│   ├── client_action.xml         — OWL client action mount point
│   ├── res_config_settings_views.xml — Settings form
│   ├── wizard.xml                — 4-step wizard form (server action gated to group_admin)
│   └── sale_order_kanban.xml     — F6: kanban trust score badge xpath inject
├── data/
│   └── system_parameters.xml     — Default ir.config_parameter values
├── static/src/
│   ├── js/
│   │   ├── trusteed_panel.js   — OWL 2 component (mount + company switch detection)
│   │   └── admin-spa.js          — Bundled React SPA (post-build copy, shared bundle)
│   ├── css/
│   │   └── trust_badge.css       — F6: kanban badge styles (high/medium/low/none)
│   └── xml/
│       └── trusteed_panel.xml  — OWL template + toast template
├── tests/
│   ├── test_token_broker.py      — 14 unit tests (JWT signing, multi-company)
│   └── test_multi_company.py     — 11 unit tests (company switch, isolation)
└── i18n/                         — .po files (en, es, fr, de, nl)
```

## Security Notes

- Bootstrap secret stored in Odoo `ir.config_parameter` with `password=True` field (masked in UI, gated to `group_admin`).
- Bootstrap secret validated as exactly 64 hexadecimal characters before storing (F-005).
- Bootstrap secret cleared from TransientModel immediately after persisting to ICP (F-010).
- JWT TTL: 30 seconds. Access tokens: 5 minutes.
- `bootstrap_secret` is deleted from memory (`del secret`) after signing — never logged.
- CSRF validated via Odoo native CSRF token on all controller routes.
- SSRF prevention on `api_base`: HTTPS-only, blocks RFC-1918, loopback, link-local, CGN (100.64.0.0/10), multicast, reserved ranges — via `utils/ssrf.validate_api_base()` (Codex R2).
- Redirect-based SSRF blocked: all `requests` calls use `allow_redirects=False` with explicit 3xx rejection (Codex R2).
- URL path injection prevention: `order_ref` in `account_move_jws` is `urllib.parse.quote()`-encoded before URL interpolation (Codex R2).
- Company guard pre-signing: active `company_id` must be in `company_ids` before JWT is issued (F-011).
- Wizard server action gated to `group_admin` via `groups_id` (F-003).

## Compatibility

| Odoo Version | Edition    | Status                                   |
| ------------ | ---------- | ---------------------------------------- |
| 18.0         | Community  | ✅ Tested                                |
| 18.0         | Enterprise | ✅ Tested                                |
| 17.x         | —          | ⚠️ Not supported (OWL 2 API differences) |
| Odoo Online  | SaaS       | ❌ Cannot install custom modules         |

## Agentic Tools Integration (Spec 046)

The addon exposes **5 AI-callable tools** via Odoo's `ir.actions.server` with `usage='ai_tool'`.
When Odoo's AI App (v18) or the upcoming Odoo v20 MCP server is active, these tools are discovered automatically — no manual registration required.

### Registered tools

| Tool name                         | Odoo record                     | Description                                                     |
| --------------------------------- | ------------------------------- | --------------------------------------------------------------- |
| `trusteed/sign-trust-receipt`     | `action_sign_trust_receipt`     | Sign a JWS Ed25519 trust receipt via the Trusteed backend       |
| `trusteed/verify-agent-signature` | `action_verify_agent_signature` | Verify an RFC 9421 HTTP Message Signature from an inbound agent |
| `trusteed/dispatch-payment-acp`   | `action_dispatch_payment_acp`   | Trigger checkout via the Stripe ACP rail                        |
| `trusteed/dispatch-payment-x402`  | `action_dispatch_payment_x402`  | Trigger checkout via the x402 crypto multi-rail                 |
| `trusteed/dispatch-payment-ap2`   | `action_dispatch_payment_ap2`   | Trigger checkout via the AP2 v0.2 SD-JWT rail (opt-in)          |

### How it works

1. `data/ai_tools.xml` declares 5 `ir.actions.server` records with `usage='ai_tool'`.
2. Each server action calls a corresponding `run_*` method on the `trusteed.ai.tool` AbstractModel (`models/ai_tool_invocation.py`).
3. The AbstractModel invokes the Trusteed backend via `models/api_client.py` (`TrusteedApiClient`) with SSRF guards and `allow_redirects=False`.
4. `hooks.py` `post_init_hook` populates `ai_schema` (One2many inputSchema args) via `env.get('ir.actions.server.schema.arg')` — graceful fallback if the AI App module is absent on Odoo 18.

### Forward compatibility (Odoo v20)

The method signature follows the expected Odoo v20 MCP server tool dispatcher convention (`params: dict`). When Odoo ships its native MCP server, these tools should be auto-exposed without code changes.

### Required Odoo groups

| Group XML ID              | Grants                          |
| ------------------------- | ------------------------------- |
| `trusteed.group_signer`   | `sign-trust-receipt`            |
| `trusteed.group_verifier` | `verify-agent-signature`        |
| `trusteed.group_pay_acp`  | `dispatch-payment-acp`          |
| `trusteed.group_pay_x402` | `dispatch-payment-x402`         |
| `trusteed.group_pay_ap2`  | `dispatch-payment-ap2` (opt-in) |

## License

MIT © Trusteed
