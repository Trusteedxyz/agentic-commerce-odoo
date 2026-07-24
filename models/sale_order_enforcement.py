"""Trusteed CEL — sale.order enforcement override for Odoo 18.

Intercepts sale.order confirmation through three entry paths discovered in
the F0D spike (T000d): action_confirm (UI + RPC), create with state='sale'
(headless XML-RPC/JSON-RPC), and write with state='sale' (ORM transition).

Enforcement logic:
  1. Pull RuleSnapshot via EnforcementSnapshotService (cached 5min, Ed25519 JWS).
  2. If kill-switch active → apply fallbackMode policy (strict=block, balanced/permissive=pass).
  3. Resolve agent token from x_trusteed_agent_token field or context key.
  4. If no token → apply fallbackMode (strict blocks, balanced/permissive pass).
  5. If token present → verify via verify_agent_token().
     BLOCK decision → raise ValidationError with trusteed:R001 prefix.
     ALLOW decision → proceed to super().
  6. Any unexpected error → apply fallbackMode (never crash for balanced/permissive).

Thread-safety: Odoo 18 workers run under gevent; dict mutations are safe.
"""

import hashlib
import json
import logging
import uuid
from typing import Optional

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class SaleOrderEnforcement(models.Model):
    """Mixin that adds CEL enforcement gates to sale.order."""

    _inherit = "sale.order"

    x_trusteed_agent_token = fields.Char(
        string="Agent Token",
        copy=False,
        readonly=True,
        help="JWS Compact token issued by an agentic checkout flow. "
             "Verified by Trusteed CEL before order confirmation.",
    )

    x_trusteed_payment_method = fields.Char(
        string="Agent Payment Method",
        copy=False,
        readonly=True,
        help="Payment rail declared by the agentic client (e.g. 'stripe', 'x402', 'paypal'). "
             "Used by R022 payment-rail-restriction rule. Set via ORM/RPC at order creation.",
    )

    x_trusteed_agent_did = fields.Char(
        string="Agent DID",
        copy=False,
        readonly=True,
        help="Verified agent DID written after CEL enforcement ALLOW. "
             "Used to propagate agentIdHash to PlatformOrder for R023 refund-abuse-guard.",
    )

    # ------------------------------------------------------------------
    # Public overrides
    # ------------------------------------------------------------------

    def action_confirm(self):
        """Override: enforce CEL before manual/RPC confirmation."""
        for order in self:
            order._trusteed_cel_enforce()
        return super().action_confirm()

    @api.model_create_multi
    def create(self, vals_list):
        """Override: enforce CEL for headless creates arriving with state='sale'."""
        for vals in vals_list:
            if vals.get("state") == "sale":
                self._trusteed_cel_enforce_vals(vals)
        return super().create(vals_list)

    def write(self, vals):
        """Override: enforce CEL when a write transitions state to 'sale'."""
        if vals.get("state") == "sale":
            for order in self:
                order._trusteed_cel_enforce()
        return super().write(vals)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _trusteed_cel_enforce(self) -> None:
        """Core enforcement gate for existing record instances.

        Reads x_trusteed_agent_token from the field or from the ORM context
        key ``trusteed_agent_token`` (useful for programmatic callers that
        pass the token without persisting it first).
        """
        eval_id = str(uuid.uuid4())[:8]
        snapshot: Optional[dict] = None
        try:
            snapshot = self.env["trusteed.enforcement.snapshot"].sudo().get_snapshot()
        except Exception as exc:
            _logger.warning(
                "CEL[%s] get_snapshot error (order %s): %s", eval_id, self.id, exc
            )

        fallback_mode = _resolve_fallback_mode(snapshot)

        if snapshot is None:
            _logger.warning(
                "CEL[%s] snapshot unavailable for order %s — applying fallbackMode=%s",
                eval_id,
                self.id,
                fallback_mode,
            )
            _apply_fallback(fallback_mode, eval_id)
            return

        # Kill-switch check — HIGH-5 fix: always block regardless of fallbackMode.
        # FR-070a: kill-switch is an emergency stop for ALL agentic traffic;
        # it must not degrade to fail-open in balanced/permissive mode.
        if snapshot.get("killSwitch") is True:
            _logger.warning(
                "CEL[%s] kill-switch active for merchant %s — blocking unconditionally",
                eval_id,
                snapshot.get("merchantId", ""),
            )
            raise ValidationError(
                f"trusteed:kill_switch — Merchant agent kill-switch active. evaluationId={eval_id}"
            )

        # Resolve agent token
        token: Optional[str] = (
            self.x_trusteed_agent_token
            or self.env.context.get("trusteed_agent_token")
        )

        agent_id_verified: Optional[str] = None
        trust_score_verified: Optional[float] = None
        nonce_attrs: dict = {}

        if token:
            # Compute checkoutIntentHash for this record
            merchant_id: str = snapshot.get("merchantId", "")
            canonical = json.dumps(
                {
                    "orderId": str(self.id),
                    "merchantId": merchant_id,
                    "amount": str(self.amount_total),
                },
                sort_keys=True,
            )
            intent_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

            # Build agent DID resolver from snapshot (allowedAgents section)
            agent_did_resolver: dict[str, dict] = _build_did_resolver(snapshot)

            # Verify token
            result: Optional[dict] = None
            try:
                from .enforcement_token_verifier import verify_agent_token

                result = verify_agent_token(
                    token,
                    intent_hash,
                    merchant_id,
                    agent_did_resolver,
                )
            except Exception as exc:
                _logger.warning(
                    "CEL[%s] verify_agent_token raised unexpectedly (order %s): %s",
                    eval_id,
                    self.id,
                    exc,
                )
                _apply_fallback(fallback_mode, eval_id)
                return

            if result is None:
                raise ValidationError(
                    f"trusteed:R001 — Agent identity not verified. evaluationId={eval_id}"
                )

            agent_id_verified = result.get("agentId", "unknown")
            trust_score_verified = result.get("trustScore")

            # ── Spec-048 P2.8 — single-use replay protection (backend nonce-consume) ─
            # verify_agent_token guarantees jti + exp are present. Consume the jti
            # now so any reuse of the same token returns REPLAY. Failure_mode tri-
            # state (Gap 5/6 parity with WC/PS):
            #   ACCEPTED      → continue
            #   REPLAY        → strict raises; balanced/permissive logs + flags attr
            #   INDETERMINATE → strict raises; balanced/permissive logs + flags attr
            nonce_attrs = _consume_nonce_with_failure_mode(
                self.env,
                snapshot,
                result,
                fallback_mode,
                eval_id,
            )
        else:
            # App Store remediation (2026-07-11): previously `_apply_fallback()`
            # + `return` here meant merchant-wide policy rules (R014/R018/R019/
            # R020/R025/R027/R030 in tier2_codes below) NEVER reached evaluation
            # for a normal (non-agentic) sale order — only the generic
            # fallbackMode decision applied, agent-identity rules aside. The
            # shared Layer-2 evaluator's ruleCode-identity `appliesTo`
            # partition safely excludes AGENT-only rules (R001, etc.) when
            # `agentId` is None, so it is now safe to continue into the tier2
            # dispatch below with no token at all.
            _logger.info(
                "CEL[%s] no agent token on order %s — evaluating merchant-wide "
                "policy rules only",
                eval_id,
                self.id,
            )

        # ── Capa 2: R003-R030 historical / configurable rules ────────────────
        tier2_codes = {
            "R003", "R004", "R005", "R006", "R008", "R009", "R010",
            "R011", "R012", "R013", "R014", "R015", "R016", "R017", "R018",
            "R019", "R020", "R021", "R022", "R023", "R024", "R025", "R026",
            "R027", "R028", "R029", "R030",
        }
        enabled_rules = {
            str(r.get("ruleCode", ""))
            for r in snapshot.get("rules", [])
            if isinstance(r, dict) and r.get("enabled")
        }
        has_tier2 = bool(enabled_rules & tier2_codes)

        if has_tier2:
            # Propagate nonce-consume attrs through env context so layer2 cart_attributes
            # picks them up (REPLAY observe / INDETERMINATE observe surface to R002/R005).
            self_for_l2 = self.with_context(trusteed_nonce_attrs=nonce_attrs) if nonce_attrs else self
            layer2 = self_for_l2._trusteed_cel_call_layer2(
                agent_id_verified,
                trust_score_verified,
                snapshot,
                eval_id,
            )
            if layer2 is None:
                _logger.warning(
                    "CEL[%s] layer-2 unavailable order %s — fallbackMode=%s",
                    eval_id,
                    self.id,
                    fallback_mode,
                )
                # App Store remediation follow-up (2026-07-11/12) — before
                # falling back to the blunt fallbackMode policy (block
                # everything in strict / allow everything otherwise), try
                # the offline safety-valve evaluator against the snapshot
                # already in hand (no extra network call — `snapshot` here
                # came from `_SNAPSHOT_CACHE` or a successful earlier fetch
                # this request). Lets a merchant's own universal policy
                # rules (max order amount, blocked countries, business
                # hours, PO-box block, gift-card cap...) still fire during a
                # Layer-2-specific outage, instead of either silently
                # letting every order through or blocking every legitimate
                # human sale.
                from .offline_safety_valve_evaluator import evaluate as offline_evaluate

                offline_order_context, offline_cart_attributes = (
                    self._trusteed_offline_context()
                )
                offline_block = offline_evaluate(
                    snapshot.get("rules", []),
                    offline_order_context,
                    offline_cart_attributes,
                )
                if offline_block is not None:
                    _logger.warning(
                        "CEL[%s] offline safety valve BLOCK ruleCode=%s reason=%s",
                        eval_id,
                        offline_block["ruleCode"],
                        offline_block["reason"],
                    )
                    rule_prefix = offline_block["ruleCode"]
                    help_url = f"https://trusteed.xyz/es/agent-rules#{rule_prefix}"
                    raise ValidationError(
                        f"trusteed:{offline_block['ruleCode']} — {offline_block['reason']}. "
                        f"evaluationId={eval_id} | Más info: {help_url}"
                    )
                if fallback_mode == "strict":
                    raise ValidationError(
                        f"trusteed:CEL-TIMEOUT — Rules service unavailable. evaluationId={eval_id}"
                    )
            elif layer2.get("decision") == "BLOCK":
                rule_code = layer2.get("ruleCode", "R000")
                reason = layer2.get("reason", "Rule triggered")
                eval_id_l2 = layer2.get("evaluationId", eval_id)
                rule_prefix = rule_code.split(".")[0] if "." in rule_code else rule_code[:4]
                help_url = f"https://trusteed.xyz/es/agent-rules#{rule_prefix}"
                raise ValidationError(
                    f"trusteed:{rule_code} — {reason}. evaluationId={eval_id_l2} | Más info: {help_url}"
                )

        _logger.info(
            "CEL[%s] ALLOW order %s — agentId=%s trustScore=%s",
            eval_id,
            self.id,
            agent_id_verified,
            trust_score_verified,
        )

        # Persist verified agentDid for R023 agentIdHash propagation to PlatformOrder.
        if agent_id_verified and agent_id_verified != "unknown":
            try:
                self.sudo().write({"x_trusteed_agent_did": agent_id_verified})
            except Exception:
                pass  # Non-fatal — R023 fails open.

    def _trusteed_offline_context(self) -> tuple[dict, dict]:
        """App Store remediation follow-up (2026-07-11/12) — build the
        minimal ``(order_context, cart_attributes)`` pair the offline
        safety-valve evaluator needs (billingCountry/shippingCountry/
        cartTotalCents/itemCount/lineItems + the small subset of
        cart_attributes it reads: ``_shipping_po_box``, ``_b2b_order``,
        ``_stored_value_cents``). Deliberately a SEPARATE, smaller
        extraction from ``_trusteed_cel_call_layer2``'s own (much richer)
        context builder — this only needs to run when the network is
        already down, so it must not depend on anything that call built.

        Best-effort: any single field failing to resolve degrades to
        absent (the offline evaluator already treats missing signal as
        "no match", never as a block), never raises.
        """
        order_context: dict = {
            "cartTotalCents": int(round(self.amount_total * 100)),
            "itemCount": len(self.order_line),
        }
        cart_attributes: dict = {}

        try:
            ship_partner = getattr(self, "partner_shipping_id", None)
            if ship_partner and getattr(ship_partner, "country_id", None):
                code = ship_partner.country_id.code
                if code:
                    order_context["shippingCountry"] = code
            bill_partner = getattr(self, "partner_invoice_id", None)
            if bill_partner and getattr(bill_partner, "country_id", None):
                code = bill_partner.country_id.code
                if code:
                    order_context["billingCountry"] = code

            line_items = []
            stored_value_cents = 0
            for line in self.order_line:
                if not line.product_id:
                    continue
                line_items.append(
                    {"id": str(line.product_id.id), "qty": int(line.product_uom_qty)}
                )
                if line.product_id.detailed_type == "gift" or "gift" in (
                    line.product_id.name or ""
                ).lower():
                    stored_value_cents += int(
                        round(line.price_unit * line.product_uom_qty * 100)
                    )
            if line_items:
                order_context["lineItems"] = line_items
            if stored_value_cents > 0:
                cart_attributes["_stored_value_cents"] = str(stored_value_cents)

            if self.partner_id and (
                self.partner_id.is_company or self.partner_id.parent_id
            ):
                cart_attributes["_b2b_order"] = "true"

            if ship_partner:
                import re

                addr = " ".join(
                    filter(None, [ship_partner.street, ship_partner.street2])
                )
                if re.search(
                    r"\b(p\.?\s*o\.?\s*box|apartado|boite postale)\b",
                    addr,
                    re.IGNORECASE,
                ):
                    cart_attributes["_shipping_po_box"] = "true"
        except Exception as exc:
            _logger.debug("CEL offline-context extraction error: %s", exc)
            # Best-effort — never block on extraction failure.

        return order_context, cart_attributes

    def _trusteed_cel_call_layer2(
        self,
        agent_id: Optional[str],
        agent_trust_score: Optional[float],
        snapshot: dict,
        eval_id: str,
    ) -> Optional[dict]:
        """Call Capa-2 proxy POST /v1/rules/evaluate for R003-R010 rules.

        `agent_id` is None for an organic (non-agentic) sale order — App Store
        remediation 2026-07-11. The shared evaluator's ruleCode-identity
        `appliesTo` partition safely excludes AGENT-only rules in that case
        while still evaluating universal merchant policy rules.

        Returns the parsed response dict or None on failure (caller applies fallbackMode).
        """
        import requests  # stdlib-compatible, available in Odoo 18

        IrParam = self.env["ir.config_parameter"].sudo()
        api_base = IrParam.get_param(
            "trusteed.api_base", "https://api.trusteed.xyz"
        ).rstrip("/")
        merchant_id: str = snapshot.get("merchantId", "")
        installation_id: str = snapshot.get("installationId", "")
        hmac_secret: str = snapshot.get("hmacSecret", "")

        if not merchant_id or not installation_id or not hmac_secret:
            _logger.warning(
                "CEL[%s] layer-2 skipped — missing credentials in snapshot", eval_id
            )
            return None

        from ..utils.ssrf import validate_api_base
        if not validate_api_base(api_base):
            _logger.warning("CEL[%s] layer-2 SSRF check failed: %s", eval_id, api_base)
            return None

        # Build enriched order context
        order_lines = []
        product_categories = set()
        lowest_stock = None
        has_subscription = False
        stored_value_cents = 0
        is_b2b = False
        cart_attributes: dict[str, str] = {}
        discount_codes: list[str] = []

        try:
            # Line items
            for line in self.order_line:
                if line.product_id:
                    priceCents = int(round(line.price_unit * 100))
                    order_lines.append({
                        "id": str(line.product_id.id),
                        "qty": int(line.product_uom_qty),
                        "priceCents": priceCents,
                    })
                    # Categories — categ_id is a Many2one (single record), not a
                    # recordset to iterate. Traverse parent_id to capture the full
                    # category tree (e.g. "All / Saleable / Software" → 3 entries),
                    # so R032 category-block rules see ancestors too.
                    categ = line.product_id.categ_id
                    while categ:
                        if categ.name:
                            product_categories.add(categ.name)
                        categ = getattr(categ, "parent_id", None)
                    # Stock
                    qty_avail = line.product_id.qty_available
                    if lowest_stock is None or qty_avail < lowest_stock:
                        lowest_stock = qty_avail
                    # Subscription detection (Odoo recurring products)
                    if hasattr(line, 'recurring_invoice') and line.recurring_invoice:
                        has_subscription = True
                    # Gift card / stored value (Odoo gift card products often have type 'gift')
                    if line.product_id.detailed_type == 'gift' or 'gift' in (line.product_id.name or '').lower():
                        stored_value_cents += int(round(line.price_unit * line.product_uom_qty * 100))

            # B2B detection (Odoo: check if customer has a company)
            if self.partner_id and self.partner_id.is_company:
                is_b2b = True
            elif self.partner_id and self.partner_id.parent_id:
                is_b2b = True

            if product_categories:
                cart_attributes["_product_categories"] = ",".join(sorted(product_categories))
                cart_attributes["_product_platform"] = "odoo"
            if lowest_stock is not None:
                cart_attributes["_lowest_stock"] = str(int(lowest_stock))
            if has_subscription:
                cart_attributes["_subscription"] = "true"
                cart_attributes["_autorenew"] = "true"
            if stored_value_cents > 0:
                cart_attributes["_stored_value_cents"] = str(stored_value_cents)
            if is_b2b:
                cart_attributes["_b2b_order"] = "true"

            # Spec-048 P2.8 — merge nonce-consume flags (replay / indeterminate)
            # passed via env context by _consume_nonce_with_failure_mode().
            nonce_flags = self.env.context.get("trusteed_nonce_attrs") or {}
            if isinstance(nonce_flags, dict):
                cart_attributes.update(nonce_flags)

            # Coupon codes
            discount_codes = []
            if hasattr(self, 'coupon_ids'):
                discount_codes = [c.code for c in self.coupon_ids if c.code]
            elif hasattr(self, 'code_promo_program_id') and self.code_promo_program_id:
                discount_codes = [self.code_promo_program_id.name]

            # Shipping address for PO box detection
            if self.partner_shipping_id:
                import re
                addr = " ".join(filter(None, [
                    self.partner_shipping_id.street,
                    self.partner_shipping_id.street2,
                ]))
                if re.search(r'\b(p\.?\s*o\.?\s*box|apartado|boite postale)\b', addr, re.IGNORECASE):
                    cart_attributes["_shipping_po_box"] = "true"

        except Exception as exc:
            _logger.warning("CEL[%s] cart context extraction error: %s", eval_id, exc)
            # Best-effort — never block on extraction failure

        # ── R004 / R008 / R013: JWT-based attributes (single decode pass) ──────
        # Resolve agent token: prefer field value, fall back to ORM context.
        try:
            import base64 as _b64
            import json as _json2
            import time as _time
            agent_token: Optional[str] = (
                self.x_trusteed_agent_token
                or self.env.context.get("trusteed_agent_token")
            )
            jwt_payload: dict = {}
            if agent_token:
                parts = agent_token.split(".")
                if len(parts) == 3:
                    # Decode header — R004: key-age tracking via kid
                    header_padded = parts[0] + "=" * (-len(parts[0]) % 4)
                    try:
                        header = _json2.loads(_b64.urlsafe_b64decode(header_padded).decode("utf-8"))
                        kid = header.get("kid", "")
                        if kid:
                            param_key = (
                                "trusteed.cel.kid_first_seen."
                                + hashlib.sha256(kid.encode()).hexdigest()[:16]
                            )
                            IrParam = self.env["ir.config_parameter"].sudo()
                            first_seen_str = IrParam.get_param(param_key)
                            if not first_seen_str:
                                first_seen_ts = int(_time.time())
                                IrParam.set_param(param_key, str(first_seen_ts))
                            else:
                                first_seen_ts = int(first_seen_str)
                            age_hours = (_time.time() - first_seen_ts) / 3600.0
                            cart_attributes["_agent_key_age_hours"] = str(round(age_hours, 2))
                    except Exception:
                        pass

                    # Decode payload — R008: declared scopes; R013: return_policy claim
                    payload_padded = parts[1] + "=" * (-len(parts[1]) % 4)
                    try:
                        jwt_payload = _json2.loads(
                            _b64.urlsafe_b64decode(payload_padded).decode("utf-8")
                        )
                    except Exception:
                        jwt_payload = {}

                    # R008: Declared scopes
                    scope = jwt_payload.get("scope", "")
                    if scope:
                        cart_attributes["_requested_scopes"] = str(scope)

            # R013: Return-policy mismatch — compare agent claim vs cart items
            agent_return_policy = jwt_payload.get("return_policy", "").lower()
            has_final_sale = any(
                line.product_id.detailed_type == "service"
                for line in self.order_line
                if line.product_id
            )
            if has_final_sale and agent_return_policy != "final_sale":
                cart_attributes["_return_policy_mismatch"] = "true"

            # R028: B2B purchase-order hash — propagate only when order is B2B.
            po_hash = jwt_payload.get("purchaseOrderHash", "").strip()
            if is_b2b and po_hash:
                cart_attributes["_purchase_order_hash"] = po_hash
        except Exception as exc:
            _logger.debug("CEL[%s] R004/R008/R013 extraction error: %s", eval_id, exc)
            # Fail silently — never block checkout on rule-attribute extraction

        # ── R015: Price delta — order line price vs current product list price ──
        try:
            max_delta_bps = 0
            for line in self.order_line:
                if not line.product_id:
                    continue
                list_price_cents = int(round(line.product_id.list_price * 100))
                line_price_cents = int(round(line.price_unit * 100))
                if list_price_cents > 0:
                    delta_bps = int(
                        round(abs(line_price_cents - list_price_cents) / list_price_cents * 10000)
                    )
                    if delta_bps > max_delta_bps:
                        max_delta_bps = delta_bps
            if max_delta_bps > 0:
                cart_attributes["_price_delta_bps"] = str(max_delta_bps)
        except Exception as exc:
            _logger.debug("CEL[%s] R015 price-delta extraction error: %s", eval_id, exc)
            # Fail silently — never block checkout on rule-attribute extraction

        # Shipping/billing country — best-effort; partner fields may be unset.
        shipping_country = ""
        billing_country = ""
        ship_partner = getattr(self, "partner_shipping_id", None)
        if ship_partner and getattr(ship_partner, "country_id", None):
            shipping_country = ship_partner.country_id.code or ""
        bill_partner = getattr(self, "partner_invoice_id", None)
        if bill_partner and getattr(bill_partner, "country_id", None):
            billing_country = bill_partner.country_id.code or ""

        payment_method = self._trusteed_resolve_payment_method()

        payload = {
            "merchantId": merchant_id,
            "agentId": agent_id,
            "orderContext": {
                "cartTotalCents": int(round(self.amount_total * 100)),
                "currency": self.currency_id.name if self.currency_id else "EUR",
                "itemCount": len(self.order_line),
                "agentTrustScore": agent_trust_score,
                **({"billingCountry": billing_country} if billing_country else {}),
                **({"shippingCountry": shipping_country} if shipping_country else {}),
                **({"paymentMethod": payment_method} if payment_method else {}),
                **({"lineItems": order_lines} if order_lines else {}),
                **({"discountCodes": discount_codes} if discount_codes else {}),
                **({"cartAttributes": cart_attributes} if cart_attributes else {}),
            },
            "platform": "ODOO",
            "installationId": installation_id,
            "timestamp": fields.Datetime.now().isoformat() + "Z",
        }

        import json as _json
        import hmac as _hmac
        import hashlib
        import time

        body_bytes = _json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ts = str(int(time.time()))
        to_sign = (ts + "." + body_bytes.decode("utf-8")).encode("utf-8")
        mac = _hmac.new(hmac_secret.encode("utf-8"), to_sign, hashlib.sha256).hexdigest()
        sig = f"t={ts},s={mac}"

        url = f"{api_base}/v1/rules/evaluate"
        try:
            resp = requests.post(
                url,
                data=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Trusteed-Installation-Id": installation_id,
                    "X-Trusteed-Signature": sig,
                },
                timeout=5,
            )
        except Exception as exc:
            _logger.warning("CEL[%s] layer-2 request failed: %s", eval_id, exc)
            return None

        if resp.status_code != 200:
            _logger.warning(
                "CEL[%s] layer-2 returned HTTP %s", eval_id, resp.status_code
            )
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        if not isinstance(data, dict) or "decision" not in data:
            return None

        return data

    def action_open_trusteed_stats(self):
        """T095: Open Trusteed Trust Center stats overview in a new browser tab.

        Shown as a button on sale.order form when x_trusteed_agent_token is set.
        Navigates to the trust overview page scoped to the current merchant.
        """
        IrParam = self.env["ir.config_parameter"].sudo()
        api_base = IrParam.get_param(
            "trusteed.api_base", "https://api.trusteed.xyz"
        ).rstrip("/")
        merchant_id = IrParam.get_param("trusteed.merchant_id", "")

        url = f"{api_base}/v1/trust/overview"
        if merchant_id:
            url += f"?merchantId={merchant_id}"

        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }

    def _trusteed_resolve_payment_method(self) -> str:
        """Return the payment gateway slug for R022 evaluation.

        Priority:
        1. x_trusteed_payment_method — agent-declared via ORM/RPC (most reliable).
        2. transaction_ids provider — ecommerce path when payment module is installed.
        3. Empty string — no gateway known; R022 will PASS (fail-open per spec).

        payment_term_id.name is intentionally excluded: it is a payment *term*
        (e.g. "30 days net"), not a gateway, and would cause false R022 positives.
        """
        if self.x_trusteed_payment_method:
            return str(self.x_trusteed_payment_method).lower().strip()

        # Safe fallback: transaction_ids only exists when the `payment` module is
        # installed (not a hard dependency of this addon).
        if hasattr(self, "transaction_ids") and self.transaction_ids:
            done_tx = self.transaction_ids.filtered(
                lambda t: t.state in ("done", "authorized")
            ).sorted("create_date", reverse=True)[:1]
            if done_tx:
                provider = done_tx.provider_id
                code = getattr(provider, "code", None) or getattr(provider, "name", None) or ""
                if code:
                    return str(code).lower().strip()

        return ""

    def _trusteed_cel_enforce_vals(self, vals: dict) -> None:
        """Enforcement gate for create() paths where self.id does not yet exist.

        Uses vals dict to extract token and compose a minimal intent hash.
        Records an eval_id in the log for auditability.
        """
        eval_id = str(uuid.uuid4())[:8]
        snapshot: Optional[dict] = None
        try:
            snapshot = self.env["trusteed.enforcement.snapshot"].sudo().get_snapshot()
        except Exception as exc:
            _logger.warning(
                "CEL[%s] get_snapshot error (headless create): %s", eval_id, exc
            )

        fallback_mode = _resolve_fallback_mode(snapshot)

        if snapshot is None:
            _logger.warning(
                "CEL[%s] snapshot unavailable (headless create) — fallbackMode=%s",
                eval_id,
                fallback_mode,
            )
            _apply_fallback(fallback_mode, eval_id)
            return

        # Kill-switch — FR-070a: emergency stop for ALL agentic traffic. Must
        # block unconditionally, mirroring _trusteed_cel_enforce(); never degrade
        # to fail-open in balanced/permissive on the headless create path.
        if snapshot.get("killSwitch") is True:
            _logger.warning(
                "CEL[%s] kill-switch active (headless create) for merchant %s — "
                "blocking unconditionally",
                eval_id,
                snapshot.get("merchantId", ""),
            )
            raise ValidationError(
                f"trusteed:kill_switch — Merchant agent kill-switch active. evaluationId={eval_id}"
            )

        token: Optional[str] = (
            vals.get("x_trusteed_agent_token")
            or self.env.context.get("trusteed_agent_token")
        )

        if not token:
            _logger.info(
                "CEL[%s] no agent token in create vals — fallbackMode=%s",
                eval_id,
                fallback_mode,
            )
            _apply_fallback(fallback_mode, eval_id)
            return

        merchant_id: str = snapshot.get("merchantId", "")
        # orderId is not yet assigned; use a deterministic placeholder so the
        # checkoutIntentHash binding still covers amount + merchantId.
        canonical = json.dumps(
            {
                "orderId": "pending",
                "merchantId": merchant_id,
                "amount": str(vals.get("amount_total", "")),
            },
            sort_keys=True,
        )
        intent_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        agent_did_resolver: dict[str, dict] = _build_did_resolver(snapshot)

        result: Optional[dict] = None
        try:
            from .enforcement_token_verifier import verify_agent_token

            result = verify_agent_token(
                token,
                intent_hash,
                merchant_id,
                agent_did_resolver,
            )
        except Exception as exc:
            _logger.warning(
                "CEL[%s] verify_agent_token raised unexpectedly (headless create): %s",
                eval_id,
                exc,
            )
            _apply_fallback(fallback_mode, eval_id)
            return

        if result is None:
            raise ValidationError(
                f"trusteed:R001 — Agent identity not verified. evaluationId={eval_id}"
            )

        # ── Capa 2 skipped for headless creates ──────────────────────────────
        # _trusteed_cel_enforce_vals() is called from @api.model_create_multi
        # before the record is persisted, so self.id / self.amount_total /
        # self.order_line are not available as ORM fields.  R001 (Capa 1)
        # already verified the agent token and cartHash binding, which provides
        # the primary identity guard.  R003-R010 (Capa 2) require a live record
        # for accurate order context and are evaluated by _trusteed_cel_enforce()
        # if a subsequent write() transitions state to 'sale'.
        _logger.info(
            "CEL[%s] ALLOW headless create — agentId=%s trustScore=%s",
            eval_id,
            result.get("agentId"),
            result.get("trustScore"),
        )


