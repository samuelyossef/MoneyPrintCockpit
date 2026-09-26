"""Local, same-origin gateway for the MoneyPrinterTurbo Cockpit."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import statistics
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from gateway.auth_crypto import admin_session_version, password_hash, session_token_hash, verify_password
from gateway.growth.api import router as growth_router
from gateway.growth.api import store as growth_store
from gateway.growth.api import OpportunityScoreRequest, PlanningWorkflowRequest, YPPGoalRequest
from gateway.growth.api import evaluate_goal, score_opportunity, start_planning_workflow
from gateway.growth.store import GrowthStoreUnavailable
from gateway.growth.storage import ObjectStorageUnavailable, ensure_media_bucket

DATA_DIR = Path(os.getenv("APP_DATA_DIR", "runtime"))
DATABASE = DATA_DIR / "cockpit.sqlite3"
TURBO_BASE_URL = os.getenv("TURBO_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
KEY_FILE = os.getenv("MPT_API_KEY_FILE", "")
ADMIN_FILE = Path(os.getenv("COCKPIT_ADMIN_FILE", str(DATA_DIR / "secrets" / "admin.json")))
SESSION_HOURS = 12
LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_MINUTES = 15
SHORT_STALE_MINUTES = int(os.getenv("SHORT_STALE_MINUTES", "45"))
LONG_STALE_MINUTES = int(os.getenv("LONG_STALE_MINUTES", "180"))
QUEUE_STALE_MINUTES = int(os.getenv("QUEUE_STALE_MINUTES", "360"))
STATIC_ROOT = Path(__file__).resolve().parents[1]
JOB_STATES = {"draft", "queued", "running", "completed", "approved", "failed", "interrupted", "deleting", "deleted"}
RETRY_BASE_SECONDS = 1.0
ACTIVE_TRACE: ContextVar[dict[str, Any] | None] = ContextVar("active_trace", default=None)


def now() -> str:
    return datetime.now(UTC).isoformat()


def as_utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return datetime.now(UTC)


def configured_admin() -> dict[str, str] | None:
    try:
        data = json.loads(ADMIN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    required = {"name", "email", "salt", "hash"}
    return data if required.issubset(data) else None


def api_key() -> str:
    if KEY_FILE and Path(KEY_FILE).is_file():
        return Path(KEY_FILE).read_text(encoding="utf-8").strip()
    return os.getenv("MPT_API_KEY", "")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # API and workers share this database. The timeout serializes concurrent writers;
    # initialization selects the journal mode supported by the runtime volume.
    connection = sqlite3.connect(DATABASE, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def database() -> Any:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def database_initialization_lock() -> Any:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = DATA_DIR / ".cockpit-db-init.lock"
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

def initialize_database() -> None:
    with database_initialization_lock():
        _initialize_database_locked()

def _initialize_database_locked() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE, timeout=30.0)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        journal_mode = os.getenv("COCKPIT_SQLITE_JOURNAL_MODE", "WAL").upper()
        if journal_mode not in {"WAL", "DELETE"}:
            raise ValueError("COCKPIT_SQLITE_JOURNAL_MODE deve ser WAL ou DELETE.")
        connection.execute(f"PRAGMA journal_mode = {journal_mode}")
    finally:
        connection.close()
    with database() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, niche TEXT NOT NULL DEFAULT '',
              language TEXT NOT NULL DEFAULT 'pt-BR', prompt_base TEXT NOT NULL DEFAULT '',
              defaults_json TEXT NOT NULL DEFAULT '{}', publish_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS video_jobs (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), turbo_task_id TEXT,
              subject TEXT NOT NULL, script TEXT NOT NULL DEFAULT '', request_json TEXT NOT NULL,
              state TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0, failed_stage TEXT, error TEXT,
              turbo_payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL, completed_at TEXT, deleted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_video_jobs_project_created ON video_jobs(project_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES video_jobs(id), kind TEXT NOT NULL,
              remote_path TEXT NOT NULL, filename TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(job_id, remote_path)
            );
            CREATE TABLE IF NOT EXISTS publications (
              id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES video_jobs(id), platform TEXT NOT NULL,
              state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, external_id TEXT, error TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(job_id, platform)
            );
            CREATE TABLE IF NOT EXISTS job_metadata (
              job_id TEXT PRIMARY KEY REFERENCES video_jobs(id), value_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS channels (
              id TEXT PRIMARY KEY, platform TEXT NOT NULL, name TEXT NOT NULL, handle TEXT NOT NULL DEFAULT '',
              external_id TEXT, status TEXT NOT NULL, niche TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT 'pt-BR',
              system_prompt TEXT NOT NULL DEFAULT '',
              voice_name TEXT NOT NULL DEFAULT 'pt-BR-FranciscaNeural', voice_rate REAL NOT NULL DEFAULT 1.0,
              voice_volume REAL NOT NULL DEFAULT 1.0, subtitle_enabled INTEGER NOT NULL DEFAULT 1,
              subtitle_position TEXT NOT NULL DEFAULT 'bottom', font_size INTEGER NOT NULL DEFAULT 56,
              text_fore_color TEXT NOT NULL DEFAULT '#FFFFFF', stroke_color TEXT NOT NULL DEFAULT '#000000',
              stroke_width REAL NOT NULL DEFAULT 1.5,
              video_defaults_json TEXT NOT NULL DEFAULT '{}',
              visual_guides_json TEXT NOT NULL DEFAULT '{}',
              publish_privacy TEXT NOT NULL DEFAULT 'private',
              stats_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
              UNIQUE(platform, external_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS generation_batches (
              id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
              client_request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
              input_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(project_id, client_request_id)
            );
            CREATE TABLE IF NOT EXISTS generation_batch_items (
              id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES generation_batches(id),
              channel_id TEXT NOT NULL REFERENCES channels(id), channel_name TEXT NOT NULL,
              state TEXT NOT NULL, stage TEXT NOT NULL, context_json TEXT NOT NULL,
              generated_json TEXT NOT NULL DEFAULT '{}', job_id TEXT REFERENCES video_jobs(id),
              error TEXT, lease_owner TEXT, lease_until TEXT, submission_started_at TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(batch_id, channel_id)
            );
            CREATE INDEX IF NOT EXISTS idx_generation_items_claim ON generation_batch_items(state, lease_until, created_at);
            CREATE TABLE IF NOT EXISTS generation_quality (
              item_id TEXT PRIMARY KEY REFERENCES generation_batch_items(id),
              job_id TEXT UNIQUE REFERENCES video_jobs(id), state TEXT NOT NULL DEFAULT 'pending',
              report_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operators (
              channel_id TEXT PRIMARY KEY REFERENCES channels(id), enabled INTEGER NOT NULL DEFAULT 0, format TEXT NOT NULL DEFAULT 'short',
              cadence_hours INTEGER NOT NULL DEFAULT 24, next_run_at TEXT, last_run_at TEXT, last_error TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operator_runs (
              id TEXT PRIMARY KEY, channel_id TEXT NOT NULL REFERENCES channels(id), slot_key TEXT NOT NULL,
              state TEXT NOT NULL, job_id TEXT REFERENCES video_jobs(id), batch_id TEXT REFERENCES generation_batches(id), error TEXT,
              started_at TEXT NOT NULL, finished_at TEXT, UNIQUE(channel_id, slot_key)
            );
            CREATE INDEX IF NOT EXISTS idx_operator_runs_channel ON operator_runs(channel_id, started_at DESC);
            CREATE TABLE IF NOT EXISTS video_analytics (
              publication_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL, metrics_json TEXT NOT NULL, score INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observability_events (
              id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, level TEXT NOT NULL, service TEXT NOT NULL,
              message TEXT NOT NULL, trace_id TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
              status_code INTEGER NOT NULL, duration_ms REAL NOT NULL, context_json TEXT NOT NULL DEFAULT '{}',
              spans_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS idx_observability_events_timestamp ON observability_events(timestamp DESC);
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL, admin_version TEXT NOT NULL, created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT, attempted_at TEXT NOT NULL, success INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notifications (
              id TEXT PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, title TEXT NOT NULL,
              detail TEXT NOT NULL DEFAULT '', job_id TEXT REFERENCES video_jobs(id), read_at TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC);
            """
        )
        job_columns = {column["name"] for column in db.execute("PRAGMA table_info(video_jobs)")}
        if "channel_id" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN channel_id TEXT REFERENCES channels(id)")
        if "format" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN format TEXT")
            # Renders from before the long/short split: landscape ones were long-form, the rest Shorts.
            db.execute("UPDATE video_jobs SET format = CASE WHEN json_extract(request_json, '$.video_aspect') = '16:9' THEN 'long' ELSE 'short' END")
        if "created_by" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN created_by TEXT")
        if "auto_publish_authorized" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN auto_publish_authorized INTEGER NOT NULL DEFAULT 0")
        if "progress_updated_at" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN progress_updated_at TEXT")
            db.execute("UPDATE video_jobs SET progress_updated_at=updated_at WHERE progress_updated_at IS NULL")
        if "last_observed_progress" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN last_observed_progress INTEGER NOT NULL DEFAULT 0")
            db.execute("UPDATE video_jobs SET last_observed_progress=progress")
        if "interruption_reason" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN interruption_reason TEXT")
        if "generation_item_id" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN generation_item_id TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_video_jobs_generation_item ON video_jobs(generation_item_id) WHERE generation_item_id IS NOT NULL")
        session_columns = {column["name"] for column in db.execute("PRAGMA table_info(sessions)")}
        if "admin_version" not in session_columns:
            # Existing sessions intentionally become invalid after this security migration.
            db.execute("ALTER TABLE sessions ADD COLUMN admin_version TEXT NOT NULL DEFAULT ''")
        channel_columns = {column["name"] for column in db.execute("PRAGMA table_info(channels)")}
        if "system_prompt" not in channel_columns:
            db.execute("ALTER TABLE channels ADD COLUMN system_prompt TEXT NOT NULL DEFAULT ''")
        if "publish_privacy" not in channel_columns:
            db.execute("ALTER TABLE channels ADD COLUMN publish_privacy TEXT NOT NULL DEFAULT 'private'")
        profile_columns = {
            "voice_name": "TEXT NOT NULL DEFAULT ''",
            "voice_rate": "REAL NOT NULL DEFAULT 1.0",
            "voice_volume": "REAL NOT NULL DEFAULT 1.0",
            "subtitle_enabled": "INTEGER NOT NULL DEFAULT 1",
            "subtitle_position": "TEXT NOT NULL DEFAULT 'bottom'",
            "font_size": "INTEGER NOT NULL DEFAULT 56",
            "text_fore_color": "TEXT NOT NULL DEFAULT '#FFFFFF'",
            "stroke_color": "TEXT NOT NULL DEFAULT '#000000'",
            "stroke_width": "REAL NOT NULL DEFAULT 1.5",
        }
        added_profile_columns = []
        for column, definition in profile_columns.items():
            if column not in channel_columns:
                db.execute(f"ALTER TABLE channels ADD COLUMN {column} {definition}")
                added_profile_columns.append(column)
        if "voice_name" in added_profile_columns:
            db.execute(
                "UPDATE channels SET voice_name = CASE lower(substr(language, 1, 2)) "
                "WHEN 'en' THEN 'en-US-JennyNeural' WHEN 'es' THEN 'es-ES-ElviraNeural' "
                "WHEN 'de' THEN 'de-DE-KatjaNeural' ELSE 'pt-BR-FranciscaNeural' END"
            )
        publication_columns = {column["name"] for column in db.execute("PRAGMA table_info(publications)")}
        if "scheduled_at" not in publication_columns:
            db.execute("ALTER TABLE publications ADD COLUMN scheduled_at TEXT")
        operator_columns = {column["name"] for column in db.execute("PRAGMA table_info(operators)")}
        operator_additions = {
            "auto_publish": "INTEGER NOT NULL DEFAULT 0",
            "publish_days_json": "TEXT NOT NULL DEFAULT '[0,1,2,3,4,5,6]'",
            "publish_time": "TEXT NOT NULL DEFAULT '12:00'",
            "publish_timezone": "TEXT NOT NULL DEFAULT 'America/Sao_Paulo'",
            "paused_reason": "TEXT",
            "lease_owner": "TEXT",
            "lease_until": "TEXT",
        }
        for column, definition in operator_additions.items():
            if column not in operator_columns:
                db.execute(f"ALTER TABLE operators ADD COLUMN {column} {definition}")
        run_columns = {column["name"] for column in db.execute("PRAGMA table_info(operator_runs)")}
        if "batch_id" not in run_columns:
            db.execute("ALTER TABLE operator_runs ADD COLUMN batch_id TEXT")
        if "auto_publish" not in publication_columns:
            db.execute("ALTER TABLE publications ADD COLUMN auto_publish INTEGER NOT NULL DEFAULT 0")
        if "auto_lease_owner" not in publication_columns:
            db.execute("ALTER TABLE publications ADD COLUMN auto_lease_owner TEXT")
        if "auto_lease_until" not in publication_columns:
            db.execute("ALTER TABLE publications ADD COLUMN auto_lease_until TEXT")
        if "channel_id" not in publication_columns:
            db.execute("ALTER TABLE publications ADD COLUMN channel_id TEXT REFERENCES channels(id)")
            db.execute("UPDATE publications SET channel_id = (SELECT channel_id FROM video_jobs WHERE video_jobs.id = publications.job_id)")
            # A channel chosen before multi-channel publishing lived on video_jobs.channel_id; it becomes a pending target.
            db.execute(
                "INSERT OR IGNORE INTO publications(id, job_id, platform, channel_id, state, created_at, updated_at) "
                "SELECT lower(hex(randomblob(16))), jobs.id, channels.platform, channels.id, 'pending', ?, ? "
                "FROM video_jobs AS jobs JOIN channels ON channels.id = jobs.channel_id WHERE jobs.deleted_at IS NULL",
                (now(), now()),
            )
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_publication_channel_slot ON publications(channel_id, scheduled_at) WHERE channel_id IS NOT NULL AND scheduled_at IS NOT NULL AND state IN ('auto_reserved', 'uploading', 'platform_scheduled')")
        existing = db.execute("SELECT value_json FROM settings WHERE key = 'generation-defaults'").fetchone()
        if not existing:
            db.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                ("generation-defaults", json.dumps(default_generation_settings()), now()),
            )
        else:
            # Backfill: instalações existentes ganham as chaves novas sem perder o que o usuário já salvou.
            stored = json.loads(existing["value_json"])
            missing = {key: value for key, value in default_generation_settings().items() if key not in stored}
            if missing:
                db.execute(
                    "UPDATE settings SET value_json=?, updated_at=? WHERE key='generation-defaults'",
                    (json.dumps({**stored, **missing}), now()),
                )
        channel_columns = {column["name"] for column in db.execute("PRAGMA table_info(channels)")}
        if "video_defaults_json" not in channel_columns:
            db.execute("ALTER TABLE channels ADD COLUMN video_defaults_json TEXT NOT NULL DEFAULT '{}'")
            settings_rows = {row["key"]: json.loads(row["value_json"]) for row in db.execute(
                "SELECT key, value_json FROM settings WHERE key IN (?, ?, ?)",
                ("generation-defaults", "generation-defaults:short", "generation-defaults:long"),
            )}
            base = settings_rows.get("generation-defaults", default_generation_settings())
            defaults = {
                "short": video_profile_from_settings("short", {**base, **FORMAT_PRESETS["short"], **settings_rows.get("generation-defaults:short", {})}),
                "long": video_profile_from_settings("long", {**base, **FORMAT_PRESETS["long"], **settings_rows.get("generation-defaults:long", {})}),
            }
            for profile in defaults.values():
                for field in CHANNEL_PROFILE_FIELDS:
                    profile.pop(field, None)
            db.execute("UPDATE channels SET video_defaults_json=?", (json.dumps(defaults),))
        if "visual_guides_json" not in channel_columns:
            db.execute("ALTER TABLE channels ADD COLUMN visual_guides_json TEXT NOT NULL DEFAULT '{}'")
        # Backfill format profiles from the old shared channel fields. Existing values in either
        # format are preserved, so this is safe to run on every startup.
        for row in db.execute("SELECT * FROM channels").fetchall():
            normalized = channel_video_defaults(row)
            raw = json.loads(row["video_defaults_json"] or "{}")
            if raw != normalized:
                db.execute("UPDATE channels SET video_defaults_json=? WHERE id=?", (json.dumps(normalized), row["id"]))


def default_generation_settings() -> dict[str, Any]:
    """Base compartilhada (Configurações): equilíbrio entre velocidade e qualidade
    com stock-only (Pexels/Pixabay/Coverr). Os presets por formato refinam por cima."""
    return {
        "video_language": "pt-BR",
        "video_aspect": "9:16",
        "video_source": "pexels",
        "voice_name": "pt-BR-FranciscaNeural",
        "voice_rate": 1.0,
        "voice_volume": 1.0,
        "video_clip_speed": 1.0,
        "subtitle_enabled": True,
        "subtitle_provider": "edge",
        "stroke_width": 1.5,
        "bgm_type": "",
        "bgm_volume": 0.2,
        "video_transition_mode": "",
        "video_count": 1,
        "video_clip_duration": 5,
        "video_concat_mode": "script",
        "paragraph_number": 5,
        "font_size": 56,
        "text_fore_color": "#FFFFFF",
        "stroke_color": "#000000",
    }


# Locked values are applied last, so neither saved defaults nor a stray setting can turn a Short into 16:9.
FORMAT_LOCKED: dict[str, dict[str, Any]] = {"long": {"video_aspect": "16:9", "video_count": 1}, "short": {"video_aspect": "9:16"}}
# Presets "final" por formato: Short dinâmico (cortes de 4 s) e longo calmo (cortes de
# 6 s), legenda legível em cada aspecto. Para rascunho rápido, escolha no Gerador
# duração menor (Short 60 s) e clipe de 6 s, sem transição/BGM.
FORMAT_PRESETS: dict[str, dict[str, Any]] = {
    "long": {**FORMAT_LOCKED["long"], "video_clip_duration": 6, "subtitle_position": "bottom", "font_size": 48,
             "video_transition_mode": "", "bgm_type": "", "voice_rate": 1.0},
    "short": {**FORMAT_LOCKED["short"], "video_clip_duration": 4, "paragraph_number": 5, "subtitle_position": "center", "font_size": 64,
              "video_transition_mode": "", "bgm_type": "", "voice_rate": 1.0},
}


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    niche: str = Field(default="", max_length=200)
    language: str = "pt-BR"
    prompt_base: str = Field(default="", max_length=5000)
    defaults: dict[str, Any] = Field(default_factory=dict)
    publish: dict[str, Any] = Field(default_factory=dict)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    niche: str | None = Field(default=None, max_length=200)
    language: str | None = None
    prompt_base: str | None = Field(default=None, max_length=5000)
    defaults: dict[str, Any] | None = None
    publish: dict[str, Any] | None = None


class JobCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    script: str = Field(default="", max_length=40000)  # a 15-minute narration is ~15k characters
    settings: dict[str, Any] = Field(default_factory=dict)
    format: Literal["long", "short"] = "short"
    channel_ids: list[str] = Field(default_factory=list, max_length=5)


class SettingsPatch(BaseModel):
    value: dict[str, Any]


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


def session_from_request(request: Request) -> sqlite3.Row | None:
    token = request.cookies.get("cockpit_session")
    if not token:
        return None
    admin = configured_admin()
    if not admin:
        return None
    expected_version = admin_session_version(admin)
    with database() as db:
        row = db.execute(
            "SELECT * FROM sessions WHERE token_hash=? AND expires_at>?", (session_token_hash(token), now())
        ).fetchone()
        if row and not secrets.compare_digest(row["admin_version"], expected_version):
            db.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
            return None
        if row:
            db.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (now(), row["token_hash"]))
        return row


def login_is_limited() -> bool:
    cutoff = (datetime.now(UTC) - timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat()
    with database() as db:
        failed = db.execute("SELECT count(*) AS count FROM login_attempts WHERE success=0 AND attempted_at>=?", (cutoff,)).fetchone()["count"]
    return failed >= LOGIN_ATTEMPTS


def notify(event_key: str, kind: str, title: str, detail: str = "", job_id: str | None = None) -> None:
    with database() as db:
        db.execute(
            "INSERT OR IGNORE INTO notifications(id, event_key, kind, title, detail, job_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid4()), event_key[:200], kind[:50], title[:300], detail[:1000], job_id, now()),
        )


def row_as_project(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"], "niche": row["niche"], "language": row["language"],
        "prompt_base": row["prompt_base"], "defaults": json.loads(row["defaults_json"]),
        "publish": json.loads(row["publish_json"]), "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def row_as_job(row: sqlite3.Row, db: sqlite3.Connection | None = None) -> dict[str, Any]:
    result = {key: row[key] for key in row.keys() if key not in {"request_json", "turbo_payload_json"}}
    result["request"] = json.loads(row["request_json"])
    result["turbo"] = json.loads(row["turbo_payload_json"])
    if db:
        result["artifacts"] = [dict(item) for item in db.execute("SELECT * FROM artifacts WHERE job_id = ?", (row["id"],))]
        result["publications"] = [dict(item) for item in db.execute("SELECT * FROM publications WHERE job_id = ?", (row["id"],))]
        metadata = db.execute("SELECT value_json FROM job_metadata WHERE job_id = ?", (row["id"],)).fetchone()
        result["metadata"] = json.loads(metadata["value_json"]) if metadata else None
    return result


def turbo_headers() -> dict[str, str]:
    key = api_key()
    return {"x-api-key": key} if key else {}


def is_retryable(method: str, status: int | None = None, exc: Exception | None = None) -> bool:
    """Only repeats a request that can't double-apply. A connect failure never reached the
    server and a 429 was refused, so both are safe for any method. A read timeout or 5xx may
    already have been processed (a repeated POST /api/v1/videos would start a second render),
    so those are retried only for idempotent methods."""
    idempotent = method.upper() in {"GET", "HEAD", "PUT", "DELETE"}
    if exc is not None:
        return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) or (idempotent and isinstance(exc, httpx.TransportError))
    return status == 429 or (idempotent and status is not None and (status in {408, 425} or status >= 500))


async def send_with_retry(client: httpx.AsyncClient, method: str, url: str, retries: int = 2, **kwargs: Any) -> httpx.Response:
    for attempt in range(retries + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if attempt == retries or not is_retryable(method, exc=exc):
                raise
        else:
            if attempt == retries or not is_retryable(method, status=response.status_code):
                return response
        await asyncio.sleep(RETRY_BASE_SECONDS * 2 ** attempt)
    raise AssertionError("unreachable")


async def turbo_request(method: str, path: str, retries: int = 2, **kwargs: Any) -> httpx.Response:
    headers = {**turbo_headers(), **kwargs.pop("headers", {})}
    started = time.perf_counter()
    status_code = 503
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=90)) as client:
            response = await send_with_retry(client, method, f"{TURBO_BASE_URL}{path}", retries=retries, headers=headers, **kwargs)
            status_code = response.status_code
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"MoneyPrinterTurbo indisponível: {exc}") from exc
    finally:
        trace = ACTIVE_TRACE.get()
        if trace is not None:
            trace["spans"].append({
                "name": f"Turbo {method.upper()} {path}",
                "service": "moneyprinterturbo",
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "status_code": status_code,
            })
    if response.status_code >= 400:
        detail = response.text[:500]
        raise HTTPException(status_code=502, detail=f"MoneyPrinterTurbo respondeu {response.status_code}: {detail}")
    return response


def unwrap(payload: Any) -> Any:
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def map_turbo_state(payload: dict[str, Any]) -> tuple[str, int, str | None, str | None]:
    raw = str(payload.get("state", "")).lower()
    progress = max(0, min(100, int(float(payload.get("progress", 0) or 0))))
    error = payload.get("error") or None
    stage = payload.get("failed_stage") or None
    if error or raw in {"-1", "failed", "failure", "error"}:
        return "failed", progress, stage, str(error or "Falha no Turbo")
    if raw in {"1", "completed", "complete", "success", "done", "finished"} or payload.get("videos") or payload.get("combined_videos"):
        return "completed", 100, None, None
    if raw in {"queued", "pending", "waiting"}:
        return "queued", progress, None, None
    # MoneyPrinterTurbo v1.3.7 uses state=4 for active processing.
    if raw in {"4", "processing", "running", "rendering"}:
        return "running", progress, None, None
    return "running", progress, None, None


