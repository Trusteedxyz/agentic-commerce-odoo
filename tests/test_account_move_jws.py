"""
Spec 043 T043-Pend39 — Tests para account_move_jws helpers.

Stdlib-only — espeja las funciones puras de
`models/account_move_jws.py` para verificar la construcción del JWT HS256 de
bootstrap sin invocar el runtime de Odoo ni hacer red.

Cubre:
  - _b64url: base64 urlsafe sin padding (alineado con RFC 7515 §2)
  - _sign_bootstrap_for_move: header alg/typ/kid + payload claims requeridos
  - Determinismo de la firma (mismo input → misma firma)
  - exp = iat + 30 (TTL bootstrap corto, FR S043-002)
  - scope.company_ids: defaults [company_id] vs override explícito vs []

Run:
  python -m pytest packages/odoo-addon-trusteed/tests/test_account_move_jws.py -v
"""

import base64
import hashlib
import hmac
import json
import time
import unittest


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sign_bootstrap_for_move(
    merchant_id,
    secret,
    db_name,
    company_id=0,
    company_ids=None,
    now=None,
):
    """Mirror of models/account_move_jws.py::_sign_bootstrap_for_move.

    `now` parameter added for deterministic tests; production helper reads
    time.time() internally.
    """
    issued = int(now if now is not None else time.time())
    payload = {
        "merchant_id": merchant_id,
        "iss": f"odoo:{db_name}",
        "iat": issued,
        "exp": issued + 30,
        "jti": "test-jti-deterministic",
        "scope": {
            "source": "account_move_jws",
            "company_id": company_id,
            "company_ids": company_ids
            if company_ids is not None
            else ([company_id] if company_id else []),
        },
    }
    header = {"alg": "HS256", "typ": "JWT", "kid": merchant_id}
    h64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    msg = f"{h64}.{p64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return f"{h64}.{p64}.{_b64url(sig)}"


def _decode_b64url(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


class B64UrlTests(unittest.TestCase):
    def test_strips_padding(self):
        # 1 byte → 2 chars padded would be "AQ==" → unpadded "AQ"
        self.assertEqual(_b64url(b"\x01"), "AQ")

    def test_urlsafe_alphabet(self):
        # 0xfb 0xff → "+/8" in standard b64, "-_8" in urlsafe
        out = _b64url(b"\xfb\xff")
        self.assertNotIn("+", out)
        self.assertNotIn("/", out)


class JwtStructureTests(unittest.TestCase):
    def setUp(self):
        self.merchant = "550e8400-e29b-41d4-a716-446655440000"
        self.secret = "a" * 64
        self.token = _sign_bootstrap_for_move(
            self.merchant,
            self.secret,
            db_name="odoo_test",
            company_id=7,
            company_ids=[7, 11],
            now=1_700_000_000,
        )

    def test_three_parts(self):
        self.assertEqual(self.token.count("."), 2)

    def test_header(self):
        header_b64 = self.token.split(".")[0]
        header = json.loads(_decode_b64url(header_b64))
        self.assertEqual(header["alg"], "HS256")
        self.assertEqual(header["typ"], "JWT")
        self.assertEqual(header["kid"], self.merchant)

    def test_payload_claims(self):
        payload_b64 = self.token.split(".")[1]
        payload = json.loads(_decode_b64url(payload_b64))
        self.assertEqual(payload["merchant_id"], self.merchant)
        self.assertEqual(payload["iss"], "odoo:odoo_test")
        self.assertEqual(payload["iat"], 1_700_000_000)
        self.assertEqual(payload["exp"], 1_700_000_030)
        self.assertEqual(payload["scope"]["source"], "account_move_jws")
        self.assertEqual(payload["scope"]["company_id"], 7)
        self.assertEqual(payload["scope"]["company_ids"], [7, 11])

    def test_signature_verifies_with_secret(self):
        h64, p64, sig_b64 = self.token.split(".")
        expected = hmac.new(
            self.secret.encode("utf-8"),
            f"{h64}.{p64}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        self.assertEqual(_decode_b64url(sig_b64), expected)


class JwtTtlTests(unittest.TestCase):
    def test_exp_is_iat_plus_30(self):
        token = _sign_bootstrap_for_move(
            "m", "s" * 64, "db", now=1_000_000_000
        )
        payload = json.loads(_decode_b64url(token.split(".")[1]))
        self.assertEqual(payload["exp"] - payload["iat"], 30)


class ScopeCompanyIdsTests(unittest.TestCase):
    """S043-002: scope must carry per-company isolation hints."""

    def test_defaults_to_singleton_when_company_id_set(self):
        token = _sign_bootstrap_for_move("m", "s" * 64, "db", company_id=5, now=0)
        payload = json.loads(_decode_b64url(token.split(".")[1]))
        self.assertEqual(payload["scope"]["company_ids"], [5])

    def test_defaults_to_empty_when_company_id_zero(self):
        token = _sign_bootstrap_for_move("m", "s" * 64, "db", company_id=0, now=0)
        payload = json.loads(_decode_b64url(token.split(".")[1]))
        self.assertEqual(payload["scope"]["company_ids"], [])

    def test_explicit_override_wins(self):
        token = _sign_bootstrap_for_move(
            "m", "s" * 64, "db", company_id=5, company_ids=[1, 2, 3], now=0
        )
        payload = json.loads(_decode_b64url(token.split(".")[1]))
        self.assertEqual(payload["scope"]["company_ids"], [1, 2, 3])


class SignDeterminismTests(unittest.TestCase):
    def test_same_inputs_same_signature(self):
        a = _sign_bootstrap_for_move("m", "s" * 64, "db", company_id=1, now=42)
        b = _sign_bootstrap_for_move("m", "s" * 64, "db", company_id=1, now=42)
        # Signatures must match byte-for-byte given identical inputs (including jti).
        self.assertEqual(a, b)

    def test_different_secret_changes_signature(self):
        a = _sign_bootstrap_for_move("m", "a" * 64, "db", company_id=1, now=42)
        b = _sign_bootstrap_for_move("m", "b" * 64, "db", company_id=1, now=42)
        self.assertNotEqual(a.split(".")[2], b.split(".")[2])


if __name__ == "__main__":
    unittest.main()
