"""Worker E: brief ranking, caps, alias actions/expiry, quiet hours, pulse."""

import time

import pytest

from hermes_bot import briefs, scheduler
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


def test_ranking_order_direct_promised_owns_waiting(conn):
    items = [
        _item("w@g.us", "waiting item", owner="Asha", requester="Sam"),
        _item("o@g.us", "owns item", owner="Sam"),
        _item("p@g.us", "promised item", owner="Sam", kind="commitment",
              context="will send it"),
        _item("q@g.us", "question item", owner="Sam", kind="ask",
              context="Sam, can you confirm?"),
    ]
    ranked = briefs.owner_next_items(items, ["Sam"])
    assert [i["chat_jid"] for i in ranked] == ["q@g.us", "p@g.us", "o@g.us", "w@g.us"]


def test_foreign_items_excluded(conn):
    items = [_item("x@g.us", "someone else thing", owner="Asha", requester="Ravi")]
    assert briefs.owner_next_items(items, ["Sam"]) == []


def test_brief_length_cap_and_alias_map(conn):
    for n in range(8):
        st.upsert_item(conn, _item(f"c{n}@g.us", f"task number {n} here", owner="Sam"))
    text, brief_no = briefs.build_brief(conn, ["Sam"], now=1000.0)
    numbered = [ln for ln in text.splitlines() if ln[:1].isdigit()]
    assert len(numbered) == 5
    also = [ln for ln in text.splitlines() if ln.startswith("Also moved")]
    assert len(also) <= 2
    assert st.resolve_alias(conn, brief_no, "1", now=1000.0 + 10) is not None
    assert st.resolve_alias(conn, brief_no, "6", now=1000.0 + 10) is None


def test_empty_brief_is_one_line(conn):
    text, _ = briefs.build_brief(conn, ["Sam"])
    assert text.strip()
    assert len(text.strip().splitlines()) == 1


def test_alias_expiry_24h(conn):
    item = st.upsert_item(conn, _item("c@g.us", "do the thing now", owner="Sam"))
    st.set_alias_map(conn, 3, {"1": item["id"]}, now=1000.0)
    assert st.resolve_alias(conn, 3, "1", now=1000.0 + 23 * 3600) is not None
    assert st.resolve_alias(conn, 3, "1", now=1000.0 + 25 * 3600) is None


def test_done_later_more_actions(conn):
    a = st.upsert_item(conn, _item("c@g.us", "finish report soon", owner="Sam"))
    b = st.upsert_item(conn, _item("d@g.us", "review slides deck", owner="Sam",
                                   context="needs eyes"))
    st.set_alias_map(conn, 9, {"1": a["id"], "2": b["id"]}, now=2000.0)
    assert "Done" in briefs.parse_command(conn, "done 1", 9, now=2000.0 + 5)
    assert st.get_item(conn, a["id"])["status"] == "done"
    assert "tomorrow" in briefs.parse_command(conn, "later 2", 9, now=2000.0 + 5)
    assert st.get_item(conn, b["id"])["due"] == "tomorrow"
    assert "review slides deck" in briefs.parse_command(conn, "more 2", 9, now=2000.0 + 5)
    assert "expired" in briefs.parse_command(conn, "done 1", 9, now=2000.0 + 30 * 3600)


def test_quiet_hours_default_suppresses_night(conn):
    for h in (22, 23, 0, 5, 7):
        assert briefs.in_quiet_hours(conn, h), h
        assert not briefs.should_ping(conn, h)
    for h in (8, 12, 18, 21):
        assert not briefs.in_quiet_hours(conn, h), h
        assert briefs.should_ping(conn, h)


def test_quiet_command_sets_window(conn):
    assert "22" in briefs.parse_command(conn, "quiet 22-8", 1)
    s, e = briefs.get_quiet_hours(conn)
    assert (s, e) == (22, 8)
    assert "Usage" in briefs.parse_command(conn, "quiet someday", 1)


def test_pulse_renders_fresh_brief(conn):
    st.upsert_item(conn, _item("c@g.us", "pulse task here", owner="Sam"))
    out = briefs.parse_command(conn, "pulse", 1, ["Sam"])
    assert "pulse task here" in out
    text, _ = briefs.pulse(conn, ["Sam"])
    assert "pulse task here" in text


def test_scheduler_due_slots():
    ist = scheduler.TZ
    from datetime import datetime
    morning = datetime(2026, 10, 15, 10, 0, tzinfo=ist)
    assert scheduler.due_slots("", morning) == [(9, 0)]
    assert scheduler.due_slots("2026-10-15 09:00", morning) == []
    evening = datetime(2026, 10, 15, 19, 0, tzinfo=ist)
    assert scheduler.due_slots("2026-10-15 09:00", evening) == [(13, 30), (18, 30)]
    nxt = scheduler.next_slot(morning)
    assert (nxt.hour, nxt.minute) == (13, 30)
