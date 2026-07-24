# E2E T091 — Odoo Agentic Tools Manual Runbook

**Spec:** 046 — Cross-Platform Agentic Tools
**Scope:** Validate the 5 canonical Trusteed agentic tools register as `ir.actions.server` with `usage='ai_tool'` in Odoo 18 and execute correctly.
**Time:** ~25 minutes (manual).

> Automated alternative: `e2e/spec-046-agentic-tools.spec.ts` covers catalog endpoint with mocked services. This runbook validates `ir.actions.server` registration and chatter integration end-to-end.

---

## 1. Prerequisites

- Docker + docker compose v2
- Odoo 18 community
- Python 3.10+ (for invoking shell)

## 2. Setup (5 min)

```bash
cd /home/sejano77/Projects/MCPWebStore

# 1. Start Odoo stack
docker compose -f e2e/docker/docker-compose.odoo.yml up -d
sleep 60   # Postgres + Odoo init

# 2. Verify Odoo reachable
curl -fsS http://localhost:8069/web/health -o /dev/null && echo "Odoo up"

# 3. Install Trusteed addon (mounted addon path)
docker exec odoo-staging odoo \
  -d odoo --init=trusteed --stop-after-init \
  --without-demo=False

# 4. Configure bootstrap token via system parameters
docker exec odoo-staging odoo shell -d odoo --no-http <<'PY'
env['ir.config_parameter'].sudo().set_param('trusteed.bootstrap_secret', 'test-token-789')
env['ir.config_parameter'].sudo().set_param('trusteed.api_base', 'http://host.docker.internal:3001')
env.cr.commit()
PY
```

## 3. Verify ir.actions.server Registration (3 min)

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

**Expected output (5 records, 4 if `SD_JWT_ENABLED` OFF):**

```
Found 5 ai_tool actions:
  - Trusteed: Sign Trust Receipt
  - Trusteed: Verify Agent Signature
  - Trusteed: Dispatch Payment ACP
  - Trusteed: Dispatch Payment x402
  - Trusteed: Dispatch Payment AP2 (experimental)
```

## 4. Test sign-trust-receipt (8 min)

```bash
docker exec odoo-staging odoo shell -d odoo --no-http <<'PY'
# 1. Create a sale order
order = env['sale.order'].create({
    'partner_id': env.ref('base.res_partner_1').id,
    'order_line': [(0, 0, {
        'product_id': env.ref('product.product_product_4').id,
        'product_uom_qty': 1,
    })],
})
order.action_confirm()
print(f"Sale order: {order.id} ({order.name})")

# 2. Invoke ai_tool action with context
action = env.ref('trusteed.action_trusteed_sign_trust_receipt')
result = action.with_context(
    orderId=str(order.id),
    amount=int(order.amount_total * 100),
    currency=order.currency_id.name,
).run()
print(f"Result: {result}")

# 3. Verify chatter
order.message_subscribe()
messages = order.message_ids.filtered(lambda m: 'Trust Receipt' in (m.body or ''))
print(f"Chatter messages: {len(messages)}")
for m in messages[:1]:
    print(f"  body[:200]: {m.body[:200]}")
env.cr.commit()
PY
```

**Verify:**

1. `Result:` dict includes `receiptId`, `jws`, `issuedAt`.
2. JWS has 3 segments separated by `.`.
3. Chatter message contains the JWS short ID.
4. Server log shows `_logger.info` line with the invocation:

```bash
docker logs odoo-staging 2>&1 | grep -i 'trusteed.*sign_trust_receipt' | tail -5
```

## 5. UI Verification (3 min)

1. Open `http://localhost:8069/web` and login admin / admin.
2. Navigate to Sales > Orders > select the created order.
3. Confirm kanban badge "Trust Receipt ✓" visible (F6 feature).
4. Click chatter tab — verify Trust Receipt message with JWS attached.
5. Open `account.move` (invoice) generated for the order — confirm JWS attachment is present (F6).

## 6. Acceptance Criteria

- [ ] `ir.actions.server` query returns ≥4 records with `usage='ai_tool'`.
- [ ] `action_trusteed_sign_trust_receipt.run()` returns valid Ed25519 JWS.
- [ ] Server log shows `_logger.info('trusteed.tool.sign_trust_receipt invoked')`.
- [ ] Sale order chatter has message with JWS reference.
- [ ] account.move JWS attachment present (F6).

## 7. Cleanup

```bash
docker compose -f e2e/docker/docker-compose.odoo.yml down -v
```

## 8. Troubleshooting

| Symptom                                | Fix                                                                         |
| -------------------------------------- | --------------------------------------------------------------------------- |
| 0 actions found                        | `--init=trusteed` did not load — re-run with `-u`                           |
| `KeyError: 'trusteed.bootstrap_secret'` | Re-run system parameter SQL in section 2.4                                  |
| JWS empty/None                         | Check `trusteed.api_base` reachable from container (`curl` test from inside) |
| Chatter empty                          | Ensure `order.message_subscribe()` was called before `action.run()`         |
| AP2 action returns 503                 | Expected — `SD_JWT_ENABLED=0`                                               |

---

**Owner:** spec-046 maintainer · **Last updated:** 2026-05-03
