#!/usr/bin/env python3
"""
engine.py
───────────────────
Shared engine for slash command V2:
  - gemini_json()          → call Gemini with JSON schema enforcement + retry
  - gemini_text()          → freeform Gemini call (for /eli5)
  - cache_read/write       → per-JID TTL-aware state cache
  - resolve_send_jid()     → convert @lid JIDs → @s.whatsapp.net for reliable delivery
  - get_messages()         → fetch + format messages as IST-timestamped text
  - get_activity_stats()   → raw message count per sender (for /stats)
  - utc_to_ist()           → datetime helper
"""

import os
import re
import json
import sqlite3
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

import google.generativeai as genai
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────
_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_dir, ".env"))

GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL      = "gemini-3.1-flash-lite-preview"
MESSAGES_DB_PATH  = os.getenv("MESSAGES_DB_PATH", os.path.join(_dir, "store", "messages.db"))
WHATSAPP_DB_PATH  = os.getenv("WHATSAPP_DB_PATH", os.path.join(_dir, "store", "whatsapp.db"))

# Cache directory: ./cache/<jid_hash>/<command>.json
CACHE_BASE = os.path.join(_dir, "cache")

# Cache TTLs in minutes per command
CACHE_TTL = {
    "sotu":    120,   # 2h
    "pending":  60,   # 1h  
    "stats":   240,   # 4h
    "recap":    60,
    "eli5":      0,   # never cache (always fresh)
}

# Logging
log_path = os.path.join(_dir, "slash_cmd.log")
logging.basicConfig(
    filename=log_path,
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M",
)

# ── Gemini setup ─────────────────────────────────────────────────────────────
_model = None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    _model = genai.GenerativeModel(GEMINI_MODEL)
else:
    logging.error("GEMINI_API_KEY not set — AI will be unavailable.")


def gemini_json(prompt: str, fallback: dict, schema: Optional[dict] = None) -> dict:
    """Call Gemini expecting a JSON response. Retries once on parse failure.
    Returns fallback dict if both attempts fail."""
    if not _model:
        return fallback
    
    config = {"response_mime_type": "application/json"}
    if schema:
        config["response_schema"] = schema

    print(f"🤖 [Gemini API] Requesting JSON... (Payload: {len(prompt):,} chars)")
    for attempt in range(2):
        try:
            resp = _model.generate_content(
                prompt,
                generation_config=config,
            )
            return json.loads(resp.text)
        except json.JSONDecodeError as e:
            print(f"⚠️ [Gemini API] JSON parse failed: {e}")
            logging.warning(f"JSON parse failed (attempt {attempt+1}): {e}")
        except Exception as e:
            print(f"❌ [Gemini API] Error: {e}")
            logging.error(f"Gemini call error (attempt {attempt+1}): {e}")
            break
    return fallback


def gemini_text(prompt: str) -> str:
    """Freeform Gemini call — returns raw text (used for /eli5)."""
    if not _model:
        return "AI unavailable. Please check GEMINI_API_KEY."
    print(f"🤖 [Gemini API] Requesting Text... (Payload: {len(prompt):,} chars)")
    try:
        resp = _model.generate_content(prompt)
        return resp.text
    except Exception as e:
        print(f"❌ [Gemini API] Error: {e}")
        logging.error(f"Gemini text error: {e}")
        return f"AI Error: {e}"


# ── Cache helpers ─────────────────────────────────────────────────────────────
def _cache_path(jid: str, command: str) -> str:
    jid_hash = hashlib.md5(jid.encode()).hexdigest()[:12]
    folder = os.path.join(CACHE_BASE, jid_hash)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{command}.json")


def cache_read(jid: str, command: str) -> Optional[dict]:
    """Return cached data if it exists and hasn't expired. None otherwise."""
    ttl = CACHE_TTL.get(command, 0)
    if ttl == 0:
        return None
    path = _cache_path(jid, command)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        written_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        if datetime.now() - written_at < timedelta(minutes=ttl):
            return data.get("payload")
    except Exception:
        pass
    return None


def cache_write(jid: str, command: str, payload: dict) -> None:  # noqa
    """Persist payload to cache."""
    ttl = CACHE_TTL.get(command, 0)
    if ttl == 0:
        return
    path = _cache_path(jid, command)
    try:
        with open(path, "w") as f:
            json.dump({"_cached_at": datetime.now().isoformat(), "payload": payload}, f)
    except Exception as e:
        logging.warning(f"Cache write failed: {e}")


