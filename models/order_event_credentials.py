"""Credenciales de los emisores de eventos de pedido (C16 cumplimiento, R023 reembolso).

BUG-279: los emisores leían un modelo inexistente (``enforcement.company.map``)
y parámetros que nadie escribe (``trusteed.access_token``/``hmac_secret``/
``installation_id``), así que no enviaban nunca nada. Ahora usan la MISMA fuente
que el enforcement: ``trusteed.enforcement.snapshot.get_credentials``.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


def resolve_order_event_credentials(env, company_id: int) -> dict | None:
    """Devuelve ``{apiBase, merchantId, installationId, hmacSecret}`` o ``None``.

    ``None`` si el addon no está configurado para esa compañía (no es un fallo:
    nada que emitir). El endpoint autentica por HMAC de instalación; no hace
    falta Bearer.
    """
    creds = env["trusteed.enforcement.snapshot"].sudo().get_credentials(company_id)
    if not creds.get("configured") or not creds.get("apiBase"):
        _logger.warning(
            "order_event_emit_skipped company_id=%s — merchant/installation/HMAC "
            "not configured (trusteed.cel_* / company map)",
            company_id,
        )
        return None
    return creds
