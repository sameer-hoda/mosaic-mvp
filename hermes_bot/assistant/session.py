import json
import uuid
import datetime
from pathlib import Path
from typing import Optional

from hermes_bot import config


def _tz():
    return config.TIMEZONE


def _now() -> str:
    return datetime.datetime.now(_tz()).isoformat()


def _parse_ts(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts)


class Session:
    def __init__(
        self,
        session_id: str = "",
        topic: str = "",
        started_at: str = "",
        last_message_at: str = "",
        message_count: int = 0,
        recent_messages: Optional[list[str]] = None,
        timeout_at: str = "",
        state: str = "active",
    ):
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.topic = topic
        self.started_at = started_at or _now()
        self.last_message_at = last_message_at or self.started_at
        self.message_count = message_count
        self.recent_messages = recent_messages or []
        self.timeout_at = timeout_at or ""
        self.state = state

    def is_timed_out(self) -> bool:
        if not self.timeout_at:
            return False
        return _parse_ts(self.timeout_at) < datetime.datetime.now(_tz())

    def touch(self):
        self.last_message_at = _now()
        self.message_count += 1
        self.timeout_at = (
            datetime.datetime.now(_tz()) + datetime.timedelta(minutes=config.SESSION_TIMEOUT_MINUTES)
        ).isoformat()

    def add_message(self, text: str):
        self.recent_messages.append(text)
        if len(self.recent_messages) > 10:
            self.recent_messages = self.recent_messages[-10:]

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "topic": self.topic,
            "started_at": self.started_at,
            "last_message_at": self.last_message_at,
            "message_count": self.message_count,
            "recent_messages": self.recent_messages,
            "timeout_at": self.timeout_at,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            session_id=d.get("session_id", ""),
            topic=d.get("topic", ""),
            started_at=d.get("started_at", ""),
            last_message_at=d.get("last_message_at", ""),
            message_count=d.get("message_count", 0),
            recent_messages=d.get("recent_messages", []),
            timeout_at=d.get("timeout_at", ""),
            state=d.get("state", "active"),
        )


class SessionManager:
    def __init__(self, filepath: str = ""):
        self.filepath = Path(filepath or config.SESSION_FILE)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        if self.filepath.exists():
            try:
                return json.loads(self.filepath.read_text())
            except (json.JSONDecodeError, Exception):
                pass
        return {"active_session": None, "archived_sessions": []}

    def save(self):
        self.filepath.write_text(json.dumps(self.data, indent=2))

    def get_active_session(self) -> Optional[Session]:
        if self.data.get("active_session"):
            session = Session.from_dict(self.data["active_session"])
            if session.is_timed_out():
                self._archive(session)
                return None
            return session
        return None

    def create_session(self, first_message: str) -> Session:
        session = Session()
        session.topic = first_message[:80]
        session.add_message(first_message)
        session.touch()
        self.data["active_session"] = session.to_dict()
        self.save()
        return session

    def update_session(self, session: Session):
        self.data["active_session"] = session.to_dict()
        self.save()

    def close_session(self, session: Session):
        self._archive(session)

    def clear_pending_confirmation(self, session: Session):
        session.state = "active"
        self.update_session(session)

    def set_awaiting_confirmation(self, session: Session):
        session.state = "awaiting_continuity_confirm"
        self.update_session(session)

    def _archive(self, session: Session):
        archived = self.data.get("archived_sessions", [])
        archived.append(session.to_dict())
        if len(archived) > 5:
            archived = archived[-5:]
        self.data["archived_sessions"] = archived
        self.data["active_session"] = None
        self.save()