# ── Time helpers ──────────────────────────────────────────────────────────────
IST_OFFSET = timedelta(hours=5, minutes=30)


def utc_to_ist(dt):  # datetime -> datetime
    """Convert naive UTC datetime to IST."""
    return dt + IST_OFFSET


def parse_ts(ts) -> Optional[datetime]:
    """Parse a SQLite timestamp (string or datetime) into a UTC datetime."""
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        try:
            clean = ts.replace("T", " ").split("+")[0].split(".")[0].strip()
            return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if isinstance(ts, (int, float)):
        # Unix timestamp — milliseconds if > 20 billion
        try:
            return datetime.utcfromtimestamp(ts / 1000 if ts > 20_000_000_000 else ts)
        except Exception:
            return None
    return None


# ── LID / JID resolution helpers ─────────────────────────────────────────────

def _wa_db_conn():
    """Open a read-only connection to whatsapp.db only."""
    if not os.path.exists(WHATSAPP_DB_PATH):
        return None
    try:
        return sqlite3.connect(f"file:{WHATSAPP_DB_PATH}?mode=ro", uri=True)
    except Exception:
        return None


def resolve_send_jid(jid: str) -> str:
    """Resolve a @lid JID to its @s.whatsapp.net equivalent for reliable delivery.

    The whatsmeow Go bridge occasionally chokes on @lid JIDs sent via the HTTP API
    (participant list hash mismatch), silently dropping the message on some devices.
    Sending to the real phone-number JID fixes this.

    @g.us group JIDs are returned unchanged.
    """
    if not jid or "@g.us" in jid or "@s.whatsapp.net" in jid:
        return jid  # already fine

    if "@lid" not in jid and not jid.replace("-", "").isdigit():
        return jid  # unknown format, pass through

    # Strip @lid / hyphens / device IDs to get the raw numeric LID
    lid = jid.replace("@lid", "").replace("-", "").strip()
    lid = lid.split(":")[0]

    conn = _wa_db_conn()
    if not conn:
        return jid
    try:
        cur = conn.cursor()
        cur.execute("SELECT pn FROM whatsmeow_lid_map WHERE lid = ?", (lid,))
        row = cur.fetchone()
        if row and row[0]:
            resolved = f"{row[0]}@s.whatsapp.net"
            logging.info(f"resolve_send_jid: {jid} → {resolved}")
            return resolved
        # Also try with the full LID string (some entries store it with hyphens)
        cur.execute("SELECT pn FROM whatsmeow_lid_map WHERE lid = ?", (jid.replace("@lid", ""),))
        row = cur.fetchone()
        if row and row[0]:
            resolved = f"{row[0]}@s.whatsapp.net"
            logging.info(f"resolve_send_jid (full): {jid} → {resolved}")
            return resolved
    except Exception as e:
        logging.warning(f"resolve_send_jid error for {jid}: {e}")
    finally:
        conn.close()
    return jid  # fallback: send as-is


