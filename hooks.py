"""TEAH-092: Odoo install lifecycle hooks.

`pre_init_hook` runs before any data is loaded — with `cr` available so we
can build a full Odoo Environment for the sysparam-based SaaS checks.

`post_init_hook` runs after all XML data records are loaded.  It creates
``ai_schema`` argument rows for each ``ir.actions.server`` ai_tool record.
``ai_schema`` is a One2many and cannot be declared inline in XML data files,
so we create the child rows here programmatically.

Compatibility: the ``ai_schema`` field was introduced in Odoo 19.0.  On Odoo
18.x the field may be absent; we guard with hasattr() so the hook is safe to
run on both versions (the tool still works on 18 via the ``code`` field, just
without schema-driven argument validation by the AI App).

Ref: specs/046-cross-platform-agentic-tools/plan.md
"""

import logging

_logger = logging.getLogger(__name__)

_DOCS_URL = "https://docs.trusteed.xyz/embed/odoo-onprem"


def pre_init_hook(env):
    """Refuse installation on Odoo SaaS (TEAH-091/092).

    Odoo 17+ calls pre_init_hook with an Environment, not a raw cursor
    (unlike the pre-17 convention this was originally written against).
    """
    from .utils.saas_detector import is_odoo_saas

    result = is_odoo_saas(env=env)

    if result.is_saas:
        raise Exception(
            "Trusteed embed shell cannot be installed on Odoo SaaS (odoo.com). "
            "Please use Odoo.sh or an on-premise deployment. "
            f"Detection reason: {result.reason}. "
            f"Documentation: {_DOCS_URL}"
        )

    _logger.info(
        "TEAH-091 SaaS check passed (%s) — proceeding with install",
        result.reason,
    )


# ---------------------------------------------------------------------------
# ai_schema definitions — one entry per argument per tool
# ---------------------------------------------------------------------------
#
# Model name for schema rows differs between Odoo versions:
#   Odoo 19 (released):  ir.actions.server.schema.arg  (field: ai_schema)
#   Odoo 18 (partial):   same model name is expected but may be absent
#
# Each entry: (name, field_type, description, required)
# field_type values accepted by Odoo 19: 'char', 'integer', 'float',
#   'boolean', 'many2one', 'text', 'json'  (use 'char' for generic string/UUID)
# ---------------------------------------------------------------------------

_AI_TOOL_SCHEMAS: dict[str, list[dict]] = {
    "trusteed.action_trusteed_sign_trust_receipt": [
        {
            "name": "orderId",
            "field_type": "char",
            "description": "Platform order identifier to sign a Trust Receipt for.",
            "required": True,
        },
        {
            "name": "agentId",
            "field_type": "char",
            "description": "Originating agent DID or identifier (optional).",
            "required": False,
        },
    ],
    "trusteed.action_trusteed_verify_agent_signature": [
        {
            "name": "method",
            "field_type": "char",
            "description": "HTTP method of the signed request (e.g. POST).",
            "required": True,
        },
        {
            "name": "url",
            "field_type": "char",
            "description": "Full URL of the signed request.",
            "required": True,
        },
        {
            "name": "headers",
            "field_type": "json",
            "description": "HTTP headers dict (must include Signature and Signature-Input).",
            "required": True,
        },
    ],
    "trusteed.action_trusteed_dispatch_payment_acp": [
        {
            "name": "cartId",
            "field_type": "char",
            "description": "UUID of the ACP checkout session.",
            "required": True,
        },
        {
            "name": "idempotencyKey",
            "field_type": "char",
            "description": "Caller-supplied idempotency key (UUID recommended).",
            "required": True,
        },
        {
            "name": "merchantId",
            "field_type": "char",
            "description": "Trusteed merchant UUID.",
            "required": True,
        },
    ],
    "trusteed.action_trusteed_dispatch_payment_x402": [
        {
            "name": "cartId",
            "field_type": "char",
            "description": "UUID of the checkout session.",
            "required": True,
        },
        {
            "name": "idempotencyKey",
            "field_type": "char",
            "description": "Caller-supplied idempotency key.",
            "required": True,
        },
        {
            "name": "merchantId",
            "field_type": "char",
            "description": "Trusteed merchant UUID.",
            "required": True,
        },
        {
            "name": "paymentPayload",
            "field_type": "json",
            "description": "x402 payment object (network, token, amount, signature).",
            "required": True,
        },
    ],
    "trusteed.action_trusteed_dispatch_payment_ap2": [
        {
            "name": "cartId",
            "field_type": "char",
            "description": "UUID of the checkout session.",
            "required": True,
        },
        {
            "name": "idempotencyKey",
            "field_type": "char",
            "description": "Caller-supplied idempotency key.",
            "required": True,
        },
        {
            "name": "merchantId",
            "field_type": "char",
            "description": "Trusteed merchant UUID.",
            "required": True,
        },
        {
            "name": "mandateJwt",
            "field_type": "char",
            "description": "Signed AP2 mandate JWT from the agent wallet.",
            "required": True,
        },
    ],
}


def _seed_tool_toggles(env):
    """FR-018b: seed the 5 per-tool toggle params (defaults: sign+verify ON,
    payments OFF). Idempotent — never clobbers a merchant's saved preference."""
    from .utils.tool_toggles import seed_defaults

    icp = env["ir.config_parameter"].sudo()
    seed_defaults(icp.get_param, icp.set_param)
    _logger.info("trusteed post_init_hook: FR-018b tool toggles seeded")


def post_init_hook(env):
    """Create ai_schema argument rows for each ai_tool server action.

    Safe on Odoo 18.x (field absent) and fully functional on Odoo 19.0+.
    Idempotent: skips creation if rows already exist for the action.

    Also seeds the FR-018b per-tool toggles (payments default OFF / opt-in).
    """
    _seed_tool_toggles(env)

    # Resolve the schema arg model — absent on Odoo 18.x without the AI App module
    SchemaArg = env.get("ir.actions.server.schema.arg")
    if SchemaArg is None:
        _logger.info(
            "trusteed post_init_hook: ir.actions.server.schema.arg not available "
            "(Odoo 18.x without AI App module) — ai_schema rows skipped"
        )
        return

    for xml_id, args in _AI_TOOL_SCHEMAS.items():
        action = env.ref(xml_id, raise_if_not_found=False)
        if action is None:
            _logger.warning(
                "trusteed post_init_hook: xml_id '%s' not found — skipping ai_schema",
                xml_id,
            )
            continue

        # Compatibility: guard in case the field is absent on this Odoo build
        if not hasattr(action, "ai_schema"):
            _logger.info(
                "trusteed post_init_hook: ai_schema field absent on ir.actions.server "
                "for '%s' — skipping (Odoo 18.x compatibility)",
                xml_id,
            )
            continue

        # Idempotency: skip if any schema rows already exist for this action
        existing = SchemaArg.search_count([("action_id", "=", action.id)])
        if existing:
            _logger.debug(
                "trusteed post_init_hook: ai_schema rows already exist for '%s' — skipping",
                xml_id,
            )
            continue

        for arg in args:
            SchemaArg.create({"action_id": action.id, **arg})

        _logger.info(
            "trusteed post_init_hook: created %s ai_schema rows for '%s'",
            len(args),
            xml_id,
        )
