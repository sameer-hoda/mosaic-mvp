"""Owner identity: PN and LID forms of the owner's JID resolve to one owner.

Port of the v4 mechat approach: the bridge session database
(whatsmeow_device) holds this account's own PN and LID. Fresh syncs
address the self-chat by LID (``...@lid``), older ones by PN
(``...@s.whatsapp.net``) — callers must accept both. Self-chat
resolution is freshest-wins between the two candidates.
"""

import sqlite3

from hermes_bot import config


def _split_user(jid):
    return (jid or "").split(":")[0].split("@")[0]


def _session_db(explicit=None):
    if explicit:
        return explicit
    return config.WHATSAPP_DB


def _messages_db(explicit=None):
    if explicit:
        return explicit
    return config.MESSAGES_DB


def own_identity(session_db=None):
    """Return (phone, lid, source) for this WhatsApp account."""
    try:
        con = sqlite3.connect(f"file:{_session_db(session_db)}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT jid, lid FROM whatsmeow_device LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        if row:
            phone, lid = _split_user(row[0]), _split_user(row[1])
            if phone.isdigit():
                return phone, lid if lid.isdigit() else "", "session-db"
    except Exception:
        pass
    if config.OWNER_PHONE:
        return config.OWNER_PHONE, "", "env"
    try:
        from hermes_bot import db as _db

        setup_phone = _db._read_setup_json().get("own_phone", "")
        if setup_phone:
            return setup_phone, "", "setup"
    except Exception:
        pass
    return None, "", "not-found"


def is_owner_jid(jid, session_db=None):
    """True when jid is the owner in PN or LID form (one owner, both spellings)."""
    user = _split_user(jid)
    if not user or "@g.us" in (jid or "") or (jid or "") == "status@broadcast":
        return False
    phone, _lid, _src = own_identity(session_db)
    if not phone:
        return False
    if user == phone:
        return True
    try:
        con = sqlite3.connect(f"file:{_session_db(session_db)}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT pn FROM whatsmeow_lid_map WHERE lid LIKE ?",
                (f"{user}%",),
            ).fetchone()
            if row and _split_user(row[0]) == phone:
                return True
            row = con.execute(
                "SELECT lid FROM whatsmeow_lid_map WHERE pn = ? OR pn LIKE ?",
                (user, f"{user}:%"),
            ).fetchone()
            if row:
                return True
        finally:
            con.close()
    except Exception:
        pass
    return False


def resolve_self_chat(messages_db=None, session_db=None):
    """Return (jid, how); freshest message timestamp wins between PN/LID forms."""
    phone, lid, src = own_identity(session_db)
    if not phone:
        return None, "not-found"
    candidates = [f"{phone}@s.whatsapp.net"]
    if lid:
        candidates.append(f"{lid}@lid")
    try:
        con = sqlite3.connect(f"file:{_messages_db(messages_db)}?mode=ro", uri=True)
        try:
            best = None
            for jid in candidates:
                try:
                    row = con.execute(
                        "SELECT COUNT(*), MAX(timestamp) FROM messages WHERE chat_jid = ?",
                        (jid,),
                    ).fetchone()
                except Exception:
                    continue
                if row and row[0]:
                    if best is None or (row[1] or "") > (best[1] or ""):
                        best = (jid, row[1])
            if best:
                return best[0], src
        finally:
            con.close()
    except Exception:
        pass
    return candidates[0], src
