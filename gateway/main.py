"""Local, same-origin gateway for the MoneyPrinterTurbo Cockpit."""
from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import math
import os
import re
import sqlite3
import statistics
import tempfile
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DATA_DIR = Path(os.getenv("APP_DATA_DIR", "runtime"))
DATABASE = DATA_DIR / "cockpit.sqlite3"
TURBO_BASE_URL = os.getenv("TURBO_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
KEY_FILE = os.getenv("MPT_API_KEY_FILE", "")
STATIC_ROOT = Path(__file__).resolve().parents[1]
JOB_STATES = {"draft", "queued", "running", "completed", "approved", "failed", "interrupted", "deleting", "deleted"}
RETRY_BASE_SECONDS = 1.0


def now() -> str:
    return datetime.now(UTC).isoformat()


def as_utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


def api_key() -> str:
    if KEY_FILE and Path(KEY_FILE).is_file():
        return Path(KEY_FILE).read_text(encoding="utf-8").strip()
    return os.getenv("MPT_API_KEY", "")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
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


def initialize_database() -> None:
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
              stats_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
              UNIQUE(platform, external_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operators (
              channel_id TEXT PRIMARY KEY REFERENCES channels(id), enabled INTEGER NOT NULL DEFAULT 0, format TEXT NOT NULL DEFAULT 'short',
              cadence_hours INTEGER NOT NULL DEFAULT 24, next_run_at TEXT, last_run_at TEXT, last_error TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_reviews (
              job_id TEXT PRIMARY KEY REFERENCES video_jobs(id), value_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS video_analytics (
              publication_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL, metrics_json TEXT NOT NULL, score INTEGER NOT NULL
            );
            """
        )
        channel_columns = {column["name"] for column in db.execute("PRAGMA table_info(channels)")}
        for column in ("persona", "visual_style", "image_source", "thumbnail_prompt"):
            if column not in channel_columns:
                db.execute(f"ALTER TABLE channels ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        job_columns = {column["name"] for column in db.execute("PRAGMA table_info(video_jobs)")}
        if "channel_id" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN channel_id TEXT REFERENCES channels(id)")
        if "format" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN format TEXT")
            # Renders from before the long/short split: landscape ones were long-form, the rest Shorts.
            db.execute("UPDATE video_jobs SET format = CASE WHEN json_extract(request_json, '$.video_aspect') = '16:9' THEN 'long' ELSE 'short' END")
        if "created_by" not in job_columns:
            db.execute("ALTER TABLE video_jobs ADD COLUMN created_by TEXT")
        publication_columns = {column["name"] for column in db.execute("PRAGMA table_info(publications)")}
        if "scheduled_at" not in publication_columns:
            db.execute("ALTER TABLE publications ADD COLUMN scheduled_at TEXT")
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
    thumb = thumbnail_path(row["id"])
    result["thumbnail_updated_at"] = thumb.stat().st_mtime if thumb.is_file() else None
    if db:
        result["artifacts"] = [dict(item) for item in db.execute("SELECT * FROM artifacts WHERE job_id = ?", (row["id"],))]
        result["publications"] = [dict(item) for item in db.execute("SELECT * FROM publications WHERE job_id = ?", (row["id"],))]
        metadata = db.execute("SELECT value_json FROM job_metadata WHERE job_id = ?", (row["id"],)).fetchone()
        result["metadata"] = json.loads(metadata["value_json"]) if metadata else None
        review = db.execute("SELECT value_json FROM job_reviews WHERE job_id = ?", (row["id"],)).fetchone()
        result["review"] = json.loads(review["value_json"]) if review else None
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
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=90)) as client:
            response = await send_with_retry(client, method, f"{TURBO_BASE_URL}{path}", retries=retries, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"MoneyPrinterTurbo indisponível: {exc}") from exc
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
    if error or raw in {"failed", "failure", "error"}:
        return "failed", progress, stage, str(error or "Falha no Turbo")
    if raw in {"completed", "complete", "success", "done", "finished"} or payload.get("videos") or payload.get("combined_videos"):
        return "completed", 100, None, None
    if raw in {"queued", "pending", "waiting"}:
        return "queued", progress, None, None
    return "running", progress, None, None


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
    with database() as db:
        db.execute(
            "UPDATE video_jobs SET state=?, progress=?, failed_stage=?, error=?, turbo_payload_json=?, updated_at=?, completed_at=COALESCE(completed_at, ?) WHERE id=?",
            (state, progress, stage, error, json.dumps(payload), now(), completed_at, job_id),
        )
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
        return row_as_job(current, db)


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
            with database() as db:
                db.execute(
                    "UPDATE video_jobs SET state='interrupted', error=COALESCE(error, 'Interrompido por reinício do Gateway'), updated_at=? WHERE id=? AND state IN ('queued','running')",
                    (now(), job_id),
                )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    await reconcile_stale_jobs()
    loop = asyncio.create_task(agent_loop())
    yield
    loop.cancel()


app = FastAPI(title="Moneyprint Cockpit Gateway", version="1.0.0", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    database_ok = DATABASE.exists()
    turbo_ok = False
    try:
        response = await turbo_request("GET", "/ping", retries=0)
        turbo_ok = response.status_code == 200
    except HTTPException:
        pass
    return {"status": "ok" if database_ok and turbo_ok else "degraded", "database": database_ok, "turbo": turbo_ok}


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


VIDEO_SOURCES = {"pexels", "pixabay", "coverr", "ai_images"}  # ai_images = NVIDIA FLUX, one image per narration chunk


@app.get("/api/settings/generation-defaults")
def generation_defaults(format: Literal["long", "short"] | None = None) -> dict[str, Any]:
    """Without format: the shared base edited in Configurações. With format: base, then that
    format's preset, then what its own screen saved with "Salvar padrão"."""
    with database() as db:
        rows = {row["key"]: json.loads(row["value_json"]) for row in db.execute(
            "SELECT key, value_json FROM settings WHERE key IN (?, ?)", ("generation-defaults", settings_key(format))
        )}
    base = rows.get("generation-defaults", default_generation_settings())
    if base.get("video_source") not in VIDEO_SOURCES:
        # Um valor salvo que já não existe cai para o Pexels.
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
        "nvidia_vision_model": _config_string(value, "nvidia_vision_model") or NVIDIA_DEFAULT_VISION_MODEL,
        "nvidia_image_model": _config_string(value, "nvidia_image_model") or NVIDIA_DEFAULT_IMAGE_MODEL,
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
    nvidia_vision_model: str | None = Field(default=None, max_length=200)
    nvidia_image_model: str | None = Field(default=None, max_length=200)
    pexels_api_key: str | None = Field(default=None, max_length=500)
    pixabay_api_key: str | None = Field(default=None, max_length=500)
    coverr_api_key: str | None = Field(default=None, max_length=500)
    upload_post_api_key: str | None = Field(default=None, max_length=500)
    upload_post_username: str | None = Field(default=None, max_length=200)


class ProviderValidation(BaseModel):
    provider: Literal["moonshot", "nvidia", "pexels", "pixabay", "coverr", "upload_post"]
    api_key: str = Field(min_length=1, max_length=500)


MOONSHOT_ENDPOINTS = {"china": "https://api.moonshot.cn/v1", "global": "https://api.moonshot.ai/v1"}
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_TEXT_MODEL = "google/gemma-4-31b-it"
NVIDIA_DEFAULT_VISION_MODEL = "google/diffusiongemma-26b-a4b-it"
# Image generation is a separate NVIDIA host/path from the chat-completions one above.
NVIDIA_GENAI_BASE_URL = "https://ai.api.nvidia.com/v1/genai"
NVIDIA_DEFAULT_IMAGE_MODEL = "black-forest-labs/flux.1-dev"
THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT = 1344, 768  # closest FLUX-supported size to YouTube's 16:9


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
    if input.nvidia_vision_model:
        text = _toml_set(text, "nvidia_vision_model", f'"{_toml_escape(input.nvidia_vision_model)}"')
    if input.nvidia_image_model:
        text = _toml_set(text, "nvidia_image_model", f'"{_toml_escape(input.nvidia_image_model)}"')
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
    return provider_readiness()


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
    payload: dict[str, Any] = {"video_subject": subject.strip(), "video_script": script.strip()}
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


def persona_prompt(persona: str, extra: str = "") -> str:
    """The channel's standing identity goes first, then this video's own requirements."""
    persona = persona.strip()
    return (f"Identidade do canal, siga em tudo o que escrever: {persona}. " if persona else "") + extra.strip()


def job_max_words(format: str, settings: dict[str, Any]) -> int | None:
    """The narration budget the screen asked for: the Short's "Duração alvo" select, or the long
    video's target in minutes. None means no target was sent, so nothing is trimmed."""
    if format == "short":
        return short_script_max_words(int(settings.get("paragraph_number", 5) or 5))
    try:
        minutes = float(settings.get("target_minutes") or 0)
    except (TypeError, ValueError):
        return None
    return int(minutes * WORDS_PER_MINUTE) or None


@app.post("/api/projects/{project_id}/jobs", status_code=202)
async def create_job(project_id: str, input: JobCreate) -> dict[str, Any]:
    project = row_as_project(get_project_or_404(project_id))
    with database() as db:
        channels = [channel_row_or_404(db, channel_id) for channel_id in dict.fromkeys(input.channel_ids)]
    # Checked before Turbo starts a render that could then never be published where it was meant to go.
    validate_publish_channels(channels, input.format)
    merged = {**generation_defaults(input.format), **project["defaults"], **input.settings, **FORMAT_LOCKED[input.format]}
    # Turbo's encoder defaults to 2 threads; this container sees the same Docker VM CPUs as Turbo.
    merged.setdefault("n_threads", os.cpu_count() or 2)
    # The channel's own choice of image source applies unless the screen named one explicitly.
    channel_source = channels[0]["image_source"] if channels else ""
    if channel_source and "video_source" not in input.settings:
        if channel_source == "ai_images":
            merged["video_source"] = "ai_images"
        elif merged.get("video_source") == "ai_images":  # "stock": leave a stock default alone, replace an AI one
            merged["video_source"] = "pexels"
    # ponytail: one script per job, so the first channel's identity wins; merge personas if that ever matters.
    persona, visual_style = (channels[0]["persona"], channels[0]["visual_style"]) if channels else ("", "")
    merged["video_script_prompt"] = persona_prompt(persona, str(merged.get("video_script_prompt", "")))
    script_text = input.script.strip()
    ai_images = merged.get("video_source") == "ai_images"
    if ai_images and not config_value("nvidia_api_key"):
        raise HTTPException(status_code=409, detail="Configure a chave NVIDIA em Configurações para gerar vídeos com imagens de IA.")
    follow_script = merged.get("video_concat_mode") == "script"
    if follow_script or ai_images:
        # "Seguir o roteiro": one visual search term per script chunk, downloaded in that
        # order, instead of Turbo's handful of generic terms shared across the whole video
        # (see visual_terms()) - that's what made footage look unrelated to the narration.
        merged["match_materials_to_script"], merged["video_concat_mode"] = True, "sequential"
        if not script_text and input.format == "short":
            script_response = await turbo_request("POST", "/api/v1/scripts", json={
                "video_subject": input.subject, "video_language": merged.get("video_language", "pt-BR"),
                "paragraph_number": merged.get("paragraph_number", 5),
                "video_script_prompt": short_script_prompt(str(merged.get("video_script_prompt", "")), int(merged.get("paragraph_number", 5) or 5)),
                "custom_system_prompt": merged.get("custom_system_prompt", ""),
            })
            script_text = str((unwrap(script_response.json()) or {}).get("video_script", "")).strip()
    # The chosen duration wins over whatever was written, pasted or generated, in every mode: a
    # longer script would stretch the narration past it, and the visuals are cut to fit the script.
    max_words = job_max_words(input.format, merged)
    if script_text and max_words:
        script_text = trim_to_words(script_text, max_words)
    if ai_images:
        if not script_text:
            raise HTTPException(status_code=409, detail="Escreva ou gere o roteiro antes de gerar o vídeo com imagens de IA.")
        materials, clip = await ai_image_materials(input.subject, script_text, input.format, float(merged.get("video_clip_duration") or 5), float(merged.get("voice_rate") or 1), visual_style)
        merged.pop("video_terms", None)
        merged.update(video_source="local", video_materials=materials, video_clip_duration=clip, match_materials_to_script=False)
    elif follow_script and script_text and not str(merged.get("video_terms", "")).strip():
        terms, clip_seconds = await visual_terms(input.subject, script_text, float(merged.get("video_clip_duration") or 5), float(merged.get("voice_rate") or 1), visual_style)
        merged["video_terms"], merged["video_clip_duration"] = ", ".join(terms), max(1, round(clip_seconds))
    request_payload = build_turbo_payload(input.subject, script_text, merged)
    response = await turbo_request("POST", "/api/v1/videos", json=request_payload)
    turbo_result = unwrap(response.json())
    if not isinstance(turbo_result, dict) or not turbo_result.get("task_id"):
        raise HTTPException(status_code=502, detail="Resposta inválida do MoneyPrinterTurbo ao criar tarefa")
    job_id, timestamp = str(uuid4()), now()
    with database() as db:
        db.execute(
            "INSERT INTO video_jobs(id, project_id, turbo_task_id, subject, script, request_json, state, created_at, updated_at, format) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (job_id, project_id, turbo_result["task_id"], input.subject.strip(), script_text, json.dumps(request_payload), timestamp, timestamp, input.format),
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
    request = json.loads(row["request_json"])
    response = await turbo_request("POST", "/api/v1/videos", json=request)
    created = unwrap(response.json())
    task_id = created.get("task_id") if isinstance(created, dict) else None
    if not task_id:
        raise HTTPException(status_code=502, detail="O Turbo não retornou task_id")
    with database() as db:
        db.execute("UPDATE video_jobs SET turbo_task_id=?, state='queued', progress=0, error=NULL, failed_stage=NULL, updated_at=? WHERE id=?", (task_id, now(), job_id))
        return row_as_job(db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), db)


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


async def llm_chat(prompt: str, json_mode: bool = False) -> str:
    provider, base_url, key, model = llm_config()
    label = "Moonshot/Kimi" if provider == "moonshot" else "NVIDIA"
    if not key:
        raise HTTPException(status_code=409, detail=f"Configure a chave {label} em Configurações.")
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    # response_format support varies across NIM models; the prompts already ask for plain JSON
    # and parse_json_object() tolerates prose or a code fence around it.
    if json_mode and provider == "moonshot":
        body["response_format"] = {"type": "json_object"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=180)) as client:
            response = await send_with_retry(client, "POST", f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"{label} indisponível: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"{label} respondeu {response.status_code}: {response.text[:300]}")
    try:
        return response.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"O {label} devolveu uma resposta inesperada") from exc


def parse_json_object(content: str) -> dict[str, Any]:
    """Kimi sometimes wraps the JSON in prose or a code fence; take the outermost object."""
    try:
        parsed = json.loads(content[content.index("{"): content.rindex("}") + 1])
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="A IA não devolveu JSON válido") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="A IA não devolveu JSON válido")
    return parsed


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


def short_script_max_words(paragraph_number: int) -> int:
    seconds = SHORT_TARGET_SECONDS.get(paragraph_number, max(SHORT_TARGET_SECONDS.values()))
    return round(seconds * WORDS_PER_MINUTE / 60)


def short_script_min_words() -> int:
    return round(SHORT_MIN_SECONDS * WORDS_PER_MINUTE / 60)


def short_script_prompt(script_prompt: str, paragraph_number: int) -> str:
    """Word-range rule Turbo's writer must hit (60–90 s of narration). Rides along with any
    custom requirements instead of replacing them."""
    max_words, min_words = short_script_max_words(paragraph_number), short_script_min_words()
    length_rule = f"O roteiro deve ter entre {min_words} e {max_words} palavras (narração de 60 a 90 segundos)."
    custom = (script_prompt or "").strip()
    return f"{custom}\n{length_rule}".strip() if custom else length_rule


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
    script_prompt: str = Field(default="", max_length=2000)
    system_prompt: str = Field(default="", max_length=4000)
    persona: str = Field(default="", max_length=2000)


@app.post("/api/scripts/short")
async def short_script(input: ShortScriptInput) -> dict[str, Any]:
    max_words, min_words = short_script_max_words(input.paragraph_number), short_script_min_words()
    # Turbo's writer only gets paragraph_number, so it often undershoots (e.g. 139 words ≈ 56 s
    # for a 60–90 s Short). State the word range explicitly in the prompt it already reads.
    response = await turbo_request("POST", "/api/v1/scripts", json={
        "video_subject": input.subject, "video_language": clean_language(input.language),
        "paragraph_number": input.paragraph_number,
        "video_script_prompt": persona_prompt(input.persona, short_script_prompt(input.script_prompt, input.paragraph_number)),
        "custom_system_prompt": input.system_prompt,
    })
    script = str((unwrap(response.json()) or {}).get("video_script", "")).strip()
    trimmed = trim_to_words(script, max_words)
    words = len(trimmed.split())
    return {"script": trimmed, "words": words, "trimmed": trimmed != script,
            "too_short": words < min_words, "min_words": min_words}


class LongScriptInput(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    language: str = Field(default="pt-BR", max_length=20)
    minutes: int = Field(default=8, ge=5, le=15)
    niche: str = Field(default="", max_length=200)
    requirements: str = Field(default="", max_length=2000)
    system_prompt: str = Field(default="", max_length=4000)
    persona: str = Field(default="", max_length=2000)


def clean_narration(text: str) -> str:
    """Kimi still slips in headings, bullets or bold now and then; the TTS would read the symbols aloud."""
    lines = (re.sub(r"^\s*(#+|[-*•]|\d+[.)])\s+", "", line).replace("**", "").strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


@app.post("/api/scripts/long")
async def long_script(input: LongScriptInput) -> dict[str, Any]:
    """Turbo's own script endpoint stops at 10 paragraphs (~2-4 min), so long videos are written here
    in sections: one outline call, then one call per section that continues from the previous ending.
    ponytail: synchronous (a few minutes for 15 min); stream progress over SSE if the wait becomes a problem."""
    language = clean_language(input.language)
    section_count = max(4, round(input.minutes / 1.5))
    words_per_section = input.minutes * WORDS_PER_MINUTE // section_count
    requirements = input.requirements.strip()
    system_prompt = input.system_prompt.strip()
    # ponytail: unlike Turbo's own custom_system_prompt (a full replacement), this can't replace the
    # whole instruction set here - the outline call must stay valid JSON and each section call must
    # stay narration-only, or the multi-step pipeline breaks. It's folded in as an extra, higher-weight
    # instruction on every call instead, same mechanism as `requirements` but framed as a standing rule.
    style = (f" Instruções de estilo do sistema, siga em tudo o que escrever: {system_prompt}." if system_prompt else "")
    style = f" {persona_prompt(input.persona)}" + style if input.persona.strip() else style
    outline = parse_json_object(await llm_chat(
        f"Planeje um vídeo de YouTube de {input.minutes} minutos no idioma {language} sobre: {input.subject}."
        + (f" Nicho do canal: {input.niche}." if input.niche else "")
        + (f" Requisitos personalizados: {requirements}." if requirements else "") + style
        + f' Responda apenas JSON: {{"title": título do vídeo, "sections": lista com {section_count} itens '
        '{"heading": título da seção, "points": lista de 2 a 4 pontos}}. '
        "A primeira seção é um gancho de abertura e a última uma conclusão com chamada para se inscrever.",
        json_mode=True,
    ))
    sections = [section for section in outline.get("sections") or [] if isinstance(section, dict) and section.get("heading")][:section_count]
    if not sections:
        raise HTTPException(status_code=502, detail="A IA não devolveu o roteiro em seções")
    parts: list[str] = []
    for index, section in enumerate(sections, start=1):
        previous = f"\nFinal da seção anterior (continue a partir dele, sem repetir): {parts[-1][-600:]}" if parts else ""
        parts.append(clean_narration(await llm_chat(
            f"Escreva a narração da seção {index} de {len(sections)} de um vídeo de YouTube no idioma {language} sobre: {input.subject}.\n"
            f"Seção: {section['heading']}\nPontos: {'; '.join(map(str, section.get('points') or []))}\n"
            f"Cerca de {words_per_section} palavras. Somente o texto falado, em parágrafos corridos: sem títulos, marcadores, markdown, emojis nem indicações de cena."
            + (f" Requisitos personalizados: {requirements}." if requirements else "") + style + previous
        )))
    script = "\n\n".join(part for part in parts if part)
    trimmed_script = trim_to_words(script, input.minutes * WORDS_PER_MINUTE)
    words = len(trimmed_script.split())
    return {
        "title": str(outline.get("title") or input.subject), "script": trimmed_script, "trimmed": trimmed_script != script,
        "sections": [str(section["heading"]) for section in sections], "words": words, "estimated_minutes": round(words / WORDS_PER_MINUTE, 1),
    }


def split_script(script: str, parts: int) -> list[str]:
    """Splits narration into `parts` contiguous, roughly-equal chunks by word count, preserving
    order and every word - used to line up one visual search term per script chunk."""
    words = script.split()
    if parts <= 1 or not words:
        return [script.strip()] if script.strip() else []
    parts = min(parts, len(words))
    chunk = -(-len(words) // parts)  # ceil division without importing math
    return [" ".join(words[i:i + chunk]) for i in range(0, len(words), chunk)]


def narration_seconds(script: str, voice_rate: float = 1.0) -> float:
    return len(script.split()) / (WORDS_PER_MINUTE / 60) / max(voice_rate, 0.1)


MAX_VISUAL_TERMS = 80  # ponytail: cap on Pexels searches per video; raise alongside the API's hourly quota


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


async def visual_terms(subject: str, script: str, clip_seconds: float, voice_rate: float = 1.0, visual_style: str = "") -> tuple[list[str], float]:
    """One stock-footage search term per script chunk, in narration order. Combined with Turbo's
    match_materials_to_script + sequential mode, this makes the footage follow what's being said
    instead of cycling a handful of subject-wide terms across the whole video."""
    seconds = narration_seconds(script, voice_rate)
    slots = max(1, -(-int(seconds) // max(int(clip_seconds), 1)))
    count = min(slots, MAX_VISUAL_TERMS)
    effective_clip_seconds = clip_seconds if slots <= MAX_VISUAL_TERMS else seconds / MAX_VISUAL_TERMS
    chunks = split_script(script, count) or [subject]
    numbered = "\n".join(f"{index + 1}. {chunk}" for index, chunk in enumerate(chunks))
    generated = parse_json_object(await llm_chat(
        f"Tema do vídeo: {subject}\nTrechos numerados da narração, na ordem em que aparecem:\n{numbered}\n"
        "Para cada trecho, gere uma busca de banco de vídeos em INGLÊS (1 a 3 palavras) que mostre algo filmável e "
        "concreto do que é dito naquele trecho: uma cena, objeto, ação ou lugar. Nada abstrato, sem nomes próprios, "
        "sem texto na tela. Os termos devem ser diferentes entre si - não repita o tema geral em todos.\n"
        + (f"Estilo visual do canal, escolha filmagens compatíveis com ele: {visual_style}.\n" if visual_style.strip() else "") +
        f'Responda apenas JSON: {{"terms": lista com exatamente {len(chunks)} strings, na mesma ordem dos trechos}}',
        json_mode=True,
    ))
    raw_terms = [str(term).strip() for term in generated.get("terms") or [] if str(term).strip()]
    return _fill_visual_terms(raw_terms, len(chunks)), effective_clip_seconds


# High enough that "Duração de cada clipe" is honoured even on a 15-minute video (180 images at 5 s);
# ponytail: it is a safety stop against a runaway NVIDIA bill, not a quality setting.
MAX_AI_IMAGES = 200
AI_IMAGE_SIZES = {"short": (768, 1344), "long": (1344, 768)}  # FLUX.1-dev accepts 768-1344 in steps of 64


async def ai_image_materials(subject: str, script: str, format: str, clip_seconds: float, voice_rate: float = 1.0, visual_style: str = "") -> tuple[list[dict[str, Any]], int]:
    """One FLUX image per narration chunk, uploaded to Turbo as local materials in narration order.
    Turbo turns each image into a slow-zoom clip (video_source="local")."""
    seconds = narration_seconds(script, voice_rate)
    count = max(1, min(math.ceil(seconds / max(clip_seconds, 1)), MAX_AI_IMAGES))
    clip = max(1, math.ceil(seconds / count))  # rounded up so the images cover the whole narration; Turbo trims the rest
    chunks = split_script(script, count) or [subject]
    numbered = "\n".join(f"{index + 1}. {chunk}" for index, chunk in enumerate(chunks))
    generated = parse_json_object(await llm_chat(
        f"Tema do vídeo: {subject}\nTrechos numerados da narração, na ordem em que aparecem:\n{numbered}\n"
        "Cada trecho vira uma imagem gerada por IA, e as imagens em sequência contam a história do vídeo. "
        + (f'O estilo visual do canal é obrigatório: use-o como base de "style": {visual_style.strip()}.\n' if visual_style.strip() else "") +
        'Defina um estilo visual único para todas ("style", em INGLÊS: técnica, paleta, iluminação) e, para cada trecho, '
        "descreva em INGLÊS uma cena concreta que ilustre o que é dito (sujeito, ação, lugar, enquadramento), sem texto na imagem.\n"
        f'Responda apenas JSON: {{"style": string, "scenes": lista com exatamente {len(chunks)} strings, na mesma ordem dos trechos}}',
        json_mode=True,
    ))
    style = str(generated.get("style") or "cinematic digital illustration, rich colors").strip()
    scenes = _fill_visual_terms([str(scene).strip() for scene in generated.get("scenes") or [] if str(scene).strip()], len(chunks))
    width, height = AI_IMAGE_SIZES[format]
    batch, limit = uuid4().hex, asyncio.Semaphore(4)

    async def render(index: int, scene: str) -> str | None:
        async with limit:
            for attempt in range(2):  # one retry: NIM occasionally times out a single image under a burst
                try:
                    image = await generate_image(f"{style}. {scene}. No text, no letters.", width, height)
                    response = await turbo_request("POST", "/api/v1/video_materials", files={"file": (f"{batch}-{index:03}.jpg", image, "image/jpeg")})
                    return str((unwrap(response.json()) or {}).get("file") or "") or None
                except HTTPException as exc:
                    logger.warning("AI image %s attempt %s failed: %s", index, attempt + 1, exc.detail)
            return None

    files = await asyncio.gather(*(render(index, scene) for index, scene in enumerate(scenes)))
    good = [index for index, file in enumerate(files) if file]
    if not good:
        raise HTTPException(status_code=502, detail="Nenhuma imagem pôde ser gerada pela NVIDIA. Tente de novo ou use filmagens de banco.")
    # A refused scene borrows its nearest good neighbour, so the rest stay aligned with the narration.
    filled = [files[index] or files[min(good, key=lambda g: abs(g - index))] for index in range(len(files))]
    return [{"provider": "local", "url": file, "duration": clip} for file in filled], clip


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
        if not db.execute("SELECT 1 FROM artifacts WHERE job_id=? AND kind='video'", (job_id,)).fetchone():
            raise HTTPException(status_code=409, detail="O vídeo final não foi encontrado")
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        if not stored or normalize_youtube_metadata(json.loads(stored["value_json"]))[1]:
            raise HTTPException(status_code=409, detail="Defina título, descrição e tags válidos antes de aprovar")
        db.execute("UPDATE video_jobs SET state='approved', updated_at=? WHERE id=?", (now(), job_id))
        return row_as_job(job_row_or_404(db, job_id), db)


# youtube.readonly is what lets the callback find out which channel was picked on Google's consent screen.
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly", "https://www.googleapis.com/auth/yt-analytics.readonly"]
LANGUAGE_PATTERN = r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*"
UPLOAD_POST_BASE = "https://api.upload-post.com"
PLATFORM_LABELS = {"youtube": "YouTube Studio", "tiktok": "TikTok"}
# ponytail: in-memory OAuth state; a Gateway restart mid-consent just means clicking "Conectar" again.
oauth_flows: dict[str, tuple[Any, dict[str, str]]] = {}


class PublishRejected(Exception):
    """The platform answered 4xx: the upload definitely did not create a post, so a retry is safe."""


def youtube_client_file() -> Path:
    return DATA_DIR / "secrets" / "youtube_client_secret.json"


def youtube_token_file(channel_id: str) -> Path:
    return DATA_DIR / "secrets" / "youtube_tokens" / f"{channel_id}.json"


def clean_language(value: str) -> str:
    return value if re.fullmatch(LANGUAGE_PATTERN, value or "") else "pt-BR"


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
    result = {key: row[key] for key in ("id", "platform", "name", "handle", "external_id", "status", "niche", "language", "persona", "visual_style", "image_source", "thumbnail_prompt", "created_at", "updated_at")}
    result["stats"] = json.loads(row["stats_json"])
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
    persona: str | None = Field(default=None, max_length=2000)
    visual_style: str | None = Field(default=None, max_length=1000)
    image_source: Literal["", "stock", "ai_images"] | None = None  # "" = the Configurações default
    # 1000, not 2000: generate_image cuts the prompt at 2000 characters, from the end, and the video topic goes last.
    thumbnail_prompt: str | None = Field(default=None, max_length=1000)


class ChannelConnect(BaseModel):
    niche: str = Field(default="", max_length=200)
    language: str = Field(default="pt-BR", max_length=20)


@app.patch("/api/channels/{channel_id}")
def patch_channel(channel_id: str, input: ChannelPatch) -> dict[str, Any]:
    with database() as db:
        channel_row_or_404(db, channel_id)
    fields = input.model_dump(exclude_none=True)
    if "language" in fields:
        fields["language"] = clean_language(fields["language"])
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
def youtube_connect(request: Request, niche: str = "", language: str = "pt-BR") -> Any:
    from google_auth_oauthlib.flow import Flow

    if not youtube_client_file().is_file():
        return HTMLResponse("<p>Coloque o OAuth client do Google Cloud em <code>runtime/secrets/youtube_client_secret.json</code> e tente de novo.</p>", status_code=409)
    flow = Flow.from_client_secrets_file(str(youtube_client_file()), scopes=YOUTUBE_SCOPES, redirect_uri=str(request.url_for("youtube_auth_callback")))
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    oauth_flows[state] = (flow, {"niche": niche[:200], "language": clean_language(language)})
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


def save_youtube_channel(info: dict[str, Any], options: dict[str, str]) -> str:
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
        channel_id = str(uuid4())
        db.execute(
            "INSERT INTO channels(id, platform, name, handle, external_id, status, niche, language, stats_json, created_at, updated_at) VALUES (?, 'youtube', ?, ?, ?, 'connected', ?, ?, ?, ?, ?)",
            (channel_id, info["name"], info["handle"], info["external_id"], options.get("niche", ""), options.get("language", "pt-BR"), json.dumps(info["stats"]), now(), now()),
        )
        return channel_id


@app.get("/api/youtube/auth/callback", name="youtube_auth_callback")
def youtube_auth_callback(request: Request, state: str = "", error: str = "") -> HTMLResponse:
    pending = oauth_flows.pop(state, None)
    if error or not pending:
        return HTMLResponse(f"<p>Autorização do YouTube não concluída: {html.escape(error or 'sessão expirada, tente de novo')}.</p>", status_code=400)
    flow, options = pending
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
    username = f"mp-{uuid4().hex[:16]}"
    await upload_post_request("POST", "/api/uploadposts/users", json={"username": username})
    access_url = await upload_post_connect_link(username)
    channel_id = str(uuid4())
    with database() as db:
        db.execute(
            "INSERT INTO channels(id, platform, name, handle, external_id, status, niche, language, created_at, updated_at) VALUES (?, 'tiktok', 'TikTok (aguardando login)', '', ?, 'pending', ?, ?, ?, ?)",
            (channel_id, username, input.niche.strip(), clean_language(input.language), now(), now()),
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


def youtube_upload(credentials: Any, video_path: Path, metadata: dict[str, Any], thumbnail_file: Path | None = None) -> str:
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
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True},
    }
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
    video_id = response["id"]
    if thumbnail_file and thumbnail_file.is_file():
        # A custom thumbnail is a nice-to-have on top of an already-published video - never fail
        # the publish over it (e.g. the channel isn't phone-verified, which YouTube requires for this).
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_file), mimetype="image/jpeg")).execute()
        except HttpError as exc:
            logger.warning("Não foi possível definir a thumbnail do vídeo %s: %s", video_id, exc)
    return video_id


async def download_turbo_file(remote_path: str, target: Path) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=None)) as client:
        async with client.stream("GET", f"{TURBO_BASE_URL}/api/v1/download/{remote_path}", headers=turbo_headers()) as response:
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail="O Turbo não encontrou o artefato")
            with target.open("wb") as file:
                async for chunk in response.aiter_bytes():
                    file.write(chunk)