def stale_minutes_for(job: sqlite3.Row) -> int:
    return SHORT_STALE_MINUTES if job["format"] == "short" else LONG_STALE_MINUTES


async def interrupt_job(job_id: str, reason: str, cancel_turbo: bool = True) -> None:
    with database() as db:
        job = db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
    if not job or job["state"] not in {"queued", "running"}:
        return
    if cancel_turbo and job["turbo_task_id"]:
        try:
            await turbo_request("DELETE", f"/api/v1/tasks/{job['turbo_task_id']}", retries=0)
        except HTTPException:
            pass
    with database() as db:
        db.execute(
            "UPDATE video_jobs SET state='interrupted', error=?, interruption_reason=?, updated_at=? WHERE id=?",
            (reason, reason, now(), job_id),
        )
    notify(f"job-interrupted:{job_id}:{reason}", "job_interrupted", f"Geração interrompida: {job['subject']}", reason, job_id)


def sse_message(event: str, data: str) -> str:
    # Real newlines: EventSource ignores a frame whose separators are a literal "\n".
    return f"event: {event}\ndata: {data}\n\n"


def turbo_relative_path(uri: str) -> str:
    """Turbo's GET /api/v1/tasks/{id} wraps videos/combined_videos through
    _task_file_to_uri: an absolute "{endpoint}/tasks/<rel>" URL if `endpoint` is
    configured, otherwise a root-relative "/tasks/<rel>" path (confirmed against
    MoneyPrinterTurbo v1.3.7 source and its own schema example). Its stream/download
    routes instead take the bare "<rel>" (task_id/filename.mp4) relative to the
    tasks dir - passing the wrapped form back in gives a 404 on every download."""
    marker = "/tasks/"
    index = uri.find(marker)
    return uri[index + len(marker):] if index != -1 else uri.lstrip("/")


async def reconcile_job(job_id: str) -> dict[str, Any]:
    with database() as db:
        job = db.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        return {}
    # "approved" is a Cockpit-only state; re-reading Turbo would downgrade it to "completed".
    if not job["turbo_task_id"] or job["state"] in {"deleted", "interrupted", "approved"}:
        with database() as db:
            return row_as_job(job, db)
    response = await turbo_request("GET", f"/api/v1/tasks/{job['turbo_task_id']}")
    payload = unwrap(response.json())
    if not isinstance(payload, dict):
        return row_as_job(job)
    state, progress, stage, error = map_turbo_state(payload)
    completed_at = now() if state == "completed" else None
    notification: tuple[str, str, str] | None = None
    with database() as db:
        # A successful poll is not progress. Preserve a distinct heartbeat timestamp so
        # state=4 at the same percentage can be detected as stalled.
        advanced = state != job["state"] or progress > int(job["last_observed_progress"] or 0)
        progress_updated_at = now() if advanced else job["progress_updated_at"]
        db.execute(
            "UPDATE video_jobs SET state=?, progress=?, last_observed_progress=?, progress_updated_at=?, failed_stage=?, error=?, turbo_payload_json=?, updated_at=?, completed_at=COALESCE(completed_at, ?) WHERE id=?",
            (state, progress, max(progress, int(job["last_observed_progress"] or 0)), progress_updated_at, stage, error, json.dumps(payload), now(), completed_at, job_id),
        )
        if state == "completed" and job["state"] != "completed":
            notification = ("job_completed", f"Geração concluída: {job['subject']}", "Pronta para revisão e publicação.")
        elif state == "failed" and job["state"] != "failed":
            notification = ("job_failed", f"Falha na geração: {job['subject']}", error or "O Turbo informou uma falha.")
        if state == "completed":
            # "videos" are the final renders (narration + subtitles + BGM); "combined_videos"
            # are the silent stitched clips used as an intermediate step.
            files = [("video", p) for p in payload.get("videos") or []] + [("combined", p) for p in payload.get("combined_videos") or []]
            for kind, raw_path in files:
                remote_path = turbo_relative_path(str(raw_path))
                filename = Path(remote_path).name
                db.execute(
                    "INSERT OR IGNORE INTO artifacts(id, job_id, kind, remote_path, filename, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid4()), job_id, kind, remote_path, filename, now()),
                )
        db.commit()
        current = db.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,)).fetchone()
        result = row_as_job(current, db)
    if notification:
        notify(f"{notification[0]}:{job_id}", notification[0], notification[1], notification[2], job_id)
    return result


async def reconcile_stale_jobs() -> None:
    """Jobs left in queued/running when the gateway stops are unverifiable on restart.
    Try to reconcile with Turbo; if that fails, mark them interrupted so the UI
    never shows a job as still processing when nothing is tracking it anymore."""
    with database() as db:
        stale_ids = [row["id"] for row in db.execute(
            "SELECT id FROM video_jobs WHERE state IN ('queued','running') AND deleted_at IS NULL"
        )]
    for job_id in stale_ids:
        try:
            await reconcile_job(job_id)
        except HTTPException:
            await interrupt_job(job_id, "Interrompido: MoneyPrinterTurbo indisponível durante a reconciliação.", cancel_turbo=False)
            continue
        with database() as db:
            current = db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
        if not current or current["state"] not in {"queued", "running"}:
            continue
        reference = parse_time(current["created_at"] if current["state"] == "queued" else current["progress_updated_at"] or current["updated_at"])
        age_minutes = (datetime.now(UTC) - reference).total_seconds() / 60
        timeout = QUEUE_STALE_MINUTES if current["state"] == "queued" else stale_minutes_for(current)
        if age_minutes >= timeout:
            label = "fila" if current["state"] == "queued" else "sem avanço de progresso"
            await interrupt_job(current["id"], f"Interrompido após {int(age_minutes)} min {label} (limite: {timeout} min).")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    try:
        growth_store.ensure_schema()
        with database() as db:
            channels = [dict(row) for row in db.execute(
                "SELECT id, platform, name, niche, language, status FROM channels WHERE deleted_at IS NULL"
            )]
        for channel in channels:
            growth_store.sync_channel(channel)
    except GrowthStoreUnavailable as exc:
        logging.getLogger("cockpit.growth").warning("Growth OS indisponível; fluxo legado preservado: %s", exc)
    try:
        ensure_media_bucket()
    except ObjectStorageUnavailable as exc:
        logging.getLogger("cockpit.growth").warning("Object storage indisponível; geração legada preservada: %s", exc)
    await reconcile_stale_jobs()
    yield


app = FastAPI(
    title="Moneyprint Cockpit Gateway", version="1.1.0", lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENABLE_API_DOCS") == "1" else None,
    redoc_url=None,
)
app.include_router(growth_router)


async def health_status() -> dict[str, Any]:
    database_ok = DATABASE.exists()
    turbo_ok = False
    try:
        response = await turbo_request("GET", "/ping", retries=0)
        turbo_ok = response.status_code == 200
    except HTTPException:
        pass
    return {"status": "ok" if database_ok and turbo_ok else "degraded", "database": database_ok, "turbo": turbo_ok}


@app.get("/api/health/live")
async def health_live() -> dict[str, Any]:
    try:
        with database() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": True}
    except sqlite3.Error:
        return JSONResponse({"status": "degraded", "database": False}, status_code=503)


@app.get("/api/health/ready")
@app.get("/api/health")
async def health() -> Response:
    state = await health_status()
    return JSONResponse(state, status_code=200 if state["status"] == "ok" else 503)


@app.post("/api/auth/login")
def login(input: LoginInput, response: Response) -> dict[str, Any]:
    if login_is_limited():
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde 15 minutos.")
    admin = configured_admin()
    success = bool(admin and input.email.strip().lower() == str(admin["email"]).lower() and verify_password(input.password, admin))
    with database() as db:
        db.execute("INSERT INTO login_attempts(attempted_at, success) VALUES (?, ?)", (now(), int(success)))
        db.execute("DELETE FROM login_attempts WHERE attempted_at<?", ((datetime.now(UTC) - timedelta(days=1)).isoformat(),))
    if not success:
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    expires = datetime.now(UTC) + timedelta(hours=SESSION_HOURS)
    with database() as db:
        db.execute("INSERT INTO sessions(token_hash, csrf_token, admin_version, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (session_token_hash(token), csrf, admin_session_version(admin), now(), expires.isoformat(), now()))
    response.set_cookie("cockpit_session", token, max_age=SESSION_HOURS * 3600, httponly=True, samesite="lax", secure=False, path="/")
    return {"name": admin["name"], "email": admin["email"], "csrf_token": csrf, "expires_at": expires.isoformat()}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> Response:
    token = request.cookies.get("cockpit_session")
    if token:
        with database() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (session_token_hash(token),))
    response.delete_cookie("cockpit_session", path="/")
    response.status_code = 204
    return response


@app.get("/api/auth/session")
def auth_session(request: Request) -> dict[str, Any]:
    session = session_from_request(request)
    admin = configured_admin()
    if not session or not admin:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada.")
    return {"name": admin["name"], "email": admin["email"], "csrf_token": session["csrf_token"], "expires_at": session["expires_at"]}


