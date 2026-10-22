"""Shared helpers for the Cockpit gateway routes.

Path and login settings stay on gateway.main so existing tests can rebind
DATA_DIR, DATABASE, and ADMIN_FILE on that module. Call runtime() inside a
function; do not import gateway.main at module scope.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request

from gateway.auth_crypto import admin_session_version, session_token_hash


def runtime():
    """The gateway.main module, loaded after this module to avoid an import cycle."""
    import gateway.main as main
    return main


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
        data = json.loads(runtime().ADMIN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    required = {"name", "email", "salt", "hash"}
    return data if required.issubset(data) else None



def connect() -> sqlite3.Connection:
    runtime().DATA_DIR.mkdir(parents=True, exist_ok=True)
    # API and workers share this database. The timeout serializes concurrent writers;
    # initialization selects the journal mode supported by the runtime volume.
    connection = sqlite3.connect(runtime().DATABASE, timeout=30.0)
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
    runtime().DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = runtime().DATA_DIR / ".cockpit-db-init.lock"
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
    runtime().DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(runtime().DATABASE, timeout=30.0)
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
              input_json TEXT NOT NULL, created_at TEXT NOT NULL, archived_at TEXT,
              UNIQUE(project_id, client_request_id)
            );
            CREATE TABLE IF NOT EXISTS generation_batch_items (
              id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES generation_batches(id),
              channel_id TEXT NOT NULL REFERENCES channels(id), channel_name TEXT NOT NULL,
              state TEXT NOT NULL, stage TEXT NOT NULL, context_json TEXT NOT NULL,
              generated_json TEXT NOT NULL DEFAULT '{}', job_id TEXT REFERENCES video_jobs(id),
              repair_parent_item_id TEXT REFERENCES generation_batch_items(id),
              repair_root_item_id TEXT REFERENCES generation_batch_items(id),
              repair_attempt INTEGER NOT NULL DEFAULT 0,
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
        batch_columns = {column["name"] for column in db.execute("PRAGMA table_info(generation_batches)")}
        if "archived_at" not in batch_columns:
            db.execute("ALTER TABLE generation_batches ADD COLUMN archived_at TEXT")
        item_columns = {column["name"] for column in db.execute("PRAGMA table_info(generation_batch_items)")}
        for column, definition in (
            ("repair_parent_item_id", "TEXT REFERENCES generation_batch_items(id)"),
            ("repair_root_item_id", "TEXT REFERENCES generation_batch_items(id)"),
            ("repair_attempt", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in item_columns:
                db.execute(f"ALTER TABLE generation_batch_items ADD COLUMN {column} {definition}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_repair_parent ON generation_batch_items(repair_parent_item_id) WHERE repair_parent_item_id IS NOT NULL")
        db.execute("CREATE INDEX IF NOT EXISTS idx_generation_repair_root ON generation_batch_items(repair_root_item_id, repair_attempt)")
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
                ("generation-defaults", json.dumps(runtime().default_generation_settings()), now()),
            )
        else:
            # Backfill: existing installs gain the new keys without losing what the user already saved.
            stored = json.loads(existing["value_json"])
            missing = {key: value for key, value in runtime().default_generation_settings().items() if key not in stored}
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
            base = settings_rows.get("generation-defaults", runtime().default_generation_settings())
            defaults = {
                "short": video_profile_from_settings("short", {**base, **runtime().FORMAT_PRESETS["short"], **settings_rows.get("generation-defaults:short", {})}),
                "long": video_profile_from_settings("long", {**base, **runtime().FORMAT_PRESETS["long"], **settings_rows.get("generation-defaults:long", {})}),
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



def open_session() -> dict[str, str]:
    return {
        "token_hash": session_token_hash("cockpit-login-disabled"),
        "csrf_token": runtime()._OPEN_SESSION_CSRF,
        "expires_at": (datetime.now(UTC) + timedelta(days=3650)).isoformat(),
    }


def session_from_request(request: Request) -> sqlite3.Row | dict[str, str] | None:
    if runtime().LOGIN_DISABLED:
        return open_session()
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



YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly", "https://www.googleapis.com/auth/yt-analytics.readonly"]
LANGUAGE_PATTERN = r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*"
UPLOAD_POST_BASE = "https://api.upload-post.com"
PLATFORM_LABELS = {"youtube": "YouTube Studio", "tiktok": "TikTok"}

VOICE_BY_LANGUAGE = {"pt": "pt-BR-FranciscaNeural", "en": "en-US-JennyNeural", "es": "es-ES-ElviraNeural", "de": "de-DE-KatjaNeural"}

def youtube_client_file() -> Path:
    return runtime().DATA_DIR / "secrets" / "youtube_client_secret.json"


def youtube_token_file(channel_id: str) -> Path:
    return runtime().DATA_DIR / "secrets" / "youtube_tokens" / f"{channel_id}.json"


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
    profiles = {format: video_profile_from_settings(format, runtime().generation_defaults(format)) for format in ("short", "long")}
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
        raise HTTPException(status_code=422, detail="The channel video parameters must be an object.")
    base = fallback if fallback is not None else default_channel_video_defaults(language)
    result: dict[str, dict[str, Any]] = {}
    for format in ("short", "long"):
        raw = values.get(format, {})
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail=f"The {format} parameters must be an object.")
        allowed = set(CHANNEL_VIDEO_PROFILE_FIELDS[format])
        unknown = set(raw) - allowed
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unsupported video parameter: {sorted(unknown)[0]}.")
        current = dict(base.get(format, {}))
        current.update(raw)
        media = normalize_channel_profile({"language": language, **current})
        media.pop("language")  # language remains channel-wide; only voice and captions vary by format.
        try:
            clip_duration = int(current["video_clip_duration"])
            clip_speed = float(current["video_clip_speed"])
            bgm_volume = float(current["bgm_volume"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Invalid numeric video parameters.") from None
        if not 1 <= clip_duration <= 30:
            raise HTTPException(status_code=422, detail="Each clip must last between 1 and 30 seconds.")
        if clip_speed not in VIDEO_CLIP_SPEEDS:
            raise HTTPException(status_code=422, detail="Invalid clip speed.")
        if bgm_volume not in VIDEO_BGM_VOLUMES:
            raise HTTPException(status_code=422, detail="Invalid music volume.")
        concat_mode = str(current.get("video_concat_mode", ""))
        transition = str(current.get("video_transition_mode", ""))
        bgm_type = str(current.get("bgm_type", ""))
        if concat_mode not in VIDEO_CONCAT_MODES:
            raise HTTPException(status_code=422, detail="Invalid image mode.")
        if transition not in VIDEO_TRANSITIONS:
            raise HTTPException(status_code=422, detail="Invalid transition.")
        if bgm_type not in VIDEO_BGM_TYPES:
            raise HTTPException(status_code=422, detail="Invalid soundtrack.")
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
                raise HTTPException(status_code=422, detail="Invalid Short duration or video count.") from None
            if paragraph_number not in {4, 5, 6} or not 1 <= video_count <= 5:
                raise HTTPException(status_code=422, detail="Shorts accept a duration of 60, 75, or 90 seconds and 1 to 5 videos.")
            result[format] = {**media, "paragraph_number": paragraph_number, "video_count": video_count, **profile}
        else:
            try:
                target_minutes = int(current["target_minutes"])
            except (KeyError, TypeError, ValueError):
                raise HTTPException(status_code=422, detail="Invalid long-video duration.") from None
            if target_minutes not in {5, 8, 10, 15}:
                raise HTTPException(status_code=422, detail="Long videos accept 5, 8, 10, or 15 minutes.")
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
        raise HTTPException(status_code=422, detail="The chosen voice is not compatible with the narration language.")
    profile: dict[str, Any] = {"language": language, "voice_name": voice_name}
    numeric_ranges = {"voice_rate": (0.5, 2.0), "voice_volume": (0.0, 2.0), "stroke_width": (0.0, 10.0)}
    for field, (minimum, maximum) in numeric_ranges.items():
        try:
            number = float(values.get(field, 1.0 if field != "stroke_width" else 1.5))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{field} must be numeric.") from None
        if not minimum <= number <= maximum:
            raise HTTPException(status_code=422, detail=f"{field} is outside the allowed limit.")
        profile[field] = number
    try:
        font_size = int(values.get("font_size", 56))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="font_size deve ser inteiro.") from None
    if not 12 <= font_size <= 120:
        raise HTTPException(status_code=422, detail="font_size is outside the allowed limit.")
    profile["font_size"] = font_size
    profile["subtitle_enabled"] = bool(values.get("subtitle_enabled", True))
    profile["subtitle_position"] = str(values.get("subtitle_position", "bottom"))
    if profile["subtitle_position"] not in CHANNEL_PROFILE_POSITIONS:
        raise HTTPException(status_code=422, detail="Invalid caption position.")
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
            raise HTTPException(status_code=422, detail="Each visual guide must be at most 2,000 characters.")
        result[format] = guide.strip()
    return result


def channel_row_or_404(db: sqlite3.Connection, channel_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM channels WHERE id=? AND deleted_at IS NULL", (channel_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")
    return row


def update_channel(channel_id: str, **fields: Any) -> None:
    assignments = ", ".join(f"{key}=?" for key in fields)  # keys come from this module, never from a request
    with database() as db:
        db.execute(f"UPDATE channels SET {assignments}, updated_at=? WHERE id=?", (*fields.values(), now(), channel_id))



async def upload_post_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    key = runtime().config_value("upload_post_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure the Upload-Post key in Settings to use TikTok channels.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            response = await runtime().send_with_retry(client, method, f"{UPLOAD_POST_BASE}{path}", headers={"Authorization": f"Apikey {key}"}, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Upload-Post is unavailable: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Upload-Post respondeu {response.status_code}: {response.text[:300]}")
    return response.json()



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



