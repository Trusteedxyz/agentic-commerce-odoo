"""Páginas de política -> Trusteed. Python puro (sin Odoo) sobre utils/policy_pages.py.

El modelo ORM (models/policy_pages_sync.py) NO se ejecuta aquí: requiere Odoo real.
"""

import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

import requests.exceptions

_ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(_ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# policy_pages hace `from . import paypal_sync`: se carga como paquete mínimo.
_pkg = types.ModuleType("amcp_utils")
_pkg.__path__ = [str(_ROOT / "utils")]
sys.modules["amcp_utils"] = _pkg
_load("amcp_utils.paypal_sync", "utils/paypal_sync.py")
pp = _load("amcp_utils.policy_pages", "utils/policy_pages.py")

API = "https://api.trusteed.xyz"


BASE = "https://tienda.example.com"
MOD = datetime(2026, 11, 1, 10, 30, 0)


def build(value, base=BASE, domains=(), modified=MOD):
    return pp.build_policy_pages(value, base, domains, modified)


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body

    def json(self):
        if self._b is None:
            raise ValueError
        return self._b


class TestBuild(unittest.TestCase):
    def test_path_resolves_against_base_url(self):
        self.assertEqual(
            build("/devoluciones"),
            [{"type": "returns", "url": "https://tienda.example.com/devoluciones",
              "page_modified_at": "2026-11-01T10:30:00Z"}],
        )

    def test_absolute_url_of_own_host(self):
        self.assertEqual(build("https://tienda.example.com/d")[0]["url"], "https://tienda.example.com/d")

    def test_absolute_url_of_website_domain(self):
        self.assertTrue(build("https://shop.example.org/d", domains=["shop.example.org"]))
        self.assertEqual(build("https://shop.example.org/d"), [])

    def test_empty_or_non_string(self):
        for v in ("", "  ", None, False):
            self.assertEqual(build(v), [])

    def test_foreign_host_rejected(self):
        self.assertEqual(build("https://evil.example.net/d"), [])

    def test_http_credentials_ip_localhost_rejected(self):
        for v in ("http://tienda.example.com/d", "https://u:p@tienda.example.com/d",
                  "//evil.example.net/d", "tienda.example.com/d"):
            self.assertEqual(build(v), [], v)
        self.assertEqual(build("/d", base="https://127.0.0.1"), [])
        self.assertEqual(build("/d", base="https://localhost"), [])
        self.assertEqual(build("https://10.0.0.1/d", domains=["10.0.0.1"]), [])

    def test_path_without_usable_base_rejected(self):
        self.assertEqual(build("/d", base="http://tienda.example.com"), [])
        self.assertEqual(build("/d", base=False), [])

    def test_length_date_and_whitespace(self):
        self.assertEqual(build("/" + "a" * 2100), [])
        self.assertEqual(build("/d", modified=None), [])
        self.assertEqual(build("/a b"), [])

    def test_no_source_or_extra_keys(self):
        self.assertEqual(set(build("/d")[0]), {"type", "url", "page_modified_at"})


class TestSend(unittest.TestCase):
    def test_sends_put_with_strict_body(self):
        calls = []

        def put(url, **kw):
            calls.append((url, kw))
            return Resp(200)

        pages = build("/d")
        res = pp.send_policy_pages(API, "tok", pages, put=put)
        self.assertEqual(res, {"ok": True, "code": "sent"})
        url, kw = calls[0]
        self.assertEqual(url, API + "/v1/embed/merchant/policy-pages")
        self.assertEqual(json.loads(kw["data"]), {"policyPages": pages})
        self.assertEqual(kw["headers"]["Authorization"], "Bearer tok")
        self.assertFalse(kw["allow_redirects"])

    def test_empty_list_is_sent_to_clear(self):
        calls = []
        res = pp.send_policy_pages(API, "t", [], put=lambda url, **kw: calls.append(kw) or Resp(200))
        self.assertTrue(res["ok"])
        self.assertEqual(json.loads(calls[0]["data"]), {"policyPages": []})

    def test_host_not_allowed(self):
        res = pp.send_policy_pages("https://evil.example", "t", [{"x": 1}], put=lambda *a, **k: None)
        self.assertEqual(res["code"], "api_host_not_allowed")

    def test_rejection_reports_only_error_code(self):
        res = pp.send_policy_pages(
            API, "t", [{"x": 1}], put=lambda *a, **k: Resp(400, {"error": "invalid_body", "x": "y"})
        )
        self.assertEqual(res, {"ok": False, "code": "platform_rejected", "reason": "invalid_body"})

    def test_network_error_not_fatal(self):
        def put(*a, **k):
            raise requests.exceptions.ConnectionError("boom")

        self.assertEqual(pp.send_policy_pages(API, "t", [{"x": 1}], put=put)["code"], "platform_unreachable")


if __name__ == "__main__":
    unittest.main()
