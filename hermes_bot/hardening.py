#!/usr/bin/env python3
"""Hardening: retention prune, unlink wipe, bridge disconnect watch.

- 30-day retention prune over mosaic.db (event_log, sent drafts,
  stale alias maps). Items are the owner's tracker and are kept.
- Unlink wipe of the data dir (known store files; config kept only
  when keep_key is set).
- BridgeWatch: probe the bridge each tick, notify the owner on
  offline/recovered transitions only — never on every tick.
"""

import logging
import os
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("hermes_bot.hardening")

RETENTION_DAYS = 30

# Files owned by this instance's data dir. config.json holds the API
# key only when keep_key is set (Unlink without key preservation).
_WIPE_FILES = ("whatsapp.db", "messages.db", "hermes.db", "mosaic.db",
               "session.json", "pending_messages.json", "setup.json",
               "mosaic.db-wal", "mosaic.db-shm", "messages.db-wal",
               "messages.db-shm")


def prune_retention(conn, days: int = RETENTION_DAYS,
                    now: float | None = None) -> dict:
    """Delete rows older than `days`. Returns per-table counts."""
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    out = {}
    out["event_log"] = conn.execute(
        "DELETE FROM event_log WHERE ts < ?", (cutoff,)).rowcount or 0
    out["drafts"] = conn.execute(
        "DELETE FROM drafts WHERE sent_at IS NOT NULL AND sent_at < ?",
        (cutoff,)).rowcount or 0
    try:
        out["item_alias"] = conn.execute(
            "DELETE FROM item_alias WHERE created_at < ?", (cutoff,)).rowcount or 0
    except Exception:
        out["item_alias"] = 0
    conn.commit()
    total = sum(out.values())
    if total:
        log.info("hardening: pruned %s rows older than %sd", total, days)
    return out


def wipe_data_dir(data_dir: "str | Path", keep_key: bool = False) -> list:
    """Remove known store files (Unlink). Returns removed names."""
    root = Path(data_dir)
    removed = []
    for name in _WIPE_FILES:
        target = root / name
        try:
            if target.exists():
                target.unlink()
                removed.append(name)
        except OSError as exc:
            log.warning("hardening: wipe %s failed (%s)", name, exc)
    if not keep_key:
        target = root / "config.json"
        try:
            if target.exists():
                target.unlink()
                removed.append("config.json")
        except OSError as exc:
            log.warning("hardening: wipe config.json failed (%s)", exc)
    return removed


def data_dir() -> Path:
    return Path(os.getenv("MOSAIC_DATA_DIR", "/data"))


class BridgeWatch:
    """Notify the owner when the bridge goes offline / recovers."""

    def __init__(self, probe: Callable[[], bool],
                 notify: Callable[[str], None]) -> None:
        self._probe = probe
        self._notify = notify
        self._was_up: bool | None = None

    def tick(self) -> str:
        try:
            up = bool(self._probe())
        except Exception:
            up = False
        prev, self._was_up = self._was_up, up
        if up and prev is False:
            self._notify("✅ *Mosaic* · bridge back online.")
            return "recovered-notice-sent"
        if not up and prev is not False:
            if prev is None:
                return "down"  # first sighting: stay quiet, wait a tick
            self._notify("⚠️ *Mosaic* · bridge offline, retrying…")
            return "down-notice-sent"
        return "up" if up else "down"
