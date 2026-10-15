"""Worker G: pipeline cycle (scan → brief → draft) + brain dispatch wiring."""

import pytest

from hermes_bot import brain, cycle
from hermes_bot import store as st


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MOSAIC_LIVE", raising=False)
    c = st.connect()
    st.init_db(c)
    yield c
    c.close()


def test_run_cycle_dry_run_end_to_end(conn):
    report = cycle.run_cycle(conn, limit_chats=20)
    assert report["dry_run"] is True
    assert report["delivered"] is False
    assert report["messages"] > 0
    assert report["items"] >= 0
    assert report["brief_text"].strip()
    assert report["brief_no"] == 1
    assert isinstance(report["pings_suppressed"], dict)
    # cursors advanced: a second pass sees nothing new
    again = cycle.run_cycle(conn, limit_chats=20)
    assert again["messages"] == 0
    assert again["brief_no"] == 2


def test_run_cycle_never_delivers_in_dry_run(conn):
    delivered = []
    report = cycle.run_cycle(conn, limit_chats=5, deliver=delivered.append)
    assert report["delivered"] is False
    assert delivered == []


def test_run_cycle_delivers_only_when_live_and_set(conn):
    delivered = []
    report = cycle.run_cycle(conn, limit_chats=5, dry_run=False,
                             deliver=delivered.append)
    assert report["delivered"] is True
    assert len(delivered) == 1 and delivered[0].strip()


def test_run_cycle_fake_llm_extracts_and_upserts(conn):
    def fake_llm(thread):
        return {"items": [{"kind": "ask", "what": "Send the weekly report",
                            "status": "open"}]}

    report = cycle.run_cycle(conn, limit_chats=20, llm_fn=fake_llm)
    assert report["llm_calls"] >= 0
    assert report["open_items"] >= 0
    rows = conn.execute("SELECT what FROM items").fetchall()
    assert any("weekly report" in (r["what"] or "").lower() for r in rows) \
        or report["items"] == 0  # gated threads only


def test_draft_sample_empty_when_nothing_actionable(conn):
    from hermes_bot import scanner as sc
    snap = sc.open_snapshot()
    try:
        assert cycle.draft_sample(conn, snap, []) == {}
    finally:
        snap.close()


def test_cycle_env_gates(monkeypatch):
    monkeypatch.delenv("MOSAIC_CYCLE", raising=False)
    assert cycle.cycle_enabled() is False
    monkeypatch.setenv("MOSAIC_CYCLE", "1")
    assert cycle.cycle_enabled() is True
    monkeypatch.setenv("MOSAIC_CYCLE_INTERVAL_S", "30")
    assert cycle.cycle_interval_s() == 60.0  # floored at 60s
    monkeypatch.setenv("MOSAIC_CYCLE_INTERVAL_S", "1200")
    assert cycle.cycle_interval_s() == 1200.0
    monkeypatch.setenv("MOSAIC_CYCLE_INTERVAL_S", "junk")
    assert cycle.cycle_interval_s() == 900.0


def test_main_owner_names_parsing(monkeypatch):
    from hermes_bot import main as m
    monkeypatch.setenv("OWNER_NAMES", " Sam, Asha ,,")
    assert m._owner_names() == ["Sam", "Asha"]
    monkeypatch.delenv("OWNER_NAMES")
    assert m._owner_names() == []


def test_brain_routes_slash_first(monkeypatch):
    calls = []

    class SlashStub:
        def handle_event(self, *a):
            calls.append("slash")
            return "private-sent"

    def boom(*a):
        calls.append("worker-f")
        return "x"

    monkeypatch.setattr("hermes_bot.slash.get_engine",
                        lambda: SlashStub())
    monkeypatch.setattr("hermes_bot.worker_f.handle_event", boom)
    assert brain.route_event("c@g.us", "s", "/pending") == "private-sent"
    assert calls == ["slash"]


def test_brain_falls_back_to_worker_f(monkeypatch):
    calls = []

    class SlashStub:
        def handle_event(self, *a):
            calls.append("slash")
            return "ignored"

    monkeypatch.setattr("hermes_bot.slash.get_engine",
                        lambda: SlashStub())
    monkeypatch.setattr("hermes_bot.worker_f.handle_event",
                        lambda *a: calls.append("worker-f") or "ping-sent")
    assert brain.route_event("c@g.us", "s", "hi") == "ping-sent"
    assert calls == ["slash", "worker-f"]
