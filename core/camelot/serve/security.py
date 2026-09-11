"""Loopback trust guards: Host and Origin allowlists.

Binding to 127.0.0.1 keeps remote machines out, but the *user's own browser*
can still be tricked into reaching the daemon (open tab, DNS rebinding). A
strict Host allowlist stops rebinding, and rejecting foreign Origins stops
cross-site reads. CORS is deliberately not used as the control.
"""
from __future__ import annotations

from urllib.parse import urlsplit

DEFAULT_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


def _host_only(header: str) -> str:
    value = header.strip()
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.rsplit(":", 1)[0] if ":" in value else value


def host_allowed(host_header: str | None, extra: tuple[str, ...] = ()) -> bool:
    if not host_header:
        return False
    allowed = {h.lower() for h in (*DEFAULT_HOSTS, *extra)}
    return _host_only(host_header).lower() in allowed


def origin_allowed(origin: str | None, extra: tuple[str, ...] = ()) -> bool:
    if not origin or origin == "null":
        return False
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    return host_allowed(parts.netloc, extra)
