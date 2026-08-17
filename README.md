**English** | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md)

# Trusteed Agentic Commerce for Odoo

Enable new online shoppers, AI agents, to make purchases in your store securely and reliably thanks to Trusteed: the network that fosters trust between businesses and agents.

- **Set your business rules**: who you allow to buy, up to what amount, which categories you don't want to offer to agents, set price limits, maintain stock levels to protect yourself against potential fraudulent agents, and more.
- **Tamper-proof receipts**: we generate electronically signed and cryptographically tamper-proof receipts that serve as proof of the actual transaction in case of any dispute. Compatible with eIDAS (EU, UK) and eSIGN (USA) regulations.
- **Agent analytics**: view statistics on agent purchases — how much they spend, what products they buy, and how often.
- **Agent blocking**: block potentially dangerous or problematic agents.
- **Digital currencies**: enables purchases in digital currencies thanks to the X402 protocol.
- **Peer-to-peer transactions**: enables direct peer-to-peer commerce between agents and merchants.

## Screenshots

| Trust Center | My Sales — My Orders | My Sales — AI Sales |
|---------------|-----------------------|----------------------|
| ![Trust Center](screenshots/01-trust-center.png) | ![My Orders](screenshots/02-my-sales-orders.png) | ![AI Sales](screenshots/03-my-sales-ai-sales.png) |

| My Sales — Keys | My Sales — Audit | Settings |
|-------------------|---------------------|----------|
| ![Keys](screenshots/04-my-sales-keys.png) | ![Audit](screenshots/05-my-sales-audit.png) | ![Settings](screenshots/06-settings.png) |

| Quick Setup Wizard |
|----------------------|
| ![Quick Setup Wizard](screenshots/07-quick-setup-wizard.png) |

Every agent transaction produces a cryptographically signed **trust receipt** — a tamper-proof record (compatible with eIDAS / eSIGN) listed under **Mis ventas → AI Sales**. Signing keys and a full audit log are available under the same **Mis ventas** menu, and the **Trust Center** screen surfaces your store's overall trust score.

## Features

Trusteed consolidates a Trust Center, a signed-receipts ledger, and 5 native agentic tools into a single Odoo addon.

- **Trust Center** — store trust score, signed trust receipts, signing keys, audit log
- **My Sales (Mis ventas)** — orders, AI-agent sales, signing keys, audit log, all in one menu
- **5 native AI tools** exposed via `ir.actions.server` (`usage='ai_tool'`), auto-discovered by Odoo's AI App / MCP server: `sign-trust-receipt`, `verify-agent-signature`, `dispatch-payment-acp`, `dispatch-payment-x402`, `dispatch-payment-ap2`
- **Sale order trust badge** — a computed trust-score badge injected into the native `sale.order` kanban view
- **JWS receipt auto-attachment** — signed trust receipts are attached to `account.move` records automatically on posting
- **Multi-company support** — silent re-bootstrap with a toast notification when the active company changes
- **Quick Setup wizard** — a 4-step onboarding flow that connects your Odoo instance to Trusteed
- **Fail-closed defaults** — SSRF-guarded outbound calls, HTTPS-only, enforcement never silently allows when misconfigured

## Compatibility

| Component | Supported |
|-----------|-----------|
| Odoo | 18.0 (Community or Enterprise) |
| Python | 3.10+ |
| Deployment | Odoo.sh or on-premise — **not** available on Odoo Online (SaaS), which blocks custom third-party modules |

## Requirements

