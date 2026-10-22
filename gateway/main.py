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

from gateway.auth_crypto import admin_session_version, password_hash as password_hash, session_token_hash, verify_password
from gateway.auto_generation import router as auto_generation_router
from gateway.growth.api import router as growth_router
from gateway.growth.api import store as growth_store
from gateway.growth.api import OpportunityScoreRequest, PlanningWorkflowRequest, YPPGoalRequest
from gateway.growth.api import evaluate_goal, score_opportunity, start_planning_workflow
from gateway.growth.store import GrowthStoreUnavailable
from gateway.growth.storage import ObjectStorageUnavailable, ensure_media_bucket

DATA_DIR = Path(os.getenv("APP_DATA_DIR", "runtime"))
APP_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
try:
    APP_VERSION = APP_VERSION_FILE.read_text(encoding="utf-8").strip()
except OSError:
    APP_VERSION = "0.0.0+unknown"
DATABASE = DATA_DIR / "cockpit.sqlite3"
TURBO_BASE_URL = os.getenv("TURBO_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
KEY_FILE = os.getenv("MPT_API_KEY_FILE", "")
ADMIN_FILE = Path(os.getenv("COCKPIT_ADMIN_FILE", str(DATA_DIR / "secrets" / "admin.json")))
LOGIN_DISABLED = os.getenv("COCKPIT_DISABLE_LOGIN", "1") == "1"
_OPEN_SESSION_CSRF = "login-disabled"
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

from gateway.deps import (
    CHANNEL_PROFILE_COLORS,
    CHANNEL_PROFILE_FIELDS,
    CHANNEL_PROFILE_POSITIONS,
    CHANNEL_VIDEO_PROFILE_FIELDS,
    LANGUAGE_PATTERN,
    PLATFORM_LABELS,
    UPLOAD_POST_BASE,
    VIDEO_BGM_TYPES,
    VIDEO_BGM_VOLUMES,
    VIDEO_CLIP_SPEEDS,
    VIDEO_CONCAT_MODES,
    VIDEO_TRANSITIONS,
    VOICE_BY_LANGUAGE,
    VOICE_CHOICES,
    YOUTUBE_SCOPES,
    _initialize_database_locked,
    as_utc,
    channel_generation_settings,
    channel_row_or_404,
    channel_video_defaults,
    channel_video_profile_fallback,
    clean_language,
    conflicting_channel_profile_fields,
    configured_admin,
    connect,
    database,
    database_initialization_lock,
    default_channel_video_defaults,
    default_voice_for_language,
    initialize_database,
    normalize_channel_profile,
    normalize_channel_video_defaults,
    normalize_visual_guides,
    notify,
    now,
    open_session,
    parse_time,
    row_as_channel,
    row_as_project,
    session_from_request,
    update_channel,
    upload_post_request,
    video_profile_from_settings,
    youtube_client_file,
    youtube_credentials,
    youtube_token_file,
)
from gateway.routes_agents import (
    ANALYTICS_METRICS,
    PROJECT_NAMES,
    PUBLISHED_YOUTUBE,
    REGION_BY_LANGUAGE,
    OperatorInput,
    StrategyInput,
    analytics_overview,
    analytics_platform_overview,
    analytics_score,
    get_or_create_project,
    list_operators,
    list_schedule,
    put_operator,
    refresh_analytics,
    run_operator,
    strategy,
    suggest_topics,
    youtube_trends,
    youtube_video_metrics,
)
from gateway.routes_agents import router as agents_router
from gateway.routes_channels import (
    ChannelConnect,
    ChannelPatch,
    delete_channel,
    list_channels,
    oauth_flows,
    patch_channel,
    refresh_channel,
    save_youtube_channel,
    tiktok_account,
    tiktok_connect,
    tiktok_reconnect_link,
    upload_post_connect_link,
    youtube_auth_callback,
    youtube_channel_info,
    youtube_connect,
)
from gateway.routes_channels import router as channels_router



def api_key() -> str:
    if KEY_FILE and Path(KEY_FILE).is_file():
        return Path(KEY_FILE).read_text(encoding="utf-8").strip()
    return os.getenv("MPT_API_KEY", "")


def default_generation_settings() -> dict[str, Any]:
    """Shared base (Settings): a balance of speed and quality
    with stock-only sources (Pexels/Pixabay/Coverr). Format presets refine it."""
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
# Final presets by format: a dynamic Short (4 s cuts) and a calm long video (6 s
# cuts), with readable captions for each aspect. For a quick draft, pick a shorter
# duration in the Generator (60 s Short) and a 6 s clip, with no transition or BGM.
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


def login_is_limited() -> bool:
    cutoff = (datetime.now(UTC) - timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat()
    with database() as db:
        failed = db.execute("SELECT count(*) AS count FROM login_attempts WHERE success=0 AND attempted_at>=?", (cutoff,)).fetchone()["count"]
    return failed >= LOGIN_ATTEMPTS


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
        raise HTTPException(status_code=503, detail=f"MoneyPrinterTurbo is unavailable: {exc}") from exc
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
        raise HTTPException(status_code=502, detail=f"MoneyPrinterTurbo returned {response.status_code}: {detail}")
    return response


def unwrap(payload: Any) -> Any:
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def map_turbo_state(payload: dict[str, Any]) -> tuple[str, int, str | None, str | None]:
    raw = str(payload.get("state", "")).lower()
    progress = max(0, min(100, int(float(payload.get("progress", 0) or 0))))
    error = payload.get("error") or None
    stage = payload.get("failed_stage") or None
    if error or raw in {"-1", "failed", "failure", "error"}:
        return "failed", progress, stage, str(error or "Turbo failed")
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
    notify(f"job-interrupted:{job_id}:{reason}", "job_interrupted", f"Generation interrupted: {job['subject']}", reason, job_id)


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
            notification = ("job_completed", f"Generation completed: {job['subject']}", "Ready for review and publishing.")
        elif state == "failed" and job["state"] != "failed":
            notification = ("job_failed", f"Generation failed: {job['subject']}", error or "Turbo reported a failure.")
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
            await interrupt_job(job_id, "Interrupted: MoneyPrinterTurbo was unavailable during reconciliation.", cancel_turbo=False)
            continue
        with database() as db:
            current = db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
        if not current or current["state"] not in {"queued", "running"}:
            continue
        reference = parse_time(current["created_at"] if current["state"] == "queued" else current["progress_updated_at"] or current["updated_at"])
        age_minutes = (datetime.now(UTC) - reference).total_seconds() / 60
        timeout = QUEUE_STALE_MINUTES if current["state"] == "queued" else stale_minutes_for(current)
        if age_minutes >= timeout:
            label = "in queue" if current["state"] == "queued" else "with no progress"
            await interrupt_job(current["id"], f"Interrupted after {int(age_minutes)} min {label} (limit: {timeout} min).")


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
        logging.getLogger("cockpit.growth").warning("Growth OS is unavailable; the legacy flow is preserved: %s", exc)
    try:
        ensure_media_bucket()
    except ObjectStorageUnavailable as exc:
        logging.getLogger("cockpit.growth").warning("Object storage is unavailable; legacy generation is preserved: %s", exc)
    await reconcile_stale_jobs()
    yield


app = FastAPI(
    title="Moneyprint Cockpit Gateway", version=APP_VERSION, lifespan=lifespan,
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
    return {"status": "ok" if database_ok and turbo_ok else "degraded", "database": database_ok, "turbo": turbo_ok, "version": APP_VERSION}


@app.get("/api/version")
async def app_version() -> dict[str, str]:
    return {"name": "MoneyPrinter Cockpit", "version": APP_VERSION}


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
    if LOGIN_DISABLED:
        raise HTTPException(status_code=404, detail="Login is disabled.")
    if login_is_limited():
        raise HTTPException(status_code=429, detail="Too many attempts. Wait 15 minutes.")
    admin = configured_admin()
    success = bool(admin and input.email.strip().lower() == str(admin["email"]).lower() and verify_password(input.password, admin))
    with database() as db:
        db.execute("INSERT INTO login_attempts(attempted_at, success) VALUES (?, ?)", (now(), int(success)))
        db.execute("DELETE FROM login_attempts WHERE attempted_at<?", ((datetime.now(UTC) - timedelta(days=1)).isoformat(),))
    if not success:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
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
    if LOGIN_DISABLED:
        session = open_session()
        admin = configured_admin() or {}
        return {
            "name": admin.get("name") or "Local",
            "email": admin.get("email") or "no login",
            "csrf_token": session["csrf_token"],
            "expires_at": session["expires_at"],
            "login_disabled": True,
        }
    session = session_from_request(request)
    admin = configured_admin()
    if not session or not admin:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
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
        raise HTTPException(status_code=404, detail="Notification not found")
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
        raise HTTPException(status_code=404, detail="Project not found")
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
    """Without format: the shared base edited in Settings. With format: base, then that
    format's preset, then what its own screen saved with "Save as default"."""
    with database() as db:
        rows = {row["key"]: json.loads(row["value_json"]) for row in db.execute(
            "SELECT key, value_json FROM settings WHERE key IN (?, ?)", ("generation-defaults", settings_key(format))
        )}
    base = rows.get("generation-defaults", default_generation_settings())
    if base.get("video_source") not in {"pexels", "pixabay", "coverr"}:
        # AI footage sources were removed (stock only): an old saved default falls back to Pexels.
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
     "description": "Advanced reasoning and a long context.", "capabilities": {"text": True, "image": True, "reasoning": True, "streaming": True}},
    {"id": "z-ai/glm-5.3", "label": "GLM 5.3", "provider": "Z.ai",
     "description": "Balanced for scripts, JSON, and general tasks.", "capabilities": {"text": True, "image": False, "reasoning": True, "streaming": True}},
    {"id": "z-ai/glm-5.3-flash", "label": "GLM 5.3 Flash", "provider": "Z.ai",
     "description": "A fast variant for frequent tasks.", "capabilities": {"text": True, "image": False, "reasoning": True, "streaming": True}},
    {"id": "nvidia/nemotron-3.5-lightning-30b-a3b", "label": "Nemotron 3.5 Lightning", "provider": "NVIDIA",
     "description": "A fast model for agent tasks with thinking.", "capabilities": {"text": True, "image": False, "reasoning": True, "streaming": True}},
    {"id": "meta/muse-glimmer-30b", "label": "Muse Glimmer 30B", "provider": "Meta",
     "description": "Multimodal reasoning with native tools.", "capabilities": {"text": True, "image": True, "reasoning": True, "streaming": True}},
    {"id": "google/diffusiongemma-26b-a4b-it", "label": "DiffusionGemma 26B", "provider": "Google",
     "description": "A multimodal model for text and images, with optional reasoning.", "capabilities": {"text": True, "image": True, "reasoning": True, "streaming": True}},
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
        raise HTTPException(status_code=422, detail="Choose one of the NVIDIA models available in the Cockpit.")
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
                "detail": f"NVIDIA did not finish the test within {NVIDIA_PROBE_TIMEOUT_SECONDS} seconds."}
    except httpx.HTTPError:
        return {"ok": False, "model": model, "latency_ms": round((time.monotonic() - started) * 1000),
                "detail": "NVIDIA could not be reached right now."}
    latency_ms = round((time.monotonic() - started) * 1000)
    if response.status_code == 200:
        return {"ok": True, "model": model, "latency_ms": latency_ms, "detail": "Modelo respondeu com sucesso."}
    detail = "NVIDIA rejected the key or the model." if response.status_code in {401, 403, 404} else "NVIDIA could not finish the test right now."
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
            return {"valid": None, "detail": "Upload-Post has no automatic check — the key will be saved without verifying it first."}
    except httpx.HTTPError as exc:
        return {"valid": None, "detail": f"Could not verify right now: {exc}"}


@app.put("/api/settings/providers")
async def update_providers(input: ProviderCredentials) -> dict[str, Any]:
    """Writes credentials straight to config.toml - never logged, never echoed back
    (the response is only the boolean readiness map, same as GET). Turbo reads this
    file once at startup, so a running moneyprinterturbo container needs a restart
    (`docker compose restart moneyprinterturbo`) to pick up a change made here."""
    config_path = DATA_DIR / "moneyprinter" / "config.toml"
    if not config_path.exists():
        raise HTTPException(status_code=409, detail="config.toml was not found. Run setup.ps1 first.")
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
        warnings.append("Unsupported legacy fields were removed.")
    return payload, warnings


def job_max_words(format: str, settings: dict[str, Any]) -> int | None:
    """The narration budget the screen asked for: the Short's "Target duration" select, or the long
    video's target in minutes. None means no target was sent, so nothing is trimmed."""
    try:
        target = int(settings.get("paragraph_number", 5) or 5) if format == "short" else float(settings.get("target_minutes") or 0)
        rate = float(settings.get("voice_rate") or 1.0)
    except (TypeError, ValueError):
        return None
    return script_duration_budget(format, target, rate, str(settings.get("voice_name") or ""))["maximum_words"] or None


@app.post("/api/projects/{project_id}/jobs", status_code=202)
async def create_job(project_id: str, input: JobCreate, *, generation_item_id: str | None = None) -> dict[str, Any]:
    if input.settings.get("video_source") == "local" and not generation_item_id:
        raise HTTPException(status_code=422, detail="Local materials can only be sent by the generation curator.")
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
        labels = {"video_language": "language", "voice_name": "voice", "voice_rate": "speaking rate", "voice_volume": "volume", "subtitle_enabled": "subtitles", "subtitle_position": "position", "font_size": "size", "text_fore_color": "text color", "stroke_color": "outline color", "stroke_width": "outline thickness", "paragraph_number": "Short duration", "target_minutes": "duration", "video_clip_duration": "clip duration", "video_count": "video count", "video_concat_mode": "image mode", "video_transition_mode": "transition", "video_clip_speed": "clip speed", "bgm_type": "soundtrack", "bgm_volume": "music volume"}
        fields = ", ".join(labels.get(field, field) for field in conflicting_profile_fields)
        raise HTTPException(status_code=409, detail=f"Channels {names} have different voice, subtitle, or video profiles ({fields}). Align the profiles or generate the videos separately.")
    channel_settings = channel_generation_settings(channels[0], input.format) if channels else {}
    merged = {**generation_defaults(input.format), **project["defaults"], **channel_settings, **input.settings, **FORMAT_LOCKED[input.format]}
    effective_profile = normalize_channel_profile({
        "language": merged.get("video_language", "pt-BR"),
        **{field: merged.get(field) for field in CHANNEL_PROFILE_FIELDS},
    })
    merged.update({"video_language": effective_profile.pop("language"), **effective_profile})
    channel_prompts = list(dict.fromkeys(str(channel["system_prompt"] or "").strip() for channel in channels if str(channel["system_prompt"] or "").strip()))
    if len(channel_prompts) > 1:
        raise HTTPException(status_code=409, detail="The selected channels have different system prompts. Generate one video per editorial standard.")
    if channel_prompts:
        generator_prompt = str(merged.get("custom_system_prompt") or "").strip()
        merged["custom_system_prompt"] = "\n\n".join([channel_prompts[0], generator_prompt]).strip()
    # Turbo's encoder defaults to 2 threads; this container sees the same Docker VM CPUs as Turbo.
    merged.setdefault("n_threads", os.cpu_count() or 2)
    script_text = input.script.strip()
    follow_script = merged.get("video_concat_mode") == "script"
    if follow_script:
        # "Follow the script": one visual search term per script chunk, downloaded in that
        # order, instead of Turbo's handful of generic terms shared across the whole video
        # (see visual_terms()) - that's what made footage look unrelated to the narration.
        merged["match_materials_to_script"], merged["video_concat_mode"] = True, "sequential"
        if not script_text and input.format == "short":
            script_response = await short_script(ShortScriptInput(
                subject=input.subject, language=merged.get("video_language", "pt-BR"),
                paragraph_number=int(merged.get("paragraph_number", 5) or 5),
                voice_rate=float(merged.get("voice_rate") or 1.0), voice_name=str(merged.get("voice_name") or ""),
                script_prompt=str(merged.get("video_script_prompt", "")),
                system_prompt=str(merged.get("custom_system_prompt", "")),
            ))
            script_text = script_response["script"]
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
        raise HTTPException(status_code=502, detail="Invalid MoneyPrinterTurbo response while creating the task")
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
            raise HTTPException(status_code=409, detail=f"Channel {channel['name']} is not connected. Reconnect it in Channels.")
        if format == "long" and channel["platform"] != "youtube":
            raise HTTPException(status_code=409, detail="TikTok only accepts Shorts. Choose a YouTube channel for long videos.")
    # publications is UNIQUE(job_id, platform): one post per platform per video.
    if len({channel["platform"] for channel in channels}) < len(channels):
        raise HTTPException(status_code=409, detail="Choose at most one channel per platform.")


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
        raise HTTPException(status_code=404, detail="Job not found")
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
        raise HTTPException(status_code=502, detail="Turbo did not return a task_id")
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
        raise HTTPException(status_code=409, detail="Only queued or generating jobs can be stopped")
    if row["turbo_task_id"]:
        try:
            await turbo_request("DELETE", f"/api/v1/tasks/{row['turbo_task_id']}")
        except HTTPException:
            pass
    with database() as db:
        db.execute(
            "UPDATE video_jobs SET state='interrupted', error=COALESCE(error, 'Canceled by the user'), updated_at=? WHERE id=?",
            (now(), job_id),
        )
        return row_as_job(db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), db)


