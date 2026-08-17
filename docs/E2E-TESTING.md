# Odoo Agentic Tools — Manual Verification Runbook

**Scope:** Validate that the 5 Trusteed agentic tools register as `ir.actions.server` with
`usage='ai_tool'`, that the per-tool availability gate behaves as documented, and that the
Trust Receipt JWS attachment lands on posted customer invoices.
**Time:** ~20 minutes (manual).

> This is an internal verification runbook for maintainers of this addon. It is not an
> installation guide — see `README.md` for installation.

---

## 1. Prerequisites

- An Odoo 18 instance (Odoo.sh or on-premise) with this addon on its `addons_path`.
- Shell access to the Odoo process (`odoo shell`).
- Docker + docker compose v2, if you prefer a throwaway local stack.
- Python 3.10+.

The commands below assume a container named `odoo-staging` and a database named `odoo`.
Adapt them to your deployment: any Odoo 18 + PostgreSQL stack with the addon mounted works.

## 2. Setup (5 min)

```bash
# 1. Verify Odoo is reachable
curl -fsS http://localhost:8069/web/health -o /dev/null && echo "Odoo up"

# 2. Install the addon (technical name: trusteed)
docker exec odoo-staging odoo \
  -d odoo --init=trusteed --stop-after-init \
  --without-demo=False

# 3. Configure the bootstrap credentials via system parameters
docker exec odoo-staging odoo shell -d odoo --no-http <<'PY'
env['ir.config_parameter'].sudo().set_param('trusteed.bootstrap_secret', '<your-bootstrap-secret>')
env['ir.config_parameter'].sudo().set_param('trusteed.api_base', 'https://api.trusteed.xyz')
env.cr.commit()
PY
```

## 3. Verify `ir.actions.server` Registration (3 min)

```bash
docker exec odoo-staging odoo shell -d odoo --no-http <<'PY'
actions = env['ir.actions.server'].search([
    ('usage', '=', 'ai_tool'),
    ('name', 'like', 'Trusteed'),
])
print(f"Found {len(actions)} ai_tool actions:")
for a in actions:
    print(f"  - {a.name} (ref={a.id})")
PY
```

**Expected output — always 5 records.** All five are declared unconditionally in
`data/ai_tools.xml`; no configuration flag changes how many get registered. Whether a tool
can be *invoked* is a separate, runtime concern (section 4).

```
Found 5 ai_tool actions:
  - Trusteed: Sign Trust Receipt
  - Trusteed: Verify Agent Signature
  - Trusteed: Dispatch Payment ACP
  - Trusteed: Dispatch Payment x402
  - Trusteed: Dispatch Payment AP2 (experimental)
```

The canonical xml_ids are:

| Tool                     | xml_id                                         |
| ------------------------ | ---------------------------------------------- |
| sign-trust-receipt       | `trusteed.action_trusteed_sign_trust_receipt`      |
| verify-agent-signature   | `trusteed.action_trusteed_verify_agent_signature`  |
| dispatch-payment-acp     | `trusteed.action_trusteed_dispatch_payment_acp`    |
| dispatch-payment-x402    | `trusteed.action_trusteed_dispatch_payment_x402`   |
| dispatch-payment-ap2     | `trusteed.action_trusteed_dispatch_payment_ap2`    |

> **Note on AI-tool discovery.** `usage='ai_tool'` is fully supported by the Odoo AI App in
> **Odoo 19.0**. On Odoo 18.x the field exists but the AI App discovery UI may not surface
> these actions; they remain callable via `env.ref(...).run()` and via the post-install hook.
> See the DESIGN NOTES header in `data/ai_tools.xml`.

## 4. Verify the Availability Gate (5 min)

Two of the five tools are `PLANNED` — their backend is **not deployed**, so they are
reported unavailable and **always raise `UserError`**, regardless of the merchant's toggle
(`utils/tool_toggles.py`, `PLANNED_TOOL_IDS`):

- `trusteed/sign-trust-receipt`
- `trusteed/dispatch-payment-ap2`

The three payment rails default to **OFF** (opt-in); `verify-agent-signature` defaults ON.

```bash
docker exec odoo-staging odoo shell -d odoo --no-http <<'PY'
from odoo.exceptions import UserError

# PLANNED tool — must raise, never return a receipt
try:
    env['trusteed.ai.tool'].run_sign_trust_receipt('12345')
    print("FAIL: expected UserError")
except UserError as e:
    print(f"OK (planned, unavailable): {e}")

# Disabled-by-default payment rail — must raise until the merchant opts in.
# The availability/toggle gate runs before argument validation, so placeholder
# args are enough to exercise it.
try:
    env['trusteed.ai.tool'].run_dispatch_payment_x402(
        'cart-placeholder', 'idem-placeholder', 'merchant-placeholder', {'network': 'base'},
    )
    print("unexpected success")
except UserError as e:
    print(f"OK (opt-in required): {e}")
PY
```

