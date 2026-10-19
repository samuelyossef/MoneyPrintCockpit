from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from .events import DomainEvent


LOCAL_ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
LOCAL_USER_ID = UUID("00000000-0000-0000-0000-000000000002")


class GrowthStoreUnavailable(RuntimeError):
    pass


class GrowthStore:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn if dsn is not None else os.getenv("GROWTH_DATABASE_URL", "")

    def _connect(self):
        if not self.dsn:
            raise GrowthStoreUnavailable("GROWTH_DATABASE_URL não está configurada.")
        try:
            import psycopg
        except ImportError as exc:
            raise GrowthStoreUnavailable("O driver PostgreSQL não está instalado.") from exc
        try:
            return psycopg.connect(self.dsn)
        except Exception as exc:
            raise GrowthStoreUnavailable(f"PostgreSQL do Growth OS indisponível: {exc}") from exc

    def ensure_schema(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self._connect() as connection:
            connection.execute(schema)
            connection.execute(
                "INSERT INTO organizations(id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                (LOCAL_ORGANIZATION_ID, "MoneyPrinter Local"),
            )
            connection.execute(
                "INSERT INTO users(id, organization_id, email, role) VALUES (%s, %s, %s, 'owner') "
                "ON CONFLICT (id) DO NOTHING",
                (LOCAL_USER_ID, LOCAL_ORGANIZATION_ID, "local@moneyprinter.invalid"),
            )

    def ping(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1").fetchone()[0] == 1

    def sync_channel(self, channel: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO growth_channels(id, organization_id, platform, name, niche, language, settings) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET platform=excluded.platform, name=excluded.name, niche=excluded.niche, "
                "language=excluded.language, settings=excluded.settings, updated_at=now()",
                (
                    channel["id"],
                    LOCAL_ORGANIZATION_ID,
                    channel["platform"],
                    channel["name"],
                    channel.get("niche", ""),
                    channel.get("language", "pt-BR"),
                    json.dumps({"legacy_status": channel.get("status", "unknown")}),
                ),
            )

    def record_event(self, event: DomainEvent) -> bool:
        payload = event.model_dump(mode="json")
        with self._connect() as connection:
            inserted = connection.execute(
                "INSERT INTO domain_events(event_id, event_type, schema_version, channel_id, workflow_id, timestamp, payload, "
                "correlation_id, causation_id, idempotency_key) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s) "
                "ON CONFLICT (idempotency_key) DO NOTHING RETURNING event_id",
                (
                    event.event_id,
                    event.event_type,
                    event.schema_version,
                    event.channel_id,
                    event.workflow_id,
                    event.timestamp,
                    json.dumps(event.payload),
                    event.correlation_id,
                    event.causation_id,
                    event.idempotency_key,
                ),
            ).fetchone()
            if not inserted:
                return False
            connection.execute(
                "INSERT INTO outbox(event_id, topic, payload) VALUES (%s, %s, %s::jsonb)",
                (event.event_id, event.event_type, json.dumps(payload)),
            )
            return True

    def create_workflow(self, *, workflow_id: UUID, channel_id: UUID, workflow_type: str, input_data: dict[str, Any]) -> dict[str, Any]:
        temporal_id = f"growth-{workflow_id}"
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO workflows(id, channel_id, temporal_workflow_id, workflow_type, state, input) "
                "VALUES (%s, %s, %s, %s, 'planned', %s::jsonb) RETURNING *",
                (workflow_id, channel_id, temporal_id, workflow_type, json.dumps(input_data)),
            )
            row = cursor.fetchone()
            columns = [column.name for column in cursor.description]
        return dict(zip(columns, row, strict=True))

    def update_workflow(
        self,
        workflow_id: str,
        *,
        state: str,
        current_step: str,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        completed = state in {"completed", "failed", "cancelled"}
        with self._connect() as connection:
            connection.execute(
                "UPDATE workflows SET state=%s, current_step=%s, output=%s::jsonb, error=%s::jsonb, updated_at=now(), "
                "completed_at=CASE WHEN %s THEN now() ELSE completed_at END WHERE id=%s",
                (state, current_step, json.dumps(output) if output is not None else None,
                 json.dumps(error) if error is not None else None, completed, workflow_id),
            )

    def workflow(self, workflow_id: UUID) -> dict[str, Any] | None:
        with self._connect() as connection:
            cursor = connection.execute("SELECT * FROM workflows WHERE id=%s", (workflow_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(zip((column.name for column in cursor.description), row, strict=True))
