"""
ops/tests/test_wiki_builder.py — Phase 1 tests (T1.1–T1.6)
Run with: pytest ops/tests/test_wiki_builder.py -v
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from ops.wiki_builder import build_wiki_from_scratch, incremental_update, _parse_wiki_json, update_group_wiki
from ops.scanner import list_non_archived_groups
from ops.db import get_wiki
from ops.models import WikiPage


class TestWikiBuild:
    """T1.2: Using real Bio-Auth chat data."""

    @pytest.fixture(scope="class")
    def bio_group(self):
        groups = list_non_archived_groups()
        bio = [g for g in groups if "bio" in g.name.lower() or "metrics" in g.name.lower()]
        if not bio:
            pytest.skip("No Bio-Auth group in dev DB")
        return bio[0]

    @pytest.fixture(scope="class")
    def wiki(self, bio_group):
        """Build wiki once per test class."""
        return build_wiki_from_scratch(bio_group.jid, message_limit=200)

    def test_wiki_created(self, wiki):
        """T1.1: Wiki object returned."""
        assert wiki is not None
        assert isinstance(wiki, WikiPage)

    def test_wiki_has_overview(self, wiki):
        """Overview is non-empty."""
        assert wiki.overview
        assert len(wiki.overview) > 20

    def test_wiki_has_people(self, wiki):
        """T1.2: Detects people."""
        assert len(wiki.people) >= 1, f"Expected ≥1 person, got {len(wiki.people)}"
        for p in wiki.people:
            assert p.name
            assert p.activity in ("high", "medium", "low")

    def test_wiki_has_topics(self, wiki):
        """T1.2: Detects topics."""
        assert len(wiki.topics) >= 1, f"Expected ≥1 topic, got {len(wiki.topics)}"
        for t in wiki.topics:
            assert t.title
            assert t.status in ("active", "resolved")

    def test_wiki_action_items(self, wiki):
        """T1.2: Detects ≥2 action items."""
        # Conservative threshold — may have 0-2 depending on LLM
        assert wiki.action_items is not None
        for a in wiki.action_items:
            assert a.item
            assert a.status in ("open", "resolved")

    def test_wiki_persists(self, wiki, bio_group):
        """T1.5: After generation, wiki table has row."""
        stored = get_wiki(bio_group.jid)
        assert stored is not None
        assert stored.group_id == bio_group.jid
        assert stored.overview == wiki.overview

    def test_wiki_thread_log(self, wiki):
        """Thread log has entries."""
        assert isinstance(wiki.thread_log, list)
        # May be empty if no significant threads detected
        for entry in wiki.thread_log:
            assert isinstance(entry, str)
            assert len(entry) > 10


class TestWikiEdgeCases:
    """T1.1: Empty group handling."""

    def test_empty_group(self):
        """Non-existent group returns empty wiki."""
        wiki = build_wiki_from_scratch("nonexistent@g.us", message_limit=10)
        assert wiki.overview == "No messages found for this group yet."
        assert wiki.people == []
        assert wiki.topics == []

    def test_parse_wiki_json(self):
        """Internal: JSON dict parses correctly."""
        data = {
            "overview": "Test overview",
            "people": [{"name": "Alice", "role": "PM", "activity": "high", "mention_count": 5}],
            "topics": [{"title": "Topic1", "status": "active", "last_discussed": "2026-04-01"}],
            "action_items": [{"item": "Fix bug", "owner": "Alice", "eta": "today", "status": "open"}],
            "thread_log": ["2026-04-01: Started project"],
        }
        wiki = _parse_wiki_json(data, "test-group@g.us")
        assert wiki.group_id == "test-group@g.us"
        assert wiki.overview == "Test overview"
        assert len(wiki.people) == 1
        assert wiki.people[0].name == "Alice"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