def _resolve_sender_name(raw_name: str) -> str:
    """Post-process a SQL-returned sender name.
    If it looks like a raw LID, phone number, or JID, resolve it to a human name.
    """
    if not raw_name:
        return "Unknown"

    # Already looks like a real name (has letters, not just digits/@)
    stripped = raw_name.replace("@lid", "").replace("@s.whatsapp.net", "").strip()
    if not stripped.replace("-", "").isdigit():
        # Contains non-digit chars beyond @/- — likely a real name already
        return raw_name

    # It's a raw LID / phone number — resolve it
    conn = _wa_db_conn()
    if not conn:
        return raw_name
    try:
        cur = conn.cursor()
        jid = None

        # Try LID → pn → JID
        clean_lid = stripped.replace("-", "")
        cur.execute("SELECT pn FROM whatsmeow_lid_map WHERE lid = ?", (clean_lid,))
        row = cur.fetchone()
        if row and row[0]:
            jid = f"{row[0]}@s.whatsapp.net"
        else:
            # Treat as phone number directly
            jid = f"{clean_lid}@s.whatsapp.net"

        cur.execute(
            "SELECT COALESCE(push_name, full_name, first_name, business_name) "
            "FROM whatsmeow_contacts WHERE their_jid = ? LIMIT 1",
            (jid,)
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
    except Exception as e:
        logging.warning(f"_resolve_sender_name error for '{raw_name}': {e}")
    finally:
        conn.close()
    return raw_name  # fallback


# ── DB helpers ────────────────────────────────────────────────────────────────
def _get_conn():
    if not os.path.exists(MESSAGES_DB_PATH):
        logging.error(f"DB connect error: messages db {MESSAGES_DB_PATH} not found")
        return None
    try:
        conn = sqlite3.connect(f"file:{MESSAGES_DB_PATH}?mode=ro", uri=True)
        if os.path.exists(WHATSAPP_DB_PATH):
            conn.execute(f"ATTACH DATABASE '{WHATSAPP_DB_PATH}' AS whatsapp_db")
        return conn
    except Exception as e:
        logging.error(f"DB connect error: {e}")
        return None


_MESSAGES_QUERY = """
    SELECT
        COALESCE(
            c_jid.push_name, c_jid.full_name, c_jid.first_name, c_jid.business_name,
            c_lid.push_name, c_lid.full_name, c_lid.first_name, c_lid.business_name,
            ms.sender_jid,
            m.sender
        ) AS sender_name,
        m.content,
        m.timestamp
    FROM messages m
    LEFT JOIN whatsapp_db.whatsmeow_message_secrets ms
           ON m.id = ms.message_id AND m.chat_jid = ms.chat_jid
    LEFT JOIN whatsapp_db.whatsmeow_contacts c_jid ON ms.sender_jid = c_jid.their_jid
    LEFT JOIN whatsapp_db.whatsmeow_lid_map lm ON ms.sender_jid = lm.lid
    LEFT JOIN whatsapp_db.whatsmeow_contacts c_lid
           ON (lm.pn || '@s.whatsapp.net') = c_lid.their_jid
    WHERE m.chat_jid = ? AND m.timestamp > ? AND m.content IS NOT NULL AND TRIM(m.content) != ''
    ORDER BY m.timestamp DESC
    LIMIT ?
"""

_FALLBACK_QUERY = """
    SELECT sender, content, timestamp FROM messages
    WHERE chat_jid = ? AND timestamp > ? AND content IS NOT NULL AND content != ''
    ORDER BY timestamp DESC LIMIT ?
"""

_STATS_QUERY = """
    SELECT
        COALESCE(
            c_jid.push_name, c_jid.full_name, c_jid.first_name, c_jid.business_name,
            c_lid.push_name, c_lid.full_name, c_lid.first_name, c_lid.business_name,
            ms.sender_jid, m.sender
        ) AS sender_name,
        COUNT(*) AS cnt,
        MIN(m.timestamp) AS first_msg,
        MAX(m.timestamp) AS last_msg
    FROM messages m
    LEFT JOIN whatsapp_db.whatsmeow_message_secrets ms
           ON m.id = ms.message_id AND m.chat_jid = ms.chat_jid
    LEFT JOIN whatsapp_db.whatsmeow_contacts c_jid ON ms.sender_jid = c_jid.their_jid
    LEFT JOIN whatsapp_db.whatsmeow_lid_map lm ON ms.sender_jid = lm.lid
    LEFT JOIN whatsapp_db.whatsmeow_contacts c_lid
           ON (lm.pn || '@s.whatsapp.net') = c_lid.their_jid
    WHERE m.chat_jid = ? AND m.timestamp > ?
    GROUP BY sender_name ORDER BY cnt DESC
"""


def get_messages(jid: str, days: int = 30, limit: int = 2000) -> List[Tuple]:
    """Returns list of (sender, content, utc_datetime) tuples, oldest first.
    Sender names are post-processed through _resolve_sender_name() to convert
    raw LID / phone fallbacks into human-readable names.
    """
    conn = _get_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cutoff = datetime.now() - timedelta(days=days)
        try:
            cur.execute(_MESSAGES_QUERY, (jid, cutoff, limit))
        except sqlite3.OperationalError:
            cur.execute(_FALLBACK_QUERY, (jid, cutoff, limit))
        rows = cur.fetchall()
        result = []
        for sender, content, ts in reversed(rows):
            dt = parse_ts(ts)
            resolved = _resolve_sender_name(sender or "")
            result.append((resolved, content, dt))
        return result
    except Exception as e:
        logging.error(f"get_messages error: {e}")
        return []
    finally:
        conn.close()


# Pattern: WhatsApp stores @-mentions as @DIGITS in message content (LID mentions)
_MENTION_RE = re.compile(r'@(\d{9,20})')


def _build_lid_name_map(lids):
    """Bulk-resolve a set of LID strings to names in a single DB transaction.
    Returns {lid_str: name_str} for all found entries.
    """
    if not lids:
        return {}
    conn = _wa_db_conn()
    if not conn:
        return {}
    result = {}
    try:
        cur = conn.cursor()
        placeholders = ",".join("?" * len(lids))
        # Step 1: LID → phone number
        cur.execute(
            f"SELECT lid, pn FROM whatsmeow_lid_map WHERE lid IN ({placeholders})",
            list(lids)
        )
        lid_to_pn = {row[0]: row[1] for row in cur.fetchall()}

        # Step 2: phone numbers → names (one query)
        pns = list(lid_to_pn.values())
        if pns:
            jids = [f"{pn}@s.whatsapp.net" for pn in pns]
            ph2 = ",".join("?" * len(jids))
            cur.execute(
                f"SELECT their_jid, COALESCE(push_name, full_name, first_name, business_name) "
                f"FROM whatsmeow_contacts WHERE their_jid IN ({ph2})",
                jids
            )
            jid_to_name = {row[0]: row[1] for row in cur.fetchall()}

            for lid, pn in lid_to_pn.items():
                name = jid_to_name.get(f"{pn}@s.whatsapp.net")
                if name:
                    result[lid] = name
    except Exception as e:
        logging.warning(f"_build_lid_name_map error: {e}")
    finally:
        conn.close()
    return result


def clean_messages_content(messages):
    """Resolve all @LID mention patterns in a list of (sender, content, dt) tuples.

    Builds a single bulk LID-→name map from all mentions found across all messages,
    then replaces them in one pass. Far faster than one DB call per mention.
    Returns a new list with resolved content.
    """
    # Collect all unique LIDs mentioned across all messages
    all_lids = set()
    for _, content, _ in messages:
        if content and '@' in content:
            all_lids.update(_MENTION_RE.findall(content))

    lid_map = _build_lid_name_map(all_lids) if all_lids else {}

    def _replace(match):
        lid = match.group(1)
        name = lid_map.get(lid)
        return f"@{name}" if name else match.group(0)

    result = []
    for sender, content, dt in messages:
        if content and '@' in content and all_lids:
            content = _MENTION_RE.sub(_replace, content)
        result.append((sender, content, dt))
    return result


# Keep single-string variant for any direct callers
def clean_message_content(text, lid_map=None):
    """Replace @LID mention patterns in a single message string.
    Prefer clean_messages_content() for bulk use.
    """
    if not text or '@' not in text:
        return text
    lids = set(_MENTION_RE.findall(text))
    if not lids:
        return text
    _map = lid_map if lid_map is not None else _build_lid_name_map(lids)

    def _replace(match):
        name = _map.get(match.group(1))
        return f"@{name}" if name else match.group(0)

    return _MENTION_RE.sub(_replace, text)


def format_messages_ist(messages, max_chars=100000) -> str:
    """Format messages as [HH:MM IST] Sender: content lines.
    Bulk-resolves all @LID mentions in one DB round-trip.
    Truncates to `max_chars` to prevent API payload errors, keeping the most recent.
    """
    messages = clean_messages_content(messages)  # bulk mention resolution
    lines = []
    current_chars = 0
    
    # Process from newest to oldest to prioritize recent context
    for sender, content, dt in reversed(messages):
        if dt:
            ist = utc_to_ist(dt)
            ts_str = ist.strftime("%H:%M IST")
        else:
            ts_str = "??"
            
        line = f"[{ts_str}] {sender}: {content or ''}"
        
        if current_chars + len(line) > max_chars:
            lines.append(f"... [Truncated {len(messages) - len(lines)} older messages to fit API limits]")
            break
            
        lines.append(line)
        current_chars += len(line) + 1
        
    return "\n".join(reversed(lines))


def get_activity_stats(jid: str, days: int = 14) -> List[Tuple]:
    """Returns [(sender_name, count, first_msg_dt, last_msg_dt), ...] sorted desc.
    Sender names are resolved through _resolve_sender_name().
    """
    conn = _get_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cutoff = datetime.now() - timedelta(days=days)
        try:
            cur.execute(_STATS_QUERY, (jid, cutoff))
        except sqlite3.OperationalError:
            cur.execute(
                "SELECT sender, COUNT(*), MIN(timestamp), MAX(timestamp) "
                "FROM messages WHERE chat_jid = ? AND timestamp > ? "
                "GROUP BY sender ORDER BY COUNT(*) DESC",
                (jid, cutoff),
            )
        rows = cur.fetchall()
        return [(_resolve_sender_name(r[0] or ""), r[1], parse_ts(r[2]), parse_ts(r[3])) for r in rows]
    except Exception as e:
        logging.error(f"get_activity_stats error: {e}")
        return []
    finally:
        conn.close()
