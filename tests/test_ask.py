"""Worker E: self-chat Q&A over items + recent messages."""

import sqlite3

import pytest

from hermes_bot import ask
from hermes_bot import store as st


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    c = st.connect()
    st.init_db(c)
    yield c
    c.close()


def _item(chat, what, **kw):
    d = {"chat_jid": chat, "what": what, "kind": "ask", "status": "open",
         "owner": "", "requester": "", "context": "", "due": "", "topic": "general"}
    d.update(kw)
    return d


def test_answer_finds_matching_item(conn):
    st.upsert_item(conn, _item("c@g.us", "share the release numbers", owner="Sam"))
    st.upsert_item(conn, _item("d@g.us", "book cricket turf", owner="Sam"))
    out = ask.answer_question(conn, "what about the release numbers?")
    assert "release numbers" in out
    assert "cricket" not in out


def test_answer_empty_when_no_match(conn):
    st.upsert_item(conn, _item("c@g.us", "book cricket turf", owner="Sam"))
    assert "Nothing found" in ask.answer_question(conn, "quantum export tariffs")


def test_answer_needs_more_detail(conn):
    assert "more" in ask.answer_question(conn, "???").lower()


def test_answer_uses_snapshot_messages(conn):
    snap = sqlite3.connect(":memory:")
    snap.row_factory = sqlite3.Row
    snap.execute("CREATE TABLE messages (chat_jid TEXT, sender TEXT, content TEXT)")
    snap.execute("INSERT INTO messages VALUES ('g@g.us','Ravi','release numbers look great today')")
    out = ask.answer_question(conn, "release numbers update?", snap_conn=snap)
    assert "release numbers" in out
    snap.close()
