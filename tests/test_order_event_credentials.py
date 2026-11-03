"""BUG-279: los emisores C16/R023 resuelven credenciales por la misma fuente que el enforcement."""
import importlib.util
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_MODELS = Path(__file__).resolve().parent.parent / "models"
_spec = importlib.util.spec_from_file_location(
    "amcp_order_event_credentials", _MODELS / "order_event_credentials.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _env(creds):
    snapshot = MagicMock()
    snapshot.sudo.return_value.get_credentials.return_value = creds
    return {"trusteed.enforcement.snapshot": snapshot}, snapshot


class OrderEventCredentialsTest(unittest.TestCase):
    def test_returns_credentials_when_configured(self):
        creds = {"configured": True, "apiBase": "https://api.trusteed.xyz", "merchantId": "m"}
        env, snap = _env(creds)
        self.assertIs(_mod.resolve_order_event_credentials(env, 7), creds)
        snap.sudo.return_value.get_credentials.assert_called_once_with(7)

    def test_none_when_not_configured(self):
        env, _ = _env({"configured": False, "apiBase": "https://api.trusteed.xyz"})
        self.assertIsNone(_mod.resolve_order_event_credentials(env, 7))

    def test_emitters_use_helper_and_no_dead_names(self):
        for name in ("stock_picking_fulfillment.py", "account_move_refund_proxy.py"):
            src = (_MODELS / name).read_text()
            self.assertIn("resolve_order_event_credentials", src)
            self.assertNotRegex(src, r'env\["enforcement\.company\.map"\]')
            for dead in ("trusteed.access_token", "trusteed.hmac_secret", "trusteed.installation_id"):
                self.assertNotIn(dead, src)


if __name__ == "__main__":
    unittest.main()
