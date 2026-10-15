#!/usr/bin/env python3
"""
test_local.py
──────────────
Local sandbox test harness for wa-slash-commands.

Seeding a minimal SQLite database with fake messages so you can
test all slash commands end-to-end without a live WhatsApp bridge.

Usage:
  python3 test_local.py           # Run all commands, capture output
  python3 test_local.py --cmd /recap  # Test a single command

How it works:
  1. Creates a temporary ./store/messages.db + whatsapp.db.
  2. Seeds fake messages so there is meaningful data for each command.
  3. Monkeypatches the send_message() function to print output locally
     instead of hitting the HTTP bridge (no bridge required!).
  4. Calls each slash command handler directly.
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime, timedelta

# ── Point the engine at our temp databases ──────────────────────────
_dir = os.path.dirname(os.path.abspath(__file__))
store_dir = os.path.join(_dir, "store")
os.makedirs(store_dir, exist_ok=True)

MESSAGES_DB = os.path.join(store_dir, "messages.db")
WHATSAPP_DB = os.path.join(store_dir, "whatsapp.db")

# Inject env vars before importing engine/wacmd
os.environ["MESSAGES_DB_PATH"] = MESSAGES_DB
os.environ["WHATSAPP_DB_PATH"] = WHATSAPP_DB
os.environ["OWNER_PHONE_NUMBER"] = "919876543210"
# Keep GEMINI_API_KEY from your .env if set; otherwise AI will give a polite error

from dotenv import load_dotenv
load_dotenv(os.path.join(_dir, ".env"), override=False)  # .env can override the defaults above

TEST_GROUP_JID = "120363000000000001@g.us"
OWNER_JID = "919876543210@s.whatsapp.net"
OTHER_JID = "911111111111@s.whatsapp.net"


# ── 1. Seed the mock databases ────────────────────────────────────────
def seed_databases():
    """Create minimal SQLite tables and populate them with realistic test data."""
    print("🌱 Seeding mock databases...")

    # messages.db
    msg_conn = sqlite3.connect(MESSAGES_DB)
    msg_conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            chat_jid TEXT,
            sender TEXT,
            content TEXT,
            timestamp TEXT
        );
        DELETE FROM messages;
    """)

    speakers = [
        ("919876543210@s.whatsapp.net", "Sameer"),
        ("911111111111@s.whatsapp.net",  "Priya"),
        ("912222222222@s.whatsapp.net",  "Rahul"),
    ]
    fake_messages = [
        # Ongoing initiative 1: API Launch (older history)
        ("Sameer", "We need to finalise the payment API before Friday.", -25 * 24),
        ("Priya",  "I'm blocked on the auth token from Rahul's team.", -24 * 24),
        ("Rahul",  "Will send it by end of day today.", -23 * 24),
        ("Sameer", "Please update the Notion doc once that's done.", -22 * 24),

        # Ongoing initiative 2: Dashboard (older history)
        ("Rahul",  "Dashboard designs are ready for review.", -20 * 24),
        ("Priya",  "Looks good! Just minor tweaks on the mobile breakpoint.", -19 * 24),
        ("Sameer", "Let's schedule a design review for Monday 3pm.", -18 * 24),

        # Decisions / escalations
        ("Sameer", "Confirmed: we're going live on 25th April, not May.", -15 * 24),
        ("Priya",  "Got it. Will update all stakeholder docs accordingly.", -14 * 24),
        ("Rahul",  "The QA environment is ready.", -13 * 24),

        # Recent 24h activity — these fall inside the recap window
        ("Sameer", "Quick check-in — QA sign off status from last night?", -3),
        ("Rahul",  "95% done, found 2 minor bugs, will fix by noon today.", -2),
        ("Priya",  "Will run smoke tests after Rahul's fixes and share results.", -1),
    ]

    rows = []
    for i, (speaker, msg, offset_hours) in enumerate(fake_messages):
        # Use human-readable names as the sender column so the fallback query
        # returns clean names without requiring whatsapp.db JOINs in the test env.
        ts = (datetime.now() + timedelta(hours=offset_hours)).isoformat()
        rows.append((f"msg_{i}", TEST_GROUP_JID, speaker, msg, ts))

    msg_conn.executemany(
        "INSERT INTO messages (id, chat_jid, sender, content, timestamp) VALUES (?,?,?,?,?)",
        rows
    )
    msg_conn.commit()
    msg_conn.close()

    # whatsapp.db — contacts only, so sender names resolve nicely
    wa_conn = sqlite3.connect(WHATSAPP_DB)
    wa_conn.executescript("""
        CREATE TABLE IF NOT EXISTS whatsmeow_contacts (
            their_jid TEXT PRIMARY KEY,
            push_name  TEXT,
            full_name  TEXT,
            first_name TEXT,
            business_name TEXT
        );
        CREATE TABLE IF NOT EXISTS whatsmeow_lid_map (
            lid TEXT PRIMARY KEY,
            pn  TEXT
        );
        CREATE TABLE IF NOT EXISTS whatsmeow_message_secrets (
            message_id TEXT,
            chat_jid   TEXT,
            sender_jid TEXT
        );
        DELETE FROM whatsmeow_contacts;
    """)
    wa_conn.executemany(
        "INSERT OR REPLACE INTO whatsmeow_contacts (their_jid, push_name) VALUES (?,?)",
        [
            ("919876543210@s.whatsapp.net", "Sameer"),
            ("911111111111@s.whatsapp.net",  "Priya"),
            ("912222222222@s.whatsapp.net",  "Rahul"),
        ]
    )
    wa_conn.commit()
    wa_conn.close()

    print("✅ Mock databases seeded.\n")


# ── 2. Monkeypatch send_message ────────────────────────────────────────
import wacmd

_original_send = wacmd.send_message

def _mock_send(jid: str, text: str):
    print("\n" + "─" * 60)
    print(f"📲  Message → {jid}")
    print("─" * 60)
    print(text)
    print("─" * 60 + "\n")

wacmd.send_message = _mock_send


# ── 3. Run commands ────────────────────────────────────────────────────
ALL_COMMANDS = ["/help", "/recap", "/pending", "/sotu", "/stats"]


def run_command(cmd: str):
    print(f"\n{'═' * 60}")
    print(f"  Testing: {cmd}")
    print(f"{'═' * 60}")
    wacmd.handle_command(TEST_GROUP_JID, OWNER_JID, cmd)


def test_owner_block():
    print(f"\n{'═' * 60}")
    print(f"  Testing: Owner Block (should be silently rejected)")
    print(f"{'═' * 60}")
    wacmd.handle_command(TEST_GROUP_JID, OTHER_JID, "/help")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local test harness for wa-slash-commands")
    parser.add_argument("--cmd", help="Run a single command (e.g. /recap)", default=None)
    parser.add_argument("--skip-seed", action="store_true", help="Skip seeding the database (use existing)")
    args = parser.parse_args()

    if not args.skip_seed:
        seed_databases()

    test_owner_block()

    if args.cmd:
        run_command(args.cmd)
    else:
        for cmd in ALL_COMMANDS:
            run_command(cmd)