REVIEW_FRAMES = 8  # calibration knob: frames sampled across the video for the vision-model review


async def run_ffmpeg(*args: str) -> None:
    process = await asyncio.create_subprocess_exec("ffmpeg", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise HTTPException(status_code=502, detail=f"ffmpeg falhou: {stderr.decode(errors='replace')[-500:]}")


async def extract_review_frames(video_path: Path, duration: float, count: int, out_dir: Path) -> list[Path]:
    frames = []
    for index in range(count):
        timestamp = max(0.0, duration * (index + 0.5) / count)  # middle of each equal slice
        frame_path = out_dir / f"frame-{index}.jpg"
        await run_ffmpeg("-y", "-ss", f"{timestamp:.2f}", "-i", str(video_path), "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "5", str(frame_path))
        frames.append(frame_path)
    return frames


async def nvidia_chat(content: list[dict[str, Any]], model: str) -> str:
    key = config_value("nvidia_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure a chave NVIDIA em Configurações.")
    body = {"model": model, "messages": [{"role": "user", "content": content}], "chat_template_kwargs": {"enable_thinking": False}, "max_tokens": 2048}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=180)) as client:
            response = await send_with_retry(client, "POST", f"{NVIDIA_BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"NVIDIA indisponível: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"NVIDIA respondeu {response.status_code}: {response.text[:300]}")
    try:
        return response.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="A NVIDIA devolveu uma resposta inesperada") from exc


