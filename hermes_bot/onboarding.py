"""Self-chat onboarding: no other chat is ever addressed.

Every send on this path goes to the resolved self-chat JID only. The
sender stays a dry-run unless MOSAIC_LIVE=1, so tests and fresh checkouts
never emit a real message.
"""

import os

from hermes_bot import sender

WELCOME_TEXT = (
    "🤖 *Mosaic* · Ready\n"
    "This is your self-chat — everything stays between us.\n"
    "/help for commands"
)


def create_group(*args, **kwargs):
    """Stub: mosaic is self-chat only, so this path can never run."""
    raise RuntimeError("mosaic is self-chat only: refusing that call")


def _resolved_self_chat():
    from hermes_bot.db import get_mechat_chat_jid

    return get_mechat_chat_jid()


def _assert_self_chat_only(jid, resolved):
    if jid.endswith("@g.us"):
        raise ValueError(f"refusing to address a group chat: {jid}")
    if jid != resolved:
        raise ValueError(f"refusing to address a non-self chat: {jid}")


def send_welcome(jid=None, text=None):
    """Send the welcome message to the resolved self-chat.

    Returns {"sent": bool, "dry_run": bool, "jid": target}. Dry-run
    (the default) logs and sends nothing; MOSAIC_LIVE=1 enqueues.
    """
    resolved = _resolved_self_chat()
    target = jid or resolved
    _assert_self_chat_only(target, resolved)
    body = WELCOME_TEXT if text is None else text
    if os.getenv("MOSAIC_LIVE", "") != "1":
        print(f"[onboarding] dry-run: welcome for {target} not sent")
        return {"sent": False, "dry_run": True, "jid": target}
    sender.enqueue_message(target, body)
    return {"sent": True, "dry_run": False, "jid": target}


def run_onboarding(text=None):
    """Resolve the self-chat and deliver the welcome. Group-safe by construction."""
    return send_welcome(text=text)
