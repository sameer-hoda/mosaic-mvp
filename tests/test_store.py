"""Worker D store: mosaic.db tables, stable ids, alias expiry, cursors."""

import os
import time

import pytest

from hermes_bot import store as st


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    c = st.connect()
    st.init_db(c)
    yield c
    c.close()


def test_db_path_uses_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    assert st.db_path() == tmp_path / "mosaic.db"


def test_db_path_defaults_to_data(monkeypatch):
    monkeypatch.delenv("MOSAIC_DATA_DIR", raising=False)
    assert st.db_path() == st.Path("/data") / "mosaic.db"


def test_stable_id_across_briefs(conn):
    a = st.upsert_item(conn, {"chat_jid": "g@g.us", "kind": "ask",
                              "what": "Share the numbers by EOD",
                              "owner": "Dev"}, now=1000.0)
    b = st.upsert_item(conn, {"chat_jid": "g@g.us", "kind": "ask",
                              "what": "share the numbers by eod  ",
                              "owner": "Dev"}, now=2000.0)
    assert a["id"] == b["id"]
    assert b["first_seen"] == 1000.0
    assert b["updated_at"] == 2000.0


def test_alias_resolves_then_expires(conn):
    item = st.upsert_item(conn, {"chat_jid": "g@g.us", "what": "Fix the checkout dip"})
    st.set_alias_map(conn, 7, {"1": item["id"]}, now=1000.0)
    assert st.resolve_alias(conn, 7, "1", now=1000.0 + 3600)["id"] == item["id"]
    assert st.resolve_alias(conn, 7, "1", now=1000.0 + 25 * 3600) is None
    assert st.resolve_alias(conn, 7, "9", now=1000.0) is None


def test_cursor_advance_and_mute(conn):
    assert st.get_cursor(conn, "g@g.us") == ""
    st.set_cursor(conn, "g@g.us", "42")
    assert st.get_cursor(conn, "g@g.us") == "42"
    st.set_cursor(conn, "g@g.us", "43")
    assert st.get_cursor(conn, "g@g.us") == "43"
    assert not st.is_muted(conn, "g@g.us")
    st.set_muted(conn, "g@g.us", True)
    assert st.is_muted(conn, "g@g.us")
    assert st.get_cursor(conn, "g@g.us") == "43"


def test_event_log_records_model_tokens(conn):
    st.log_event(conn, "gemini_call", model="gemini-3.5-flash-lite",
                 input_tokens=120, output_tokens=30)
    row = conn.execute("SELECT * FROM event_log").fetchone()
    assert row["model"] == "gemini-3.5-flash-lite"
    assert row["input_tokens"] == 120
    assert row["output_tokens"] == 30


def test_config_kv_and_drafts(conn):
    st.config_set(conn, "brief_no", "7")
    assert st.config_get(conn, "brief_no") == "7"
    assert st.config_get(conn, "missing", "dflt") == "dflt"
    did = st.save_draft(conn, "g@g.us", "hello")
    assert did == 1


def test_env_file_stays_gitignored():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    gi = (root / ".gitignore").read_text()
    assert ".env" in gi
