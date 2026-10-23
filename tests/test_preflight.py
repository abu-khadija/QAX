"""core/preflight.py — an unreachable target is an error, never a clean-looking scan."""
import shutil
import socket
import ssl
import subprocess
import threading
import wsgiref.simple_server as ws

import requests

from core.preflight import check_reachable


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def test_a_dead_port_is_reported_not_scanned():
    msg = check_reachable(f"http://127.0.0.1:{_port()}")
    assert msg and "nothing is listening" in msg


def test_any_http_answer_counts_as_reachable():
    def app(environ, start_response):
        start_response("500 Internal Server Error", [("Content-Type", "text/plain")]); return [b"boom"]
    port = _port(); srv = ws.make_server("127.0.0.1", port, app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert check_reachable(f"http://127.0.0.1:{port}") is None          # even a 500: the app answered
    finally:
        srv.shutdown()


def test_bad_names_and_urls_get_a_plain_reason():
    assert "could not be resolved" in check_reachable("http://qax-no-such-host.invalid")
    assert "not a valid URL" in check_reachable("not a url")


def test_self_signed_https_is_refused_with_the_insecure_hint_and_allowed_with_it(tmp_path):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    if shutil.which("openssl") is None:                     # Windows has none by default
        import pytest; pytest.skip("openssl not available")
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
                        "-days", "2", "-subj", "/CN=localhost"], capture_output=True)
    if r.returncode != 0:
        import pytest; pytest.skip("openssl not available")
    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")]); return [b"ok"]
    port = _port(); srv = ws.make_server("127.0.0.1", port, app)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(str(crt), str(key))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"https://127.0.0.1:{port}"
        msg = check_reachable(url)
        assert msg and "--insecure" in msg and "ONLY for a target you control" in msg
        from core.session_auth import apply_extras
        assert check_reachable(url, session=apply_extras(requests.Session(), {"insecure": True})) is None
    finally:
        srv.shutdown()
