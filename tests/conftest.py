# conftest.py — pytest isolation for pure-Python Odoo addon tests.
#
# This file prevents pytest from treating the parent odoo-addon-trusteed
# directory as a Python package (which would trigger relative imports that
# require the Odoo runtime).  When pytest finds a conftest.py it does NOT
# ascend further looking for __init__.py files in parent directories when the
# test file is collected directly.
#
# Run tests with:
#   python3 -m pytest packages/odoo-addon-trusteed/tests/ \
#     --rootdir=packages/odoo-addon-trusteed/tests --import-mode=importlib -v

import datetime
import sys
import types


def _install_full_odoo_stubs() -> None:
    """Install comprehensive odoo.* stubs in sys.modules.

    Called once at conftest load time (before any test module is imported).
    Subsequent calls are idempotent — missing attrs are patched in without
    replacing already-registered module objects.
    """

    class _FakeField:
        def __init__(self, *a, **kw):
            pass

    class _FakeDatetime(_FakeField):
        @staticmethod
        def now() -> datetime.datetime:
            return datetime.datetime(2026, 5, 7, 12, 0, 0)

    # ── odoo.exceptions ────────────────────────────────────────────────────────

    if "odoo.exceptions" not in sys.modules:
        odoo_exc = types.ModuleType("odoo.exceptions")
        sys.modules["odoo.exceptions"] = odoo_exc
    else:
        odoo_exc = sys.modules["odoo.exceptions"]

    for exc_name in ("ValidationError", "UserError", "AccessError"):
        if not hasattr(odoo_exc, exc_name):
            setattr(odoo_exc, exc_name, type(exc_name, (Exception,), {}))

    # ── odoo.api ───────────────────────────────────────────────────────────────

    if "odoo.api" not in sys.modules:
        odoo_api = types.ModuleType("odoo.api")
        sys.modules["odoo.api"] = odoo_api
    else:
        odoo_api = sys.modules["odoo.api"]

    for dec in ("model", "model_create_multi"):
        if not hasattr(odoo_api, dec):
            setattr(odoo_api, dec, lambda fn: fn)
    if not hasattr(odoo_api, "depends"):
        odoo_api.depends = lambda *a: (lambda fn: fn)  # type: ignore[attr-defined]

    # ── odoo.fields ────────────────────────────────────────────────────────────

    if "odoo.fields" not in sys.modules:
        odoo_fields = types.ModuleType("odoo.fields")
        sys.modules["odoo.fields"] = odoo_fields
    else:
        odoo_fields = sys.modules["odoo.fields"]

    for field_name in ("Char", "Integer", "Float", "Boolean", "Selection", "Text",
                       "Html", "Binary", "Date", "Monetary", "Many2one", "One2many",
                       "Many2many"):
        if not hasattr(odoo_fields, field_name):
            setattr(odoo_fields, field_name, _FakeField)

    if not hasattr(odoo_fields, "Datetime"):
        odoo_fields.Datetime = _FakeDatetime  # type: ignore[attr-defined]

    # ── odoo.models ────────────────────────────────────────────────────────────

    if "odoo.models" not in sys.modules:
        odoo_models = types.ModuleType("odoo.models")
        sys.modules["odoo.models"] = odoo_models
    else:
        odoo_models = sys.modules["odoo.models"]

    for cls_name in ("AbstractModel", "Model"):
        if not hasattr(odoo_models, cls_name):
            setattr(odoo_models, cls_name, type(cls_name, (), {"_name": "", "_description": ""}))

    # ── odoo.http ─────────────────────────────────────────────────────────────

    if "odoo.http" not in sys.modules:
        odoo_http = types.ModuleType("odoo.http")
        sys.modules["odoo.http"] = odoo_http
    else:
        odoo_http = sys.modules["odoo.http"]

    if not hasattr(odoo_http, "route"):
        odoo_http.route = lambda *a, **kw: (lambda fn: fn)  # type: ignore[attr-defined]
    if not hasattr(odoo_http, "Controller"):
        odoo_http.Controller = object  # type: ignore[attr-defined]
    if not hasattr(odoo_http, "request"):
        odoo_http.request = None  # type: ignore[attr-defined]
    if not hasattr(odoo_http, "Response"):
        odoo_http.Response = type("Response", (), {})  # type: ignore[attr-defined]

    # ── odoo (root module) ────────────────────────────────────────────────────

    if "odoo" not in sys.modules:
        odoo_mod = types.ModuleType("odoo")
        sys.modules["odoo"] = odoo_mod
    else:
        odoo_mod = sys.modules["odoo"]

    for attr, mod in [
        ("api", odoo_api), ("fields", odoo_fields), ("models", odoo_models),
        ("exceptions", odoo_exc), ("http", odoo_http),
    ]:
        if not hasattr(odoo_mod, attr):
            setattr(odoo_mod, attr, mod)


_install_full_odoo_stubs()
