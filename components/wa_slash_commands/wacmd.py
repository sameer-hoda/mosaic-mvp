#!/usr/bin/env python3
"""
wacmd.py
─────────────────────
Standalone slash command handler for WhatsApp bridge.
Invoked by the Go bridge as a CLI:
  python3 wacmd.py <chat_jid> <sender_jid> <command_text>

Features owner-protection mechanics so it only responds to the verified owner JID.
Commands: /sotu  /pending  /stats  /recap  /eli5  /help
"""

import sys
import os
import argparse
import logging
import sqlite3
import time
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

# ── Path setup ────────────────────────────────────────────────────────────────
_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)
load_dotenv(os.path.join(_dir, ".env"), override=True)

from engine import (
    gemini_json, gemini_text,
    cache_read, cache_write,
    get_messages, format_messages_ist, get_activity_stats,
    utc_to_ist, resolve_send_jid,
)
from formatter import fmt_sotu, fmt_pending, fmt_stats, fmt_recap

_base_url = os.getenv("WA_API_URL", "http://127.0.0.1:8081").rstrip("/")
API_URL = _base_url if _base_url.endswith("/api/send") else f"{_base_url}/api/send"

import json
from pathlib import Path

_setup_file = Path(os.getenv("STORE_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "hermes_bot", "store"))) / "setup.json"
_owner_from_setup = ""
if _setup_file.exists():
    try:
        _setup = json.loads(_setup_file.read_text())
        _owner_from_setup = _setup.get("own_phone", "")
    except:
        pass

OWNER_PHONE = os.getenv("OWNER_PHONE_NUMBER", "").strip().replace("+", "") or _owner_from_setup
OWNER_JID = f"{OWNER_PHONE}@s.whatsapp.net" if OWNER_PHONE else None

log_path = os.path.join(_dir, "slash_cmd.log")
logging.basicConfig(
    filename=log_path,
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M",
)


# ── WA send ───────────────────────────────────────────────────────────────────

def _print_context_info(msgs, command):
    if not msgs:
        return
    earliest = msgs[0][2]
    latest = msgs[-1][2]
    earliest_str = earliest.strftime("%Y-%m-%d %H:%M") if earliest else "??"
    print(f"📚 [{command}] Context Loaded: {len(msgs):,} messages (Earliest: {earliest_str})")

def send_message(jid: str, text: str) -> None:
    # Resolve @lid JIDs to @s.whatsapp.net before sending.
    send_to = resolve_send_jid(jid)
    try:
        resp = requests.post(API_URL, json={"recipient": send_to, "message": text}, timeout=10)
        if resp.status_code == 200:
            print(f"✅ Sent to {send_to}" + (f" (resolved from {jid})" if send_to != jid else ""))
        else:
            print(f"⚠️ Send failed {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"❌ Send error: {e}")


def log_invocation(command: str, jid: str, detail: str, latency: float) -> None:
    logging.info(f"{command} for {jid[:20]}… → {detail} [{latency:.1f}s]")


# ── /sotu ─────────────────────────────────────────────────────────────────────
def cmd_sotu(jid: str) -> None:
    t0 = time.time()
    cached = cache_read(jid, "sotu")
    if cached:
        send_message(jid, fmt_sotu(cached))
        log_invocation("/sotu", jid, "cache hit", time.time() - t0)
        return

    msgs = get_messages(jid, days=30, limit=2000)
    _print_context_info(msgs, "/sotu")
    if not msgs:
        send_message(jid, "⚠️ Not enough history for a State of the Union.")
        return

    context = format_messages_ist(msgs)
    prior = cache_read(jid, "_sotu_prior") or {}
    prior_block = ""
    if prior.get("top_decisions"):
        prior_block = f"""
Previous run summary:
  Decisions: {'; '.join(prior['top_decisions'][:2])}
  Momentum: {prior.get('current_momentum', '')}

Instruction: In 'compared_to_last' describe what is NEW or CHANGED since last run.
"""

    prompt = f"""You are a sharp executive chief of staff. Produce a 'State of the Union' summary for this group chat.

Chat History:
{context}

Return ONLY valid JSON with these exact fields:
- "group_purpose": What this group is about (short paragraph)
- "big_picture_objective": The big picture objective (short paragraph)
- "key_themes": array of objects, each with "title" and "description" (The main discussion themes and their details)
- "recent_happenings": What's been happening recently (last 2 weeks)
- "key_ask": The one thing asked for recently (if any, otherwise omit or say none)

Output ONLY valid JSON, no markdown.
"""
    fallback = {
        "group_purpose": "Analysis unavailable. Please try again.",
        "big_picture_objective": "",
        "key_themes": [],
        "recent_happenings": "",
        "key_ask": ""
    }
    schema = {
        "type": "object",
        "properties": {
            "group_purpose": {"type": "string"},
            "big_picture_objective": {"type": "string"},
            "key_themes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"}
                    },
                    "required": ["title", "description"]
                }
            },
            "recent_happenings": {"type": "string"},
            "key_ask": {"type": "string"}
        },
        "required": ["group_purpose", "big_picture_objective", "key_themes", "recent_happenings", "key_ask"]
    }
    data = gemini_json(prompt, fallback, schema)
    cache_write(jid, "sotu", data)
    # Save as prior for next run
    cache_write(jid, "_sotu_prior", data)
    send_message(jid, fmt_sotu(data))
    log_invocation("/sotu", jid, f"{len(data.get('top_decisions',[]))} decisions", time.time() - t0)