@app.get("/api/status")
async def system_status() -> dict[str, Any]:
    ready = await health_status()
    provider = provider_readiness()
    with database() as db:
        job = db.execute("SELECT * FROM video_jobs WHERE state IN ('queued','running') AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 1").fetchone()
    return {
        "gateway": "online" if ready["database"] else "offline", "turbo": "online" if ready["turbo"] else "offline",
        "llm_provider": provider["llm_provider"],
        "llm_model": provider["nvidia_text_model"] if provider["llm_provider"] == "nvidia" else provider["moonshot_model"],
        "environment": os.getenv("APP_ENV", "local"), "active_job": row_as_job(job) if job else None,
    }


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
    service = observability_service(path)
    message = error or f"{method.upper()} {path} respondeu {status_code} em {duration_ms:.1f} ms"
    context = {"method": method.upper(), "path": path, "status_code": status_code}
    with database() as db:
        db.execute(
            "INSERT INTO observability_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid4()), now(), level, service, message, trace_id, method.upper(), path,
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


@app.get("/api/logs")
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
    with database() as db:
        total = db.execute(f"SELECT count(*) AS total FROM observability_events{where}", params).fetchone()["total"]
        rows = db.execute(
            f"SELECT * FROM observability_events{where} ORDER BY timestamp DESC LIMIT ?", (*params, limit)
        ).fetchall()
        services = [row["service"] for row in db.execute("SELECT DISTINCT service FROM observability_events ORDER BY service")]
    return {"items": [row_as_observability_event(row) for row in rows], "total": total, "services": services}


@app.get("/api/notifications")
def list_notifications(limit: int = 100) -> dict[str, Any]:
    with database() as db:
        rows = db.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        unread = db.execute("SELECT count(*) AS count FROM notifications WHERE read_at IS NULL").fetchone()["count"]
    return {"items": [dict(row) for row in rows], "unread": unread}


@app.patch("/api/notifications/{notification_id}/read")
def read_notification(notification_id: str) -> dict[str, Any]:
    with database() as db:
        db.execute("UPDATE notifications SET read_at=COALESCE(read_at, ?) WHERE id=?", (now(), notification_id))
        row = db.execute("SELECT * FROM notifications WHERE id=?", (notification_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Notificação não encontrada")
    return dict(row)


@app.post("/api/notifications/read-all")
def read_all_notifications() -> dict[str, int]:
    with database() as db:
        updated = db.execute("UPDATE notifications SET read_at=? WHERE read_at IS NULL", (now(),)).rowcount
    return {"updated": updated}


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    with database() as db:
        return [row_as_project(row) for row in db.execute("SELECT * FROM projects WHERE deleted_at IS NULL ORDER BY updated_at DESC")]


@app.post("/api/projects", status_code=201)
def create_project(input: ProjectCreate) -> dict[str, Any]:
    project_id = str(uuid4())
    timestamp = now()
    with database() as db:
        db.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (project_id, input.name.strip(), input.niche.strip(), input.language, input.prompt_base.strip(), json.dumps(input.defaults), json.dumps(input.publish), timestamp, timestamp),
        )
        row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row_as_project(row)


def get_project_or_404(project_id: str) -> sqlite3.Row:
    with database() as db:
        row = db.execute("SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    return row


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    return row_as_project(get_project_or_404(project_id))


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, input: ProjectPatch) -> dict[str, Any]:
    current = row_as_project(get_project_or_404(project_id))
    updated = {**current, **input.model_dump(exclude_none=True)}
    with database() as db:
        db.execute(
            "UPDATE projects SET name=?, niche=?, language=?, prompt_base=?, defaults_json=?, publish_json=?, updated_at=? WHERE id=?",
            (updated["name"].strip(), updated["niche"].strip(), updated["language"], updated["prompt_base"].strip(), json.dumps(updated["defaults"]), json.dumps(updated["publish"]), now(), project_id),
        )
        row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row_as_project(row)


@app.delete("/api/projects/{project_id}", status_code=204, response_model=None)
def delete_project(project_id: str) -> None:
    get_project_or_404(project_id)
    with database() as db:
        db.execute("UPDATE projects SET deleted_at=?, updated_at=? WHERE id=?", (now(), now(), project_id))


def settings_key(format: str | None) -> str:
    return f"generation-defaults:{format}" if format else "generation-defaults"


@app.get("/api/settings/generation-defaults")
def generation_defaults(format: Literal["long", "short"] | None = None) -> dict[str, Any]:
    """Without format: the shared base edited in Configurações. With format: base, then that
    format's preset, then what its own screen saved with "Salvar padrão"."""
    with database() as db:
        rows = {row["key"]: json.loads(row["value_json"]) for row in db.execute(
            "SELECT key, value_json FROM settings WHERE key IN (?, ?)", ("generation-defaults", settings_key(format))
        )}
    base = rows.get("generation-defaults", default_generation_settings())
    if base.get("video_source") not in {"pexels", "pixabay", "coverr"}:
        # Fontes de IA removidas (só stock): um padrão antigo salvo cai para o Pexels.
        base = {**base, "video_source": "pexels"}
    if not format:
        return base
    return {**base, **FORMAT_PRESETS[format], **rows.get(settings_key(format), {}), **FORMAT_LOCKED[format]}


@app.put("/api/settings/generation-defaults")
def put_generation_defaults(input: SettingsPatch, format: Literal["long", "short"] | None = None) -> dict[str, Any]:
    key = settings_key(format)
    with database() as db:
        row = db.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        current = json.loads(row["value_json"]) if row else ({} if format else default_generation_settings())
        merged = {**current, **input.value}
        db.execute(
            "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (key, json.dumps(merged), now()),
        )
    return generation_defaults(format)


@app.get("/api/settings/providers")
def provider_readiness() -> dict[str, Any]:
    config = DATA_DIR / "moneyprinter" / "config.toml"
    value = config.read_text(encoding="utf-8") if config.exists() else ""
    model_match = re.search(r'moonshot_model_name\s*=\s*"([^"]*)"', value)
    return {
        "moonshot": 'moonshot_api_key = ""' not in value and "moonshot_api_key" in value,
        "nvidia": 'nvidia_api_key = ""' not in value and "nvidia_api_key" in value,
        "pexels": 'pexels_api_keys = []' not in value and "pexels_api_keys" in value,
        "pixabay": 'pixabay_api_keys = []' not in value and "pixabay_api_keys" in value,
        "coverr": 'coverr_api_keys = []' not in value and "coverr_api_keys" in value,
        "upload_post": 'upload_post_api_key = ""' not in value and "upload_post_api_key" in value,
        "edge_tts": True,
        "moonshot_model": model_match.group(1) if model_match else "",
        "llm_provider": _config_string(value, "cockpit_llm_provider") or "moonshot",
        "nvidia_text_model": _config_string(value, "nvidia_text_model") or NVIDIA_DEFAULT_TEXT_MODEL,
    }


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _toml_set(text: str, key: str, rendered_value: str) -> str:
    line = f"{key} = {rendered_value}"
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    return pattern.sub(line, text, count=1) if pattern.search(text) else text.rstrip("\n") + "\n" + line + "\n"


class ProviderCredentials(BaseModel):
    llm_provider: Literal["moonshot", "nvidia"] | None = None
    moonshot_api_key: str | None = Field(default=None, max_length=500)
    moonshot_model_name: str | None = Field(default=None, max_length=100)
    nvidia_api_key: str | None = Field(default=None, max_length=500)
    nvidia_text_model: str | None = Field(default=None, max_length=200)
    pexels_api_key: str | None = Field(default=None, max_length=500)
    pixabay_api_key: str | None = Field(default=None, max_length=500)
    coverr_api_key: str | None = Field(default=None, max_length=500)
    upload_post_api_key: str | None = Field(default=None, max_length=500)
    upload_post_username: str | None = Field(default=None, max_length=200)


class ProviderValidation(BaseModel):
    provider: Literal["moonshot", "nvidia", "pexels", "pixabay", "coverr", "upload_post"]
    api_key: str = Field(min_length=1, max_length=500)


class NvidiaModelTest(BaseModel):
    model: str = Field(min_length=1, max_length=200)


MOONSHOT_ENDPOINTS = {"china": "https://api.moonshot.cn/v1", "global": "https://api.moonshot.ai/v1"}
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_TEXT_MODEL = "moonshotai/kimi-k3"
NVIDIA_CATALOG_CACHE_SECONDS = 600
NVIDIA_PROBE_TIMEOUT_SECONDS = 30
# Long-form scripts issue several sequential completions.  Kimi K3 may spend
# substantial time reasoning before returning a section, so the ordinary 180s
# read limit produced a false "service unavailable" while the provider was
# still working.  This is a read limit only; connection failures still fail
# promptly and no provider/model fallback is performed.
NVIDIA_GENERATION_READ_TIMEOUT_SECONDS = 300

# The Cockpit deliberately exposes a small, tested set instead of NVIDIA's full
# heterogeneous catalog.  Some catalog entries are embedding, image, or base
# models and would fail in the text-generation flows that call /chat/completions.
NVIDIA_MODELS: tuple[dict[str, Any], ...] = (
    {"id": "moonshotai/kimi-k3", "label": "Kimi K3", "provider": "Moonshot AI",
     "description": "Raciocínio avançado e contexto longo.", "capabilities": {"text": True, "image": True, "reasoning": True, "streaming": True}},
    {"id": "z-ai/glm-5.3", "label": "GLM 5.3", "provider": "Z.ai",
     "description": "Equilibrado para roteiros, JSON e tarefas gerais.", "capabilities": {"text": True, "image": False, "reasoning": True, "streaming": True}},
    {"id": "z-ai/glm-5.3-flash", "label": "GLM 5.3 Flash", "provider": "Z.ai",
     "description": "Variante rápida para tarefas frequentes.", "capabilities": {"text": True, "image": False, "reasoning": True, "streaming": True}},
    {"id": "nvidia/nemotron-3.5-lightning-30b-a3b", "label": "Nemotron 3.5 Lightning", "provider": "NVIDIA",
     "description": "Modelo rápido para tarefas agentivas com thinking.", "capabilities": {"text": True, "image": False, "reasoning": True, "streaming": True}},
    {"id": "meta/muse-glimmer-30b", "label": "Muse Glimmer 30B", "provider": "Meta",
     "description": "Raciocínio multimodal com ferramentas nativas.", "capabilities": {"text": True, "image": True, "reasoning": True, "streaming": True}},
    {"id": "google/diffusiongemma-26b-a4b-it", "label": "DiffusionGemma 26B", "provider": "Google",
     "description": "Modelo multimodal para texto e imagem, com raciocínio habilitável.", "capabilities": {"text": True, "image": True, "reasoning": True, "streaming": True}},
)
NVIDIA_MODEL_IDS = frozenset(model["id"] for model in NVIDIA_MODELS)
NVIDIA_TASK_TOKEN_LIMITS = {"json": 2048, "outline": 4096, "script": 8192, "test": 16}
# The long-script editor has its own conservative caps.  They deliberately do
# not change the profiles used by metadata, agents, or the Turbo integration.
FAST_LONG_SCRIPT_MAX_TOKENS = 4096
FAST_LONG_SECTION_MAX_TOKENS = 1536
_nvidia_catalog_cache: dict[str, Any] = {"expires_at": 0.0}


async def detect_moonshot_region(client: httpx.AsyncClient, api_key: str) -> str | None:
    """Moonshot/Kimi keys are platform-specific: a key from the China platform
    (platform.kimi.com) only works against api.moonshot.cn, a Global-platform key
    (platform.kimi.ai) only against api.moonshot.ai. Turbo defaults to the China
    endpoint when moonshot_base_url is blank, so a Global key looks "wrong" unless
    we detect it and point moonshot_base_url there ourselves."""
    for region, base_url in MOONSHOT_ENDPOINTS.items():
        try:
            response = await client.get(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"})
        except httpx.HTTPError:
            continue
        if response.status_code == 200:
            return region
    return None


def nvidia_model_or_422(model: str) -> str:
    if model not in NVIDIA_MODEL_IDS:
        raise HTTPException(status_code=422, detail="Escolha um dos modelos NVIDIA disponíveis no Cockpit.")
    return model


async def nvidia_model_catalog() -> dict[str, Any]:
    """Returns only supported chat models and never exposes NVIDIA credentials.

    /v1/models is a discovery endpoint, not an authentication probe.  Availability
    is therefore advisory; the explicit test endpoint is the proof that a selected
    model can complete a request with the configured key.
    """
    config_path = DATA_DIR / "moneyprinter" / "config.toml"
    config = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    key = _config_string(config, "nvidia_api_key")
    selected = _config_string(config, "nvidia_text_model") or NVIDIA_DEFAULT_TEXT_MODEL
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest() if key else ""
    cached = _nvidia_catalog_cache
    if cached.get("fingerprint") == fingerprint and cached.get("expires_at", 0.0) > time.monotonic():
        return {**cached["value"], "selected_model": selected}

    checked_at = now()
    available_ids: set[str] | None = None
    catalog_status = "not_configured" if not key else "unavailable"
    if key:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
                response = await client.get(f"{NVIDIA_BASE_URL}/models", headers={"Authorization": f"Bearer {key}"})
            if response.status_code == 200:
                data = response.json().get("data", [])
                available_ids = {str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")}
                catalog_status = "available"
            elif response.status_code in {401, 403}:
                catalog_status = "unauthorized"
        except (httpx.HTTPError, ValueError, TypeError):
            # Deliberately do not put transport/provider details into a browser response.
            catalog_status = "unavailable"

    models = [{**model, "capabilities": dict(model["capabilities"]), "available": (model["id"] in available_ids) if available_ids is not None else None}
              for model in NVIDIA_MODELS]
    value = {"models": models, "catalog_status": catalog_status, "checked_at": checked_at}
    _nvidia_catalog_cache.clear()
    _nvidia_catalog_cache.update({"fingerprint": fingerprint, "expires_at": time.monotonic() + NVIDIA_CATALOG_CACHE_SECONDS, "value": value})
    return {**value, "selected_model": selected}


def nvidia_chat_body(
    model: str,
    prompt: str,
    task: Literal["json", "outline", "script", "test"] = "json",
    *,
    max_tokens: int | None = None,
    fast_script: bool = False,
) -> dict[str, Any]:
    """Builds only the parameters documented for each supported NIM model."""
    model = nvidia_model_or_422(model)
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens if max_tokens is not None else NVIDIA_TASK_TOKEN_LIMITS[task],
        "stream": False,
    }
    if model == "moonshotai/kimi-k3":
        body.update({"temperature": 1, "reasoning_effort": "medium" if fast_script else "max"})
    elif model in {"z-ai/glm-5.3", "z-ai/glm-5.3-flash"}:
        body.update({"temperature": 0.5, "top_p": 1})
    elif model == "nvidia/nemotron-3.5-lightning-30b-a3b":
        body.update({"temperature": 1, "top_p": 0.95, "chat_template_kwargs": {"enable_thinking": True},
                     "reasoning_budget": body["max_tokens"]})
    elif model == "meta/muse-glimmer-30b":
        body.update({"temperature": 1, "top_p": 0.95})
    elif model == "google/diffusiongemma-26b-a4b-it":
        # Structured outputs (visual terms, metadata and outlines) need the
        # final JSON within a bounded response.  DiffusionGemma can spend that
        # entire budget in private thinking, leaving content=null and causing a
        # misleading 502.  Keep thinking for narration, where it improves the
        # prose, but turn it off for JSON contracts.
        body.update({"temperature": 1, "top_p": 0.95,
                     "chat_template_kwargs": {"enable_thinking": task != "json"}})
    return body


@app.get("/api/settings/providers/nvidia/models")
async def list_nvidia_models() -> dict[str, Any]:
    return await nvidia_model_catalog()


@app.post("/api/settings/providers/nvidia/test")
async def test_nvidia_model(input: NvidiaModelTest) -> dict[str, Any]:
    model = nvidia_model_or_422(input.model)
    config_path = DATA_DIR / "moneyprinter" / "config.toml"
    config = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    key = _config_string(config, "nvidia_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure a chave NVIDIA antes de testar um modelo.")
    started = time.monotonic()
    # A connectivity probe must stay cheap even when the selected generation
    # profile enables deep reasoning (Kimi/Nemotron).  It validates the same
    # model and key without consuming a full reasoning budget.
    probe_body = {"model": model, "messages": [{"role": "user", "content": "Responda somente: OK."}], "max_tokens": 16, "stream": False}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=60)) as client:
            response = await asyncio.wait_for(
                client.post(f"{NVIDIA_BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=probe_body),
                timeout=NVIDIA_PROBE_TIMEOUT_SECONDS,
            )
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return {"ok": False, "model": model, "latency_ms": round((time.monotonic() - started) * 1000),
                "detail": f"A NVIDIA não concluiu o teste em {NVIDIA_PROBE_TIMEOUT_SECONDS} segundos."}
    except httpx.HTTPError:
        return {"ok": False, "model": model, "latency_ms": round((time.monotonic() - started) * 1000),
                "detail": "Não foi possível alcançar a NVIDIA agora."}
    latency_ms = round((time.monotonic() - started) * 1000)
    if response.status_code == 200:
        return {"ok": True, "model": model, "latency_ms": latency_ms, "detail": "Modelo respondeu com sucesso."}
    detail = "A NVIDIA recusou a chave ou o modelo." if response.status_code in {401, 403, 404} else "A NVIDIA não pôde concluir o teste agora."
    return {"ok": False, "model": model, "latency_ms": latency_ms, "detail": detail}


@app.post("/api/settings/providers/validate")
async def validate_provider_key(input: ProviderValidation) -> dict[str, Any]:
    """Calls the real provider API to check the key works, before it's ever saved.
    The key is used for this one outbound request and never logged or persisted here.
    Upload-Post has no publicly documented lightweight auth-check endpoint, so it's
    accepted without verification rather than guessing at one and risking a false
    rejection of a good key (or false acceptance of a bad one)."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            if input.provider == "moonshot":
                region = await detect_moonshot_region(client, input.api_key)
                detail = None if region else "Chave rejeitada pelo Moonshot/Kimi (testei as plataformas China e Global)."
                return {"valid": region is not None, "detail": detail, "region": region}
            if input.provider == "nvidia":
                # /v1/models on the NIM gateway answers without checking the key at all, so a
                # cheap real completion is the only reliable way to tell a bad key from a good one.
                response = await client.post(
                    f"{NVIDIA_BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {input.api_key}"},
                    json={"model": NVIDIA_DEFAULT_TEXT_MODEL, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                )
                return {"valid": response.status_code == 200, "detail": None if response.status_code == 200 else "Chave rejeitada pela NVIDIA."}
            if input.provider == "pexels":
                # A common query string like "test" gets served from Cloudflare's edge
                # cache regardless of the Authorization header, making every key look
                # valid; a random query forces a real cache bypass to Pexels' origin.
                response = await client.get("https://api.pexels.com/v1/search", params={"query": str(uuid4()), "per_page": 1}, headers={"Authorization": input.api_key})
                return {"valid": response.status_code == 200, "detail": None if response.status_code == 200 else "Chave rejeitada pelo Pexels."}
            if input.provider == "pixabay":
                response = await client.get("https://pixabay.com/api/videos/", params={"key": input.api_key, "q": str(uuid4()), "per_page": 3})
                return {"valid": response.status_code == 200, "detail": None if response.status_code == 200 else "Chave rejeitada pelo Pixabay."}
            if input.provider == "coverr":
                response = await client.get("https://api.coverr.co/videos", params={"query": str(uuid4())}, headers={"Authorization": f"Bearer {input.api_key}"})
                return {"valid": response.status_code == 200, "detail": None if response.status_code == 200 else "Chave rejeitada pelo Coverr."}
            return {"valid": None, "detail": "Upload-Post não tem verificação automática — a chave será salva sem checar antes."}
    except httpx.HTTPError as exc:
        return {"valid": None, "detail": f"Não foi possível verificar agora: {exc}"}


@app.put("/api/settings/providers")
async def update_providers(input: ProviderCredentials) -> dict[str, Any]:
    """Writes credentials straight to config.toml - never logged, never echoed back
    (the response is only the boolean readiness map, same as GET). Turbo reads this
    file once at startup, so a running moneyprinterturbo container needs a restart
    (`docker compose restart moneyprinterturbo`) to pick up a change made here."""
    config_path = DATA_DIR / "moneyprinter" / "config.toml"
    if not config_path.exists():
        raise HTTPException(status_code=409, detail="config.toml não encontrado. Rode setup.ps1 primeiro.")
    text = config_path.read_text(encoding="utf-8")
    if input.nvidia_text_model:
        nvidia_model_or_422(input.nvidia_text_model)
    active_provider = _config_string(text, "cockpit_llm_provider") or "moonshot"
    effective_provider = input.llm_provider or active_provider
    turbo_changed = bool(input.llm_provider) or (
        effective_provider == "nvidia" and bool(input.nvidia_api_key or input.nvidia_text_model)
    ) or (
        effective_provider == "moonshot" and bool(input.moonshot_api_key or input.moonshot_model_name)
    )
    if input.moonshot_api_key:
        text = _toml_set(text, "moonshot_api_key", f'"{_toml_escape(input.moonshot_api_key)}"')
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            region = await detect_moonshot_region(client, input.moonshot_api_key)
        # Blank base_url makes Turbo default to the China endpoint; a Global-platform
        # key only works if we point it at api.moonshot.ai explicitly. When detection is
        # inconclusive (network blip, rejected key saved anyway) keep the current value.
        if region:
            text = _toml_set(text, "moonshot_base_url", f'"{MOONSHOT_ENDPOINTS["global"]}"' if region == "global" else '""')
    if input.moonshot_model_name:
        text = _toml_set(text, "moonshot_model_name", f'"{_toml_escape(input.moonshot_model_name)}"')
    if input.nvidia_api_key:
        text = _toml_set(text, "nvidia_api_key", f'"{_toml_escape(input.nvidia_api_key)}"')
    if input.nvidia_text_model:
        text = _toml_set(text, "nvidia_text_model", f'"{_toml_escape(input.nvidia_text_model)}"')
    if input.llm_provider:
        text = _toml_set(text, "cockpit_llm_provider", f'"{input.llm_provider}"')
        if input.llm_provider == "nvidia":
            # The Short's own script generator lives in Turbo, not here - point its "openai"
            # provider slot at the NIM gateway too, so both scripts come from the same model.
            nvidia_key = _config_string(text, "nvidia_api_key")
            if not nvidia_key:
                raise HTTPException(status_code=409, detail="Configure a chave NVIDIA antes de trocar o provedor de IA.")
            text = _toml_set(text, "llm_provider", '"openai"')
            text = _toml_set(text, "openai_api_key", f'"{_toml_escape(nvidia_key)}"')
            text = _toml_set(text, "openai_base_url", f'"{NVIDIA_BASE_URL}"')
            text = _toml_set(text, "openai_model_name", f'"{_toml_escape(_config_string(text, "nvidia_text_model") or NVIDIA_DEFAULT_TEXT_MODEL)}"')
        else:
            text = _toml_set(text, "llm_provider", '"moonshot"')
    # Saving a different NVIDIA model used to update only the Gateway setting.  Keep
    # Turbo's OpenAI-compatible slot in sync even when the provider was already NVIDIA.
    if _config_string(text, "cockpit_llm_provider") == "nvidia":
        nvidia_key = _config_string(text, "nvidia_api_key")
        if not nvidia_key:
            raise HTTPException(status_code=409, detail="Configure a chave NVIDIA antes de usar este provedor.")
        text = _toml_set(text, "llm_provider", '"openai"')
        text = _toml_set(text, "openai_api_key", f'"{_toml_escape(nvidia_key)}"')
        text = _toml_set(text, "openai_base_url", f'"{NVIDIA_BASE_URL}"')
        text = _toml_set(text, "openai_model_name", f'"{_toml_escape(_config_string(text, "nvidia_text_model") or NVIDIA_DEFAULT_TEXT_MODEL)}"')
    if input.pexels_api_key:
        text = _toml_set(text, "pexels_api_keys", f'["{_toml_escape(input.pexels_api_key)}"]')
    if input.pixabay_api_key:
        text = _toml_set(text, "pixabay_api_keys", f'["{_toml_escape(input.pixabay_api_key)}"]')
    if input.coverr_api_key:
        text = _toml_set(text, "coverr_api_keys", f'["{_toml_escape(input.coverr_api_key)}"]')
    if input.upload_post_api_key:
        text = _toml_set(text, "upload_post_api_key", f'"{_toml_escape(input.upload_post_api_key)}"')
    if input.upload_post_username:
        text = _toml_set(text, "upload_post_username", f'"{_toml_escape(input.upload_post_username)}"')
    config_path.write_text(text, encoding="utf-8")
    return {**provider_readiness(), "restart_required": turbo_changed, "turbo_pending_restart": turbo_changed}


# VideoParams fields from MoneyPrinterTurbo v1.3.7 app/models/schema.py that the
# Cockpit exposes. Anything else in settings (e.g. subtitle_provider, a config.toml
# concern) is dropped instead of being sent and rejected by Turbo's validation.
TURBO_VIDEO_FIELDS = (
    "video_aspect", "video_language", "video_source", "video_count", "video_clip_duration", "video_clip_speed",
    "video_concat_mode", "video_transition_mode", "video_terms", "match_materials_to_script", "paragraph_number",
    "video_script_prompt", "custom_system_prompt", "voice_name", "voice_rate", "voice_volume",
    "bgm_type", "bgm_volume", "subtitle_enabled", "subtitle_position", "font_size", "text_fore_color",
    "stroke_color", "stroke_width", "n_threads", "video_materials",
)


# Field(ge/le) bounds from Turbo's VideoParams; out-of-range values would 422 the whole job.
BOUNDED_INT_FIELDS = {"paragraph_number": (1, 10), "video_clip_duration": (1, None), "video_count": (1, None)}


def build_turbo_payload(subject: str, script: str, settings: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"video_subject": subject.strip(), "video_script": script.strip(), "font_name": "Arial.ttf"}
    for key in TURBO_VIDEO_FIELDS:
        value = settings.get(key)
        # Turbo treats a blank transition as invalid and blank terms as "use these
        # (empty) terms"; omitting them lets Turbo apply its own defaults.
        if value is None or (value == "" and key in {"video_transition_mode", "video_terms"}):
            continue
        if key in BOUNDED_INT_FIELDS:
            low, high = BOUNDED_INT_FIELDS[key]
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            value = max(low, value if high is None else min(high, value))
        payload[key] = value
    return payload


def normalize_retry_payload(job: sqlite3.Row) -> tuple[dict[str, Any], list[str]]:
    """Rebuild legacy payloads through today's allow-list and format locks."""
    legacy = json.loads(job["request_json"])
    raw_settings = {key: legacy[key] for key in TURBO_VIDEO_FIELDS if key in legacy}
    if "target_minutes" in legacy:
        raw_settings["target_minutes"] = legacy["target_minutes"]
    warnings: list[str] = []
    curated = bool(job["generation_item_id"]) and raw_settings.get("video_source") == "local"
    if raw_settings.get("video_source") not in ({None, "pexels", "pixabay", "coverr", "local"} if curated else {None, "pexels", "pixabay", "coverr"}):
        warnings.append("Fonte antiga convertida para Pexels.")
    raw_settings["video_source"] = raw_settings.get("video_source") if raw_settings.get("video_source") in ({"pexels", "pixabay", "coverr", "local"} if curated else {"pexels", "pixabay", "coverr"}) else "pexels"
    format = job["format"] if job["format"] in FORMAT_LOCKED else "short"
    settings = {**generation_defaults(format), **raw_settings, **FORMAT_LOCKED[format]}
    payload = build_turbo_payload(str(legacy.get("video_subject") or job["subject"]), str(legacy.get("video_script") or job["script"]), settings)
    if set(legacy) - set(TURBO_VIDEO_FIELDS) - {"video_subject", "video_script", "target_minutes"}:
        warnings.append("Campos legados não suportados foram removidos.")
    return payload, warnings


def job_max_words(format: str, settings: dict[str, Any]) -> int | None:
    """The narration budget the screen asked for: the Short's "Duração alvo" select, or the long
    video's target in minutes. None means no target was sent, so nothing is trimmed."""
    if format == "short":
        return short_script_max_words(int(settings.get("paragraph_number", 5) or 5), float(settings.get("voice_rate") or 1.0))
    try:
        minutes = float(settings.get("target_minutes") or 0)
    except (TypeError, ValueError):
        return None
    return int(minutes * WORDS_PER_MINUTE * float(settings.get("voice_rate") or 1.0)) or None


@app.post("/api/projects/{project_id}/jobs", status_code=202)
async def create_job(project_id: str, input: JobCreate, *, generation_item_id: str | None = None) -> dict[str, Any]:
    if input.settings.get("video_source") == "local" and not generation_item_id:
        raise HTTPException(status_code=422, detail="Materiais locais só podem ser enviados pelo curador da geração.")
    if generation_item_id:
        with database() as db:
            existing = db.execute("SELECT * FROM video_jobs WHERE generation_item_id=?", (generation_item_id,)).fetchone()
            if existing:
                return row_as_job(existing, db)
    project = row_as_project(get_project_or_404(project_id))
    with database() as db:
        channels = [channel_row_or_404(db, channel_id) for channel_id in dict.fromkeys(input.channel_ids)]
    # Checked before Turbo starts a render that could then never be published where it was meant to go.
    validate_publish_channels(channels, input.format)
    conflicting_profile_fields = conflicting_channel_profile_fields(channels, input.format)
    if conflicting_profile_fields:
        names = ", ".join(channel["name"] for channel in channels)
        labels = {"video_language": "idioma", "voice_name": "voz", "voice_rate": "velocidade", "voice_volume": "volume", "subtitle_enabled": "legendas", "subtitle_position": "posição", "font_size": "tamanho", "text_fore_color": "cor do texto", "stroke_color": "cor da borda", "stroke_width": "espessura da borda", "paragraph_number": "duração do Short", "target_minutes": "duração", "video_clip_duration": "duração do clipe", "video_count": "quantidade de vídeos", "video_concat_mode": "modo de imagens", "video_transition_mode": "transição", "video_clip_speed": "velocidade do clipe", "bgm_type": "trilha sonora", "bgm_volume": "volume da música"}
        fields = ", ".join(labels.get(field, field) for field in conflicting_profile_fields)
        raise HTTPException(status_code=409, detail=f"Os canais {names} têm perfis de voz, legendas ou parâmetros de vídeo diferentes ({fields}). Alinhe os perfis ou gere vídeos separados.")
    channel_settings = channel_generation_settings(channels[0], input.format) if channels else {}
    merged = {**generation_defaults(input.format), **project["defaults"], **channel_settings, **input.settings, **FORMAT_LOCKED[input.format]}
    effective_profile = normalize_channel_profile({
        "language": merged.get("video_language", "pt-BR"),
        **{field: merged.get(field) for field in CHANNEL_PROFILE_FIELDS},
    })
    merged.update({"video_language": effective_profile.pop("language"), **effective_profile})
    channel_prompts = list(dict.fromkeys(str(channel["system_prompt"] or "").strip() for channel in channels if str(channel["system_prompt"] or "").strip()))
    if len(channel_prompts) > 1:
        raise HTTPException(status_code=409, detail="Os canais selecionados têm prompts de sistema diferentes. Gere um vídeo por padrão editorial.")
    if channel_prompts:
        generator_prompt = str(merged.get("custom_system_prompt") or "").strip()
        merged["custom_system_prompt"] = "\n\n".join([channel_prompts[0], generator_prompt]).strip()
    # Turbo's encoder defaults to 2 threads; this container sees the same Docker VM CPUs as Turbo.
    merged.setdefault("n_threads", os.cpu_count() or 2)
    script_text = input.script.strip()
    follow_script = merged.get("video_concat_mode") == "script"
    if follow_script:
        # "Seguir o roteiro": one visual search term per script chunk, downloaded in that
        # order, instead of Turbo's handful of generic terms shared across the whole video
        # (see visual_terms()) - that's what made footage look unrelated to the narration.
        merged["match_materials_to_script"], merged["video_concat_mode"] = True, "sequential"
        if not script_text and input.format == "short":
            script_response = await turbo_request("POST", "/api/v1/scripts", json={
                "video_subject": input.subject, "video_language": merged.get("video_language", "pt-BR"),
                "paragraph_number": merged.get("paragraph_number", 5),
                "video_script_prompt": short_script_prompt(str(merged.get("video_script_prompt", "")), int(merged.get("paragraph_number", 5) or 5), float(merged.get("voice_rate") or 1.0)),
                "custom_system_prompt": merged.get("custom_system_prompt", ""),
            })
            script_text = str((unwrap(script_response.json()) or {}).get("video_script", "")).strip()
    # The chosen duration wins over whatever was written, pasted or generated, in every mode: a
    # longer script would stretch the narration past it, and the visuals are cut to fit the script.
    max_words = job_max_words(input.format, merged)
    if script_text and max_words and not generation_item_id:
        script_text = trim_to_words(script_text, max_words)
    if follow_script and script_text and not str(merged.get("video_terms", "")).strip():
        terms, clip_seconds = await visual_terms(input.subject, script_text, float(merged.get("video_clip_duration") or 5), float(merged.get("voice_rate") or 1))
        merged["video_terms"], merged["video_clip_duration"] = ", ".join(terms), max(1, round(clip_seconds))
    request_payload = build_turbo_payload(input.subject, script_text, merged)
    response = await turbo_request("POST", "/api/v1/videos", json=request_payload)
    turbo_result = unwrap(response.json())
    if not isinstance(turbo_result, dict) or not turbo_result.get("task_id"):
        raise HTTPException(status_code=502, detail="Resposta inválida do MoneyPrinterTurbo ao criar tarefa")
    job_id, timestamp = str(uuid4()), now()
    persisted_request = {**request_payload, **({"target_minutes": merged["target_minutes"]} if "target_minutes" in merged else {})}
    with database() as db:
        db.execute(
            "INSERT INTO video_jobs(id, project_id, turbo_task_id, subject, script, request_json, state, created_at, updated_at, format, generation_item_id) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
            (job_id, project_id, turbo_result["task_id"], input.subject.strip(), script_text, json.dumps(persisted_request), timestamp, timestamp, input.format, generation_item_id),
        )
        # The channels picked now are the video's publish targets, waiting for approval.
        for channel in channels:
            db.execute(
                "INSERT INTO publications(id, job_id, platform, channel_id, state, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (str(uuid4()), job_id, channel["platform"], channel["id"], timestamp, timestamp),
            )
        return row_as_job(db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), db)


def validate_publish_channels(channels: list[sqlite3.Row], format: str | None) -> None:
    for channel in channels:
        if channel["status"] != "connected":
            raise HTTPException(status_code=409, detail=f"O canal {channel['name']} não está conectado. Reconecte em Canais.")
        if format == "long" and channel["platform"] != "youtube":
            raise HTTPException(status_code=409, detail="TikTok recebe apenas Shorts. Escolha um canal do YouTube para vídeos longos.")
    # publications is UNIQUE(job_id, platform): one post per platform per video.
    if len({channel["platform"] for channel in channels}) < len(channels):
        raise HTTPException(status_code=409, detail="Escolha no máximo um canal por plataforma.")


@app.get("/api/projects/{project_id}/jobs")
async def list_jobs(project_id: str) -> list[dict[str, Any]]:
    get_project_or_404(project_id)
    with database() as db:
        identifiers = [row["id"] for row in db.execute("SELECT id FROM video_jobs WHERE project_id=? AND deleted_at IS NULL ORDER BY created_at DESC", (project_id,))]
    results = []
    for job_id in identifiers:
        await reconcile_publications(job_id)
        try:
            results.append(await reconcile_job(job_id))
        except HTTPException:
            with database() as db:
                row = db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
                if row:
                    results.append(row_as_job(row, db))
    return results


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    with database() as db:
        row = db.execute("SELECT * FROM video_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    await reconcile_publications(job_id)
    try:
        return await reconcile_job(job_id)
    except HTTPException:
        with database() as db:
            return row_as_job(db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), db)


@app.post("/api/jobs/{job_id}/retry", status_code=202)
async def retry_job(job_id: str) -> dict[str, Any]:
    with database() as db:
        row = db.execute("SELECT * FROM video_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row or row["state"] not in {"failed", "interrupted"}:
        raise HTTPException(status_code=409, detail="Apenas trabalhos interrompidos ou com falha podem ser repetidos")
    request, warnings = normalize_retry_payload(row)
    with database() as db:
        channels = [item for item in db.execute(
            "SELECT channels.* FROM publications JOIN channels ON channels.id=publications.channel_id WHERE publications.job_id=? AND publications.channel_id IS NOT NULL",
            (job_id,),
        )]
    validate_publish_channels(channels, row["format"])
    response = await turbo_request("POST", "/api/v1/videos", json=request)
    created = unwrap(response.json())
    task_id = created.get("task_id") if isinstance(created, dict) else None
    if not task_id:
        raise HTTPException(status_code=502, detail="O Turbo não retornou task_id")
    with database() as db:
        db.execute("UPDATE video_jobs SET turbo_task_id=?, request_json=?, state='queued', progress=0, last_observed_progress=0, progress_updated_at=?, error=NULL, failed_stage=NULL, interruption_reason=NULL, updated_at=? WHERE id=?", (task_id, json.dumps(request), now(), now(), job_id))
        if row["generation_item_id"]:
            db.execute("UPDATE generation_quality SET state='pending', report_json='{}', updated_at=? WHERE item_id=? AND job_id=?", (now(), row["generation_item_id"], job_id))
        result = row_as_job(db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), db)
    result["warnings"] = warnings
    return result


@app.post("/api/jobs/{job_id}/cancel", status_code=202)
async def cancel_job(job_id: str) -> dict[str, Any]:
    with database() as db:
        row = db.execute("SELECT * FROM video_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row or row["state"] not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Apenas trabalhos na fila ou em geração podem ser parados")
    if row["turbo_task_id"]:
        try:
            await turbo_request("DELETE", f"/api/v1/tasks/{row['turbo_task_id']}")
        except HTTPException:
            pass
    with database() as db:
        db.execute(
            "UPDATE video_jobs SET state='interrupted', error=COALESCE(error, 'Cancelado pelo usuário'), updated_at=? WHERE id=?",
            (now(), job_id),
        )
        return row_as_job(db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), db)


