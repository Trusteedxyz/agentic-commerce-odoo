"""Spec 043 R1 (codex remediation P1#1) — RFC 8785 JCS canonicalizer.

Produces byte-identical output to:
  - Node `canonicalizeJSON` from `@trusteed/shared/a2a/agent-card-signer.ts`
  - PHP `JcsCanonicalize` from `packages/prestashop-module-trusteed/src/Hmac/JcsCanonicalize.php`

Cross-language CI gate: `apps/api/src/__tests__/fixtures/jcs-golden-vectors.json`
runs the same 10 vectors against all 3 implementations.

Rules (RFC 8785):
  - Object keys sorted by Unicode code point (lex, str.__lt__)
  - No extra whitespace
  - Strings: only \", \\, control chars <0x20 escaped (slash, unicode as-is UTF-8)
  - Numbers per ECMA-262 (no trailing zeros for integer-valued floats)

NOTE: Python `json.dumps` with `ensure_ascii=False, sort_keys=True` is *close*
but `sort_keys` only sorts top-level. Recursive sort requires hand-rolled.
"""

from __future__ import annotations

import json
import math
from typing import Any


def canonicalize(value: Any) -> str:
    """Canonicalize value to RFC 8785 canonical JSON string."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JCS: non-finite numbers not allowed")
        return _format_number(value)
    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonicalize(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value.keys(), key=lambda k: str(k))
        pairs = [
            _escape_string(str(k)) + ":" + canonicalize(value[k]) for k in keys
        ]
        return "{" + ",".join(pairs) + "}"
    raise TypeError(f"JCS: unsupported type {type(value).__name__}")


def _escape_string(s: str) -> str:
    """Match Node's JSON.stringify string escaping."""
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


def _format_number(value: float) -> str:
    """Match ECMA-262 Number.prototype.toString for typical payload values.

    For integer-valued floats: drop trailing ".0" (e.g. 99.0 → "99").
    For others: use Python's default repr which matches JS for non-exponential
    finite values in the precision range we use (totalPaid, amount, hours).
    """
    if value == int(value):
        return str(int(value))
    # Python's repr() gives shortest round-trippable representation.
    # json.dumps(float) is equivalent for typical values.
    return json.dumps(value)
