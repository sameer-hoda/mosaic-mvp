"""
ops/sender.py -- Send WhatsApp messages via the slash-commands bridge API
"""
import requests
from typing import Optional


BRIDGE_API_URL = "http://localhost:8080/api/send"


def send_whatsapp_message(group_id: str, message: str, dry_run: bool = False) -> bool:
    """
    Send a WhatsApp message to a group via the bridge.
    Returns True if sent successfully.
    """
    if dry_run:
        print(f"[DRY RUN] Would send to {group_id}: {message[:100]}...")
        return True

    try:
        resp = requests.post(
            BRIDGE_API_URL,
            json={"recipient": group_id, "message": message},
            timeout=10
        )
        if resp.status_code == 200:
            print(f"Message sent to {group_id}")
            return True
        else:
            print(f"Failed to send message: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"Error sending message: {e}")
        return False


def send_nudge(group_id: str, card_title: str, message: str, dry_run: bool = False) -> bool:
    """
    Send a nudge message with proper formatting.
    """
    formatted = f"[WA Ops] {card_title}\n\n{message}"
    return send_whatsapp_message(group_id, formatted, dry_run=dry_run)