def normalize_review(parsed: dict[str, Any], frame_count: int) -> dict[str, Any]:
    frames_result = []
    for item in parsed.get("frames") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= index < frame_count):
            continue
        frames_result.append({"index": index, "matches": bool(item.get("matches")), "reason": str(item.get("reason") or "")[:300]})
    return {"frames": frames_result, "summary": str(parsed.get("summary") or "")[:1000]}


@app.post("/api/jobs/{job_id}/review")
async def review_job(job_id: str) -> dict[str, Any]:
    """Only ever advisory: never blocks approval, and doesn't touch cuts/terms automatically."""
    with database() as db:
        job = job_row_or_404(db, job_id)
        artifact = db.execute("SELECT * FROM artifacts WHERE job_id=? AND kind='video' ORDER BY created_at LIMIT 1", (job_id,)).fetchone()
    if job["state"] not in METADATA_STATES:
        raise HTTPException(status_code=409, detail="Só é possível revisar vídeos concluídos")
    if not artifact:
        raise HTTPException(status_code=409, detail="O vídeo final não foi encontrado")
    if not config_value("nvidia_api_key"):
        raise HTTPException(status_code=409, detail="Configure a chave NVIDIA em Configurações para revisar imagens.")
    vision_model = config_value("nvidia_vision_model") or NVIDIA_DEFAULT_VISION_MODEL
    turbo_payload = json.loads(job["turbo_payload_json"])
    script = job["script"] or turbo_payload.get("script") or ""
    audio_duration = float(turbo_payload.get("audio_duration") or 0) or 30.0
    chunks = split_script(script, REVIEW_FRAMES) if script.strip() else []
    with tempfile.TemporaryDirectory() as folder:
        video_path = Path(folder) / "video.mp4"
        await download_turbo_file(artifact["remote_path"], video_path)
        frames = await extract_review_frames(video_path, audio_duration, REVIEW_FRAMES, Path(folder))
        content: list[dict[str, Any]] = [{"type": "text", "text": (
            "Você está revisando um vídeo gerado automaticamente. Para cada imagem a seguir, diga se ela combina "
            "com o trecho do roteiro narrado naquele momento (cena, objeto ou lugar relacionado). Responda apenas "
            'JSON: {"frames": [{"index": número do quadro, "matches": true ou false, "reason": motivo curto}], "summary": resumo curto geral}'
        )}]
        for index, frame_path in enumerate(frames):
            excerpt = chunks[index] if index < len(chunks) else ""
            content.append({"type": "text", "text": f"Quadro {index}, trecho narrado: {excerpt}" if excerpt else f"Quadro {index}"})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(frame_path.read_bytes()).decode('ascii')}"}})
        value = normalize_review(parse_json_object(await nvidia_chat(content, vision_model)), len(frames))
    with database() as db:
        db.execute(
            "INSERT INTO job_reviews(job_id, value_json, created_at) VALUES (?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET value_json=excluded.value_json, created_at=excluded.created_at",
            (job_id, json.dumps(value), now()),
        )
        return row_as_job(job_row_or_404(db, job_id), db)


