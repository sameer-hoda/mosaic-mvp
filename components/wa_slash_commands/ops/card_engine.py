"""
ops/card_engine.py — Create and manage Kanban cards from wiki action items
"""
from __future__ import annotations
import json
import re
import uuid
from datetime import datetime
from typing import List, Optional

from ops.models import Card, CardStatus, CardMode, CardOrigin, ProgressLogEntry
from ops.db import save_card, get_card, list_cards
from ops.wiki_builder import update_group_wiki


def _normalize(text: str) -> set:
    """Lowercase, remove punctuation, return word set."""
    words = re.sub(r"[^a-z0-9]", " ", text.lower()).split()
    return set(words)


def _title_similarity(a: str, b: str) -> float:
    """Word-level Jaccard similarity. 0.0–1.0."""
    tokens_a = _normalize(a)
    tokens_b = _normalize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def should_create_card(action_item: dict) -> bool:
    """
    Relaxed threshold: card if has owner OR looks like action item.
    Now accepts more legitimate tasks.
    """
    item_text = action_item.get("item", "").lower()
    owner = action_item.get("owner")
    
    # Accept if has owner (including "Team")
    has_owner = owner is not None and owner.strip() != ""
    
    # Expanded action keywords to catch more legitimate tasks
    action_keywords = [
        "fix", "build", "investigate", "resolve", "deploy",
        "update", "send", "confirm", "schedule", "create",
        "automate", "clarify", "address", "review", "test",
        "prepare", "share", "provide", "complete", "finish",
        "implement", "configure", "setup", "optimize", "improve"
    ]
    looks_like_action = any(kw in item_text for kw in action_keywords)
    
    # Relaxed: accept if either has owner OR looks like action
    return has_owner or looks_like_action


def find_duplicate_card(title: str, group_id: str, threshold: float = 0.5) -> Optional[Card]:
    """
    Check if a similar card already exists in the same group.
    T2.4
    """
    existing = list_cards(group_id=group_id)
    for card in existing:
        sim = _title_similarity(card.title, title)
        if sim >= threshold:
            return card
    return None


def create_card_from_action(
    group_id: str,
    group_name: str,
    action_item: dict,
    context: str = "",
) -> Card:
    """
    Create a new card from a wiki action item.
    T2.1
    """
    card_id = f"ce-{uuid.uuid4().hex[:8]}"
    owner = action_item.get("owner", "Team")
    confidence = 0.85 if owner and owner != "Team" else 0.6

    card = Card(
        id=card_id,
        group_id=group_id,
        group_name=group_name,
        title=action_item["item"][:80],
        context=context or action_item["item"],
        status=CardStatus.BACKLOG,
        mode=CardMode.PASSIVE,
        origin=CardOrigin.INGESTION,
        key_people=[owner] if owner else [],
        key_people_confidence=confidence,
        eta_raw=action_item.get("eta"),
        progress_log=[],
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    save_card(card)
    return card


def process_wiki_for_cards(group_id: str, group_name: str) -> List[Card]:
    """
    Scan wiki action items for a group and create cards.
    Skips items that don't meet threshold or are duplicates.
    Returns list of newly created cards.
    T2.1 + T2.2 + T2.3 + T2.4
    """
    wiki = update_group_wiki(group_id)
    if not wiki or not wiki.action_items:
        return []

    created = []
    for item in wiki.action_items:
        if not should_create_card(item.model_dump()):
            continue

        dup = find_duplicate_card(item.item, group_id)
        if dup:
            # Merge: append context if new info
            if item.item not in (dup.context or ""):
                dup.context += f"\n\n[Updated] {item.item}"
                dup.updated_at = datetime.now()
                save_card(dup)
            continue

        card = create_card_from_action(group_id, group_name, item.model_dump(), wiki.overview)
        created.append(card)

    return created


def create_manual_card(
    group_id: str,
    group_name: str,
    headline: str,
    description: str = "",
) -> Card:
    """
    User-created card. T2.5
    """
    card = Card(
        id=f"uc-{uuid.uuid4().hex[:8]}",
        group_id=group_id,
        group_name=group_name,
        title=headline[:80],
        context=description,
        status=CardStatus.BACKLOG,
        mode=CardMode.PASSIVE,
        origin=CardOrigin.USER,
        key_people=[],
        progress_log=[],
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    save_card(card)
    return card


def activate_card(card_id: str) -> Card:
    """
    User hits Play. Move to In Progress, start bot. T2.6
    """
    card = get_card(card_id)
    if not card:
        raise ValueError(f"Card not found: {card_id}")

    card.status = CardStatus.IN_PROGRESS
    card.mode = CardMode.ACTIVE
    card.next_nudge_at = datetime.now()  # nudge ASAP
    card.progress_log.append(ProgressLogEntry(
        ts=datetime.now(),
        event="scan",
        detail=f"Card activated by user. Bot now managing follow-up for: {card.title}",
    ))
    card.updated_at = datetime.now()
    save_card(card)
    return card


def pause_card(card_id: str) -> Card:
    """User pauses active card."""
    card = get_card(card_id)
    if not card:
        raise ValueError(f"Card not found: {card_id}")

    card.mode = CardMode.PASSIVE
    card.next_nudge_at = None
    card.progress_log.append(ProgressLogEntry(
        ts=datetime.now(),
        event="scan",
        detail="Card paused by user. Bot stopped.",
    ))
    card.updated_at = datetime.now()
    save_card(card)
    return card


def close_card(card_id: str) -> Card:
    """User drags to Done."""
    card = get_card(card_id)
    if not card:
        raise ValueError(f"Card not found: {card_id}")

    card.status = CardStatus.DONE
    card.mode = CardMode.PASSIVE
    card.next_nudge_at = None
    card.progress_log.append(ProgressLogEntry(
        ts=datetime.now(),
        event="closure_detected",
        detail="Card closed by user.",
    ))
    card.updated_at = datetime.now()
    save_card(card)
    return card


def process_new_messages_for_group(group_id: str, messages: list, wiki) -> List[Card]:
    """
    Process new messages for a group and create/update cards accordingly.
    This is called by the scheduler during the 15-minute scan.
    """
    # Update wiki with new messages (wiki passed in, already updated)
    # Process wiki for new action items -> cards
    group_name = ""
    # Get group name from db
    from ops.db import get_group
    group = get_group(group_id)
    if group:
        group_name = group.name

    # Process wiki action items for new cards
    new_cards = process_wiki_for_cards(group_id, group_name)

    return new_cards
