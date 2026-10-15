"""
ops/scheduler.py -- APScheduler for 15-min scans and hourly nudges
"""
import logging
from datetime import datetime, timedelta
from typing import List

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from ops.db import list_groups, get_active_cards, get_card, save_card, log_nudge, get_wiki
from ops.models import NudgeLogEntry
from ops.scanner import get_group_messages
from ops.scanner import get_group_messages
from ops.wiki_builder import update_group_wiki
from ops.card_engine import process_new_messages_for_group
from ops.bot_engine import compose_nudge, check_closure, is_working_hours, calculate_next_nudge
from ops.sender import send_whatsapp_message

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def scan_all_groups():
    """15-minute scan: update wikis and process cards for all whitelisted groups."""
    logger.info("Starting 15-minute group scan...")
    groups = list_groups(whitelisted_only=True)

    for group in groups:
        try:
            # Update wiki with new messages
            wiki = update_group_wiki(group.jid)

            # Get new messages and process for cards
            messages = get_group_messages(group.jid, limit=200)
            if messages:
                process_new_messages_for_group(group.jid, messages, wiki)
                logger.info(f"Processed {len(messages)} messages for group {group.name}")

        except Exception as e:
            logger.error(f"Error scanning group {group.jid}: {e}")

    logger.info("15-minute scan complete.")


def nudge_active_cards():
    """Hourly nudge: send follow-ups for active cards (Mon-Fri, 08:00-17:00 IST)."""
    if not is_working_hours():
        logger.debug("Outside working hours, skipping nudges")
        return

    logger.info("Checking for cards to nudge...")
    cards = get_active_cards()

    for card in cards:
        try:
            # Check if nudge is due
            if card.next_nudge_at:
                # Handle both string and datetime types
                if isinstance(card.next_nudge_at, str):
                    next_nudge = datetime.fromisoformat(card.next_nudge_at)
                else:
                    next_nudge = card.next_nudge_at
                
                if datetime.now() < next_nudge:
                    continue

            # Get recent messages and wiki context for contextual nudge
            recent_messages = get_group_messages(card.group_id, limit=50)
            wiki = get_wiki(card.group_id)
            wiki_context = wiki.overview if wiki else None
            
            # Compose and send nudge with context
            message = compose_nudge(card, recent_messages=recent_messages, wiki_context=wiki_context)
            if message:
                sent = send_whatsapp_message(card.group_id, message, dry_run=False)  # Set to False for real messages
                if sent:
                    # Log nudge to nudge_log with message text
                    nudge_entry = NudgeLogEntry(
                        card_id=card.id,
                        group_id=card.group_id,
                        message_text=message,
                        sent_at=datetime.now(),
                        response_received=False
                    )
                    log_nudge(nudge_entry)
                    
                    # Update nudge count and schedule next nudge
                    card.nudge_count = (card.nudge_count or 0) + 1
                    card.next_nudge_at = calculate_next_nudge(card)
                    save_card(card)
                    logger.info(f"Nudged card {card.title} (nudge #{card.nudge_count})")

        except Exception as e:
            logger.error(f"Error nudging card {card.id}: {e}")

    logger.info("Nudge check complete.")


def scan_card_progress(card_id: str):
    """Scan for a specific card's progress (called every 15 min for active cards)."""
    from ops.bot_engine import scan_messages_for_card

    card = get_card(card_id)
    if not card or card.mode != "active":
        return

    try:
        # Get new messages for the group
        messages = get_messages_for_group(card.group_id, limit=50)

        # Filter relevant messages
        relevant = scan_messages_for_card(card, messages)

        # Check for closure
        if check_closure(card, relevant):
            card.status = "in_review"
            card.mode = "passive"
            card.next_nudge_at = None
            update_card(card)
            logger.info(f"Card {card.title} moved to In Review (closure detected)")
        else:
            # Update progress log with new messages
            update_card(card)

    except Exception as e:
        logger.error(f"Error scanning card {card_id}: {e}")


def start_scheduler():
    """Start the background scheduler."""
    # 15-minute scan for all groups
    scheduler.add_job(
        scan_all_groups,
        trigger=IntervalTrigger(minutes=15),
        id='scan_groups',
        replace_existing=True
    )

    # Hourly nudge check (during working hours only)
    scheduler.add_job(
        nudge_active_cards,
        trigger=CronTrigger(hour='8-17', minute=0, day_of_week='mon-fri'),
        id='nudge_cards',
        replace_existing=True
    )

    scheduler.start()
    logger.info("Scheduler started.")


def stop_scheduler():
    """Stop the scheduler."""
    scheduler.shutdown()
    logger.info("Scheduler stopped.")