# ------------------------------------------------------------------
# Module-level helpers (no ORM access, pure functions)
# ------------------------------------------------------------------


def _consume_nonce_with_failure_mode(
    env,
    snapshot: dict,
    verify_result: dict,
    fallback_mode: str,
    eval_id: str,
) -> dict:
    """Spec-048 P2.8 — consume jti against backend, apply tri-state failure_mode.

    Returns a dict of cartAttributes to merge into the layer-2 payload. Raises
    ``ValidationError`` on strict-fail-closed paths (REPLAY in strict,
    INDETERMINATE in strict). Mirrors PS ValidateOrderHook::consumeAgentNonce()
    and WC Amcp_Checkout_Enforcer::enrich_with_token_verification().
    """
    from . import nonce_consumer

    jti = str(verify_result.get("jti", ""))
    agent_did = str(verify_result.get("agentId", ""))
    exp = int(verify_result.get("exp", 0))
    if not jti or not agent_did:
        # verify_agent_token guarantees both — defensive return.
        return {}

    IrParam = env["ir.config_parameter"].sudo()
    api_base = IrParam.get_param("trusteed.api_base", "https://api.trusteed.xyz")
    merchant_id = str(snapshot.get("merchantId", ""))
    installation_id = str(snapshot.get("installationId", ""))
    hmac_secret = str(snapshot.get("hmacSecret", ""))

    if not merchant_id or not installation_id or not hmac_secret:
        # No backend credentials — skip consume, log INDETERMINATE telemetry.
        _logger.warning(
            "CEL[%s] nonce-consume skipped — missing credentials (fallback=%s)",
            eval_id,
            fallback_mode,
        )
        if fallback_mode == "strict":
            raise ValidationError(
                f"trusteed:CEL-NONCE-UNAVAILABLE — Replay protection credentials missing. evaluationId={eval_id}"
            )
        return {"_agent_token_nonce_unavailable": "true"}

    outcome_dict = nonce_consumer.consume_nonce(
        api_base=api_base,
        merchant_id=merchant_id,
        installation_id=installation_id,
        hmac_secret=hmac_secret,
        agent_did=agent_did,
        jti=jti,
        exp=exp,
    )
    outcome = outcome_dict.get("outcome", nonce_consumer.INDETERMINATE)
    reason = outcome_dict.get("reason", "")

    if outcome == nonce_consumer.ACCEPTED:
        return {}

    if outcome == nonce_consumer.REPLAY:
        _logger.warning(
            "CEL[%s] enforcement_replay — agent=%s fallback=%s",
            eval_id,
            agent_did,
            fallback_mode,
        )
        if fallback_mode == "strict":
            raise ValidationError(
                f"trusteed:R002 — Agent token replay detected. evaluationId={eval_id}"
            )
        return {
            "_agent_token_signature_invalid": "true",
            "_agent_token_replay": "true",
        }

    # INDETERMINATE
    _logger.warning(
        "CEL[%s] enforcement_indeterminate (nonce) — reason=%s fallback=%s",
        eval_id,
        reason,
        fallback_mode,
    )
    if fallback_mode == "strict":
        raise ValidationError(
            f"trusteed:CEL-NONCE-UNAVAILABLE — Replay protection service unavailable. evaluationId={eval_id}"
        )
    return {"_agent_token_nonce_unavailable": "true"}


