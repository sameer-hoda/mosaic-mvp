"""
ops/api.py — FastAPI backend for WA Ops Platform
"""
from __future__ import annotations
from typing import List, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ops.models import Group, Card, CardStatus, CardMode, WikiPage
from ops.db import (
    list_groups, save_group, get_group,
    list_cards, get_card, save_card, delete_card,
    get_wiki, list_nudges,
)
from ops.scanner import list_non_archived_groups
from ops.card_engine import (
    create_manual_card, activate_card, pause_card, close_card,
    process_wiki_for_cards,
)
from ops.bot_engine import calculate_next_nudge, compose_nudge
from ops.scanner import get_group_messages
from ops.db import get_wiki

app = FastAPI(title="WA Ops Platform API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request/Response Models ─────────────────────────────────────────────────

class GroupWhitelistRequest(BaseModel):
    jids: List[str]

class GroupWhitelistUpdateRequest(BaseModel):
    jid: str
    action: str  # "add" or "remove"

class ManualCardRequest(BaseModel):
    group_id: str
    headline: str
    description: str = ""


class MoveCardRequest(BaseModel):
    target_status: str  # backlog|in_progress|in_review|done


# ── Groups ──────────────────────────────────────────────────────────────────

@app.get("/api/groups")
def get_all_groups() -> List[Group]:
    """Return all groups from bridge DB with whitelisted flag."""
    bridge_groups = list_non_archived_groups()
    whitelisted = {g.jid for g in list_groups(whitelisted_only=True)}
    for g in bridge_groups:
        g.whitelisted = g.jid in whitelisted
    return bridge_groups


@app.post("/api/groups/whitelist")
def whitelist_groups(req: GroupWhitelistRequest) -> dict:
    """Select up to 5 groups to monitor (bulk replace)."""
    if len(req.jids) > 5:
        raise HTTPException(status_code=400, detail="Max 5 groups allowed")
    # First, unwhitelist all existing
    for g in list_groups(whitelisted_only=True):
        g.whitelisted = False
        save_group(g)
    # Then whitelist selected
    for jid in req.jids:
        bridge_groups = list_non_archived_groups()
        match = next((g for g in bridge_groups if g.jid == jid), None)
        if match:
            match.whitelisted = True
            save_group(match)
    return {"whitelisted": len(req.jids)}

@app.put("/api/groups/{jid}/whitelist")
def update_group_whitelist(jid: str, req: GroupWhitelistUpdateRequest) -> dict:
    """Add or remove a single group from whitelist."""
    whitelisted = list_groups(whitelisted_only=True)
    if req.action == "add":
        if len(whitelisted) >= 5:
            raise HTTPException(status_code=400, detail="Max 5 groups allowed")
        bridge_groups = list_non_archived_groups()
        match = next((g for g in bridge_groups if g.jid == jid), None)
        if match:
            match.whitelisted = True
            save_group(match)
            return {"added": jid}
        raise HTTPException(status_code=404, detail="Group not found")
    elif req.action == "remove":
        for g in whitelisted:
            if g.jid == jid:
                g.whitelisted = False
                save_group(g)
                return {"removed": jid}
        raise HTTPException(status_code=404, detail="Group not whitelisted")
    raise HTTPException(status_code=400, detail="Invalid action. Use 'add' or 'remove'")


# ── Cards ───────────────────────────────────────────────────────────────────

@app.get("/api/cards")
def get_cards(
    group_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Card]:
    return list_cards(group_id=group_id, status=status)


@app.post("/api/cards")
def create_card(req: ManualCardRequest) -> Card:
    group = get_group(req.group_id)
    group_name = group.name if group else "Unknown"
    return create_manual_card(req.group_id, group_name, req.headline, req.description)


@app.get("/api/cards/{card_id}")
def get_card_detail(card_id: str) -> Card:
    card = get_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    # Attach nudge logs to card response
    card.nudge_log = list_nudges(card_id=card_id)
    return card

@app.post("/api/cards/{card_id}/preview-nudge")
def preview_nudge(card_id: str) -> dict:
    """Preview the next nudge message for a card using current context."""
    card = get_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    
    # Get context for nudge composition
    recent_messages = get_group_messages(card.group_id, limit=50)
    wiki = get_wiki(card.group_id)
    wiki_context = wiki.overview if wiki else None
    
    # Compose nudge with context
    message = compose_nudge(card, recent_messages=recent_messages, wiki_context=wiki_context)
    return {"preview": message}


@app.put("/api/cards/{card_id}/play")
def play_card(card_id: str) -> Card:
    return activate_card(card_id)


@app.put("/api/cards/{card_id}/pause")
def pause_card_api(card_id: str) -> Card:
    return pause_card(card_id)


@app.put("/api/cards/{card_id}/move")
def move_card(card_id: str, req: MoveCardRequest) -> Card:
    card = get_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    target = req.target_status
    if target == "in_progress":
        card.status = CardStatus.IN_PROGRESS
    elif target == "in_review":
        card.status = CardStatus.IN_REVIEW
        card.mode = CardMode.PASSIVE
        card.next_nudge_at = None
    elif target == "done":
        card = close_card(card_id)
    elif target == "backlog":
        card.status = CardStatus.BACKLOG
        card.mode = CardMode.PASSIVE
        card.next_nudge_at = None
    else:
        raise HTTPException(status_code=400, detail=f"Invalid status: {target}")
    save_card(card)
    return card


@app.delete("/api/cards/{card_id}")
def delete_card_api(card_id: str) -> dict:
    delete_card(card_id)
    return {"deleted": card_id}


# ── Wiki ────────────────────────────────────────────────────────────────────

@app.get("/api/wiki/{group_id}")
def get_wiki_page(group_id: str) -> WikiPage:
    wiki = get_wiki(group_id)
    if not wiki:
        raise HTTPException(status_code=404, detail="Wiki not found")
    return wiki


# ── Dashboard Stats ─────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats() -> dict:
    all_cards = list_cards()
    by_status = {}
    for c in all_cards:
        by_status[c.status.value] = by_status.get(c.status.value, 0) + 1
    return {
        "total_cards": len(all_cards),
        "by_status": by_status,
        "whitelisted_groups": len(list_groups(whitelisted_only=True)),
    }

@app.get("/api/setup-status")
def get_setup_status() -> dict:
    """Check if initial setup (wiki + cards) is complete for whitelisted groups."""
    whitelisted = list_groups(whitelisted_only=True)
    total = len(whitelisted)
    ready = 0
    for g in whitelisted:
        # Check if wiki exists
        wiki = get_wiki(g.jid)
        if wiki:
            ready += 1
    return {
        "total_groups": total,
        "ready_groups": ready,
        "is_ready": ready == total and total > 0
    }
