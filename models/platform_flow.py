"""Flujo principal — port Python de packages/shared/src/enforcement/platform-flow.ts.

Fijado por ``packages/shared/src/enforcement/__fixtures__/platform-flow-conformance.json``
(auditoría spec-048 2026-09-27, plan 04 de reglas de checkout, Task 13) y
consumido por ``tests/test_platform_flow_conformance.py``.

Invariante (decisión D1 del plan 04): un comprador humano —pedido sin ninguna
señal agéntica— nunca se bloquea por una avería nuestra, en ningún modo
(tampoco ``strict``); sólo puede bloquearlo una regla ``appliesTo: ALL`` del
propio comerciante (el tope de R030, países, horario…), que sigue valiendo
offline para todos. El ``fallbackMode`` del comerciante sólo gobierna a los
agentes.

Funciones puras: sin Odoo, sin red, sin estado.
"""

from typing import Iterable, List, Optional

# Vocabulario de ``fallbackMode`` en Odoo (``trusteed.company.enforcement.map``
# y el snapshot firmado): ``_vocabulary.failureModes.odoo`` del fixture.
ODOO_FAILURE_MODES = ("strict", "balanced", "permissive")

# Default del addon cuando el modo NO viene (ausente / snapshot inexistente):
# es el que ``_resolve_fallback_mode`` devolvía antes de esta tarea.
DEFAULT_FAILURE_MODE = "balanced"

# Modos que ABREN a un agente ante una avería, igual que la referencia
# (``AGENT_OPENING_FAILURE_MODES``): lista GLOBAL de los cinco ports, que por
# eso incluye ``observe``. En Odoo ``observe`` no es un modo válido y nunca
# llega aquí: ``resolve_failure_mode`` lo convierte en «no resuelto» (None),
# que cierra.
AGENT_OPENING_FAILURE_MODES = ("observe", "balanced", "permissive")


def is_agentic_checkout(agent_id: Optional[str], token_present: bool, origin_marker: bool) -> bool:
    """Cualquier señal agéntica ⇒ agéntico; un token presentado cuenta aunque no verifique.

    En Odoo la única señal que existe hoy es el token (campo
    ``x_trusteed_agent_token`` o clave de contexto ``trusteed_agent_token``):
    no hay DID declarado ni marca de origen que el addon lea antes de decidir.
    """
    return (agent_id is not None and agent_id != "") or bool(token_present) or bool(origin_marker)


def resolve_failure_mode(raw) -> Optional[str]:
    """Normaliza el ``fallbackMode`` contra el vocabulario de Odoo.

    - ausente (``None``) ⇒ ``balanced`` (default histórico del addon);
    - recorta espacios y pasa a minúsculas;
    - un valor fuera de ``strict|balanced|permissive`` (``""``, ``observe``,
      ``unknown``, un tipo que no es cadena…) ⇒ ``None`` = no resuelto, que
      CIERRA para un agente en ``decide_on_evaluator_unavailable``.
    """
    if raw is None:
        return DEFAULT_FAILURE_MODE
    if not isinstance(raw, str):
        return None
    mode = raw.strip().lower()
    return mode if mode in ODOO_FAILURE_MODES else None


def offline_valve_rules_for(rules: Iterable, is_agentic: bool) -> List[dict]:
    """Reglas que la válvula offline puede aplicar, en el orden recibido.

    - Agente: todas salvo las ``appliesTo: HUMAN`` (ausente incluido).
    - Humano: sólo ``ALL`` y ``HUMAN`` — las del propio comerciante. Un
      ``appliesTo`` ausente no cuenta para un humano: ante la duda, nunca
      bloquear a una persona. (El snapshot firmado siempre lo trae.)

    ``appliesTo`` se compara sin distinguir mayúsculas. Los elementos que no
    son diccionarios se descartan (la válvula ya los ignoraba).
    """
    kept = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        applies_to = str(rule.get("appliesTo") or "").upper()
        if is_agentic:
            if applies_to != "HUMAN":
                kept.append(rule)
        elif applies_to in ("ALL", "HUMAN"):
            kept.append(rule)
    return kept


