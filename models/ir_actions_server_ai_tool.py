"""Odoo 18.x compatibility shim.

`ir.actions.server.usage` only gained the `ai_tool` selection value in
Odoo 19's native AI App. On 18.x that value does not exist in the
registry, so any XML record declaring `<field name="usage">ai_tool</field>`
fails to load. Extend the selection here so the same data files
(data/ai_tools.xml) work unmodified on both versions; on 19.0+ this
addition is a harmless duplicate of the core value.
"""

from odoo import models, fields


class IrActionsServer(models.Model):
    _inherit = "ir.actions.server"

    usage = fields.Selection(
        selection_add=[("ai_tool", "AI Tool")],
        ondelete={"ai_tool": "cascade"},
    )
