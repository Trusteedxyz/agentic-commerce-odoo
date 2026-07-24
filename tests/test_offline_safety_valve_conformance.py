"""pytest tests — App Store remediation follow-up (2026-07-11/12).

Cross-language conformance test. Loads the SAME fixture consumed by
packages/shared/src/enforcement/__tests__/offline-safety-valve-conformance.test.ts
and the WooCommerce/PrestaShop/Magento plugins' PHPUnit equivalents, and
asserts offline_safety_valve_evaluator.evaluate() matches the canonical TS
evaluators for every vector.

Run:
    python3 -m pytest packages/odoo-addon-trusteed/tests/test_offline_safety_valve_conformance.py \\
      --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_ADDON_ROOT = Path(__file__).parent.parent
_FIXTURE_PATH = (
    _ADDON_ROOT.parent / "shared" / "src" / "enforcement" / "__fixtures__"
    / "offline-safety-valve-conformance.json"
)

_ROOT_PKG = "addon_offline_valve"
_MODELS_PKG = f"{_ROOT_PKG}.models"


def _ensure_pkg(key: str) -> types.ModuleType:
    if key not in sys.modules:
        sys.modules[key] = types.ModuleType(key)
    return sys.modules[key]


def _load_module(rel: str, key: str, pkg: str) -> types.ModuleType:
    if key in sys.modules:
        return sys.modules[key]
    _ensure_pkg(pkg)
    spec = importlib.util.spec_from_file_location(key, _ADDON_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


_ensure_pkg(_ROOT_PKG)
_ensure_pkg(_MODELS_PKG)

evaluator = _load_module(
    "models/offline_safety_valve_evaluator.py",
    f"{_MODELS_PKG}.offline_safety_valve_evaluator",
    _MODELS_PKG,
)


def _load_fixture() -> list[dict]:
    assert _FIXTURE_PATH.exists(), "conformance fixture must be reachable from the monorepo layout"
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["vectors"]


VECTORS = _load_fixture()


def test_fixture_has_vectors_for_every_implemented_rule():
    codes = sorted({v["ruleCode"] for v in VECTORS})
    assert codes == ["R014", "R018", "R019", "R020", "R025", "R027", "R028", "R029", "R030"]


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_vector_matches_expectation(vector):
    rules = [
        {
            "ruleCode": vector["ruleCode"],
            "enabled": True,
            "params": vector["params"],
        }
    ]
    result = evaluator.evaluate(rules, vector["orderContext"], vector["cartAttributes"])

    if vector["expectedMatch"]:
        assert result is not None, (
            f"vector '{vector['id']}' ({vector['ruleCode']}) expected a BLOCK but got ALLOW"
        )
        assert result["ruleCode"] == vector["ruleCode"]
    else:
        assert result is None, (
            f"vector '{vector['id']}' ({vector['ruleCode']}) expected ALLOW but got BLOCK: "
            f"{result.get('reason') if result else ''}"
        )
