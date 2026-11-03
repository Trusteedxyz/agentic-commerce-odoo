"""Sincroniza la cuenta PayPal de ``payment_paypal`` (Odoo 18) con la plataforma.

Módulo de Python puro: SIN dependencias de Odoo, para poder probarlo aquí.
La integración con el ORM vive en ``models/payment_provider_paypal_sync.py``.

Semántica copiada de ``class-paypal-sync.php`` (WooCommerce, F3):
  - lector con validación de cota (20-200, ``[A-Za-z0-9_-]``), no un formato:
    PayPal no publica uno y no se inventa; la plataforma valida de verdad.
  - huella ``sha256(client_id|client_secret|modo)`` para idempotencia.
  - entrega a ``/v1/embed/merchant/payment-methods/paypal/connect`` con
    ``{flow:"apiKey", accountId, apiKey, mode, force:true}``.
  - desconexión (D5) cuando el proveedor pasa a ``disabled`` o se vacían las
    credenciales y antes se había sincronizado algo.

Reglas de seguridad:
  - El secreto NUNCA se registra ni se devuelve: ningún resultado de este
    módulo lo contiene, ni siquiera en excepciones de red.
  - Un intento, sin reintento (F-008): timeout (2 s conexión, 4 s lectura).
  - Sin redirecciones (SSRF) y sólo a hosts de la allowlist.

Mapa de estado de ``payment.provider`` (Odoo 18):
  ``enabled`` -> ``production``; ``test`` -> ``sandbox``; ``disabled`` -> nada.
"""

import hashlib
import json
import re
import urllib.parse
from typing import Any, Callable, Optional

import requests
import requests.exceptions

CONNECT_PATH = "/v1/embed/merchant/payment-methods/paypal/connect"
DISCONNECT_PATH = "/v1/embed/merchant/payment-methods/paypal/disconnect"
EMBED_SOURCE = "odoo-embed"
USER_AGENT = "TrusteedAddon-Odoo/1.0"

# Campos de ``payment.provider`` cuyo cambio dispara la resincronización.
TRIGGER_FIELDS = ("paypal_client_id", "paypal_client_secret", "state")

# F-008: misma política que controllers/main.py (2 s conexión + 4 s lectura).
CONNECT_TIMEOUT = 2
READ_TIMEOUT = 4

# Mismo criterio que el plugin WooCommerce: cota local, no formato de PayPal.
_CREDENTIAL_RE = re.compile(r"^[A-Za-z0-9_-]{20,200}$")

# El Bearer del comerciante sólo puede viajar a hosts de Trusteed.
ALLOWED_API_HOSTS = frozenset(
    {
        "api.trusteed.xyz",
        "agenticmcpstores-production.up.railway.app",
        "staging-api.trusteed.xyz",
    }
)

_STATE_TO_MODE = {"enabled": "production", "test": "sandbox"}

PostFn = Callable[..., Any]


def _valid(value: Any) -> bool:
    # Odoo lee un Char vacío como False: sólo se admiten ``str``.
    return isinstance(value, str) and _CREDENTIAL_RE.fullmatch(value) is not None


def read_credentials(client_id: Any, client_secret: Any, state: Any) -> Optional[dict]:
    """Credenciales y modo, o ``None`` si no hay nada entregable."""
    mode = _STATE_TO_MODE.get(state) if isinstance(state, str) else None
    if mode is None or not _valid(client_id) or not _valid(client_secret):
        return None
    return {"client_id": client_id, "client_secret": client_secret, "mode": mode}


def fingerprint(creds: dict) -> str:
    """Huella no reversible de lo que se entrega (no contiene el secreto)."""
    raw = f"{creds['client_id']}|{creds['client_secret']}|{creds['mode']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def describe(client_id: Any, client_secret: Any, state: Any, enabled: bool) -> dict:
    """Estado para un panel: sólo modo y últimas 4 cifras del client_id."""
    creds = read_credentials(client_id, client_secret, state)
    return {
        "available": creds is not None,
        "enabled": bool(enabled),
        "mode": creds["mode"] if creds else None,
        "last4": creds["client_id"][-4:] if creds else None,
    }


