"""Self-chat Q&A: free-text questions answered from items + recent messages.

Stub retrieval this week: keyword overlap over the items snapshot (and,
when a snapshot connection is supplied, over recent message text).
No network, no sends — returns a plain-text answer.
"""

from __future__ import annotations

import re
import sqlite3

_WORD_RE = re.compile(r"[a-z0-9']+", re.I)
_STOP = {"the", "a", "an", "is", "are", "was", "were", "what", "when", "where",
        "who", "whom", "whose", "why", "how", "which", "do", "does", "did",
        "can", "could", "should", "would", "will", "me", "my", "for", "on",
        "in", "of", "to", "and", "or", "any", "there", "it", "its", "with",
        "about", "have", "has", "had", "been", "please", "tell", "give", "show"}


def keywords(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text or "") if len(w) > 2} - _STOP


def _score(item: dict, keys: set[str]) -> int:
    hay = f"{item.get('what', '')} {item.get('context', '')} {item.get('topic', '')}".lower()
    words = set(_WORD_RE.findall(hay))
    return len(keys & words)


def fetch_all_items(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM items").fetchall()]


def fetch_recent_messages(snap_conn: sqlite3.Connection | None, limit: int = 200) -> list[dict]:
    if snap_conn is None:
        return []
    try:
        rows = snap_conn.execute(
            "SELECT chat_jid, sender, content FROM messages ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def answer_question(store_conn: sqlite3.Connection, question: str,
                    snap_conn: sqlite3.Connection | None = None,
                    limit: int = 3) -> str:
    """Answer a free-text question from tracked items + recent messages."""
    keys = keywords(question)
    if not keys:
        return "Say a little more — which person, topic, or chat is this about?"
    scored = [(_score(i, keys), i) for i in fetch_all_items(store_conn)]
    hits = [(s, i) for s, i in scored if s > 0]
    hits.sort(key=lambda t: (-t[0], str(t[1].get("what", ""))))
    lines: list[str] = []
    for _, item in hits[:limit]:
        state = item.get("status", "open")
        lines.append(f"- [{item.get('chat_jid', '')}] {item.get('what', '')} "
                     f"(owner {item.get('owner') or '?'}; {state})")
    if snap_conn is not None:
        for m in fetch_recent_messages(snap_conn):
            body = (m.get("content") or "").lower()
            if sum(1 for k in keys if k in body) >= 2:
                lines.append(f"- said in {m.get('chat_jid', '')} by "
                             f"{m.get('sender', '')}: {(m.get('content') or '')[:100]}")
                if len(lines) >= limit + 2:
                    break
    if not lines:
        return "Nothing found on that yet — no tracked item or recent message matches."
    return "Here's what I found:\n" + "\n".join(lines[:limit + 2])
