"""Fix 9: immediate first tick writes a heartbeat; watcher renders."""

from hermes_bot import main as m
from hermes_bot import store as st


def test_run_cycle_once_writes_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MOSAIC_LIVE", raising=False)
    report = m._run_cycle_once()
    assert report is not None and report["brief_no"] >= 1
    conn = st.connect()
    try:
        rows = conn.execute(
            "SELECT detail FROM event_log WHERE kind = 'cycle'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1 and "brief=" in rows[0]["detail"]


def test_watch_renders_without_live_data(tmp_path):
    import sys
    sys.path.insert(0, "scripts")
    import watch_mosaic as w
    data = w.collect(str(tmp_path / "mosaic.db"),
                     str(tmp_path / "messages.db"))
    text = w.render({**data, "outbox": 0})
    assert "mosaic watch" in text and "no heartbeat yet" in text


def test_watch_snapshot_names_items(tmp_path):
    import sqlite3
    import sys
    sys.path.insert(0, "scripts")
    import watch_mosaic as w
    from hermes_bot import scanner as sc
    msg = tmp_path / "messages.db"
    c = sqlite3.connect(str(msg))
    c.execute("CREATE TABLE chats (jid TEXT, name TEXT, last_message_time TEXT)")
    c.execute("INSERT INTO chats VALUES (?,?,?)",
              ("1@g.us", "Named Chat", "2026-10-16T10:00:00"))
    try:
        c.execute("CREATE TABLE whatsmeow_contacts (jid TEXT)")
        c.execute("INSERT INTO whatsmeow_contacts VALUES (?)", ("a",))
    except Exception:
        pass
    c.commit()
    c.close()
    mdb = tmp_path / "mosaic.db"
    d = sqlite3.connect(str(mdb))
    d.execute("CREATE TABLE items (what TEXT, chat_jid TEXT, kind TEXT,"
              " status TEXT, updated_at REAL)")
    d.execute("CREATE TABLE event_log (ts REAL, kind TEXT, model TEXT,"
              " input_tokens INTEGER, output_tokens INTEGER, detail TEXT)")
    d.execute("INSERT INTO items VALUES (?,?,?,?,?)",
              ("Do the thing", "1@g.us", "ask", "open", 1.0))
    d.commit()
    d.close()
    text = w.render({**w.collect(str(mdb), str(msg)), "outbox": 0})
    assert "Named Chat" in text and "Do the thing" in text
