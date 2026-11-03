"""Consume el fixture compartido del flujo principal (platform-flow-conformance.json).

Plan 04 de reglas de checkout, Task 13 (Odoo). La referencia es
``packages/shared/src/enforcement/platform-flow.ts``; el port es
``models/platform_flow.py``. Los conteos por sección se asertan para que un
fixture vaciado o un filtro equivocado no pasen en verde por vacuidad.

``serverRouting`` no tiene vectores de Odoo (el fixture sólo trae
``prestashop`` y ``woocommerce_agentictools``): el enrutado de Odoo lo fija el
subplan 01 en ``tests/test_organic_checkout_universal_rules.py``.

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_platform_flow_conformance.py \\
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import importlib.util
import json
from pathlib import Path

import pytest

_ADDON = Path(__file__).parent.parent
_FIXTURE = json.loads(
    (
        _ADDON.parent / "shared" / "src" / "enforcement" / "__fixtures__" / "platform-flow-conformance.json"
    ).read_text(encoding="utf-8")
)
_spec = importlib.util.spec_from_file_location("odoo_platform_flow", _ADDON / "models" / "platform_flow.py")
platform_flow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(platform_flow)

_VOCAB = _FIXTURE["_vocabulary"]
_MODES = _VOCAB["failureModes"]["odoo"]
_OPENING = _VOCAB["agentOpeningFailureModes"]["odoo"]
_ALL_UNAVAILABLE = _FIXTURE["unavailableDecision"]
_ODOO_UNAVAILABLE = [v for v in _ALL_UNAVAILABLE if v["failureMode"] in _MODES]
# Task 21: secciones de la Task 20 (avería degradada y forma del resolver).
_ODOO_DEGRADED = [v for v in _FIXTURE["degradedEnforcement"]["vectors"] if v["port"] == "odoo"]
_RESOLVER = _FIXTURE["snapshotResolverShape"]
_RESOLVER_VECTORS = _RESOLVER["vectors"]


# ── Conteos por sección ─────────────────────────────────────────────────────


def test_section_counts():
    assert len(_FIXTURE["agenticDetection"]) == 6
    assert len(_FIXTURE["offlineValveScope"]) == 6
    assert len(_ALL_UNAVAILABLE) == 21
    # 3 humanos + 3 agentes + 1 humano con tope propio en permissive.
    assert len(_ODOO_UNAVAILABLE) == 7
    assert not [v for v in _FIXTURE["serverRouting"] if v.get("port") == "odoo"]
    assert len(_ODOO_DEGRADED) == 22
    assert len(_RESOLVER_VECTORS) == 19


def test_degraded_kinds_match_reference():
    kinds = {v["kind"] for v in _FIXTURE["degradedEnforcement"]["vectors"]}
    assert kinds == set(platform_flow.PLATFORM_DEGRADED_KINDS)


def test_odoo_consumers_of_the_resolver_are_listed():
    """Los ficheros de Odoo que leen el resolver están en ``consumers`` y, los
    que leen el del snapshot, declaran las dos formas (Task 21). El verificador
    recibe el mapa interno que construye ``_build_did_resolver``."""
    reads = {c["path"]: c["readsToday"] for c in _RESOLVER["consumers"] if c["port"] == "odoo"}
    assert reads == {
        "packages/odoo-addon-trusteed/models/enforcement_token_verifier.py": "map",
        "packages/odoo-addon-trusteed/models/platform_flow.py": "list+map",
        "packages/odoo-addon-trusteed/models/sale_order_enforcement.py": "list+map",
    }


def test_vocabulary_matches_port():
    assert tuple(_MODES) == platform_flow.ODOO_FAILURE_MODES
    opening_in_port = [m for m in _MODES if m in platform_flow.AGENT_OPENING_FAILURE_MODES]
    assert opening_in_port == _OPENING
    assert platform_flow.DEFAULT_FAILURE_MODE in _MODES


# ── agenticDetection ────────────────────────────────────────────────────────


@pytest.mark.parametrize("v", _FIXTURE["agenticDetection"], ids=lambda v: v["id"])
def test_agentic_detection(v):
    s = v["signals"]
    assert platform_flow.is_agentic_checkout(s["agentId"], s["tokenPresent"], s["originMarker"]) is v["expected"]


# ── offlineValveScope ───────────────────────────────────────────────────────


@pytest.mark.parametrize("v", _FIXTURE["offlineValveScope"], ids=lambda v: v["id"])
def test_offline_valve_scope(v):
    codes = [r["ruleCode"] for r in platform_flow.offline_valve_rules_for(v["rules"], v["isAgentic"])]
    assert codes == v["expectedCodes"]


# ── unavailableDecision ─────────────────────────────────────────────────────


@pytest.mark.parametrize("v", _ALL_UNAVAILABLE, ids=lambda v: v["id"])
def test_unavailable_decision_pure_port(v):
    """Los 21 vectores contra la función pura (misma lista global que la referencia)."""
    got = platform_flow.decide_on_evaluator_unavailable(v["isAgentic"], v["failureMode"], v["offlineBlock"])
    assert got == v["expected"]


@pytest.mark.parametrize("v", _ODOO_UNAVAILABLE, ids=lambda v: v["id"])
def test_unavailable_decision_through_resolver(v):
    """Los 7 del vocabulario de Odoo, pasando antes por el resolutor del port."""
    mode = platform_flow.resolve_failure_mode(v["failureMode"])
    assert platform_flow.decide_on_evaluator_unavailable(v["isAgentic"], mode, v["offlineBlock"]) == v["expected"]


# ── Resolutor del modo (CARRY de la Task 1: cada port valida SU vocabulario) ─


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, "balanced"),
        ("strict", "strict"),
        ("balanced", "balanced"),
        ("permissive", "permissive"),
        ("  Strict ", "strict"),
        ("BALANCED", "balanced"),
        ("observe", None),
        ("enforce", None),
        ("", None),
        ("unknown", None),
        (1, None),
    ],
)
def test_resolve_failure_mode(raw, expected):
    assert platform_flow.resolve_failure_mode(raw) == expected


@pytest.mark.parametrize("raw", ["observe", "enforce", "", "unknown", 1] + _VOCAB["unresolvedFailureModeSamples"])
def test_agent_closes_with_a_mode_outside_the_odoo_vocabulary(raw):
    """``observe`` abre en la lista GLOBAL de la referencia; en Odoo no es un
    modo, así que tras el resolutor tiene que cerrar para un agente."""
    mode = platform_flow.resolve_failure_mode(raw)
    if raw in ("STRICT",):
        # Se normaliza a ``strict``, que también cierra.
        assert mode == "strict"
    assert platform_flow.decide_on_evaluator_unavailable(True, mode, False) == "BLOCK"
    assert platform_flow.decide_on_evaluator_unavailable(False, mode, False) == "ALLOW"


def test_internal_error_never_blocks_a_human_and_never_opens_an_agent():
    assert platform_flow.decide_on_internal_error(False) == "ALLOW"
    assert platform_flow.decide_on_internal_error(True) == "BLOCK"


# ── degradedEnforcement (Task 20/21) ────────────────────────────────────────


@pytest.mark.parametrize("v", _ODOO_DEGRADED, ids=lambda v: v["id"])
def test_degraded_enforcement(v):
    """Los 22 vectores del port ``odoo`` (failureMode ya resuelto por el port)."""
    got = platform_flow.decide_on_degraded_enforcement(
        v["kind"], v["isAgentic"], v["tokenPresent"], v["failureMode"], v["offlineBlock"]
    )
    assert got == v["expected"]


@pytest.mark.parametrize("v", [v for v in _ODOO_DEGRADED if v["failureMode"] in _MODES], ids=lambda v: v["id"])
def test_degraded_enforcement_through_resolver(v):
    """Los que llevan un modo del vocabulario, pasando antes por ``resolve_failure_mode``."""
    mode = platform_flow.resolve_failure_mode(v["failureMode"])
    got = platform_flow.decide_on_degraded_enforcement(
        v["kind"], v["isAgentic"], v["tokenPresent"], mode, v["offlineBlock"]
    )
    assert got == v["expected"]


def test_unknown_kind_is_treated_as_invalid_signature():
    assert platform_flow.decide_on_degraded_enforcement("texto-crudo", True, False, "permissive", False) == "BLOCK"
    assert platform_flow.decide_on_degraded_enforcement("texto-crudo", False, False, "strict", True) == "ALLOW"


@pytest.mark.parametrize(
    "local, expected",
    [
        (None, "balanced"),
        ("balanced", "balanced"),
        ("permissive", "balanced"),  # el modo local no firmado nunca ABRE
        ("strict", "strict"),  # sí puede cerrar
        (" STRICT ", "strict"),
        ("garbage", None),  # presente y no reconocido: cierra (CARRY Task 1)
    ],
)
def test_degraded_failure_mode_only_tightens(local, expected):
    assert platform_flow.degraded_failure_mode(local) == expected


# ── snapshotResolverShape (Task 20/21) ──────────────────────────────────────


@pytest.mark.parametrize("v", _RESOLVER_VECTORS, ids=lambda v: v["id"])
def test_resolver_shape(v):
    assert platform_flow.resolve_agent_did_public_key(v["resolver"], v["did"]) == v["expected"]


@pytest.mark.parametrize("v", _RESOLVER_VECTORS, ids=lambda v: v["id"])
def test_resolver_map_used_by_the_verifier_agrees(v):
    """``agent_did_resolver_map`` (lo que recibe ``verify_agent_token``) da la
    misma clave que la referencia para el DID del vector."""
    got = platform_flow.agent_did_resolver_map(v["resolver"]).get(v["did"]) if isinstance(v["did"], str) else None
    assert got == v["expected"]


def test_snapshot_payload_resolver_is_a_list_and_resolves():
    payload = _RESOLVER["snapshotPayload"]
    assert isinstance(payload["agentDidResolver"], list)
    assert "installationId" not in payload and "hmacSecret" not in payload
    resolved = platform_flow.agent_did_resolver_map(payload["agentDidResolver"])
    assert set(resolved) == {e["did"] for e in payload["agentDidResolver"]}
