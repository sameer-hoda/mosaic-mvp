#!/usr/bin/env python3
"""Private slash-command engine (Worker C).

Parses ``/catchup /pending /sotu /eli5`` (plus ``/mute /unmute``), analyses
the target chat, and delivers the answer to the user's self-chat only.

Self-chat resolution reuses Worker B's approach
(``hermes_bot.db.get_mechat_chat_jid``); the ``MOSAIC_SELF_JID`` env fallback
below is temporary.
TODO: drop the MOSAIC_SELF_JID fallback once Worker B's resolution is
confirmed as the single source of truth on main.
"""

import logging
import os
import re
import time
from typing import Callable, Optional

from hermes_bot import slash_format

log = logging.getLogger("hermes_bot.slash")

_COMMAND_RE = re.compile(r"^/([a-z0-9]+)(!)?\s*(.*)$", re.DOTALL)
KNOWN_COMMANDS = frozenset({"catchup", "pending", "sotu", "eli5", "mute", "unmute"})
ANALYSIS_COMMANDS = frozenset({"catchup", "pending", "sotu", "eli5"})


def parse_command(text: str) -> Optional[dict]:
    """Parse a slash command. Returns {name, public, arg} or None."""
    if not text:
        return None
    stripped = text.strip()
    match = _COMMAND_RE.match(stripped)
    if not match:
        return None
    name, bang, arg = match.group(1).lower(), match.group(2), match.group(3)
    if name not in KNOWN_COMMANDS:
        return None
    return {"name": name, "public": bool(bang), "arg": (arg or "").strip()}


def resolve_self_jid() -> str:
    """Resolve the owner's self-chat JID via Worker B's resolution."""
    try:
        from hermes_bot.db import get_mechat_chat_jid
        jid = get_mechat_chat_jid()
        if jid:
            return jid
    except Exception:
        pass
    # TODO: drop this fallback once Worker B's resolution is confirmed.
    return os.getenv("MOSAIC_SELF_JID", "")


def default_owner_check(chat_jid: str, sender_jid: str) -> bool:
    from hermes_bot.db import is_message_from_owner
    try:
        return bool(is_message_from_owner(chat_jid, sender_jid))
    except Exception:
        return False


CONFIRM_WINDOW_SECONDS = 300.0  # `yes` must arrive within 5 minutes


class MuteStore:
    """Per-chat mute list, persisted as JSON (in-memory when path is None)."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path
        self._muted: set[str] = set()
        if path:
            try:
                import json
                with open(path, encoding="utf-8") as fh:
                    self._muted = set(json.load(fh))
            except (OSError, ValueError):
                self._muted = set()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            import json
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(sorted(self._muted), fh)
        except OSError as exc:
            log.warning("slash: mute persist failed (%s)", exc)

    def is_muted(self, chat_jid: str) -> bool:
        return chat_jid in self._muted

    def mute(self, chat_jid: str) -> None:
        self._muted.add(chat_jid)
        self._save()

    def unmute(self, chat_jid: str) -> None:
        self._muted.discard(chat_jid)
        self._save()


def default_mute_store() -> MuteStore:
    try:
        from hermes_bot import config
        return MuteStore(path=str(config.STORE_DIR / "muted_chats.json"))
    except Exception:
        return MuteStore()


class ConfirmStore:
    """One-shot pending public posts, keyed by origin chat JID."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._pending: dict[str, dict] = {}

    def request(self, chat_jid: str, text: str, chat_name: str) -> None:
        self._pending[chat_jid] = {
            "text": text,
            "chat_name": chat_name,
            "expires_at": self._clock() + CONFIRM_WINDOW_SECONDS,
        }

    def confirm(self, chat_jid: str) -> Optional[dict]:
        entry = self._pending.get(chat_jid)
        if entry is None:
            return None
        del self._pending[chat_jid]
        if self._clock() > entry["expires_at"]:
            return {"expired": True}
        return entry

    def has(self, chat_jid: str) -> bool:
        return chat_jid in self._pending

    def single_pending(self) -> str:
        """Return the pending chat when exactly one exists, else ''."""
        if len(self._pending) == 1:
            return next(iter(self._pending))
        return ""


def default_deleter(chat_jid: str, msg_id: Optional[str]) -> None:
    """Best-effort invoke-delete via the bridge delete path (2 s budget)."""
    import requests
    from hermes_bot import config
    if not msg_id:
        log.warning("slash: deletion impossible (no message id for %s)", chat_jid)
        return
    try:
        resp = requests.post(
            f"{config.BRIDGE_URL}/api/delete",
            json={"chat_jid": chat_jid, "message_id": msg_id},
            timeout=2,
        )
        if resp.status_code >= 300:
            log.warning("slash: deletion impossible (bridge status %s)", resp.status_code)
    except Exception as exc:
        log.warning("slash: deletion impossible (%s)", exc)


