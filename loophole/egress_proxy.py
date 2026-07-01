"""Localhost egress proxy — per-host network scoping for executors.

OS sandboxes cannot filter egress by hostname (Seatbelt scopes remotes to
``*`` or ``localhost``; bubblewrap's netns is all-or-nothing), so
``--executor-network <host>`` used to be recorded for the audit trail but
not enforced. This sidecar closes the gap:

- the jail's network is limited to **localhost only**,
- this proxy runs OUTSIDE the jail on ``127.0.0.1`` with the declared host
  allowlist, advertised to the jail via ``HTTP_PROXY``/``HTTPS_PROXY``,
- proxy-aware clients (curl, requests, httpx, provider SDKs) tunnel HTTPS
  through ``CONNECT``; anything else can't leave the box at all,
- a denied host gets ``403`` and fires ``on_deny`` so the run's audit trail
  shows exactly what the executor tried to reach.

Allowlist matching is by hostname: exact or subdomain suffix
(``api.anthropic.com`` allows itself and ``x.api.anthropic.com``, never
``evilapi.anthropic.com.attacker.net``).
"""

from __future__ import annotations

import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

_TUNNEL_CHUNK = 65536
_IDLE_TIMEOUT = 300.0     # drop a silent tunnel after 5 minutes


class EgressProxy:
    """A host-allowlisted forward proxy bound to an ephemeral localhost port."""

    def __init__(self, allowed_hosts: Iterable[str],
                 on_deny: Optional[Callable[[str], None]] = None):
        self.allowed = tuple(h.strip().lower().lstrip(".")
                             for h in allowed_hosts if h and h.strip())
        self.on_deny = on_deny
        self.port: Optional[int] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def allows(self, host: str) -> bool:
        host = (host or "").strip().lower().rstrip(".")
        return any(host == a or host.endswith("." + a) for a in self.allowed)

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> int:
        proxy = self

        class _Handler(_ProxyHandler):
            _proxy = proxy

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="loophole-egress-proxy",
            daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def url(self) -> str:
        return "http://127.0.0.1:{}".format(self.port)

    def env(self) -> dict:
        """Proxy env vars for the jailed process (both case conventions)."""
        u = self.url
        return {"HTTP_PROXY": u, "HTTPS_PROXY": u,
                "http_proxy": u, "https_proxy": u, "NO_PROXY": "", "no_proxy": ""}


class _ProxyHandler(BaseHTTPRequestHandler):
    _proxy: EgressProxy          # injected per-instance class in start()
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:   # quiet; denials go to on_deny
        pass

    def _deny(self, host: str) -> None:
        if self._proxy.on_deny is not None:
            try:
                self._proxy.on_deny(host)
            except Exception:
                pass
        self.send_error(403, "egress to {} not in the allowlist".format(host))

    # ---- HTTPS: CONNECT tunnel ----------------------------------------------
    def do_CONNECT(self) -> None:
        host, _, port = self.path.rpartition(":")
        if not self._proxy.allows(host):
            self._deny(host)
            return
        try:
            upstream = socket.create_connection((host, int(port or 443)), timeout=30)
        except OSError as e:
            self.send_error(502, "connect to {} failed: {}".format(host, e))
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self._pump(self.connection, upstream)

    def _pump(self, a: socket.socket, b: socket.socket) -> None:
        try:
            while True:
                r, _, _ = select.select([a, b], [], [], _IDLE_TIMEOUT)
                if not r:
                    return                     # idle timeout
                for src in r:
                    data = src.recv(_TUNNEL_CHUNK)
                    if not data:
                        return                 # one side closed
                    (b if src is a else a).sendall(data)
        except OSError:
            return
        finally:
            try:
                b.close()
            except OSError:
                pass

    # ---- plain HTTP: absolute-URI forward (best-effort) ----------------------
    def _forward(self) -> None:
        u = urlsplit(self.path)
        if not u.hostname:
            self.send_error(400, "proxy requires an absolute URI")
            return
        if not self._proxy.allows(u.hostname):
            self._deny(u.hostname)
            return
        try:
            upstream = socket.create_connection(
                (u.hostname, u.port or 80), timeout=30)
        except OSError as e:
            self.send_error(502, "connect to {} failed: {}".format(u.hostname, e))
            return
        try:
            path = (u.path or "/") + (("?" + u.query) if u.query else "")
            lines = ["{} {} HTTP/1.1".format(self.command, path)]
            for k, v in self.headers.items():
                if k.lower() not in ("proxy-connection", "connection"):
                    lines.append("{}: {}".format(k, v))
            lines.append("Connection: close")
            upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                upstream.sendall(self.rfile.read(length))
            while True:
                data = upstream.recv(_TUNNEL_CHUNK)
                if not data:
                    break
                self.wfile.write(data)
            self.close_connection = True
        except OSError:
            pass
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _forward
