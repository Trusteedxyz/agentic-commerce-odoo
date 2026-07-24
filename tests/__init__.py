"""Test package for the Trusteed Odoo addon.

Two runners consume this package:

  1. Odoo's native test loader imports this module under a real Odoo runtime
     (where ``odoo.*`` is the real framework) and discovers each listed test
     module.
  2. pytest (pure-Python, no Odoo kernel) discovers the test files directly by
     path. Under ``--import-mode=importlib`` pytest also imports this package
     while resolving ``conftest.py``; the addon code pulled in by the eager
     imports below needs the ``odoo.*`` stubs to already exist, so we install
     them defensively here before importing any sibling test module.

Keeping every test module listed here (H1) guarantees the Odoo runner collects
all of them — not just a stale subset.
"""

# Ensure odoo.* stubs exist before the eager test-module imports below pull in
# addon code (models import `from odoo import ...` at module top-level). When
# running under real Odoo this import is a no-op because the kernel is present;
# under pytest it mirrors conftest's stub installation, made order-independent.
try:  # pragma: no cover - exercised by both runners
    import odoo.exceptions  # noqa: F401

    odoo.exceptions.ValidationError  # type: ignore[attr-defined]
except (ImportError, AttributeError):
    from . import conftest as _conftest  # noqa: F401

    _conftest._install_full_odoo_stubs()

from . import test_account_move_chatter  # noqa: E402,F401
from . import test_account_move_jws  # noqa: E402,F401
from . import test_ai_tool_invocation  # noqa: E402,F401
from . import test_bundled_catalog  # noqa: E402,F401
from . import test_cart_signals_r035_r036  # noqa: E402,F401
from . import test_cart_signals_sprint_e  # noqa: E402,F401
from . import test_dispatch_payment_invocations  # noqa: E402,F401
from . import test_enforcement  # noqa: E402,F401
from . import test_enforcement_layer2  # noqa: E402,F401
from . import test_enforcement_perf  # noqa: E402,F401
from . import test_jcs_golden_vectors  # noqa: E402,F401
from . import test_multi_company  # noqa: E402,F401
from . import test_nonce_consume  # noqa: E402,F401
from . import test_order_event_emission  # noqa: E402,F401
from . import test_permission_check  # noqa: E402,F401
from . import test_r022_payment_method  # noqa: E402,F401
from . import test_r043_hitl_gate  # noqa: E402,F401
from . import test_sign_trust_receipt_invocation  # noqa: E402,F401
from . import test_token_broker  # noqa: E402,F401
from . import test_tool_toggles  # noqa: E402,F401
