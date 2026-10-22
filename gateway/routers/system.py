"""Health, version, status, logs and notification routes."""
from __future__ import annotations

import os
import sqlite3
from typing import Any, AsyncIterator, Literal
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse

from gateway import main as m

router = APIRouter()


@router.get("/api/version")
async def app_version() -> dict[str, str]:
    return {"name": "MoneyPrinter Cockpit", "version": m.APP_VERSION}


@router.get("/api/health/live")
async def health_live() -> dict[str, Any]:
    try:
        with m.database() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": True}
    except sqlite3.Error:
        return JSONResponse({"status": "degraded", "database": False}, status_code=503)


@router.get("/api/health/ready")
@router.get("/api/health")
async def health() -> Response:
    state = await m.health_status()
    return JSONResponse(state, status_code=200 if state["status"] == "ok" else 503)


@router.get("/api/status")
async def system_status() -> dict[str, Any]:
    ready = await m.health_status()
    provider = m.provider_readiness()
    with m.database() as db:
        job = db.execute("SELECT * FROM video_jobs WHERE state IN ('queued','running') AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 1").fetchone()
    return {
        "gateway": "online" if ready["database"] else "offline", "turbo": "online" if ready["turbo"] else "offline",
        "llm_provider": provider["llm_provider"],
        "llm_model": provider["nvidia_text_model"] if provider["llm_provider"] == "nvidia" else provider["moonshot_model"],
        "environment": os.getenv("APP_ENV", "local"), "active_job": m.row_as_job(job) if job else None,
    }


@router.get("/api/logs")
def list_observability_events(
    limit: int = 200, q: str = "", level: str = "", service: str = "",
) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    clauses: list[str] = []
    params: list[Any] = []
    if q.strip():
        clauses.append("(lower(message) LIKE ? OR lower(trace_id) LIKE ? OR lower(path) LIKE ?)")
        needle = f"%{q.strip().lower()}%"
        params.extend([needle, needle, needle])
    if level.strip():
        clauses.append("level = ?")
        params.append(level.strip().upper())
    if service.strip():
        clauses.append("service = ?")
        params.append(service.strip())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with m.database() as db:
        total = db.execute(f"SELECT count(*) AS total FROM observability_events{where}", params).fetchone()["total"]
        rows = db.execute(
            f"SELECT * FROM observability_events{where} ORDER BY timestamp DESC LIMIT ?", (*params, limit)
        ).fetchall()
        services = [row["service"] for row in db.execute("SELECT DISTINCT service FROM observability_events ORDER BY service")]
    return {"items": [m.row_as_observability_event(row) for row in rows], "total": total, "services": services}


@router.get("/api/notifications")
def list_notifications(limit: int = 100) -> dict[str, Any]:
    with m.database() as db:
        rows = db.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        unread = db.execute("SELECT count(*) AS count FROM notifications WHERE read_at IS NULL").fetchone()["count"]
    return {"items": [dict(row) for row in rows], "unread": unread}


@router.patch("/api/notifications/{notification_id}/read")
def read_notification(notification_id: str) -> dict[str, Any]:
    with m.database() as db:
        db.execute("UPDATE notifications SET read_at=COALESCE(read_at, ?) WHERE id=?", (m.now(), notification_id))
        row = db.execute("SELECT * FROM notifications WHERE id=?", (notification_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Notificação não encontrada")
    return dict(row)


@router.post("/api/notifications/read-all")
def read_all_notifications() -> dict[str, int]:
    with m.database() as db:
        updated = db.execute("UPDATE notifications SET read_at=? WHERE read_at IS NULL", (m.now(),)).rowcount
    return {"updated": updated}
