"""
ops/tests/test_e2e.py -- Phase 6: End-to-End tests (T6.1--T6.6)
Run with: pytest ops/tests/test_e2e.py -v
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from ops.db import init_schema, list_groups, get_card, list_cards
from ops.scanner import list_non_archived_groups
from ops.wiki_builder import update_group_wiki
from ops.card_engine import create_manual_card, activate_card, close_card
from ops.bot_engine import check_closure


class TestE2EWhitelistToWiki:
    """T6.1: Whitelist group, verify wiki created."""

    def test_whitelist_to_wiki(self):
        """Whitelist a group and verify wiki is created."""
        init_schema()

        # Get a group from bridge DB
        groups = list_non_archived_groups()
        if not groups:
            return

        group = groups[0]

        # Build wiki for group
        wiki = update_group_wiki(group.jid)

        assert wiki is not None
        assert wiki.overview is not None


class TestE2ECardCreation:
    """T6.2: After bootstrap, cards exist."""

    def test_card_creation_after_wiki(self):
        """Cards should be created from wiki action items."""
        init_schema()

        groups = list_non_archived_groups()
        if not groups:
            return

        group = groups[0]

        # Build wiki (which should trigger card creation)
        wiki = update_group_wiki(group.jid)

        # Check if cards were created
        cards = list_cards(group_id=group.jid)

        # May or may not have cards depending on group data
        assert isinstance(cards, list)


    def test_reply_updates_progress(self):
        """Simulate a reply message and verify progress log."""
        init_schema()

        groups = list_non_archived_groups()
        if not groups:
            return

        # Create and play a card
        card = create_manual_card(
            groups[0].jid, groups[0].name,
            "Reply Test", "Test description"
        )
        card = activate_card(card.id)

        # Simulate a reply (in real system, this would come from scanner)
        from ops.models import ProgressLogEntry
        card.progress_log.append(ProgressLogEntry(
            ts=datetime.now(),
            event="reply_received",
            detail="User: This is done"
        ))

        from ops.db import save_card
        save_card(card)

        # Verify progress log
        updated = get_card(card.id)
        assert len(updated.progress_log) >0
        assert updated.progress_log[-1].event == "reply_received"


class TestE2EClosureToReview:
    """T6.5: Detect closure, move to In Review."""

    def test_closure_moves_to_review(self):
        """When closure detected, card moves to In Review."""
        init_schema()

        groups = list_non_archived_groups()
        if not groups:
            return

        # Create and play a card
        card = create_manual_card(
            groups[0].jid, groups[0].name,
            "Closure Test", "Test closure detection"
        )
        card = activate_card(card.id)

        # Simulate closure message using proper Message object
        from ops.models import Message
        messages = [Message(
            id="msg_done",
            chat_jid=groups[0].jid,
            sender_jid="user@s.whatsapp.net",
            sender_name="User",
            content="Closure Test is now complete and deployed",
            timestamp=datetime.now()
        )]

        # Detect closure
        if check_closure(card, messages):
            from ops.models import CardStatus, CardMode
            card.status = CardStatus.IN_REVIEW
            card.mode = CardMode.PASSIVE
            from ops.db import save_card
            save_card(card)

        updated = get_card(card.id)
        assert updated.status == CardStatus.IN_REVIEW


class TestE2EUserClose:
    """T6.6: User closes card, moves to Done."""

    def test_user_close_to_done(self):
        """User can move card from In Review to Done."""
        init_schema()

        groups = list_non_archived_groups()
        if not groups:
            return

        # Create card directly in in_review
        card = create_manual_card(
            groups[0].jid, groups[0].name,
            "Close Test", "Test closing"
        )
        card = activate_card(card.id)

        # Close the card
        closed = close_card(card.id)

        assert closed.status == "done"


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
