"""Spec-048 Sprint E (T-E42, ADR-054) — Agent history projection for Odoo.

R042 velocity-cap counter: completed sales orders for ``agent_id_hash``
within a sliding time window.

DEFERRED-WIRING NOTE (2026-05-23): the Odoo ``sale.order`` model does not yet
carry an ``amcp_agent_id_hash`` column. Spec-048 wave A landed this column
on `sales_order` for Magento and the `ps_amcp_agent_order_links` join table
for PrestaShop, but the Odoo migration is pending Sprint E.2.

Until then this helper:
  * returns ``None`` (not 0) so the evaluator can distinguish "no signal"
    from "zero completions" and degrade gracefully (R042 is OBSERVE-only
    until source-of-truth is wired),
  * logs a one-shot warning per process via the supplied ``logger`` callable,
  * keeps the public contract identical to the PrestaShop / Magento variants
    so the evaluator can rely on a stable shape across platforms.
"""
from __future__ import annotations

from typing import Callable, Optional

_WARNED_ONCE: bool = False


class AgentHistoryFetcher:
    """Stub fetcher honouring the cross-platform R042 contract."""

    def __init__(self, env: object, logger: Callable[[str], None]) -> None:
        """env = Odoo ``self.env`` (or any compatible namespace); kept for
        forward-compat once the migration lands.
        """
        self._env = env
        self._logger = logger

    def completed_order_count_in_window(
        self,
        agent_id_hash: str,
        window_seconds: int,
    ) -> Optional[int]:
        global _WARNED_ONCE
        if not agent_id_hash or window_seconds <= 0:
            return None

        if not _WARNED_ONCE:
            _WARNED_ONCE = True
            try:
                self._logger(
                    "[trusteed] spec-048 R042: sale.order.amcp_agent_id_hash "
                    "column not yet present — R042 evaluator will receive "
                    "None (no signal) and degrade gracefully. Wiring deferred "
                    "to Sprint E.2."
                )
            except Exception:  # noqa: BLE001 — never fail on logging
                pass
        return None

    def reset_warning(self) -> None:
        """Test helper: re-arm the one-shot warning flag."""
        global _WARNED_ONCE
        _WARNED_ONCE = False


__all__ = ("AgentHistoryFetcher",)
