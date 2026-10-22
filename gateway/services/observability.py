"""Notifications and observability event log."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4

from gateway import main as m


def notify(event_key: str, kind: str, title: str, detail: str = "", job_id: str | None = None) -> None:
    with m.database() as db:
        db.execute(
            "INSERT OR IGNORE INTO notifications(id, event_key, kind, title, detail, job_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid4()), event_key[:200], kind[:50], title[:300], detail[:1000], job_id, m.now()),
        )


def observability_service(path: str) -> str:
    segment = path.removeprefix("/api/").split("/", 1)[0]
    return {
        "jobs": "jobs", "projects": "projects", "scripts": "moneyprinterturbo",
        "channels": "channels", "agents": "agents", "analytics": "analytics",
        "settings": "settings",
    }.get(segment, "gateway")


def observability_trace_id(value: str) -> str:
    candidate = value.strip()[:80]
    return candidate if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", candidate) else f"tr_{uuid4().hex[:12]}"


def record_observability_event(
    *, trace_id: str, method: str, path: str, status_code: int, duration_ms: float,
    spans: list[dict[str, Any]] | None = None, error: str | None = None,
) -> None:
    """Persist a bounded, credential-free request event for the local observability screen."""
    level = "ERROR" if status_code >= 500 else "WARN" if status_code >= 400 else "INFO"
    service = m.observability_service(path)
    message = error or f"{method.upper()} {path} respondeu {status_code} em {duration_ms:.1f} ms"
    context = {"method": method.upper(), "path": path, "status_code": status_code}
    with m.database() as db:
        db.execute(
            "INSERT INTO observability_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid4()), m.now(), level, service, message, trace_id, method.upper(), path,
             status_code, duration_ms, json.dumps(context), json.dumps(spans or [])),
        )
        db.execute(
            "DELETE FROM observability_events WHERE id IN (SELECT id FROM observability_events ORDER BY timestamp DESC LIMIT -1 OFFSET 2000)"
        )


def row_as_observability_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "timestamp": row["timestamp"], "level": row["level"],
        "service": row["service"], "message": row["message"], "trace_id": row["trace_id"],
        "method": row["method"], "path": row["path"], "status_code": row["status_code"],
        "duration_ms": row["duration_ms"], "context": json.loads(row["context_json"]),
        "spans": json.loads(row["spans_json"]),
    }