def is_allowed_api_base(api_base: Any) -> bool:
    if not isinstance(api_base, str):
        return False
    try:
        parsed = urllib.parse.urlparse(api_base)
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in ALLOWED_API_HOSTS


def _reason(resp: Any) -> str:
    """Sólo el código ``error`` de la plataforma, nunca el cuerpo entero."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return f"http_{int(resp.status_code)}"


def _post(url: str, token: str, body: dict, post: PostFn) -> dict:
    """Un único POST. Devuelve ``{"ok": bool, ...}`` sin material secreto."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Embed-Source": EMBED_SOURCE,
        "User-Agent": USER_AGENT,
    }
    try:
        resp = post(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            allow_redirects=False,
        )
    except requests.exceptions.RequestException:
        # Se descarta el mensaje a propósito: no se arrastra nada del cuerpo.
        return {"ok": False, "code": "platform_unreachable"}
    if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
        return {"ok": False, "code": "platform_rejected", "reason": "redirect_rejected"}
    if resp.status_code == 200:
        return {"ok": True}
    return {"ok": False, "code": "platform_rejected", "reason": _reason(resp)}


def connect(api_base: str, token: str, creds: dict, post: PostFn = requests.post) -> dict:
    if not is_allowed_api_base(api_base):
        return {"ok": False, "code": "api_host_not_allowed"}
    res = _post(
        api_base.rstrip("/") + CONNECT_PATH,
        token,
        {
            "flow": "apiKey",
            "accountId": creds["client_id"],
            "apiKey": creds["client_secret"],
            "mode": creds["mode"],
            # Reemplaza la conexión existente: el comerciante pidió sincronizar.
            "force": True,
        },
        post,
    )
    if res["ok"]:
        return {"ok": True, "code": "connected", "mode": creds["mode"]}
    return res


def disconnect(api_base: str, token: str, post: PostFn = requests.post) -> dict:
    if not is_allowed_api_base(api_base):
        return {"ok": False, "code": "api_host_not_allowed"}
    res = _post(api_base.rstrip("/") + DISCONNECT_PATH, token, {}, post)
    if res["ok"]:
        return {"ok": True, "code": "disconnected"}
    return res


def resolve_merchant_id(mapped: Any, global_id: Any, company_count: int) -> str:
    """Merchant de la compañía: el mapa explícito; el global SÓLO con 1 compañía.

    Con varias compañías, caer al global enviaría el secreto de una al merchant
    de otra (H2).
    """
    if isinstance(mapped, str) and mapped.strip():
        return mapped.strip()
    if int(company_count or 0) <= 1 and isinstance(global_id, str):
        return global_id.strip()
    return ""


def flag_param(value: bool) -> str:
    """Valor explícito del parámetro (``"0"`` distingue «apagado» de «ausente»)."""
    return "1" if value else "0"


def resolve_enabled(company_param: Any, legacy_global: Any, company_count: int) -> bool:
    """Opt-in por compañía; el parámetro global antiguo sólo vale con 1 compañía."""
    if isinstance(company_param, str) and company_param != "":
        return company_param == "1"
    if int(company_count or 0) <= 1:
        return legacy_global is True or legacy_global == "1"
    return False


def opt_in_transition(was_enabled: bool, now_enabled: bool) -> Optional[str]:
    if now_enabled and not was_enabled:
        return "enable"
    if was_enabled and not now_enabled:
        return "disable"
    return None


