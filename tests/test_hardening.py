"""Worker F hardening: retention prune, unlink wipe, disconnect watch,
SELF_JID single source of truth, bridge DELETE /api/delete contract."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from hermes_bot import hardening as hd
from hermes_bot import store as st


def _conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    c = st.connect()
    st.init_db(c)
    yield c
    c.close()


def test_prune_drops_rows_older_than_30_days(tmp_path, monkeypatch):
    gen = _conn(tmp_path, monkeypatch)
    conn = next(gen)
    now = 3_000_000.0
    old = now - 31 * 86400
    conn.execute("INSERT INTO event_log (ts, kind) VALUES (?, 'x')", (old,))
    conn.execute("INSERT INTO event_log (ts, kind) VALUES (?, 'x')", (now,))
    conn.execute("INSERT INTO drafts (chat_jid, text, created_at, sent_at)"
                 " VALUES ('a', 't', ?, ?)", (old, old))
    conn.execute("INSERT INTO drafts (chat_jid, text, created_at)"
                 " VALUES ('a', 'fresh', ?)", (now,))
    conn.execute("INSERT INTO item_alias (brief_no, alias, item_id, created_at)"
                 " VALUES (1, '1', 'x', ?)", (old,))
    conn.commit()
    got = hd.prune_retention(conn, days=30, now=now)
    assert got == {"event_log": 1, "drafts": 1, "item_alias": 1}
    assert conn.execute("SELECT COUNT(*) c FROM event_log").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM drafts").fetchone()["c"] == 1
    next(gen, None)


def test_unlink_wipe_removes_store_files(tmp_path):
    data = tmp_path / "store"
    data.mkdir()
    for name in ("whatsapp.db", "messages.db", "hermes.db", "mosaic.db",
                 "session.json", "pending_messages.json", "setup.json",
                 "config.json"):
        (data / name).write_text("{}")
    removed = hd.wipe_data_dir(str(data), keep_key=True)
    assert "mosaic.db" in removed and "messages.db" in removed
    assert "config.json" not in removed  # key kept
    assert (data / "config.json").exists()
    assert not (data / "messages.db").exists()


def test_disconnect_watch_notifies_once_per_transition():
    notices = []
    states = [True]
    watch = hd.BridgeWatch(probe=lambda: states[0],
                           notify=lambda text: notices.append(text))
    assert watch.tick() == "up"
    states[0] = False
    assert watch.tick() == "down-notice-sent"
    assert watch.tick() == "down"  # no repeat notice
    assert len(notices) == 1 and "offline" in notices[0].lower()
    states[0] = True
    assert watch.tick() == "recovered-notice-sent"
    assert "online" in notices[1].lower()


def test_self_jid_ignores_env_fallback(monkeypatch):
    from hermes_bot import slash
    monkeypatch.setattr("hermes_bot.db.get_mechat_chat_jid",
                        lambda: "resolved@s.whatsapp.net")
    monkeypatch.setenv("MOSAIC_SELF_JID", "stale-env@s.whatsapp.net")
    assert slash.resolve_self_jid() == "resolved@s.whatsapp.net"
    assert "MOSAIC_SELF_JID" not in slash.__doc__


def test_deleter_uses_delete_method_with_token(monkeypatch):
    from hermes_bot import slash as sl
    seen = {}

    class H(BaseHTTPRequestHandler):
        def _ok(self):
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_DELETE = lambda self: (seen.update(
            {"method": "DELETE", "token": self.headers.get("X-Bridge-Token"),
             "path": self.path}), self._ok())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(sl.config, "BRIDGE_URL",
                        f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("MOSAIC_BRIDGE_TOKEN", "fake-token")
    sl.default_deleter("c@s.whatsapp.net", "MSG1")
    srv.shutdown()
    assert seen == {"method": "DELETE", "token": "fake-token",
                    "path": "/api/delete"}
