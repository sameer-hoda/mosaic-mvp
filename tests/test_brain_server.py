"""Fix 4: the brain event hook is served (in-process by main)."""

import json
import urllib.request

from hermes_bot import main as m


def _post(port, path, payload):
    import urllib.error
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def test_brain_server_serves_event_and_health(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAIN_PORT", "0")
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    server = m._start_brain_server()
    assert server is not None
    try:
        port = server.server_address[1]
        assert port != 0
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=5) as resp:
            assert resp.status == 200
        status, body = _post(port, "/event", {"chat_jid": "x", "sender": "y",
                                              "content": "pulse"})
        assert status == 200 and body.get("ok") is True
        status, _ = _post(port, "/nope", {})
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_brain_server_skips_owned_port(monkeypatch):
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        monkeypatch.setenv("BRAIN_PORT", str(sock.getsockname()[1]))
        assert m._start_brain_server() is None
    finally:
        sock.close()
