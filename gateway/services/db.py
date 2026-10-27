"""SQLite connection management and schema initialization."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Literal

from gateway import main as m


def connect() -> sqlite3.Connection:
    m.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # API and workers share this database. The timeout serializes concurrent writers;
    # initialization selects the journal mode supported by the runtime volume.
    connection = sqlite3.connect(m.DATABASE, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def database() -> Any:
    connection = m.connect()
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
    m.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = m.DATA_DIR / ".cockpit-db-init.lock"
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
    with m.database_initialization_lock():
        m._initialize_database_locked()


def _initialize_database_locked() -> None:
    m.DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(m.DATABASE, timeout=30.0)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        journal_mode = os.getenv("COCKPIT_SQLITE_JOURNAL_MODE", "WAL").upper()
        if journal_mode not in {"WAL", "DELETE"}:
            raise ValueError("COCKPIT_SQLITE_JOURNAL_MODE deve ser WAL ou DELETE.")
        connection.execute(f"PRAGMA journal_mode = {journal_mode}")
    finally:
        connection.close()
    with m.database() as db:
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
                (m.now(), m.now()),
            )
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_publication_channel_slot ON publications(channel_id, scheduled_at) WHERE channel_id IS NOT NULL AND scheduled_at IS NOT NULL AND state IN ('auto_reserved', 'uploading', 'platform_scheduled')")
        existing = db.execute("SELECT value_json FROM settings WHERE key = 'generation-defaults'").fetchone()
        if not existing:
            db.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                ("generation-defaults", json.dumps(m.default_generation_settings()), m.now()),
            )
        else:
            # Backfill: instalações existentes ganham as chaves novas sem perder o que o usuário já salvou.
            stored = json.loads(existing["value_json"])
            missing = {key: value for key, value in m.default_generation_settings().items() if key not in stored}
            if missing:
                db.execute(
                    "UPDATE settings SET value_json=?, updated_at=? WHERE key='generation-defaults'",
                    (json.dumps({**stored, **missing}), m.now()),
                )
        channel_columns = {column["name"] for column in db.execute("PRAGMA table_info(channels)")}
        if "video_defaults_json" not in channel_columns:
            db.execute("ALTER TABLE channels ADD COLUMN video_defaults_json TEXT NOT NULL DEFAULT '{}'")
            settings_rows = {row["key"]: json.loads(row["value_json"]) for row in db.execute(
                "SELECT key, value_json FROM settings WHERE key IN (?, ?, ?)",
                ("generation-defaults", "generation-defaults:short", "generation-defaults:long"),
            )}
            base = settings_rows.get("generation-defaults", m.default_generation_settings())
            defaults = {
                "short": m.video_profile_from_settings("short", {**base, **m.FORMAT_PRESETS["short"], **settings_rows.get("generation-defaults:short", {})}),
                "long": m.video_profile_from_settings("long", {**base, **m.FORMAT_PRESETS["long"], **settings_rows.get("generation-defaults:long", {})}),
            }
            for profile in defaults.values():
                for field in m.CHANNEL_PROFILE_FIELDS:
                    profile.pop(field, None)
            db.execute("UPDATE channels SET video_defaults_json=?", (json.dumps(defaults),))
        if "visual_guides_json" not in channel_columns:
            db.execute("ALTER TABLE channels ADD COLUMN visual_guides_json TEXT NOT NULL DEFAULT '{}'")
        channel_columns = {column["name"] for column in db.execute("PRAGMA table_info(channels)")}
        for column in ("persona", "visual_style", "image_source", "thumbnail_prompt"):
            if column not in channel_columns:
                db.execute(f"ALTER TABLE channels ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE TABLE IF NOT EXISTS job_reviews (job_id TEXT PRIMARY KEY REFERENCES video_jobs(id), value_json TEXT NOT NULL, created_at TEXT NOT NULL)")
        # Backfill format profiles from the old shared channel fields. Existing values in either
        # format are preserved, so this is safe to run on every startup.
        for row in db.execute("SELECT * FROM channels").fetchall():
            normalized = m.channel_video_defaults(row)
            raw = json.loads(row["video_defaults_json"] or "{}")
            if raw != normalized:
                db.execute("UPDATE channels SET video_defaults_json=? WHERE id=?", (json.dumps(normalized), row["id"]))
