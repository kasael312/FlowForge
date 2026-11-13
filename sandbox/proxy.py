"""Tiny egress proxy for the ``allowlist`` sandbox network mode.

Runs as a sidecar next to the sandbox container. The sandbox sits on an
``--internal`` Docker network (no route out); this proxy is attached to both
that network and ``bridge`` and forwards only what the allowlist permits:

- ``CONNECT host:port`` (HTTPS) and absolute-form plain HTTP requests;
- host must match ``FF_ALLOWED_HOSTS`` (``example.com`` or ``*.example.com``);
- the name is resolved *here* and loopback / private / link-local / multicast
  addresses are refused, so an allowlisted name cannot be pointed at the host,
  the Docker network or cloud metadata (DNS rebinding);
- only ports 80, 443, 8080 and 8443;
- when ``FF_PROXY_TOKEN`` is set, every request must carry
  ``Proxy-Authorization: Basic base64("ff:<token>")`` (the backend puts the
  token into the sandbox's ``HTTP_PROXY`` URL), so other containers on the
  bridge cannot borrow this proxy.

Only the standard library is used so the sandbox image needs nothing extra.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import socket
from urllib.parse import urlsplit

PORT = int(os.environ.get("FF_PROXY_PORT", "3128"))
ALLOWED = [
    h.strip().lower() for h in os.environ.get("FF_ALLOWED_HOSTS", "").split(",") if h.strip()
]
TOKEN = os.environ.get("FF_PROXY_TOKEN", "")
ALLOWED_PORTS = {80, 443, 8080, 8443}
#: Tests only: let the proxy reach private addresses (never set in production).
ALLOW_PRIVATE = os.environ.get("FF_PROXY_ALLOW_PRIVATE") == "1"
MAX_HEADER = 64 * 1024


def host_allowed(host: str, allowed: list[str] | None = None) -> bool:
    """Exact match, or wildcard ``*.domain`` which also matches ``domain`` itself."""
    host = host.lower().strip(".")
    for pat in ALLOWED if allowed is None else allowed:
        if pat == "*":
            return True
        if pat.startswith("*."):
            base = pat[2:]
            if host == base or host.endswith("." + base):
                return True
        elif host == pat:
            return True
    return False


def address_allowed(ip: str) -> bool:
    """Public unicast only: no loopback, private, link-local, multicast or reserved ranges."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if ALLOW_PRIVATE:
        return True
    return not (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def authorized(headers: bytes, token: str | None = None) -> bool:
    """``Proxy-Authorization: Basic base64("ff:<token>")`` when a token is configured."""
    token = TOKEN if token is None else token
    if not token:
        return True
    for line in headers.split(b"\r\n"):
        if line.lower().startswith(b"proxy-authorization:"):
            value = line.split(b":", 1)[1].strip()
            if value.lower().startswith(b"basic "):
                try:
                    creds = base64.b64decode(value[6:].strip()).decode("utf-8", "replace")
                except ValueError:
                    return False
                return creds == f"ff:{token}"
    return False


async def resolve(host: str, port: int) -> str | None:
    """First public IP ``host`` resolves to, or None when it is unresolvable / not public."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=10
        )
    except (OSError, asyncio.TimeoutError):
        return None
    for info in infos:
        ip = info[4][0]
        if address_allowed(ip):
            return ip
    return None


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def reply(writer: asyncio.StreamWriter, status: str, body: str = "") -> None:
    data = body.encode()
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\nContent-Length: {len(data)}\r\n"
        "Connection: close\r\n\r\n".encode()
        + data
    )
    await writer.drain()
    writer.close()


async def connect_upstream(host: str, port: int, writer: asyncio.StreamWriter):
    """Validate port + resolved address, then open the upstream connection (or reply with an error)."""
    if port not in ALLOWED_PORTS and not ALLOW_PRIVATE:
        await reply(writer, "403 Forbidden", f"port {port} is not allowed\n")
        return None
    ip = await resolve(host, port)
    if ip is None:
        await reply(writer, "403 Forbidden", f"{host} does not resolve to a public address\n")
        return None
    try:
        return await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=20)
    except (OSError, asyncio.TimeoutError):
        await reply(writer, "502 Bad Gateway", f"cannot reach {host}:{port}\n")
        return None


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
        writer.close()
        return
    if len(head) > MAX_HEADER:
        await reply(writer, "431 Request Header Fields Too Large")
        return
    request_line, _, rest = head.partition(b"\r\n")
    try:
        method, target, version = request_line.decode("latin-1").split(" ", 2)
    except ValueError:
        await reply(writer, "400 Bad Request")
        return
    if not authorized(rest):
        writer.write(
            b"HTTP/1.1 407 Proxy Authentication Required\r\n"
            b'Proxy-Authenticate: Basic realm="flowforge"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
        )
        await writer.drain()
        writer.close()
        return

    if method.upper() == "CONNECT":
        host, _, port_s = target.rpartition(":")
        try:
            port = int(port_s or 443)
        except ValueError:
            await reply(writer, "400 Bad Request", "bad port\n")
            return
        if not host or not host_allowed(host):
            await reply(writer, "403 Forbidden", f"host {host or '?'} is not in the allowlist\n")
            return
        upstream = await connect_upstream(host, port, writer)
        if upstream is None:
            return
        r2, w2 = upstream
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(pipe(reader, w2), pipe(r2, writer))
        return

    # plain HTTP: absolute-form request line
    try:
        parts = urlsplit(target)
        host = parts.hostname or ""
        port = parts.port or 80
    except ValueError:
        await reply(writer, "400 Bad Request", "bad target\n")
        return
    if not host or not host_allowed(host):
        await reply(writer, "403 Forbidden", f"host {host or '?'} is not in the allowlist\n")
        return
    upstream = await connect_upstream(host, port, writer)
    if upstream is None:
        return
    r2, w2 = upstream
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    headers = [h for h in rest.split(b"\r\n") if h and not h.lower().startswith(b"proxy-")]
    w2.write(f"{method} {path} {version}\r\n".encode() + b"\r\n".join(headers) + b"\r\n\r\n")
    await w2.drain()
    await asyncio.gather(pipe(reader, w2), pipe(r2, writer))


async def main() -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", PORT, limit=MAX_HEADER)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