@app.delete("/api/jobs/{job_id}", status_code=204, response_model=None)
async def delete_job(job_id: str) -> None:
    with database() as db:
        row = db.execute("SELECT * FROM video_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["state"] in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="An active generation cannot be deleted")
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
        raise HTTPException(status_code=404, detail="Artifact not found")
    return row


def derived_video(artifact: sqlite3.Row) -> Path | None:
    remote = str(artifact["remote_path"])
    if not remote.startswith("derived/"):
        return None
    root = (DATA_DIR / "derived_shorts").resolve()
    path = (root / remote.removeprefix("derived/")).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def review_copy_for_artifact(job_id: str, artifact: sqlite3.Row) -> Path | None:
    """Quality review keeps a local copy of each final render. The Turbo task
    volume can lose that file; playback and download use the copy instead."""
    if artifact["kind"] != "video":
        return None
    with database() as db:
        job = db.execute("SELECT generation_item_id FROM video_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or not job["generation_item_id"]:
            return None
        ordered = db.execute(
            "SELECT id FROM artifacts WHERE job_id=? AND kind='video' ORDER BY created_at, id",
            (job_id,),
        ).fetchall()
    index = next((position for position, row in enumerate(ordered) if row["id"] == artifact["id"]), None)
    if index is None:
        return None
    root = (DATA_DIR / "curated_materials").resolve()
    path = (root / str(job["generation_item_id"]) / f"final-review-{index}.mp4").resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def local_video_response(path: Path, artifact: sqlite3.Row, mode: Literal["stream", "download"]) -> FileResponse:
    filename = str(artifact["filename"]).replace('"', "")
    if mode == "download":
        return FileResponse(path, media_type="video/mp4", filename=filename)
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/jobs/{job_id}/artifacts/{artifact_id}/{mode}", response_model=None)
async def artifact_proxy(job_id: str, artifact_id: str, mode: Literal["stream", "download"], request: Request) -> StreamingResponse | FileResponse:
    artifact = artifact_or_404(job_id, artifact_id)
    local_copy = derived_video(artifact) or review_copy_for_artifact(job_id, artifact)
    headers = turbo_headers()
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    # Turbo mounts these as router.get("/stream/...") under the /api/v1 prefix - no /tasks/ segment.
    path = f"/api/v1/{mode}/{artifact['remote_path']}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
    try:
        response = await client.send(client.build_request("GET", f"{TURBO_BASE_URL}{path}", headers=headers), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        if local_copy:
            return local_video_response(local_copy, artifact, mode)
        raise HTTPException(status_code=503, detail=f"Failed to fetch the artifact: {exc}") from exc
    if response.status_code >= 400:
        await response.aclose()
        await client.aclose()
        if local_copy:
            return local_video_response(local_copy, artifact, mode)
        raise HTTPException(status_code=502, detail="Turbo could not find the artifact")
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
        raise HTTPException(status_code=404, detail="Job not found")
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
        errors.append("The title is required.")
    if not re.fullmatch(r"\d{1,3}", value["category_id"]):
        errors.append("The YouTube category must be a numeric ID.")
    if not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*", value["language"]):
        errors.append("Invalid language (use a code such as pt-BR).")
    if not description:
        warnings.append("The description is empty.")
    if len(tags) < 3:
        warnings.append("Fewer than three usable tags.")
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
            raise HTTPException(status_code=409, detail="Metadata can only be set for completed videos")
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
    if "Plan a YouTube video" in prompt:
        return "outline"
    if "Write the narration for section" in prompt:
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
        raise HTTPException(status_code=409, detail=f"Configure the {label} key in Settings.")
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
                    "NVIDIA did not finish this script step within 5 minutes. "
                    "The basic connection is available; try again or choose "
                    "GLM 5.3 Flash in Settings for faster scripts."
                ),
            ) from exc
        raise HTTPException(status_code=504, detail=f"{label} took too long to respond.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"{label} is unavailable: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"{label} returned error {response.status_code}.")
    try:
        message = response.json()["choices"][0]["message"]
        # NVIDIA reasoning models may send their private chain-of-thought in a
        # separate field.  The Cockpit keeps only the final content for scripts
        # and metadata; reasoning is intentionally neither persisted nor shown.
        _reasoning = message.get("reasoning_content")
        content = message.get("content")
        return str(content) if content is not None else ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"{label} returned an unexpected response") from exc


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
    raise HTTPException(status_code=502, detail="The model did not return valid JSON")


