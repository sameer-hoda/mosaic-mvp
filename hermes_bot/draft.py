#!/usr/bin/env python3
"""Draft N: three grounded reply options (A/B/C) for a brief item.

Strict schema: every option carries a ``grounded`` boolean. A draft is
grounded only when every date/number token it contains already appears
in the thread — drafts never invent dates or numbers.
"""

import logging
import re

log = logging.getLogger("hermes_bot.draft")

LABELS = ("A", "B", "C")

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "options": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"$ref": "#/option"},
        },
    },
    "required": ["options"],
    "option": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": ["A", "B", "C"]},
            "text": {"type": "string"},
            "grounded": {"type": "boolean"},
        },
        "required": ["label", "text", "grounded"],
    },
}

# Dates (25 Dec, Dec 25, 25/12, 2026-…) and numbers (3, 5pm, 50,000, ₹5000).
_TOKEN_RE = re.compile(r"""
    \b\d{1,2}\s?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b
  | \b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s?\d{1,2}\b
  | \b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b
  | [₹$€]?\d[\d,]*(?:\.\d+)?\b
""", re.IGNORECASE | re.VERBOSE)


def _norm_token(tok: str) -> str:
    return re.sub(r"\s+", " ", tok.strip().lower().rstrip(",."))


def extract_tokens(text: str) -> set:
    """Return the set of date/number tokens in text."""
    return {_norm_token(t) for t in _TOKEN_RE.findall(text or "") if _norm_token(t)}


def check_grounded(thread_text: str, draft_text: str) -> dict:
    """Every date/number in the draft must already occur in the thread."""
    allowed = extract_tokens(thread_text)
    invented = sorted(t for t in extract_tokens(draft_text) if t not in allowed)
    return {"grounded": not invented, "invented": invented}


def _heuristic_options(item: dict, thread_lines: list) -> list:
    what = (item.get("what") or "").strip().rstrip(".")
    opener = thread_lines[0].split(":", 1)[-1].strip() if thread_lines else ""
    cands = [
        f"Got it — {what[0].lower() + what[1:] if what else 'noted'}.",
        f"On it: {what[0].lower() + what[1:] if what else 'noted'} — will confirm here.",
        f"Noted{f' re: “{opener[:60]}”' if opener else ''} — {what[0].lower() + what[1:] if what else 'on it'}.",
    ]
    seen, out = set(), []
    for text in cands:
        if text not in seen:
            seen.add(text)
            out.append(text)
    while len(out) < 3:
        out.append(f"{out[0]} (alt {len(out) + 1})")
    return out[:3]


def build_drafts(item: dict, thread_lines: list, llm=None) -> dict:
    """Return {"options": [{label, text, grounded}]} — never touches network.

    `llm`, when given, is a stub-friendly callable(prompt) -> [3 texts];
    each candidate is grounding-checked and flagged, never silently fixed.
    """
    thread_text = "\n".join(thread_lines or [])
    if llm is None:
        texts = _heuristic_options(item, thread_lines or [])
    else:
        try:
            texts = list(llm(thread_text))[:3]
        except Exception as exc:
            log.warning("draft: llm failed (%s)", exc)
            texts = _heuristic_options(item, thread_lines or [])
        while len(texts) < 3:
            texts += _heuristic_options(item, thread_lines or [])[: 3 - len(texts)]
    options = []
    for label, text in zip(LABELS, texts[:3]):
        res = check_grounded(thread_text, text)
        if not res["grounded"]:
            log.warning("draft: option %s invents %s", label, res["invented"])
        options.append({"label": label, "text": text, "grounded": res["grounded"]})
    return {"options": options}


def format_draft_reply(ref: str, chat_name: str, what: str, options: list) -> str:
    """Render the A/B/C reply shown in self-chat."""
    lines = [f"✏️ Draft for {chat_name} — {what}"]
    for opt in options:
        flag = "" if opt.get("grounded", True) else " ⚠️ ungrounded"
        lines.append(f"({opt['label']}) {opt['text']}{flag}")
        lines.append(f"    → send {ref}{opt['label']}")
    return "\n".join(lines)


_REF_SCHEMA = """
CREATE TABLE IF NOT EXISTS draft_refs (
  ref TEXT PRIMARY KEY,
  chat_jid TEXT NOT NULL,
  chat_name TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""

_REF_RE = re.compile(r"^([A-Za-z0-9]+)([A-Ca-c])$")


def init_refs(conn) -> None:
    conn.executescript(_REF_SCHEMA)
    conn.commit()


def remember_options(conn, ref_base: str, chat_jid: str, chat_name: str,
                     options: list, now: float | None = None) -> list:
    """Persist A/B/C options; returns refs like ["2A", "2B", "2C"]."""
    import time as _time
    from hermes_bot import store as _store
    now = _time.time() if now is None else now
    init_refs(conn)
    refs = []
    for opt in options:
        ref = f"{ref_base}{opt['label']}".upper()
        _store.save_draft(conn, chat_jid, opt["text"], now=now)
        conn.execute(
            """INSERT INTO draft_refs (ref, chat_jid, chat_name, text, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (ref) DO UPDATE SET
                 chat_jid=excluded.chat_jid, chat_name=excluded.chat_name,
                 text=excluded.text, created_at=excluded.created_at""",
            (ref, chat_jid, chat_name, opt["text"], now),
        )
        refs.append(ref)
    conn.commit()
    return refs


def lookup_option(conn, ref: str) -> dict | None:
    """Resolve "2B" → {chat_jid, chat_name, text}; None when unknown."""
    if not _REF_RE.match((ref or "").strip()):
        return None
    init_refs(conn)
    row = conn.execute(
        "SELECT chat_jid, chat_name, text FROM draft_refs WHERE ref = ?",
        (ref.strip().upper(),),
    ).fetchone()
    return dict(row) if row else None


def lookup_item(conn, alias: str) -> tuple | None:
    """Freshest brief alias → (brief_no, item). None when unknown/expired."""
    import time as _time
    from hermes_bot import store as _store
    row = conn.execute(
        """SELECT brief_no, item_id, created_at FROM item_alias
           WHERE alias = ? ORDER BY created_at DESC LIMIT 1""",
        (str(alias),),
    ).fetchone()
    if not row:
        return None
    if _time.time() - row["created_at"] > _store.ALIAS_TTL_S:
        return None
    item = _store.get_item(conn, row["item_id"])
    return (row["brief_no"], item) if item else None
