"""Worker C acceptance: private slash-command engine.

Task 1 scope: parsing per command, private routing to self-chat,
owner-check rejection.
"""

from hermes_bot.slash import Engine, parse_command


def _engine(**over):
    sent_self = []
    sent_group = []
    fetched = {"chat_name": "Family", "messages": ["Alice: hello", "Bob: hi"]}

    kwargs = dict(
        is_owner=lambda chat, sender: True,
        self_jid="owner-self@s.whatsapp.net",
        fetch=lambda chat_jid: fetched,
        send_self=lambda jid, text: sent_self.append((jid, text)),
        send_group=lambda jid, text: sent_group.append((jid, text)),
        deleter=lambda chat_jid, msg_id: None,
    )
    kwargs.update(over)
    eng = Engine(**kwargs)
    eng.test_sent_self = sent_self
    eng.test_sent_group = sent_group
    return eng


# ── parsing ──────────────────────────────────────────────────────────────

def test_parse_catchup():
    assert parse_command("/catchup") == {"name": "catchup", "public": False, "arg": ""}


def test_parse_pending():
    assert parse_command("/pending") == {"name": "pending", "public": False, "arg": ""}


def test_parse_sotu():
    assert parse_command("/sotu") == {"name": "sotu", "public": False, "arg": ""}


def test_parse_eli5_with_topic():
    assert parse_command("/eli5 inflation") == {
        "name": "eli5", "public": False, "arg": "inflation",
    }


def test_parse_mute_unmute():
    assert parse_command("/mute")["name"] == "mute"
    assert parse_command("/unmute")["name"] == "unmute"


def test_parse_bang_requests_public():
    parsed = parse_command("/catchup!")
    assert parsed == {"name": "catchup", "public": True, "arg": ""}


def test_parse_unknown_and_non_command():
    assert parse_command("/frobnicate") is None
    assert parse_command("hello there") is None
    assert parse_command("") is None


# ── routing: answers target self-chat only ───────────────────────────────

def test_private_answer_goes_to_self_chat_only():
    eng = _engine()
    outcome = eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/catchup")

    assert outcome == "private-sent"
    assert len(eng.test_sent_self) == 1
    jid, text = eng.test_sent_self[0]
    assert jid == "owner-self@s.whatsapp.net"
    assert text, "answer body must not be empty"
    assert eng.test_sent_group == []


def test_private_answer_names_target_chat():
    eng = _engine()
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/pending")
    _, text = eng.test_sent_self[0]
    assert "Family" in text


# ── owner check ──────────────────────────────────────────────────────────

def test_non_owner_command_rejected():
    eng = _engine(is_owner=lambda chat, sender: False)
    outcome = eng.handle_event("group123@g.us", "intruder@s.whatsapp.net", "/catchup")

    assert outcome == "rejected-not-owner"
    assert eng.test_sent_self == []
    assert eng.test_sent_group == []


# ── Task 2: best-effort invoke-delete ────────────────────────────────────

def test_invoking_message_delete_attempted():
    calls = []
    eng = _engine(deleter=lambda chat_jid, msg_id: calls.append((chat_jid, msg_id)))
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/catchup",
                     msg_id="MSGID123")

    assert calls == [("group123@g.us", "MSGID123")]


# ── Task 3: `!` public post needs `yes` ─────────────────────────────────

def _timed_engine(now, **over):
    return _engine(clock=lambda: now[0], **over)


def test_bang_posts_preview_to_self_only():
    now = [1000.0]
    eng = _timed_engine(now)
    outcome = eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/catchup!")

    assert outcome == "confirm-pending"
    assert eng.test_sent_group == []
    assert len(eng.test_sent_self) == 1
    jid, preview = eng.test_sent_self[0]
    assert jid == "owner-self@s.whatsapp.net"
    assert "Family" in preview  # chat name shown
    assert "yes" in preview.lower()


def test_yes_within_window_posts_to_group():
    now = [1000.0]
    eng = _timed_engine(now)
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/catchup!")
    _, preview = eng.test_sent_self[0]

    now[0] += 60.0  # 1 minute later
    outcome = eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "yes")

    assert outcome == "posted"
    assert len(eng.test_sent_group) == 1
    gjid, gtext = eng.test_sent_group[0]
    assert gjid == "group123@g.us"
    assert gtext in preview  # preview held the exact text


def test_no_group_post_without_yes():
    now = [1000.0]
    eng = _timed_engine(now)
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/catchup!")

    now[0] += 60.0
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "something else")

    assert eng.test_sent_group == []


def test_yes_from_non_owner_posts_nothing():
    # owner gate is evaluated against the sender of `yes` itself:
    eng = _engine(is_owner=lambda chat, sender: sender.startswith("owner"))
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/catchup!")

    outcome = eng.handle_event("group123@g.us", "intruder@s.whatsapp.net", "yes")

    assert outcome == "rejected-not-owner"
    assert eng.test_sent_group == []


def test_yes_from_self_chat_confirms_group_post():
    now = [1000.0]
    eng = _timed_engine(now)
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/catchup!")

    now[0] += 30.0
    outcome = eng.handle_event(
        "owner-self@s.whatsapp.net", "owner@s.whatsapp.net", "yes")

    assert outcome == "posted"
    assert len(eng.test_sent_group) == 1
    assert eng.test_sent_group[0][0] == "group123@g.us"


def test_yes_after_expiry_posts_nothing():
    now = [1000.0]
    eng = _timed_engine(now)
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/catchup!")

    now[0] += 301.0  # past the 5-minute window
    outcome = eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "yes")

    assert outcome == "confirm-expired"
    assert eng.test_sent_group == []


# ── Task 4: /mute + /unmute per chat ────────────────────────────────────

def test_mute_silences_chat_commands():
    eng = _engine()
    assert eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/mute") == "muted"
    eng.test_sent_self.clear()
    eng.test_sent_group.clear()

    outcome = eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/catchup")

    assert outcome == "muted"
    assert eng.test_sent_self == []
    assert eng.test_sent_group == []


def test_mute_is_per_chat():
    eng = _engine()
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/mute")

    outcome = eng.handle_event("other999@g.us", "owner@s.whatsapp.net", "/catchup")

    assert outcome == "private-sent"


def test_unmute_reenables_chat():
    eng = _engine()
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/mute")
    assert eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/unmute") == "unmuted"

    outcome = eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/catchup")

    assert outcome == "private-sent"


def test_mute_unmute_need_owner():
    eng = _engine(is_owner=lambda chat, sender: False)

    assert eng.handle_event("group123@g.us", "x@s.whatsapp.net", "/mute") == "rejected-not-owner"
    assert eng.handle_event("group123@g.us", "x@s.whatsapp.net", "/unmute") == "rejected-not-owner"


def test_private_answer_never_posts_to_group_on_yes():
    now = [1000.0]
    eng = _timed_engine(now)
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "/pending")

    now[0] += 10.0
    eng.handle_event("group123@g.us", "owner@s.whatsapp.net", "yes")

    assert eng.test_sent_group == []


def test_delete_failure_never_blocks_answer(caplog):
    def _boom(chat_jid, msg_id):
        raise RuntimeError("bridge has no delete endpoint")

    eng = _engine(deleter=_boom)
    with caplog.at_level("WARNING", logger="hermes_bot.slash"):
        outcome = eng.handle_event("group123@g.us", "owner@s.whatsapp.net",
                                   "/catchup", msg_id="MSGID123")

    assert outcome == "private-sent"
    assert len(eng.test_sent_self) == 1
    assert "deletion impossible" in caplog.text