def thumbnail_path(job_id: str) -> Path:
    return DATA_DIR / "thumbnails" / f"{job_id}.jpg"


async def generate_image(prompt: str, width: int = THUMBNAIL_WIDTH, height: int = THUMBNAIL_HEIGHT) -> bytes:
    key = config_value("nvidia_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure a chave NVIDIA em Configurações para gerar imagens.")
    model = config_value("nvidia_image_model") or NVIDIA_DEFAULT_IMAGE_MODEL
    body = {"prompt": prompt[:2000], "width": width, "height": height, "steps": 30, "cfg_scale": 3.5}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120)) as client:
            response = await send_with_retry(client, "POST", f"{NVIDIA_GENAI_BASE_URL}/{model}", headers={"Authorization": f"Bearer {key}"}, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"NVIDIA indisponível: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"NVIDIA respondeu {response.status_code}: {response.text[:300]}")
    try:
        artifact = response.json()["artifacts"][0]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="A NVIDIA devolveu uma resposta inesperada") from exc
    if artifact.get("finishReason") != "SUCCESS":
        raise HTTPException(status_code=502, detail=f"A NVIDIA recusou a imagem: {artifact.get('finishReason')}")
    return base64.b64decode(artifact["base64"])


class ThumbnailInput(BaseModel):
    prompt: str = Field(default="", max_length=1000)  # this thumbnail's own idea; replaces only the default wording
    channel_id: str | None = None  # whose thumbnail prompt and style apply; default: the job's YouTube channel