# ── /pending ──────────────────────────────────────────────────────────────────
def cmd_pending(jid: str) -> None:
    t0 = time.time()
    msgs = get_messages(jid, days=30, limit=500)
    _print_context_info(msgs, "/pending")
    if not msgs:
        send_message(jid, "⚠️ No recent messages to analyse for pending tasks.")
        return

    context = format_messages_ist(msgs)
    prior = cache_read(jid, "pending") or {}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M IST")

    prior_block = ""
    if prior.get("critical_items"):
        prev_items = [f"{i.get('owner','?')}: {i.get('action','?')}" for i in prior["critical_items"][:3]]
        prior_block = f"""
Previous /pending items (from last run):
  {'; '.join(prev_items)}

Instruction: In 'resolved_since_last' list any of these that now appear closed/resolved.
"""

    prompt = f"""You are an executive chief of staff. Review this chat history (which may span up to 30 days). Identify the ongoing projects, tasks, or distinct work streams (initiatives). For each initiative, extract the most important chronological developments, decisions, or roadblocks. Finally, synthesize a clear, single-line "Next Step" that specifies who needs to do what. Be extremely thorough and do not miss any major initiatives discussed.

Chat History:
{context}

Return ONLY valid JSON with:
- "initiatives": array of objects, each with:
    "title": string (Name of the initiative),
    "updates": array of strings (e.g. "1 Apr — Name: Action/Update"),
    "next_steps": string (A line what is expected next on this initiative)

Output ONLY valid JSON, no markdown.
"""
    fallback = {
        "initiatives": []
    }
    schema = {
        "type": "object",
        "properties": {
            "initiatives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "updates": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "next_steps": {"type": "string"}
                    },
                    "required": ["title", "updates", "next_steps"]
                }
            }
        },
        "required": ["initiatives"]
    }
    data = gemini_json(prompt, fallback, schema)
    cache_write(jid, "pending", data)
    send_message(jid, fmt_pending(data))
    log_invocation("/pending", jid, f"{len(data.get('critical_items',[]))} items", time.time() - t0)


