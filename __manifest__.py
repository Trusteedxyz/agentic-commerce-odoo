{
    "name": "Trusteed Trust Center",
    "version": "18.0.1.1.2",
    "summary": "Embed Trusteed Trust Center inside Odoo backoffice (Odoo.sh / on-premise only).",
    "description": """
Trusteed embed shell — production addon for Odoo 17/18.

Provides:
  - Top-level "Trusteed" menu with Trust Center panel (React SPA same-origin).
  - Bootstrap JWT signing controller (HS256, stdlib only — no external deps).
  - Settings form to configure Merchant ID, Bootstrap Secret, and API base URL.
  - Multi-company isolation: company_id captured at issuance, validated by apps/api.

Requires Odoo.sh or an on-premise deployment.
Odoo Online (SaaS) is blocked at install time (pre_init_hook, TEAH-091/092).
""",
    "author": "Trusteed",
    "license": "LGPL-3",
    "category": "Tools",
    "depends": ["base", "web", "mail", "sale", "account", "stock", "sale_stock"],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "data/system_parameters.xml",
        "data/ai_tools.xml",
        # spec-048 T087: CEL snapshot refresh cron (load before views)
        "data/cron.xml",
        "views/wizard.xml",
        "views/client_action.xml",
        "views/res_config_settings_views.xml",
        # menu.xml references actions from both files above; must load last
        "views/menu.xml",
        "views/sale_order_kanban.xml",
        # spec-048 T084: hidden x_trusteed_agent_token field on sale.order form
        "views/sale_order_enforcement.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "trusteed/static/src/css/trust_badge.css",
            "trusteed/static/src/js/admin-spa.js",
            "trusteed/static/src/js/trusteed_panel.js",
            "trusteed/static/src/xml/trusteed_panel.xml",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
    # spec-048 T005: external Python deps for CEL plugin features
    # (Ed25519 snapshot signature verify, RFC 9421 agent identity verify,
    # RFC 8785 JCS canonicalization for checkoutIntentHash recompute).
    "external_dependencies": {
        "python": ["cryptography"],
    },
    # TEAH-092: Pre-install hook — refuses installation on Odoo SaaS with
    # full sysparam-based detection (env available at this point).
    "pre_init_hook": "pre_init_hook",
    # spec-046 T083: Post-install hook — creates ai_schema argument rows for
    # each ir.actions.server ai_tool (One2many, cannot be set in XML data).
    "post_init_hook": "post_init_hook",
}