@app.post("/api/jobs/{job_id}/thumbnail")
async def create_thumbnail(job_id: str, input: ThumbnailInput | None = None) -> dict[str, Any]:
    input = input or ThumbnailInput()
    with database() as db:
        job = job_row_or_404(db, job_id)
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        channel = channel_row_or_404(db, input.channel_id) if input.channel_id else db.execute(
            "SELECT channels.* FROM publications JOIN channels ON channels.id = publications.channel_id "
            "WHERE publications.job_id=? AND publications.platform='youtube'", (job_id,),
        ).fetchone()
    if job["state"] not in METADATA_STATES:
        raise HTTPException(status_code=409, detail="Só é possível gerar thumbnail para vídeos concluídos")
    title = json.loads(stored["value_json"]).get("title") if stored else None
    # The channel's standing rules always apply; only the wording after them is replaceable.
    rules, style = (channel["thumbnail_prompt"].strip(), channel["visual_style"].strip()) if channel else ("", "")
    head = " ".join(part for part in (rules, f"Visual style: {style}." if style else "") if part)
    body = input.prompt.strip() or f"YouTube thumbnail, bold eye-catching composition, cinematic lighting, no text or letters, about: {title or job['subject']}"
    prompt = f"{head} {body}".strip()
    image = await generate_image(prompt)
    path = thumbnail_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image)
    with database() as db:
        return row_as_job(job_row_or_404(db, job_id), db)


