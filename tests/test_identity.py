"""Worker B Task 1 (RED): PN and LID forms of the owner resolve to one owner.

Fixture session DB mimics a fresh WhatsApp sync: the device row carries a
PN JID plus a LID, and the LID map bridges them. Both spellings of the
owner must be recognised, and self-chat resolution is freshest-wins.
"""

import sqlite3

import hermes_bot.identity as ident

PHONE = "919876543210"
LID = "219541632213229"
PN_JID = f"{PHONE}@s.whatsapp.net"
LID_JID = f"{LID}@lid"


def _session_db(tmp_path):
    p = tmp_path / "whatsapp.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE whatsmeow_device (jid TEXT, lid TEXT)")
    con.execute(
        "INSERT INTO whatsmeow_device VALUES (?, ?)",
        (f"{PHONE}:16@s.whatsapp.net", f"{LID}:37@lid"),
    )
    con.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT, pn TEXT)")
    con.execute(
        "INSERT INTO whatsmeow_lid_map VALUES (?, ?)",
        (f"{LID}:37@lid", PHONE),
    )
    con.commit()
    con.close()
    return str(p)


def _messages_db(tmp_path, rows):
    p = tmp_path / "messages.db"
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE messages (chat_jid TEXT, timestamp TEXT, is_from_me INT)"
    )
    con.executemany("INSERT INTO messages VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return str(p)


def test_pn_and_lid_forms_resolve_to_one_owner(tmp_path):
    sdb = _session_db(tmp_path)
    assert ident.is_owner_jid(PN_JID, sdb)
    assert ident.is_owner_jid(f"{PHONE}:16@s.whatsapp.net", sdb)
    assert ident.is_owner_jid(LID_JID, sdb)
    assert ident.is_owner_jid(f"{LID}:37@lid", sdb)
    assert not ident.is_owner_jid("911234567890@s.whatsapp.net", sdb)
    assert not ident.is_owner_jid("120363405680258025@g.us", sdb)


def test_own_identity_prefers_session_db(tmp_path):
    sdb = _session_db(tmp_path)
    phone, lid, source = ident.own_identity(sdb)
    assert phone == PHONE
    assert lid == LID
    assert source == "session-db"


def test_resolve_self_chat_freshest_wins(tmp_path):
    sdb = _session_db(tmp_path)
    mdb = _messages_db(tmp_path, [
        (PN_JID, "2026-10-14 10:00:00+05:30", 1),
        (LID_JID, "2026-10-15 10:00:00+05:30", 1),
    ])
    jid, how = ident.resolve_self_chat(mdb, sdb)
    assert jid == LID_JID
    assert how == "session-db"


def test_resolve_self_chat_pn_wins_when_fresher(tmp_path):
    sdb = _session_db(tmp_path)
    mdb = _messages_db(tmp_path, [
        (PN_JID, "2026-10-15 10:00:00+05:30", 1),
        (LID_JID, "2026-10-14 10:00:00+05:30", 1),
    ])
    jid, _ = ident.resolve_self_chat(mdb, sdb)
    assert jid == PN_JID