@app.delete("/api/jobs/{job_id}", status_code=204, response_model=None)
async def delete_job(job_id: str) -> None:
    with database() as db:
        row = db.execute("SELECT * FROM video_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    if row["state"] in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Não é permitido excluir uma geração ativa")
    if row["turbo_task_id"]:
        try:
            await turbo_request("DELETE", f"/api/v1/tasks/{row['turbo_task_id']}")
        except HTTPException:
            pass
    with database() as db:
        db.execute("UPDATE video_jobs SET state='deleted', deleted_at=?, updated_at=? WHERE id=?", (now(), now(), job_id))


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        previous = ""
        for _ in range(3600):
            current = await get_job(job_id)
            encoded = json.dumps(current)
            if encoded != previous:
                yield sse_message("job.updated", encoded)
                previous = encoded
            if current["state"] in {"completed", "approved", "failed", "interrupted", "deleted"}:
                break
            await asyncio.sleep(2)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def artifact_or_404(job_id: str, artifact_id: str) -> sqlite3.Row:
    with database() as db:
        row = db.execute("SELECT * FROM artifacts WHERE id=? AND job_id=?", (artifact_id, job_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Artefato não encontrado")
    return row


@app.get("/api/jobs/{job_id}/artifacts/{artifact_id}/{mode}")
async def artifact_proxy(job_id: str, artifact_id: str, mode: Literal["stream", "download"], request: Request) -> StreamingResponse:
    artifact = artifact_or_404(job_id, artifact_id)
    headers = turbo_headers()
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    # Turbo mounts these as router.get("/stream/...") under the /api/v1 prefix - no /tasks/ segment.
    path = f"/api/v1/{mode}/{artifact['remote_path']}"
    try:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
        response = await client.send(client.build_request("GET", f"{TURBO_BASE_URL}{path}", headers=headers), stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Falha ao obter artefato: {exc}") from exc
    if response.status_code >= 400:
        await client.aclose()
        raise HTTPException(status_code=502, detail="O Turbo não encontrou o artefato")
    pass_headers = {key: value for key, value in response.headers.items() if key.lower() in {"content-length", "content-range", "accept-ranges", "content-type"}}
    if mode == "download":
        pass_headers["Content-Disposition"] = f'attachment; filename="{artifact["filename"].replace(chr(34), "")}"'
    async def iterator() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()
    return StreamingResponse(iterator(), status_code=response.status_code, headers=pass_headers)


def job_row_or_404(db: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM video_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    return row


def normalize_youtube_metadata(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Port of Lumen's youtube-metadata-validator: YouTube rejects the whole upload over one
    bad field, so clean what can be cleaned and report the rest. Unlike the original, line
    breaks survive in the description; '<' and '>' go because the API refuses them."""
    def line(value: Any) -> str:
        return re.sub(r"[\s\x00-\x1f\x7f<>]+", " ", str(value or "")).strip()

    title = line(raw.get("title"))[:100].strip()
    description = re.sub(r"[\x00-\x08\x0b-\x1f\x7f<>]", "", str(raw.get("description") or "")).strip()[:5000]
    raw_tags = raw.get("tags") or []
    tags: list[str] = []
    total = 0
    for item in raw_tags.split(",") if isinstance(raw_tags, str) else raw_tags:
        tag = line(item).lstrip("#").replace('"', "")[:100].strip()
        if not tag or tag.lower() in {existing.lower() for existing in tags}:
            continue
        # YouTube counts a tag with spaces as quoted, plus a comma between tags; 450 of 500 leaves headroom.
        size = len(tag) + (2 if " " in tag else 0) + (1 if tags else 0)
        if total + size > 450 or len(tags) >= 30:
            break
        tags.append(tag)
        total += size
    value = {
        "title": title, "description": description, "tags": tags,
        "category_id": str(raw.get("category_id") or "22").strip(), "language": str(raw.get("language") or "pt-BR").strip(),
    }
    errors, warnings = [], []
    if not title:
        errors.append("O título é obrigatório.")
    if not re.fullmatch(r"\d{1,3}", value["category_id"]):
        errors.append("A categoria do YouTube deve ser um ID numérico.")
    if not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*", value["language"]):
        errors.append("Idioma inválido (use um código como pt-BR).")
    if not description:
        warnings.append("A descrição está vazia.")
    if len(tags) < 3:
        warnings.append("Menos de três tags utilizáveis.")
    return value, errors, warnings


METADATA_STATES = {"completed", "approved"}


class MetadataInput(BaseModel):
    title: str = Field(default="", max_length=1000)
    description: str = Field(default="", max_length=20000)
    tags: list[str] | str = Field(default_factory=list, max_length=500)
    category_id: str = Field(default="22", max_length=10)


def store_metadata(job_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    with database() as db:
        job = job_row_or_404(db, job_id)
        if job["state"] not in METADATA_STATES:
            raise HTTPException(status_code=409, detail="Metadados só podem ser definidos para vídeos concluídos")
        value, errors, warnings = normalize_youtube_metadata({**raw, "language": job_language(db, job)})
        if errors:
            raise HTTPException(status_code=422, detail=" ".join(errors))
        db.execute(
            "INSERT INTO job_metadata(job_id, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (job_id, json.dumps(value), now()),
        )
        return {**row_as_job(job, db), "warnings": warnings}


def _config_string(text: str, key: str) -> str:
    match = re.search(rf'^{key}\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def config_value(key: str) -> str:
    config = DATA_DIR / "moneyprinter" / "config.toml"
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    return _config_string(text, key)


def moonshot_config() -> tuple[str, str, str]:
    # Blank base_url means the China endpoint, same default Turbo applies.
    base_url = (config_value("moonshot_base_url") or MOONSHOT_ENDPOINTS["china"]).rstrip("/")
    return config_value("moonshot_api_key"), base_url, config_value("moonshot_model_name") or "moonshot-v1-8k"


def llm_config() -> tuple[str, str, str, str]:
    """Which provider/base_url/key/model backs the gateway's own text calls (metadata, long
    script, strategy, analytics) - independent of what Turbo itself uses for the Short's script."""
    if config_value("cockpit_llm_provider") == "nvidia":
        return "nvidia", NVIDIA_BASE_URL, config_value("nvidia_api_key"), config_value("nvidia_text_model") or NVIDIA_DEFAULT_TEXT_MODEL
    key, base_url, model = moonshot_config()
    return "moonshot", base_url, key, model


@app.put("/api/jobs/{job_id}/metadata")
def put_metadata(job_id: str, input: MetadataInput) -> dict[str, Any]:
    return store_metadata(job_id, input.model_dump())


def llm_task(prompt: str, json_mode: bool) -> Literal["json", "outline", "script"]:
    """Keep existing call sites stable while enforcing task-sized output limits."""
    if "Planeje um vídeo de YouTube" in prompt:
        return "outline"
    if "Escreva a narração da seção" in prompt:
        return "script"
    return "json" if json_mode else "script"


async def llm_chat(
    prompt: str,
    json_mode: bool = False,
    *,
    task: Literal["json", "outline", "script", "test"] | None = None,
    max_tokens: int | None = None,
    fast_script: bool = False,
) -> str:
    provider, base_url, key, model = llm_config()
    label = "Moonshot/Kimi" if provider == "moonshot" else "NVIDIA"
    if not key:
        raise HTTPException(status_code=409, detail=f"Configure a chave {label} em Configurações.")
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    # response_format support varies across NIM models; the prompts already ask for plain JSON
    # and parse_json_object() tolerates prose or a code fence around it.
    if json_mode and provider == "moonshot":
        body["response_format"] = {"type": "json_object"}
    if provider == "nvidia":
        body = nvidia_chat_body(model, prompt, task or llm_task(prompt, json_mode), max_tokens=max_tokens, fast_script=fast_script)
    elif max_tokens is not None:
        body["max_tokens"] = max_tokens
    read_timeout = NVIDIA_GENERATION_READ_TIMEOUT_SECONDS if provider == "nvidia" else 180
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=read_timeout)) as client:
            response = await send_with_retry(client, "POST", f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)
    except httpx.ReadTimeout as exc:
        if provider == "nvidia":
            raise HTTPException(
                status_code=504,
                detail=(
                    "A NVIDIA não concluiu esta etapa do roteiro em 5 minutos. "
                    "A conexão básica está disponível; tente novamente ou escolha "
                    "GLM 5.3 Flash em Configurações para roteiros mais rápidos."
                ),
            ) from exc
        raise HTTPException(status_code=504, detail=f"{label} demorou demais para responder.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"{label} indisponível: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"{label} respondeu com erro {response.status_code}.")
    try:
        message = response.json()["choices"][0]["message"]
        # NVIDIA reasoning models may send their private chain-of-thought in a
        # separate field.  The Cockpit keeps only the final content for scripts
        # and metadata; reasoning is intentionally neither persisted nor shown.
        _reasoning = message.get("reasoning_content")
        content = message.get("content")
        return str(content) if content is not None else ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"O {label} devolveu uma resposta inesperada") from exc


def parse_json_object(content: str) -> dict[str, Any]:
    """Find a complete JSON object even when the model adds prose or code fences."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", content or ""):
        try:
            parsed, _ = decoder.raw_decode(content, match.start())
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise HTTPException(status_code=502, detail="A IA não devolveu JSON válido")


async def llm_json_object(
    prompt: str,
    *,
    task: Literal["json", "outline", "script", "test"] = "json",
    max_tokens: int | None = None,
    fast_script: bool = False,
    label: str = "esta etapa",
) -> dict[str, Any]:
    """Retry malformed structured output with the selected provider and model."""
    for attempt in range(3):
        retry_prompt = prompt
        if attempt:
            retry_prompt += (
                "\nA resposta anterior veio vazia ou com JSON inválido. "
                "Responda somente com um objeto JSON completo, sem markdown, comentários ou texto adicional."
            )
        content = await llm_chat(
            retry_prompt, json_mode=True, task=task,
            max_tokens=(max_tokens or NVIDIA_TASK_TOKEN_LIMITS[task]) + attempt * 512,
            fast_script=fast_script,
        )
        try:
            return parse_json_object(content)
        except HTTPException as exc:
            if exc.detail != "A IA não devolveu JSON válido":
                raise
    raise HTTPException(
        status_code=502,
        detail=f"A IA não devolveu JSON válido para {label} após 3 tentativas. Repita este canal ou escolha outro modelo no provedor configurado.",
    )



class AssistantChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=3000)


class AssistantChatInput(BaseModel):
    messages: list[AssistantChatMessage] = Field(min_length=1, max_length=16)
    channel_id: str | None = Field(default=None, max_length=36)


def _assistant_channel(channels: list[dict[str, Any]], selected_id: str | None, args: dict[str, Any]) -> dict[str, Any] | None:
    requested = str(selected_id or args.get("channel_id") or "").strip()
    if requested:
        match = next((item for item in channels if str(item.get("id")) == requested), None)
        if match:
            return match
    name = str(args.get("channel_name") or "").strip().casefold()
    if name:
        matches = [item for item in channels if str(item.get("name") or "").casefold() == name]
        if len(matches) == 1:
            return matches[0]
    return channels[0] if len(channels) == 1 else None


@app.post("/api/assistant/chat")
async def assistant_chat(input: AssistantChatInput) -> dict[str, Any]:
    channels = list_channels()
    channel_context = [
        {key: channel.get(key) for key in ("id", "name", "platform", "niche", "language", "status")}
        for channel in channels
    ]
    transcript = [{"role": item.role, "content": item.content} for item in input.messages[-12:]]
    prompt = (
        "Você é a assistente conversacional do MoneyPrinter Cockpit. Responda em português brasileiro, "
        "de modo direto e natural. Entenda o histórico e escolha uma ação somente quando o usuário a pedir "
        "ou quando ela for necessária para responder. Nunca invente métricas do canal. "
        "Ações disponíveis: reply (conversa e explicações), clarify (perguntar os dados que faltam), "
        "growth_goal (calcular metas YPP), growth_score (pontuar uma oportunidade de tema), "
        "growth_plan (criar um plano durável; só quando pedido explicitamente), short_script "
        "(gerar um roteiro de Short; só quando pedido explicitamente), channels (listar canais), "
        "jobs (listar trabalhos recentes), workflow_status (consultar um workflow) e system_status "
        "(verificar a saúde do sistema). Não publique nem aprove conteúdo. Planos ficam em revisão humana. "
        "Para growth_goal, forneça args.metrics com subscribers, subscriber_target, qualified_watch_hours, "
        "watch_hour_target, qualified_shorts_views, shorts_view_target, days_remaining e, se conhecido, "
        "average_view_duration_minutes. Se faltar algum dado obrigatório, escolha clarify sem adivinhar. "
        "Para growth_score, forneça topic e dimensions com 12 notas de 0 a 100: demand, audience_fit, "
        "historical_retention, subscriber_conversion, expected_avd, evergreen, trend_velocity, monetization, "
        "stock_availability, competition, production_cost e saturation. Se estimar notas com a IA, marque "
        "args.estimated=true. Para growth_plan, forneça topic, ypp_goal (mesmas métricas) e opportunity "
        "(as 12 notas); nunca defina publicação automática. Para short_script, forneça subject. "
        "Para workflow_status, forneça workflow_id. Para ações que dependem de canal, use o canal selecionado "
        "ou um nome que corresponda exatamente à lista. Responda somente com um objeto JSON neste formato: "
        '{"action":"reply|clarify|growth_goal|growth_score|growth_plan|short_script|channels|jobs|workflow_status|system_status","message":"texto curto","args":{}}'
        + "\nCanal selecionado: " + json.dumps(input.channel_id, ensure_ascii=False)
        + "\nCanais disponíveis: " + json.dumps(channel_context, ensure_ascii=False)
        + "\nHistórico: " + json.dumps(transcript, ensure_ascii=False)
    )
    decision = parse_json_object(await llm_chat(prompt, json_mode=True, task="json", max_tokens=1600))
    action = str(decision.get("action") or "reply")
    message = str(decision.get("message") or "").strip()
    args = decision.get("args") if isinstance(decision.get("args"), dict) else {}

    if action in {"reply", "clarify"}:
        return {"action": action, "message": message or "Pode me contar um pouco mais sobre o que você precisa?"}
    if action == "channels":
        if not channels:
            return {"action": action, "message": "Ainda não há canais cadastrados. Conecte um canal na área Canais."}
        lines = [f"- {item.get('name')} ({item.get('platform')}) · {item.get('status')}" for item in channels]
        return {"action": action, "message": "Seus canais cadastrados:\n" + "\n".join(lines), "data": channel_context}
    if action == "jobs":
        with database() as db:
            jobs = [dict(row) for row in db.execute(
                "SELECT id, subject, state, progress, created_at FROM video_jobs "
                "WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 8"
            )]
        if not jobs:
            return {"action": action, "message": "Ainda não há vídeos em processamento ou no histórico."}
        lines = [f"- {item['subject']} · {item['state']} · {item['progress']}%" for item in jobs]
        return {"action": action, "message": "Trabalhos recentes:\n" + "\n".join(lines), "data": jobs}
    if action == "system_status":
        status = await health_status()
        return {"action": action, "message": (
            f"Estado do sistema: {status.get('status', 'indisponível')}. "
            f"Banco de dados: {'conectado' if status.get('database') else 'indisponível'}; "
            f"MoneyPrinter Turbo: {'conectado' if status.get('turbo') else 'indisponível'}."
        ), "data": status}
    if action == "workflow_status":
        try:
            workflow_id = UUID(str(args.get("workflow_id") or ""))
            workflow = growth_store.workflow(workflow_id)
        except (ValueError, GrowthStoreUnavailable) as exc:
            raise HTTPException(status_code=400 if isinstance(exc, ValueError) else 503, detail="Não foi possível consultar esse workflow.") from exc
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow não encontrado.")
        return {"action": action, "message": (
            f"Plano {workflow_id}: estado {workflow.get('state')} · etapa {workflow.get('current_step') or '—'}."
        ), "data": workflow}

    channel = _assistant_channel(channels, input.channel_id, args)
    if not channel:
        detail = "Conecte um canal antes de continuar." if not channels else "Selecione um canal ou diga o nome exato dele."
        return {"action": "clarify", "message": detail}

    try:
        channel_id = UUID(str(channel["id"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="O canal selecionado tem um identificador inválido.") from exc

    if action == "growth_goal":
        try:
            goal_input = YPPGoalRequest(channel_id=channel_id, metrics=args.get("metrics") or {})
        except ValidationError as exc:
            return {"action": "clarify", "message": "Para calcular seu gap, me passe os valores atuais e as metas de inscritos, horas qualificadas e visualizações de Shorts, além de quantos dias faltam. Pode escrever tudo em uma única frase."}
        result = evaluate_goal(goal_input)
        values = result["result"].model_dump(mode="json")
        return {"action": action, "data": values, "message": (
            f"Para {channel.get('name')}, faltam {values['subscribers']['gap']:,.0f} inscritos "
            f"({values['subscribers']['required_daily_velocity']:,.2f}/dia), "
            f"{values['watch_hours']['gap']:,.0f} horas qualificadas "
            f"({values['watch_hours']['required_daily_velocity']:,.2f}/dia) e "
            f"{values['shorts_views']['gap']:,.0f} visualizações qualificadas de Shorts. "
            "O cálculo considera apenas métricas orgânicas qualificadas."
        )}
    if action == "growth_score":
        try:
            score_input = OpportunityScoreRequest(
                channel_id=channel_id, topic=str(args.get("topic") or ""), dimensions=args.get("dimensions") or {},
            )
        except ValidationError as exc:
            return {"action": "clarify", "message": "Me diga o tema da oportunidade. Posso estimar as dimensões para iniciar a avaliação e identificar claramente o resultado como estimativa."}
        result = score_opportunity(score_input)
        values = result["result"].model_dump(mode="json")
        estimate = " Esta é uma estimativa da IA, não uma medição de dados externos." if args.get("estimated") else ""
        return {"action": action, "data": values, "message": (
            f"O tema “{score_input.topic}” recebeu {values['score']}/100 "
            f"(versão {values['version']}).{estimate}"
        )}
    if action == "growth_plan":
        try:
            plan_input = PlanningWorkflowRequest(
                channel_id=channel_id, topic=str(args.get("topic") or ""),
                ypp_goal=args.get("ypp_goal") or {}, opportunity=args.get("opportunity") or {},
                production_target=args.get("production_target") or {"long": 1, "shorts": 3},
                publish_mode="review",
            )
        except ValidationError as exc:
            return {"action": "clarify", "message": "Para montar o plano, preciso do tema, dos números atuais e das metas YPP e do prazo. Posso coletar esses dados com você, um passo de cada vez."}
        started = await start_planning_workflow(plan_input)
        return {"action": action, "data": started, "message": (
            f"Iniciei o plano para “{plan_input.topic}” no canal {channel.get('name')}. "
            f"Ele ficará em revisão humana. ID do workflow: {started['id']}."
        )}
    if action == "short_script":
        subject = str(args.get("subject") or "").strip()
        if not subject:
            return {"action": "clarify", "message": "Qual é o tema do roteiro?"}
        result = await short_script(ShortScriptInput(
            subject=subject, language=str(channel.get("language") or "pt-BR"),
            script_prompt=str(args.get("style") or "")[:2000],
            system_prompt=str(channel.get("system_prompt") or "")[:4000],
        ))
        script = result["script"]
        return {"action": action, "data": result, "message": (
            f"Roteiro de Short sobre “{subject}” ({result['words']} palavras):\n\n{script}"
        )}
    return {"action": "reply", "message": message or "Ainda não consigo executar esse pedido pela conversa. Posso consultar canais e jobs, calcular metas e oportunidades, criar um plano de crescimento ou gerar um roteiro de Short."}


@app.post("/api/jobs/{job_id}/metadata/generate")
async def generate_metadata(job_id: str) -> dict[str, Any]:
    with database() as db:
        job = job_row_or_404(db, job_id)
        language = job_language(db, job)
    if job["state"] not in METADATA_STATES:
        raise HTTPException(status_code=409, detail="Metadados só podem ser gerados para vídeos concluídos")
    script = job["script"] or json.loads(job["turbo_payload_json"]).get("script") or ""
    if job["format"] == "long":
        guidance = ('"title": até 90 caracteres, otimizado para busca no YouTube, "description": um parágrafo de resumo, '
                    'depois "Neste vídeo:" com a lista dos tópicos principais e 3 hashtags no final, "tags": lista de 10 a 20 termos de busca')
        excerpt = script[:12000]
    else:
        guidance = '"title": até 70 caracteres, curto e chamativo, "description": 1 a 2 frases seguidas de 3 a 5 hashtags, "tags": lista de 8 a 15 termos de busca'
        excerpt = script[:6000]
    prompt = f"Crie metadados de YouTube no idioma {language} para o vídeo abaixo. Responda apenas JSON: {{{guidance}}}.\n\nTema: {job['subject']}\nRoteiro: {excerpt}"
    generated = parse_json_object(await llm_chat(prompt, json_mode=True))
    return store_metadata(job_id, {field: generated.get(field) for field in ("title", "description", "tags")})


WORDS_PER_MINUTE = 150  # calibration knob: Edge TTS narrates ~150 words/min at voice rate 1.0
SHORT_TARGET_SECONDS = {4: 60, 5: 75, 6: 90}  # mirrors the Shorts "Duração alvo" select in cockpit-views.js
SHORT_MIN_SECONDS = 60  # below this a Short is "curto demais" (see the word-count hint in moneyprinter-runtime.js)


def short_script_max_words(paragraph_number: int, voice_rate: float = 1.0) -> int:
    seconds = SHORT_TARGET_SECONDS.get(paragraph_number, max(SHORT_TARGET_SECONDS.values()))
    return round(seconds * WORDS_PER_MINUTE * voice_rate / 60)


def short_script_min_words() -> int:
    return round(SHORT_MIN_SECONDS * WORDS_PER_MINUTE / 60)


def short_script_prompt(script_prompt: str, paragraph_number: int, voice_rate: float = 1.0) -> str:
    """Word-range rule Turbo's writer must hit (60–90 s of narration). Rides along with any
    custom requirements instead of replacing them."""
    max_words = short_script_max_words(paragraph_number, voice_rate)
    min_words = round(SHORT_MIN_SECONDS * WORDS_PER_MINUTE * voice_rate / 60)
    length_rule = f"O roteiro deve ter entre {min_words} e {max_words} palavras (narração de 60 a 90 segundos)."
    custom = (script_prompt or "").strip()
    from gateway.editorial_quality import writing_brief
    return "\n".join(part for part in (custom, length_rule, writing_brief("short")) if part)


def trim_to_words(script: str, max_words: int) -> str:
    """Caps an AI-written script at max_words, backing up to the end of the last full sentence
    so a generated narration never reads longer than the format's chosen duration. Never applied
    to a script the user typed or pasted themselves."""
    words = script.split()
    if len(words) <= max_words:
        return script.strip()
    truncated = " ".join(words[:max_words])
    match = re.search(r"^(.*[.!?])[^.!?]*$", truncated, re.DOTALL)
    return (match.group(1) if match else truncated).strip()


class ShortScriptInput(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    language: str = Field(default="pt-BR", max_length=20)
    paragraph_number: int = Field(default=5, ge=1, le=10)
    voice_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    script_prompt: str = Field(default="", max_length=4200)
    system_prompt: str = Field(default="", max_length=12000)


@app.post("/api/scripts/short")
async def short_script(input: ShortScriptInput) -> dict[str, Any]:
    max_words = short_script_max_words(input.paragraph_number, input.voice_rate)
    min_words = round(SHORT_MIN_SECONDS * WORDS_PER_MINUTE * input.voice_rate / 60)
    # Turbo's writer only gets paragraph_number, so it often undershoots (e.g. 139 words ≈ 56 s
    # for a 60–90 s Short). State the word range explicitly in the prompt it already reads.
    response = await turbo_request("POST", "/api/v1/scripts", json={
        "video_subject": input.subject, "video_language": clean_language(input.language),
        "paragraph_number": input.paragraph_number,
        "video_script_prompt": short_script_prompt(input.script_prompt, input.paragraph_number, input.voice_rate),
        "custom_system_prompt": input.system_prompt,
    })
    script = clean_narration(str((unwrap(response.json()) or {}).get("video_script", "")))
    trimmed = trim_to_words(script, max_words)
    words = len(trimmed.split())
    return {"script": trimmed, "words": words, "trimmed": trimmed != script,
            "too_short": words < min_words, "min_words": min_words}


class LongScriptInput(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    language: str = Field(default="pt-BR", max_length=20)
    minutes: int = Field(default=8, ge=5, le=15)
    voice_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    niche: str = Field(default="", max_length=200)
    requirements: str = Field(default="", max_length=4200)
    system_prompt: str = Field(default="", max_length=12000)


def clean_narration(text: str) -> str:
    """Kimi still slips in headings, bullets or bold now and then; the TTS would read the symbols aloud."""
    text = re.sub(r"(?m)^\s*```[^\n]*$", "", text)
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"\[(?:pausa|pause|música|music|sfx|cena|scene)(?:\s*[:\-][^\]]*)?\]", "", text, flags=re.IGNORECASE)
    lines = (re.sub(r"^\s*(#+|[-*•]|\d+[.)])\s+", "", line).replace("**", "").strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def long_script_tokens(words: int, ceiling: int) -> int:
    """Reserve enough output for Portuguese narration without inviting needless reasoning.

    The estimate is intentionally below the provider-wide script cap.  A final
    word trim remains the authoritative duration guardrail.
    """
    return min(ceiling, max(768, round(words * 1.8)))


async def long_script_events(input: LongScriptInput) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Writes an editable long script and reports only safe UI progress events."""
    from gateway.editorial_quality import writing_brief
    language = clean_language(input.language)
    requirements = input.requirements.strip()
    system_prompt = input.system_prompt.strip()
    # ponytail: unlike Turbo's own custom_system_prompt (a full replacement), this can't replace the
    # whole instruction set here - the outline call must stay valid JSON and each section call must
    # stay narration-only, or the multi-step pipeline breaks. It's folded in as an extra, higher-weight
    # instruction on every call instead, same mechanism as `requirements` but framed as a standing rule.
    style = (f" Instruções de estilo do sistema, siga em tudo o que escrever: {system_prompt}." if system_prompt else "")
    style += " " + writing_brief("long")
    target_words = round(input.minutes * WORDS_PER_MINUTE * input.voice_rate)

    # The common 5-8 minute case used to incur an outline plus 4-5 serial
    # writes.  One bounded completion is both faster and coherent because the
    # entire requested narration fits in the selected model's context.
    if input.minutes <= 8:
        yield "progress", {"stage": "writing", "section": 1, "total": 1, "message": "Escrevendo o roteiro completo…"}
        generated = parse_json_object(await llm_chat(
            f"Escreva um roteiro completo de vídeo de YouTube de cerca de {input.minutes} minutos no idioma {language} sobre: {input.subject}."
            + (f" Nicho do canal: {input.niche}." if input.niche else "")
            + (f" Requisitos personalizados: {requirements}." if requirements else "") + style
            + f' Responda apenas JSON: {{"title": título conciso, "script": narração com cerca de {target_words} palavras}}. '
            "A narração deve ter abertura forte, desenvolvimento coerente e conclusão com chamada para se inscrever. "
            "Use apenas texto falado em parágrafos corridos: sem títulos no roteiro, marcadores, markdown, emojis nem indicações de cena.",
            json_mode=True,
            task="script",
            max_tokens=long_script_tokens(target_words, FAST_LONG_SCRIPT_MAX_TOKENS),
            fast_script=True,
        ))
        script = clean_narration(str(generated.get("script") or ""))
        if not script:
            raise HTTPException(status_code=502, detail="A IA não devolveu um roteiro válido")
        trimmed_script = trim_to_words(script, target_words)
        words = len(trimmed_script.split())
        result = {
            "title": str(generated.get("title") or input.subject), "script": trimmed_script,
            "trimmed": trimmed_script != script, "sections": ["Roteiro completo"],
            "words": words, "estimated_minutes": round(words / (WORDS_PER_MINUTE * input.voice_rate), 1),
        }
        yield "progress", {"stage": "finalizing", "section": 1, "total": 1, "message": "Finalizando roteiro…"}
        yield "completed", result
        return

    section_count = max(4, round(input.minutes / 1.5))
    words_per_section = target_words // section_count
    yield "progress", {"stage": "planning", "section": 0, "total": section_count, "message": "Planejando as seções…"}
    outline = parse_json_object(await llm_chat(
        f"Planeje um vídeo de YouTube de {input.minutes} minutos no idioma {language} sobre: {input.subject}."
        + (f" Nicho do canal: {input.niche}." if input.niche else "")
        + (f" Requisitos personalizados: {requirements}." if requirements else "") + style
        + f' Responda apenas JSON: {{"title": título do vídeo, "sections": lista com {section_count} itens '
        '{"heading": título da seção, "points": lista de 2 a 4 pontos}}. '
        "A primeira seção é um gancho de abertura e a última uma conclusão com chamada para se inscrever.",
        json_mode=True,
        task="outline",
        max_tokens=long_script_tokens(section_count * 180, FAST_LONG_SECTION_MAX_TOKENS),
        fast_script=True,
    ))
    sections = [section for section in outline.get("sections") or [] if isinstance(section, dict) and section.get("heading")][:section_count]
    if not sections:
        raise HTTPException(status_code=502, detail="A IA não devolveu o roteiro em seções")
    parts: list[str] = []
    for index, section in enumerate(sections, start=1):
        yield "progress", {"stage": "writing", "section": index, "total": len(sections), "message": f"Escrevendo seção {index} de {len(sections)}…"}
        previous = f"\nFinal da seção anterior (continue a partir dele, sem repetir): {parts[-1][-600:]}" if parts else ""
        parts.append(clean_narration(await llm_chat(
            f"Escreva a narração da seção {index} de {len(sections)} de um vídeo de YouTube no idioma {language} sobre: {input.subject}.\n"
            f"Seção: {section['heading']}\nPontos: {'; '.join(map(str, section.get('points') or []))}\n"
            f"Cerca de {words_per_section} palavras. Somente o texto falado, em parágrafos corridos: sem títulos, marcadores, markdown, emojis nem indicações de cena."
            + (f" Requisitos personalizados: {requirements}." if requirements else "") + style + previous,
            task="script",
            max_tokens=long_script_tokens(words_per_section, FAST_LONG_SECTION_MAX_TOKENS),
            fast_script=True,
        )))
    script = "\n\n".join(part for part in parts if part)
    trimmed_script = trim_to_words(script, target_words)
    words = len(trimmed_script.split())
    result = {
        "title": str(outline.get("title") or input.subject), "script": trimmed_script, "trimmed": trimmed_script != script,
        "sections": [str(section["heading"]) for section in sections], "words": words, "estimated_minutes": round(words / (WORDS_PER_MINUTE * input.voice_rate), 1),
    }
    yield "progress", {"stage": "finalizing", "section": len(sections), "total": len(sections), "message": "Finalizando roteiro…"}
    yield "completed", result


async def generate_long_script(input: LongScriptInput) -> dict[str, Any]:
    async for event, data in long_script_events(input):
        if event == "completed":
            return data
    raise HTTPException(status_code=502, detail="A IA não concluiu o roteiro")


@app.post("/api/scripts/long")
async def long_script(input: LongScriptInput) -> dict[str, Any]:
    """Compatibility endpoint for clients that do not consume script progress."""
    return await generate_long_script(input)


@app.post("/api/scripts/long/stream")
async def long_script_stream(input: LongScriptInput, request: Request) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        try:
            async for event, data in long_script_events(input):
                if await request.is_disconnected():
                    return
                yield sse_message(event, json.dumps(data, ensure_ascii=False))
        except HTTPException as exc:
            yield sse_message("error", json.dumps({"code": exc.status_code, "message": str(exc.detail)}, ensure_ascii=False))
        except Exception:
            logging.exception("long script stream failed")
            yield sse_message("error", json.dumps({"code": 500, "message": "Não foi possível gerar o roteiro."}, ensure_ascii=False))
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def split_script(script: str, parts: int) -> list[str]:
    """Splits narration into `parts` contiguous, roughly-equal chunks by word count, preserving
    order and every word - used to line up one visual search term per script chunk."""
    words = script.split()
    if parts <= 1 or not words:
        return [script.strip()] if script.strip() else []
    parts = min(parts, len(words))
    chunk = -(-len(words) // parts)  # ceil division without importing math
    return [" ".join(words[i:i + chunk]) for i in range(0, len(words), chunk)]


def voice_words_per_minute(voice_name: str = "") -> int:
    # The connected en-US Jenny voice produced 160 spoken words in 56.4 s on a real render.
    return 170 if voice_name.casefold() == "en-us-jennyneural" else WORDS_PER_MINUTE


def narration_seconds(script: str, voice_rate: float = 1.0, voice_name: str = "") -> float:
    return len(script.split()) / (voice_words_per_minute(voice_name) / 60) / max(voice_rate, 0.1)


MAX_VISUAL_TERMS = 80  # ponytail: cap on Pexels searches per video; raise alongside the API's hourly quota


def visual_term_count(script: str, clip_seconds: float, voice_rate: float = 1.0) -> int:
    if clip_seconds <= 0:
        raise HTTPException(status_code=422, detail="A duração de cada clipe deve ser maior que zero.")
    slots = max(1, math.ceil(narration_seconds(script, voice_rate) / clip_seconds))
    if slots > MAX_VISUAL_TERMS:
        raise HTTPException(status_code=422, detail=(
            f"A duração escolhida exige {slots} buscas de filmagens, acima do limite de {MAX_VISUAL_TERMS}. "
            "Aumente a duração de cada clipe ou reduza a duração do vídeo."
        ))
    return slots


def _fill_visual_terms(terms: list[str], count: int) -> list[str]:
    """Turbo drops a term's candidates once its URLs are already claimed by an earlier term
    in the list, so duplicates silently lose their slot; dedupe, then pad or truncate to
    exactly `count` so every script chunk still gets a term of its own."""
    seen: set[str] = set()
    unique = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            unique.append(term)
    if not unique:
        unique = ["b-roll footage"]
    result = list(unique[:count])
    suffixes = ["close up", "wide shot", "detail shot"]
    index = 0
    while len(result) < count:
        result.append(f"{unique[index % len(unique)]} {suffixes[(index // len(unique)) % len(suffixes)]}")
        index += 1
    return result[:count]


async def visual_terms(subject: str, script: str, clip_seconds: float, voice_rate: float = 1.0, context: str = "") -> tuple[list[str], float]:
    """One stock-footage search term per script chunk, in narration order. Combined with Turbo's
    match_materials_to_script + sequential mode, this makes the footage follow what's being said
    instead of cycling a handful of subject-wide terms across the whole video."""
    slots = visual_term_count(script, clip_seconds, voice_rate)
    count = slots
    chunks = split_script(script, count) or [subject]
    numbered = "\n".join(f"{index + 1}. {chunk}" for index, chunk in enumerate(chunks))
    generated = await llm_json_object(
        f"Tema do vídeo: {subject}\nContexto editorial: {context[:5000]}\nTrechos numerados da narração, na ordem em que aparecem:\n{numbered}\n"
        "Para cada trecho, gere uma busca de banco de vídeos em INGLÊS (1 a 3 palavras) que mostre algo filmável e "
        "concreto do que é dito naquele trecho: uma cena, objeto, ação ou lugar. Nada abstrato, sem nomes próprios, "
        "sem texto na tela. Os termos devem ser diferentes entre si - não repita o tema geral em todos.\n"
        f'Responda apenas JSON: {{"terms": lista com exatamente {len(chunks)} strings, na mesma ordem dos trechos}}',
        label="as palavras-chave das filmagens",
    )
    raw_terms = [str(term).strip() for term in generated.get("terms") or [] if str(term).strip()]
    return _fill_visual_terms(raw_terms, len(chunks)), clip_seconds


class VisualTermsInput(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    script: str = Field(min_length=1, max_length=40000)
    clip_seconds: float = Field(default=5, ge=1, le=30)
    voice_rate: float = Field(default=1.0, ge=0.5, le=2.0)


@app.post("/api/scripts/terms")
async def script_terms(input: VisualTermsInput) -> dict[str, Any]:
    terms, clip_seconds = await visual_terms(input.subject, input.script, input.clip_seconds, input.voice_rate)
    return {"terms": terms, "clip_seconds": round(clip_seconds, 1), "cuts": len(terms)}


@app.post("/api/jobs/{job_id}/approve")
def approve_job(job_id: str) -> dict[str, Any]:
    with database() as db:
        job = job_row_or_404(db, job_id)
        if job["state"] != "completed":
            raise HTTPException(status_code=409, detail="Só vídeos concluídos podem ser aprovados")
        require_quality_approval(db, job)
        if not db.execute("SELECT 1 FROM artifacts WHERE job_id=? AND kind='video'", (job_id,)).fetchone():
            raise HTTPException(status_code=409, detail="O vídeo final não foi encontrado")
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        if not stored or normalize_youtube_metadata(json.loads(stored["value_json"]))[1]:
            raise HTTPException(status_code=409, detail="Defina título, descrição e tags válidos antes de aprovar")
        db.execute("UPDATE video_jobs SET state='approved', updated_at=? WHERE id=?", (now(), job_id))
        return row_as_job(job_row_or_404(db, job_id), db)


def require_quality_approval(db: sqlite3.Connection, job: sqlite3.Row) -> None:
    if not job["generation_item_id"]:
        return
    quality = db.execute("SELECT state FROM generation_quality WHERE item_id=? AND job_id=?", (job["generation_item_id"], job["id"])).fetchone()
    if not quality or quality["state"] != "approved":
        raise HTTPException(status_code=409, detail="A revisão final do vídeo ainda não aprovou este trabalho. Confira o relatório da geração.")


# youtube.readonly is what lets the callback find out which channel was picked on Google's consent screen.
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly", "https://www.googleapis.com/auth/yt-analytics.readonly"]
LANGUAGE_PATTERN = r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*"
UPLOAD_POST_BASE = "https://api.upload-post.com"
PLATFORM_LABELS = {"youtube": "YouTube Studio", "tiktok": "TikTok"}
# ponytail: in-memory OAuth state; a Gateway restart mid-consent just means clicking "Conectar" again.
oauth_flows: dict[str, tuple[Any, dict[str, Any], str]] = {}


class PublishRejected(Exception):
    """The platform answered 4xx: the upload definitely did not create a post, so a retry is safe."""


def youtube_client_file() -> Path:
    return DATA_DIR / "secrets" / "youtube_client_secret.json"


def youtube_token_file(channel_id: str) -> Path:
    return DATA_DIR / "secrets" / "youtube_tokens" / f"{channel_id}.json"


def clean_language(value: str) -> str:
    return value if re.fullmatch(LANGUAGE_PATTERN, value or "") else "pt-BR"


CHANNEL_PROFILE_FIELDS = (
    "voice_name", "voice_rate", "voice_volume", "subtitle_enabled", "subtitle_position",
    "font_size", "text_fore_color", "stroke_color", "stroke_width",
)
CHANNEL_PROFILE_COLORS = {"text_fore_color", "stroke_color"}
CHANNEL_PROFILE_POSITIONS = {"bottom", "center", "top"}
CHANNEL_VIDEO_PROFILE_FIELDS = {
    "short": (*CHANNEL_PROFILE_FIELDS, "paragraph_number", "video_clip_duration", "video_count", "video_concat_mode", "video_transition_mode", "video_clip_speed", "bgm_type", "bgm_volume"),
    "long": (*CHANNEL_PROFILE_FIELDS, "target_minutes", "video_clip_duration", "video_concat_mode", "video_transition_mode", "video_clip_speed", "bgm_type", "bgm_volume"),
}
VIDEO_TRANSITIONS = {"", "Shuffle", "FadeIn", "FadeOut", "SlideIn", "SlideOut", "ZoomIn", "ZoomOut"}
VIDEO_CONCAT_MODES = {"script", "random"}
VIDEO_CLIP_SPEEDS = {0.8, 1.0, 1.2, 1.5}
VIDEO_BGM_TYPES = {"", "random"}
VIDEO_BGM_VOLUMES = {0.1, 0.2, 0.4, 0.6}
VOICE_CHOICES = {
    "pt-BR": {"pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"},
    "en-US": {"en-US-JennyNeural", "en-US-GuyNeural"},
    "es-ES": {"es-ES-ElviraNeural", "es-ES-AlvaroNeural"},
    "de-DE": {"de-DE-KatjaNeural", "de-DE-ConradNeural"},
}


def default_voice_for_language(language: str) -> str:
    prefix = clean_language(language).split("-", 1)[0]
    return VOICE_BY_LANGUAGE.get(prefix, "pt-BR-FranciscaNeural")


def video_profile_from_settings(format: Literal["long", "short"], settings: dict[str, Any]) -> dict[str, Any]:
    """Keep the channel's format-specific voice, caption and render controls."""
    common = {
        "voice_name": settings.get("voice_name", "pt-BR-FranciscaNeural"),
        "voice_rate": settings.get("voice_rate", 1.0),
        "voice_volume": settings.get("voice_volume", 1.0),
        "subtitle_enabled": settings.get("subtitle_enabled", True),
        "subtitle_position": settings.get("subtitle_position", "bottom"),
        "font_size": settings.get("font_size", 56),
        "text_fore_color": settings.get("text_fore_color", "#FFFFFF"),
        "stroke_color": settings.get("stroke_color", "#000000"),
        "stroke_width": settings.get("stroke_width", 1.5),
        "video_clip_duration": settings.get("video_clip_duration", 6 if format == "long" else 4),
        "video_concat_mode": settings.get("video_concat_mode", "script"),
        "video_transition_mode": settings.get("video_transition_mode", ""),
        "video_clip_speed": settings.get("video_clip_speed", 1.0),
        "bgm_type": settings.get("bgm_type", ""),
        "bgm_volume": settings.get("bgm_volume", 0.2),
    }
    if format == "short":
        return {"paragraph_number": settings.get("paragraph_number", 5), "video_count": settings.get("video_count", 1), **common}
    return {"target_minutes": settings.get("target_minutes", 8), **common}


def default_channel_video_defaults(language: str = "pt-BR") -> dict[str, dict[str, Any]]:
    profiles = {format: video_profile_from_settings(format, generation_defaults(format)) for format in ("short", "long")}
    for profile in profiles.values():
        profile["voice_name"] = default_voice_for_language(language)
    return profiles


def channel_video_profile_fallback(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Copy the legacy shared channel profile to both formats for older API clients."""
    defaults = default_channel_video_defaults(str(profile.get("language") or "pt-BR"))
    media = {field: profile[field] for field in CHANNEL_PROFILE_FIELDS if field in profile}
    return {format: {**defaults[format], **media} for format in ("short", "long")}


def normalize_channel_video_defaults(values: Any, *, fallback: dict[str, Any] | None = None, language: str = "pt-BR") -> dict[str, dict[str, Any]]:
    """Validate voice, captions and render settings stored independently per format."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise HTTPException(status_code=422, detail="Os parâmetros de vídeo do canal devem ser um objeto.")
    base = fallback if fallback is not None else default_channel_video_defaults(language)
    result: dict[str, dict[str, Any]] = {}
    for format in ("short", "long"):
        raw = values.get(format, {})
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail=f"Os parâmetros de {format} devem ser um objeto.")
        allowed = set(CHANNEL_VIDEO_PROFILE_FIELDS[format])
        unknown = set(raw) - allowed
        if unknown:
            raise HTTPException(status_code=422, detail=f"Parâmetro de vídeo não suportado: {sorted(unknown)[0]}.")
        current = dict(base.get(format, {}))
        current.update(raw)
        media = normalize_channel_profile({"language": language, **current})
        media.pop("language")  # language remains channel-wide; only voice and captions vary by format.
        try:
            clip_duration = int(current["video_clip_duration"])
            clip_speed = float(current["video_clip_speed"])
            bgm_volume = float(current["bgm_volume"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Parâmetros de vídeo numéricos inválidos.") from None
        if not 1 <= clip_duration <= 30:
            raise HTTPException(status_code=422, detail="A duração de cada clipe deve estar entre 1 e 30 segundos.")
        if clip_speed not in VIDEO_CLIP_SPEEDS:
            raise HTTPException(status_code=422, detail="Velocidade de clipe inválida.")
        if bgm_volume not in VIDEO_BGM_VOLUMES:
            raise HTTPException(status_code=422, detail="Volume da música inválido.")
        concat_mode = str(current.get("video_concat_mode", ""))
        transition = str(current.get("video_transition_mode", ""))
        bgm_type = str(current.get("bgm_type", ""))
        if concat_mode not in VIDEO_CONCAT_MODES:
            raise HTTPException(status_code=422, detail="Modo de imagens inválido.")
        if transition not in VIDEO_TRANSITIONS:
            raise HTTPException(status_code=422, detail="Transição inválida.")
        if bgm_type not in VIDEO_BGM_TYPES:
            raise HTTPException(status_code=422, detail="Trilha sonora inválida.")
        profile = {
            "video_clip_duration": clip_duration,
            "video_concat_mode": concat_mode,
            "video_transition_mode": transition,
            "video_clip_speed": clip_speed,
            "bgm_type": bgm_type,
            "bgm_volume": bgm_volume,
        }
        if format == "short":
            try:
                paragraph_number, video_count = int(current["paragraph_number"]), int(current["video_count"])
            except (KeyError, TypeError, ValueError):
                raise HTTPException(status_code=422, detail="Duração ou quantidade de vídeos do Short inválida.") from None
            if paragraph_number not in {4, 5, 6} or not 1 <= video_count <= 5:
                raise HTTPException(status_code=422, detail="Shorts aceitam duração de 60, 75 ou 90 segundos e de 1 a 5 vídeos.")
            result[format] = {**media, "paragraph_number": paragraph_number, "video_count": video_count, **profile}
        else:
            try:
                target_minutes = int(current["target_minutes"])
            except (KeyError, TypeError, ValueError):
                raise HTTPException(status_code=422, detail="Duração do vídeo longo inválida.") from None
            if target_minutes not in {5, 8, 10, 15}:
                raise HTTPException(status_code=422, detail="Vídeos longos aceitam 5, 8, 10 ou 15 minutos.")
            result[format] = {**media, "target_minutes": target_minutes, **profile}
    return result


def channel_video_defaults(channel: sqlite3.Row | dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = dict(channel).get("video_defaults_json", "{}")
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        values = {}
    legacy = normalize_channel_profile(dict(channel))
    fallback = channel_video_profile_fallback(legacy)
    return normalize_channel_video_defaults(values, fallback=fallback, language=legacy["language"])


def normalize_channel_profile(values: dict[str, Any], *, require_all: bool = True) -> dict[str, Any]:
    """Validate one render profile shared by channel forms, direct API calls and OAuth setup."""
    language = clean_language(str(values.get("language", "pt-BR")))
    voice_name = str(values.get("voice_name") or default_voice_for_language(language))
    allowed_voices = VOICE_CHOICES.get(language)
    if not allowed_voices or voice_name not in allowed_voices:
        raise HTTPException(status_code=422, detail="A voz escolhida não é compatível com o idioma da narração.")
    profile: dict[str, Any] = {"language": language, "voice_name": voice_name}
    numeric_ranges = {"voice_rate": (0.5, 2.0), "voice_volume": (0.0, 2.0), "stroke_width": (0.0, 10.0)}
    for field, (minimum, maximum) in numeric_ranges.items():
        try:
            number = float(values.get(field, 1.0 if field != "stroke_width" else 1.5))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{field} deve ser numérico.") from None
        if not minimum <= number <= maximum:
            raise HTTPException(status_code=422, detail=f"{field} está fora do limite permitido.")
        profile[field] = number
    try:
        font_size = int(values.get("font_size", 56))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="font_size deve ser inteiro.") from None
    if not 12 <= font_size <= 120:
        raise HTTPException(status_code=422, detail="font_size está fora do limite permitido.")
    profile["font_size"] = font_size
    profile["subtitle_enabled"] = bool(values.get("subtitle_enabled", True))
    profile["subtitle_position"] = str(values.get("subtitle_position", "bottom"))
    if profile["subtitle_position"] not in CHANNEL_PROFILE_POSITIONS:
        raise HTTPException(status_code=422, detail="Posição de legenda inválida.")
    for field in CHANNEL_PROFILE_COLORS:
        color = str(values.get(field, "#FFFFFF" if field == "text_fore_color" else "#000000")).upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", color):
            raise HTTPException(status_code=422, detail=f"{field} deve usar a cor hexadecimal #RRGGBB.")
        profile[field] = color
    return profile


def channel_generation_settings(channel: sqlite3.Row | dict[str, Any], format: Literal["long", "short"]) -> dict[str, Any]:
    values = dict(channel)
    values.update(channel_video_defaults(channel)[format])
    profile = normalize_channel_profile(values)
    return {"video_language": profile.pop("language"), **profile, **channel_video_defaults(channel)[format]}


def conflicting_channel_profile_fields(channels: list[sqlite3.Row], format: Literal["long", "short"]) -> list[str]:
    if len(channels) < 2:
        return []
    profiles = [channel_generation_settings(channel, format) for channel in channels]
    fields = ("video_language", *CHANNEL_PROFILE_FIELDS, *CHANNEL_VIDEO_PROFILE_FIELDS[format])
    return [field for field in fields if any(profile[field] != profiles[0][field] for profile in profiles[1:])]


def job_language(db: sqlite3.Connection, job: sqlite3.Row) -> str:
    """Metadata language follows the channel the video is for, falling back to the project's.

    ponytail: video_jobs.channel_id is only ever set by the pre-multi-channel migration
    backfill (never by create_job), so the YouTube target now comes from publications
    (one row per platform per job) instead of that stale column.
    """
    row = db.execute(
        "SELECT channels.language FROM publications JOIN channels ON channels.id = publications.channel_id "
        "WHERE publications.job_id=? AND publications.platform='youtube'",
        (job["id"],),
    ).fetchone()
    row = row or db.execute("SELECT language FROM projects WHERE id=?", (job["project_id"],)).fetchone()
    return row["language"] if row else "pt-BR"


def row_as_channel(row: sqlite3.Row) -> dict[str, Any]:
    result = {key: row[key] for key in ("id", "platform", "name", "handle", "external_id", "status", "niche", "language", "system_prompt", "publish_privacy", *CHANNEL_PROFILE_FIELDS, "created_at", "updated_at")}
    result["subtitle_enabled"] = bool(result["subtitle_enabled"])
    result["video_defaults"] = channel_video_defaults(row)
    result["visual_guides"] = json.loads(row["visual_guides_json"] or "{}")
    result["stats"] = json.loads(row["stats_json"])
    return result


def normalize_visual_guides(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) - {"short", "long"}:
        raise HTTPException(status_code=422, detail="Envie apenas os guias visuais short e long.")
    result = {}
    for format in ("short", "long"):
        guide = value.get(format, "")
        if not isinstance(guide, str) or len(guide) > 2000:
            raise HTTPException(status_code=422, detail="Cada guia visual deve ter até 2.000 caracteres.")
        result[format] = guide.strip()
    return result


def channel_row_or_404(db: sqlite3.Connection, channel_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM channels WHERE id=? AND deleted_at IS NULL", (channel_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Canal não encontrado")
    return row


def update_channel(channel_id: str, **fields: Any) -> None:
    assignments = ", ".join(f"{key}=?" for key in fields)  # keys come from this module, never from a request
    with database() as db:
        db.execute(f"UPDATE channels SET {assignments}, updated_at=? WHERE id=?", (*fields.values(), now(), channel_id))


async def upload_post_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    key = config_value("upload_post_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure a chave Upload-Post em Configurações para usar canais TikTok.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            response = await send_with_retry(client, method, f"{UPLOAD_POST_BASE}{path}", headers={"Authorization": f"Apikey {key}"}, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Upload-Post indisponível: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Upload-Post respondeu {response.status_code}: {response.text[:300]}")
    return response.json()


@app.get("/api/channels")
def list_channels() -> list[dict[str, Any]]:
    with database() as db:
        return [row_as_channel(row) for row in db.execute("SELECT * FROM channels WHERE deleted_at IS NULL ORDER BY created_at DESC")]


class ChannelPatch(BaseModel):
    niche: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=20)
    system_prompt: str | None = Field(default=None, max_length=4000)
    publish_privacy: Literal["private", "public"] | None = None
    voice_name: str | None = Field(default=None, max_length=120)
    voice_rate: float | None = Field(default=None)
    voice_volume: float | None = Field(default=None)
    subtitle_enabled: bool | None = None
    subtitle_position: str | None = Field(default=None, max_length=20)
    font_size: int | None = None
    text_fore_color: str | None = Field(default=None, max_length=7)
    stroke_color: str | None = Field(default=None, max_length=7)
    stroke_width: float | None = Field(default=None)
    video_defaults: dict[str, Any] | None = None
    visual_guides: dict[str, str] | None = None


class ChannelConnect(BaseModel):
    niche: str = Field(default="", max_length=200)
    language: str = Field(default="pt-BR", max_length=20)
    system_prompt: str = Field(default="", max_length=4000)
    publish_privacy: Literal["private", "public"] = "private"
    voice_name: str = Field(default="pt-BR-FranciscaNeural", max_length=120)
    voice_rate: float = 1.0
    voice_volume: float = 1.0
    subtitle_enabled: bool = True
    subtitle_position: str = "bottom"
    font_size: int = 56
    text_fore_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: float = 1.5
    video_defaults: dict[str, Any] = Field(default_factory=dict)
    visual_guides: dict[str, str] = Field(default_factory=dict)


@app.patch("/api/channels/{channel_id}")
def patch_channel(channel_id: str, input: ChannelPatch) -> dict[str, Any]:
    with database() as db:
        channel = channel_row_or_404(db, channel_id)
    fields = input.model_dump(exclude_none=True)
    if "visual_guides" in fields:
        fields["visual_guides_json"] = json.dumps(normalize_visual_guides(fields.pop("visual_guides")), ensure_ascii=False)
    profile_changed = set(fields).intersection({"language", *CHANNEL_PROFILE_FIELDS})
    if profile_changed:
        combined = dict(channel)
        combined.update(fields)
        # Changing only the language chooses a compatible default voice; an explicitly
        # supplied incompatible voice remains a validation error.
        if "language" in fields and "voice_name" not in fields:
            combined["voice_name"] = default_voice_for_language(str(fields["language"]))
        fields.update(normalize_channel_profile(combined))
    raw_video_defaults = fields.pop("video_defaults", None)
    language = str(fields.get("language", channel["language"]))
    if profile_changed or raw_video_defaults is not None:
        fallback = channel_video_defaults(channel)
        if raw_video_defaults is None:
            raw_video_defaults = {"short": {}, "long": {}}
        if profile_changed:
            # Keep legacy API updates functional by applying shared fields to both formats.
            for format in ("short", "long"):
                for key in profile_changed.intersection(CHANNEL_PROFILE_FIELDS):
                    fallback[format][key] = fields[key]
                if "language" in profile_changed and "voice_name" not in profile_changed:
                    fallback[format]["voice_name"] = default_voice_for_language(language)
        fields["video_defaults_json"] = json.dumps(
            normalize_channel_video_defaults(raw_video_defaults, fallback=fallback, language=language)
        )
    if "system_prompt" in fields:
        fields["system_prompt"] = fields["system_prompt"].strip()
    if fields:
        update_channel(channel_id, **fields)
    with database() as db:
        return row_as_channel(channel_row_or_404(db, channel_id))


@app.delete("/api/channels/{channel_id}", status_code=204, response_model=None)
async def delete_channel(channel_id: str) -> None:
    with database() as db:
        channel = channel_row_or_404(db, channel_id)
        # Clearing external_id frees UNIQUE(platform, external_id), so the same account can be connected again later.
        db.execute("UPDATE channels SET deleted_at=?, external_id=NULL, updated_at=? WHERE id=?", (now(), now(), channel_id))
    youtube_token_file(channel_id).unlink(missing_ok=True)
    if channel["platform"] == "tiktok" and channel["external_id"]:
        try:
            await upload_post_request("DELETE", "/api/uploadposts/users", json={"username": channel["external_id"]})
        except HTTPException:
            pass  # the Cockpit side is already gone; an orphan Upload-Post profile only costs a slot


@app.get("/api/channels/youtube/connect")
def youtube_connect(
    request: Request, niche: str = "", language: str = "pt-BR", system_prompt: str = "",
    voice_name: str = "pt-BR-FranciscaNeural", voice_rate: float = 1.0, voice_volume: float = 1.0,
    subtitle_enabled: bool = True, subtitle_position: str = "bottom", font_size: int = 56,
    text_fore_color: str = "#FFFFFF", stroke_color: str = "#000000", stroke_width: float = 1.5,
    publish_privacy: Literal["private", "public"] = "private", video_defaults: str = "", visual_guides: str = "",
) -> Any:
    from google_auth_oauthlib.flow import Flow

    session = session_from_request(request)
    if not session:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada.")
    if not youtube_client_file().is_file():
        return HTMLResponse("<p>Coloque o OAuth client do Google Cloud em <code>runtime/secrets/youtube_client_secret.json</code> e tente de novo.</p>", status_code=409)
    profile = normalize_channel_profile({
        "language": language, "voice_name": voice_name, "voice_rate": voice_rate, "voice_volume": voice_volume,
        "subtitle_enabled": subtitle_enabled, "subtitle_position": subtitle_position, "font_size": font_size,
        "text_fore_color": text_fore_color, "stroke_color": stroke_color, "stroke_width": stroke_width,
    })
    try:
        raw_video_defaults = json.loads(video_defaults) if video_defaults else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Os parâmetros de vídeo do canal são inválidos.") from None
    video_profile = normalize_channel_video_defaults(raw_video_defaults, fallback=channel_video_profile_fallback(profile), language=profile["language"])
    try:
        guides = normalize_visual_guides(json.loads(visual_guides) if visual_guides else {})
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Os guias visuais do canal são inválidos.") from None
    flow = Flow.from_client_secrets_file(str(youtube_client_file()), scopes=YOUTUBE_SCOPES, redirect_uri=str(request.url_for("youtube_auth_callback")))
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    oauth_flows[state] = (
        flow,
        {"niche": niche[:200], "system_prompt": system_prompt.strip()[:4000], "publish_privacy": publish_privacy, "video_defaults": video_profile, "visual_guides": guides, **profile},
        session["token_hash"],
    )
    return RedirectResponse(url)


def youtube_channel_info(credentials: Any) -> dict[str, Any] | None:
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    items = youtube.channels().list(part="snippet,statistics", mine=True).execute().get("items") or []
    if not items:
        return None
    snippet, stats = items[0]["snippet"], items[0].get("statistics", {})
    return {
        "external_id": items[0]["id"], "name": snippet.get("title") or "Canal do YouTube", "handle": snippet.get("customUrl") or "",
        "stats": {key: int(stats.get(source) or 0) for key, source in (("followers", "subscriberCount"), ("videos", "videoCount"), ("views", "viewCount"))},
    }


def save_youtube_channel(info: dict[str, Any], options: dict[str, Any]) -> str:
    """Upserts by YouTube channel id, so reconnecting a channel refreshes it (keeping its niche
    and language) instead of creating a duplicate."""
    with database() as db:
        existing = db.execute("SELECT id FROM channels WHERE platform='youtube' AND external_id=? AND deleted_at IS NULL", (info["external_id"],)).fetchone()
        if existing:
            db.execute(
                "UPDATE channels SET name=?, handle=?, status='connected', stats_json=?, updated_at=? WHERE id=?",
                (info["name"], info["handle"], json.dumps(info["stats"]), now(), existing["id"]),
            )
            return existing["id"]
        profile = normalize_channel_profile(options)
        video_defaults = normalize_channel_video_defaults(options.get("video_defaults", {}), fallback=channel_video_profile_fallback(profile), language=profile["language"])
        channel_id = str(uuid4())
        db.execute(
            "INSERT INTO channels(id, platform, name, handle, external_id, status, niche, language, system_prompt, publish_privacy, voice_name, voice_rate, voice_volume, subtitle_enabled, subtitle_position, font_size, text_fore_color, stroke_color, stroke_width, video_defaults_json, visual_guides_json, stats_json, created_at, updated_at) VALUES (?, 'youtube', ?, ?, ?, 'connected', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (channel_id, info["name"], info["handle"], info["external_id"], options.get("niche", ""), profile["language"], options.get("system_prompt", ""), options.get("publish_privacy", "private"), profile["voice_name"], profile["voice_rate"], profile["voice_volume"], int(profile["subtitle_enabled"]), profile["subtitle_position"], profile["font_size"], profile["text_fore_color"], profile["stroke_color"], profile["stroke_width"], json.dumps(video_defaults), json.dumps(normalize_visual_guides(options.get("visual_guides", {}))), json.dumps(info["stats"]), now(), now()),
        )
        return channel_id


@app.get("/api/youtube/auth/callback", name="youtube_auth_callback")
def youtube_auth_callback(request: Request, state: str = "", error: str = "") -> HTMLResponse:
    pending = oauth_flows.get(state)
    session = session_from_request(request)
    if error or not pending or not session or not secrets.compare_digest(pending[2], session["token_hash"]):
        oauth_flows.pop(state, None)
        return HTMLResponse(f"<p>Autorização do YouTube não concluída: {html.escape(error or 'sessão expirada, tente de novo')}.</p>", status_code=400)
    oauth_flows.pop(state, None)
    flow, options, _ = pending
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # the redirect is plain-http loopback
    flow.fetch_token(authorization_response=str(request.url))
    info = youtube_channel_info(flow.credentials)
    if not info:
        return HTMLResponse("<p>Esta conta Google não tem um canal do YouTube. Crie o canal e conecte de novo.</p>", status_code=400)
    channel_id = save_youtube_channel(info, options)
    token_file = youtube_token_file(channel_id)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(flow.credentials.to_json(), encoding="utf-8")
    return HTMLResponse(f"<p>Canal <b>{html.escape(info['name'])}</b> conectado. Pode fechar esta aba e voltar ao Cockpit.</p>")


def youtube_credentials(channel_id: str) -> Any:
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials

    token_file = youtube_token_file(channel_id)
    # The scopes granted at connect time, not YOUTUBE_SCOPES: refreshing with a scope the token never got
    # (e.g. Analytics, added later) fails with invalid_scope and would wrongly mark the channel expired.
    credentials = Credentials.from_authorized_user_file(str(token_file))
    if not credentials.valid:
        try:
            credentials.refresh(GoogleRequest())
        except RefreshError as exc:
            update_channel(channel_id, status="expired")
            raise RuntimeError("O acesso ao canal expirou ou foi revogado. Reconecte em Canais.") from exc
        token_file.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


async def upload_post_connect_link(username: str) -> str:
    link = await upload_post_request("POST", "/api/uploadposts/users/generate-jwt", json={"username": username, "platforms": ["tiktok"]})
    if not link.get("access_url"):
        raise HTTPException(status_code=502, detail="O Upload-Post não devolveu o link de conexão")
    return link["access_url"]


@app.post("/api/channels/tiktok/connect", status_code=201)
async def tiktok_connect(input: ChannelConnect) -> dict[str, Any]:
    """TikTok goes through one Upload-Post profile per Cockpit channel; the user links the
    TikTok account on Upload-Post's own page (their TikTok app is audited, ours would need
    a TikTok review plus an HTTPS redirect). The channel stays "pending" until /refresh sees it."""
    visual_guides = normalize_visual_guides(input.visual_guides)
    username = f"mp-{uuid4().hex[:16]}"
    await upload_post_request("POST", "/api/uploadposts/users", json={"username": username})
    access_url = await upload_post_connect_link(username)
    channel_id = str(uuid4())
    profile = normalize_channel_profile(input.model_dump())
    video_defaults = normalize_channel_video_defaults(input.video_defaults, fallback=channel_video_profile_fallback(profile), language=profile["language"])
    with database() as db:
        db.execute(
            "INSERT INTO channels(id, platform, name, handle, external_id, status, niche, language, system_prompt, publish_privacy, voice_name, voice_rate, voice_volume, subtitle_enabled, subtitle_position, font_size, text_fore_color, stroke_color, stroke_width, video_defaults_json, visual_guides_json, created_at, updated_at) VALUES (?, 'tiktok', 'TikTok (aguardando login)', '', ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (channel_id, username, input.niche.strip(), profile["language"], input.system_prompt.strip(), input.publish_privacy, profile["voice_name"], profile["voice_rate"], profile["voice_volume"], int(profile["subtitle_enabled"]), profile["subtitle_position"], profile["font_size"], profile["text_fore_color"], profile["stroke_color"], profile["stroke_width"], json.dumps(video_defaults), json.dumps(visual_guides), now(), now()),
        )
        channel = row_as_channel(channel_row_or_404(db, channel_id))
    return {"channel": channel, "access_url": access_url}


@app.post("/api/channels/{channel_id}/tiktok-link")
async def tiktok_reconnect_link(channel_id: str) -> dict[str, str]:
    with database() as db:
        channel = channel_row_or_404(db, channel_id)
    if channel["platform"] != "tiktok":
        raise HTTPException(status_code=409, detail="Este canal não é do TikTok")
    return {"access_url": await upload_post_connect_link(channel["external_id"])}


def tiktok_account(payload: dict[str, Any]) -> dict[str, Any] | None:
    profile = payload.get("profile") or payload
    account = (profile.get("social_accounts") or {}).get("tiktok")
    return account if isinstance(account, dict) and account else None


@app.post("/api/channels/{channel_id}/refresh")
async def refresh_channel(channel_id: str) -> dict[str, Any]:
    with database() as db:
        channel = channel_row_or_404(db, channel_id)
    if channel["platform"] == "youtube":
        if not youtube_token_file(channel_id).is_file():
            update_channel(channel_id, status="expired")
        else:
            try:
                info = await asyncio.to_thread(lambda: youtube_channel_info(youtube_credentials(channel_id)))
            except RuntimeError as exc:  # revoked token; youtube_credentials already marked the channel expired
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Não foi possível consultar o YouTube: {exc}") from exc
            if info:
                update_channel(channel_id, name=info["name"], handle=info["handle"], stats_json=json.dumps(info["stats"]), status="connected")
    else:
        account = tiktok_account(await upload_post_request("GET", f"/api/uploadposts/users/{channel['external_id']}"))
        if account:
            name = account.get("display_name") or account.get("handle") or "TikTok"
            update_channel(channel_id, name=name, handle=account.get("handle") or "", status="expired" if account.get("reauth_required") else "connected")
        elif channel["status"] == "connected":
            update_channel(channel_id, status="expired")
    with database() as db:
        return row_as_channel(channel_row_or_404(db, channel_id))


def youtube_description(metadata: dict[str, Any]) -> str:
    description = metadata["description"]
    if metadata.get("format") == "short" and "#shorts" not in description.lower():
        description = f"{description}\n\n#Shorts".strip()
    return description[:5000]


def youtube_upload(credentials: Any, video_path: Path, metadata: dict[str, Any], publish_at: datetime | None = None, publish_privacy: str = "private") -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    # YouTube's language list uses "pt" for Brazilian Portuguese, not "pt-BR".
    language = metadata["language"].split("-")[0]
    body = {
        "snippet": {
            "title": metadata["title"], "description": youtube_description(metadata), "tags": metadata["tags"],
            "categoryId": metadata["category_id"], "defaultLanguage": language, "defaultAudioLanguage": language,
        },
        "status": {"privacyStatus": "private" if publish_at else publish_privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True},
    }
    if publish_at:
        body["status"]["publishAt"] = as_utc(publish_at).isoformat().replace("+00:00", "Z")
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    upload = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    try:
        response = None
        while response is None:
            _, response = upload.next_chunk(num_retries=3)
    except HttpError as exc:
        if exc.resp.status < 500:
            raise PublishRejected(str(exc)) from exc
        raise
    return response["id"]


async def download_turbo_file(remote_path: str, target: Path) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=None)) as client:
        async with client.stream("GET", f"{TURBO_BASE_URL}/api/v1/download/{remote_path}", headers=turbo_headers()) as response:
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail="O Turbo não encontrou o artefato")
            with target.open("wb") as file:
                async for chunk in response.aiter_bytes():
                    file.write(chunk)


async def tiktok_upload(username: str, video_path: Path, metadata: dict[str, Any], publish_privacy: str = "private") -> tuple[str, str]:
    """Returns (external_id, state): Upload-Post normally answers async with a request_id
    ("processing", reconciled later); a synchronous answer is already the final post."""
    hashtags = " ".join("#" + re.sub(r"\s+", "", tag) for tag in metadata["tags"])
    caption = "\n\n".join(part for part in (metadata["title"], metadata["description"], hashtags) if part)[:2200]
    privacy_level = "PUBLIC_TO_EVERYONE" if publish_privacy == "public" else "SELF_ONLY"
    form = {"user": username, "platform[]": "tiktok", "title": caption, "privacy_level": privacy_level, "is_aigc": "true", "async_upload": "true"}
    # ponytail: whole file in memory (Shorts are tens of MB); stream it if long videos start pushing gateway RAM.
    files = {"video": ("video.mp4", video_path.read_bytes(), "video/mp4")}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=300)) as client:
        response = await client.post(f"{UPLOAD_POST_BASE}/api/upload", headers={"Authorization": f"Apikey {config_value('upload_post_api_key')}"}, data=form, files=files)
    if 400 <= response.status_code < 500:
        raise PublishRejected(f"Upload-Post recusou o envio ({response.status_code}): {response.text[:300]}")
    response.raise_for_status()
    body = response.json()
    if body.get("request_id"):
        return body["request_id"], "processing"
    result = (body.get("results") or {}).get("tiktok") or {}
    if not (body.get("success") and result.get("success")):
        raise PublishRejected(result.get("error") or body.get("message") or "O Upload-Post não aceitou o envio")
    return result.get("url") or result.get("post_id") or "", "published"


