"""Channel connection routes. Paths match the handlers that used to live in gateway.main."""
from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from gateway.deps import (
    CHANNEL_PROFILE_FIELDS,
    YOUTUBE_SCOPES,
    channel_row_or_404,
    channel_video_defaults,
    channel_video_profile_fallback,
    database,
    default_voice_for_language,
    normalize_channel_profile,
    normalize_channel_video_defaults,
    normalize_visual_guides,
    now,
    row_as_channel,
    runtime,
    update_channel,
    youtube_client_file,
    youtube_token_file,
)

router = APIRouter()

# ponytail: in-memory OAuth state; a Gateway restart mid-consent just means clicking "Conectar" again.
oauth_flows: dict[str, tuple[Any, dict[str, Any], str]] = {}



@router.get("/api/channels")
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


@router.patch("/api/channels/{channel_id}")
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


@router.delete("/api/channels/{channel_id}", status_code=204, response_model=None)
async def delete_channel(channel_id: str) -> None:
    with database() as db:
        channel = channel_row_or_404(db, channel_id)
        # Clearing external_id frees UNIQUE(platform, external_id), so the same account can be connected again later.
        db.execute("UPDATE channels SET deleted_at=?, external_id=NULL, updated_at=? WHERE id=?", (now(), now(), channel_id))
    youtube_token_file(channel_id).unlink(missing_ok=True)
    if channel["platform"] == "tiktok" and channel["external_id"]:
        try:
            await runtime().upload_post_request("DELETE", "/api/uploadposts/users", json={"username": channel["external_id"]})
        except HTTPException:
            pass  # the Cockpit side is already gone; an orphan Upload-Post profile only costs a slot


@router.get("/api/channels/youtube/connect")
def youtube_connect(
    request: Request, niche: str = "", language: str = "pt-BR", system_prompt: str = "",
    voice_name: str = "pt-BR-FranciscaNeural", voice_rate: float = 1.0, voice_volume: float = 1.0,
    subtitle_enabled: bool = True, subtitle_position: str = "bottom", font_size: int = 56,
    text_fore_color: str = "#FFFFFF", stroke_color: str = "#000000", stroke_width: float = 1.5,
    publish_privacy: Literal["private", "public"] = "private", video_defaults: str = "", visual_guides: str = "",
) -> Any:
    from google_auth_oauthlib.flow import Flow

    session = runtime().session_from_request(request)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
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
        raise HTTPException(status_code=422, detail="The channel video parameters are invalid.") from None
    video_profile = normalize_channel_video_defaults(raw_video_defaults, fallback=channel_video_profile_fallback(profile), language=profile["language"])
    try:
        guides = normalize_visual_guides(json.loads(visual_guides) if visual_guides else {})
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="The channel visual guides are invalid.") from None
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
        "external_id": items[0]["id"], "name": snippet.get("title") or "YouTube channel", "handle": snippet.get("customUrl") or "",
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


@router.get("/api/youtube/auth/callback", name="youtube_auth_callback")
def youtube_auth_callback(request: Request, state: str = "", error: str = "") -> HTMLResponse:
    pending = oauth_flows.get(state)
    session = runtime().session_from_request(request)
    if error or not pending or not session or not secrets.compare_digest(pending[2], session["token_hash"]):
        oauth_flows.pop(state, None)
        return HTMLResponse(f"<p>YouTube authorization was not completed: {html.escape(error or 'the session expired, try again')}.</p>", status_code=400)
    oauth_flows.pop(state, None)
    flow, options, _ = pending
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # the redirect is plain-http loopback
    flow.fetch_token(authorization_response=str(request.url))
    info = youtube_channel_info(flow.credentials)
    if not info:
        return HTMLResponse("<p>This Google account has no YouTube channel. Create the channel and connect again.</p>", status_code=400)
    channel_id = save_youtube_channel(info, options)
    token_file = youtube_token_file(channel_id)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(flow.credentials.to_json(), encoding="utf-8")
    return HTMLResponse(f"<p>Channel <b>{html.escape(info['name'])}</b> connected. You can close this tab and return to the Cockpit.</p>")


async def upload_post_connect_link(username: str) -> str:
    link = await runtime().upload_post_request("POST", "/api/uploadposts/users/generate-jwt", json={"username": username, "platforms": ["tiktok"]})
    if not link.get("access_url"):
        raise HTTPException(status_code=502, detail="Upload-Post did not return the connection link")
    return link["access_url"]


@router.post("/api/channels/tiktok/connect", status_code=201)
async def tiktok_connect(input: ChannelConnect) -> dict[str, Any]:
    """TikTok goes through one Upload-Post profile per Cockpit channel; the user links the
    TikTok account on Upload-Post's own page (their TikTok app is audited, ours would need
    a TikTok review plus an HTTPS redirect). The channel stays "pending" until /refresh sees it."""
    visual_guides = normalize_visual_guides(input.visual_guides)
    username = f"mp-{uuid4().hex[:16]}"
    await runtime().upload_post_request("POST", "/api/uploadposts/users", json={"username": username})
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


@router.post("/api/channels/{channel_id}/tiktok-link")
async def tiktok_reconnect_link(channel_id: str) -> dict[str, str]:
    with database() as db:
        channel = channel_row_or_404(db, channel_id)
    if channel["platform"] != "tiktok":
        raise HTTPException(status_code=409, detail="This channel is not a TikTok channel")
    return {"access_url": await upload_post_connect_link(channel["external_id"])}


def tiktok_account(payload: dict[str, Any]) -> dict[str, Any] | None:
    profile = payload.get("profile") or payload
    account = (profile.get("social_accounts") or {}).get("tiktok")
    return account if isinstance(account, dict) and account else None


@router.post("/api/channels/{channel_id}/refresh")
async def refresh_channel(channel_id: str) -> dict[str, Any]:
    with database() as db:
        channel = channel_row_or_404(db, channel_id)
    if channel["platform"] == "youtube":
        if not youtube_token_file(channel_id).is_file():
            update_channel(channel_id, status="expired")
        else:
            try:
                info = await asyncio.to_thread(lambda: youtube_channel_info(runtime().youtube_credentials(channel_id)))
            except RuntimeError as exc:  # revoked token; youtube_credentials already marked the channel expired
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Could not query YouTube: {exc}") from exc
            if info:
                update_channel(channel_id, name=info["name"], handle=info["handle"], stats_json=json.dumps(info["stats"]), status="connected")
    else:
        account = tiktok_account(await runtime().upload_post_request("GET", f"/api/uploadposts/users/{channel['external_id']}"))
        if account:
            name = account.get("display_name") or account.get("handle") or "TikTok"
            update_channel(channel_id, name=name, handle=account.get("handle") or "", status="expired" if account.get("reauth_required") else "connected")
        elif channel["status"] == "connected":
            update_channel(channel_id, status="expired")
    with database() as db:
        return row_as_channel(channel_row_or_404(db, channel_id))



