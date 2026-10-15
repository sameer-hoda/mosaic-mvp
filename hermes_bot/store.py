"""mosaic.db — items, aliases, drafts, chat prefs, event log, config kv.

Path via env MOSAIC_DATA_DIR (default /data). The WhatsApp snapshot is
never touched here; this module owns only the mosaic.db file.
"""

import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path

ALIAS_TTL_S = 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  chat_jid TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'ask',
  what TEXT NOT NULL,
  context TEXT DEFAULT '',
  owner TEXT DEFAULT '',
  requester TEXT DEFAULT '',
  due TEXT DEFAULT '',
  status TEXT DEFAULT 'open',
  topic TEXT DEFAULT 'general',
  first_seen REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS item_alias (
  brief_no INTEGER NOT NULL,
  alias TEXT NOT NULL,
  item_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (brief_no, alias)
);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_jid TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at REAL NOT NULL,
  sent_at REAL
);
CREATE TABLE IF NOT EXISTS chat_pref (
  chat_jid TEXT PRIMARY KEY,
  muted INTEGER NOT NULL DEFAULT 0,
  last_scanned_msg_id TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS event_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  model TEXT DEFAULT '',
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  detail TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS config (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def db_path() -> Path:
    data_dir = os.getenv("MOSAIC_DATA_DIR", "/data")
    return Path(data_dir) / "mosaic.db"


def connect(path: "str | Path | None" = None) -> sqlite3.Connection:
    p = Path(path) if path is not None else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _norm_what(what: str) -> str:
    text = what.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,!?;:'\"")[:120]


def stable_item_id(chat_jid: str, what: str, owner: str = "") -> str:
    """Deterministic id so the same item keeps its id across briefs."""
    key = f"{chat_jid}|{_norm_what(what)}|{owner.strip().lower()}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def upsert_item(conn: sqlite3.Connection, item: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    item_id = item.get("id") or stable_item_id(
        item.get("chat_jid", ""), item.get("what", ""), item.get("owner", "")
    )
    row = conn.execute("SELECT id, first_seen FROM items WHERE id = ?", (item_id,)).fetchone()
    if row:
        conn.execute(
            """UPDATE items SET chat_jid=?, kind=?, what=?, context=?, owner=?,
               requester=?, due=?, status=?, topic=?, updated_at=? WHERE id=?""",
            (
                item.get("chat_jid", ""), item.get("kind", "ask"), item.get("what", ""),
                item.get("context", ""), item.get("owner", ""), item.get("requester", ""),
                item.get("due", ""), item.get("status", "open"), item.get("topic", "general"),
                now, item_id,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO items (id, chat_jid, kind, what, context, owner,
               requester, due, status, topic, first_seen, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id, item.get("chat_jid", ""), item.get("kind", "ask"),
                item.get("what", ""), item.get("context", ""), item.get("owner", ""),
                item.get("requester", ""), item.get("due", ""), item.get("status", "open"),
                item.get("topic", "general"), now, now,
            ),
        )
    conn.commit()
    return get_item(conn, item_id)


def get_item(conn: sqlite3.Connection, item_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def set_alias_map(conn: sqlite3.Connection, brief_no: int, mapping: dict,
                  now: float | None = None) -> None:
    """Record brief_no -> {alias: item_id} map for 24 h resolution."""
    now = time.time() if now is None else now
    for alias, item_id in mapping.items():
        conn.execute(
            """INSERT INTO item_alias (brief_no, alias, item_id, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (brief_no, alias)
               DO UPDATE SET item_id=excluded.item_id, created_at=excluded.created_at""",
            (brief_no, str(alias), item_id, now),
        )
    conn.commit()


def resolve_alias(conn: sqlite3.Connection, brief_no: int, alias: str,
                  now: float | None = None) -> dict | None:
    """Resolve a brief alias to its item; None when unknown or expired."""
    now = time.time() if now is None else now
    row = conn.execute(
        "SELECT item_id, created_at FROM item_alias WHERE brief_no=? AND alias=?",
        (brief_no, str(alias)),
    ).fetchone()
    if not row:
        return None
    if now - row["created_at"] > ALIAS_TTL_S:
        return None
    return get_item(conn, row["item_id"])


def save_draft(conn: sqlite3.Connection, chat_jid: str, text: str,
               now: float | None = None) -> int:
    now = time.time() if now is None else now
    cur = conn.execute(
        "INSERT INTO drafts (chat_jid, text, created_at) VALUES (?, ?, ?)",
        (chat_jid, text, now),
    )
    conn.commit()
    return cur.lastrowid


def get_cursor(conn: sqlite3.Connection, chat_jid: str) -> str:
    row = conn.execute(
        "SELECT last_scanned_msg_id FROM chat_pref WHERE chat_jid = ?", (chat_jid,)
    ).fetchone()
    return row["last_scanned_msg_id"] if row and row["last_scanned_msg_id"] else ""


def set_cursor(conn: sqlite3.Connection, chat_jid: str, msg_id: str) -> None:
    conn.execute(
        """INSERT INTO chat_pref (chat_jid, muted, last_scanned_msg_id)
           VALUES (?, 0, ?)
           ON CONFLICT (chat_jid)
           DO UPDATE SET last_scanned_msg_id=excluded.last_scanned_msg_id""",
        (chat_jid, str(msg_id)),
    )
    conn.commit()


def set_muted(conn: sqlite3.Connection, chat_jid: str, muted: bool) -> None:
    conn.execute(
        """INSERT INTO chat_pref (chat_jid, muted, last_scanned_msg_id)
           VALUES (?, ?, '')
           ON CONFLICT (chat_jid)
           DO UPDATE SET muted=excluded.muted""",
        (chat_jid, 1 if muted else 0),
    )
    conn.commit()


def is_muted(conn: sqlite3.Connection, chat_jid: str) -> bool:
    row = conn.execute("SELECT muted FROM chat_pref WHERE chat_jid = ?", (chat_jid,)).fetchone()
    return bool(row and row["muted"])


def log_event(conn: sqlite3.Connection, kind: str, model: str = "",
              input_tokens: int = 0, output_tokens: int = 0,
              detail: str = "") -> int:
    cur = conn.execute(
        """INSERT INTO event_log (ts, kind, model, input_tokens, output_tokens, detail)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (time.time(), kind, model, int(input_tokens), int(output_tokens), detail),
    )
    conn.commit()
    return cur.lastrowid


def config_get(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def config_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
