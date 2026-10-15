"""
ops/tests/test_bot_engine.py — Phase 3 tests (T3.1–T3.9)
Run with: pytest ops/tests/test_bot_engine.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from ops.bot_engine import (
    parse_eta,
    compose_nudge,
    check_closure,
    _mentions_topic,
    _is_working_hours,
    _next_working_hour_start,
    calculate_next_nudge,
    should_send_nudge,
)
from ops.models import Card, CardStatus, CardMode, Message

# Mock Gemini JSON to return static responses for tests
def mock_gemini_json(prompt, default):
    # Return a static nudge message for testing
    if "compose" in prompt.lower() or "follow" in prompt.lower():
        return {"message": "Just checking in on Server cert renewal — any updates from the team?"}
    if "closure" in prompt.lower():
        return {"resolved": False, "confidence": 0.0}
    return default


class TestNudgeComposer:
    """T3.1: Soft check-in on first nudge."""

    @patch('ops.bot_engine.gemini_json', return_value={"message": "Just checking in on Server cert renewal — any updates from the team?"})
    def test_nudge_soft(self, mock_gemini):
        card = Card(
            id="c1", group_id="g1", group_name="G1", title="Server cert renewal",
            status=CardStatus.IN_PROGRESS, mode=CardMode.ACTIVE,
            key_people=["Kunal"], key_people_confidence=0.9,
            nudge_count=0, progress_log=[],
        )
        msg = compose_nudge(card)
        assert "checking in" in msg.lower() or "Server cert renewal" in msg

    @patch('ops.bot_engine.gemini_json', return_value={"message": "Following up on Server cert renewal — are there any blockers I should know about?"})
    def test_nudge_direct(self, mock_gemini):
        """T3.2: Second nudge is more direct."""
        card = Card(
            id="c1", group_id="g1", group_name="G1", title="Server cert renewal",
            status=CardStatus.IN_PROGRESS, mode=CardMode.ACTIVE,
            key_people=["Kunal"], key_people_confidence=0.9,
            nudge_count=1, progress_log=[],
        )
        msg = compose_nudge(card)
        assert "blockers" in msg.lower() or "Server cert renewal" in msg.lower()

    @patch('ops.bot_engine.gemini_json', return_value={"message": "Kunal, could you share an update on Server cert renewal? Want to make sure this doesn't slip."})
    def test_nudge_named(self, mock_gemini):
        """T3.2: Third nudge names individual if confidence high."""
        card = Card(
            id="c1", group_id="g1", group_name="G1", title="Server cert renewal",
            status=CardStatus.IN_PROGRESS, mode=CardMode.ACTIVE,
            key_people=["Kunal"], key_people_confidence=0.9,
            nudge_count=2, progress_log=[],
        )
        msg = compose_nudge(card)
        assert "Kunal" in msg

    def test_nudge_team_fallback(self):
        """T3.2: Low confidence -- address team."""
        card = Card(
            id="c1", group_id="g1", group_name="G1", title="Server cert renewal",
            status=CardStatus.IN_PROGRESS, mode=CardMode.ACTIVE,
            key_people=["Team"], key_people_confidence=0.5,
            nudge_count=2, progress_log=[],
        )
        # Mock random.choice to return first template
        with patch('random.choice', return_value="Team, need an update on Server cert renewal to keep this moving."):
            msg = compose_nudge(card)
            assert "Team" in msg


class TestWorkingHours:
    """T3.3: Nudges only during Mon–Fri 08:00–17:00 IST."""

    def test_working_hours_weekday(self):
        t = datetime(2026, 5, 5, 10, 0)  # Monday 10:00
        assert _is_working_hours(t) is True

    def test_not_working_hours_night(self):
        t = datetime(2026, 5, 5, 20, 0)  # Monday 20:00
        assert _is_working_hours(t) is False

    def test_not_working_hours_saturday(self):
        t = datetime(2026, 5, 3, 10, 0)  # Saturday 10:00
        assert _is_working_hours(t) is False

    def test_next_working_hour_respects_weekend(self):
        friday_18h = datetime(2026, 5, 8, 18, 0)  # Friday 18:00
        nxt = _next_working_hour_start(friday_18h)
        assert nxt.weekday() == 0  # Monday
        assert nxt.hour == 8


class TestETAParser:
    """T3.4 + T3.5: ETA parsing."""

    def test_eta_today_3pm(self):
        ref = datetime(2026, 5, 2, 10, 0)
        eta = parse_eta("I'll send it by 3pm today", ref)
        assert eta is not None
        assert eta.hour == 15
        assert eta.day == 2

    def test_eta_tomorrow_eod(self):
        ref = datetime(2026, 5, 2, 10, 0)
        eta = parse_eta("will finish by tomorrow EOD", ref)
        assert eta is not None
        assert eta.day == 3
        assert eta.hour == 17

    def test_eta_in_2_hours(self):
        ref = datetime(2026, 5, 2, 10, 0)
        eta = parse_eta("done in 2 hours", ref)
        assert eta is not None
        assert eta.hour == 12

    def test_eta_no_match(self):
        ref = datetime(2026, 5, 2, 10, 0)
        eta = parse_eta("This is just a discussion thread", ref)
        assert eta is None


class TestClosureDetector:
    """T3.6 + T3.7: Detect task completion."""

    def test_explicit_closure(self):
        card = Card(
            id="c1", group_id="g1", group_name="G1", title="Fix Z9 errors",
            status=CardStatus.IN_PROGRESS, mode=CardMode.ACTIVE,
            key_people=["Harshit"], progress_log=[],
        )
        msgs = [
            Message(id="m1", chat_jid="g1", sender_name="Harshit", content="Z9 is fixed now", timestamp=datetime.now()),
        ]
        assert check_closure(card, msgs) is True

    def test_no_closure(self):
        card = Card(
            id="c1", group_id="g1", group_name="G1", title="Fix Z9 errors",
            status=CardStatus.IN_PROGRESS, mode=CardMode.ACTIVE,
            key_people=["Harshit"], progress_log=[],
        )
        msgs = [
            Message(id="m1", chat_jid="g1", sender_name="Harshit", content="Working on it", timestamp=datetime.now()),
        ]
        assert check_closure(card, msgs) is False

    def test_mentions_topic(self):
        assert _mentions_topic("Fix the Z9 error spike", "Z9 error spike") is True
        assert _mentions_topic("Let's have lunch", "Z9 error spike") is False


class TestRelevanceFilter:
    """T3.8: Irrelevant messages ignored."""

    def test_irrelevant_message(self):
        card = Card(
            id="c1", group_id="g1", group_name="G1", title="Fix Z9 errors",
            status=CardStatus.IN_PROGRESS, mode=CardMode.ACTIVE,
            progress_log=[],
        )
        msgs = [
            Message(id="m1", chat_jid="g1", sender_name="Rahul", content="Anyone up for lunch today?", timestamp=datetime.now()),
        ]
        assert check_closure(card, msgs) is False


class TestScheduling:
    """T3.3: Next nudge scheduling."""

    def test_next_nudge_default(self):
        card = Card(
            id="c1", group_id="g1", group_name="G1", title="Fix Z9 errors",
            status=CardStatus.IN_PROGRESS, mode=CardMode.ACTIVE,
            eta_parsed=None, next_nudge_at=None, progress_log=[],
        )
        nxt = calculate_next_nudge(card)
        assert nxt is not None

    def test_should_send_nudge_active(self):
        card = Card(
            id="c1", group_id="g1", group_name="G1", title="Fix Z9 errors",
            status=CardStatus.IN_PROGRESS, mode=CardMode.ACTIVE,
            next_nudge_at=datetime.now() - timedelta(hours=1), progress_log=[],
        )
        assert should_send_nudge(card) is True

    def test_should_not_send_nudge_backlog(self):
        card = Card(
            id="c1", group_id="g1", group_name="G1", title="Fix Z9 errors",
            status=CardStatus.BACKLOG, mode=CardMode.PASSIVE,
            next_nudge_at=datetime.now() - timedelta(hours=1), progress_log=[],
        )
        assert should_send_nudge(card) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
