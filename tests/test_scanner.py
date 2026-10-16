"""Worker D scanner: incremental cursor scan, dry-run default, no snapshot writes."""

import os
import sqlite3

import pytest

from hermes_bot import scanner as sc
from hermes_bot import store as st


@pytest.fixture()
def store_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    c = st.connect()
    st.init_db(c)
    yield c
    c.close()


def _snapshot_mtime():
    return os.stat(sc.SNAPSHOT_PATH).st_mtime_ns


def test_snapshot_opened_read_only(store_conn):
    before = _snapshot_mtime()
    conn = sc.open_snapshot()
    conn.execute("SELECT count(*) FROM messages").fetchone()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE probe_x (id INTEGER)")
    conn.close()
    assert os.stat(sc.SNAPSHOT_PATH).st_mtime_ns == before


def test_refuses_non_snapshot_path():
    with pytest.raises(ValueError):
        sc.open_snapshot("/Users/sameerhoda/Projects/wa-wunderlist/v4/bridge/store/x.db")


def test_cursor_advance_consumes_only_new(store_conn):
    first = sc.scan(store_conn, limit_chats=20)
    assert first["messages"] > 0
    chats = [c["jid"] for c in
             sc.list_recent_chats(sc.open_snapshot(), 20)
             if not st.is_muted(store_conn, c["jid"])]
    for jid in chats:
        if jid in first["per_chat"]:
            assert st.get_cursor(store_conn, jid) != ""
    second = sc.scan(store_conn, limit_chats=20)
    assert second["messages"] == 0
    assert second["items"] == 0


def test_dry_run_default_sends_nothing(store_conn):
    sent = []
    # fresh cursor state in a new db so there is work to do
    summary = sc.scan(store_conn, limit_chats=5, send_fn=lambda j, i: sent.append((j, i)))
    assert summary["messages"] > 0
    assert sent == []
    import inspect
    assert inspect.signature(sc.scan).parameters["dry_run"].default is True


def test_muted_chat_skipped(store_conn):
    chats = sc.list_recent_chats(sc.open_snapshot(), 3)
    assert chats
    st.set_muted(store_conn, chats[0]["jid"], True)
    summary = sc.scan(store_conn, limit_chats=3)
    assert chats[0]["name"] not in summary["per_chat"]
    assert st.get_cursor(store_conn, chats[0]["jid"]) == ""


def test_llm_called_only_for_gated_threads(store_conn):
    calls = []

    def fake_llm(thread):
        calls.append(thread)
        return {"items": []}

    summary = sc.scan(store_conn, limit_chats=20, llm_fn=fake_llm)
    assert summary["llm_calls"] == len(calls)
    assert summary["llm_calls"] <= 20
    # every LLM-routed thread carried a real signal
    from hermes_bot import extract as ex
    for t in calls:
        assert ex.worth_llm(t)


ARCH_A = "11111-111@g.us"
ARCH_B = "22222-222@g.us"


def _snapshot_pair(tmp_path, archived=()):
    msg = tmp_path / "messages.db"
    c = sqlite3.connect(str(msg))
    c.execute("CREATE TABLE chats (jid TEXT, name TEXT, last_message_time TEXT)")
    c.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT,"
              " content TEXT, timestamp TEXT, is_from_me INTEGER)")
    c.executemany("INSERT INTO chats VALUES (?,?,?)", [
        (ARCH_A, "Active group", "2026-10-15T10:00:00"),
        (ARCH_B, "Old archived", "2026-10-15T09:00:00"),
    ])
    c.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?)", [
        ("m1", ARCH_A, "Rahul", "please send the SOW today?",
         "2026-10-15T10:00:00", 0),
        ("m2", ARCH_B, "Spammer", "promo invite!!!",
         "2026-10-15T09:00:00", 0),
    ])
    c.commit()
    c.close()
    wa = tmp_path / "whatsapp.db"
    w = sqlite3.connect(str(wa))
    w.execute("CREATE TABLE whatsmeow_chat_settings (our_jid TEXT, chat_jid TEXT,"
              " muted_until INTEGER, pinned INTEGER, archived INTEGER)")
    for jid in archived:
        w.execute("INSERT INTO whatsmeow_chat_settings VALUES (NULL,?,NULL,NULL,1)",
                  (jid,))
    w.commit()
    w.close()
    return str(msg)


def test_archived_chats_excluded_from_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SNAPSHOT_PATH", _snapshot_pair(tmp_path, (ARCH_B,)))
    snap = sc.open_snapshot()
    try:
        assert sc.archived_jids(snap) == {ARCH_B}
        jids = [c["jid"] for c in sc.list_recent_chats(snap, 10)]
        assert jids == [ARCH_A]
    finally:
        snap.close()


def test_missing_settings_db_means_no_exclusion(tmp_path, monkeypatch):
    msg = tmp_path / "messages.db"
    c = sqlite3.connect(str(msg))
    c.execute("CREATE TABLE chats (jid TEXT, name TEXT, last_message_time TEXT)")
    c.execute("INSERT INTO chats VALUES (?,?,?)",
              (ARCH_A, "Active group", "2026-10-15T10:00:00"))
    c.commit()
    c.close()
    monkeypatch.setattr(sc, "SNAPSHOT_PATH", str(msg))
    snap = sc.open_snapshot()
    try:
        assert sc.archived_jids(snap) == set()
        assert [c["jid"] for c in sc.list_recent_chats(snap, 10)] == [ARCH_A]
    finally:
        snap.close()


def test_scan_skips_archived_chats(store_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SNAPSHOT_PATH", _snapshot_pair(tmp_path, (ARCH_B,)))
    summary = sc.scan(store_conn, limit_chats=10)
    assert ARCH_B not in summary["per_chat"]
    assert st.get_cursor(store_conn, ARCH_B) == ""
    assert st.get_cursor(store_conn, ARCH_A) != ""
