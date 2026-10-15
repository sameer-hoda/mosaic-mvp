"""
ops/tests/test_card_engine.py — Phase 2 tests (T2.1–T2.7)
Run with: pytest ops/tests/test_card_engine.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest
from datetime import datetime

from ops.card_engine import (
    should_create_card,
    find_duplicate_card,
    create_card_from_action,
    process_wiki_for_cards,
    create_manual_card,
    activate_card,
    close_card,
)
from ops.models import Card, CardStatus, CardMode, CardOrigin
from ops.db import init_schema, save_card, list_cards, delete_card


class TestCardCreationThresholds:
    """T2.1, T2.2, T2.3: Conservative card creation."""

    def test_card_with_action_and_owner(self):
        action = {"item": "Fix Z9 error spike", "owner": "Harshit", "eta": None, "status": "open"}
        assert should_create_card(action) is True

    def test_card_no_owner_but_action_accepted(self):
        action = {"item": "Something needs fixing", "owner": None, "eta": None, "status": "open"}
        # Relaxed filter: accept if looks like action (has "fix" keyword)
        assert should_create_card(action) is True

    def test_card_no_action_but_owner_accepted(self):
        action = {"item": "Bio auth SR is better than mPIN", "owner": "Rahul", "eta": None, "status": "open"}
        # Relaxed filter: accept if has owner
        assert should_create_card(action) is True

    def test_card_team_owner_accepted(self):
        action = {"item": "Fix server cert", "owner": "Team", "eta": None, "status": "open"}
        # Team as owner still creates card (but with lower confidence)
        assert should_create_card(action) is True


class TestCardDeduplication:
    """T2.4: Duplicate detection."""

    def test_duplicate_detected(self):
        init_schema()
        c1 = create_card_from_action("g1", "Group1", {"item": "Fix Z9 error spike in bio auth", "owner": "Harshit", "status": "open"})
        dup = find_duplicate_card("Fix Z9 error spike", "g1")
        assert dup is not None
        assert dup.id == c1.id
        delete_card(c1.id)

    def test_no_duplicate_different_group(self):
        c1 = create_card_from_action("g1", "Group1", {"item": "Fix Z9", "owner": "Harshit", "status": "open"})
        dup = find_duplicate_card("Fix Z9", "g2")
        assert dup is None
        delete_card(c1.id)


class TestManualCard:
    """T2.5: Manual card creation."""

    def test_manual_card_created(self):
        card = create_manual_card("g1", "Group1", "Server cert renewal", "SSL cert expires May 15")
        assert card.origin == CardOrigin.USER
        assert card.status == CardStatus.BACKLOG
        assert card.mode == CardMode.PASSIVE
        assert "cert" in card.title.lower()
        delete_card(card.id)


class TestCardLifecycle:
    """T2.6, T2.7: Play, progress log."""

    def test_play_activation(self):
        card = create_card_from_action("g1", "Group1", {"item": "Fix Z9", "owner": "Harshit", "status": "open"})
        activated = activate_card(card.id)
        assert activated.status == CardStatus.IN_PROGRESS
        assert activated.mode == CardMode.ACTIVE
        assert activated.next_nudge_at is not None
        assert any(e.event == "scan" for e in activated.progress_log)
        delete_card(card.id)

    def test_close_card(self):
        card = create_card_from_action("g1", "Group1", {"item": "Fix Z9", "owner": "Harshit", "status": "open"})
        closed = close_card(card.id)
        assert closed.status == CardStatus.DONE
        assert closed.mode == CardMode.PASSIVE
        assert closed.next_nudge_at is None
        delete_card(card.id)


class TestProcessWiki:
    """T2.1: Full integration from wiki to cards."""

    def test_process_wiki_creates_cards(self):
        # Assumes Bio-Auth group has wiki with action items
        from ops.scanner import list_non_archived_groups
        groups = list_non_archived_groups()
        bio = [g for g in groups if "bio" in g.name.lower()]
        if not bio:
            pytest.skip("No Bio-Auth group for integration test")

        # Clean existing cards first
        for c in list_cards(group_id=bio[0].jid):
            delete_card(c.id)

        created = process_wiki_for_cards(bio[0].jid, bio[0].name)
        # May create 0-3 cards depending on LLM output
        assert isinstance(created, list)
        for c in created:
            delete_card(c.id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
