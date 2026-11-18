"""Páginas de política del comercio -> ``PUT /v1/embed/merchant/policy-pages``.

Python puro, SIN Odoo (probable con unittest). El ORM vive en
``models/policy_pages_sync.py``. Sólo se manda lo que el comercio escribió.
"""

import ipaddress
import json
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

import requests
import requests.exceptions

from . import paypal_sync as ps

PATH = "/v1/embed/merchant/policy-pages"
MAX_URL = 2048


def _clean_host(parsed: Any) -> Optional[str]:
    """Hostname público (minúsculas) de una URL https sin credenciales; si no, ``None``."""
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    try:
        host = (parsed.hostname or "").lower()
        parsed.port  # noqa: B018 — fuerza el error de un puerto inválido
    except ValueError:
        return None
    if not host or host == "localhost" or host.endswith(".localhost") or "." not in host:
        return None
    try:
        ipaddress.ip_address(host)
        return None  # IP literal
    except ValueError:
        return host


def _site_host(domain: Any) -> Optional[str]:
    """Host de ``web.base.url`` o ``website.domain`` (éste puede venir sin esquema)."""
    if not isinstance(domain, str) or not domain.strip():
        return None
    raw = domain.strip()
    try:
        return _clean_host(urllib.parse.urlparse(raw if "://" in raw else "https://" + raw))
    except ValueError:
        return None


def _iso_utc(value: Any) -> Optional[str]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:  # Odoo guarda las fechas en UTC naive
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_policy_pages(
    setting_value: Any,
    base_url: Any,
    website_domains: Iterable[Any] = (),
    modified_at: Any = None,
) -> list:
    """Lista para el cuerpo (0 o 1 elementos: sólo ``returns``).

    ``setting_value``: URL https absoluta del propio dominio, o ruta que empieza por
    «/» (se resuelve contra ``base_url``). Vacío o inválido => ``[]``.
    ``modified_at``: cuándo fijó el comercio el ajuste (no hay fecha de página sin website).
    ``terms`` y ``privacy`` no se mandan: Odoo no tiene un ajuste inequívoco para ellas.
    """
    if not isinstance(setting_value, str) or not setting_value.strip():
        return []
    value = setting_value.strip()
    base_host = _site_host(base_url)
    allowed = {h for h in [base_host, *(_site_host(d) for d in website_domains)] if h}
    if value.startswith("/") and not value.startswith("//"):
        if not base_host:
            return []
        url = f"https://{base_host}{value}"  # shortcut: sin puerto no estándar
    else:
        try:
            host = _clean_host(urllib.parse.urlparse(value))
        except ValueError:
            return []
        if host not in allowed:
            return []
        url = value
    modified = _iso_utc(modified_at)
    if modified is None or len(url) > MAX_URL or any(c.isspace() for c in url):
        return []
    return [{"type": "returns", "url": url, "page_modified_at": modified}]


def send_policy_pages(
    api_base: str, token: str, pages: list, put: Callable[..., Any] = requests.put
) -> dict:
    """Un único PUT, sin redirecciones, sólo a hosts de Trusteed. Nunca lanza.

    Lista vacía también se envía: el servidor borra lo importado antes.
    """
    if not ps.is_allowed_api_base(api_base):
        return {"ok": False, "code": "api_host_not_allowed"}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Embed-Source": ps.EMBED_SOURCE,
        "User-Agent": ps.USER_AGENT,
    }
    try:
        resp = put(
            api_base.rstrip("/") + PATH,
            data=json.dumps({"policyPages": pages}).encode("utf-8"),
            headers=headers,
            timeout=(ps.CONNECT_TIMEOUT, ps.READ_TIMEOUT),
            allow_redirects=False,
        )
    except requests.exceptions.RequestException:
        return {"ok": False, "code": "platform_unreachable"}
    if resp.status_code == 200:
        return {"ok": True, "code": "sent"}
    return {"ok": False, "code": "platform_rejected", "reason": ps._reason(resp)}
