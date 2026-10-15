#!/usr/bin/env python3
"""
ops/bot_engine.py — Active mode: nudges, closure detection, ETA parsing
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta
from typing import Optional, List

from ops.models import Card, CardStatus, CardMode, ProgressLogEntry, Message
from ops.db import save_card
from ops.llm import gemini_json
from ops.scanner import get_group_messages

_IST_OFFSET = timedelta(hours=5, minutes=30)


def _now_ist() -> datetime:
    return datetime.utcnow() + _IST_OFFSET


def _is_working_hours(dt: datetime) -> bool:
    """Mon–Fri 08:00–17:00 IST."""
    wd = dt.weekday()
    if wd >= 5:
        return False
    return 8 <= dt.hour < 17


def is_working_hours() -> bool:
    """Check if current time is within working hours (Mon-Fri 08:00-17:00 IST).
    NOTE: For testing on weekends, temporarily return True.
    """
    # TODO: Remove this override after testing
    return True  # _is_working_hours(_now_ist())


def _next_working_hour_start(base: datetime) -> datetime:
    """Return next valid nudge time respecting working hours."""
    t = base + timedelta(minutes=1)
    while not _is_working_hours(t):
        t += timedelta(hours=1)
        if t.hour >= 17 or t.weekday() >= 5:
            t = t.replace(hour=8, minute=0) + timedelta(days=1)
    return t


# ── ETA Parser ──────────────────────────────────────────────────────────────

def parse_eta(text: str, reference_time: Optional[datetime] = None) -> Optional[datetime]:
    """
    Extract ETA from free-form text. Returns absolute datetime.
    T3.4 + T3.5
    """
    ref = reference_time or _now_ist()
    text_lower = text.lower()

    patterns = [
        (r"\bby\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*today\b",
         lambda m, ref: _build_dt(ref, int(m.group(1)), int(m.group(2) or 0), m.group(3))),
        (r"\b(?:by\s+)?tomorrow\s+(?:eod|(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)\b",
         lambda m, ref: _build_dt(ref + timedelta(days=1), int(m.group(1) or 17), int(m.group(2) or 0), m.group(3))),
        (r"\b(?:by\s+)?next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
         lambda m, ref: _next_weekday(ref, m.group(1))),
        (r"\bthis week\b",
         lambda m, ref: _this_friday(ref)),
        (r"\bby\s+eod\b",
         lambda m, ref: _build_dt(ref, 17, 0, "pm") if ref.hour < 17 else _build_dt(ref + timedelta(days=1), 17, 0, "pm")),
        (r"\bin\s+(\d+)\s*hours?\b",
         lambda m, ref: ref + timedelta(hours=int(m.group(1)))),
    ]

    for pattern, builder in patterns:
        m = re.search(pattern, text_lower)
        if m:
            try:
                return builder(m, ref)
            except Exception:
                continue
    return None


def _build_dt(base: datetime, hour: int, minute: int, ampm: Optional[str]) -> datetime:
    if ampm and ampm.lower() == "pm" and hour < 12:
        hour += 12
    if ampm and ampm.lower() == "am" and hour == 12:
        hour = 0
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _next_weekday(ref: datetime, day_name: str) -> datetime:
    days = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}
    target = days.get(day_name, 0)
    days_ahead = (target - ref.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (ref + timedelta(days=days_ahead)).replace(hour=10, minute=0, second=0)


def _this_friday(ref: datetime) -> datetime:
    days_to_fri = (4 - ref.weekday()) % 7
    if days_to_fri == 0:
        days_to_fri = 7
    return (ref + timedelta(days=days_to_fri)).replace(hour=17, minute=0, second=0)


# ── Nudge Composer ───────────────────────────────────────────────────────────

def compose_nudge(card: Card, recent_messages: list = None, wiki_context: str = None) -> str:
    """
    Compose a contextual follow-up message using Gemini with full context.
    Includes last N messages, progress log, wiki context, and previous nudges.
    """
    nudge_num = card.nudge_count + 1
    topic = card.title
    people = card.key_people or []
    has_high_confidence = card.key_people_confidence >= 0.8

    # Build context for Gemini
    context_parts = [f"Topic: {topic}"]
    
    # Add card context
    if card.context:
        context_parts.append(f"Original Context: {card.context[:500]}")
    
    # Add recent messages if provided
    if recent_messages:
        msgs_text = "\n".join(
            f"[{m.timestamp.strftime('%H:%M')}] {m.sender_name}: {m.content[:200]}"
            for m in recent_messages[-10:]  # Last 10 messages
        )
        context_parts.append(f"Recent Group Messages:\n{msgs_text}")
    
    # Add wiki context if provided
    if wiki_context:
        context_parts.append(f"Wiki Context: {wiki_context[:500]}")
    
    # Add progress log (previous replies, ETAs, etc.)
    if card.progress_log:
        log_text = "\n".join(
            f"[{e.ts.strftime('%H:%M')}] {e.event}: {e.detail[:200]}"
            for e in card.progress_log[-5:]  # Last 5 progress events
        )
        context_parts.append(f"Previous Progress:\n{log_text}")
    
    # Add previous nudges to avoid repetition
    if card.nudge_log:
        prev_nudges = "\n".join(
            f"- {n.message_text[:200]}" for n in card.nudge_log[-3:]  # Last 3 nudges
        )
        context_parts.append(f"Previous Nudges Sent:\n{prev_nudges}")
    
    # Add key people info
    if people:
        context_parts.append(f"Key People: {', '.join(people)}")
        if has_high_confidence:
            context_parts.append("High confidence in key people identification.")
        else:
            context_parts.append("Address the team as 'Team' if no high-confidence owner.")

    # Build Gemini prompt
    context_str = "\n\n".join(context_parts)
    
    # Determine tone based on nudge number
    if nudge_num == 1:
        tone = "soft, polite check-in"
    elif nudge_num == 2:
        tone = "more direct, ask about blockers"
    else:
        tone = "escalated, name key people if possible, otherwise address team"
    
    prompt = f"""You are a professional project follow-up assistant. Compose a formal English WhatsApp message to follow up on a task.