# ── /stats ────────────────────────────────────────────────────────────────────
def cmd_stats(jid: str, days: int = 14) -> None:
    t0 = time.time()
    cached = cache_read(jid, "stats")
    if cached:
        send_message(jid, fmt_stats(cached, days))
        log_invocation("/stats", jid, "cache hit", time.time() - t0)
        return

    activity = get_activity_stats(jid, days=days)
    msgs = get_messages(jid, days=days, limit=1000)
    _print_context_info(msgs, "/stats")

    if not activity:
        send_message(jid, f"📊 *Team Stats — Last {days} Days*\n\n_No messages found._")
        return

    context = format_messages_ist(msgs)
    total = sum(r[1] for r in activity)
    activity_json = json.dumps({r[0]: r[1] for r in activity[:10]})

    prompt = f"""Analyse this WhatsApp chat (last {days} days, {total} msgs total) and produce a team stats JSON.

Activity counts (Name: MessageCount):
{activity_json}

Chat sample (for collaboration style):
{context[:8000]}

Return ONLY valid JSON with:
- "participants": array of up to 5 objects (top senders only), each with:
    "name": string,
    "message_count": integer,
    "share_pct": float (percentage of total),
    "role_tag": exactly one of "The Driver" | "The Reviewer" | "The Lurker" | "The Mediator" | "The Escalator",
    "one_liner": string (max 8 words describing their collaboration style)
- "group_health": exactly one of "Active" | "Slowing" | "Stalled" | "Escalating"
- "bottleneck": string (name of person most likely causing delays, or "None")

Output ONLY valid JSON, no markdown.
"""
    fallback = {
        "participants": [{"name": r[0], "message_count": r[1], "share_pct": round(r[1]/total*100, 1),
                          "role_tag": "The Driver", "one_liner": ""} for r in activity[:5]],
        "group_health": "Active",
        "bottleneck": "None",
    }
    schema = {
        "type": "object",
        "properties": {
            "participants": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "message_count": {"type": "integer"},
                        "share_pct": {"type": "number"},
                        "role_tag": {"type": "string", "enum": ["The Driver", "The Reviewer", "The Lurker", "The Mediator", "The Escalator"]},
                        "one_liner": {"type": "string"}
                    },
                    "required": ["name", "message_count", "share_pct", "role_tag", "one_liner"]
                }
            },
            "group_health": {"type": "string", "enum": ["Active", "Slowing", "Stalled", "Escalating"]},
            "bottleneck": {"type": "string"}
        },
        "required": ["participants", "group_health", "bottleneck"]
    }
    data = gemini_json(prompt, fallback, schema)
    cache_write(jid, "stats", data)
    send_message(jid, fmt_stats(data, days))
    log_invocation("/stats", jid, f"{len(data.get('participants',[]))} participants", time.time() - t0)


# ── /recap ────────────────────────────────────────────────────────────────────
def cmd_recap(jid: str) -> None:
    t0 = time.time()
    msgs = get_messages(jid, days=1, limit=2000)
    _print_context_info(msgs, "/recap")
    if not msgs:
        send_message(jid, "📌 *24h Recap*\n\n_No messages in the last 24 hours._")
        return

    context = format_messages_ist(msgs)

    prompt = f"""You are a sharp executive assistant. Produce a chronological 24-hour timeline of key events from this WhatsApp chat. Filter out trivial chatter, generic acknowledgments ("ok", "done", "will do"), and system messages. Only include significant signals: decisions, escalations, commitments, blocks, and data shared. Keep the action summaries concise but highly informative. 

Chat History (last 24h, timestamps in IST):
{context}

Return ONLY valid JSON with:
- "date_str": string (e.g., "14 Apr 2026")
- "events": array of objects in chronological order, each with:
    "time": string (e.g., "3:49 PM"),
    "actor": string (sender name),
    "action": string (what important thing they said/did)
- "summary": string (1-2 line summary at the end)

Output ONLY valid JSON, no markdown.
"""
    fallback = {"events": [], "summary": "Analysis failed.", "date_str": ""}
    schema = {
        "type": "object",
        "properties": {
            "date_str": {"type": "string"},
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "time": {"type": "string"},
                        "actor": {"type": "string"},
                        "action": {"type": "string"}
                    },
                    "required": ["time", "actor", "action"]
                }
            },
            "summary": {"type": "string"}
        },
        "required": ["events", "summary"]
    }
    data = gemini_json(prompt, fallback, schema)
    send_message(jid, fmt_recap(data))
    log_invocation("/recap", jid, f"{data.get('signal_count',0)} events", time.time() - t0)