@app.get("/api/jobs/{job_id}/thumbnail")
def get_thumbnail(job_id: str) -> FileResponse:
    path = thumbnail_path(job_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Nenhuma thumbnail gerada ainda")
    return FileResponse(path, media_type="image/jpeg")


async def tiktok_upload(username: str, video_path: Path, metadata: dict[str, Any]) -> tuple[str, str]:
    """Returns (external_id, state): Upload-Post normally answers async with a request_id
    ("processing", reconciled later); a synchronous answer is already the final post."""
    hashtags = " ".join("#" + re.sub(r"\s+", "", tag) for tag in metadata["tags"])
    caption = "\n\n".join(part for part in (metadata["title"], metadata["description"], hashtags) if part)[:2200]
    form = {"user": username, "platform[]": "tiktok", "title": caption, "privacy_level": "SELF_ONLY", "is_aigc": "true", "async_upload": "true"}
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


async def publish_to_channel(job_id: str, channel: sqlite3.Row, video: Any, metadata: dict[str, Any], confirm_not_uploaded: bool) -> None:
    """`video` is an async callable returning the local MP4, so the download happens only after a claim succeeds."""
    platform, label = channel["platform"], PLATFORM_LABELS[channel["platform"]]
    # A "reconciliation_required" upload may already be live; only the operator can say it isn't.
    blocked = ("uploading", "processing", "published") + (() if confirm_not_uploaded else ("reconciliation_required",))
    with database() as db:
        # Atomic claim, so a double click or a second tab can't start a parallel upload.
        claimed = db.execute(
            "INSERT INTO publications(id, job_id, platform, channel_id, state, attempts, created_at, updated_at) VALUES (?, ?, ?, ?, 'uploading', 1, ?, ?) "
            "ON CONFLICT(job_id, platform) DO UPDATE SET channel_id=excluded.channel_id, state='uploading', attempts=publications.attempts+1, error=NULL, updated_at=excluded.updated_at "
            f"WHERE publications.state NOT IN ({','.join('?' * len(blocked))})",
            (str(uuid4()), job_id, platform, channel["id"], now(), now(), *blocked),
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
    except BaseException as exc:  # nothing has reached the platform yet, so a plain retry stays safe
        message = str(getattr(exc, "detail", exc))[:500] or "Envio cancelado"
        finish("failed", error=message)
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(status_code=502, detail=f"{channel['name']}: não foi possível preparar o envio: {message}") from exc
    try:
        # ponytail: uploads run inside the request, one channel after another; move to a background task if long videos hit browser/proxy timeouts.
        if platform == "youtube":
            external_id, state = await asyncio.to_thread(youtube_upload, credentials, video_path, metadata, thumbnail_path(job_id)), "published"
        else:
            external_id, state = await tiktok_upload(channel["external_id"], video_path, metadata)
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
            "SELECT p.job_id, p.channel_id, p.platform, p.scheduled_at, jobs.subject, channels.name AS channel_name, json_extract(meta.value_json, '$.title') AS title "
            "FROM publications AS p JOIN video_jobs AS jobs ON jobs.id = p.job_id LEFT JOIN channels ON channels.id = p.channel_id "
            "LEFT JOIN job_metadata AS meta ON meta.job_id = jobs.id WHERE p.state='scheduled' AND jobs.deleted_at IS NULL ORDER BY p.scheduled_at"
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


@app.get("/api/agents/operators")
def list_operators() -> dict[str, Any]:
    with database() as db:
        operators = [
            {"channel_id": row["id"], "channel_name": row["name"], "platform": row["platform"], "enabled": bool(row["enabled"]),
             "format": row["format"] or "short", "cadence_hours": row["cadence_hours"] or 24,
             "next_run_at": row["next_run_at"], "last_run_at": row["last_run_at"], "last_error": row["last_error"]}
            for row in db.execute(
                "SELECT channels.id, channels.name, channels.platform, operators.enabled, operators.format, operators.cadence_hours, "
                "operators.next_run_at, operators.last_run_at, operators.last_error FROM channels LEFT JOIN operators ON operators.channel_id = channels.id "
                "WHERE channels.status='connected' AND channels.deleted_at IS NULL ORDER BY channels.created_at"
            )
        ]
        awaiting = [dict(row) for row in db.execute(
            "SELECT id, subject, format, completed_at FROM video_jobs WHERE created_by='operator' AND state='completed' AND deleted_at IS NULL ORDER BY completed_at DESC"
        )]
    return {"operators": operators, "awaiting_approval": awaiting}


@app.put("/api/agents/operators/{channel_id}")
def put_operator(channel_id: str, input: OperatorInput) -> dict[str, Any]:
    with database() as db:
        channel = channel_row_or_404(db, channel_id)
        if channel["status"] != "connected":
            raise HTTPException(status_code=409, detail="O canal não está conectado. Reconecte em Canais.")
        if channel["platform"] != "youtube" and input.format == "long":
            raise HTTPException(status_code=409, detail="TikTok recebe apenas Shorts.")
        next_run = as_utc(input.next_run_at) if input.next_run_at else datetime.now(UTC) + timedelta(hours=input.cadence_hours)
        db.execute(
            "INSERT INTO operators(channel_id, enabled, format, cadence_hours, next_run_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET enabled=excluded.enabled, format=excluded.format, cadence_hours=excluded.cadence_hours, next_run_at=excluded.next_run_at, updated_at=excluded.updated_at",
            (channel_id, int(input.enabled), input.format, input.cadence_hours, next_run.isoformat(), now()),
        )
    return next(operator for operator in list_operators()["operators"] if operator["channel_id"] == channel_id)


@app.post("/api/agents/operators/{channel_id}/run")
async def run_operator(channel_id: str) -> dict[str, Any]:
    """Starts one video for the channel and stops there: the loop writes its metadata, but approving
    and publishing stay with the user."""
    with database() as db:
        operator = db.execute("SELECT * FROM operators WHERE channel_id=?", (channel_id,)).fetchone()
        channel = db.execute("SELECT * FROM channels WHERE id=? AND deleted_at IS NULL", (channel_id,)).fetchone()
        busy = db.execute(
            "SELECT 1 FROM video_jobs AS jobs JOIN publications ON publications.job_id = jobs.id "
            "WHERE publications.channel_id=? AND jobs.created_by='operator' AND jobs.state IN ('queued', 'running') AND jobs.deleted_at IS NULL",
            (channel_id,),
        ).fetchone()
    if not operator:
        raise HTTPException(status_code=404, detail="Salve o operador deste canal antes de rodar.")
    job, error = None, None
    try:
        if not channel or channel["status"] != "connected":
            raise HTTPException(status_code=409, detail="O canal não está conectado. Reconecte em Canais.")
        if busy:  # Turbo renders one video at a time; stacking the operator's queue only delays everything
            raise HTTPException(status_code=409, detail="Pulado: o vídeo anterior do operador ainda está sendo gerado.")
        format, language = operator["format"], channel["language"]
        topic = (await suggest_topics(channel, format, 1))[0]["topic"]
        settings: dict[str, Any] = {"video_language": language}
        prefix = language.split("-")[0]
        if prefix in VOICE_BY_LANGUAGE and not str(generation_defaults(format).get("voice_name", "")).startswith(prefix):
            settings["voice_name"] = VOICE_BY_LANGUAGE[prefix]
        script = ""
        if format == "long":
            written = await long_script(LongScriptInput(subject=topic, language=language, minutes=OPERATOR_MINUTES, niche=channel["niche"], persona=channel["persona"]))
            script = written["script"]
        project = get_or_create_project(PROJECT_NAMES[format], language)
        job = await create_job(project["id"], JobCreate(subject=topic, script=script, format=format, channel_ids=[channel_id], settings=settings))
        with database() as db:
            db.execute("UPDATE video_jobs SET created_by='operator' WHERE id=?", (job["id"],))
    except Exception as exc:
        error = str(getattr(exc, "detail", exc))[:500] or "Falha no operador"
    next_run = datetime.now(UTC) + timedelta(hours=operator["cadence_hours"])
    with database() as db:
        db.execute(
            "UPDATE operators SET last_run_at=?, next_run_at=?, last_error=?, updated_at=? WHERE channel_id=?",
            (now(), next_run.isoformat(), error, now(), channel_id),
        )
    return {"job": job, "error": error}


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
        due = [row["channel_id"] for row in db.execute("SELECT channel_id FROM operators WHERE enabled=1 AND next_run_at <= ?", (now(),))]
    for channel_id in due:
        await run_operator(channel_id)  # records its own errors and always moves next_run_at forward


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


async def agent_tick() -> None:
    for step in (publish_due_schedules, reconcile_processing_publications, run_due_operators, prepare_operator_jobs):
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
    response = await call_next(request)
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


app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