Context:
{context_str}

Instructions:
- Tone: {tone}
- Reference specific previous messages or replies if available
- Mention what was last said and who said it
- If an ETA was given, reference it
- Don't repeat what was said in previous nudges
- Keep message concise (under 500 characters)
- Formal English, no slang
- Sign off naturally

Output ONLY the message text, no quotes, no explanation."""

    try:
        message = gemini_json(prompt, {"message": ""}).get("message", "")
        if message:
            return message
    except Exception as e:
        logger.error(f"Gemini nudge composition failed: {e}")

    # Fallback to static templates if Gemini fails
    return _fallback_template(topic, people, nudge_num, has_high_confidence)

def _fallback_template(topic: str, people: list, nudge_num: int, has_high_confidence: bool) -> str:
    """Fallback static templates if Gemini fails."""
    person = people[0] if people and people[0] != "Team" else ""
    templates = {
        1: [  # Soft
            f"Just checking in on {topic} — any updates from the team?",
            f"Hey, wanted to follow up on {topic}. How's it going?",
        ],
        2: [  # Direct
            f"Following up on {topic} — are there any blockers I should know about?",
            f"Quick check: where do we stand on {topic}? Any blockers?",
        ],
        3: [  # Named/Team
            f"{person}, could you share an update on {topic}? Want to make sure this doesn't slip." if has_high_confidence and person else f"Team, need an update on {topic} to keep this moving.",
            f"{person}, following up on {topic} — what's the latest?" if has_high_confidence and person else f"Checking in on {topic} — can someone share where we stand?",
        ],
    }
    import random
    return random.choice(templates.get(nudge_num, templates[3]))


def _pick_template(tone: str, topic: str, people: List[str]) -> str:
    person = people[0] if people and people[0] != "Team" else ""
    templates = {
        "soft": [
            f"Just checking in on {topic} — any updates from the team?",
            f"Hey, wanted to follow up on {topic}. How's it going?",
        ],
        "direct": [
            f"Following up on {topic} — are there any blockers I should know about?",
            f"Quick check: where do we stand on {topic}? Any blockers?",
        ],
        "named": [
            f"{person}, could you share an update on {topic}? Want to make sure this doesn't slip.",
            f"{person}, following up on {topic} — what's the latest?",
        ],
        "team": [
            f"Team, need an update on {topic} to keep this moving.",
            f"Checking in on {topic} — can someone share where we stand?",
        ],
    }
    import random
    return random.choice(templates.get(tone, templates["team"]))


# ── Closure Detector ─────────────────────────────────────────────────────────

def check_closure(card: Card, new_messages: List[Message]) -> bool:
    """
    Detect if a card's topic is resolved.
    T3.6 + T3.7
    """
    if not new_messages:
        return False

    closure_keywords = ["done", "completed", "shipped", "closed", "fixed",
                        "resolved", "deployed", "merged", "finished", "it's live"]
    explicit_closures = 0
    for msg in new_messages:
        text_lower = (msg.content or "").lower()
        if any(kw in text_lower for kw in closure_keywords):
            if _mentions_topic(text_lower, card.title) or msg.sender_name in card.key_people:
                explicit_closures += 1

    if explicit_closures > 0:
        return True

    if len(new_messages) >= 3:
        return _llm_check_closure(card, new_messages)

    return False


def _mentions_topic(text: str, topic: str) -> bool:
    topic_words = set(re.sub(r"[^a-z0-9]", " ", topic.lower()).split())
    text_words = set(re.sub(r"[^a-z0-9]", " ", text.lower()).split())
    if not topic_words:
        return False
    overlap = len(topic_words & text_words) / len(topic_words)
    return overlap >= 0.3


def _llm_check_closure(card: Card, messages: List[Message]) -> bool:
    text = "\n".join(f"[{m.timestamp.isoformat()}] {m.sender_name}: {m.content}" for m in messages[-10:])
    prompt = f"""Given these messages about "{card.title}", has the task been completed or resolved?