def _resolve_fallback_mode(snapshot: Optional[dict]) -> str:
    """Return the fallbackMode string from snapshot, defaulting to 'balanced'."""
    if snapshot is None:
        return "balanced"
    return snapshot.get("fallbackMode", "balanced")


def _apply_fallback(fallback_mode: str, eval_id: str) -> None:
    """Apply fallback policy.

    - strict   → raise ValidationError (fail-closed)
    - balanced → log warning and pass (fail-open)
    - permissive → pass silently
    """
    if fallback_mode == "strict":
        raise ValidationError(
            f"trusteed:R001 — Agent identity not verified. evaluationId={eval_id}"
        )
    if fallback_mode == "balanced":
        _logger.warning(
            "CEL[%s] fail-open (balanced) — proceeding without agent verification",
            eval_id,
        )
    # permissive: pass silently


def _build_did_resolver(snapshot: dict) -> dict[str, dict]:
    """Extract {agentDid: {x: <base64url-pubkey>}} from snapshot.agentDidResolver.

    MEDIUM-3 fix: the snapshot uses `agentDidResolver` (same key as TypeScript/
    PHP/Rust). Previous code read `allowedAgents` which is not in the snapshot
    schema — so the resolver was always empty.

    Expected shape: { "<did>": { "x": "<base64url 32-byte Ed25519 pubkey>" } }
    """
    resolver: dict[str, dict] = {}
    did_resolver = snapshot.get("agentDidResolver", {})
    if not isinstance(did_resolver, dict):
        return resolver
    for did, jwk in did_resolver.items():
        if isinstance(did, str) and did and isinstance(jwk, dict) and jwk.get("x"):
            resolver[did] = {"x": jwk["x"]}
    return resolver
