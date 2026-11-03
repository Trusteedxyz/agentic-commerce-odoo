"""Trusteed CEL — sale.order enforcement override for Odoo 18.

Intercepts sale.order confirmation through three entry paths discovered in
the F0D spike (T000d): action_confirm (UI + RPC), create with state='sale'
(headless XML-RPC/JSON-RPC), and write with state='sale' (ORM transition).

Enforcement logic:
  1. Resolve agent token from x_trusteed_agent_token field or context key.
     A token — valid or not — makes the order agentic (plan 04, Task 13).
  2. Pull RuleSnapshot via EnforcementSnapshotService (cached 5min, Ed25519 JWS).
  3. If kill-switch active and the order is agentic → block (any fallbackMode).
     A human order is never stopped by the kill-switch (FR-070a).
  4. If no token → human order: only the merchant's rules that apply to
     everyone (appliesTo ALL, e.g. the R030 cap) can block it, also offline.
  5. If token present → verify via verify_agent_token().
     BLOCK decision → raise ValidationError with trusteed:R001 prefix.
     ALLOW decision → proceed to super().
  6. No verified snapshot → decided by cause (plan 04, Task 21): a snapshot
     that fails its signature holds agents in every mode; a missing snapshot
     holds an agent WITH token in every mode and lets the mode decide without
     token; CEL disabled / not configured holds nobody. Rules service down
     with a verified snapshot → fallbackMode decides for AGENTS only (strict
     or unresolved blocks, balanced/permissive pass). A human order passes in
     all these cases (only the merchant's own ALL rules of a verified snapshot
     can block it). Our own unexpected error → human passes, agent blocked in
     every mode (``platform_flow``).
  7. Installation id + HMAC secret for layer 2 and nonce-consume come from the
     addon settings (``get_credentials``), never from the snapshot payload.

Thread-safety: Odoo 18 workers run under gevent; dict mutations are safe.
"""

import hashlib
import json
import logging
import re
import uuid
from typing import Optional

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..utils.agent_reason import REASON_CHECKS_UNAVAILABLE, block_message

_logger = logging.getLogger(__name__)

# Auditoría 2026-09-27 (C1) — qué reglas del snapshot necesitan capa 2.
#
# El snapshot trae el `ruleCode` tal como está en BD, y el panel escribe el
# código canónico (`R030.simple-controls`). Antes se comparaba con un conjunto
# de códigos CORTOS escrito a mano que además se cortaba en R030: con
# canónicos la intersección era vacía y Odoo nunca llamaba al servidor.
#
# Ahora la lista se DERIVA del snapshot: `POST /v1/rules/evaluate` corre el
# catálogo entero (R031+ y R007 incluidas), así que toda regla activa — en
# ENFORCE u OBSERVE, igual que `isModeActiveRule` del evaluador — va a capa 2,
# salvo las que Odoo resuelve en local sin servidor.
_LOCALLY_RESOLVED_SHORT_CODES = frozenset({"R001"})  # identidad: verify_agent_token
_RULE_SHORT_CODE_RE = re.compile(r"^R\d{3}$")


def _short_rule_code(rule_code: str) -> str:
    """``"R030.simple-controls"`` → ``"R030"``; un código corto se devuelve igual."""
    return rule_code.split(".", 1)[0]