def map_upload_post_status(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Only the top-level status is final: a per-platform "success": false can still be retried by Upload-Post."""
    status = str(payload.get("status", "")).lower()
    result = next((item for item in payload.get("results") or [] if item.get("platform") == "tiktok"), {})
    if status == "completed":
        return "published", result.get("post_url") or result.get("post_id"), None
    if status in {"failed", "not_found"}:
        return "failed", None, result.get("message") or payload.get("message") or "Falha ao publicar no TikTok"
    return "processing", None, None


async def reconcile_publications(job_id: str) -> None:
    with database() as db:
        pending = db.execute("SELECT external_id FROM publications WHERE job_id=? AND state='processing'", (job_id,)).fetchone()
    if not pending:
        return
    try:
        payload = await upload_post_request("GET", "/api/uploadposts/status", params={"request_id": pending["external_id"]})
    except HTTPException:
        return  # stays "processing"; the next job read tries again
    state, external_id, error = map_upload_post_status(payload)
    if state == "processing":
        return
    with database() as db:
        db.execute(
            "UPDATE publications SET state=?, external_id=COALESCE(?, external_id), error=?, updated_at=? WHERE job_id=? AND state='processing'",
            (state, external_id, error, now(), job_id),
        )


class PublishInput(BaseModel):
    channel_ids: list[str] | None = Field(default=None, max_length=5)  # None: the job's pending/failed targets
    confirm_not_uploaded: bool = False
    publish_at: datetime | None = None  # future: validate now, upload when the agent loop reaches it


async def publish_to_channel(job_id: str, channel: sqlite3.Row, video: Any, metadata: dict[str, Any], confirm_not_uploaded: bool, auto_publish_at: datetime | None = None) -> None:
    """`video` is an async callable returning the local MP4, so the download happens only after a claim succeeds."""
    platform, label = channel["platform"], PLATFORM_LABELS[channel["platform"]]
    # A "reconciliation_required" upload may already be live; only the operator can say it isn't.
    blocked = ("uploading", "processing", "published") + (() if confirm_not_uploaded else ("reconciliation_required",))
    with database() as db:
        # Atomic claim, so a double click or a second tab can't start a parallel upload.
        claimed = db.execute(
            "INSERT INTO publications(id, job_id, platform, channel_id, state, attempts, auto_publish, created_at, updated_at) VALUES (?, ?, ?, ?, 'uploading', 1, ?, ?, ?) "
            "ON CONFLICT(job_id, platform) DO UPDATE SET channel_id=excluded.channel_id, state='uploading', attempts=publications.attempts+1, "
            "auto_publish=excluded.auto_publish, scheduled_at=CASE WHEN excluded.auto_publish=0 THEN NULL ELSE publications.scheduled_at END, error=NULL, updated_at=excluded.updated_at "
            f"WHERE publications.state NOT IN ({','.join('?' * len(blocked))})",
            (str(uuid4()), job_id, platform, channel["id"], int(auto_publish_at is not None), now(), now(), *blocked),
        ).rowcount
    if not claimed:
        raise HTTPException(status_code=409, detail=f"{channel['name']}: já está sendo enviado, já foi publicado ou tem resultado incerto. Confira no {label} antes de reenviar.")

    def finish(state: str, external_id: str | None = None, error: str | None = None) -> None:
        with database() as db:
            db.execute(
                "UPDATE publications SET state=?, external_id=?, error=?, updated_at=? WHERE job_id=? AND platform=?",
                (state, external_id, error, now(), job_id, platform),
            )

    try:
        video_path = await video()
        credentials = await asyncio.to_thread(youtube_credentials, channel["id"]) if platform == "youtube" else None
        if auto_publish_at:
            with database() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM operators WHERE channel_id=?", (channel["id"],)).fetchone()
                if not current or not current["enabled"] or not current["auto_publish"] or current["paused_reason"]:
                    raise HTTPException(status_code=409, detail="A publicação automática foi desativada ou pausada.")
                if auto_publish_at <= datetime.now(UTC) + timedelta(minutes=5):
                    auto_publish_at = next_publish_slot(current, db)
                    db.execute("UPDATE publications SET scheduled_at=?, updated_at=? WHERE job_id=? AND channel_id=? AND state='uploading'",
                               (auto_publish_at.isoformat(), now(), job_id, channel["id"]))
    except BaseException as exc:  # nothing has reached the platform yet, so a plain retry stays safe
        message = str(getattr(exc, "detail", exc))[:500] or "Envio cancelado"
        finish("failed", error=message)
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(status_code=502, detail=f"{channel['name']}: não foi possível preparar o envio: {message}") from exc
    try:
        # ponytail: uploads run inside the request, one channel after another; move to a background task if long videos hit browser/proxy timeouts.
        if platform == "youtube":
            external_id, state = await asyncio.to_thread(youtube_upload, credentials, video_path, metadata, auto_publish_at, "private" if auto_publish_at else channel["publish_privacy"]), "platform_scheduled" if auto_publish_at else "published"
        else:
            external_id, state = await tiktok_upload(channel["external_id"], video_path, metadata, channel["publish_privacy"])
    except PublishRejected as exc:
        finish("failed", error=str(exc)[:500])
        raise HTTPException(status_code=502, detail=f"{channel['name']}: a plataforma recusou o envio: {exc}") from exc
    except BaseException as exc:
        finish("reconciliation_required", error=str(exc)[:500] or "Envio interrompido")
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(status_code=502, detail=f"{channel['name']}: o envio foi interrompido e pode ter chegado ao canal. Confira no {label} antes de repetir.") from exc
    finish(state, external_id=external_id)


@app.post("/api/jobs/{job_id}/publish")
async def publish_job(job_id: str, input: PublishInput | None = None) -> dict[str, Any]:
    input = input or PublishInput()
    if input.publish_at and as_utc(input.publish_at) <= datetime.now(UTC):
        raise HTTPException(status_code=422, detail="Escolha uma data de agendamento no futuro.")
    with database() as db:
        job = job_row_or_404(db, job_id)
        if job["state"] != "approved":
            raise HTTPException(status_code=409, detail="Aprove o vídeo antes de publicar.")
        require_quality_approval(db, job)
        artifact = db.execute("SELECT * FROM artifacts WHERE job_id=? AND kind='video' ORDER BY created_at LIMIT 1", (job_id,)).fetchone()
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        if not artifact or not stored:
            raise HTTPException(status_code=409, detail="Vídeo final ou metadados ausentes.")
        channel_ids = input.channel_ids or [row["channel_id"] for row in db.execute(
            "SELECT channel_id FROM publications WHERE job_id=? AND channel_id IS NOT NULL AND state IN ('pending', 'failed', 'reconciliation_required')", (job_id,)
        )]
        if not channel_ids:
            raise HTTPException(status_code=409, detail="Escolha o canal onde o vídeo será publicado.")
        channels = [channel_row_or_404(db, channel_id) for channel_id in dict.fromkeys(channel_ids)]
    # Every target is checked before the first upload, so a bad pick doesn't leave the video half-published.
    validate_publish_channels(channels, job["format"])
    for channel in channels:
        if channel["platform"] == "youtube" and not youtube_token_file(channel["id"]).is_file():
            raise HTTPException(status_code=409, detail=f"O canal {channel['name']} perdeu a autorização. Reconecte em Canais.")
    if input.publish_at:
        return schedule_publications(job_id, channels, as_utc(input.publish_at))
    metadata ={**normalize_youtube_metadata(json.loads(stored["value_json"]))[0], "format": job["format"]}
    failures: list[tuple[str, HTTPException]] = []
    with tempfile.TemporaryDirectory() as folder:
        video_path = Path(folder) / "video.mp4"
        downloaded = False

        async def video() -> Path:  # downloaded once, on the first channel that actually claims an upload
            nonlocal downloaded
            if not downloaded:  # a failed download leaves this False, so the next channel retries it
                await download_turbo_file(artifact["remote_path"], video_path)
                downloaded = True
            return video_path

        for channel in channels:
            try:
                await publish_to_channel(job_id, channel, video, metadata, input.confirm_not_uploaded)
            except HTTPException as exc:  # one channel failing must not stop the others
                failures.append((channel["id"], exc))
    if len(failures) == len(channels):
        raise failures[0][1] if len(failures) == 1 else HTTPException(status_code=502, detail=" | ".join(str(exc.detail) for _, exc in failures))
    with database() as db:
        job_view = row_as_job(job_row_or_404(db, job_id), db)
    return {**job_view, "errors": [{"channel_id": channel_id, "detail": exc.detail} for channel_id, exc in failures]}


def schedule_publications(job_id: str, channels: list[sqlite3.Row], publish_at: datetime) -> dict[str, Any]:
    with database() as db:
        for channel in channels:
            claimed = db.execute(
                "INSERT INTO publications(id, job_id, platform, channel_id, state, scheduled_at, created_at, updated_at) VALUES (?, ?, ?, ?, 'scheduled', ?, ?, ?) "
                "ON CONFLICT(job_id, platform) DO UPDATE SET channel_id=excluded.channel_id, state='scheduled', scheduled_at=excluded.scheduled_at, error=NULL, updated_at=excluded.updated_at "
                "WHERE publications.state IN ('pending', 'failed', 'scheduled')",
                (str(uuid4()), job_id, channel["platform"], channel["id"], publish_at.isoformat(), now(), now()),
            ).rowcount
            if not claimed:  # the database() block rolls back the channels already scheduled
                raise HTTPException(status_code=409, detail=f"{channel['name']}: já foi enviado ou tem resultado incerto, não dá para agendar.")
        return {**row_as_job(job_row_or_404(db, job_id), db), "errors": []}


@app.delete("/api/jobs/{job_id}/schedule")
def cancel_schedule(job_id: str, channel_id: str | None = None) -> dict[str, Any]:
    with database() as db:
        job = job_row_or_404(db, job_id)
        db.execute(
            "UPDATE publications SET state='pending', scheduled_at=NULL, updated_at=? WHERE job_id=? AND state='scheduled' AND (? IS NULL OR channel_id=?)",
            (now(), job_id, channel_id, channel_id),
        )
        return row_as_job(job, db)


# ── Agentes ──────────────────────────────────────────────────────────────────
logger = logging.getLogger("cockpit.agents")
AGENT_LOOP_SECONDS = 60
PROJECT_NAMES = {"long": "Vídeos longos", "short": "Shorts"}  # the same projects the Gerador and Shorts screens use
REGION_BY_LANGUAGE = {"pt": "BR", "en": "US", "es": "ES", "de": "DE"}
VOICE_BY_LANGUAGE = {"pt": "pt-BR-FranciscaNeural", "en": "en-US-JennyNeural", "es": "es-ES-ElviraNeural", "de": "de-DE-KatjaNeural"}
OPERATOR_MINUTES = 8  # calibration knob: length of the long videos the operator writes
ANALYTICS_METRICS = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,subscribersGained"


def youtube_trends(language: str) -> list[str]:
    """Titles trending on YouTube in the channel's region. Only a hint for the strategist, so any failure means no trends."""
    from googleapiclient.discovery import build

    with database() as db:
        row = db.execute("SELECT id FROM channels WHERE platform='youtube' AND status='connected' AND deleted_at IS NULL LIMIT 1").fetchone()
    if not row:
        return []
    try:
        youtube = build("youtube", "v3", credentials=youtube_credentials(row["id"]), cache_discovery=False)
        region = REGION_BY_LANGUAGE.get(language.split("-")[0], "US")
        items = youtube.videos().list(part="snippet", chart="mostPopular", regionCode=region, maxResults=25).execute().get("items") or []
    except Exception as exc:
        logger.warning("Tendências do YouTube indisponíveis: %s", exc)
        return []
    return [item["snippet"]["title"] for item in items if (item.get("snippet") or {}).get("title")]


async def suggest_topics(channel: sqlite3.Row, format: str, count: int) -> list[dict[str, str]]:
    with database() as db:
        recent = [row["subject"] for row in db.execute(
            "SELECT jobs.subject FROM video_jobs AS jobs JOIN publications ON publications.job_id = jobs.id "
            "WHERE publications.channel_id=? AND jobs.deleted_at IS NULL ORDER BY jobs.created_at DESC LIMIT 30",
            (channel["id"],),
        )]
    trends = await asyncio.to_thread(youtube_trends, channel["language"])
    length = "vídeos longos de 5 a 15 minutos" if format == "long" else "Shorts verticais de 60 a 90 segundos"
    prompt = (
        f"Você é o estrategista de conteúdo de um canal. Sugira {count} temas para {length}, no idioma {channel['language']}."
        + (f" Nicho do canal: {channel['niche']}." if channel["niche"] else "")
        + ("\nEm alta no YouTube agora (só inspiração, adapte ao nicho):\n- " + "\n- ".join(trends) if trends else "")
        + ("\nJá produzidos, não repita:\n- " + "\n- ".join(recent) if recent else "")
        + "\nNão invente números, estatísticas nem fontes."
        + ' Responda apenas JSON: {"topics": [{"topic": tema pronto para usar como título de trabalho, "angle": o gancho ou ângulo, "rationale": por que deve funcionar neste canal}]}'
    )
    seen = {subject.strip().lower() for subject in recent}
    topics: list[dict[str, str]] = []
    for item in parse_json_object(await llm_chat(prompt, json_mode=True)).get("topics") or []:
        topic = str(item.get("topic") or "").strip()[:500] if isinstance(item, dict) else ""
        if topic and topic.lower() not in seen:  # the model still repeats a produced subject now and then
            seen.add(topic.lower())
            topics.append({"topic": topic, "angle": str(item.get("angle") or ""), "rationale": str(item.get("rationale") or "")})
    if not topics:
        raise HTTPException(status_code=502, detail="A IA não sugeriu nenhum tema novo")
    return topics[:count]


class StrategyInput(BaseModel):
    channel_id: str
    format: Literal["long", "short"] = "short"
    count: int = Field(default=5, ge=1, le=10)


@app.post("/api/agents/strategy")
async def strategy(input: StrategyInput) -> dict[str, Any]:
    with database() as db:
        channel = channel_row_or_404(db, input.channel_id)
    return {"topics": await suggest_topics(channel, input.format, input.count)}


def analytics_score(metrics: dict[str, Any], channel_median_views: float) -> int:
    """0-100 from retention, reach against the channel's own median (a small channel isn't judged by
    absolute views) and engagement."""
    views = float(metrics.get("views") or 0)
    retention = min(float(metrics.get("averageViewPercentage") or 0), 100) / 100
    reach = min(views / (2 * channel_median_views), 1) if channel_median_views > 0 else float(views > 0)  # twice the median scores full
    interactions = float(metrics.get("likes") or 0) + float(metrics.get("comments") or 0)
    engagement = min(interactions / views / 0.05, 1) if views else 0.0  # calibration knob: 5% likes+comments per view scores full
    return round(100 * (0.4 * retention + 0.3 * reach + 0.3 * engagement))


def youtube_video_metrics(credentials: Any, video_id: str, start_date: str) -> dict[str, Any]:
    from googleapiclient.discovery import build

    analytics = build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)
    report = analytics.reports().query(
        ids="channel==MINE", startDate=start_date, endDate=datetime.now(UTC).date().isoformat(), metrics=ANALYTICS_METRICS, filters=f"video=={video_id}",
    ).execute()
    rows = report.get("rows") or []
    if not rows:  # no views yet (Analytics lags a day or two)
        return {name: 0 for name in ANALYTICS_METRICS.split(",")}
    return dict(zip((header["name"] for header in report.get("columnHeaders") or []), rows[0]))


