"""Worker F: send NB confirm machine — yes-gated, capped, guarded sends.

Acceptance: confirm-expiry, send-cap, no-autosend, group needs history.
"""

from hermes_bot import send as snd


def _engine(now, **over):
    sent = []
    counts = {"sends": over.pop("sends_today", 0)}
    kwargs = dict(
        is_owner=lambda chat, sender: sender.startswith("owner"),
        self_jid="owner-self@s.whatsapp.net",
        enqueue=lambda jid, text: sent.append((jid, text)),
        is_contact=lambda jid: True,
        owner_wrote_in_chat=lambda jid: True,
        sends_today=lambda: counts["sends"],
        record_send=lambda: counts.__setitem__("sends", counts["sends"] + 1),
        clock=lambda: now[0],
        resolve=lambda ref: {"chat_jid": "dev@s.whatsapp.net",
                             "chat_name": "Dev",
                             "text": f"reply {ref}"},
    )
    kwargs.update(over)
    eng = snd.SendEngine(**kwargs)
    eng.test_sent = sent
    eng.test_counts = counts
    return eng


def _request(eng):
    return eng.handle_event("owner-self@s.whatsapp.net",
                            "owner@s.whatsapp.net", "send 2B")


def test_send_shows_confirm_naming_chat_and_text():
    eng = _engine([1000.0])
    outcome = _request(eng)
    assert outcome == "confirm-pending"
    assert [j for j, _ in eng.test_sent] == ["owner-self@s.whatsapp.net"]
    preview = eng.last_preview
    assert "Dev" in preview and "dev@s.whatsapp.net" in preview
    assert "reply 2B" in preview and "yes" in preview.lower()


def test_yes_within_window_sends_exact_text_live(monkeypatch):
    monkeypatch.setenv("MOSAIC_LIVE", "1")
    now = [1000.0]
    eng = _engine(now)
    _request(eng)
    now[0] += 60.0
    outcome = eng.handle_event("owner-self@s.whatsapp.net",
                               "owner@s.whatsapp.net", "yes")
    assert outcome == "posted"
    assert eng.test_sent[-1] == ("dev@s.whatsapp.net", "reply 2B")


def test_confirm_expiry_cancels():
    now = [1000.0]
    eng = _engine(now)
    _request(eng)
    now[0] += 301.0
    outcome = eng.handle_event("owner-self@s.whatsapp.net",
                               "owner@s.whatsapp.net", "yes")
    assert outcome == "confirm-expired"
    assert all(j != "dev@s.whatsapp.net" for j, _ in eng.test_sent)


def test_anything_else_cancels():
    now = [1000.0]
    eng = _engine(now)
    _request(eng)
    now[0] += 10.0
    outcome = eng.handle_event("owner-self@s.whatsapp.net",
                               "owner@s.whatsapp.net", "maybe later")
    assert outcome == "cancelled"
    assert all(j != "dev@s.whatsapp.net" for j, _ in eng.test_sent)
    now[0] += 10.0
    assert eng.handle_event("owner-self@s.whatsapp.net",
                           "owner@s.whatsapp.net", "yes") == "ignored"


def test_no_autosend_without_mosaic_live(monkeypatch):
    monkeypatch.delenv("MOSAIC_LIVE", raising=False)
    now = [1000.0]
    eng = _engine(now)
    _request(eng)
    now[0] += 10.0
    outcome = eng.handle_event("owner-self@s.whatsapp.net",
                               "owner@s.whatsapp.net", "yes")
    assert outcome == "posted-dry-run"
    assert all(j != "dev@s.whatsapp.net" for j, _ in eng.test_sent)


def test_send_cap_30_per_day(monkeypatch):
    monkeypatch.setenv("MOSAIC_LIVE", "1")
    now = [1000.0]
    eng = _engine(now, sends_today=30)
    outcome = _request(eng)
    assert outcome == "rejected-cap"
    assert eng.test_sent == []


def test_group_post_needs_owner_history(monkeypatch):
    monkeypatch.setenv("MOSAIC_LIVE", "1")
    now = [1000.0]
    eng = _engine(now, owner_wrote_in_chat=lambda jid: False,
                  resolve=lambda ref: {
                      "chat_jid": "family@g.us", "chat_name": "Fam",
                      "text": "hi"})
    assert _request(eng) == "rejected-no-owner-history"
    assert eng.test_sent == []


def test_group_post_allowed_after_yes_with_history(monkeypatch):
    monkeypatch.setenv("MOSAIC_LIVE", "1")
    now = [1000.0]
    eng = _engine(now, resolve=lambda ref: {
        "chat_jid": "family@g.us", "chat_name": "Fam",
        "text": "hi"})
    assert _request(eng) == "confirm-pending"
    now[0] += 10.0
    outcome = eng.handle_event("owner-self@s.whatsapp.net",
                               "owner@s.whatsapp.net", "yes")
    assert outcome == "posted"
    assert eng.test_sent[-1] == ("family@g.us", "hi")


def test_rejects_non_contact_and_no_owner_history():
    eng = _engine([1000.0], is_contact=lambda jid: False)
    assert _request(eng) == "rejected-non-contact"
    eng = _engine([1000.0], owner_wrote_in_chat=lambda jid: False)
    assert _request(eng) == "rejected-no-owner-history"


def test_yes_from_non_owner_posts_nothing():
    now = [1000.0]
    eng = _engine(now)
    _request(eng)
    now[0] += 10.0
    outcome = eng.handle_event("owner-self@s.whatsapp.net",
                               "intruder@s.whatsapp.net", "yes")
    assert outcome == "rejected-not-owner"
    assert all(j != "dev@s.whatsapp.net" for j, _ in eng.test_sent)
