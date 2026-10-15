"""
ops/models.py — Pydantic data models for WA Ops Platform
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, ConfigDict


class CardStatus(str, Enum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"


class CardMode(str, Enum):
    ACTIVE = "active"
    PASSIVE = "passive"


class CardOrigin(str, Enum):
    INGESTION = "ingestion_engine"
    USER = "user"


class Message(BaseModel):
    id: str
    chat_jid: str
    sender_jid: Optional[str] = None
    sender_name: Optional[str] = None
    content: Optional[str] = None
    timestamp: datetime
    is_from_me: bool = False
    media_type: Optional[str] = None
    filename: Optional[str] = None


class Group(BaseModel):
    jid: str
    name: str
    participant_count: Optional[int] = None
    whitelisted: bool = False
    last_message_time: Optional[datetime] = None
    created_at: Optional[datetime] = None


class WikiPerson(BaseModel):
    name: str
    role: Optional[str] = None
    activity: str = "medium"  # high | medium | low
    mention_count: int = 0


class WikiTopic(BaseModel):
    title: str
    status: str = "active"  # active | resolved
    last_discussed: Optional[str] = None


class WikiActionItem(BaseModel):
    item: str
    owner: Optional[str] = None
    eta: Optional[str] = None
    status: str = "open"  # open | resolved


class WikiPage(BaseModel):
    group_id: str
    overview: str = ""
    people: List[WikiPerson] = Field(default_factory=list)
    topics: List[WikiTopic] = Field(default_factory=list)
    action_items: List[WikiActionItem] = Field(default_factory=list)
    thread_log: List[str] = Field(default_factory=list)
    last_updated: Optional[datetime] = None


class ProgressLogEntry(BaseModel):
    ts: datetime
    event: str  # scan | nudge_sent | reply_received | eta_updated | closure_detected | nudge_scheduled
    detail: str


class Card(BaseModel):
    id: str
    group_id: str
    group_name: Optional[str] = None
    title: str
    context: Optional[str] = None
    status: CardStatus = CardStatus.BACKLOG
    mode: CardMode = CardMode.PASSIVE
    origin: CardOrigin = CardOrigin.INGESTION
    key_people: List[str] = Field(default_factory=list)
    key_people_confidence: float = 0.0
    eta_raw: Optional[str] = None
    eta_parsed: Optional[datetime] = None
    next_nudge_at: Optional[datetime] = None
    nudge_count: int = 0
    progress_log: List[ProgressLogEntry] = Field(default_factory=list)
    nudge_log: List[NudgeLogEntry] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


class NudgeLogEntry(BaseModel):
    id: Optional[int] = None
    card_id: str
    group_id: str
    message_text: str
    sent_at: Optional[datetime] = None
    response_received: bool = False