- Odoo 18.0, on Odoo.sh or an on-premise installation
- Python 3.10+
- A Trusteed account — [sign up free at trusteed.xyz](https://trusteed.xyz)

## Installation

### Manual install

1. **Download the installable `.zip`** from the latest GitHub Release:
   [**⬇ trusteed-agentic-commerce-odoo-18.0.1.1.0.zip**](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases/latest/download/trusteed-agentic-commerce-odoo-18.0.1.1.0.zip)
   — or browse all versions at the [Releases page](https://github.com/Trusteedxyz/agentic-commerce-odoo/releases).
2. Extract it into your Odoo `addons_path` — the extracted folder must be named `trusteed` (this is the addon's technical name).
3. Restart Odoo: `systemctl restart odoo` (or equivalent for your deployment).
4. In the Odoo Back Office: **Settings → Activate developer mode**.
5. **Settings → Apps → Update Apps List**, then search for "Trusteed" and click **Install**.
6. Open the new **Trusteed** menu — the Quick Setup wizard opens automatically.

### From source (build the zip yourself)

```bash
git clone https://github.com/Trusteedxyz/agentic-commerce-odoo.git
cd agentic-commerce-odoo
bash bin/build-zip.sh   # outputs dist/trusteed-agentic-commerce-odoo-<version>.zip
```

### Docker / local dev

```bash
docker compose -f e2e/docker/odoo-staging.yml up -d
```

Then in Odoo: **Settings → Apps → Trusteed → Install**.

## Configuration

The Quick Setup wizard (**Trusteed → Quick Setup**) walks through the whole flow:

1. **Welcome** — confirms prerequisites (a Trusteed account + outbound HTTPS from the Odoo host).
2. **Connect** — opens the Trusteed Portal at [trusteed.xyz/dashboard](https://trusteed.xyz/dashboard), where you navigate to **Connect a Store → Odoo**.
3. **Credentials** — paste your **Merchant ID** and **Bootstrap Secret** (64 hex characters).
4. **Test** — verifies connectivity before finishing.

Credentials can also be entered directly under **Settings → Trusteed**, without going through the wizard.

### System parameters

| `ir.config_parameter` key | Default | Purpose |
|----------------------------|---------|---------|
| `trusteed.merchant_id` | _(empty)_ | Merchant ID issued by Trusteed |
| `trusteed.bootstrap_secret` | _(empty)_ | 64-hex-char bootstrap secret, `password=True`, gated to `group_admin` |
| `trusteed.api_base` | `https://api.trusteed.xyz` | Trusteed backend endpoint |

## Admin Menus

After installation, a **Trusteed** top-level menu appears in the Odoo Back Office:

| Menu | Description |
|------|--------------|
| Quick Setup | 4-step onboarding wizard |
| Trust Center | Store trust score overview |
| Mis ventas | My Orders, AI Sales, Keys, Audit — all in one place |
| Settings | Merchant ID, Bootstrap Secret, API base URL, and per-tool toggles |

## FAQ

**What data is sent?** Only what enforcement rules and trust receipts require (order totals, country, agent identity). No payment card data ever passes through Trusteed. All communication uses HTTPS.

**Which agents are supported?** Any agent connected through an MCP-compatible client that calls the 5 native AI tools this addon exposes, including Claude Desktop and Odoo's own AI App / MCP server.

**Does it slow down my store?** No. Enforcement runs synchronously only at the relevant transaction step, with fail-closed defaults instead of a blanket allow fallback.

**Can I install this on Odoo Online (SaaS)?** No — Odoo Online does not allow custom third-party modules. Use Odoo.sh or an on-premise deployment.

## Changelog

### 18.0.1.1.2

- **Fixed** — the admin panel bundle (`static/src/js/admin-spa.js`) shipped unminified: 869 KB / 25,064 lines instead of the 490 KB / 41 lines the documented build command (`pnpm run build:odoo`) actually produces. It still worked, but its provenance could not be verified — no diff against the source could confirm what it contained. Rebuilt from source; the compiled bundle now matches character-for-character what the build command produces.
- **Fixed** — the R047 minimum-contribution-amount rule had no form field in the admin panel: its parameters existed in the schema but could only be set via the API. Also fixed: rendering a merchant-supplied category name printed the raw prompt-injection delimiters (`<<<MERCHANT_CONTENT_START>>> … <<<MERCHANT_CONTENT_END>>>`) around it instead of stripping them for display.

### 18.0.1.1.1

- **Fixed** — `_DOCS_URL` sent merchants to `https://docs.trusteed.xyz/embed/odoo-onprem`, a host that returns NXDOMAIN. Every merchant who followed the in-app docs link got a browser error instead of the integration guide. Now points at `https://trusteed.xyz/en/integrations/odoo`.

### 18.0.1.1.0

- **Security fix** — the agent token verifier's replay detection hung off `if nonce:`, so a token that simply omitted the `nonce` claim skipped offline replay detection entirely. The claim is now mandatory (16–64 characters, as the canonical token schema requires) and a token without it is rejected — fail-closed, matching the WooCommerce, PrestaShop and Magento connectors.
- **Added** — the addon now reports which cart signals this installation can project (`POST /api/v1/enforcement/capabilities`, HMAC-signed, sent from the post-init hook, which is exactly when the addon version changes). Without it, a rule whose signal never arrives returns `NO_SIGNAL` on every checkout: it passes silently, and the merchant sees a rule in ENFORCE that blocks nothing. The report never propagates a failure — a network error there cannot break an install or upgrade.

### 18.0.1.0.0

- Initial public release: Trust Center, Mis ventas (orders, AI sales, keys, audit), Quick Setup wizard, Settings integration, 5 native AI tools, sale-order trust badge, JWS receipt auto-attachment, multi-company support.

## Support

- Support email: support@trusteed.xyz
- GitHub issues: [github.com/Trusteedxyz/agentic-commerce-odoo/issues](https://github.com/Trusteedxyz/agentic-commerce-odoo/issues)

## License

MIT. See [LICENSE](LICENSE) for full text.
