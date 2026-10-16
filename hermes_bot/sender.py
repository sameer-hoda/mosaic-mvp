import time
import json
import sqlite3
import threading
from pathlib import Path

from hermes_bot import config


def _get_outbox_db():
    db_path = str(config.HERMES_DB_PATH)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            jid TEXT NOT NULL,
            text TEXT NOT NULL,
            queued_at REAL NOT NULL,
            sent_at REAL,
            attempts INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def enqueue_message(jid: str, text: str, *, via_confirmed_yes: bool = False):
    if jid.endswith("@g.us") and not via_confirmed_yes:
        raise ValueError(f"refusing to address a group chat: {jid}")
    conn = _get_outbox_db()
    conn.execute(
        "INSERT INTO outbox (jid, text, queued_at) VALUES (?, ?, ?)",
        (jid, text, time.time()),
    )
    conn.commit()
    conn.close()


def enqueue_to_mechat(text: str):
    from hermes_bot.db import get_mechat_chat_jid
    enqueue_message(get_mechat_chat_jid(), text)


def flush_pending():
    import requests
    conn = _get_outbox_db()
    rows = conn.execute(
        "SELECT id, jid, text, attempts FROM outbox WHERE sent_at IS NULL ORDER BY id"
    ).fetchall()

    if not rows:
        conn.close()
        return

    url = f"{config.BRIDGE_URL}/api/send"
    sent_count = 0

    import os

    headers = {}
    token = os.getenv("MOSAIC_BRIDGE_TOKEN", "")
    if token:
        headers["X-Bridge-Token"] = token

    for row in rows:
        msg_id, jid, text, attempts = row
        try:
            resp = requests.post(
                url,
                json={"recipient": jid, "message": text},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200 and resp.json().get("success"):
                conn.execute(
                    "UPDATE outbox SET sent_at = ? WHERE id = ?",
                    (time.time(), msg_id),
                )
                sent_count += 1
            else:
                _handle_failure(conn, msg_id, jid, attempts)
        except Exception:
            _handle_failure(conn, msg_id, jid, attempts)

    conn.commit()
    conn.close()

    if sent_count:
        print(f"[sender] Flushed {sent_count} pending message(s)")


def _handle_failure(conn, msg_id, jid, attempts):
    import logging
    new_attempts = attempts + 1
    if new_attempts >= 3:
        conn.execute("DELETE FROM outbox WHERE id = ?", (msg_id,))
        # Destination only — message text is never logged.
        logging.getLogger("mosaic.sender").warning(
            "sender: dropping message for %s after %d failed flushes",
            jid, new_attempts)
    else:
        conn.execute(
            "UPDATE outbox SET attempts = ? WHERE id = ?",
            (new_attempts, msg_id),
        )


def _flush_loop():
    while True:
        time.sleep(2)
        try:
            flush_pending()
        except Exception:
            pass


_flush_thread = None


def start_flush_thread():
    global _flush_thread
    if _flush_thread and _flush_thread.is_alive():
        return
    _flush_thread = threading.Thread(target=_flush_loop, daemon=True)
    _flush_thread.start()
    print("[sender] Flush thread started.")