class Engine:
    """Slash-command dispatcher with injectable deps (offline-testable)."""

    def __init__(
        self,
        is_owner: Callable[[str, str], bool] = default_owner_check,
        self_jid: Optional[str] = None,
        fetch: Optional[Callable[[str], dict]] = None,
        send_self: Optional[Callable[[str, str], None]] = None,
        send_group: Optional[Callable[[str, str], None]] = None,
        deleter: Callable[[str, Optional[str]], None] = default_deleter,
        clock: Callable[[], float] = time.time,
        mute_store: Optional[MuteStore] = None,
    ) -> None:
        self._is_owner = is_owner
        self._self_jid = self_jid  # resolved lazily via resolve_self_jid()
        self._fetch = fetch or (lambda chat_jid: {"chat_name": chat_jid, "messages": []})
        self._send_self = send_self or (lambda jid, text: None)
        self._send_group = send_group or (lambda jid, text: None)
        self._deleter = deleter
        self._clock = clock
        self._confirms = ConfirmStore(clock)
        self._mutes = mute_store or MuteStore()

    @property
    def _self(self) -> str:
        return self._self_jid or resolve_self_jid()

    def handle_event(self, chat_jid: str, sender_jid: str, content: str,
                     msg_id: Optional[str] = None) -> str:
        text = (content or "").strip()
        if text.lower() == "yes":
            return self._handle_yes(chat_jid, sender_jid)
        parsed = parse_command(content or "")
        if parsed is None:
            return "ignored"
        if not self._is_owner(chat_jid, sender_jid):
            return "rejected-not-owner"
        try:
            self._deleter(chat_jid, msg_id)
        except Exception as exc:  # best-effort: never block the answer
            log.warning("slash: deletion impossible (%s)", exc)
        name = parsed["name"]
        if name == "mute":
            self._mutes.mute(chat_jid)
            self._send_self(self._self, f"🔇 Muted slash commands for {chat_jid}.")
            return "muted"
        if name == "unmute":
            self._mutes.unmute(chat_jid)
            self._send_self(self._self, f"🔊 Unmuted slash commands for {chat_jid}.")
            return "unmuted"
        if self._mutes.is_muted(chat_jid):
            return "muted"
        if name not in ANALYSIS_COMMANDS:
            return "ignored"
        bundle = self._fetch(chat_jid)
        chat_name = bundle.get("chat_name") or chat_jid
        messages = bundle.get("messages") or []
        answer = slash_format.format_answer(name, chat_name, messages, parsed["arg"])
        if parsed["public"]:
            self._confirms.request(chat_jid, answer, chat_name)
            self._send_self(
                self._self, slash_format.format_public_preview(answer, chat_name))
            return "confirm-pending"
        self._send_self(self._self, answer)
        return "private-sent"

    def _handle_yes(self, chat_jid: str, sender_jid: str) -> str:
        if not self._is_owner(chat_jid, sender_jid):
            return "rejected-not-owner"
        target = chat_jid
        if not self._confirms.has(target) and chat_jid == self._self:
            target = self._confirms.single_pending()
        if not target:
            return "ignored"
        entry = self._confirms.confirm(target)
        if entry is None:
            return "ignored"
        if entry.get("expired"):
            self._send_self(self._self, "⌛ That preview expired — re-run the command.")
            return "confirm-expired"
        self._send_group(target, entry["text"])
        return "posted"


def build_default_engine() -> Engine:
    """Production wiring: Worker B resolution, sender outbox, bridge delete."""
    from hermes_bot import db
    from hermes_bot.sender import enqueue_message

    def _fetch(chat_jid: str) -> dict:
        try:
            rows = db.get_chat_messages(chat_jid)
        except Exception:
            return {"chat_name": chat_jid, "messages": []}
        chat_name = rows[0].get("chat_name", chat_jid) if rows else chat_jid
        return {
            "chat_name": chat_name,
            "messages": [f"{r.get('sender', '?')}: {r.get('content', '')}" for r in rows],
        }

    return Engine(
        is_owner=default_owner_check,
        fetch=_fetch,
        send_self=lambda jid, text: enqueue_message(jid, text),
        send_group=lambda jid, text: enqueue_message(jid, text),
        deleter=default_deleter,
        mute_store=default_mute_store(),
    )


_ENGINE: Optional[Engine] = None


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = build_default_engine()
    return _ENGINE
