import json
import sqlite3
import datetime
from pathlib import Path
from typing import Optional

from hermes_bot import config


def _read_setup_json():
    path = Path(config.SETUP_FILE)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            pass
    return {}


def _connect():
    conn = sqlite3.connect(config.MESSAGES_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS wa", (config.WHATSAPP_DB,))
    return conn


def get_own_jid() -> str:
    conn = _connect()
    row = conn.execute("SELECT jid FROM wa.whatsmeow_device LIMIT 1").fetchone()
    conn.close()
    if row and row["jid"]:
        full_jid = row["jid"]
        phone = full_jid.split(":")[0]
        return f"{phone}@s.whatsapp.net"
    if config.OWNER_PHONE:
        return f"{config.OWNER_PHONE}@s.whatsapp.net"
    raise RuntimeError("No device row in whatsmeow_device and OWNER_PHONE_NUMBER not set — is the bridge paired?")


def get_own_phone() -> str:
    setup = _read_setup_json()
    if setup.get("own_phone"):
        return setup["own_phone"]
    conn = _connect()
    row = conn.execute("SELECT jid FROM wa.whatsmeow_device LIMIT 1").fetchone()
    conn.close()
    if row and row["jid"]:
        return row["jid"].split(":")[0]
    if config.OWNER_PHONE:
        return config.OWNER_PHONE
    jid = get_own_jid()
    return jid.split("@")[0]


def get_mechat_chat_jid() -> str:
    setup = _read_setup_json()
    if setup.get("mechat_jid"):
        return setup["mechat_jid"]
    if config.MECHAT_JID:
        return config.MECHAT_JID
    own_phone = get_own_phone()
    conn = _connect()

    row = conn.execute(
        "SELECT lid FROM wa.whatsmeow_lid_map WHERE pn = ?",
        (own_phone,),
    ).fetchone()
    if row:
        lid = row["lid"].split(":")[0]
        conn.close()
        return f"{lid}@lid"

    row2 = conn.execute(
        "SELECT lid FROM wa.whatsmeow_device WHERE jid LIKE ?",
        (f"{own_phone}%",),
    ).fetchone()
    if row2 and row2["lid"]:
        lid = row2["lid"].split(":")[0]
        conn.close()
        return f"{lid}@lid"

    conn.close()
    return f"{own_phone}@s.whatsapp.net"


def get_non_archived_groups() -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT ch.jid, ch.name, ch.last_message_time
        FROM chats ch
        LEFT JOIN wa.whatsmeow_chat_settings cs ON ch.jid = cs.chat_jid
        WHERE ch.jid LIKE '%@g.us'
          AND (cs.archived IS NULL OR cs.archived = 0)
        ORDER BY ch.last_message_time DESC
        """
    ).fetchall()
    conn.close()

    return [
        {
            "jid": r["jid"],
            "name": r["name"] or r["jid"].split("@")[0],
            "last_message_time": r["last_message_time"],
        }
        for r in rows
    ]


def get_active_groups(days: int = 30) -> list[dict]:
    threshold = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=days)
    ).isoformat()

    conn = _connect()
    rows = conn.execute(
        """
        SELECT ch.jid, ch.name, MAX(m.timestamp) AS last_msg
        FROM chats ch
        JOIN messages m ON ch.jid = m.chat_jid
        LEFT JOIN wa.whatsmeow_chat_settings cs ON ch.jid = cs.chat_jid
        WHERE ch.jid LIKE '%@g.us'
          AND (cs.archived IS NULL OR cs.archived = 0)
          AND m.timestamp >= ?
          AND m.content IS NOT NULL
          AND m.content != ''
        GROUP BY ch.jid
        ORDER BY last_msg DESC
        """,
        (threshold,),
    ).fetchall()
    conn.close()

    return [
        {
            "jid": r["jid"],
            "name": r["name"] or r["jid"].split("@")[0],
            "last_message_time": r["last_msg"],
        }
        for r in rows
    ]


def get_chat_messages(chat_jid: str, days: int = 14, limit: int = 200) -> list[dict]:
    threshold = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=days)
    ).isoformat()

    conn = _connect()
    rows = conn.execute(
        """
        SELECT m.content, m.timestamp, m.is_from_me,
               COALESCE(c.full_name, c.push_name, c.first_name,
                         c.business_name) AS contact_name,
               ms.sender_jid,
               ch.name AS chat_name
        FROM messages m
        LEFT JOIN chats ch ON m.chat_jid = ch.jid
        LEFT JOIN wa.whatsmeow_message_secrets ms
            ON m.id = ms.message_id AND m.chat_jid = ms.chat_jid
        LEFT JOIN wa.whatsmeow_contacts c
            ON ms.sender_jid = c.their_jid
        WHERE m.chat_jid = ?
          AND m.timestamp >= ?
          AND m.content IS NOT NULL
          AND m.content != ''
        ORDER BY m.timestamp DESC
        LIMIT ?
        """,
        (chat_jid, threshold, limit),
    ).fetchall()
    conn.close()

    results = []
    for r in reversed(rows):
        try:
            dt = datetime.datetime.fromisoformat(r["timestamp"].replace(" ", "T"))
        except (ValueError, AttributeError):
            dt = datetime.datetime.now(datetime.timezone.utc)

        sender = "You" if r["is_from_me"] else (
            r["contact_name"]
            or (r["sender_jid"].split("@")[0] if r["sender_jid"] else "Unknown")
        )

        results.append({
            "time": dt,
            "sender": sender,
            "content": r["content"].strip(),
            "is_from_me": bool(r["is_from_me"]),
            "chat_name": r["chat_name"] or chat_jid.split("@")[0],
        })
    return results


def get_recent_all_messages(hours: int = 24, limit: int = 1000) -> list[dict]:
    threshold = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=hours)
    ).isoformat()

    conn = _connect()
    rows = conn.execute(
        """
        SELECT m.content, m.timestamp, m.is_from_me, m.chat_jid,
               COALESCE(c.full_name, c.push_name, c.first_name,
                         c.business_name) AS contact_name,
               ms.sender_jid,
               ch.name AS chat_name
        FROM messages m
        LEFT JOIN chats ch ON m.chat_jid = ch.jid
        LEFT JOIN wa.whatsmeow_message_secrets ms
            ON m.id = ms.message_id AND m.chat_jid = ms.chat_jid
        LEFT JOIN wa.whatsmeow_contacts c
            ON ms.sender_jid = c.their_jid
        LEFT JOIN wa.whatsmeow_chat_settings cs ON m.chat_jid = cs.chat_jid
        WHERE m.chat_jid LIKE '%@g.us'
          AND (cs.archived IS NULL OR cs.archived = 0)
          AND m.timestamp >= ?
          AND m.content IS NOT NULL
          AND m.content != ''
        ORDER BY m.timestamp DESC
        LIMIT ?
        """,
        (threshold, limit),
    ).fetchall()
    conn.close()

    results = []
    for r in reversed(rows):
        try:
            dt = datetime.datetime.fromisoformat(r["timestamp"].replace(" ", "T"))
        except (ValueError, AttributeError):
            dt = datetime.datetime.now(datetime.timezone.utc)

        sender = "You" if r["is_from_me"] else (
            r["contact_name"]
            or (r["sender_jid"].split("@")[0] if r["sender_jid"] else "Unknown")
        )

        results.append({
            "time": dt,
            "sender": sender,
            "content": r["content"].strip(),
            "is_from_me": bool(r["is_from_me"]),
            "chat_name": r["chat_name"] or r["chat_jid"].split("@")[0],
        })
    return results


def resolve_contact_name(jid: str) -> str:
    conn = _connect()
    row = conn.execute(
        """
        SELECT COALESCE(push_name, full_name, first_name, business_name) AS name
        FROM wa.whatsmeow_contacts
        WHERE their_jid = ?
        """,
        (jid,),
    ).fetchone()
    conn.close()
    if row and row["name"]:
        return row["name"]
    return jid.split("@")[0] if "@" in jid else jid


def is_message_from_owner(chat_jid: str, sender_jid: str) -> bool:
    own_phone = get_own_phone()
    sender_user = sender_jid.split("@")[0].split(":")[0]
    if sender_user == own_phone:
        return True
    conn = _connect()
    row = conn.execute(
        "SELECT pn FROM wa.whatsmeow_lid_map WHERE lid LIKE ?",
        (f"{sender_user}%",),
    ).fetchone()
    conn.close()
    if row and row["pn"] == own_phone:
        return True
    return False


def resolve_contact_by_name(name_hint: str) -> list[dict]:
    conn = _connect()
    pattern = f"%{name_hint}%"
    rows = conn.execute(
        """
        SELECT their_jid,
               COALESCE(full_name, push_name, first_name, business_name) AS name
        FROM wa.whatsmeow_contacts
        WHERE COALESCE(full_name, push_name, first_name, business_name) LIKE ?
        """,
        (pattern,),
    ).fetchall()
    conn.close()
    return [{"jid": r["their_jid"], "name": r["name"]} for r in rows]


def get_best_contact(name_hint: str) -> dict | None:
    candidates = resolve_contact_by_name(name_hint)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    conn = _connect()
    placeholders = ",".join("?" for _ in candidates)
    jids = [c["jid"] for c in candidates]
    rows = conn.execute(
        f"SELECT chat_jid, COUNT(*) AS cnt FROM messages WHERE chat_jid IN ({placeholders}) GROUP BY chat_jid ORDER BY cnt DESC",
        jids,
    ).fetchall()
    conn.close()

    msg_counts = {r["chat_jid"]: r["cnt"] for r in rows}
    best = max(candidates, key=lambda c: msg_counts.get(c["jid"], 0))
    return best


def get_person_messages(person_jid: str, days: int = 14, limit: int = 200) -> list[dict]:
    threshold = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=days)
    ).isoformat()

    conn = _connect()
    rows = conn.execute(
        """
        SELECT m.content, m.timestamp, m.is_from_me,
               COALESCE(c.full_name, c.push_name, c.first_name,
                         c.business_name) AS contact_name,
               ms.sender_jid
        FROM messages m
        LEFT JOIN wa.whatsmeow_message_secrets ms
            ON m.id = ms.message_id AND m.chat_jid = ms.chat_jid
        LEFT JOIN wa.whatsmeow_contacts c
            ON ms.sender_jid = c.their_jid
        WHERE m.chat_jid = ?
          AND m.timestamp >= ?
          AND m.content IS NOT NULL
          AND m.content != ''
        ORDER BY m.timestamp DESC
        LIMIT ?
        """,
        (person_jid, threshold, limit),
    ).fetchall()
    conn.close()

    results = []
    for r in reversed(rows):
        try:
            dt = datetime.datetime.fromisoformat(r["timestamp"].replace(" ", "T"))
        except (ValueError, AttributeError):
            dt = datetime.datetime.now(datetime.timezone.utc)

        sender = "You" if r["is_from_me"] else (
            r["contact_name"]
            or (r["sender_jid"].split("@")[0] if r["sender_jid"] else "Unknown")
        )

        results.append({
            "time": dt,
            "sender": sender,
            "content": r["content"].strip(),
            "is_from_me": bool(r["is_from_me"]),
        })
    return results