PUBLISHED_YOUTUBE = (
    "FROM publications AS p JOIN video_jobs AS jobs ON jobs.id = p.job_id LEFT JOIN channels ON channels.id = p.channel_id "
    "LEFT JOIN job_metadata AS meta ON meta.job_id = jobs.id LEFT JOIN video_analytics AS stats ON stats.publication_id = p.id "
    "WHERE p.platform='youtube' AND p.state='published' AND p.channel_id IS NOT NULL AND jobs.deleted_at IS NULL"
)


@app.get("/api/analytics/overview")
def analytics_platform_overview() -> dict[str, Any]:
    """Operational numbers for the Analytics tab: only rows that exist in SQLite
    plus YouTube Analytics snapshots. No estimates, no hardcoded totals."""
    with database() as db:
        channels = [row_as_channel(row) for row in db.execute("SELECT * FROM channels WHERE deleted_at IS NULL ORDER BY created_at DESC")]
        job_counts = {row["state"]: row["n"] for row in db.execute("SELECT state, COUNT(*) AS n FROM video_jobs WHERE deleted_at IS NULL GROUP BY state")}
        rows = db.execute(
            "SELECT p.id, p.channel_id, channels.name AS channel_name, jobs.subject, json_extract(meta.value_json, '$.title') AS title, "
            f"stats.metrics_json, stats.score, stats.captured_at {PUBLISHED_YOUTUBE} ORDER BY stats.score IS NULL, stats.score DESC"
        ).fetchall()
    videos: list[dict[str, Any]] = []
    total_views = total_likes = total_comments = 0
    retentions: list[float] = []
    scores: list[float] = []
    for row in rows:
        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else None
        videos.append({
            "publication_id": row["id"], "channel_id": row["channel_id"], "channel_name": row["channel_name"],
            "title": row["title"] or row["subject"], "metrics": metrics,
            "score": row["score"], "captured_at": row["captured_at"],
        })
        if metrics:
            total_views += int(metrics.get("views") or 0)
            total_likes += int(metrics.get("likes") or 0)
            total_comments += int(metrics.get("comments") or 0)
            if metrics.get("averageViewPercentage") is not None:
                retentions.append(float(metrics.get("averageViewPercentage") or 0))
        if row["score"] is not None:
            scores.append(float(row["score"]))
    followers = sum(int((channel.get("stats") or {}).get("followers") or 0) for channel in channels)
    return {
        "channels": channels,
        "jobs": {
            "total": sum(job_counts.values()),
            "completed": job_counts.get("completed", 0),
            "active": job_counts.get("queued", 0) + job_counts.get("running", 0),
            "failed": job_counts.get("failed", 0) + job_counts.get("interrupted", 0),
            "by_state": job_counts,
        },
        "youtube": {
            "videos_published": len(videos),
            "videos_measured": sum(1 for video in videos if video["metrics"]),
            "total_views": total_views,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "avg_retention": round(sum(retentions) / len(retentions), 1) if retentions else None,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "top": videos[:10],
        },
        "followers_total": followers,
    }


