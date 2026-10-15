"""Worker F hookup: brain routes draft/send; confirm survives events."""

import pytest

from hermes_bot import worker_f


@pytest.fixture()
def hooked(monkeypatch, tmp_path):
    monkeypatch.setenv("MOSAIC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MOSAIC_LIVE", raising=False)
    worker_f.reset_engine()
    sent = []
    monkeypatch.setattr("hermes_bot.db.is_message_from_owner",
                        lambda c, s: s.startswith("owner"))
    monkeypatch.setattr("hermes_bot.slash.resolve_self_jid",
                        lambda: "self@s.whatsapp.net")
    monkeypatch.setattr("hermes_bot.db.get_chat_messages",
                        lambda j: [{"sender": "Dev", "content": "share the 3 files",
                                    "chat_name": "Dev"}])
    # Guards read the snapshot read-only, like production reads live DBs.
    monkeypatch.setattr("hermes_bot.config.MESSAGES_DB",
                        "/tmp/mosaic-test/messages.db")
    monkeypatch.setattr("hermes_bot.sender.enqueue_to_mechat",
                        lambda t: sent.append(t))
    monkeypatch.setattr("hermes_bot.sender.enqueue_message",
                        lambda j, t: sent.append((j, t)))
    from hermes_bot import store as st
    conn = st.connect()
    st.init_db(conn)
    item = st.upsert_item(conn, {"chat_jid": "918879522474@s.whatsapp.net",
                                 "what": "Share the 3 files"})
    st.set_alias_map(conn, 5, {"2": item["id"]})
    conn.close()
    yield sent
    worker_f.reset_engine()


def test_draft_command_replies_with_options(hooked):
    outcome = worker_f.handle_event("self@s.whatsapp.net",
                                    "owner@s.whatsapp.net", "draft 2")
    assert outcome == "draft-sent"
    assert hooked and "send 2A" in hooked[0]


def test_send_confirm_survives_across_events(hooked):
    assert worker_f.handle_event("self@s.whatsapp.net",
                                 "owner@s.whatsapp.net",
                                 "send 2B") == "rejected-unknown-ref"
    worker_f.handle_event("self@s.whatsapp.net",
                          "owner@s.whatsapp.net", "draft 2")
    assert worker_f.handle_event("self@s.whatsapp.net",
                                 "owner@s.whatsapp.net",
                                 "send 2B") == "confirm-pending"
    # separate event, same process engine: yes must still match
    assert worker_f.handle_event("self@s.whatsapp.net",
                                 "owner@s.whatsapp.net",
                                 "yes") == "posted-dry-run"
    targets = [j for j in hooked if isinstance(j, tuple)]
    assert all(j[0] != "918879522474@s.whatsapp.net" for j in targets)


def test_group_banter_suppresses_ping(hooked):
    outcome = worker_f.handle_event("fam@g.us", "friend@s.whatsapp.net",
                                    "random banter lol")
    assert outcome == "ping-suppressed-not-direct"
    assert hooked == []
