"""TEAH-091: Odoo SaaS environment detection.

Three independent signals — any one positive → SaaS detected:

  1. Hostname check: `web.base.url` sysparam contains `*.odoo.com`.
  2. Filesystem sandbox: attempt to create `/tmp/.trusteed_test`; failure
     (PermissionError / OSError) indicates a read-only/containerised SaaS FS.
  3. Sysparam `database.show_url` resolves to an `*.odoo.com` URL.

Escape hatch: env var `_AMCP_FORCE_SAAS=1` makes this always return True
(used by automated tests — TEAH-093).
"""

import os
import re
import logging

_logger = logging.getLogger(__name__)

_ODOO_COM_PATTERN = re.compile(r"(^|\.)odoo\.com(/|$)", re.IGNORECASE)


class SaasDetectionResult:
    __slots__ = ("is_saas", "reason")

    def __init__(self, is_saas: bool, reason: str) -> None:
        self.is_saas = is_saas
        self.reason = reason

    def __bool__(self) -> bool:
        return self.is_saas

    def __repr__(self) -> str:  # pragma: no cover
        return f"SaasDetectionResult(is_saas={self.is_saas!r}, reason={self.reason!r})"


def _check_hostname(env) -> SaasDetectionResult:
    """Signal 1: `web.base.url` contains *.odoo.com."""
    try:
        ICP = env["ir.config_parameter"].sudo()
        base_url: str = ICP.get_param("web.base.url", "") or ""
        if _ODOO_COM_PATTERN.search(base_url):
            return SaasDetectionResult(True, f"hostname_match:{base_url}")
    except Exception as exc:  # pragma: no cover  # defensive
        _logger.debug("TEAH-091 hostname check failed: %s", exc)
    return SaasDetectionResult(False, "hostname_ok")


def _check_filesystem() -> SaasDetectionResult:
    """Signal 2: attempt to write a temp file under /tmp.

    On Odoo SaaS the worker process runs in a read-only overlay FS;
    writes to /tmp raise PermissionError or OSError.
    """
    probe_path = "/tmp/.trusteed_test"
    try:
        with open(probe_path, "w") as fh:
            fh.write("probe")
        os.remove(probe_path)
        return SaasDetectionResult(False, "fs_writable")
    except (PermissionError, OSError):
        return SaasDetectionResult(True, "fs_readonly")


def _check_show_url(env) -> SaasDetectionResult:
    """Signal 3: `database.show_url` sysparam resolves to *.odoo.com."""
    try:
        ICP = env["ir.config_parameter"].sudo()
        show_url: str = ICP.get_param("database.show_url", "") or ""
        if show_url and _ODOO_COM_PATTERN.search(show_url):
            return SaasDetectionResult(True, f"show_url_match:{show_url}")
    except Exception as exc:  # pragma: no cover  # defensive
        _logger.debug("TEAH-091 show_url check failed: %s", exc)
    return SaasDetectionResult(False, "show_url_ok")


def is_odoo_saas(env=None) -> SaasDetectionResult:
    """Return a SaasDetectionResult — truthy when running on Odoo SaaS.

    Pass `env` (Odoo Environment) to enable sysparam-based checks.
    When `env` is None only the filesystem probe is performed.

    Forced by env var `_AMCP_FORCE_SAAS=1` (tests — TEAH-093).
    """
    if os.environ.get("_AMCP_FORCE_SAAS") == "1":
        return SaasDetectionResult(True, "forced_by_env")

    if env is not None:
        result = _check_hostname(env)
        if result.is_saas:
            return result

    result = _check_filesystem()
    if result.is_saas:
        return result

    if env is not None:
        result = _check_show_url(env)
        if result.is_saas:
            return result

    return SaasDetectionResult(False, "all_checks_passed")