@app.get("/api/agents/analytics")
def analytics_overview() -> dict[str, Any]:
    with database() as db:
        videos = [
            {"publication_id": row["id"], "channel_id": row["channel_id"], "channel_name": row["channel_name"], "external_id": row["external_id"],
             "title": row["title"] or row["subject"], "metrics": json.loads(row["metrics_json"]) if row["metrics_json"] else None,
             "score": row["score"], "captured_at": row["captured_at"]}
            for row in db.execute(
                "SELECT p.id, p.channel_id, p.external_id, jobs.subject, channels.name AS channel_name, json_extract(meta.value_json, '$.title') AS title, "
                f"stats.metrics_json, stats.score, stats.captured_at {PUBLISHED_YOUTUBE} ORDER BY stats.score IS NULL, stats.score DESC"
            )
        ]
        names = {row["id"]: row["name"] for row in db.execute("SELECT id, name FROM channels")}
        recommendations = [
            {"channel_id": row["key"].split(":", 1)[1], "channel_name": names.get(row["key"].split(":", 1)[1], ""), "items": json.loads(row["value_json"]), "updated_at": row["updated_at"]}
            for row in db.execute("SELECT key, value_json, updated_at FROM settings WHERE key LIKE 'analytics-recommendations:%'")
        ]
    return {"videos": videos, "recommendations": recommendations}


@app.post("/api/agents/analytics/refresh")
async def refresh_analytics() -> dict[str, Any]:
    from googleapiclient.errors import HttpError

    with database() as db:
        rows = db.execute(f"SELECT p.id, p.channel_id, p.external_id, p.updated_at, jobs.subject {PUBLISHED_YOUTUBE}").fetchall()
    by_channel: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_channel.setdefault(row["channel_id"], []).append(row)
    errors: list[dict[str, str]] = []
    for channel_id, publications in by_channel.items():
        try:
            credentials = await asyncio.to_thread(youtube_credentials, channel_id)
        except HttpError as exc:
            detail = "Reconecte o canal em Canais para liberar o Analytics." if exc.resp.status == 403 else f"O YouTube Analytics respondeu {exc.resp.status}."
            errors.append({"channel_id": channel_id, "detail": detail})
            continue
        except Exception as exc:
            errors.append({"channel_id": channel_id, "detail": str(exc)[:300]})
            continue
        # ponytail: per-video try/except so one bad video (deleted/privated on YouTube) can't abort
        # the rest of the channel's refresh; upgrade to a batch Analytics query if this loop gets slow.
        ok_publications: list[sqlite3.Row] = []
        metrics: list[dict[str, Any]] = []
        for pub in publications:
            try:
                # Published is the last state change of a published row, so updated_at is the publish date.
                values = await asyncio.to_thread(youtube_video_metrics, credentials, pub["external_id"], pub["updated_at"][:10])
            except HttpError as exc:
                detail = "Reconecte o canal em Canais para liberar o Analytics." if exc.resp.status == 403 else f"O YouTube Analytics respondeu {exc.resp.status}."
                errors.append({"channel_id": channel_id, "detail": f"{pub['subject']}: {detail}"})
                continue
            except Exception as exc:
                errors.append({"channel_id": channel_id, "detail": f"{pub['subject']}: {str(exc)[:300]}"})
                continue
            ok_publications.append(pub)
            metrics.append(values)
        if not metrics:
            continue
        publications = ok_publications
        median = statistics.median(float(values.get("views") or 0) for values in metrics)
        with database() as db:
            for pub, values in zip(publications, metrics):
                db.execute(
                    "INSERT INTO video_analytics(publication_id, captured_at, metrics_json, score) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(publication_id) DO UPDATE SET captured_at=excluded.captured_at, metrics_json=excluded.metrics_json, score=excluded.score",
                    (pub["id"], now(), json.dumps(values), analytics_score(values, median)),
                )
        table = "\n".join(f"- {pub['subject']}: " + ", ".join(f"{key}={value}" for key, value in values.items()) for pub, values in zip(publications, metrics))
        try:
            advice = parse_json_object(await llm_chat(
                "Você analisa o desempenho dos vídeos de um canal do YouTube. Métricas do YouTube Analytics por vídeo "
                f"(averageViewDuration em segundos, averageViewPercentage em %):\n{table}\n"
                "Compare os vídeos entre si e dê de 3 a 5 recomendações práticas para os próximos, citando apenas números desta tabela. "
                'Responda apenas JSON: {"recommendations": [texto curto]}',
                json_mode=True,
            ))
        except HTTPException as exc:
            errors.append({"channel_id": channel_id, "detail": f"Recomendações indisponíveis: {exc.detail}"})
            continue
        items = [str(item).strip() for item in advice.get("recommendations") or [] if str(item).strip()][:5]
        with database() as db:
            db.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (f"analytics-recommendations:{channel_id}", json.dumps(items), now()),
            )
    return {**analytics_overview(), "errors": errors}


@app.get("/api/agents/schedule")
def list_schedule() -> list[dict[str, Any]]:
    with database() as db:
        return [dict(row) for row in db.execute(
            "SELECT p.job_id, p.channel_id, p.platform, p.state, p.auto_publish, p.external_id, p.scheduled_at, jobs.subject, channels.name AS channel_name, json_extract(meta.value_json, '$.title') AS title "
            "FROM publications AS p JOIN video_jobs AS jobs ON jobs.id = p.job_id LEFT JOIN channels ON channels.id = p.channel_id "
            "LEFT JOIN job_metadata AS meta ON meta.job_id = jobs.id WHERE p.state IN ('scheduled','auto_reserved','platform_scheduled') AND jobs.deleted_at IS NULL ORDER BY p.scheduled_at"
        )]


def get_or_create_project(name: str, language: str) -> dict[str, Any]:
    with database() as db:
        row = db.execute("SELECT * FROM projects WHERE name=? AND deleted_at IS NULL ORDER BY created_at LIMIT 1", (name,)).fetchone()
    if row:
        return row_as_project(row)
    return create_project(ProjectCreate(name=name, language=language, publish={"auto_publish": False, "platforms": [], "youtube_privacy": "private"}))


class OperatorInput(BaseModel):
    enabled: bool = False
    format: Literal["long", "short"] = "short"
    cadence_hours: Literal[12, 24, 48, 168] = 24
    next_run_at: datetime | None = None
    auto_publish: bool = False
    publish_days: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    publish_time: str = "12:00"
    publish_timezone: str = "America/Sao_Paulo"


def next_publish_slot(operator: sqlite3.Row, db: sqlite3.Connection, reference: datetime | None = None) -> datetime:
    zone = ZoneInfo(operator["publish_timezone"])
    hour, minute = map(int, operator["publish_time"].split(":"))
    days = set(json.loads(operator["publish_days_json"]))
    start = as_utc(reference or datetime.now(UTC)) + timedelta(hours=1)
    local_day = start.astimezone(zone).date()
    for offset in range(60):
        date = local_day + timedelta(days=offset)
        if date.weekday() not in days:
            continue
        local = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone)
        slot = local.astimezone(UTC)
        if slot <= start or slot.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
            continue
        occupied = db.execute(
            "SELECT 1 FROM publications WHERE channel_id=? AND scheduled_at=? AND state IN ('scheduled', 'auto_reserved', 'uploading', 'platform_scheduled', 'published')",
            (operator["channel_id"], slot.isoformat()),
        ).fetchone()
        if not occupied:
            return slot
    raise HTTPException(status_code=409, detail="Não há janela livre nos próximos 60 dias.")


def pause_operator(channel_id: str, reason: str, job_id: str | None = None) -> None:
    with database() as db:
        db.execute("UPDATE operators SET paused_reason=?, updated_at=? WHERE channel_id=?", (reason[:500], now(), channel_id))
    notify(f"operator-paused:{channel_id}:{job_id or reason}", "operator_paused", "Automação pausada", reason, job_id)