Messages:
{text}

Answer ONLY with a JSON object: {{"resolved": true/false, "confidence": 0.0-1.0}}
"""
    result = gemini_json(prompt, {"resolved": False, "confidence": 0.0})
    return result.get("resolved") is True and result.get("confidence", 0) > 0.85


# ── Progress Update ──────────────────────────────────────────────────────────

def scan_card(card: Card) -> List[Message]:
    """
    Scan group for new messages related to this card.
    Returns relevant messages. T3.8
    """
    since = card.updated_at or card.created_at
    if not since:
        since = datetime.now() - timedelta(days=7)

    all_msgs = get_group_messages(card.group_id, since=since)
    relevant = []
    for msg in all_msgs:
        if _mentions_topic(msg.content or "", card.title):
            relevant.append(msg)
    return relevant


def update_card_progress(card: Card, messages: List[Message]) -> Card:
    """
    Process new messages, update progress log, check closure.
    Returns updated card.
    """
    for msg in messages:
        eta = parse_eta(msg.content or "")
        if eta:
            card.eta_parsed = eta
            card.eta_raw = msg.content
            card.next_nudge_at = eta + timedelta(minutes=30)
            card.progress_log.append(ProgressLogEntry(
                ts=msg.timestamp,
                event="eta_updated",
                detail=f"ETA set to {eta.isoformat()}: {msg.content[:100]}",
            ))
        else:
            card.progress_log.append(ProgressLogEntry(
                ts=msg.timestamp,
                event="reply_received",
                detail=f"{msg.sender_name}: {msg.content[:200]}",
            ))

    if check_closure(card, messages):
        card.status = CardStatus.IN_REVIEW
        card.mode = CardMode.PASSIVE
        card.next_nudge_at = None
        card.progress_log.append(ProgressLogEntry(
            ts=datetime.now(),
            event="closure_detected",
            detail="Topic appears resolved. Moved to In Review.",
        ))

    card.updated_at = datetime.now()
    save_card(card)
    return card


# ── Scheduling ───────────────────────────────────────────────────────────────

def calculate_next_nudge(card: Card) -> datetime:
    """
    Calculate when the next nudge should be sent.
    Respects working hours and ETA.
    T3.3
    """
    now = _now_ist()

    if card.eta_parsed and card.eta_parsed > now:
        return _next_working_hour_start(card.eta_parsed + timedelta(minutes=30))

    return _next_working_hour_start(now + timedelta(hours=1))


def should_send_nudge(card: Card) -> bool:
    """
    Check if it's time to send a nudge for this card.
    """
    if card.mode != CardMode.ACTIVE or card.status != CardStatus.IN_PROGRESS:
        return False

    if not card.next_nudge_at:
        return True

    return _now_ist() >= card.next_nudge_at
