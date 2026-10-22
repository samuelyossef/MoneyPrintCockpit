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
LOGIN_DISABLED = os.getenv("COCKPIT_DISABLE_LOGIN", "0") == "1"
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


METADATA_STATES = {"completed", "approved"}


class MetadataInput(BaseModel):
    title: str = Field(default="", max_length=1000)
    description: str = Field(default="", max_length=20000)
    tags: list[str] | str = Field(default_factory=list, max_length=500)
    category_id: str = Field(default="22", max_length=10)



class AssistantChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=3000)


class AssistantChatInput(BaseModel):
    messages: list[AssistantChatMessage] = Field(min_length=1, max_length=16)
    channel_id: str | None = Field(default=None, max_length=36)


WORDS_PER_MINUTE = 150  # calibration knob: Edge TTS narrates ~150 words/min at voice rate 1.0
SHORT_TARGET_SECONDS = {4: 60, 5: 75, 6: 90}  # mirrors the Shorts "Duração alvo" select in cockpit-views.js
SHORT_MIN_SECONDS = 60  # below this a Short is "curto demais" (see the word-count hint in moneyprinter-runtime.js)


class ScriptProposalInput(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    format: Literal["short", "long"] = "short"
    language: str = Field(default="pt-BR", max_length=20)
    channel_context: str = Field(default="", max_length=2000)


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


MAX_VISUAL_TERMS = 80  # ponytail: cap on Pexels searches per video; raise alongside the API's hourly quota


class VisualTermsInput(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    script: str = Field(min_length=1, max_length=40000)
    clip_seconds: float = Field(default=5, ge=1, le=30)
    voice_rate: float = Field(default=1.0, ge=0.5, le=2.0)


# youtube.readonly is what lets the callback find out which channel was picked on Google's consent screen.
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly", "https://www.googleapis.com/auth/yt-analytics.readonly"]
LANGUAGE_PATTERN = r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*"
UPLOAD_POST_BASE = "https://api.upload-post.com"
PLATFORM_LABELS = {"youtube": "YouTube Studio", "tiktok": "TikTok"}
# ponytail: in-memory OAuth state; a Gateway restart mid-consent just means clicking "Conectar" again.
oauth_flows: dict[str, tuple[Any, dict[str, Any], str]] = {}


class PublishRejected(Exception):
    """The platform answered 4xx: the upload definitely did not create a post, so a retry is safe."""


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


class PublishInput(BaseModel):
    channel_ids: list[str] | None = Field(default=None, max_length=5)  # None: the job's pending/failed targets
    confirm_not_uploaded: bool = False
    publish_at: datetime | None = None  # future: validate now, upload when the agent loop reaches it


# ── Agentes ──────────────────────────────────────────────────────────────────
logger = logging.getLogger("cockpit.agents")
AGENT_LOOP_SECONDS = 60
PROJECT_NAMES = {"long": "Vídeos longos", "short": "Shorts"}  # the same projects the Gerador and Shorts screens use
REGION_BY_LANGUAGE = {"pt": "BR", "en": "US", "es": "ES", "de": "DE"}
VOICE_BY_LANGUAGE = {"pt": "pt-BR-FranciscaNeural", "en": "en-US-JennyNeural", "es": "es-ES-ElviraNeural", "de": "de-DE-KatjaNeural"}
OPERATOR_MINUTES = 8  # calibration knob: length of the long videos the operator writes
ANALYTICS_METRICS = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,subscribersGained"


class StrategyInput(BaseModel):
    channel_id: str
    format: Literal["long", "short"] = "short"
    count: int = Field(default=5, ge=1, le=10)


PUBLISHED_YOUTUBE = (
    "FROM publications AS p JOIN video_jobs AS jobs ON jobs.id = p.job_id LEFT JOIN channels ON channels.id = p.channel_id "
    "LEFT JOIN job_metadata AS meta ON meta.job_id = jobs.id LEFT JOIN video_analytics AS stats ON stats.publication_id = p.id "
    "WHERE p.platform='youtube' AND p.state='published' AND p.channel_id IS NOT NULL AND jobs.deleted_at IS NULL"
)


class OperatorInput(BaseModel):
    enabled: bool = False
    format: Literal["long", "short"] = "short"
    cadence_hours: Literal[12, 24, 48, 168] = 24
    next_run_at: datetime | None = None
    auto_publish: bool = False
    publish_days: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    publish_time: str = "12:00"
    publish_timezone: str = "America/Sao_Paulo"


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



# HTTP routes live in gateway/routers; shared services and app assembly stay here.
from gateway.routers import agents, assistant, auth, channels, jobs, projects, scripts, settings, system
app.include_router(system.router)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(settings.router)
app.include_router(jobs.router)
app.include_router(assistant.router)
app.include_router(scripts.router)
app.include_router(channels.router)
app.include_router(agents.router)

# Backwards-compatible facade: route handlers stay reachable as gateway.main.<name>.
from gateway.routers.system import app_version, health, health_live, list_notifications, list_observability_events, read_all_notifications, read_notification, system_status
from gateway.routers.auth import auth_session, login, logout
from gateway.routers.projects import create_project, delete_project, get_project, list_projects, patch_project
from gateway.routers.settings import generation_defaults, list_nvidia_models, provider_readiness, put_generation_defaults, test_nvidia_model, update_providers, validate_provider_key
from gateway.routers.jobs import approve_job, artifact_proxy, cancel_job, cancel_schedule, create_job, delete_job, derive_shorts, generate_metadata, get_job, job_events, list_jobs, publish_job, put_metadata, retry_job
from gateway.routers.assistant import assistant_chat
from gateway.routers.scripts import long_script, long_script_stream, script_proposal, script_reference, script_terms, short_script
from gateway.routers.channels import delete_channel, list_channels, patch_channel, refresh_channel, tiktok_connect, tiktok_reconnect_link, youtube_auth_callback, youtube_connect
from gateway.routers.agents import analytics_overview, analytics_platform_overview, list_operators, list_schedule, put_operator, refresh_analytics, run_operator, strategy


# Domain services: implementations live in gateway/services; this module stays the
# shared namespace (config, models, app assembly) and re-exports every service.
from gateway.services.util import (  # noqa: F401
    now,
    as_utc,
    parse_time,
)
from gateway.services.db import (  # noqa: F401
    connect,
    database,
    database_initialization_lock,
    initialize_database,
    _initialize_database_locked,
)
from gateway.services.auth import (  # noqa: F401
    configured_admin,
    open_session,
    session_from_request,
    login_is_limited,
)
from gateway.services.observability import (  # noqa: F401
    notify,
    observability_service,
    observability_trace_id,
    record_observability_event,
    row_as_observability_event,
)
from gateway.services.projects import (  # noqa: F401
    row_as_project,
    get_project_or_404,
    get_or_create_project,
)
from gateway.services.turbo import (  # noqa: F401
    api_key,
    turbo_headers,
    is_retryable,
    send_with_retry,
    turbo_request,
    unwrap,
    map_turbo_state,
    turbo_relative_path,
    download_turbo_file,
)
from gateway.services.jobs import (  # noqa: F401
    row_as_job,
    stale_minutes_for,
    interrupt_job,
    sse_message,
    reconcile_job,
    reconcile_stale_jobs,
    build_turbo_payload,
    normalize_retry_payload,
    job_max_words,
    validate_publish_channels,
    artifact_or_404,
    derived_video,
    review_copy_for_artifact,
    local_video_response,
    job_row_or_404,
    normalize_youtube_metadata,
    store_metadata,
    require_quality_approval,
    job_language,
)
from gateway.services.settings import (  # noqa: F401
    default_generation_settings,
    settings_key,
    _toml_escape,
    _toml_set,
    detect_moonshot_region,
    nvidia_model_or_422,
    nvidia_model_catalog,
    nvidia_chat_body,
    _config_string,
    config_value,
    moonshot_config,
    llm_config,
    llm_task,
    llm_chat,
    parse_json_object,
    llm_json_object,
)
from gateway.services.scripts import (  # noqa: F401
    short_script_max_words,
    short_script_min_words,
    script_duration_budget,
    short_script_prompt,
    trim_to_words,
    normalize_proposal,
    reference_structure,
    clean_narration,
    long_script_tokens,
    _fit_long_script_duration,
    long_script_events,
    generate_long_script,
    split_script,
    voice_words_per_minute,
    narration_seconds,
    visual_term_count,
    _fill_visual_terms,
    visual_terms,
)
from gateway.services.assistant import (  # noqa: F401
    _assistant_channel,
)
from gateway.services.channels import (  # noqa: F401
    youtube_client_file,
    youtube_token_file,
    clean_language,
    default_voice_for_language,
    video_profile_from_settings,
    default_channel_video_defaults,
    channel_video_profile_fallback,
    normalize_channel_video_defaults,
    channel_video_defaults,
    normalize_channel_profile,
    channel_generation_settings,
    conflicting_channel_profile_fields,
    row_as_channel,
    normalize_visual_guides,
    channel_row_or_404,
    update_channel,
    upload_post_request,
    youtube_channel_info,
    save_youtube_channel,
    youtube_credentials,
    upload_post_connect_link,
    tiktok_account,
    youtube_description,
    youtube_upload,
    tiktok_upload,
    map_upload_post_status,
    reconcile_publications,
    publish_to_channel,
    schedule_publications,
)
from gateway.services.agents import (  # noqa: F401
    youtube_trends,
    suggest_topics,
    analytics_score,
    youtube_video_metrics,
    next_publish_slot,
    pause_operator,
    youtube_video_status,
    verify_auto_youtube_publication,
    auto_publish_operator_job,
    prepare_auto_publications,
    reconcile_auto_publications,
    publish_due_schedules,
    reconcile_processing_publications,
    run_due_operators,
    prepare_operator_jobs,
    pause_unapproved_operator_reviews,
    pause_failed_operator_jobs,
    recover_operator_leases,
    agent_tick,
    agent_loop,
)
app.include_router(auto_generation_router)
app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