# ── /eli5 ─────────────────────────────────────────────────────────────────────
def cmd_eli5(jid: str, topic: str) -> None:
    t0 = time.time()
    msgs = get_messages(jid, days=30, limit=500)
    _print_context_info(msgs, "/eli5")
    context = format_messages_ist(msgs)
    topic_str = topic if topic else "the current conversation context"

    prompt = f"""Explain the following to a 30-year-old professional. Be concise, clear, and professional.

Topic: "{topic_str}"

Use the chat history below for context if the topic refers to something discussed there.

Chat History:
{context}

Format your response for WhatsApp:
- Start with this exact header: 💡 *ELI30: {topic_str[:60]}*
- Bold key terms with *asterisks*
- Use _italics_ for caveats or nuance
- Keep to 3-4 short paragraphs max
- No ## headers, no --- dividers, no bullet storms
- Write in plain, direct language — no jargon or filler
"""
    response = gemini_text(prompt)
    send_message(jid, response)
    log_invocation("/eli5", jid, f"topic={topic_str[:30]}", time.time() - t0)


# ── /help ─────────────────────────────────────────────────────────────────────
HELP_TEXT = """\
*Available Commands:* 🤖

*/sotu* — State of the Union: decisions, risks, momentum (last 30 days).
*/pending* — Critical dropped balls & stalled threads with owner names.
*/stats* — Team personas, roles & activity (last 14 days).
*/recap* — 24h timeline: key events only, in IST, noise filtered out.
*/eli5 <topic>* — Explain any topic clearly (professional tone, WA-friendly).
*/help* — This message.\
"""


# ── Security ──────────────────────────────────────────────────────────────────

_LID_MAP_CACHE: dict[str, str] = {}


def _get_wa_db():
    db_path = os.getenv("WHATSAPP_DB_PATH", os.getenv("STORE_DIR", os.path.join(_dir, "..", "..", "hermes_bot", "store")) + "/whatsapp.db")
    if not os.path.exists(db_path):
        db_path = os.getenv("STORE_DIR", os.path.join(_dir, "..", "..", "hermes_bot", "store")) + "/whatsapp.db"
        if not os.path.exists(db_path):
            return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_phone_from_jid(jid: str) -> str | None:
    """Extract phone number from a JID, resolving LID → phone if needed."""
    if not jid:
        return None
    base = jid.split(":")[0].split("@")[0]
    if "@lid" in jid:
        if base in _LID_MAP_CACHE:
            return _LID_MAP_CACHE[base]
        try:
            conn = _get_wa_db()
            if conn:
                row = conn.execute(
                    "SELECT pn FROM whatsmeow_lid_map WHERE lid = ?", (base,)
                ).fetchone()
                if row:
                    _LID_MAP_CACHE[base] = row[0]
                    return row[0]
        except Exception:
            pass
        return None
    return base


def _is_owner(chat_jid: str, sender_jid: str) -> bool:
    if not OWNER_JID:
        return False
    base_owner = OWNER_JID.split(":")[0].split("@")[0]
    base_sender = _resolve_phone_from_jid(sender_jid)
    return base_sender == base_owner


# ── Main dispatch ─────────────────────────────────────────────────────────────
def handle_command(chat_jid: str, sender_jid: str, command_text: str) -> None:
    # ── SECURITY CHECK: Only allow the owner's WhatsApp number to use commands ──
    if OWNER_JID:
        if not _is_owner(chat_jid, sender_jid):
            print(f"[🛡️ Security] Ignoring command from non-owner: {sender_jid}")
            return
    else:
        print("[⚠️ Warning] OWNER_PHONE_NUMBER not set. Responding to everyone!")

    parts = command_text.strip().split(" ", 1)
    command = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    print(f"[v2] {command} for {chat_jid}")

    if command == "/sotu":
        cmd_sotu(chat_jid)
    elif command == "/pending":
        cmd_pending(chat_jid)
    elif command == "/stats":
        cmd_stats(chat_jid, days=14)
    elif command == "/recap":
        cmd_recap(chat_jid)
    elif command == "/eli5":
        cmd_eli5(chat_jid, args)
    elif command == "/help":
        send_message(chat_jid, HELP_TEXT)
    else:
        # Unknown command — silently ignore (bridge may send non-command messages)
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WhatsApp Slash Command CLI")
    parser.add_argument("chat_jid", help="JID of the chat")
    parser.add_argument("sender_jid", help="JID of the sender")
    parser.add_argument("command", help="Command text (e.g. /sotu)")
    args = parser.parse_args()
    handle_command(args.chat_jid, args.sender_jid, args.command)