def decide_on_evaluator_unavailable(is_agentic: bool, failure_mode: Optional[str], offline_block: bool) -> str:
    """El servidor no contestó o no hay política legible: ``"ALLOW"`` | ``"BLOCK"``.

    ``offline_block`` es el veredicto de la válvula ya filtrada con
    ``offline_valve_rules_for``: una regla del comerciante bloquea a cualquiera.
    Sin ella, un humano pasa SIEMPRE; un agente sólo pasa con un modo de
    ``AGENT_OPENING_FAILURE_MODES`` (comparación exacta; ``None`` o cualquier
    otro valor cierra).
    """
    if offline_block:
        return "BLOCK"
    if not is_agentic:
        return "ALLOW"
    return "ALLOW" if failure_mode is not None and failure_mode in AGENT_OPENING_FAILURE_MODES else "BLOCK"


def decide_on_internal_error(is_agentic: bool) -> str:
    """Una excepción NUESTRA durante la evaluación (no una regla, no la red).

    Un humano nunca se bloquea por ella; a un agente no se le abre en ningún
    modo (una avería propia no es un permiso): ``BLOCK``.
    """
    return "BLOCK" if is_agentic else "ALLOW"


# ── Capa degradada (plan 04, Task 20/21) ────────────────────────────────────
#
# Port de ``PlatformDegradedKind`` / ``decideOnDegradedEnforcement`` de la
# referencia (``platform-flow.ts``), fijado por la sección
# ``degradedEnforcement`` del fixture.
#
# - ``invalid_signature``: hay snapshot pero su JWS no verifica (kid
#   desconocido tras reintentar el JWKS, firma que no cuadra, payload
#   manipulado). Señal de ataque, no caída.
# - ``missing_snapshot``: no hay snapshot utilizable (caché vacía o caducada y
#   servidor caído, o el servidor no lo devuelve).
# - ``evaluator_unavailable``: snapshot VERIFICADO (de caché incluido) y capa 2
#   caída.
# - ``layer_disabled``: el comerciante apagó la capa (``celEnabled=false``).
#   Configuración, no avería.
PLATFORM_DEGRADED_KINDS = (
    "invalid_signature",
    "missing_snapshot",
    "evaluator_unavailable",
    "layer_disabled",
)


def decide_on_degraded_enforcement(
    kind: str,
    is_agentic: bool,
    token_present: bool,
    failure_mode: Optional[str],
    offline_block: bool,
) -> str:
    """Decisión ante una capa degradada: ``"ALLOW"`` | ``"BLOCK"``.

    Sobre un AGENTE (``token_present`` también cuenta como señal agéntica):

    - ``invalid_signature`` ⇒ BLOCK en todos los modos;
    - ``missing_snapshot`` con token ⇒ BLOCK en todos los modos (el resolver de
      DIDs viaja DENTRO del snapshot: sin él el token no se puede verificar, y
      dejarlo pasar en ``balanced`` convertía un token falsificado en un pase,
      I1-Odoo);
    - ``missing_snapshot`` sin token ⇒ el modo (sin válvula);
    - ``evaluator_unavailable`` ⇒ el modo, con la válvula offline;
    - ``layer_disabled`` ⇒ ALLOW (configuración).

    Sobre un HUMANO nunca se cierra por avería: ALLOW salvo
    ``evaluator_unavailable`` con ``offline_block`` (regla ALL/HUMAN de un
    snapshot verificado, p. ej. el tope de R030). En ``invalid_signature`` y
    ``missing_snapshot`` se IGNORA ``offline_block``: no hay reglas verificadas
    del comerciante. Una causa desconocida se trata como ``invalid_signature``.
    """
    agentic = bool(is_agentic) or bool(token_present)
    if kind == "layer_disabled":
        return "ALLOW"
    if kind == "evaluator_unavailable":
        return decide_on_evaluator_unavailable(agentic, failure_mode, offline_block)
    if kind == "missing_snapshot":
        if not agentic:
            return "ALLOW"
        if token_present:
            return "BLOCK"
        return decide_on_evaluator_unavailable(agentic, failure_mode, False)
    # ``invalid_signature`` y cualquier causa desconocida.
    return "BLOCK" if agentic else "ALLOW"


