from . import ir_actions_server_ai_tool  # Odoo 18.x compat: usage='ai_tool' selection
from . import res_config_settings
from . import setup_wizard
from . import sale_order_trust
from . import account_move_jws
from . import api_client  # noqa: F401 — TrusteedApiClient used by ai_tool_invocation
from . import ai_tool_invocation
# spec-048 CEL — Checkout Enforcement Layer for Odoo 18
from . import enforcement_snapshot
from . import enforcement_token_verifier
from . import nonce_consumer  # noqa: F401 — spec-048 P2.8 backend replay protection
from . import sale_order_enforcement          # T081: sale.order confirm/create/write gates
from . import enforcement_company_map         # T085: multi-company merchantId mapping
# spec-048 4.9 — reporte de capacidades de señales (negociación con el servidor)
from . import capabilities_reporter          # noqa: F401 — invocado desde hooks.py
# spec-043 T043-064/T043-065 — Fulfillment + refund proxy emission to backend
from . import stock_picking_fulfillment
from . import account_move_refund_proxy
