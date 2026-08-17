/** @odoo-module **/
/**
 * Trusteed embed shell — OWL component (Odoo 17/18, OWL 2).
 *
 * Responsibilities:
 *   1. Mount the shared React SPA (window.TrusteedEmbed.mount) on first render.
 *   2. Detect active-company changes via env.services.company.
 *   3. On company switch: show a toast for 3 s, then re-mount the SPA so the
 *      new company's data is loaded.
 *
 * S042-003: _mountSpa now fetches an Odoo-specific access token via
 * /trusteed/token and passes source="odoo-embed" + getToken to mount(),
 * fixing X-Embed-Source audit attribution and WP-specific tokenManager usage.
 *
 * The IIFE bundle (admin-spa.js) is loaded first via __manifest__ assets_backend,
 * exposing window.TrusteedEmbed.mount(rootEl, opts).
 */

import {
  Component,
  onMounted,
  onWillUnmount,
  useEffect,
  useRef,
  useState,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";

// Duration (ms) the "switching company" toast remains visible.
const TOAST_DURATION_MS = 3000;

class TrusteedPanel extends Component {
  static template = "trusteed.TrusteedPanel";

  setup() {
    this.rootRef = useRef("root");

    // companyService may be absent in test environments — guard with optional
    // chaining everywhere it is accessed.
    this.companyService = useService("company");

    // rpc used to call /trusteed/token with Odoo auth + CSRF. Odoo 17+
    // dropped the injectable "rpc" service in favor of a plain import
    // (verified empirically 2026-07-23: useService("rpc") throws
    // "Service rpc is not available" on this Odoo 18 build).
    this.rpcService = rpc;

    // Spec 043 Codex P2 Pend17: track pending toast timer so we can cancel it
    // if the component unmounts mid-switch (otherwise setTimeout fires against
    // a destroyed component and re-mounts the SPA into a stale DOM node).
    this._switchTimerId = null;

    this.state = useState({
      // activeCompanyId tracks the last company the SPA was mounted for.
      activeCompanyId: null,
      // toastMsg is non-null while the "switching company" notification is shown.
      toastMsg: null,
    });

    // Initialise state and perform first mount after the DOM is ready.
    onMounted(() => {
      const currentId = this.companyService?.activeCompany?.id ?? null;
      this.state.activeCompanyId = currentId;
      this._mountSpa();
    });

    // Spec 043 Codex P2 Pend17: cancel any pending toast timer on unmount.
    onWillUnmount(() => {
      if (this._switchTimerId !== null) {
        clearTimeout(this._switchTimerId);
        this._switchTimerId = null;
      }
    });

    // useEffect re-runs whenever the company service's active company changes.
    // OWL 2 tracks reactive dependencies automatically; accessing
    // companyService.activeCompany inside the effect body registers it.
    useEffect(
      () => {
        const newId = this.companyService?.activeCompany?.id ?? null;
        if (
          this.state.activeCompanyId !== null &&
          newId !== this.state.activeCompanyId
        ) {
          // Company has switched — show toast then re-mount.
          this._handleCompanySwitch(newId);
        }
      },
      // Dependency accessor: return a stable value that changes when the
      // active company changes so OWL knows to re-run the effect.
      () => [this.companyService?.activeCompany?.id]
    );
  }

  // ── Private helpers ─────────────────────────────────────────────────────────

  /**
   * Fetch an Odoo bootstrap access token via /trusteed/token.
   * Returns null if the bootstrap endpoint is unavailable or unconfigured.
   */
  async _fetchOdooToken() {
    try {
      const result = await this.rpcService("/trusteed/token", {});
      return result && result.success && result.access_token
        ? { accessToken: result.access_token, apiBase: result.api_base }
        : null;
    } catch (_err) {
      return null;
    }
  }

  /**
   * Mount (or re-mount) the React SPA inside #amcp-root.
   * S042-003: fetches Odoo token and passes source="odoo-embed" + getToken so
   * the SPA uses the correct X-Embed-Source header and Odoo-specific token broker.
   */
  /**
   * Resolve which SPA section to mount. Defaults to "trust-center" (the Trust
   * Center menu); the "My Sales" menu passes `trusteed_section: "mis-ventas"`
   * via its client-action context so the same panel mounts the receipts list +
   * comprobante download (dispute-evidence Fase A / Bloque 3 / T9).
   */
  _resolveSection() {
    return this.props?.action?.context?.trusteed_section ?? "trust-center";
  }

  async _mountSpa() {
    const rootEl = this.rootRef.el?.querySelector("#amcp-root");
    if (!rootEl || !window.TrusteedEmbed?.mount) return;

    const fetched = await this._fetchOdooToken();
    // Capture token in closure — returned synchronously to avoid extra roundtrips.
    const getToken = () => Promise.resolve(fetched?.accessToken ?? null);

    window.TrusteedEmbed.mount(rootEl, {
      section: this._resolveSection(),
      source: "odoo-embed",
      getToken,
      // Without this the bundle falls back to its built-in default
      // (production api.trusteed.xyz) — verified empirically 2026-07-23,
      // mirrors how the WooCommerce/PrestaShop loaders pass their own
      // configured apiBase into this same mount() call.
      apiBase: fetched?.apiBase,
    });
  }

  /**
   * Handle an active-company switch:
   *   1. Update tracked company ID.
   *   2. Show toast for TOAST_DURATION_MS ms.
   *   3. After the toast, re-mount the SPA (new token fetched for the new company).
   */
  _handleCompanySwitch(newCompanyId) {
    this.state.activeCompanyId = newCompanyId;
    this.state.toastMsg = this.env._t
      ? this.env._t("Reconnecting with new company…")
      : "Reconnecting with new company…";

    // Cancel any prior pending switch (rapid double-switch) before scheduling.
    if (this._switchTimerId !== null) {
      clearTimeout(this._switchTimerId);
    }
    this._switchTimerId = setTimeout(() => {
      this._switchTimerId = null;
      this.state.toastMsg = null;
      this._mountSpa();
    }, TOAST_DURATION_MS);
  }
}

registry.category("actions").add("trusteed_panel", TrusteedPanel);