async def llm_json_object(
    prompt: str,
    *,
    task: Literal["json", "outline", "script", "test"] = "json",
    max_tokens: int | None = None,
    fast_script: bool = False,
    label: str = "this step",
) -> dict[str, Any]:
    """Retry malformed structured output with the selected provider and model."""
    for attempt in range(3):
        retry_prompt = prompt
        if attempt:
            retry_prompt += (
                "\nThe previous response was empty or contained invalid JSON. "
                "Respond with only one complete JSON object, without markdown, comments, or extra text."
            )
        content = await llm_chat(
            retry_prompt, json_mode=True, task=task,
            max_tokens=(max_tokens or NVIDIA_TASK_TOKEN_LIMITS[task]) + attempt * 512,
            fast_script=fast_script,
        )
        try:
            return parse_json_object(content)
        except HTTPException as exc:
            if exc.detail != "The model did not return valid JSON":
                raise
    raise HTTPException(
        status_code=502,
        detail=f"The model did not return valid JSON for {label} after 3 attempts. Retry this channel or choose another model from the configured provider.",
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
        "You are the conversational assistant for MoneyPrinter Cockpit. Reply in clear, natural English. "
        "Read the history and choose an action only when the user asks for it or it is required to answer. "
        "Never invent channel metrics. "
        "Available actions: reply (conversation and explanations), clarify (ask for missing data), "
        "growth_goal (calculate YPP targets), growth_score (score a topic opportunity), "
        "growth_plan (create a durable plan; only when explicitly requested), script_proposal "
        "(propose an angle before writing; use this when they ask for a script and no proposal is confirmed yet), "
        "short_script (generate a Short script only after the user confirms the proposal in args.proposal), "
        "channels (list channels), "
        "jobs (list recent jobs), workflow_status (look up a workflow), and system_status "
        "(check system health). Do not publish or approve content. Plans stay in human review. "
        "When you write narration, titles, or metadata, use the selected channel's language and do not force English. "
        "For growth_goal, provide args.metrics with subscribers, subscriber_target, qualified_watch_hours, "
        "watch_hour_target, qualified_shorts_views, shorts_view_target, days_remaining and, if known, "
        "average_view_duration_minutes. If any required value is missing, choose clarify and do not guess. "
        "For growth_score, provide topic and dimensions with 12 scores from 0 to 100: demand, audience_fit, "
        "historical_retention, subscriber_conversion, expected_avd, evergreen, trend_velocity, monetization, "
        "stock_availability, competition, production_cost, and saturation. If the scores are model estimates, set "
        "args.estimated=true. For growth_plan, provide topic, ypp_goal (the same metrics), and opportunity "
        "(the 12 scores); never enable automatic publishing. For script_proposal, provide subject. "
        "For short_script, provide subject and proposal with the text the user confirmed. "
        "For workflow_status, provide workflow_id. For actions that depend on a channel, use the selected channel "
        "or a name that matches the list exactly. Respond with only one JSON object in this format: "
        '{"action":"reply|clarify|growth_goal|growth_score|growth_plan|script_proposal|short_script|channels|jobs|workflow_status|system_status","message":"short text","args":{}}'
        + "\nSelected channel: " + json.dumps(input.channel_id, ensure_ascii=False)
        + "\nAvailable channels: " + json.dumps(channel_context, ensure_ascii=False)
        + "\nHistory: " + json.dumps(transcript, ensure_ascii=False)
    )
    decision = parse_json_object(await llm_chat(prompt, json_mode=True, task="json", max_tokens=1600))
    action = str(decision.get("action") or "reply")
    message = str(decision.get("message") or "").strip()
    args = decision.get("args") if isinstance(decision.get("args"), dict) else {}

    if action in {"reply", "clarify"}:
        return {"action": action, "message": message or "Can you tell me a little more about what you need?"}
    if action == "channels":
        if not channels:
            return {"action": action, "message": "There are no channels yet. Connect one in Channels."}
        lines = [f"- {item.get('name')} ({item.get('platform')}) · {item.get('status')}" for item in channels]
        return {"action": action, "message": "Your connected channels:\n" + "\n".join(lines), "data": channel_context}
    if action == "jobs":
        with database() as db:
            jobs = [dict(row) for row in db.execute(
                "SELECT id, subject, state, progress, created_at FROM video_jobs "
                "WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 8"
            )]
        if not jobs:
            return {"action": action, "message": "There are no videos in progress or in the history yet."}
        lines = [f"- {item['subject']} · {item['state']} · {item['progress']}%" for item in jobs]
        return {"action": action, "message": "Recent jobs:\n" + "\n".join(lines), "data": jobs}
    if action == "system_status":
        status = await health_status()
        return {"action": action, "message": (
            f"System status: {status.get('status', 'unavailable')}. "
            f"Database: {'connected' if status.get('database') else 'unavailable'}; "
            f"MoneyPrinter Turbo: {'connected' if status.get('turbo') else 'unavailable'}."
        ), "data": status}
    if action == "workflow_status":
        try:
            workflow_id = UUID(str(args.get("workflow_id") or ""))
            workflow = growth_store.workflow(workflow_id)
        except (ValueError, GrowthStoreUnavailable) as exc:
            raise HTTPException(status_code=400 if isinstance(exc, ValueError) else 503, detail="Could not look up that workflow.") from exc
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return {"action": action, "message": (
            f"Plan {workflow_id}: state {workflow.get('state')} · step {workflow.get('current_step') or '—'}."
        ), "data": workflow}

    channel = _assistant_channel(channels, input.channel_id, args)
    if not channel:
        detail = "Connect a channel before continuing." if not channels else "Select a channel or give its exact name."
        return {"action": "clarify", "message": detail}

    try:
        channel_id = UUID(str(channel["id"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="The selected channel has an invalid identifier.") from exc

    if action == "growth_goal":
        try:
            goal_input = YPPGoalRequest(channel_id=channel_id, metrics=args.get("metrics") or {})
        except ValidationError:
            return {"action": "clarify", "message": "To calculate your gap, send the current values and the targets for subscribers, qualified hours, and Shorts views, plus how many days remain. You can write it all in one sentence."}
        result = evaluate_goal(goal_input)
        values = result["result"].model_dump(mode="json")
        return {"action": action, "data": values, "message": (
            f"For {channel.get('name')}, the gap is {values['subscribers']['gap']:,.0f} subscribers "
            f"({values['subscribers']['required_daily_velocity']:,.2f}/day), "
            f"{values['watch_hours']['gap']:,.0f} qualified hours "
            f"({values['watch_hours']['required_daily_velocity']:,.2f}/day), and "
            f"{values['shorts_views']['gap']:,.0f} qualified Shorts views. "
            "The calculation uses qualified organic metrics only."
        )}
    if action == "growth_score":
        try:
            score_input = OpportunityScoreRequest(
                channel_id=channel_id, topic=str(args.get("topic") or ""), dimensions=args.get("dimensions") or {},
            )
        except ValidationError:
            return {"action": "clarify", "message": "Tell me the opportunity topic. I can estimate the dimensions to start the evaluation and clearly mark the result as an estimate."}
        result = score_opportunity(score_input)
        values = result["result"].model_dump(mode="json")
        estimate = " This is a model estimate, not a measurement from external data." if args.get("estimated") else ""
        return {"action": action, "data": values, "message": (
            f"The topic \"{score_input.topic}\" scored {values['score']}/100 "
            f"(version {values['version']}).{estimate}"
        )}
    if action == "growth_plan":
        try:
            plan_input = PlanningWorkflowRequest(
                channel_id=channel_id, topic=str(args.get("topic") or ""),
                ypp_goal=args.get("ypp_goal") or {}, opportunity=args.get("opportunity") or {},
                production_target=args.get("production_target") or {"long": 1, "shorts": 3},
                publish_mode="review",
            )
        except ValidationError:
            return {"action": "clarify", "message": "To build the plan I need the topic, the current numbers, the YPP targets, and the deadline. I can collect those with you, one step at a time."}
        started = await start_planning_workflow(plan_input)
        return {"action": action, "data": started, "message": (
            f"I started the plan for \"{plan_input.topic}\" on {channel.get('name')}. "
            f"It will stay in human review. Workflow ID: {started['id']}."
        )}
    if action == "script_proposal":
        subject = str(args.get("subject") or "").strip()
        if not subject:
            return {"action": "clarify", "message": "What is the video topic?"}
        proposal = await script_proposal(ScriptProposalInput(
            subject=subject, format="short", language=str(channel.get("language") or "pt-BR"),
            channel_context=str(channel.get("niche") or ""),
        ))
        questions = "\n".join(f"- {item}" for item in proposal["audience_questions"])
        return {"action": action, "data": proposal, "message": (
            f"Proposal for \"{subject}\":\n{proposal['brief']}\n\nAudience questions:\n{questions}\n\n"
            "Confirm this angle, or say what to change, and I will write the script."
        )}
    if action == "short_script":
        subject = str(args.get("subject") or "").strip()
        proposal = str(args.get("proposal") or "").strip()
        if not subject:
            return {"action": "clarify", "message": "What is the script topic?"}
        if not proposal:
            return {"action": "clarify", "message": "Before the script, confirm the proposal angle. I can draft that proposal now."}
        result = await short_script(ShortScriptInput(
            subject=subject, language=str(channel.get("language") or "pt-BR"),
            voice_rate=float(channel.get("voice_rate") or 1.0), voice_name=str(channel.get("voice_name") or ""),
            script_prompt=str(args.get("style") or "")[:2000],
            system_prompt=str(channel.get("system_prompt") or "")[:4000],
            proposal=proposal,
            reference_brief=str(args.get("reference_brief") or "")[:1000],
        ))
        script = result["script"]
        return {"action": action, "data": result, "message": (
            f"Short script about \"{subject}\" ({result['words']} words):\n\n{script}"
        )}
    return {"action": "reply", "message": message or "I cannot run that request from the conversation yet. I can look up channels and jobs, calculate targets and opportunities, create a growth plan, or generate a Short script."}


@app.post("/api/jobs/{job_id}/metadata/generate")
async def generate_metadata(job_id: str) -> dict[str, Any]:
    with database() as db:
        job = job_row_or_404(db, job_id)
        language = job_language(db, job)
    if job["state"] not in METADATA_STATES:
        raise HTTPException(status_code=409, detail="Metadata can only be generated for completed videos")
    script = job["script"] or json.loads(job["turbo_payload_json"]).get("script") or ""
    if job["format"] == "long":
        guidance = ('"title": up to 90 characters, optimized for YouTube search, "description": a summary paragraph, '
                    'then "In this video:" with the main topics and 3 hashtags at the end, "tags": a list of 10 to 20 search terms')
        excerpt = script[:12000]
    else:
        guidance = '"title": up to 70 characters, short and catchy, "description": 1 to 2 sentences followed by 3 to 5 hashtags, "tags": a list of 8 to 15 search terms'
        excerpt = script[:6000]
    prompt = f"Create YouTube metadata in {language} for the video below. Write the title, description, and tags in {language}. Respond with JSON only: {{{guidance}}}.\n\nTopic: {job['subject']}\nScript: {excerpt}"
    generated = parse_json_object(await llm_chat(prompt, json_mode=True))
    return store_metadata(job_id, {field: generated.get(field) for field in ("title", "description", "tags")})


WORDS_PER_MINUTE = 150  # calibration knob: Edge TTS narrates ~150 words/min at voice rate 1.0
SHORT_TARGET_SECONDS = {4: 60, 5: 75, 6: 90}  # mirrors the Shorts "Target duration" select in cockpit-views.js
SHORT_MIN_SECONDS = 60  # below this a Short is "too short" (see the word-count hint in moneyprinter-runtime.js)


def short_script_max_words(paragraph_number: int, voice_rate: float = 1.0, voice_name: str = "") -> int:
    return int(script_duration_budget("short", paragraph_number, voice_rate, voice_name)["maximum_words"])


def short_script_min_words(paragraph_number: int = 4, voice_rate: float = 1.0, voice_name: str = "") -> int:
    return int(script_duration_budget("short", paragraph_number, voice_rate, voice_name)["minimum_words"])


def script_duration_budget(format: str, target: int | float, voice_rate: float = 1.0, voice_name: str = "") -> dict[str, float | int]:
    """Returns one narration-time and word budget for every script-generation path."""
    if format == "short":
        target_seconds = SHORT_TARGET_SECONDS.get(int(target), max(SHORT_TARGET_SECONDS.values()))
        minimum_seconds = max(SHORT_MIN_SECONDS, target_seconds * 0.9)
        maximum_seconds = min(90, target_seconds * 1.1)
    elif format == "long":
        target_seconds = float(target) * 60
        minimum_seconds, maximum_seconds = target_seconds * 0.85, target_seconds
    else:
        raise ValueError(f"Invalid script format: {format}")
    words_per_minute = voice_words_per_minute(voice_name)
    minimum_words = math.ceil(minimum_seconds * words_per_minute * voice_rate / 60)
    maximum_words = math.floor(maximum_seconds * words_per_minute * voice_rate / 60)
    if minimum_words > maximum_words:
        raise HTTPException(status_code=422, detail="The duration and voice rate do not allow a valid script range.")
    target_words = round((minimum_words + maximum_words) / 2)
    return {
        "target_seconds": float(target_seconds), "minimum_seconds": float(minimum_seconds),
        "maximum_seconds": float(maximum_seconds), "minimum_words": minimum_words,
        "maximum_words": maximum_words, "target_words": target_words,
    }


def short_script_prompt(script_prompt: str, paragraph_number: int, voice_rate: float = 1.0, voice_name: str = "") -> str:
    """Carries the selected Short duration and voice-calibrated word range into Turbo's prompt."""
    budget = script_duration_budget("short", paragraph_number, voice_rate, voice_name)
    length_rule = (
        f"The narration must last between {budget['minimum_seconds']:g} and {budget['maximum_seconds']:g} seconds "
        f"(target {(budget['minimum_seconds'] + budget['maximum_seconds']) / 2:g} s), with {budget['minimum_words']} to "
        f"{budget['maximum_words']} words in the selected voice."
    )
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


class ScriptProposalInput(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    format: Literal["short", "long"] = "short"
    language: str = Field(default="pt-BR", max_length=20)
    channel_context: str = Field(default="", max_length=2000)


def normalize_proposal(raw: dict[str, Any]) -> dict[str, Any]:
    angle = str(raw.get("angle") or "").strip()[:300]
    questions = [str(item).strip()[:180] for item in raw.get("audience_questions") or [] if str(item).strip()][:5]
    references = [str(item).strip()[:180] for item in raw.get("references") or [] if str(item).strip()][:5]
    brief = str(raw.get("brief") or "").strip()[:1200]
    if not angle or not brief or len(questions) < 2:
        raise HTTPException(status_code=502, detail="The research proposal came back incomplete. Try again.")
    return {"angle": angle, "audience_questions": questions, "references": references, "brief": brief}


@app.post("/api/scripts/proposal")
async def script_proposal(input: ScriptProposalInput) -> dict[str, Any]:
    generated = await llm_json_object(
        f"Research an editorial angle for a {input.format} video in {input.language}. "
        f"Topic: {input.subject}. Channel context: {input.channel_context[:1500] or 'not provided'}. "
        "Do not invent URLs, audience numbers, or verifiable quotations. References are topics or questions, not links. "
        "Write the angle, questions, references, and brief in the language above. "
        'Respond with JSON: {"angle": "angle in one sentence", "audience_questions": ["question", "question"], '
        '"references": ["reference topic"], "brief": "4 to 8 lines of script direction, with opening, progression, and close"}',
        task="json", max_tokens=700, label="the research proposal",
    )
    return normalize_proposal(generated)


@app.post("/api/scripts/reference")
async def script_reference(request: Request) -> dict[str, Any]:
    payload = await request.body()
    if not 1024 <= len(payload) <= 80 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Send a local video between 1 KB and 80 MB.")
    folder = DATA_DIR / "references"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid4()}.mp4"
    path.write_bytes(payload)
    try:
        return reference_structure(path)
    finally:
        path.unlink(missing_ok=True)


def reference_structure(path: Path) -> dict[str, Any]:
    """Describe pacing only. The source pictures and words are not copied into the script."""
    import subprocess
    from gateway.visual_specialists import probe

    try:
        info = probe(path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Could not read the reference video.") from exc
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise HTTPException(status_code=422, detail="The reference video has no duration.")
    scene = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "select='gt(scene,0.3)',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    cuts = len(re.findall(r"pts_time:", scene.stderr))
    rate = cuts / duration
    pace = "fast" if rate >= 0.35 else "moderate" if rate >= 0.12 else "calm"
    hook = max(1.0, min(3.0, duration * 0.12))
    brief = (
        f"Open in the first {hook:.0f}s, keep a {pace} pace "
        f"({cuts} scene changes in {duration:.0f}s), and reserve the last tenth for the close. "
        "Do not copy lines, images, or the reference video's brand."
    )
    return {"duration_seconds": round(duration, 1), "hook_seconds": round(hook, 1), "cuts": cuts, "pace": pace, "brief": brief}


class ShortScriptInput(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    language: str = Field(default="pt-BR", max_length=20)
    paragraph_number: int = Field(default=5, ge=1, le=10)
    voice_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    voice_name: str = Field(default="", max_length=120)
    script_prompt: str = Field(default="", max_length=4200)
    system_prompt: str = Field(default="", max_length=12000)
    proposal: str = Field(default="", max_length=2000)
    reference_brief: str = Field(default="", max_length=1000)


@app.post("/api/scripts/short")
async def short_script(input: ShortScriptInput) -> dict[str, Any]:
    budget = script_duration_budget("short", input.paragraph_number, input.voice_rate, input.voice_name)
    minimum, maximum = budget["minimum_seconds"], budget["maximum_seconds"]
    previous = ""
    for attempt in range(3):
        trimmed_this_attempt = False
        prompt = short_script_prompt(input.script_prompt, input.paragraph_number, input.voice_rate, input.voice_name)
        if input.proposal.strip():
            prompt += "\nConfirmed editorial proposal. Follow this angle without copying sources:\n" + input.proposal.strip()
        if input.reference_brief.strip():
            prompt += "\nStructure observed in a reference video. Use only the pacing, not the content:\n" + input.reference_brief.strip()
        if previous:
            seconds = narration_seconds(previous, input.voice_rate, input.voice_name)
            prompt += (
                f"\nThe previous version had {len(previous.split())} words and was estimated at {seconds:.1f} seconds. "
                f"Rewrite the full script so it stays between {budget['minimum_words']} and {budget['maximum_words']} words "
                f"and {minimum:.0f} to {maximum:.0f} seconds. Preserve the topic, the facts, and the instructions; do not add filler. "
                f"Previous script:\n{previous}"
            )
        response = await turbo_request("POST", "/api/v1/scripts", json={
            "video_subject": input.subject, "video_language": clean_language(input.language),
            "paragraph_number": input.paragraph_number,
            "video_script_prompt": prompt,
            "custom_system_prompt": input.system_prompt,
        })
        generated = clean_narration(str((unwrap(response.json()) or {}).get("video_script", ""))).strip()
        if not generated:
            raise HTTPException(status_code=502, detail="The provider did not return a valid script.")
        candidate = generated
        if len(candidate.split()) > budget["maximum_words"]:
            candidate = trim_to_words(candidate, int(budget["maximum_words"]))
            trimmed_this_attempt = True
        words = len(candidate.split())
        seconds = narration_seconds(candidate, input.voice_rate, input.voice_name)
        if words >= budget["minimum_words"] and minimum <= seconds <= maximum:
            return {"script": candidate, "words": words, "trimmed": trimmed_this_attempt,
                    "too_short": False, "min_words": budget["minimum_words"],
                    "estimated_seconds": round(seconds)}
        previous = candidate
    seconds = narration_seconds(previous, input.voice_rate, input.voice_name)
    raise HTTPException(
        status_code=502,
        detail=f"The provider did not fit the script to the duration: {round(seconds)} s estimated; the selected range is {round(minimum)}–{round(maximum)} s. Generate again or adjust the duration or voice.",
    )


class LongScriptInput(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    language: str = Field(default="pt-BR", max_length=20)
    minutes: int = Field(default=8, ge=5, le=15)
    voice_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    voice_name: str = Field(default="", max_length=120)
    niche: str = Field(default="", max_length=200)
    requirements: str = Field(default="", max_length=4200)
    system_prompt: str = Field(default="", max_length=12000)
    proposal: str = Field(default="", max_length=2000)
    reference_brief: str = Field(default="", max_length=1000)


def clean_narration(text: str) -> str:
    """Kimi still slips in headings, bullets or bold now and then; the TTS would read the symbols aloud."""
    text = re.sub(r"(?m)^\s*```[^\n]*$", "", text)
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"\[(?:pausa|pause|música|music|sfx|cena|scene)(?:\s*[:\-][^\]]*)?\]", "", text, flags=re.IGNORECASE)
    lines = (re.sub(r"^\s*(#+|[-*•]|\d+[.)])\s+", "", line).replace("**", "").strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def long_script_tokens(words: int, ceiling: int) -> int:
    """Reserve enough output for narration in the channel language without inviting needless reasoning.

    The estimate is intentionally below the provider-wide script cap.  A final
    word trim remains the authoritative duration guardrail.
    """
    return min(ceiling, max(768, round(words * 1.8)))


async def _fit_long_script_duration(input: LongScriptInput, result: dict[str, Any]) -> dict[str, Any]:
    budget = script_duration_budget("long", input.minutes, input.voice_rate, input.voice_name)
    minimum, maximum = budget["minimum_seconds"], budget["maximum_seconds"]
    words_per_minute = voice_words_per_minute(input.voice_name)
    script = clean_narration(str(result.get("script") or "")).strip()
    trimmed = bool(result.get("trimmed"))
    for attempt in range(3):
        if not script:
            raise HTTPException(status_code=502, detail="The model did not return a valid script.")
        if len(script.split()) > budget["maximum_words"]:
            shorter = trim_to_words(script, int(budget["maximum_words"]))
            if len(shorter.split()) >= budget["minimum_words"]:
                script = shorter
                trimmed = True
        seconds = narration_seconds(script, input.voice_rate, input.voice_name)
        if budget["minimum_words"] <= len(script.split()) and minimum <= seconds <= maximum:
            return {
                **result, "script": script, "words": len(script.split()), "trimmed": trimmed,
                "estimated_minutes": round(len(script.split()) / (words_per_minute * input.voice_rate), 1),
            }
        if attempt == 2:
            break
        response = parse_json_object(await llm_chat(
            f"Adjust this script for a YouTube video with a selected duration of {input.minutes} minutes, "
            f"voice {input.voice_name or 'default'} at {input.voice_rate}x, in {input.language}. The previous narration was estimated at {seconds:.1f} s. "
            f"Write the full script at about {budget['target_words']} words, necessarily between "
            f"{budget['minimum_words']} and {budget['maximum_words']} words ({minimum:.0f}–{maximum:.0f} s estimated). "
            "Preserve the topic, the facts, the structure, the ending, and the editorial instructions. "
            f"Custom requirements: {input.requirements}. Channel instructions: {input.system_prompt}. "
            "If it is short, develop the requested points; condense repetition if it is long. Do not use filler. "
            "Use narration in continuous paragraphs only, in the language above, with no titles, bullets, Markdown, emoji, or scene directions. "
            f'Respond with JSON only: {{"script": "narration complete"}}. Current script:\n{script}',
            json_mode=True, task="script", max_tokens=long_script_tokens(int(budget["maximum_words"]), FAST_LONG_SCRIPT_MAX_TOKENS),
            fast_script=True,
        ))
        script = clean_narration(str(response.get("script") or "")).strip()
        trimmed = False
    final_seconds = narration_seconds(script, input.voice_rate, input.voice_name) if script else 0
    raise HTTPException(
        status_code=502,
        detail=f"The provider did not fit the script to the duration: {round(final_seconds)} s estimated; the selected range is {round(minimum)}–{round(maximum)} s. Generate again or adjust the duration or voice.",
    )


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
    style = (f" System style instructions, follow them in everything you write: {system_prompt}." if system_prompt else "")
    style += " " + writing_brief("long")
    if input.proposal.strip():
        style += " Confirmed editorial proposal, follow this angle without copying sources: " + input.proposal.strip()
    if input.reference_brief.strip():
        style += " Reference structure, use only the pacing: " + input.reference_brief.strip()
    budget = script_duration_budget("long", input.minutes, input.voice_rate, input.voice_name)
    target_words = int(budget["target_words"])

    # The common 5-8 minute case used to incur an outline plus 4-5 serial
    # writes.  One bounded completion is both faster and coherent because the
    # entire requested narration fits in the selected model's context.
    if input.minutes <= 8:
        yield "progress", {"stage": "writing", "section": 1, "total": 1, "message": "Writing the full script…"}
        generated = parse_json_object(await llm_chat(
            f"Write a complete YouTube video script of about {input.minutes} minutes in {language} about: {input.subject}."
            + (f" Channel niche: {input.niche}." if input.niche else "")
            + (f" Custom requirements: {requirements}." if requirements else "") + style
            + f' Respond with JSON only: {{"title": "concise title", "script": "narration of about {target_words} words, between {budget["minimum_words"]} and {budget["maximum_words"]}"}}. '
            "Write the title and narration in the language above. The narration needs a strong opening, coherent development, and a close that asks the viewer to subscribe. "
            "Use spoken text in continuous paragraphs only: no titles inside the script, bullets, markdown, emoji, or scene directions.",
            json_mode=True,
            task="script",
            max_tokens=long_script_tokens(target_words, FAST_LONG_SCRIPT_MAX_TOKENS),
            fast_script=True,
        ))
        script = clean_narration(str(generated.get("script") or ""))
        if not script:
            raise HTTPException(status_code=502, detail="The model did not return a valid script")
        trimmed_script = script
        if len(script.split()) > budget["maximum_words"]:
            candidate = trim_to_words(script, int(budget["maximum_words"]))
            if len(candidate.split()) >= budget["minimum_words"]:
                trimmed_script = candidate
        words = len(trimmed_script.split())
        result = {
            "title": str(generated.get("title") or input.subject), "script": trimmed_script,
            "trimmed": trimmed_script != script, "sections": ["Complete script"],
            "words": words, "estimated_minutes": round(words / (voice_words_per_minute(input.voice_name) * input.voice_rate), 1),
        }
        result = await _fit_long_script_duration(input, result)
        yield "progress", {"stage": "finalizing", "section": 1, "total": 1, "message": "Finishing the script…"}
        yield "completed", result
        return

    section_count = max(4, round(input.minutes / 1.5))
    words_per_section = target_words // section_count
    yield "progress", {"stage": "planning", "section": 0, "total": section_count, "message": "Planning the sections…"}
    outline = parse_json_object(await llm_chat(
        f"Plan a YouTube video of {input.minutes} minutes in {language} about: {input.subject}."
        + (f" Channel niche: {input.niche}." if input.niche else "")
        + (f" Custom requirements: {requirements}." if requirements else "") + style
        + f' Respond with JSON only: {{"title": "video title", "sections": a list of {section_count} items '
        '{"heading": "section title", "points": "a list of 2 to 4 points"}}}. '
        "Write the title and section headings in the language above. The first section is an opening hook and the last is a close that asks the viewer to subscribe.",
        json_mode=True,
        task="outline",
        max_tokens=long_script_tokens(section_count * 180, FAST_LONG_SECTION_MAX_TOKENS),
        fast_script=True,
    ))
    sections = [section for section in outline.get("sections") or [] if isinstance(section, dict) and section.get("heading")][:section_count]
    if not sections:
        raise HTTPException(status_code=502, detail="The model did not return the script in sections")
    parts: list[str] = []
    for index, section in enumerate(sections, start=1):
        yield "progress", {"stage": "writing", "section": index, "total": len(sections), "message": f"Writing section {index} of {len(sections)}…"}
        previous = f"\nEnd of the previous section (continue from it, without repeating): {parts[-1][-600:]}" if parts else ""
        parts.append(clean_narration(await llm_chat(
            f"Write the narration for section {index} of {len(sections)} of a YouTube video in {language} about: {input.subject}.\n"
            f"Section: {section['heading']}\nPoints: {'; '.join(map(str, section.get('points') or []))}\n"
            f"About {words_per_section} words. Spoken text only, in the language above, in continuous paragraphs: no titles, bullets, markdown, emoji, or scene directions."
            + (f" Requisitos personalizados: {requirements}." if requirements else "") + style + previous,
            task="script",
            max_tokens=long_script_tokens(words_per_section, FAST_LONG_SECTION_MAX_TOKENS),
            fast_script=True,
        )))
    script = "\n\n".join(part for part in parts if part)
    trimmed_script = script
    if len(script.split()) > budget["maximum_words"]:
        candidate = trim_to_words(script, int(budget["maximum_words"]))
        if len(candidate.split()) >= budget["minimum_words"]:
            trimmed_script = candidate
    words = len(trimmed_script.split())
    result = {
        "title": str(outline.get("title") or input.subject), "script": trimmed_script, "trimmed": trimmed_script != script,
        "sections": [str(section["heading"]) for section in sections], "words": words, "estimated_minutes": round(words / (voice_words_per_minute(input.voice_name) * input.voice_rate), 1),
    }
    result = await _fit_long_script_duration(input, result)
    yield "progress", {"stage": "finalizing", "section": len(sections), "total": len(sections), "message": "Finishing the script…"}
    yield "completed", result


async def generate_long_script(input: LongScriptInput) -> dict[str, Any]:
    async for event, data in long_script_events(input):
        if event == "completed":
            return data
    raise HTTPException(status_code=502, detail="The model did not finish the script")


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
            yield sse_message("error", json.dumps({"code": 500, "message": "Could not generate the script."}, ensure_ascii=False))
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
        raise HTTPException(status_code=422, detail="Each clip's duration must be greater than zero.")
    slots = max(1, math.ceil(narration_seconds(script, voice_rate) / clip_seconds))
    if slots > MAX_VISUAL_TERMS:
        raise HTTPException(status_code=422, detail=(
            f"The selected duration requires {slots} footage searches, above the limit of {MAX_VISUAL_TERMS}. "
            "Increase the duration of each clip or shorten the video."
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
        f"Video topic: {subject}\nEditorial context: {context[:5000]}\nNumbered excerpts of the narration, in the order they appear:\n{numbered}\n"
        "For each excerpt, create a stock-video search in ENGLISH (1 to 3 words) that shows something filmable and "
        "concrete from what that excerpt says: a scene, object, action, or place. Nothing abstract, no proper names, "
        "no on-screen text. The terms must differ from each other — do not repeat the general topic in every one.\n"
        f'Respond with JSON only: {{"terms": a list of exactly {len(chunks)} strings, in the same order as the excerpts}}',
        label="the footage keywords",
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
            raise HTTPException(status_code=409, detail="Only completed videos can be approved")
        require_quality_approval(db, job)
        if not db.execute("SELECT 1 FROM artifacts WHERE job_id=? AND kind='video'", (job_id,)).fetchone():
            raise HTTPException(status_code=409, detail="The final video was not found")
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        if not stored or normalize_youtube_metadata(json.loads(stored["value_json"]))[1]:
            raise HTTPException(status_code=409, detail="Set a valid title, description, and tags before approving")
        db.execute("UPDATE video_jobs SET state='approved', updated_at=? WHERE id=?", (now(), job_id))
        return row_as_job(job_row_or_404(db, job_id), db)


@app.post("/api/jobs/{job_id}/shorts", status_code=202)
async def derive_shorts(job_id: str) -> dict[str, Any]:
    """Cut an approved landscape video into vertical Shorts and queue them for the same channel."""
    import subprocess

    with database() as db:
        job = job_row_or_404(db, job_id)
        if job["format"] != "long":
            raise HTTPException(status_code=409, detail="Only an approved long video can become Shorts.")
        if job["state"] not in {"completed", "approved"}:
            raise HTTPException(status_code=409, detail="The long video is not ready yet.")
        if job["generation_item_id"]:
            require_quality_approval(db, job)
        elif job["state"] != "approved":
            raise HTTPException(status_code=409, detail="Approve the long video before cutting Shorts.")
        artifact = db.execute("SELECT * FROM artifacts WHERE job_id=? AND kind='video' ORDER BY created_at, id LIMIT 1", (job_id,)).fetchone()
        publication = db.execute("SELECT channel_id FROM publications WHERE job_id=? LIMIT 1", (job_id,)).fetchone()
    if not artifact:
        raise HTTPException(status_code=409, detail="The long video has no final file.")
    source = derived_video(artifact) or review_copy_for_artifact(job_id, artifact)
    temporary = None
    if source is None:
        temporary = tempfile.TemporaryDirectory()
        source = Path(temporary.name) / "source.mp4"
        await download_turbo_file(artifact["remote_path"], source)
    try:
        from gateway.visual_specialists import probe
        duration = float(probe(source).get("duration") or 0)
    except Exception as exc:
        if temporary:
            temporary.cleanup()
        raise HTTPException(status_code=422, detail="Could not measure the long video.") from exc
    if duration < 20:
        if temporary:
            temporary.cleanup()
        raise HTTPException(status_code=409, detail="The long video is too short to generate Shorts.")
    length = min(45.0, max(20.0, duration / 3))
    starts = [0.0]
    if duration > length + 15:
        starts.append(max(0.0, duration * 0.35))
    if duration > length * 2 + 15:
        starts.append(max(0.0, duration * 0.7))
    channel_id = job["channel_id"] or (publication["channel_id"] if publication else None)
    created: list[str] = []
    root = DATA_DIR / "derived_shorts"
    root.mkdir(parents=True, exist_ok=True)
    try:
        for index, start in enumerate(starts[:3], start=1):
            short_id = str(uuid4())
            target = root / short_id / "clip.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            render = subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(source), "-t", f"{length:.2f}",
                 "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                 "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(target)],
                capture_output=True, timeout=600, check=False,
            )
            if render.returncode or not target.is_file() or target.stat().st_size == 0:
                raise HTTPException(status_code=502, detail=f"Could not cut Short {index}.")
            timestamp = now()
            with database() as db:
                db.execute(
                    "INSERT INTO video_jobs(id, project_id, subject, script, request_json, state, format, channel_id, created_by, created_at, updated_at, completed_at) "
                    "VALUES (?, ?, ?, ?, ?, 'completed', 'short', ?, 'derived', ?, ?, ?)",
                    (short_id, job["project_id"], f"{job['subject']} · Short {index}", job["script"] or "",
                     json.dumps({"source_job_id": job_id, "start_seconds": round(start, 2), "duration_seconds": round(length, 2)}),
                     channel_id, timestamp, timestamp, timestamp),
                )
                db.execute(
                    "INSERT INTO artifacts(id, job_id, kind, remote_path, filename, created_at) VALUES (?, ?, 'video', ?, 'short.mp4', ?)",
                    (str(uuid4()), short_id, f"derived/{short_id}/clip.mp4", timestamp),
                )
                if channel_id:
                    db.execute(
                        "INSERT INTO publications(id, job_id, platform, channel_id, state, created_at, updated_at) VALUES (?, ?, 'youtube', ?, 'pending', ?, ?)",
                        (str(uuid4()), short_id, channel_id, timestamp, timestamp),
                    )
            created.append(short_id)
    finally:
        if temporary:
            temporary.cleanup()
    return {"created": len(created), "job_ids": created}


def require_quality_approval(db: sqlite3.Connection, job: sqlite3.Row) -> None:
    if not job["generation_item_id"]:
        if job["auto_publish_authorized"]:
            raise HTTPException(status_code=409, detail="The final review has not approved this job yet. Check the generation report.")
        return
    quality = db.execute("SELECT state FROM generation_quality WHERE item_id=? AND job_id=?", (job["generation_item_id"], job["id"])).fetchone()
    if not quality or quality["state"] != "approved":
        raise HTTPException(status_code=409, detail="The video's final review has not approved this job yet. Check the generation report.")


# youtube.readonly is what lets the callback find out which channel was picked on Google's consent screen.
class PublishRejected(Exception):
    """The platform answered 4xx: the upload definitely did not create a post, so a retry is safe."""


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
                raise HTTPException(status_code=502, detail="Turbo could not find the artifact")
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
        raise PublishRejected(result.get("error") or body.get("message") or "Upload-Post did not accept the upload")
    return result.get("url") or result.get("post_id") or "", "published"


def map_upload_post_status(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Only the top-level status is final: a per-platform "success": false can still be retried by Upload-Post."""
    status = str(payload.get("status", "")).lower()
    result = next((item for item in payload.get("results") or [] if item.get("platform") == "tiktok"), {})
    if status == "completed":
        return "published", result.get("post_url") or result.get("post_id"), None
    if status in {"failed", "not_found"}:
        return "failed", None, result.get("message") or payload.get("message") or "Failed to publish on TikTok"
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
        raise HTTPException(status_code=409, detail=f"{channel['name']}: it is already uploading, already published, or the result is uncertain. Check {label} before sending again.")

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
                    raise HTTPException(status_code=409, detail="Automatic publishing was turned off or paused.")
                if auto_publish_at <= datetime.now(UTC) + timedelta(minutes=5):
                    auto_publish_at = next_publish_slot(current, db)
                    db.execute("UPDATE publications SET scheduled_at=?, updated_at=? WHERE job_id=? AND channel_id=? AND state='uploading'",
                               (auto_publish_at.isoformat(), now(), job_id, channel["id"]))
    except BaseException as exc:  # nothing has reached the platform yet, so a plain retry stays safe
        message = str(getattr(exc, "detail", exc))[:500] or "Envio cancelado"
        finish("failed", error=message)
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(status_code=502, detail=f"{channel['name']}: could not prepare the upload: {message}") from exc
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
        raise HTTPException(status_code=422, detail="Choose a schedule date in the future.")
    with database() as db:
        job = job_row_or_404(db, job_id)
        if job["state"] != "approved":
            raise HTTPException(status_code=409, detail="Approve the video before publishing.")
        require_quality_approval(db, job)
        artifact = db.execute("SELECT * FROM artifacts WHERE job_id=? AND kind='video' ORDER BY created_at LIMIT 1", (job_id,)).fetchone()
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        if not artifact or not stored:
            raise HTTPException(status_code=409, detail="Final video or metadata is missing.")
        channel_ids = input.channel_ids or [row["channel_id"] for row in db.execute(
            "SELECT channel_id FROM publications WHERE job_id=? AND channel_id IS NOT NULL AND state IN ('pending', 'failed', 'reconciliation_required')", (job_id,)
        )]
        if not channel_ids:
            raise HTTPException(status_code=409, detail="Choose the channel where the video will be published.")
        channels = [channel_row_or_404(db, channel_id) for channel_id in dict.fromkeys(channel_ids)]
    # Every target is checked before the first upload, so a bad pick doesn't leave the video half-published.
    validate_publish_channels(channels, job["format"])
    for channel in channels:
        if channel["platform"] == "youtube" and not youtube_token_file(channel["id"]).is_file():
            raise HTTPException(status_code=409, detail=f"Channel {channel['name']} lost its authorization. Reconnect it in Channels.")
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
                raise HTTPException(status_code=409, detail=f"{channel['name']}: it was already sent or the result is uncertain, so it cannot be scheduled.")
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


# ── Agents ───────────────────────────────────────────────────────────────────
logger = logging.getLogger("cockpit.agents")
AGENT_LOOP_SECONDS = 60
OPERATOR_MINUTES = 8  # calibration knob: length of the long videos the operator writes
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
    raise HTTPException(status_code=409, detail="There is no free slot in the next 60 days.")


def pause_operator(channel_id: str, reason: str, job_id: str | None = None) -> None:
    with database() as db:
        db.execute("UPDATE operators SET paused_reason=?, updated_at=? WHERE channel_id=?", (reason[:500], now(), channel_id))
    notify(f"operator-paused:{channel_id}:{job_id or reason}", "operator_paused", "Automation paused", reason, job_id)


def youtube_video_status(credentials: Any, video_id: str) -> dict[str, Any] | None:
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    items = youtube.videos().list(part="status", id=video_id).execute().get("items") or []
    return items[0].get("status") if items else None


async def verify_auto_youtube_publication(job_id: str, channel_id: str, external_id: str, slot: datetime) -> None:
    credentials = await asyncio.to_thread(youtube_credentials, channel_id)
    status = await asyncio.to_thread(youtube_video_status, credentials, external_id)
    if not status:
        raise HTTPException(status_code=502, detail="YouTube did not return the uploaded video.")
    privacy = status.get("privacyStatus")
    if privacy == "public":
        if datetime.now(UTC) < slot - timedelta(minutes=2):
            raise HTTPException(status_code=502, detail="The video became public before the scheduled time.")
        with database() as db:
            db.execute("UPDATE publications SET state='published', updated_at=? WHERE job_id=? AND channel_id=? AND state='platform_scheduled'",
                       (now(), job_id, channel_id))
        return
    published_at = status.get("publishAt")
    if privacy != "private" or not published_at or abs((parse_time(published_at) - slot).total_seconds()) > 60:
        raise HTTPException(status_code=502, detail="YouTube did not confirm the scheduled publication.")
    if datetime.now(UTC) > slot + timedelta(minutes=15):
        raise HTTPException(status_code=502, detail="The video is still private after the scheduled time.")


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
            raise HTTPException(status_code=409, detail="The channel has no valid authorization. Reconnect it in Channels.")
        artifact = db.execute("SELECT remote_path FROM artifacts WHERE job_id=? AND kind='video' LIMIT 1", (job_id,)).fetchone()
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        if not artifact or not stored:
            raise HTTPException(status_code=409, detail="Final video or metadata is missing.")
        metadata, errors, _ = normalize_youtube_metadata(json.loads(stored["value_json"]))
        if errors:
            raise HTTPException(status_code=409, detail="Invalid metadata: " + ", ".join(errors))
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
                raise HTTPException(status_code=502, detail="The final video is empty.")
            with database() as db:
                current = db.execute(
                    "SELECT o.enabled, o.auto_publish, o.paused_reason, c.status FROM operators o "
                    "JOIN channels c ON c.id=o.channel_id WHERE o.channel_id=?", (channel["id"],)
                ).fetchone()
                if not current or not current["enabled"] or not current["auto_publish"] or current["paused_reason"] or current["status"] != "connected":
                    raise HTTPException(status_code=409, detail="Automatic authorization or the channel connection changed during preparation.")
                if not youtube_token_file(channel["id"]).is_file():
                    raise HTTPException(status_code=409, detail="YouTube authorization is missing.")
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
            "AND EXISTS (SELECT 1 FROM generation_quality q WHERE q.item_id=jobs.generation_item_id AND q.job_id=jobs.id AND q.state='approved') "
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
                       ("Upload interrupted; check YouTube before trying again.", now(), row["id"]))
        pause_operator(row["channel_id"], "The upload result is uncertain; check YouTube.", row["job_id"])
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
            logger.warning("Reconciliation: job %s failed: %s", job_id, getattr(exc, "detail", exc))


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
            logger.warning("Operator: job %s did not advance: %s", job_id, getattr(exc, "detail", exc))
            with database() as db:
                channel = db.execute(
                    "SELECT p.channel_id FROM publications p JOIN operators o ON o.channel_id=p.channel_id "
                    "WHERE p.job_id=? AND o.auto_publish=1 LIMIT 1", (job_id,)
                ).fetchone()
            if channel:
                pause_operator(channel["channel_id"], str(getattr(exc, "detail", exc)), job_id)


def pause_unapproved_operator_reviews() -> None:
    """A blocked final review must stop the channel instead of waiting silently for upload."""
    with database() as db:
        blocked = [dict(row) for row in db.execute(
            "SELECT j.id, p.channel_id, q.state FROM video_jobs j "
            "JOIN publications p ON p.job_id=j.id JOIN operators o ON o.channel_id=p.channel_id "
            "JOIN generation_quality q ON q.job_id=j.id "
            "WHERE j.created_by='operator' AND j.auto_publish_authorized=1 AND j.deleted_at IS NULL "
            "AND q.state IN ('blocked','error') AND o.enabled=1 AND o.auto_publish=1 AND o.paused_reason IS NULL"
        )]
    for row in blocked:
        pause_operator(row["channel_id"], "The final review did not approve the video. Fix the report before turning automation back on.", row["id"])


def pause_failed_operator_jobs() -> None:
    with database() as db:
        failed = [dict(row) for row in db.execute(
            "SELECT j.id, j.error, p.channel_id FROM video_jobs j JOIN publications p ON p.job_id=j.id "
            "JOIN operators o ON o.channel_id=p.channel_id WHERE j.created_by='operator' AND j.state IN ('failed','interrupted') "
            "AND NOT EXISTS (SELECT 1 FROM generation_quality q WHERE q.item_id=j.generation_item_id AND q.state IN ('pending','reviewing','repairing')) "
            "AND o.enabled=1 AND o.auto_publish=1 AND o.paused_reason IS NULL AND j.updated_at > o.updated_at"
        )]
    for row in failed:
        pause_operator(row["channel_id"], row["error"] or "Generation failed; check the job.", row["id"])


def recover_operator_leases() -> None:
    with database() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT channel_id, lease_owner FROM operators WHERE lease_owner IS NOT NULL AND lease_until <= ?", (now(),)
        )]
        for row in rows:
            db.execute("UPDATE operator_runs SET state='interrupted', error=?, finished_at=? WHERE id=? AND state='running'",
                       ("The worker stopped before the run finished.", now(), row["lease_owner"]))
            db.execute("UPDATE operators SET lease_owner=NULL, lease_until=NULL, paused_reason=?, updated_at=? WHERE channel_id=? AND lease_owner=?",
                       ("The run was interrupted; check the job before turning automation back on.", now(), row["channel_id"], row["lease_owner"]))
    for row in rows:
        notify(f"operator-interrupted:{row['lease_owner']}", "operator_paused", "Automation paused",
               "The worker stopped; check the job before turning automation back on.")


async def agent_tick() -> None:
    for check in (recover_operator_leases, pause_failed_operator_jobs, pause_unapproved_operator_reviews):
        try:
            check()
        except Exception:
            logger.exception("Agent loop step %s failed", check.__name__)
    for step in (reconcile_stale_jobs, reconcile_auto_publications, publish_due_schedules, reconcile_processing_publications, run_due_operators, prepare_operator_jobs, prepare_auto_publications):
        try:
            await step()
        except Exception:
            logger.exception("Agent loop step %s failed", step.__name__)


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
        exempt = {"/api/health", "/api/health/live", "/api/health/ready", "/api/version", "/api/auth/login", "/favicon.ico"}
        protected = path.startswith("/api/") or path == "/docs" or path == "/openapi.json"
        session = None
        if protected and path not in exempt:
            session = session_from_request(request)
            if not session:
                return JSONResponse({"detail": "Authentication required."}, status_code=401)
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                supplied = request.headers.get("X-CSRF-Token", "")
                if not secrets.compare_digest(supplied, session["csrf_token"]):
                    return JSONResponse({"detail": "Invalid CSRF token."}, status_code=403)
        response = await call_next(request)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        if request.url.path.startswith("/api/") and request.url.path != "/api/logs":
            try:
                record_observability_event(
                    trace_id=trace_id, method=request.method, path=request.url.path,
                    status_code=500, duration_ms=duration_ms, spans=trace["spans"],
                    error=f"{request.method} {request.url.path} failed: {type(exc).__name__}",
                )
            except Exception:
                logging.getLogger("cockpit.observability").exception("Failed to record the event")
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
            logging.getLogger("cockpit.observability").exception("Failed to record the event")
    is_app_shell = request.url.path == "/" or request.url.path.startswith("/shared/") or request.url.path.endswith(".html")
    if request.method in {"GET", "HEAD"} and is_app_shell:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_ROOT / "Cockpit.html")


@app.get("/Login.html", include_in_schema=False)
def login_page() -> Response:
    if LOGIN_DISABLED:
        return RedirectResponse("/", status_code=302)
    return FileResponse(STATIC_ROOT / "Login.html")


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


app.include_router(channels_router)
app.include_router(agents_router)
app.include_router(auto_generation_router)
app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
