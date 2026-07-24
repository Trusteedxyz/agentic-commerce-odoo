"""Spec 043 R1 (codex remediation P1#1) — JCS golden vectors for Python.

Loads the SAME golden vectors used by Node + PHP. Cross-language CI gate.

Run: python -m pytest packages/odoo-addon-trusteed/tests/test_jcs_golden_vectors.py -v
"""

import json
import os
import sys
import unittest

# Vendored canonicalize impl — keep tests stdlib-only by inlining.
import math


def canonicalize(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JCS: non-finite numbers not allowed")
        if value == int(value):
            return str(int(value))
        return json.dumps(value)
    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonicalize(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value.keys(), key=lambda k: str(k))
        pairs = [_escape_string(str(k)) + ":" + canonicalize(value[k]) for k in keys]
        return "{" + ",".join(pairs) + "}"
    raise TypeError(f"JCS: unsupported type {type(value).__name__}")


def _escape_string(s):
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if cp < 0x20:
            if cp == 0x08:
                out.append("\\b")
            elif cp == 0x09:
                out.append("\\t")
            elif cp == 0x0A:
                out.append("\\n")
            elif cp == 0x0C:
                out.append("\\f")
            elif cp == 0x0D:
                out.append("\\r")
            else:
                out.append(f"\\u{cp:04x}")
        elif cp == 0x22:
            out.append('\\"')
        elif cp == 0x5C:
            out.append("\\\\")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


_THIS = os.path.dirname(os.path.abspath(__file__))
_FIXTURE_PATH = os.path.normpath(
    os.path.join(
        _THIS, "..", "..", "..", "apps", "api", "src", "__tests__", "fixtures",
        "jcs-golden-vectors.json",
    )
)


def _load_fixture():
    if not os.path.exists(_FIXTURE_PATH):
        return None
    with open(_FIXTURE_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class JcsGoldenVectorsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = _load_fixture()
        if cls.fixture is None:
            raise unittest.SkipTest(f"fixture not found at {_FIXTURE_PATH}")

    def test_at_least_10_vectors(self):
        self.assertGreaterEqual(len(self.fixture["vectors"]), 10)

    def test_all_vectors_pass(self):
        failures = []
        for v in self.fixture["vectors"]:
            actual = canonicalize(v["input"])
            if actual != v["canonical"]:
                failures.append(
                    f"\n{v['id']}: expected={v['canonical']!r} actual={actual!r}"
                )
        if failures:
            self.fail(f"{len(failures)} vector(s) failed:" + "".join(failures))

    def test_deterministic(self):
        a = canonicalize({"b": 2, "a": 1})
        b = canonicalize({"a": 1, "b": 2})
        self.assertEqual(a, b)

    def test_non_finite_raises(self):
        with self.assertRaises(ValueError):
            canonicalize(float("inf"))


if __name__ == "__main__":
    unittest.main()
