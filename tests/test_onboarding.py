"""Worker B Task 2 (RED): no group ever; welcome goes to the resolved
self-chat; sender defaults to dry-run unless MOSAIC_LIVE=1.
"""

import ast
import sqlite3
from pathlib import Path

import pytest

import hermes_bot.onboarding as ob
from hermes_bot import sender

PHONE = "919876543210"
LID = "219541632213229"
LID_JID = f"{LID}@lid"

GROUP_CREATISH = ("creategroup", "groupcreate", "creategroupchat", "newgroup")


def _fixture_dbs(tmp_path, monkeypatch):
    sdb = tmp_path / "whatsapp.db"
    con = sqlite3.connect(sdb)
    con.execute("CREATE TABLE whatsmeow_device (jid TEXT, lid TEXT)")
    con.execute(
        "INSERT INTO whatsmeow_device VALUES (?, ?)",
        (f"{PHONE}:16@s.whatsapp.net", f"{LID}:37@lid"),
    )
    con.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT, pn TEXT)")
    con.execute("INSERT INTO whatsmeow_lid_map VALUES (?, ?)", (f"{LID}:37@lid", PHONE))
    con.commit()
    con.close()
    mdb = tmp_path / "messages.db"
    con = sqlite3.connect(mdb)
    con.execute("CREATE TABLE messages (chat_jid TEXT, timestamp TEXT, is_from_me INT)")
    con.execute("INSERT INTO messages VALUES (?, ?, 1)", (LID_JID, "2026-10-15 10:00:00+05:30",))
    con.commit()
    con.close()
    monkeypatch.setattr("hermes_bot.config.MESSAGES_DB", str(mdb))
    monkeypatch.setattr("hermes_bot.config.WHATSAPP_DB", str(sdb))
    monkeypatch.setattr("hermes_bot.config.MECHAT_JID", "")
    # A real paired setup.json must never leak into these tests.
    monkeypatch.setattr("hermes_bot.config.SETUP_FILE",
                        str(tmp_path / "setup.json"))
    monkeypatch.setattr("hermes_bot.config.OWNER_PHONE", "")
    return mdb, sdb


def _calls_in(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.append(f.id.lower())
            elif isinstance(f, ast.Attribute):
                names.append(f.attr.lower())
    return names


def test_onboarding_path_has_zero_group_create_calls():
    names = _calls_in(Path("hermes_bot/onboarding.py"))
    hits = [n for n in names if n in GROUP_CREATISH]
    assert hits == [], f"group-create calls on onboarding path: {hits}"


def test_create_group_stub_raises():
    with pytest.raises(RuntimeError):
        ob.create_group("anyone", "whatever")


def test_welcome_goes_to_resolved_self_chat_when_live(tmp_path, monkeypatch):
    _fixture_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("MOSAIC_LIVE", "1")
    sent = []
    monkeypatch.setattr(sender, "enqueue_message", lambda jid, text: sent.append((jid, text)))
    result = ob.send_welcome()
    assert result["sent"] is True
    assert result["jid"] == LID_JID
    assert len(sent) == 1 and sent[0][0] == LID_JID
    assert not sent[0][0].endswith("@g.us")


def test_sender_defaults_to_dry_run(tmp_path, monkeypatch):
    _fixture_dbs(tmp_path, monkeypatch)
    monkeypatch.delenv("MOSAIC_LIVE", raising=False)
    sent = []
    monkeypatch.setattr(sender, "enqueue_message", lambda jid, text: sent.append((jid, text)))
    result = ob.send_welcome()
    assert result["sent"] is False
    assert result["dry_run"] is True
    assert sent == []


def test_send_welcome_refuses_group_jid(tmp_path, monkeypatch):
    _fixture_dbs(tmp_path, monkeypatch)
    monkeypatch.setenv("MOSAIC_LIVE", "1")
    with pytest.raises(ValueError):
        ob.send_welcome(jid="120363405680258025@g.us")


def test_enqueue_message_rejects_groups(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_bot.config.HERMES_DB_PATH", str(tmp_path / "hermes.db"))
    with pytest.raises(ValueError):
        sender.enqueue_message("120363405680258025@g.us", "nope")
