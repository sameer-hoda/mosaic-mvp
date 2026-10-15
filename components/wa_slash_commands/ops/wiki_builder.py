"""
ops/wiki_builder.py — Build structured wiki pages from WhatsApp group messages
"""
from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ops.models import WikiPage, WikiPerson, WikiTopic, WikiActionItem
from ops.db import save_wiki, get_wiki
from ops.scanner import get_group_messages

# Load prompt template
_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "wiki_extraction.txt"
_WIKI_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

from ops.llm import gemini_json


def _format_messages_for_prompt(msgs: list) -> str:
    """Format Message objects for the LLM prompt."""
    lines = []
    for m in msgs:
        lines.append(f"[{m.timestamp.isoformat()}] {m.sender_name}: {m.content}")
    return "\n".join(lines)


def _parse_wiki_json(data: dict, group_id: str) -> WikiPage:
    """Parse LLM response dict into WikiPage model."""
    return WikiPage(
        group_id=group_id,
        overview=data.get("overview", ""),
        people=[WikiPerson(**p) for p in data.get("people", [])],
        topics=[WikiTopic(**t) for t in data.get("topics", [])],
        action_items=[WikiActionItem(**a) for a in data.get("action_items", [])],
        thread_log=data.get("thread_log", []),
        last_updated=datetime.now(),
    )


def build_wiki_from_scratch(group_jid: str, message_limit: int = 800) -> WikiPage:
    """
    Generate a full wiki page for a group using all available messages.
    Used during bootstrap or when wiki doesn't exist yet.
    """
    msgs = get_group_messages(group_jid, limit=message_limit)
    if not msgs:
        return WikiPage(
            group_id=group_jid,
            overview="No messages found for this group yet.",
            last_updated=datetime.now(),
        )

    text = _format_messages_for_prompt(msgs)
    prompt = _WIKI_PROMPT.replace("{messages_text}", text)

    schema = {
        "type": "object",
        "properties": {
            "overview": {"type": "string"},
            "people": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string"},
                        "activity": {"type": "string", "enum": ["high", "medium", "low"]},
                        "mention_count": {"type": "integer"},
                    },
                    "required": ["name", "activity"],
                },
            },
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "status": {"type": "string", "enum": ["active", "resolved"]},
                        "last_discussed": {"type": "string"},
                    },
                    "required": ["title", "status"],
                },
            },
            "action_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string"},
                        "owner": {"type": ["string", "null"]},
                        "eta": {"type": ["string", "null"]},
                        "status": {"type": "string", "enum": ["open", "resolved"]},
                    },
                    "required": ["item", "status"],
                },
            },
            "thread_log": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["overview", "people", "topics", "action_items", "thread_log"],
    }

    result = gemini_json(prompt, fallback={})
    wiki = _parse_wiki_json(result, group_jid)
    save_wiki(wiki)
    return wiki


def incremental_update(group_jid: str, new_msgs: list) -> WikiPage:
    """
    Update an existing wiki with new messages since last update.
    If no wiki exists, does a full build.
    """
    existing = get_wiki(group_jid)
    if not existing:
        return build_wiki_from_scratch(group_jid)

    if not new_msgs:
        return existing

    # For now, do a full rebuild — incremental merge is complex and error-prone.
    # In production, we'd pass existing wiki + new messages to Gemini for smart merge.
    return build_wiki_from_scratch(group_jid)


def update_group_wiki(group_jid: str) -> WikiPage:
    """
    Public API: ensure wiki exists and is up to date for a group.
    """
    existing = get_wiki(group_jid)
    if existing and existing.last_updated:
        # Only fetch new messages since last update
        msgs = get_group_messages(group_jid, since=existing.last_updated)
        return incremental_update(group_jid, msgs)
    else:
        return build_wiki_from_scratch(group_jid)
