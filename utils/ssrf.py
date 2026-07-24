"""Shared SSRF prevention helpers for the trusteed addon.

Used by controllers/main.py and models/account_move_jws.py to validate
api_base URLs before any outbound HTTP request.
"""

import ipaddress
import urllib.parse

# Explicit RFC-1918 + loopback + link-local + private IPv6 blocklist.
# Used as fallback when is_global() fails (e.g. IPv4-mapped IPv6 edge cases).
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # AWS IMDSv1
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("0.0.0.0/8"),         # CGN / this-network
    ipaddress.ip_network("100.64.0.0/10"),      # Carrier-grade NAT
    ipaddress.ip_network("192.0.0.0/24"),       # IETF Protocol Assignments
    ipaddress.ip_network("198.51.100.0/24"),    # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),     # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),        # Multicast
    ipaddress.ip_network("240.0.0.0/4"),        # Reserved
]


def validate_api_base(url: str) -> bool:
    """Return True only if url is safe to use as external API base.

    Enforces HTTPS and blocks:
    - non-HTTPS schemes (http, file, ftp, ...)
    - localhost / loopback by name
    - RFC-1918, link-local, CGNAT, reserved, multicast ranges (IPv4)
    - private IPv6 ranges
    - IP addresses that are not globally routable (via is_global())
    - empty hostname

    Note: hostname-based DNS rebinding (a hostname that later resolves to a
    private IP at request time) is NOT fully mitigated here. For production
    hardening, restrict outbound traffic at the network/egress layer.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False

    hostname = (parsed.hostname or "").lower()

    if parsed.scheme != "https":
        return False

    if not hostname:
        return False

    if hostname in ("localhost", "localhost.localdomain"):
        return False

    try:
        addr = ipaddress.ip_address(hostname)
        # is_global() covers all non-routable ranges more completely than a manual list.
        if not addr.is_global:
            return False
        # Belt-and-suspenders: also check explicit blocklist (handles edge cases
        # like IPv4-mapped IPv6 where is_global() can return True inconsistently).
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                return False
    except ValueError:
        # S042-004: Hostname — restrict to known Trusteed domains to mitigate DNS
        # rebinding (a hostname that resolves to a private IP at request time).
        if not (hostname == "api.trusteed.xyz" or hostname.endswith(".trusteed.xyz")):
            return False

    return True
