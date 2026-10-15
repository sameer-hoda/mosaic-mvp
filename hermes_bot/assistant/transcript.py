"""
Simple transcript store for MeChat conversation context.

Replaces the old session.py — no sessions, no continuity state machine,
just a rolling list of user/assistant exchanges for Stage 1 context.
"""

import json
import time
from pathlib import Path

from hermes_bot import config

MAX_ENTRIES = 30  # ~15 turns (user + assistant)


class Transcript:
    def __init__(self, filepath: str = ""):
        self.filepath = Path(filepath or config.STORE_DIR / "transcript.json")
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text())
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, Exception):
                pass
        return []

    def save(self):
        self.filepath.write_text(json.dumps(self.entries, indent=2))

    def add(self, role: str, text: str):
        self.entries.append({
            "role": role,
            "text": text,
            "ts": time.time(),
        })
        if len(self.entries) > MAX_ENTRIES:
            self.entries = self.entries[-MAX_ENTRIES:]

    def get_formatted(self, last_n: int = MAX_ENTRIES) -> str:
        entries = self.entries[-last_n:]
        if not entries:
            return ""
        return "\n".join(f"{e['role']}: {e['text']}" for e in entries)

    def clear(self):
        self.entries = []
        self.save()
