"""Fix 1: synthetic snapshot is built when missing, never clobbers data."""

import sqlite3

from ensure_test_snapshot import ensure_test_snapshot


def test_builds_snapshot_when_missing(tmp_path):
    target = str(tmp_path / "messages.db")
    assert ensure_test_snapshot(target) == target
    c = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        n_chats = c.execute("SELECT count(*) FROM chats").fetchone()[0]
        n_msgs = c.execute("SELECT count(*) FROM messages").fetchone()[0]
    finally:
        c.close()
    assert n_chats == 22
    assert n_msgs > 0


def test_never_overwrites_snapshot_with_messages(tmp_path):
    target = str(tmp_path / "messages.db")
    c = sqlite3.connect(target)
    c.execute("CREATE TABLE chats (jid TEXT, name TEXT, last_message_time TEXT)")
    c.execute("CREATE TABLE messages (id TEXT)")
    c.execute("INSERT INTO messages VALUES ('keepme')")
    c.commit()
    c.close()
    before = open(target, "rb").read()
    assert ensure_test_snapshot(target) == target
    assert open(target, "rb").read() == before