@app.get("/api/agents/operators")
def list_operators() -> dict[str, Any]:
    with database() as db:
        operators = [
            {"channel_id": row["id"], "channel_name": row["name"], "platform": row["platform"], "enabled": bool(row["enabled"]),
             "format": row["format"] or "short", "cadence_hours": row["cadence_hours"] or 24,
             "next_run_at": row["next_run_at"], "last_run_at": row["last_run_at"], "last_error": row["last_error"],
             "auto_publish": bool(row["auto_publish"]), "publish_days": json.loads(row["publish_days_json"] or "[0,1,2,3,4,5,6]"),
             "publish_time": row["publish_time"], "publish_timezone": row["publish_timezone"], "paused_reason": row["paused_reason"],
             "last_run": dict(db.execute("SELECT id, state, job_id, batch_id, error, started_at FROM operator_runs WHERE channel_id=? ORDER BY started_at DESC LIMIT 1", (row["id"],)).fetchone() or {}),
             "next_publication": dict(db.execute(
                 "SELECT p.job_id, p.state, p.scheduled_at, p.external_id, j.subject FROM publications p JOIN video_jobs j ON j.id=p.job_id "
                 "WHERE p.channel_id=? AND p.auto_publish=1 AND p.state IN ('auto_reserved','uploading','platform_scheduled') "
                 "ORDER BY p.scheduled_at LIMIT 1", (row["id"],)
             ).fetchone() or {})}
            for row in db.execute(
                "SELECT channels.id, channels.name, channels.platform, operators.enabled, operators.format, operators.cadence_hours, "
                "operators.next_run_at, operators.last_run_at, operators.last_error, operators.auto_publish, operators.publish_days_json, operators.publish_time, operators.publish_timezone, operators.paused_reason FROM channels LEFT JOIN operators ON operators.channel_id = channels.id "
                "WHERE channels.status='connected' AND channels.deleted_at IS NULL ORDER BY channels.created_at"
            )
        ]
        operator_jobs = (
            "FROM video_jobs AS jobs JOIN publications AS p ON p.job_id = jobs.id "
            "LEFT JOIN channels ON channels.id = p.channel_id "
            "WHERE jobs.created_by='operator' AND jobs.deleted_at IS NULL"
        )
        awaiting = [dict(row) for row in db.execute(
            "SELECT jobs.id, jobs.subject, jobs.format, jobs.completed_at, channels.name AS channel_name "
            f"{operator_jobs} AND jobs.state='completed' AND (jobs.generation_item_id IS NULL OR EXISTS ("
            "SELECT 1 FROM generation_quality q WHERE q.item_id=jobs.generation_item_id AND q.state='approved')) "
            "ORDER BY jobs.completed_at DESC LIMIT 50"
        )]
        in_progress = [dict(row) for row in db.execute(
            "SELECT jobs.id, jobs.subject, jobs.format, jobs.state, jobs.error, jobs.created_at, channels.name AS channel_name "
            f"{operator_jobs} AND jobs.state IN ('queued', 'running', 'failed', 'interrupted') "
            "ORDER BY jobs.created_at DESC LIMIT 50"
        )]
    return {"operators": operators, "awaiting_approval": awaiting, "in_progress": in_progress}


@app.put("/api/agents/operators/{channel_id}")
def put_operator(channel_id: str, input: OperatorInput) -> dict[str, Any]:
    with database() as db:
        channel = channel_row_or_404(db, channel_id)
        if channel["status"] != "connected":
            raise HTTPException(status_code=409, detail="O canal não está conectado. Reconecte em Canais.")
        if channel["platform"] != "youtube" and input.format == "long":
            raise HTTPException(status_code=409, detail="TikTok recebe apenas Shorts.")
        if input.auto_publish and channel["platform"] != "youtube":
            raise HTTPException(status_code=422, detail="Publicação automática está disponível apenas no YouTube.")
        if len(set(input.publish_days)) != len(input.publish_days) or any(day not in range(7) for day in input.publish_days):
            raise HTTPException(status_code=422, detail="Dias da grade inválidos.")
        if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", input.publish_time):
            raise HTTPException(status_code=422, detail="Horário da grade inválido.")
        try:
            ZoneInfo(input.publish_timezone)
        except ZoneInfoNotFoundError:
            raise HTTPException(status_code=422, detail="Fuso horário inválido.") from None
        next_run = as_utc(input.next_run_at) if input.next_run_at else datetime.now(UTC) + timedelta(hours=input.cadence_hours)
        db.execute(
            "INSERT INTO operators(channel_id, enabled, format, cadence_hours, next_run_at, auto_publish, publish_days_json, publish_time, publish_timezone, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET enabled=excluded.enabled, format=excluded.format, cadence_hours=excluded.cadence_hours, next_run_at=excluded.next_run_at, auto_publish=excluded.auto_publish, publish_days_json=excluded.publish_days_json, publish_time=excluded.publish_time, publish_timezone=excluded.publish_timezone, paused_reason=NULL, updated_at=excluded.updated_at",
            (channel_id, int(input.enabled), input.format, input.cadence_hours, next_run.isoformat(), int(input.auto_publish), json.dumps(input.publish_days), input.publish_time, input.publish_timezone, now()),
        )
    return next(operator for operator in list_operators()["operators"] if operator["channel_id"] == channel_id)


@app.post("/api/agents/operators/{channel_id}/run")
async def run_operator(channel_id: str, scheduled: bool = False) -> dict[str, Any]:
    """Claim a channel run before calling providers so two workers cannot generate twice."""
    run_id = str(uuid4())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        operator = db.execute("SELECT * FROM operators WHERE channel_id=?", (channel_id,)).fetchone()
        if not operator:
            raise HTTPException(status_code=404, detail="Salve o operador deste canal antes de rodar.")
        channel = db.execute("SELECT * FROM channels WHERE id=? AND deleted_at IS NULL", (channel_id,)).fetchone()
        if scheduled and (not operator["enabled"] or operator["paused_reason"] or not operator["next_run_at"] or operator["next_run_at"] > now()):
            return {"job": None, "error": "Operador não está pronto para a execução agendada."}
        busy = db.execute(
            "SELECT 1 FROM video_jobs AS jobs JOIN publications ON publications.job_id=jobs.id "
            "WHERE publications.channel_id=? AND jobs.created_by='operator' AND jobs.state IN ('queued','running') AND jobs.deleted_at IS NULL",
            (channel_id,),
        ).fetchone()
        pending_batch = db.execute("SELECT 1 FROM operator_runs WHERE channel_id=? AND state IN ('running','queued') LIMIT 1", (channel_id,)).fetchone()
        lease_active = operator["lease_until"] and operator["lease_until"] > now()
        if busy or pending_batch or lease_active:
            return {"job": None, "error": "Pulado: o vídeo anterior do operador ainda está sendo gerado."}
        slot_key = operator["next_run_at"] if scheduled else f"manual:{run_id}"
        db.execute(
            "INSERT OR IGNORE INTO operator_runs(id, channel_id, slot_key, state, started_at) VALUES (?, ?, ?, 'running', ?)",
            (run_id, channel_id, slot_key, now()),
        )
        if not db.execute("SELECT 1 FROM operator_runs WHERE id=?", (run_id,)).fetchone():
            return {"job": None, "error": "Esta janela já foi executada."}
        next_run = (datetime.now(UTC) + timedelta(hours=operator["cadence_hours"])).isoformat()
        db.execute(
            "UPDATE operators SET lease_owner=?, lease_until=?, next_run_at=CASE WHEN ? THEN ? ELSE next_run_at END, last_run_at=?, updated_at=? WHERE channel_id=?",
            (run_id, (datetime.now(UTC) + timedelta(hours=1)).isoformat(), int(scheduled), next_run, now(), now(), channel_id),
        )
    batch, error = None, None
    try:
        if not channel or channel["status"] != "connected":
            raise HTTPException(status_code=409, detail="O canal não está conectado. Reconecte em Canais.")
        from gateway.auto_generation import AutoGenerationInput, create_auto_batch
        format = operator["format"]
        project = get_or_create_project(PROJECT_NAMES[format], channel["language"])
        batch = create_auto_batch(project["id"], AutoGenerationInput(format=format, channel_ids=[channel_id], client_request_id=run_id))
    except Exception as exc:
        error = str(getattr(exc, "detail", exc))[:500] or "Falha no operador"
    with database() as db:
        db.execute("UPDATE operator_runs SET state=?, batch_id=?, error=?, finished_at=? WHERE id=?",
                   ("failed" if error else "queued", batch["batch_id"] if batch else None, error, now() if error else None, run_id))
        if error:
            db.execute("UPDATE operators SET lease_owner=NULL, lease_until=NULL, last_error=?, updated_at=? WHERE channel_id=? AND lease_owner=?",
                       (error, now(), channel_id, run_id))
    if error and scheduled:
        pause_operator(channel_id, error)
    return {"job": None, "batch_id": batch["batch_id"] if batch else None, "error": error}


def youtube_video_status(credentials: Any, video_id: str) -> dict[str, Any] | None:
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    items = youtube.videos().list(part="status", id=video_id).execute().get("items") or []
    return items[0].get("status") if items else None


async def verify_auto_youtube_publication(job_id: str, channel_id: str, external_id: str, slot: datetime) -> None:
    credentials = await asyncio.to_thread(youtube_credentials, channel_id)
    status = await asyncio.to_thread(youtube_video_status, credentials, external_id)
    if not status:
        raise HTTPException(status_code=502, detail="O YouTube não retornou o vídeo enviado.")
    privacy = status.get("privacyStatus")
    if privacy == "public":
        if datetime.now(UTC) < slot - timedelta(minutes=2):
            raise HTTPException(status_code=502, detail="O vídeo ficou público antes do horário programado.")
        with database() as db:
            db.execute("UPDATE publications SET state='published', updated_at=? WHERE job_id=? AND channel_id=? AND state='platform_scheduled'",
                       (now(), job_id, channel_id))
        return
    published_at = status.get("publishAt")
    if privacy != "private" or not published_at or abs((parse_time(published_at) - slot).total_seconds()) > 60:
        raise HTTPException(status_code=502, detail="O YouTube não confirmou a publicação programada.")
    if datetime.now(UTC) > slot + timedelta(minutes=15):
        raise HTTPException(status_code=502, detail="O vídeo continua privado após o horário programado.")


async def auto_publish_operator_job(job_id: str) -> None:
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        job = job_row_or_404(db, job_id)
        target = db.execute(
            "SELECT p.*, c.name AS channel_name, c.status AS channel_status, c.platform, o.auto_publish AS operator_auto_publish, o.enabled, "
            "o.paused_reason, o.publish_days_json, o.publish_time, o.publish_timezone "
            "FROM publications p JOIN channels c ON c.id=p.channel_id JOIN operators o ON o.channel_id=c.id "
            "WHERE p.job_id=? AND c.deleted_at IS NULL AND c.platform='youtube'",
            (job_id,),
        ).fetchone()
        if not target or not target["operator_auto_publish"] or not target["enabled"] or target["paused_reason"]:
            return
        if target["state"] in ("platform_scheduled", "published", "reconciliation_required", "uploading"):
            return
        if target["state"] not in ("pending", "auto_reserved"):
            return
        if target["state"] == "auto_reserved" and target["auto_lease_until"] and target["auto_lease_until"] > now():
            return
        if job["state"] not in ("completed", "approved") or not job["auto_publish_authorized"]:
            return
        require_quality_approval(db, job)
        if target["channel_status"] != "connected" or not youtube_token_file(target["channel_id"]).is_file():
            raise HTTPException(status_code=409, detail="Canal sem autorização válida. Reconecte em Canais.")
        artifact = db.execute("SELECT remote_path FROM artifacts WHERE job_id=? AND kind='video' LIMIT 1", (job_id,)).fetchone()
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        if not artifact or not stored:
            raise HTTPException(status_code=409, detail="Vídeo final ou metadados ausentes.")
        metadata, errors, _ = normalize_youtube_metadata(json.loads(stored["value_json"]))
        if errors:
            raise HTTPException(status_code=409, detail="Metadados inválidos: " + ", ".join(errors))
        slot = parse_time(target["scheduled_at"]) if target["state"] == "auto_reserved" else next_publish_slot(target, db)
        if slot <= datetime.now(UTC) + timedelta(hours=1):
            slot = next_publish_slot(target, db)
        claimed = db.execute(
            "UPDATE publications SET state='auto_reserved', scheduled_at=?, auto_publish=1, auto_lease_owner=?, auto_lease_until=?, updated_at=? "
            "WHERE id=? AND state IN ('pending','auto_reserved') AND (auto_lease_until IS NULL OR auto_lease_until <= ? OR state='pending')",
            (slot.isoformat(), str(uuid4()), (datetime.now(UTC) + timedelta(hours=3)).isoformat(), now(), target["id"], now()),
        ).rowcount
        if not claimed:
            return
        channel = channel_row_or_404(db, target["channel_id"])
    with tempfile.TemporaryDirectory() as folder:
        video_path = Path(folder) / "video.mp4"
        async def video() -> Path:
            await download_turbo_file(artifact["remote_path"], video_path)
            if not video_path.is_file() or video_path.stat().st_size == 0:
                raise HTTPException(status_code=502, detail="O vídeo final está vazio.")
            with database() as db:
                current = db.execute(
                    "SELECT o.enabled, o.auto_publish, o.paused_reason, c.status FROM operators o "
                    "JOIN channels c ON c.id=o.channel_id WHERE o.channel_id=?", (channel["id"],)
                ).fetchone()
                if not current or not current["enabled"] or not current["auto_publish"] or current["paused_reason"] or current["status"] != "connected":
                    raise HTTPException(status_code=409, detail="Autorização automática ou conexão do canal mudou durante o preparo.")
                if not youtube_token_file(channel["id"]).is_file():
                    raise HTTPException(status_code=409, detail="Autorização do YouTube ausente.")
                db.execute("UPDATE video_jobs SET state='approved', updated_at=? WHERE id=? AND state='completed'", (now(), job_id))
            return video_path
        await publish_to_channel(job_id, channel, video, {**metadata, "format": job["format"]}, False, slot)
    with database() as db:
        publication = db.execute("SELECT external_id, scheduled_at FROM publications WHERE job_id=? AND channel_id=? AND state='platform_scheduled'",
                                 (job_id, channel["id"])).fetchone()
    if publication:
        await verify_auto_youtube_publication(job_id, channel["id"], publication["external_id"], parse_time(publication["scheduled_at"]))


async def prepare_auto_publications() -> None:
    with database() as db:
        jobs = [dict(row) for row in db.execute(
            "SELECT jobs.id, p.channel_id FROM video_jobs jobs JOIN publications p ON p.job_id=jobs.id "
            "JOIN operators o ON o.channel_id=p.channel_id "
            "WHERE jobs.created_by='operator' AND jobs.auto_publish_authorized=1 AND jobs.deleted_at IS NULL AND jobs.state IN ('completed','approved') "
            "AND p.state IN ('pending','auto_reserved') AND o.enabled=1 AND o.auto_publish=1 AND o.paused_reason IS NULL "
            "AND EXISTS(SELECT 1 FROM job_metadata m WHERE m.job_id=jobs.id)"
        )]
    for row in jobs:
        try:
            await auto_publish_operator_job(row["id"])
        except Exception as exc:
            with database() as db:
                db.execute(
                    "UPDATE publications SET auto_lease_owner=NULL, auto_lease_until=NULL WHERE job_id=? AND channel_id=? AND state='auto_reserved'",
                    (row["id"], row["channel_id"]),
                )
            pause_operator(row["channel_id"], str(getattr(exc, "detail", exc)), row["id"])


async def reconcile_auto_publications() -> None:
    with database() as db:
        uncertain = [dict(row) for row in db.execute(
            "SELECT p.id, p.job_id, p.channel_id FROM publications p WHERE p.auto_publish=1 AND p.state='uploading' "
            "AND (p.auto_lease_until IS NULL OR p.auto_lease_until <= ?)", (now(),)
        )]
    for row in uncertain:
        with database() as db:
            db.execute("UPDATE publications SET state='reconciliation_required', error=?, updated_at=? WHERE id=? AND state='uploading'",
                       ("Envio interrompido; confira o YouTube antes de tentar novamente.", now(), row["id"]))
        pause_operator(row["channel_id"], "Resultado do upload incerto; confira o YouTube.", row["job_id"])
    with database() as db:
        scheduled = [dict(row) for row in db.execute(
            "SELECT p.job_id, p.channel_id, p.external_id, p.scheduled_at FROM publications p "
            "WHERE p.auto_publish=1 AND p.state='platform_scheduled' AND p.scheduled_at <= ?",
            ((datetime.now(UTC) + timedelta(minutes=15)).isoformat(),),
        )]
    for row in scheduled:
        try:
            await verify_auto_youtube_publication(row["job_id"], row["channel_id"], row["external_id"], parse_time(row["scheduled_at"]))
        except Exception as exc:
            pause_operator(row["channel_id"], str(getattr(exc, "detail", exc)), row["job_id"])


async def publish_due_schedules() -> None:
    with database() as db:
        due = db.execute("SELECT job_id, channel_id FROM publications WHERE state='scheduled' AND scheduled_at <= ?", (now(),)).fetchall()
    targets: dict[str, list[str]] = {}
    for row in due:
        targets.setdefault(row["job_id"], []).append(row["channel_id"])
    for job_id, channel_ids in targets.items():
        try:
            await publish_job(job_id, PublishInput(channel_ids=channel_ids))
        except Exception as exc:
            # A check failing before the claim (lost authorization, deleted video) would otherwise retry every minute forever.
            with database() as db:
                db.execute(
                    f"UPDATE publications SET state='failed', error=?, updated_at=? WHERE job_id=? AND state='scheduled' AND channel_id IN ({','.join('?' * len(channel_ids))})",
                    (str(getattr(exc, "detail", exc))[:500], now(), job_id, *channel_ids),
                )


async def reconcile_processing_publications() -> None:
    with database() as db:
        job_ids = [row["job_id"] for row in db.execute("SELECT DISTINCT job_id FROM publications WHERE state='processing'")]
    for job_id in job_ids:
        try:
            await reconcile_publications(job_id)
        except Exception as exc:
            logger.warning("Reconciliação: o trabalho %s falhou: %s", job_id, getattr(exc, "detail", exc))


async def run_due_operators() -> None:
    with database() as db:
        due = [row["channel_id"] for row in db.execute("SELECT channel_id FROM operators WHERE enabled=1 AND paused_reason IS NULL AND next_run_at <= ?", (now(),))]
    for channel_id in due:
        await run_operator(channel_id, scheduled=True)


async def prepare_operator_jobs() -> None:
    """Keeps operator renders tracked without a browser open and writes their metadata; they stay "completed" for approval."""
    with database() as db:
        job_ids = [row["id"] for row in db.execute(
            "SELECT jobs.id FROM video_jobs AS jobs LEFT JOIN job_metadata AS meta ON meta.job_id = jobs.id "
            "WHERE jobs.created_by='operator' AND jobs.deleted_at IS NULL AND meta.job_id IS NULL AND jobs.state IN ('queued', 'running', 'completed')"
        )]
    for job_id in job_ids:
        try:
            if (await reconcile_job(job_id)).get("state") == "completed":
                await generate_metadata(job_id)
        except Exception as exc:
            logger.warning("Operador: o trabalho %s não avançou: %s", job_id, getattr(exc, "detail", exc))
            with database() as db:
                channel = db.execute(
                    "SELECT p.channel_id FROM publications p JOIN operators o ON o.channel_id=p.channel_id "
                    "WHERE p.job_id=? AND o.auto_publish=1 LIMIT 1", (job_id,)
                ).fetchone()
            if channel:
                pause_operator(channel["channel_id"], str(getattr(exc, "detail", exc)), job_id)


def pause_failed_operator_jobs() -> None:
    with database() as db:
        failed = [dict(row) for row in db.execute(
            "SELECT j.id, j.error, p.channel_id FROM video_jobs j JOIN publications p ON p.job_id=j.id "
            "JOIN operators o ON o.channel_id=p.channel_id WHERE j.created_by='operator' AND j.state IN ('failed','interrupted') "
            "AND o.enabled=1 AND o.auto_publish=1 AND o.paused_reason IS NULL AND j.updated_at > o.updated_at"
        )]
    for row in failed:
        pause_operator(row["channel_id"], row["error"] or "Geração falhou; confira o trabalho.", row["id"])


def recover_operator_leases() -> None:
    with database() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT channel_id, lease_owner FROM operators WHERE lease_owner IS NOT NULL AND lease_until <= ?", (now(),)
        )]
        for row in rows:
            db.execute("UPDATE operator_runs SET state='interrupted', error=?, finished_at=? WHERE id=? AND state='running'",
                       ("Worker interrompido antes da conclusão da execução.", now(), row["lease_owner"]))
            db.execute("UPDATE operators SET lease_owner=NULL, lease_until=NULL, paused_reason=?, updated_at=? WHERE channel_id=? AND lease_owner=?",
                       ("Execução interrompida; confira o trabalho antes de reativar.", now(), row["channel_id"], row["lease_owner"]))
    for row in rows:
        notify(f"operator-interrupted:{row['lease_owner']}", "operator_paused", "Automação pausada",
               "Worker interrompido; confira o trabalho antes de reativar.")


async def agent_tick() -> None:
    for check in (recover_operator_leases, pause_failed_operator_jobs):
        try:
            check()
        except Exception:
            logger.exception("Passo %s do laço de agentes falhou", check.__name__)
    for step in (reconcile_stale_jobs, reconcile_auto_publications, publish_due_schedules, reconcile_processing_publications, run_due_operators, prepare_operator_jobs, prepare_auto_publications):
        try:
            await step()
        except Exception:
            logger.exception("Passo %s do laço de agentes falhou", step.__name__)


async def agent_loop() -> None:
    # ponytail: in-process loop, fine while the Dockerfile runs a single uvicorn worker; several workers would need a DB lock.
    while True:
        await agent_tick()
        await asyncio.sleep(AGENT_LOOP_SECONDS)


@app.middleware("http")
async def no_cache_for_static_app(request: Request, call_next):
    """Without this, Chrome heuristically caches the HTML document itself (no Cache-Control
    at all was being sent), so a reload can keep serving yesterday's Cockpit.html - complete
    with its old ?v=... script tags - without ever asking the server if anything changed.
    Bumping the query string then does nothing until a hard refresh. server.js (the non-Docker
    path) already disables caching for exactly this reason; this mirrors it for the gateway."""
    trace_id = observability_trace_id(request.headers.get("X-Trace-Id", ""))
    trace = {"spans": []}
    token = ACTIVE_TRACE.set(trace)
    started = time.perf_counter()
    try:
        path = request.url.path
        exempt = {"/api/health", "/api/health/live", "/api/health/ready", "/api/auth/login", "/favicon.ico"}
        protected = path.startswith("/api/") or path == "/docs" or path == "/openapi.json"
        session = None
        if protected and path not in exempt:
            session = session_from_request(request)
            if not session:
                return JSONResponse({"detail": "Autenticação necessária."}, status_code=401)
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                supplied = request.headers.get("X-CSRF-Token", "")
                if not secrets.compare_digest(supplied, session["csrf_token"]):
                    return JSONResponse({"detail": "Token CSRF inválido."}, status_code=403)
        response = await call_next(request)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        if request.url.path.startswith("/api/") and request.url.path != "/api/logs":
            try:
                record_observability_event(
                    trace_id=trace_id, method=request.method, path=request.url.path,
                    status_code=500, duration_ms=duration_ms, spans=trace["spans"],
                    error=f"{request.method} {request.url.path} falhou: {type(exc).__name__}",
                )
            except Exception:
                logging.getLogger("cockpit.observability").exception("Falha ao registrar evento")
        raise
    finally:
        ACTIVE_TRACE.reset(token)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; script-src 'self'"
    if request.url.path.startswith("/api/") and request.url.path != "/api/logs":
        try:
            record_observability_event(
                trace_id=trace_id, method=request.method, path=request.url.path,
                status_code=response.status_code, duration_ms=duration_ms, spans=trace["spans"],
            )
        except Exception:
            logging.getLogger("cockpit.observability").exception("Falha ao registrar evento")
    is_app_shell = request.url.path == "/" or request.url.path.startswith("/shared/") or request.url.path.endswith(".html")
    if request.method in {"GET", "HEAD"} and is_app_shell:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_ROOT / "Cockpit.html")


@app.get("/.image-slots.state.json")
def image_slots_state() -> JSONResponse:
    """The <image-slot> scaffold fetches this sidecar on every page load. Outside the
    omelette runtime nothing ever writes it, so serve an empty store instead of 404ing
    (the component treats both the same, but the 404 litters the browser console)."""
    return JSONResponse({})


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Browsers request this unprompted (fresh loads, bookmarks, cached pages from before
    the data-URI icon). Answer empty instead of 404ing."""
    return Response(status_code=204)


from gateway.auto_generation import router as auto_generation_router

app.include_router(auto_generation_router)
app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
