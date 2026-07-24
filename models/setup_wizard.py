"""Trusteed Setup Wizard — 4-step TransientModel.

Guides the administrator through:
  welcome     — explanation + group check
  connect     — instructions to visit app.trusteed.xyz/connect
  credentials — paste Merchant ID + Bootstrap Secret → persists to ir.config_parameter
  test        — fires the internal /trusteed/token endpoint → shows result

Security notes:
  - wizard requires trusteed.group_admin group (enforced via action domain).
  - bootstrap_secret is written only via ir.config_parameter.sudo(), with minimal
    sudo() scope (ICP access only).
  - test_result carries human-readable text only; raw API error bodies are never
    forwarded.
  - The TransientModel is auto-vacuumed by Odoo after 24 h (standard behaviour).
"""

import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_PORTAL_URL = "https://app.trusteed.xyz/connect"


class TrusteedSetupWizard(models.TransientModel):
    _name = "trusteed.setup.wizard"
    _description = "Trusteed Quick Setup Wizard"

    # ── State machine ──────────────────────────────────────────────────────────

    state = fields.Selection(
        selection=[
            ("welcome", "Welcome"),
            ("connect", "Connect Portal"),
            ("credentials", "Enter Credentials"),
            ("test", "Test Connection"),
        ],
        default="welcome",
        required=True,
        string="Step",
    )

    # ── Credential fields ──────────────────────────────────────────────────────

    merchant_id = fields.Char(
        string="Merchant ID",
        help="UUID provided by the Trusteed portal.",
    )
    bootstrap_secret = fields.Char(
        string="Bootstrap Secret",
        help="Hex secret from the Trusteed portal. Keep confidential.",
        groups="trusteed.group_admin",
    )

    # ── Test result fields (read-only) ─────────────────────────────────────────

    test_result = fields.Char(
        string="Test Result",
        readonly=True,
    )
    test_success = fields.Boolean(
        string="Test Passed",
        readonly=True,
    )

    # ── Portal URL (computed constant — avoids hardcoding in XML) ─────────────

    portal_url = fields.Char(
        string="Portal URL",
        compute="_compute_portal_url",
    )

    @api.depends()
    def _compute_portal_url(self):
        for rec in self:
            rec.portal_url = _PORTAL_URL

    # ── Onchange: clear test result when credentials change ───────────────────

    @api.onchange("merchant_id", "bootstrap_secret")
    def _onchange_credentials(self):
        self.test_result = False
        self.test_success = False

    # ── Navigation actions ─────────────────────────────────────────────────────

    def action_next_welcome(self):
        """Move from Welcome → Connect."""
        self.ensure_one()
        if not self.env.user.has_group("trusteed.group_admin"):
            raise UserError(
                _("You need the Trusteed Administrator role to run this wizard.")
            )
        self.state = "connect"
        return self._reopen()

    def action_next_connect(self):
        """Move from Connect → Credentials, pre-filling current ICP values."""
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()
        self.merchant_id = ICP.get_param("trusteed.merchant_id", "")
        # Never pre-fill the secret — force a conscious paste from the portal.
        self.bootstrap_secret = False
        self.state = "credentials"
        return self._reopen()

    def action_prev_credentials(self):
        """Move from Credentials → Connect."""
        self.ensure_one()
        self.state = "connect"
        return self._reopen()

    def action_save_credentials(self):
        """Validate + persist credentials → move to Test step."""
        self.ensure_one()

        merchant_id = (self.merchant_id or "").strip()
        if not merchant_id:
            raise UserError(_("Merchant ID is required."))
        # Spec 043 Codex P2 Pend13: enforce canonical lowercase UUID v4 format.
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            merchant_id,
        ):
            raise UserError(
                _(
                    "Merchant ID must be a UUID (e.g., 550e8400-e29b-41d4-a716-446655440000)."
                )
            )

        secret = (self.bootstrap_secret or "").strip()
        if not secret:
            raise UserError(_("Bootstrap Secret is required."))
        # F-005: require exactly 64 hex characters (32-byte secret)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", secret):
            raise UserError(
                _("Bootstrap Secret must be exactly 64 hexadecimal characters.")
            )

        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("trusteed.merchant_id", merchant_id)
        ICP.set_param("trusteed.bootstrap_secret", secret)
        # F-010: clear secret from TransientModel immediately after persisting to ICP
        self.sudo().write({"bootstrap_secret": False})

        self.state = "test"
        self.test_result = False
        self.test_success = False
        return self._reopen()

    def action_test_connection(self):
        """Call the internal /trusteed/token controller and report the outcome."""
        self.ensure_one()

        ICP = self.env["ir.config_parameter"].sudo()
        merchant_id = ICP.get_param("trusteed.merchant_id", "")
        if not merchant_id:
            self.test_result = _("Credentials not saved yet. Please go back to step 3.")
            self.test_success = False
            return self._reopen()

        try:
            result = self._call_token_endpoint_internal()
            if result.get("success"):
                self.test_result = _("Connection successful!")
                self.test_success = True
            else:
                error_key = result.get("error", "unknown")
                self.test_result = self._format_error(error_key)
                self.test_success = False
        except Exception as exc:
            _logger.warning(
                "trusteed wizard test_connection failed: %s", type(exc).__name__
            )
            self.test_result = _("Connection test failed. Check your credentials and API connectivity.")
            self.test_success = False

        return self._reopen()

    def action_prev_test(self):
        """Move from Test → Credentials."""
        self.ensure_one()
        self.state = "credentials"
        return self._reopen()

    def action_finish(self):
        """Close the wizard and open the Trust Center menu."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "trusteed_panel",
        }

    # ── Private helpers ────────────────────────────────────────────────────────

    def _reopen(self) -> dict:
        """Return an act_window action that re-opens this wizard record."""
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _call_token_endpoint_internal(self) -> dict:
        """Invoke the bootstrap exchange controller in-process.

        Delegates to the same Python function that the HTTP route calls,
        bypassing the network stack entirely for the wizard test.  This is
        safe because:
          - We are already inside a valid Odoo user session.
          - The controller reads credentials from ICP (which we just saved).
          - No secrets are logged at any call site.
        """
        # Import lazily to avoid circular-import issues at module load time.
        from odoo.addons.trusteed.controllers.main import (  # noqa: PLC0415
            TrusteedController,
        )

        controller = TrusteedController()
        # exchange_bootstrap() reads from request.env — which is the current
        # env when called from a TransientModel action (same request context).
        try:
            result = controller.exchange_bootstrap()
        except Exception as exc:  # pragma: no cover — defensive
            _logger.exception(
                "trusteed in-process bootstrap exchange raised: %s",
                type(exc).__name__,
            )
            return {"error": "bootstrap_failed"}

        if not isinstance(result, dict):
            return {"error": "unexpected_response"}
        return result

    @staticmethod
    def _format_error(error_key: str) -> str:
        _error_labels = {
            "unauthorized":           _("Not authorized. Check your Trusteed group membership."),
            "configure_module_first": _("Credentials are not fully saved. Go back to step 3."),
            "bootstrap_rejected":     _("The Trusteed API rejected the credentials. Verify your Merchant ID and Bootstrap Secret."),
            "bootstrap_timeout":      _("Connection timed out. Check outbound HTTPS access to api.trusteed.xyz."),
            "bootstrap_connection_error": _("Could not reach api.trusteed.xyz. Check your network configuration."),
            "bootstrap_failed":       _("Bootstrap exchange failed. Check credentials and try again."),
        }
        return _error_labels.get(error_key, _("Error: %(key)s", key=error_key))
