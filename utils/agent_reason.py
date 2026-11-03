"""Motivo de bloqueo apto para quien compra: sin umbrales ni política.

El servidor devuelve en BLOCK un ``agent_reason`` aditivo (frase fija de
categoría). Aquí se sanea y se construye el mensaje de ``ValidationError``;
el ``reason`` crudo (con umbrales) sólo puede ir al log del servidor.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Mismas frases que class-checkout-enforcer.php (plugin Woo) y AGENT_REASON_TEXT del servidor.
REASON_MERCHANT_POLICY = (
    "Checkout declined: the merchant does not accept this purchase from automated agents. "
    "The order was not placed and nothing was charged. Escalate to a human buyer."
)
REASON_CHECKS_UNAVAILABLE = (
    "Checkout not completed: the checks for this order could not run right now. "
    "This is not a merchant decision, the order was not placed and nothing was charged. "
    "Wait a few minutes and retry."
)

_RULE_CODE_RE = re.compile(r"R\d{3}(\.[a-z0-9-]+)?")
_MAX_CODE = 64
_MAX_TEXT = 400
_MAX_CATEGORY = 64
# La frase del contrato no lleva digitos (umbrales), "<" ">" ni entidades HTML.
_UNSAFE_REASON_RE = re.compile(r"[\d<>]|&#?[A-Za-z0-9]+;")


def _clean(value, limit: int) -> Optional[str]:
    """``value`` si es str no vacío, acotado y sin control/formato (bidi); si no, None."""
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in value):
        return None
    return value


_MARKUP_RE = re.compile(r"[<>]|&#?[A-Za-z0-9]+;")
_URL_RE = re.compile(r"https?://[^\s<>\"'`]+")
_ITEM_VALUE_RE = re.compile(r"[A-Za-z0-9_.:/ -]{1,64}")
_ITEM_KINDS = ("category", "sku", "digital_good_type")
_MAX_URL = 2048


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _human_checkout(raw) -> Optional[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("available"), bool):
        return None
    message = _clean(raw.get("message"), _MAX_TEXT)
    if message is None or _MARKUP_RE.search(message):
        return None
    out = {"available": raw["available"], "message": message}
    if "url" in raw:
        url = _clean(raw["url"], _MAX_URL)
        if url is None or not _URL_RE.fullmatch(url):
            return None
        out["url"] = url
    return out


def _blocked_item(raw) -> Optional[dict]:
    if not isinstance(raw, dict) or raw.get("kind") not in _ITEM_KINDS:
        return None
    value = raw.get("value")
    if not isinstance(value, str) or not _ITEM_VALUE_RE.fullmatch(value):
        return None
    return {"kind": raw["kind"], "value": value}


def _published_limit(raw) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    source = _clean(raw.get("source"), _MAX_TEXT)
    if source is None:
        return None
    if raw.get("kind") == "max_cart_value":
        if not _is_int(raw.get("amount_cents")) or raw.get("unit") != "minor_units_of_store_currency":
            return None
        return {"kind": "max_cart_value", "amount_cents": raw["amount_cents"],
                "unit": "minor_units_of_store_currency", "source": source}
    if raw.get("kind") == "max_units_per_line" and _is_int(raw.get("units")):
        return {"kind": "max_units_per_line", "units": raw["units"], "source": source}
    return None


_LIMIT_RULE_RE = re.compile(r"R\d{3}")
_LIMIT_CODE_RE = re.compile(r"R\d{3}\.[a-z0-9-]{1,60}")
_LIMIT_KINDS = ("max_cart_value", "max_line_value", "max_items_per_order", "max_units_per_line")
_LIMIT_UNITS = ("minor_units_of_store_currency", "units")


def _limit_exceeded(raw) -> Optional[dict]:
    """Todo o nada: cualquier campo inválido descarta el objeto entero."""
    if not isinstance(raw, dict):
        return None
    rule, code = raw.get("rule"), raw.get("rule_code")
    name = _clean(raw.get("rule_name"), 80)
    message = _clean(raw.get("message"), _MAX_TEXT)
    if not (isinstance(rule, str) and _LIMIT_RULE_RE.fullmatch(rule)
            and isinstance(code, str) and _LIMIT_CODE_RE.fullmatch(code)
            and name and message and not _MARKUP_RE.search(message)
            and raw.get("kind") in _LIMIT_KINDS and raw.get("unit") in _LIMIT_UNITS
            and _is_int(raw.get("limit")) and _is_int(raw.get("observed"))):
        return None
    return {"rule": rule, "rule_code": code, "rule_name": name, "kind": raw["kind"],
            "limit": raw["limit"], "observed": raw["observed"], "unit": raw["unit"], "message": message}


def sanitize_agent_reason(raw) -> Optional[dict]:
    """4 claves str acotadas + 3 opcionales validadas por separado (una inválida se descarta sola).

    Sin ``reason`` válido => None (se usa la frase genérica)."""
    if not isinstance(raw, dict):
        return None
    limit_exceeded = _limit_exceeded(raw.get("limit_exceeded"))
    reason = _clean(raw.get("reason"), _MAX_TEXT)
    if limit_exceeded is not None:
        reason = limit_exceeded["message"]  # el texto con cifras sustituye al reason (que puede llevarlas)
    elif reason is None or _UNSAFE_REASON_RE.search(reason):
        return None
    out = {"reason": reason}
    if limit_exceeded is not None:
        out["limit_exceeded"] = limit_exceeded
    category = _clean(raw.get("reason_category"), _MAX_CATEGORY)
    if category is not None:
        out["reason_category"] = category
    next_action = _clean(raw.get("next_action"), _MAX_TEXT)
    if next_action is not None:
        out["next_action"] = next_action
    code = _clean(raw.get("rule_code"), _MAX_CODE)
    if code is not None and _RULE_CODE_RE.fullmatch(code):
        out["rule_code"] = code
    for key, check in (("human_checkout", _human_checkout), ("blocked_item", _blocked_item),
                       ("published_limit", _published_limit)):
        clean = check(raw.get(key))
        if clean is not None:
            out[key] = clean
    return out


def _details(ar: dict, currency: Optional[str]) -> str:
    """Detalle breve para el comprador; texto plano (los valores ya no llevan marcado)."""
    parts = []
    item = ar.get("blocked_item")
    if item:
        parts.append(f"Item: {item['value']}")
    limit = ar.get("published_limit")
    if limit and limit["kind"] == "max_cart_value":
        parts.append(f"Limit: {limit['amount_cents'] / 100:.2f} {currency or ''}".rstrip())
    elif limit:
        parts.append(f"Limit: max. {limit['units']} units per line")
    human = ar.get("human_checkout")
    if human:
        parts.append(f"{human['message']} {human['url']}" if human["available"] and human.get("url")
                     else human["message"])
    return " | ".join(parts)


def block_message(agent_reason: Optional[dict], eval_id: str, currency: Optional[str] = None) -> str:
    """Mensaje de bloqueo. Conserva el marcador ``trusteed:<código> — …  evaluationId=…``."""
    ar = sanitize_agent_reason(agent_reason)
    code = (ar or {}).get("rule_code")
    phrase = ar["reason"] if ar else REASON_MERCHANT_POLICY
    msg = f"trusteed:{code or 'merchant_policy'} — {phrase} evaluationId={eval_id}"
    if code:
        msg += f" | Más info: https://trusteed.xyz/es/agent-rules#{code.split('.')[0]}"
    details = _details(ar, currency) if ar else ""
    if details:
        msg += f" | {details}"
    return msg
