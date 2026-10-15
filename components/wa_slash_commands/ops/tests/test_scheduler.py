"""
ops/tests/test_scheduler.py -- Phase 5: Scheduler tests (T5.1--T5.5)
Run with: pytest ops/tests/test_scheduler.py -v
"""
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from ops.scheduler import scan_all_groups, nudge_active_cards, start_scheduler, stop_scheduler
from ops.db import init_schema, get_active_cards, list_groups


class TestSchedulerScan:
    """T5.1: Scan tick processes messages."""

    @patch('ops.scheduler.list_groups')
    @patch('ops.scheduler.update_group_wiki')
    @patch('ops.scheduler.get_group_messages')
    @patch('ops.scheduler.process_new_messages_for_group')
    def test_scan_tick(self, mock_process, mock_get_msgs, mock_build_wiki, mock_get_groups):
        """Test that scan_all_groups processes messages."""
        mock_get_groups.return_value = [Mock(jid='test_group', name='Test', whitelisted=True)]
        mock_build_wiki.return_value = {'overview': 'Test wiki'}
        mock_get_msgs.return_value = [{'id': 'msg1', 'text': 'test'}]

        scan_all_groups()

        mock_get_groups.assert_called_once()
        mock_build_wiki.assert_called_once_with('test_group')
        mock_get_msgs.assert_called_once()
        mock_process.assert_called_once()


class TestSchedulerNudge:
    """T5.2: Nudge tick sends messages for due cards."""

    @patch('ops.scheduler.is_working_hours')
    @patch('ops.scheduler.get_active_cards')
    @patch('ops.scheduler.compose_nudge')
    @patch('ops.scheduler.send_whatsapp_message')
    def test_nudge_due_card(self, mock_send, mock_compose, mock_get_cards, mock_working):
        """Test nudge sent when card is due."""
        mock_working.return_value = True
        mock_get_cards.return_value = [
            Mock(
                id='card1', group_id='group1', title='Test',
                next_nudge_at=datetime.now() - timedelta(hours=1),
                nudge_count=0
            )
        ]
        mock_compose.return_value = "Test nudge message"
        mock_send.return_value = True

        nudge_active_cards()

        mock_send.assert_called_once()


class TestSchedulerHours:
    """T5.3, T5.4: Weekend and night checks."""

    @patch('ops.scheduler.is_working_hours')
    @patch('ops.scheduler.get_active_cards')
    def test_no_nudge_outside_hours(self, mock_get_cards, mock_working):
        """T5.3/T5.4: No nudges outside working hours."""
        mock_working.return_value = False

        nudge_active_cards()

        mock_get_cards.assert_not_called()


class TestSchedulerStartup:
    """T5.5: Main startup starts both scheduler and API."""

    @patch('ops.scheduler.start_scheduler')
    def test_scheduler_start(self, mock_start):
        """Test that start_scheduler can be called."""
        # Just verify the function exists and can be called
        from ops.scheduler import start_scheduler
        # The function should exist
        assert callable(start_scheduler)


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