def degraded_failure_mode(local_raw) -> Optional[str]:
    """Modo para decidir SIN snapshot verificado (``missing_snapshot`` sin token).

    Sin snapshot firmado no hay ``fallbackMode`` autoritativo: vale el default
    ``balanced``. El ``fallbackMode`` del mapa por compañía
    (``trusteed.company.enforcement.map``) es local y NO va firmado, así que
    sólo puede CERRAR, nunca abrir: si, resuelto contra el vocabulario, cierra
    a un agente (``strict``, o un valor presente que no se reconoce), gana; si
    abre o no existe, se queda ``balanced``.
    """
    if local_raw is not None:
        local = resolve_failure_mode(local_raw)
        if decide_on_evaluator_unavailable(True, local, False) == "BLOCK":
            return local
    return DEFAULT_FAILURE_MODE


def _to_agent_did_public_key(jwk) -> Optional[dict]:
    if not isinstance(jwk, dict):
        return None
    x = jwk.get("x")
    if not isinstance(x, str) or x == "":
        return None
    key = {"x": x}
    for name in ("kty", "crv", "kid"):
        value = jwk.get(name)
        if isinstance(value, str):
            key[name] = value
    return key


def resolve_agent_did_public_key(resolver, did) -> Optional[dict]:
    """Clave pública de ``did`` en el ``agentDidResolver`` del snapshot.

    Port de ``resolveAgentDidPublicKey``. Forma REAL que emite el servidor: una
    LISTA ``[{did, publicKeyJwk: {kty, crv, x, kid?}}]``; forma ANTIGUA que
    leía este addon: un MAPA ``{did: {x, …}}``. Acepta las dos:

    - lista: vale la ÚLTIMA entrada bien formada cuyo ``did`` coincide
      exactamente (como WC principal y Magento);
    - mapa: sólo claves reales del diccionario;
    - una entrada malformada (no objeto, ``did`` no cadena, ``publicKeyJwk`` no
      objeto, ``x`` ausente o vacío) se ignora sin lanzar; cualquier otro
      ``resolver`` (``None``, cadena, número) no resuelve nada.

    Devuelve ``{x, kty?, crv?, kid?}`` (sólo cadenas) o ``None``. Sólo
    RESUELVE: comprobar ``kty``/``crv`` y la firma es del verificador.
    """
    if not isinstance(did, str) or did == "":
        return None
    if isinstance(resolver, list):
        found = None
        for entry in resolver:
            if not isinstance(entry, dict) or entry.get("did") != did:
                continue
            key = _to_agent_did_public_key(entry.get("publicKeyJwk"))
            if key is not None:
                found = key
        return found
    if not isinstance(resolver, dict) or did not in resolver:
        return None
    return _to_agent_did_public_key(resolver[did])


def agent_did_resolver_map(resolver) -> dict:
    """``{did: clave}`` con la misma semántica que ``resolve_agent_did_public_key``
    para cada DID presente (la forma que consume ``verify_agent_token``)."""
    if isinstance(resolver, list):
        dids = [e.get("did") for e in resolver if isinstance(e, dict)]
    elif isinstance(resolver, dict):
        dids = list(resolver.keys())
    else:
        return {}
    out = {}
    for did in dids:
        key = resolve_agent_did_public_key(resolver, did)
        if key is not None:
            out[did] = key
    return out
