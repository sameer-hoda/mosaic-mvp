"""Build a synthetic test snapshot when none exists (fix 1).

Creates /tmp/mosaic-test/messages.db (20 groups + 1 DM, mixed ask /
banter / FYI / empty threads) plus a sibling whatsapp.db with
whatsmeow_chat_settings. Never overwrites a snapshot that already
holds messages. No network, no secrets.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hermes_bot import scanner as sc

GROUPS = [(f"90000{i:04d}-1@g.us", f"Fixture Group {i}") for i in range(1, 21)]
DM = "911234567890@s.whatsapp.net"
# Suite-support DM: test_worker_f.py's contact guard looks this chat up.
SUITE_DM = "918879522474@s.whatsapp.net"


def _seed_messages(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS chats"
              " (jid TEXT, name TEXT, last_message_time TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS messages"
              " (id TEXT, chat_jid TEXT, sender TEXT, content TEXT,"
              " timestamp TEXT, is_from_me INTEGER)")
    day = "2026-10-1"
    chats = [(jid, name, f"{day}5T10:00:00") for jid, name in GROUPS]
    chats.append((DM, "Fixture DM", f"{day}5T11:00:00"))
    chats.append((SUITE_DM, "Fixture Suite DM", f"{day}5T11:05:00"))
    c.executemany("INSERT INTO chats VALUES (?,?,?)", chats)
    bodies = [
        ("please share the fixture SOW today?", 0),   # ask -> gated
        ("Haha nice one, see you there", 0),           # banter
        ("FYI https://example.com/fixture-report", 0),  # fyi link
        ("", 0),                                        # empty
        ("noted, will revert by EOD", 1),              # promise, from me
    ]
    rows = []
    for n, (jid, _) in enumerate(GROUPS):
        body, frm = bodies[n % len(bodies)]
        rows.append((f"fx{n}", jid, "Asha", body,
                     f"{day}5T10:{n:02d}:00", frm))
    rows.append(("fxdm", DM, "Asha", "call me when free?",
                 f"{day}5T11:00:00", 0))
    rows.append(("fxsuite", SUITE_DM, "Dev", "share the 3 files",
                 f"{day}5T11:05:00", 1))  # from_me: owner wrote here
    c.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?)", rows)
    c.commit()


def _seed_settings(path: str) -> None:
    w = sqlite3.connect(path)
    w.execute("CREATE TABLE IF NOT EXISTS whatsmeow_chat_settings"
              " (our_jid TEXT, chat_jid TEXT, muted_until INTEGER,"
              " pinned INTEGER, archived INTEGER)")
    w.executemany("INSERT INTO whatsmeow_chat_settings VALUES (NULL,?,NULL,NULL,0)",
                  [(jid,) for jid, _ in GROUPS] + [(DM,), (SUITE_DM,)])
    w.commit()
    w.close()


def ensure_test_snapshot(path: str | None = None) -> str:
    """Return a usable snapshot path, building a synthetic one if needed."""
    target = path or sc.SNAPSHOT_PATH
    if os.path.exists(target):
        try:
            c = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                n = c.execute("SELECT count(*) FROM messages").fetchone()[0]
            finally:
                c.close()
            if n > 0:
                return target
        except Exception:
            pass
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target):
        os.remove(target)
    c = sqlite3.connect(target)
    try:
        _seed_messages(c)
    finally:
        c.close()
    _seed_settings(os.path.join(os.path.dirname(target), "whatsapp.db"))
    return target


if __name__ == "__main__":
    print(ensure_test_snapshot())
