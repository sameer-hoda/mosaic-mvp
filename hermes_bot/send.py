#!/usr/bin/env python3
"""Send NB: yes-gated confirmed sends to contacts only.

``send 2B`` shows a confirm screen naming the exact chat + text. Only
``yes`` within 5 minutes sends; anything else cancels. Guards: never a
group, never a non-contact, never a chat the owner never wrote in, max
30 sends/day. Dry-run unless MOSAIC_LIVE=1 (tests never set it).
"""

import logging
import os
import re
import time
from typing import Callable, Optional

log = logging.getLogger("hermes_bot.send")

CONFIRM_WINDOW_SECONDS = 300.0
SEND_CAP_PER_DAY = 30

_SEND_RE = re.compile(r"^send\s+(\S+)\s*$", re.IGNORECASE)


def is_live() -> bool:
    return os.getenv("MOSAIC_LIVE", "") == "1"


class SendEngine:
    """Confirm machine with injectable deps (offline-testable)."""

    def __init__(
        self,
        is_owner: Callable[[str, str], bool],
        self_jid: Optional[str] = None,
        enqueue: Callable[[str, str], None] = lambda jid, text: None,
        is_contact: Callable[[str], bool] = lambda jid: False,
        owner_wrote_in_chat: Callable[[str], bool] = lambda jid: False,
        sends_today: Callable[[], int] = lambda: 0,
        record_send: Callable[[], None] = lambda: None,
        clock: Callable[[], float] = time.time,
        resolve: Callable[[str], Optional[dict]] = lambda ref: None,
    ) -> None:
        self._is_owner = is_owner
        self._self_jid = self_jid or ""
        self._enqueue = enqueue
        self._is_contact = is_contact
        self._owner_wrote = owner_wrote_in_chat
        self._sends_today = sends_today
        self._record_send = record_send
        self._clock = clock
        self._resolve = resolve
        self._pending: Optional[dict] = None
        self.last_preview = ""

    def handle_event(self, chat_jid: str, sender_jid: str, content: str) -> str:
        text = (content or "").strip()
        match = _SEND_RE.match(text)
        if match:
            return self._request(chat_jid, sender_jid, match.group(1))
        if self._pending and chat_jid in (
                self._pending["origin"], self._self_jid):
            if not self._is_owner(chat_jid, sender_jid):
                return "rejected-not-owner"
            if text.lower() == "yes":
                return self._confirm()
            return self._cancel()
        return "ignored"

    # ── request ──────────────────────────────────────────────
    def _request(self, chat_jid: str, sender_jid: str, ref: str) -> str:
        if not self._is_owner(chat_jid, sender_jid):
            return "rejected-not-owner"
        target = self._resolve(ref)
        if target is None:
            self._enqueue(self._self_jid, f"❓ No draft `{ref}` — try `draft N` first.")
            return "rejected-unknown-ref"
        dest = target.get("chat_jid", "")
        name = target.get("chat_name") or dest
        body = target.get("text", "")
        if dest.endswith("@g.us"):
            return "rejected-group"
        try:
            if not self._is_contact(dest):
                return "rejected-non-contact"
        except Exception:
            return "rejected-non-contact"
        try:
            if not self._owner_wrote(dest):
                return "rejected-no-owner-history"
        except Exception:
            return "rejected-no-owner-history"
        try:
            if self._sends_today() >= SEND_CAP_PER_DAY:
                return "rejected-cap"
        except Exception:
            pass
        self._pending = {"origin": chat_jid, "dest": dest, "name": name,
                         "text": body,
                         "expires_at": self._clock() + CONFIRM_WINDOW_SECONDS}
        self.last_preview = (
            f"📤 Send to {name} ({dest})?\n\n{body}\n\n"
            f"Reply `yes` within 5 min to send. Anything else cancels.")
        self._enqueue(self._self_jid, self.last_preview)
        return "confirm-pending"

    # ── yes / anything-else ──────────────────────────────────
    def _confirm(self) -> str:
        pending, self._pending = self._pending, None
        if self._clock() > pending["expires_at"]:
            self._enqueue(self._self_jid, "⌛ That send expired — re-run `send NB`.")
            return "confirm-expired"
        try:
            if self._sends_today() >= SEND_CAP_PER_DAY:
                return "rejected-cap"
        except Exception:
            pass
        if not is_live():
            log.info("send: dry-run, not sending to %s", pending["dest"])
            self._enqueue(self._self_jid,
                           f"🧪 Dry-run: would send to {pending['name']}:\n\n"
                           f"{pending['text']}")
            return "posted-dry-run"
        self._enqueue(pending["dest"], pending["text"])
        try:
            self._record_send()
        except Exception as exc:
            log.warning("send: record failed (%s)", exc)
        return "posted"

    def _cancel(self) -> str:
        self._pending = None
        self._enqueue(self._self_jid, "Cancelled — nothing sent.")
        return "cancelled"
