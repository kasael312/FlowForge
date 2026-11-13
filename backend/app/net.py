"""Outbound-request guard (SSRF): only public addresses, re-checked on every redirect.

The backend makes HTTP calls on behalf of users (``http_request`` nodes, web tools,
``webhook_call``, model ``base_url``, MCP URLs). Each target hostname is resolved
and every resulting address must be public; loopback, private, link-local,
multicast and reserved ranges (cloud metadata, the Docker network, the host) are
refused. ``guarded_client`` installs the same check as an httpx request hook so
redirects cannot escape it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

from app.config import get_settings

LOCAL_NAMES = {"localhost", "localhost.localdomain", "0.0.0.0", "::", "::1"}

#: Tests replace DNS with a function ``host -> [ip, ...]``.
resolver_override: Callable[[str], list[str]] | None = None


class UnsafeUrl(ValueError):
    """The URL points at a local/private address or is malformed."""


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _trusted(url: str) -> bool:
    """Operator-approved internal endpoints (e.g. a local Ollama), prefix match."""
    for prefix in get_settings().trusted_internal_urls_list:
        if url.lower().startswith(prefix.lower()):
            return True
    return False


def _resolve_sync(host: str) -> list[str]:
    if resolver_override is not None:
        return resolver_override(host)
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeUrl(f"cannot resolve {host}") from exc
    return [i[4][0] for i in infos]


def _check_host_ips(host: str, ips: list[str]) -> None:
    if not ips:
        raise UnsafeUrl(f"cannot resolve {host}")
    for ip in ips:
        if not is_public_ip(ip):
            raise UnsafeUrl(f"{host} resolves to a non-public address; blocked")


def _parse(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc or not u.hostname:
        raise UnsafeUrl("url must be http(s) with a host")
    host = u.hostname.lower().strip("[]")
    if host in LOCAL_NAMES or host.endswith((".localhost", ".internal")):
        raise UnsafeUrl("local/internal addresses are not allowed")
    return host


def _literal_ip(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def check_public_url_sync(url: str) -> str:
    """Validate ``url`` (blocking DNS). Returns the stripped URL or raises ``UnsafeUrl``."""
    url = url.strip()
    if _trusted(url):
        return url
    host = _parse(url)
    ip = _literal_ip(host)
    _check_host_ips(host, [ip] if ip else _resolve_sync(host))
    return url


async def check_public_url(url: str) -> str:
    """Async variant for request paths (DNS in a worker thread)."""
    url = url.strip()
    if _trusted(url):
        return url
    host = _parse(url)
    ip = _literal_ip(host)
    if ip:
        _check_host_ips(host, [ip])
        return url
    if resolver_override is not None:
        _check_host_ips(host, resolver_override(host))
        return url
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), timeout=10
        )
    except (TimeoutError, OSError) as exc:
        raise UnsafeUrl(f"cannot resolve {host}") from exc
    _check_host_ips(host, [i[4][0] for i in infos])
    return url


async def _guard_request(request: httpx.Request) -> None:
    try:
        await check_public_url(str(request.url))
    except UnsafeUrl as exc:
        raise httpx.RequestError(f"blocked: {exc}", request=request) from exc


def guarded_client(**kwargs: object) -> httpx.AsyncClient:
    """``httpx.AsyncClient`` that refuses non-public targets, including after redirects."""
    hooks = dict(kwargs.pop("event_hooks", {}) or {})  # type: ignore[arg-type]
    hooks["request"] = [*hooks.get("request", []), _guard_request]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)  # type: ignore[arg-type]