def select_provider(providers: list, written_ids: Any) -> dict:
    """Elige el proveedor PayPal de una compañía. Devuelve sólo lo necesario.

    ``providers``: dicts ``{id, state, client_id, client_secret}`` de la compañía.
    Más de uno no ``disabled`` => ``ambiguous_provider`` (no se envía). Sin
    activos se usa un proveedor escrito (para evaluar D5) o ``disabled``.
    """
    active = [p for p in providers if p.get("state") != "disabled"]
    if len(active) > 1:
        return {"state": "disabled", "client_id": False, "client_secret": False,
                "error": "ambiguous_provider"}
    chosen = None
    if active:
        chosen = active[0]
    else:
        written = set(written_ids or ())
        chosen = next((p for p in providers if p.get("id") in written), None)
    if chosen is None:
        return {"state": "disabled", "client_id": False, "client_secret": False, "error": None}
    return {"state": chosen.get("state") or "disabled",
            "client_id": chosen.get("client_id"),
            "client_secret": chosen.get("client_secret"),
            "error": None}


def _peer_conflict(peer_hashes: Any, own_fp: Optional[str]) -> bool:
    """Otra compañía con el MISMO merchant tiene una huella distinta de la nuestra.

    ``own_fp`` ``None`` (desconexión): cualquier huella ajena es conflicto.
    """
    for h in peer_hashes or ():
        if isinstance(h, str) and h and h != own_fp:
            return True
    return False


def _outcome(action: str, ok: bool, code: str, new_hash: Optional[str] = None, **extra: Any) -> dict:
    return {"action": action, "ok": ok, "code": code, "new_hash": new_hash, **extra}


def run_sync(
    snapshot: dict,
    token_provider: Callable[[], Optional[str]],
    post: PostFn = requests.post,
) -> dict:
    """Decide y ejecuta connect / disconnect / noop para UNA compañía.

    ``snapshot`` (leído por el modelo antes de llamar):
      enabled, state, client_id, client_secret, stored_hash, merchant_id, api_base
      y opcionales ``provider_error`` (p. ej. ``ambiguous_provider``) y
      ``peer_hashes`` (huellas de OTRAS compañías con el mismo merchant).

    Devuelve ``{action, ok, code, new_hash}``. ``new_hash`` es el valor a
    persistir: huella nueva (connect ok), ``""`` (disconnect ok) o ``None``
    (no tocar lo guardado). No contiene el secreto.
    """
    if not snapshot.get("enabled"):
        return _outcome("noop", True, "opt_out")
    if not snapshot.get("merchant_id"):
        return _outcome("noop", False, "no_merchant")
    if snapshot.get("provider_error"):
        return _outcome("noop", False, str(snapshot["provider_error"]))

    creds = read_credentials(
        snapshot.get("client_id"), snapshot.get("client_secret"), snapshot.get("state")
    )
    stored = snapshot.get("stored_hash") or ""

    if creds is None:
        # D5: proveedor desactivado o credenciales vaciadas. Sólo desconecta si
        # antes sincronizamos algo; si no, no hay nada que desconectar.
        if not stored:
            return _outcome("noop", True, "nothing_to_sync")
        action = "disconnect"
    else:
        if fingerprint(creds) == stored:
            return _outcome("noop", True, "unchanged")
        action = "connect"

    own_fp = fingerprint(creds) if creds is not None else None
    if _peer_conflict(snapshot.get("peer_hashes"), own_fp):
        return _outcome(action, False, "shared_merchant_conflict")

    # L2: el host se valida ANTES de pedir el token (no se emite uno para un
    # destino que luego se rechazaría).
    if not is_allowed_api_base(snapshot.get("api_base")):
        return _outcome(action, False, "api_host_not_allowed")

    try:
        token = token_provider()
    except Exception:  # noqa: BLE001 — se contiene sin propagar texto del error
        token = None
    if not isinstance(token, str) or not token:
        return _outcome(action, False, "token_unavailable")

    if action == "connect":
        res = connect(snapshot["api_base"], token, creds, post=post)
        new_hash = fingerprint(creds) if res["ok"] else None
    else:
        res = disconnect(snapshot["api_base"], token, post=post)
        new_hash = "" if res["ok"] else None
    extra = {k: v for k, v in res.items() if k in ("reason", "mode")}
    return _outcome(action, res["ok"], res["code"], new_hash, **extra)
