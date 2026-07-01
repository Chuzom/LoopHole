"""Egress proxy: allowlisted hosts tunnel, everything else is 403 + audited."""

import socket
import threading

from loophole.egress_proxy import EgressProxy


def test_allows_exact_and_subdomain_only():
    p = EgressProxy(["api.anthropic.com", "Localhost"])
    assert p.allows("api.anthropic.com")
    assert p.allows("API.ANTHROPIC.COM.")           # case + trailing dot
    assert p.allows("shard7.api.anthropic.com")     # subdomain
    assert p.allows("localhost")
    assert not p.allows("anthropic.com")            # parent domain is NOT implied
    assert not p.allows("evilapi.anthropic.com.attacker.net")
    assert not p.allows("notapi.anthropic.com.evil.io")
    assert not p.allows("")


def _echo_server():
    """A local TCP server that echoes one payload back, prefixed."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        data = conn.recv(1024)
        conn.sendall(b"echo:" + data)
        conn.close()

    threading.Thread(target=run, daemon=True).start()
    return srv, srv.getsockname()[1]


def _connect(proxy_port, target):
    s = socket.create_connection(("127.0.0.1", proxy_port), timeout=10)
    s.sendall("CONNECT {} HTTP/1.1\r\n\r\n".format(target).encode())
    status = b""
    while b"\r\n\r\n" not in status:
        chunk = s.recv(1024)
        if not chunk:
            break
        status += chunk
    return s, status.decode(errors="replace")


def test_connect_tunnel_to_allowed_host():
    srv, echo_port = _echo_server()
    proxy = EgressProxy(["localhost"])
    proxy.start()
    try:
        s, status = _connect(proxy.port, "localhost:{}".format(echo_port))
        assert " 200 " in status.splitlines()[0]
        s.sendall(b"ping")
        assert s.recv(1024) == b"echo:ping"
        s.close()
    finally:
        proxy.stop()
        srv.close()


def test_connect_to_denied_host_is_403_and_audited():
    denied = []
    proxy = EgressProxy(["api.anthropic.com"], on_deny=denied.append)
    proxy.start()
    try:
        s, status = _connect(proxy.port, "exfil.attacker.net:443")
        assert " 403 " in status.splitlines()[0]
        assert denied == ["exfil.attacker.net"]
        s.close()
    finally:
        proxy.stop()


def test_proxy_env_covers_both_conventions():
    proxy = EgressProxy(["localhost"])
    proxy.start()
    try:
        env = proxy.env()
        assert env["HTTP_PROXY"] == env["https_proxy"] == proxy.url
        assert env["NO_PROXY"] == ""
    finally:
        proxy.stop()