**Verify:** both calls raise `UserError`. The `sign-trust-receipt` message must state that
the backend is not yet deployed (`planned`).

> There is **no standalone receipt-signing write endpoint.** Trust Receipts are emitted as a
> side effect of the checkout pipeline and are *read* via `GET /v1/embed/trust/receipts`.
> Any acceptance criterion expecting `run_sign_trust_receipt` to return a valid JWS is
> **not achievable today** and must not be asserted.

## 5. Verify the JWS Receipt Attachment (5 min)

The JWS Trust Receipt is attached by the `account.move.action_post()` override
(`models/account_move_jws.py`), which fetches an **already-issued** receipt via
`GET /api/v1/trust/receipts/by-order/:orderId`. This path does not involve the
`sign-trust-receipt` tool at all.

```bash
docker exec odoo-staging odoo shell -d odoo --no-http <<'PY'
order = env['sale.order'].create({
    'partner_id': env.ref('base.res_partner_1').id,
    'order_line': [(0, 0, {
        'product_id': env.ref('product.product_product_4').id,
        'product_uom_qty': 1,
    })],
})
order.action_confirm()
invoice = order._create_invoices()
invoice.action_post()
jose = env['ir.attachment'].sudo().search([
    ('res_model', '=', 'account.move'),
    ('res_id', '=', invoice.id),
    ('mimetype', '=', 'application/jose'),
])
print(f"Order {order.name} · invoice {invoice.name} · .jose attachments: {len(jose)}")
for a in jose:
    print(f"  - {a.name}")
env.cr.commit()
PY
```

**Verify:**

1. If a receipt exists upstream for that order, exactly one `application/jose` attachment is
   present on the invoice, and the invoice chatter carries the corresponding message.
2. If no receipt exists (or the API base is unreachable), **zero** attachments and a warning
   in the log — the override is deliberately non-blocking and must never fail `action_post`.
3. Re-running `action_post` does not create a second `.jose` attachment (idempotency).

## 6. UI Verification (3 min)

1. Open `http://localhost:8069/web` and log in as an administrator.
2. **Sales → Orders** (kanban view) — the trust badge renders only when
   `trusteed_trust_level != 'none'`. On a fresh order with no trust signals, its absence is
   the expected result, not a failure.
3. Open the posted invoice — confirm the `.jose` attachment and the chatter message from
   section 5.

## 7. Acceptance Criteria

- [ ] The `ir.actions.server` query returns **5** records with `usage='ai_tool'`.
- [ ] All 5 canonical xml_ids from section 3 resolve via `env.ref(...)`.
- [ ] `run_sign_trust_receipt` raises `UserError` stating the backend is `planned`.
- [ ] The three payment rails raise `UserError` until explicitly toggled on.
- [ ] A posted invoice for an order with an upstream receipt carries exactly one
      `application/jose` attachment; re-posting adds none.
- [ ] An unreachable API base yields zero attachments and a logged warning, **not** a
      failed `action_post`.

## 8. Cleanup

```bash
# Uninstall the addon (keeps the database)
docker exec odoo-staging odoo shell -d odoo --no-http <<'PY'
env['ir.module.module'].search([('name', '=', 'trusteed')]).button_immediate_uninstall()
env.cr.commit()
PY
```

If you used a throwaway local stack, tear down its volumes instead.

## 9. Troubleshooting

| Symptom                                       | Fix                                                                                |
| --------------------------------------------- | ---------------------------------------------------------------------------------- |
| 0 actions found                               | `--init=trusteed` did not load the addon — re-run with `-u trusteed`               |
| `KeyError: 'trusteed.bootstrap_secret'`       | Re-apply the system parameters from section 2                                       |
| `sign-trust-receipt` returns instead of raising | The availability gate is bypassed — check `PLANNED_TOOL_IDS` in `utils/tool_toggles.py` |
| Payment rail raises "is disabled"              | Expected until the merchant opts in — **Settings → General Settings → Trusteed**    |
| No `.jose` attachment                          | No receipt exists upstream for that order, or `trusteed.api_base` is unreachable from the container (`curl` it from inside) |

---

**Audience:** addon maintainers · **Last updated:** 2026-08-17