def _server_evaluated_rule_codes(snapshot: dict) -> set:
    """Códigos cortos de las reglas activas que evalúa el servidor."""
    codes = set()
    for rule in snapshot.get("rules", []):
        if not isinstance(rule, dict):
            continue
        active = bool(rule.get("enabled")) or rule.get("mode") == "observe"
        if not active:
            continue
        short = _short_rule_code(str(rule.get("ruleCode", "")))
        if _RULE_SHORT_CODE_RE.match(short) and short not in _LOCALLY_RESOLVED_SHORT_CODES:
            codes.add(short)
    return codes


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
        """Override: enforce CEL before manual/RPC confirmation.

        ``super().action_confirm()`` confirms through ``write({'state': 'sale'})``;
        the orders evaluated here are marked so that ``write`` does not
        evaluate them a second time (a second pass would see its own token as
        a replay) — ``agent_did_guard``.
        """
        from .agent_did_guard import evaluated_marker

        for order in self:
            order._trusteed_cel_enforce()
        marked = self.with_context(**evaluated_marker(self.ids))
        return super(SaleOrderEnforcement, marked).action_confirm()

    @api.model_create_multi
    def create(self, vals_list):
        """Override: enforce CEL for headless creates arriving with state='sale'.

        ``x_trusteed_agent_did`` is dropped from caller vals (Task 21): only the
        addon writes it, after a verified and consumed token.
        """
        from .agent_did_guard import (
            context_without_did_default,
            headless_view,
            strip_agent_did,
        )
        from .headless_human_valve import enforce_created_human_orders

        vals_list = [strip_agent_did(vals, self.env.context) for vals in vals_list]
        for vals in vals_list:
            # Lo que el ORM va a escribir: ``default_state``/``default_x_trusteed_
            # agent_token`` del contexto rellenan lo que ``vals`` no trae.
            effective = headless_view(vals, self.env.context)
            if effective.get("state") == "sale":
                self._trusteed_cel_enforce_vals(effective)
        # ``default_x_trusteed_agent_did`` del contexto llenaría el campo que
        # ``strip_agent_did`` acaba de quitar de ``vals`` (M2).
        clean_ctx = context_without_did_default(self.env.context)
        target = self.with_context(clean_ctx) if clean_ctx is not None else self
        records = super(SaleOrderEnforcement, target).create(vals_list)
        # Task 38d: la persona que crea ya en 'sale' recibe la válvula offline
        # (reglas ALL, tope R030) que ``action_confirm`` ya le aplica.
        enforce_created_human_orders(records, _company_id_of, _reraise_if_db_error)
        return records

    def write(self, vals):
        """Override: enforce CEL when a write transitions state to 'sale'."""
        from .agent_did_guard import already_evaluated, strip_agent_did

        vals = strip_agent_did(vals, self.env.context)
        if vals.get("state") == "sale":
            for order in self:
                if already_evaluated(self.env.context, order.id):
                    continue
                order._trusteed_cel_enforce()
        return super().write(vals)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _trusteed_cel_enforce(self) -> None:
        """Guarded entry (plan 04, Task 13): our own exception never blocks a
        human and never opens an agent — see ``_on_internal_error``."""
        token: Optional[str] = (
            self.x_trusteed_agent_token or self.env.context.get("trusteed_agent_token")
        )
        try:
            self._trusteed_cel_enforce_unguarded(token)
        except ValidationError:
            raise
        except Exception as exc:
            _reraise_if_db_error(exc)
            _on_internal_error(bool(token), f"order {self.id}", exc)

    def _trusteed_cel_enforce_unguarded(self, token: Optional[str]) -> None:
        """Core enforcement gate for existing record instances.

        ``token`` is x_trusteed_agent_token from the field or from the ORM
        context key ``trusteed_agent_token`` (useful for programmatic callers
        that pass the token without persisting it first), read ONCE by
        ``_trusteed_cel_enforce``. A token that is present but does not verify
        still makes the order agentic (plan 04: «token inválido = agente»).
        """
        from .platform_flow import (
            decide_on_degraded_enforcement,
            is_agentic_checkout,
            offline_valve_rules_for,
        )

        is_agentic = is_agentic_checkout(None, bool(token), False)
        eval_id = str(uuid.uuid4())[:8]
        # Task 35b (M3): la configuración (modo, credenciales, snapshot) es la
        # de la compañía del PEDIDO, nunca ``env.company`` (que el cliente RPC
        # elige con ``allowed_company_ids``).
        company_id = _company_id_of(self)
        if company_id is None:
            _apply_company_unresolved(eval_id, is_agentic, f"order {self.id}")
            return
        snapshot, degraded_kind, local_mode = self._trusteed_cel_snapshot(eval_id, company_id)

        fallback_mode = _resolve_fallback_mode(snapshot)

        if snapshot is None:
            # Task 21: se decide con la CAUSA antes de salir (I1-Odoo). Con
            # token y sin snapshot verificado no hay resolver con el que
            # verificarlo: el agente queda retenido en todo modo.
            _apply_degraded(degraded_kind, local_mode, eval_id, is_agentic, bool(token))
            return

        # Kill-switch check — HIGH-5 fix: always block regardless of fallbackMode.
        # FR-070a: kill-switch is an emergency stop for ALL agentic traffic
        # — y sólo ese (auditoría 2026-09-27): a human order is never stopped
        # by it; it must not degrade to fail-open in balanced/permissive mode.
        if is_agentic and snapshot.get("killSwitch") is True:
            _logger.warning(
                "CEL[%s] kill-switch active for merchant %s — blocking unconditionally",
                eval_id,
                snapshot.get("merchantId", ""),
            )
            raise ValidationError(
                f"trusteed:kill_switch — Merchant agent kill-switch active. evaluationId={eval_id}"
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
                _reraise_if_db_error(exc)
                # Una avería NUESTRA con un agente: no es un permiso en ningún
                # modo ni se publica como la regla R001 (guarda de la Task 13).
                _on_internal_error(True, f"order {self.id} verify_agent_token", exc)

            if result is None:
                raise ValidationError(
                    f"trusteed:R001 — Agent identity not verified. evaluationId={eval_id}"
                )

            agent_id_verified = result.get("agentId", "unknown")
            trust_score_verified = result.get("trustScore")

            # ── Spec-048 P2.8 — single-use replay protection (backend nonce-consume) ─
            # verify_agent_token guarantees nonce (or its jti alias) + exp are present. Consume it
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
                self._trusteed_cel_credentials(),
            )
        else:
            # App Store remediation (2026-07-11): previously `_apply_fallback()`
            # + `return` here meant merchant-wide policy rules (R014/R018/R019/
            # R020/R025/R027/R030 y las que deriva `_server_evaluated_rule_codes`)
            # NEVER reached evaluation for a normal (non-agentic) sale order — only
            # the generic fallbackMode decision applied, agent-identity rules aside.
            # The shared Layer-2 evaluator's ruleCode-identity `appliesTo` partition
            # safely excludes AGENT-only rules (R001, etc.) when `agentId` is None,
            # so it is now safe to continue into the dispatch below with no token at all.
            _logger.info(
                "CEL[%s] no agent token on order %s — evaluating merchant-wide "
                "policy rules only",
                eval_id,
                self.id,
            )

        # ── Capa 2: toda regla activa que evalúa el servidor ──────────────────
        # Derivado del snapshot (ver `_server_evaluated_rule_codes`).
        has_tier2 = bool(_server_evaluated_rule_codes(snapshot))

        if has_tier2:
            # Nonce-consume attrs (REPLAY / INDETERMINATE surface to R002/R005) go
            # to layer 2 as an ARGUMENT: the env context is client-controlled
            # over RPC and must never feed a rule signal (Task 27a, M1).
            # L1: un token REUTILIZADO viaja como agente anónimo — ni su DID
            # (la allowlist del servidor podía dar ALLOW antes de R030 y su
            # historial entraría en R010/R021/R044) ni el score de esa
            # identidad; ``endpointClass`` lo sigue marcando agéntico.
            replayed = nonce_attrs.get("_agent_token_replay") == "true"
            layer2 = self._trusteed_cel_call_layer2(
                None if replayed else agent_id_verified,
                None if replayed else trust_score_verified,
                snapshot,
                eval_id,
                is_agentic=is_agentic,
                nonce_attrs=nonce_attrs,
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
                # M1: actor signal for the valve's currency-not-comparable branch.
                offline_order_context = {**offline_order_context, "agentCheckout": is_agentic}
                # A un humano sólo le aplican las reglas appliesTo ALL/HUMAN.
                offline_block = offline_evaluate(
                    offline_valve_rules_for(snapshot.get("rules", []), is_agentic),
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
                    # Frase genérica: ni regla ni umbral de la válvula salen del servidor.
                    raise ValidationError(block_message(None, eval_id))
                # D1: el fallbackMode sólo gobierna a los agentes.
                if decide_on_degraded_enforcement(
                    "evaluator_unavailable", is_agentic, bool(token), fallback_mode, False
                ) == "BLOCK":
                    raise ValidationError(
                        f"trusteed:CEL-TIMEOUT — {REASON_CHECKS_UNAVAILABLE} evaluationId={eval_id}"
                    )
            elif layer2.get("decision") == "BLOCK":
                eval_id_l2 = layer2.get("evaluationId", eval_id)
                # El reason/ruleCode crudos (con umbrales) sólo al log; al comprador, agent_reason.
                _logger.info(
                    "CEL[%s] layer-2 BLOCK ruleCode=%s reason=%s",
                    eval_id_l2, layer2.get("ruleCode"), layer2.get("reason"),
                )
                raise ValidationError(block_message(
                    layer2.get("agent_reason"), eval_id_l2,
                    self.currency_id.name if self.currency_id else None,
                ))

        _logger.info(
            "CEL[%s] ALLOW order %s — agentId=%s trustScore=%s",
            eval_id,
            self.id,
            agent_id_verified,
            trust_score_verified,
        )

        # Persist verified agentDid for R023 agentIdHash propagation to PlatformOrder.
        # Task 21: only when the token verified AND its jti was consumed
        # (nonce ACCEPTED ⇒ ``nonce_attrs`` empty). A replayed or unconsumed
        # token (REPLAY / INDETERMINATE let through in balanced/permissive)
        # never attributes history to that DID.
        if agent_id_verified and agent_id_verified != "unknown" and not nonce_attrs:
            try:
                from .agent_did_guard import internal_did_write_context

                self.sudo().with_context(**internal_did_write_context()).write(
                    {"x_trusteed_agent_did": agent_id_verified}
                )
            except Exception as exc:
                _reraise_if_db_error(exc)
                # Non-fatal — R023 fails open.

    def _trusteed_cel_snapshot(self, eval_id: str, company_id: int):
        """``(payload | None, degraded kind | None, local fallbackMode)`` (Task 21).

        Translates the snapshot service reason to ``platform_flow`` kinds:
        ``tampered`` → ``invalid_signature``; ``unavailable`` (or our own
        exception reading it) → ``missing_snapshot``; ``layer_disabled`` →
        ``layer_disabled``; ``ok`` → payload, no kind.

        ``company_id`` is the ORDER's company (Task 35b), passed explicitly.
        """
        try:
            res = self.env["trusteed.enforcement.snapshot"].sudo().get_snapshot_result(company_id)
        except Exception as exc:
            _reraise_if_db_error(exc)
            _logger.warning("CEL[%s] get_snapshot error: %s", eval_id, exc)
            return None, "missing_snapshot", None
        status = res.get("status")
        local_mode = res.get("localFallbackMode")
        if status == "ok" and isinstance(res.get("payload"), dict):
            return res["payload"], None, local_mode
        kind = {
            "tampered": "invalid_signature",
            "layer_disabled": "layer_disabled",
        }.get(status, "missing_snapshot")
        _logger.warning("CEL[%s] snapshot not usable (%s) → %s", eval_id, status, kind)
        return None, kind, local_mode

    def _trusteed_cel_credentials(self) -> dict:
        """Installation credentials of the ORDER's company (Tasks 21 and 35b).

        They come from the addon settings (``get_credentials``), the same ones
        that sign the snapshot pull — never from the snapshot payload, which
        does not carry them. The company is the order's, not ``env.company``;
        an order without a determinable company raises (the guards turn that
        into: agent held / person passes).
        """
        company_id = _company_id_of(self)
        if company_id is None:
            raise ValueError("order without a determinable company")
        return self.env["trusteed.enforcement.snapshot"].sudo().get_credentials(company_id)

    def _trusteed_signals_provided(self) -> list:
        """Spec-048 4.9 — señales de carrito que ESTA instalación sabe proyectar.

        Es la mitad "qué aporto" del contrato cuya otra mitad es
        ``RULE_SIGNALS_READ`` ("qué lee cada regla") en el servidor. Cruzarlas
        convierte el silencio de ``NO_SIGNAL`` —una regla activada en ENFORCE
        que nunca dispara porque su señal no llega— en un aviso que el
        comerciante ve al activarla.

        La lista NO se mantiene a ojo: el gate
        ``signals-provided-plugin-declarations.test.ts`` (en
        ``packages/shared``) escanea el cuerpo de este modelo y exige que
        coincida exactamente con las claves que escribe. Corre siempre, que es
        más de lo que puede decirse de esta suite: a este entorno le falta el
        módulo ``odoo`` y los tests ni se recolectan.
        """
        return [
            "_agent_key_age_hours",
            "_agent_token_nonce_unavailable",
            "_agent_token_replay",
            "_agent_token_signature_invalid",
            "_autorenew",
            "_b2b_order",
            "_lowest_stock",
            "_price_delta_bps",
            "_product_categories",
            "_product_platform",
            "_purchase_order_hash",
            "_requested_scopes",
            "_return_policy_mismatch",
            "_shipping_po_box",
            "_stored_value_cents",
            "_subscription",
        ]

    # SIGNALS_PROVIDED_BUILDER_START — ancla del gate de 4.9: todo lo que se
    # escriba en ``cart_attributes`` de aquí abajo tiene que estar en
    # ``_trusteed_signals_provided()``.
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
        is_agentic: bool = False,
        nonce_attrs: Optional[dict] = None,
    ) -> Optional[dict]:
        """Call Capa-2 proxy POST /v1/rules/evaluate for R003-R010 rules.

        ``is_agentic`` adds ``endpointClass: "agentic_endpoint"`` (last key)
        so the server classifies the order as agentic even without a
        verified ``agentId``; an organic payload stays byte-for-byte as before.

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
        # Task 21: installation id + secret come from the addon settings; the
        # signed snapshot never carries them, so reading them from it skipped
        # layer 2 on every order.
        creds = self._trusteed_cel_credentials()
        installation_id: str = str(creds.get("installationId") or "")
        hmac_secret: str = str(creds.get("hmacSecret") or "")

        if not merchant_id or not installation_id or not hmac_secret:
            _logger.warning(
                "CEL[%s] layer-2 skipped — missing installation credentials", eval_id
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
            # computed by _consume_nonce_with_failure_mode(). They come ONLY
            # from the ``nonce_attrs`` argument: never from ``env.context``,
            # which an RPC client fully controls (Task 27a, M1).
            if isinstance(nonce_attrs, dict):
                cart_attributes.update(nonce_attrs)

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
            **({"endpointClass": "agentic_endpoint"} if is_agentic else {}),
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

        # agent_reason aditivo: se conserva saneado (claves validadas) o se descarta.
        from ..utils.agent_reason import sanitize_agent_reason
        clean = sanitize_agent_reason(data.get("agent_reason"))
        data = {k: v for k, v in data.items() if k != "agent_reason"}
        if clean is not None:
            data["agent_reason"] = clean
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
        """Guarded entry for headless create (plan 04, Task 13) — see
        ``_trusteed_cel_enforce``."""
        token: Optional[str] = (
            vals.get("x_trusteed_agent_token") or self.env.context.get("trusteed_agent_token")
        )
        try:
            self._trusteed_cel_enforce_vals_unguarded(vals, token)
        except ValidationError:
            raise
        except Exception as exc:
            _reraise_if_db_error(exc)
            _on_internal_error(bool(token), "headless create", exc)

    def _trusteed_cel_enforce_vals_unguarded(self, vals: dict, token: Optional[str]) -> None:
        """Enforcement gate for create() paths where self.id does not yet exist.

        ``token`` is read once by ``_trusteed_cel_enforce_vals``. Records an
        eval_id in the log for auditability. A human create in 'sale' is not
        evaluated (known gap, ledger); an agent one is rejected — see the end.
        """
        from .platform_flow import is_agentic_checkout

        is_agentic = is_agentic_checkout(None, bool(token), False)
        eval_id = str(uuid.uuid4())[:8]
        # Task 35b (M3): sin registro, la compañía sale de ``vals['company_id']``
        # (explícito). El default del ORM (``env.company``) y ``default_company_id``
        # del contexto los elige el cliente: no son evidencia. Sin compañía
        # determinable un agente se retiene; una persona pasa (D1).
        company_id = _company_id_from_vals(vals)
        if company_id is None:
            _apply_company_unresolved(eval_id, is_agentic, "headless create")
            return
        snapshot, degraded_kind, local_mode = self._trusteed_cel_snapshot(eval_id, company_id)

        fallback_mode = _resolve_fallback_mode(snapshot)

        if snapshot is None:
            # Task 21: misma decisión por causa que ``_trusteed_cel_enforce``.
            _apply_degraded(degraded_kind, local_mode, eval_id, is_agentic, bool(token))
            return

        # Kill-switch — FR-070a: emergency stop for ALL agentic traffic — y sólo
        # ese (auditoría 2026-09-27). Mirrors _trusteed_cel_enforce(); never
        # degrades to fail-open in balanced/permissive on the headless create path.
        if is_agentic and snapshot.get("killSwitch") is True:
            _logger.warning(
                "CEL[%s] kill-switch active (headless create) for merchant %s — "
                "blocking unconditionally",
                eval_id,
                snapshot.get("merchantId", ""),
            )
            raise ValidationError(
                f"trusteed:kill_switch — Merchant agent kill-switch active. evaluationId={eval_id}"
            )

        if not is_agentic:
            # D1: sin token es un pedido humano; el fallbackMode no le aplica.
            _logger.info(
                "CEL[%s] no agent token in create vals — human order, fallbackMode=%s not applied",
                eval_id,
                fallback_mode,
            )
            return

        # Task 27a (M3): un agente con token NO puede crear un pedido ya en
        # 'sale'. Aquí no hay registro (ni id, ni importe, ni líneas): el
        # ``checkoutIntentHash`` iba con ``orderId: "pending"`` y el importe de
        # ``vals`` (casi siempre ``""``), o sea un token que no queda atado a
        # ESTE pedido ni a su importe, y el camino se saltaba la capa 2, la
        # válvula y el tope R030. Se rechaza SIN verificar el token ni consumir
        # su ``jti`` (sigue siendo válido para ``action_confirm``): se crea el
        # presupuesto y se confirma con ``action_confirm``, que sí evalúa con el
        # id y el importe reales.
        _logger.warning(
            "CEL[%s] headless create in state 'sale' with an agent token — rejected "
            "(confirm with action_confirm)",
            eval_id,
        )
        raise ValidationError(
            "trusteed:CEL-HEADLESS-CONFIRM-REQUIRED — An agent order cannot be created "
            "already confirmed. Create the quotation and confirm it with action_confirm, "
            f"passing the agent token. evaluationId={eval_id}"
        )


# ------------------------------------------------------------------
# Module-level helpers (no ORM access, pure functions)
# ------------------------------------------------------------------


def _positive_int(value) -> Optional[int]:
    """``value`` si es un entero positivo (no ``bool``); si no, ``None``."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _company_id_of(order) -> Optional[int]:
    """Compañía del PEDIDO (``order.company_id``), o ``None`` si no se puede
    determinar con seguridad. Nunca ``env.company`` ni el contexto del cliente
    (Task 35b, M3)."""
    company = getattr(order, "company_id", None)
    return _positive_int(getattr(company, "id", None))


def _company_id_from_vals(vals) -> Optional[int]:
    """Compañía de un ``create`` headless: sólo la que ``vals`` declara como id
    entero. El ORM rechaza (AccessError) una compañía fuera de las del usuario;
    aquí no se reimplementa. Sin ella ⇒ ``None``."""
    return _positive_int(vals.get("company_id")) if isinstance(vals, dict) else None


def _apply_company_unresolved(eval_id: str, is_agentic: bool, where: str) -> None:
    """Sin compañía determinable no hay configuración que aplicar (Task 35b).

    Un agente (token presente) se retiene con una decisión de AVERÍA que no
    nombra ninguna regla; una persona NO se bloquea por una avería nuestra (D1).
    """
    if is_agentic:
        _logger.warning(
            "CEL[%s] company of %s not determinable — agent held", eval_id, where
        )
        raise ValidationError(
            "trusteed:R000-COMPANY-UNRESOLVED — The store's company for this order could "
            f"not be determined; agent checkouts are held. evaluationId={eval_id}"
        )
    _logger.warning(
        "CEL[%s] company of %s not determinable — human order not evaluated (D1)",
        eval_id,
        where,
    )


def _consume_nonce_with_failure_mode(
    env,
    snapshot: dict,
    verify_result: dict,
    fallback_mode: str,
    eval_id: str,
    credentials: dict,
) -> dict:
    """Spec-048 P2.8 — consume jti against backend, apply tri-state failure_mode.

    Returns a dict of cartAttributes to merge into the layer-2 payload
    (empty only when the jti was ACCEPTED). Raises ``ValidationError`` on
    fail-closed paths (REPLAY / INDETERMINATE / missing credentials with a
    mode that closes). Mirrors PS ValidateOrderHook::consumeAgentNonce()
    and WC Amcp_Checkout_Enforcer::enrich_with_token_verification().

    ``credentials`` is ``EnforcementSnapshotService.get_credentials()`` — the
    installation id + HMAC secret of the addon settings (Task 21). Before,
    they were read from the snapshot payload, which never carries them, so
    the jti was never consumed: strict held every verified agent and
    balanced/permissive flagged ``_agent_token_nonce_unavailable``.
    """
    from . import nonce_consumer

    # B5: el identificador canónico es `nonce` (`jti` = alias del mismo valor).
    jti = str(verify_result.get("nonce") or verify_result.get("jti") or "")
    agent_did = str(verify_result.get("agentId", ""))
    exp = int(verify_result.get("exp", 0))
    if not jti or not agent_did:
        # verify_agent_token guarantees both — defensive return.
        return {}

    IrParam = env["ir.config_parameter"].sudo()
    api_base = IrParam.get_param("trusteed.api_base", "https://api.trusteed.xyz")
    merchant_id = str(snapshot.get("merchantId", ""))
    installation_id = str((credentials or {}).get("installationId") or "")
    hmac_secret = str((credentials or {}).get("hmacSecret") or "")

    if not merchant_id or not installation_id or not hmac_secret:
        # No backend credentials — skip consume, log INDETERMINATE telemetry.
        _logger.warning(
            "CEL[%s] nonce-consume skipped — missing credentials (fallback=%s)",
            eval_id,
            fallback_mode,
        )
        if _agent_closes(fallback_mode):
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
        if _agent_closes(fallback_mode):
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
    if _agent_closes(fallback_mode):
        raise ValidationError(
            f"trusteed:CEL-NONCE-UNAVAILABLE — Replay protection service unavailable. evaluationId={eval_id}"
        )
    return {"_agent_token_nonce_unavailable": "true"}


def _resolve_fallback_mode(snapshot: Optional[dict]) -> Optional[str]:
    """fallbackMode resuelto contra el vocabulario de Odoo (``platform_flow``).

    Ausente (o sin snapshot) ⇒ ``balanced``; fuera de
    ``strict|balanced|permissive`` ⇒ ``None`` (no resuelto: cierra a agentes).
    """
    from .platform_flow import resolve_failure_mode

    return resolve_failure_mode(None if snapshot is None else snapshot.get("fallbackMode"))


def _agent_closes(fallback_mode: Optional[str]) -> bool:
    """¿Cierra el modo a un agente ante una avería? (sólo se llama con agente)."""
    from .platform_flow import decide_on_evaluator_unavailable

    return decide_on_evaluator_unavailable(True, fallback_mode, False) == "BLOCK"


def _apply_degraded(
    kind: Optional[str],
    local_mode,
    eval_id: str,
    is_agentic: bool,
    token_present: bool,
) -> None:
    """No verified snapshot: decide by CAUSE (plan 04, Task 21; ``platform_flow``).

    - ``invalid_signature`` (JWS just served does not verify): an agent is held
      in every mode; a person passes.
    - ``missing_snapshot``: an agent WITH token is held in every mode (the DID
      resolver travels inside the snapshot, so the token cannot be verified);
      without token the mode decides — ``balanced`` by default, and the
      unsigned company-map ``fallbackMode`` can only tighten it (``strict``),
      never open it (``degraded_failure_mode``). A person passes.
    - ``layer_disabled`` (CEL disabled or addon not configured): nobody held.

    D1: a person is never blocked by our outage. The message names the outage,
    never a rule.
    """
    from .platform_flow import decide_on_degraded_enforcement, degraded_failure_mode

    kind = kind or "missing_snapshot"
    mode = degraded_failure_mode(local_mode)
    decision = decide_on_degraded_enforcement(kind, is_agentic, token_present, mode, False)
    if decision == "BLOCK":
        if kind == "invalid_signature":
            raise ValidationError(
                "trusteed:R000-SNAPSHOT-INVALID — The store's rules failed signature "
                f"verification; agent checkouts are held in every mode. evaluationId={eval_id}"
            )
        raise ValidationError(
            "trusteed:R000-SNAPSHOT-UNAVAILABLE — Store rules could not be loaded; "
            f"agent checkouts are held until they are. evaluationId={eval_id}"
        )
    if is_agentic or token_present:
        _logger.warning(
            "CEL[%s] %s — agent order proceeds without the rule snapshot (mode=%s)",
            eval_id,
            kind,
            mode,
        )


def _db_error_types() -> tuple:
    """Errores de base de datos que el ORM de Odoo sabe reintentar.

    ``psycopg2.Error`` (incluye ``SerializationFailure``/``DeadlockDetected``,
    que Odoo reintenta en ``service.model``) y ``odoo.exceptions.ConcurrencyError``.
    Import defensivo: sin ``psycopg2`` (o sin ese símbolo) no hay tipo que atrapar.
    """
    found = []
    try:
        import psycopg2

        found.append(psycopg2.Error)
    except (ImportError, AttributeError):
        pass
    try:
        from odoo import exceptions as odoo_exceptions

        concurrency = getattr(odoo_exceptions, "ConcurrencyError", None)
        if isinstance(concurrency, type):
            found.append(concurrency)
    except ImportError:
        pass
    return tuple(found)


def _reraise_if_db_error(exc: Exception) -> None:
    """L6d: un error de base de datos NO es un veredicto ni una avería nuestra.

    Convertirlo en «humano pasa / agente bloqueado» le quita al ORM el
    reintento de la transacción (y, tras un fallo de serialización, la
    transacción ya está abortada). Se relanza tal cual; ``ValidationError``
    y cualquier otra excepción propia siguen su camino de siempre.
    """
    types_ = _db_error_types()
    if types_ and isinstance(exc, types_):
        raise exc


def _on_internal_error(is_agentic: bool, where: str, exc: Exception) -> None:
    """Our own exception while evaluating (not a rule, not the network).

    Never blocks a human; never opens an agent in any mode (an outage of ours
    is not a permission) and never names a rule in the message.
    """
    _logger.warning("CEL internal error (%s, agentic=%s): %s", where, is_agentic, exc)
    try:
        from .platform_flow import decide_on_internal_error

        block = decide_on_internal_error(is_agentic) == "BLOCK"
    except Exception:  # the helper itself failing must not reach a human either
        block = bool(is_agentic)
    if block:
        raise ValidationError(
            "trusteed:CEL-INTERNAL-ERROR — The store's agent checks could not run; "
            "agent checkouts are held in every mode."
        )


def _build_did_resolver(snapshot: dict) -> dict[str, dict]:
    """``{agentDid: {x, kty?, crv?, kid?}}`` from ``snapshot.agentDidResolver``.

    Task 21: the server emits a LIST ``[{did, publicKeyJwk}]``
    (``RuleSnapshotPayloadSchema``); this addon only read the old MAP
    ``{did: jwk}``, so no legitimate token ever verified. Both forms are
    accepted with the reference semantics (``platform_flow``): in a list the
    LAST well-formed entry of a DID wins; malformed entries are ignored.
    """
    from .platform_flow import agent_did_resolver_map

    return agent_did_resolver_map(snapshot.get("agentDidResolver"))
