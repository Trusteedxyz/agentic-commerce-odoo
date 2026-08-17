# Trusteed — Odoo Addon Developer Notes

Embeds the Trusteed Trust Center and Merchant Center inside the Odoo Back Office via the
embed bootstrap two-step auth flow.

> **Odoo Online (SaaS) disclaimer**: custom third-party modules cannot be installed on Odoo
> Online (SaaS). Use Odoo.sh or an on-premise installation. Installation on Odoo Online is
> refused at install time by `pre_init_hook`.

## Features

- Trust Center: receipts, signing keys, audit log, trust score
- Merchant Center: orders, payment methods, agents, checkout config, certification + NLWeb
- Onboarding wizard (4 steps — `trusteed.setup.wizard` TransientModel)
- Multi-company support: OWL `useEffect` on `activeCompanyId`, silent re-bootstrap on company
  switch + toast notification
- `sale.order` kanban trust score badge, `account.move` JWS receipt auto-attachment on post
- Checkout enforcement layer: signed rule snapshot, offline safety-valve evaluator, agent
  token verification, nonce replay consumption
- Settings integration: Trusteed Settings in the standard Odoo Settings menu

## Requirements

- Odoo 17.0 or 18.0 (Community or Enterprise) — see [Compatibility](#compatibility)
- Python 3.10+
- Python package `cryptography` (declared in `external_dependencies`)
- A Trusteed account at [trusteed.xyz](https://trusteed.xyz)
- Odoo.sh or on-premise deployment

## Installation

### Option 1: Odoo Apps Store

[Coming soon]

### Option 2: Manual install

1. Download the installable `.zip` from the
   [latest GitHub Release](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases/latest)
   and extract it into your Odoo addons path. The extracted folder must be named `trusteed`
   (the addon's technical name).
2. Restart Odoo: `systemctl restart odoo` (or equivalent).
3. In the Odoo Back Office: **Settings → Activate developer mode**.
4. **Settings → Apps → Update Apps List**.
5. Search for "Trusteed" → **Install**.
6. Navigate to the new **Trusteed** menu — the Setup Wizard opens automatically.

### Option 3: Local development stack

Run any Odoo 17/18 + PostgreSQL stack with this addon mounted on the `addons_path`, then
install it from **Settings → Apps**. A `docker compose` file is not shipped with this
package; provide your own, or use an existing Odoo image with a bind-mounted addons volume.

## Quick Setup Wizard

On first install (or when the `trusteed.merchant_id` config parameter is empty), the addon
opens the 4-step wizard (`trusteed.setup.wizard`):

1. **Welcome** — confirm prerequisites (account + HTTPS outbound from the Odoo host)
2. **Connect** — open the Trusteed Portal, navigate to "Connect a Store → Odoo"
3. **Credentials** — paste your Merchant ID and Bootstrap Secret (must be exactly 64 hex chars)
4. **Test** — click "Check credentials" to verify the connection

## Multi-company

- Each company has a separate embed context (`company_id` + `company_ids[]` JWT claims).
- The OWL mount component detects `activeCompanyId` changes via `useEffect` and triggers a
  silent re-bootstrap automatically.
- A toast notification is shown during the switch.
- If re-bootstrap fails (network error), the user receives an error toast and can manually
  reload the page.

## Addon Files

Principal functional pieces (test files and compiled assets abbreviated):

```
trusteed/
├── __manifest__.py                    — Version 18.0.1.1.2 + dependencies
├── hooks.py                           — pre_init_hook (SaaS detection blocks install),
│                                        post_init_hook (ai_schema rows, toggle seeding,
│                                        capability report)
├── controllers/
│   └── main.py                        — bootstrap broker: S2S relay call → opaque data-plane token
├── models/
│   ├── res_config_settings.py         — Trusteed Settings (password=True for the secret)
│   ├── setup_wizard.py                — TransientModel 4-step onboarding wizard
│   ├── api_client.py                  — TrusteedApiClient (SSRF guards, allow_redirects=False)
│   ├── ai_tool_invocation.py          — trusteed.ai.tool: run_* methods for the 5 AI tools
│   ├── ir_actions_server_ai_tool.py   — ir.actions.server extension for ai_tool usage
│   ├── account_move_jws.py            — account.move hook → JWS receipt ir.attachment on post
│   ├── account_move_refund_proxy.py   — refund projection for enforcement signals
│   ├── sale_order_trust.py            — sale.order computed trust score fields (kanban badge)
│   ├── sale_order_enforcement.py      — checkout enforcement hooks on create/write/confirm
│   ├── enforcement_snapshot.py        — signed rule snapshot fetch + cache
│   ├── enforcement_token_verifier.py  — agent token verification (Ed25519, nonce mandatory)
│   ├── enforcement_company_map.py     — company → merchant resolution for enforcement
│   ├── offline_safety_valve_evaluator.py — local evaluation of the universal rule set
│   ├── nonce_consumer.py              — offline replay detection (nonce consumption)
│   ├── capabilities_reporter.py       — reports projectable cart signals to the backend
│   └── stock_picking_fulfillment.py   — fulfillment signal projection
├── utils/
│   ├── ssrf.py                        — SSRF validation (is_global + CGN/multicast blocklist)
│   ├── saas_detector.py               — 3-signal Odoo SaaS detector (blocks install)
│   ├── tool_toggles.py                — per-tool enable/disable + planned-tool honest gate
│   ├── cart_signals.py                — cart signal extraction for enforcement
│   ├── jcs.py                         — RFC 8785 JCS canonicalization
│   ├── r043_hitl_gate.py              — human-in-the-loop gate helper
│   └── agent_history_fetcher.py       — agent history lookup for window rules
├── security/
│   ├── groups.xml                     — group_user + group_admin (with implied_ids)
│   └── ir.model.access.csv            — ACL
├── views/
│   ├── menu.xml                       — Trusteed top-level menu
│   ├── client_action.xml              — OWL client action mount point
│   ├── res_config_settings_views.xml  — Settings form
│   ├── wizard.xml                     — 4-step wizard form (server action gated to group_admin)
│   ├── sale_order_kanban.xml          — kanban trust score badge xpath inject
│   └── sale_order_enforcement.xml     — hidden agent token field on the sale.order form
├── data/
│   ├── system_parameters.xml          — Default ir.config_parameter values
│   ├── ai_tools.xml                   — 5 ir.actions.server ai_tool records (noupdate="1")
│   ├── cron.xml                       — enforcement snapshot refresh cron
│   └── agentic-tools-catalog.json     — bundled canonical tool catalog
├── static/src/
│   ├── js/trusteed_panel.js           — OWL 2 component (mount + company switch detection)
│   ├── js/admin-spa.js                — Bundled React SPA (build artifact, shared bundle)
│   ├── css/trust_badge.css            — kanban badge styles (high/medium/low/none)
│   └── xml/trusteed_panel.xml         — OWL template + toast template
├── scripts/
│   └── export_tool_schemas.py         — exports the AI tool argument schemas
├── tests/                             — 22 test modules, e.g.
│   ├── test_token_broker.py           — 13 tests (JWT signing, multi-company)
│   ├── test_multi_company.py          — 7 tests (company switch, isolation)
│   └── …                              — enforcement, AI tool invocation, JCS vectors,
│                                        nonce replay, tool toggles, offline safety valve
└── i18n/                              — .po files (en, es, fr, de, nl)
```

## Security Notes

- Bootstrap secret stored in Odoo `ir.config_parameter` with a `password=True` field (masked
  in the UI, gated to `group_admin`).
- Bootstrap secret validated as exactly 64 hexadecimal characters before storing.
- Bootstrap secret cleared from the TransientModel immediately after persisting.
- Token lifetime is set by the Trusteed backend, not by this addon: the controller reads the
  `expires_at` value returned by the relay. Do not rely on a hard-coded TTL here.
- `bootstrap_secret` is deleted from memory (`del secret`) immediately after the relay call —
  never logged, and never included in any log message.
- The bootstrap route checks `trusteed.group_user` **before** reading any config parameter.
- CSRF validated via Odoo's native CSRF token on all controller routes.
- SSRF prevention on `api_base`: HTTPS-only, blocks RFC-1918, loopback, link-local,
  CGN (100.64.0.0/10), multicast and reserved ranges — via `utils/ssrf.validate_api_base()`.
- Redirect-based SSRF blocked: all `requests` calls use `allow_redirects=False` with explicit
  3xx rejection.
- URL path injection prevention: the order reference in `account_move_jws` is
  `urllib.parse.quote()`-encoded before URL interpolation.
- Company guard pre-signing: the active `company_id` must be in `company_ids` before the JWT
  is issued.
- Wizard server action gated to `group_admin` via `groups_id`.
- Agent token replay: the `nonce` claim is **mandatory** (16–64 chars); a token omitting it is
  rejected fail-closed.

## Compatibility

| Odoo Version | Edition    | Status                                     |
| ------------ | ---------- | ------------------------------------------ |
| 18.0         | Community  | ✅ Supported — primary target              |
| 18.0         | Enterprise | ✅ Supported — primary target              |
| 17.0         | Community  | ✅ Supported (views use inline `invisible`) |
| 17.0         | Enterprise | ✅ Supported (views use inline `invisible`) |
| Odoo Online  | SaaS       | ❌ Cannot install custom modules            |

The addon declares support for Odoo 17/18 (`__manifest__.py`), and the view definitions use
the inline `invisible` domain attributes required by both versions (`views/wizard.xml`).
The manifest `version` string is `18.0.<addon-version>` per Odoo convention; it identifies the
packaging series, not an exclusion of 17.0.

## Agentic Tools Integration

The addon exposes **5 AI-callable tools** via Odoo's `ir.actions.server` with `usage='ai_tool'`.

### Registered tools

| Tool name                         | Odoo xml_id                              | Invocable from Odoo?          | Toggle default |
| --------------------------------- | ---------------------------------------- | ----------------------------- | -------------- |
| `trusteed/sign-trust-receipt`     | `action_trusteed_sign_trust_receipt`     | ❌ no — backend `planned`      | ON (inert)     |
| `trusteed/verify-agent-signature` | `action_trusteed_verify_agent_signature` | ✅ yes                        | ON             |
| `trusteed/dispatch-payment-acp`   | `action_trusteed_dispatch_payment_acp`   | ❌ no — needs an MCP gateway  | OFF            |
| `trusteed/dispatch-payment-x402`  | `action_trusteed_dispatch_payment_x402`  | ✅ yes (once toggled on)      | OFF            |
| `trusteed/dispatch-payment-ap2`   | `action_trusteed_dispatch_payment_ap2`   | ❌ no — backend `planned`      | OFF            |

Only **two** of the five tools can currently complete a call from this module:
`verify-agent-signature` and `dispatch-payment-x402`. The other three raise `UserError`
unconditionally — see below.

Prefix the xml_id with the module name to resolve it, e.g.
`env.ref('trusteed.action_trusteed_verify_agent_signature')`.

### Availability and toggles

Each tool has an independent merchant toggle persisted in `ir.config_parameter`
(`utils/tool_toggles.py`). Installing the addon **never** silently enables agent payments —
the three payment rails default to OFF and must be opted into.

Two tools are `backendStatus: planned` — **no backend is deployed for them**. They are
reported UNAVAILABLE and **always raise `UserError`** regardless of their toggle (honest
gate); the toggle merely preserves the merchant's preference for when those backends ship:

- `trusteed/sign-trust-receipt` — there is no standalone receipt-signing write endpoint.
  Trust Receipts are emitted as a side effect of the checkout pipeline and are *read* back.
  **Do not write integrations that assume `run_sign_trust_receipt` returns a JWS** — it
  cannot, today.
- `trusteed/dispatch-payment-ap2`

A third tool passes the availability gate but is still not fulfillable from this module:

- `trusteed/dispatch-payment-acp` — the canonical ACP backend is the `process_agent_payment`
  MCP checkout-bucket tool, reached over a store-slug MCP gateway that this addon does not
  provision. There is no REST dispatch route for it, so the method fails closed with an
  explicit `UserError` rather than issuing a guaranteed-404 call. Use the x402 rail, or the
  per-store MCP checkout bucket directly.

### How it works

1. `data/ai_tools.xml` declares the 5 `ir.actions.server` records with `usage='ai_tool'`
   (`noupdate="1"` — created once on install, not clobbered on upgrade).
2. Each server action calls the corresponding `run_*` method on the `trusteed.ai.tool` model
   (`models/ai_tool_invocation.py`).
3. That model invokes the Trusteed backend via `models/api_client.py`
   (`TrusteedApiClient`) with SSRF guards and `allow_redirects=False`.
4. `post_init_hook` populates `ai_schema` (One2many argument rows) via
   `env.get('ir.actions.server.schema.arg')`, with a graceful fallback when that model is
   absent.

### AI-tool discovery by Odoo version

`usage='ai_tool'` is the canonical registration mechanism and is **fully supported in
Odoo 19.0**, where the `ai_schema` field was introduced. On **Odoo 18.x the field exists but
the AI App discovery UI may not surface these actions** — they remain callable via
`env.ref(...).run()` and via the post-install hook, and `ai_schema` row creation is skipped
with a logged notice. The authoritative statement of this behaviour is the DESIGN NOTES
header of `data/ai_tools.xml`; treat that file as the source of truth.

The output contract follows the Odoo 19 AI App convention: the action's `code` assigns the
tool result to `ai['result']`. Registration is also intended to be consumable by a future
Odoo MCP server implementation, but no such server is shipped or dated by Odoo today — no
forward-compatibility guarantee is made here.

### Required Odoo groups

The addon defines exactly **two** groups (`security/groups.xml`); there are no per-tool
groups:

| Group XML ID           | Grants                                                     |
| ---------------------- | ---------------------------------------------------------- |
| `trusteed.group_user`  | View the Trust Center and Merchant Center panels           |
| `trusteed.group_admin` | Configure the bootstrap secret and manage settings (implies `group_user`) |

Per-tool authorization is enforced by the toggle/availability gate in
`utils/tool_toggles.py`, not by Odoo security groups.

## License

LGPL-3 © Trusteed (see the `license` field in `__manifest__.py`).
