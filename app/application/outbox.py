from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class OutboxEventDraft:
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    payload: dict[str, Any]


@dataclass(slots=True)
class OutboxEvent:
    id: UUID
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    payload: dict[str, Any]
    status: str
    attempts: int
    created_at: datetime
    published_at: datetime | None
    last_error: str | None
