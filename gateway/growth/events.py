from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class DomainEvent(BaseModel):
    event_id: str
    event_type: str = Field(min_length=3, pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    schema_version: int = Field(default=1, ge=1)
    channel_id: str
    workflow_id: str
    timestamp: datetime
    payload: dict[str, Any]
    correlation_id: str
    causation_id: str | None = None
    idempotency_key: str = Field(min_length=3)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        channel_id: str,
        workflow_id: str,
        payload: dict[str, Any],
        correlation_id: str,
        causation_id: str | None,
        idempotency_key: str,
    ) -> "DomainEvent":
        return cls(
            event_id=str(uuid4()),
            event_type=event_type,
            channel_id=channel_id,
            workflow_id=workflow_id,
            timestamp=datetime.now(UTC),
            payload=payload,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
        )
