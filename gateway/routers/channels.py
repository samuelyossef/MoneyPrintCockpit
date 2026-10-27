"""Channel, YouTube/TikTok connection and refresh routes."""
from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from gateway import main as m

router = APIRouter()


@router.get("/api/channels")
def list_channels() -> list[dict[str, Any]]:
    with m.database() as db:
        return [m.row_as_channel(row) for row in db.execute("SELECT * FROM channels WHERE deleted_at IS NULL ORDER BY created_at DESC")]


@router.patch("/api/channels/{channel_id}")
def patch_channel(channel_id: str, input: m.ChannelPatch) -> dict[str, Any]:
    with m.database() as db:
        channel = m.channel_row_or_404(db, channel_id)
    fields = input.model_dump(exclude_none=True)
    if "visual_guides" in fields:
        fields["visual_guides_json"] = json.dumps(m.normalize_visual_guides(fields.pop("visual_guides")), ensure_ascii=False)
    profile_changed = set(fields).intersection({"language", *m.CHANNEL_PROFILE_FIELDS})
    if profile_changed:
        combined = dict(channel)
        combined.update(fields)
        # Changing only the language chooses a compatible default voice; an explicitly
        # supplied incompatible voice remains a validation error.
        if "language" in fields and "voice_name" not in fields:
            combined["voice_name"] = m.default_voice_for_language(str(fields["language"]))
        fields.update(m.normalize_channel_profile(combined))
    raw_video_defaults = fields.pop("video_defaults", None)
    language = str(fields.get("language", channel["language"]))
    if profile_changed or raw_video_defaults is not None:
        fallback = m.channel_video_defaults(channel)
        if raw_video_defaults is None:
            raw_video_defaults = {"short": {}, "long": {}}
        if profile_changed:
            # Keep legacy API updates functional by applying shared fields to both formats.
            for format in ("short", "long"):
                for key in profile_changed.intersection(m.CHANNEL_PROFILE_FIELDS):
                    fallback[format][key] = fields[key]
                if "language" in profile_changed and "voice_name" not in profile_changed:
                    fallback[format]["voice_name"] = m.default_voice_for_language(language)
        fields["video_defaults_json"] = json.dumps(
            m.normalize_channel_video_defaults(raw_video_defaults, fallback=fallback, language=language)
        )
    if "system_prompt" in fields:
        fields["system_prompt"] = fields["system_prompt"].strip()
    for key in ("persona", "visual_style", "thumbnail_prompt"):
        if key in fields:
            fields[key] = fields[key].strip()
    if fields:
        m.update_channel(channel_id, **fields)
    with m.database() as db:
        return m.row_as_channel(m.channel_row_or_404(db, channel_id))


@router.delete("/api/channels/{channel_id}", status_code=204, response_model=None)
async def delete_channel(channel_id: str) -> None:
    with m.database() as db:
        channel = m.channel_row_or_404(db, channel_id)
        # Clearing external_id frees UNIQUE(platform, external_id), so the same account can be connected again later.
        db.execute("UPDATE channels SET deleted_at=?, external_id=NULL, updated_at=? WHERE id=?", (m.now(), m.now(), channel_id))
    m.youtube_token_file(channel_id).unlink(missing_ok=True)
    if channel["platform"] == "tiktok" and channel["external_id"]:
        try:
            await m.upload_post_request("DELETE", "/api/uploadposts/users", json={"username": channel["external_id"]})
        except HTTPException:
            pass  # the Cockpit side is already gone; an orphan Upload-Post profile only costs a slot


@router.get("/api/channels/youtube/connect")
def youtube_connect(
    request: Request, niche: str = "", language: str = "pt-BR", system_prompt: str = "",
    voice_name: str = "pt-BR-FranciscaNeural", voice_rate: float = 1.0, voice_volume: float = 1.0,
    subtitle_enabled: bool = True, subtitle_position: str = "bottom", font_size: int = 56,
    text_fore_color: str = "#FFFFFF", stroke_color: str = "#000000", stroke_width: float = 1.5,
    publish_privacy: Literal["private", "public"] = "private", video_defaults: str = "", visual_guides: str = "",
    persona: str = "", visual_style: str = "", image_source: Literal["", "stock", "ai_images"] = "", thumbnail_prompt: str = "",
) -> Any:
    from google_auth_oauthlib.flow import Flow

    session = m.session_from_request(request)
    if not session:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada.")
    if not m.youtube_client_file().is_file():
        return HTMLResponse("<p>Coloque o OAuth client do Google Cloud em <code>runtime/secrets/youtube_client_secret.json</code> e tente de novo.</p>", status_code=409)
    profile = m.normalize_channel_profile({
        "language": language, "voice_name": voice_name, "voice_rate": voice_rate, "voice_volume": voice_volume,
        "subtitle_enabled": subtitle_enabled, "subtitle_position": subtitle_position, "font_size": font_size,
        "text_fore_color": text_fore_color, "stroke_color": stroke_color, "stroke_width": stroke_width,
    })
    try:
        raw_video_defaults = json.loads(video_defaults) if video_defaults else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Os parâmetros de vídeo do canal são inválidos.") from None
    video_profile = m.normalize_channel_video_defaults(raw_video_defaults, fallback=m.channel_video_profile_fallback(profile), language=profile["language"])
    try:
        guides = m.normalize_visual_guides(json.loads(visual_guides) if visual_guides else {})
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Os guias visuais do canal são inválidos.") from None
    flow = Flow.from_client_secrets_file(str(m.youtube_client_file()), scopes=m.YOUTUBE_SCOPES, redirect_uri=str(request.url_for("youtube_auth_callback")))
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    m.oauth_flows[state] = (
        flow,
        {"niche": niche[:200], "system_prompt": system_prompt.strip()[:4000], "publish_privacy": publish_privacy, "video_defaults": video_profile, "visual_guides": guides,
         "persona": persona.strip()[:2000], "visual_style": visual_style.strip()[:1000], "image_source": image_source, "thumbnail_prompt": thumbnail_prompt.strip()[:1000], **profile},
        session["token_hash"],
    )
    return RedirectResponse(url)


@router.get("/api/youtube/auth/callback", name="youtube_auth_callback")
def youtube_auth_callback(request: Request, state: str = "", error: str = "") -> HTMLResponse:
    pending = m.oauth_flows.get(state)
    session = m.session_from_request(request)
    if error or not pending or not session or not secrets.compare_digest(pending[2], session["token_hash"]):
        m.oauth_flows.pop(state, None)
        return HTMLResponse(f"<p>Autorização do YouTube não concluída: {html.escape(error or 'sessão expirada, tente de novo')}.</p>", status_code=400)
    m.oauth_flows.pop(state, None)
    flow, options, _ = pending
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # the redirect is plain-http loopback
    flow.fetch_token(authorization_response=str(request.url))
    info = m.youtube_channel_info(flow.credentials)
    if not info:
        return HTMLResponse("<p>Esta conta Google não tem um canal do YouTube. Crie o canal e conecte de novo.</p>", status_code=400)
    channel_id = m.save_youtube_channel(info, options)
    token_file = m.youtube_token_file(channel_id)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(flow.credentials.to_json(), encoding="utf-8")
    return HTMLResponse(f"<p>Canal <b>{html.escape(info['name'])}</b> conectado. Pode fechar esta aba e voltar ao Cockpit.</p>")


@router.post("/api/channels/tiktok/connect", status_code=201)
async def tiktok_connect(input: m.ChannelConnect) -> dict[str, Any]:
    """TikTok goes through one Upload-Post profile per Cockpit channel; the user links the
    TikTok account on Upload-Post's own page (their TikTok app is audited, ours would need
    a TikTok review plus an HTTPS redirect). The channel stays "pending" until /refresh sees it."""
    visual_guides = m.normalize_visual_guides(input.visual_guides)
    username = f"mp-{uuid4().hex[:16]}"
    await m.upload_post_request("POST", "/api/uploadposts/users", json={"username": username})
    access_url = await m.upload_post_connect_link(username)
    channel_id = str(uuid4())
    profile = m.normalize_channel_profile(input.model_dump())
    video_defaults = m.normalize_channel_video_defaults(input.video_defaults, fallback=m.channel_video_profile_fallback(profile), language=profile["language"])
    with m.database() as db:
        db.execute(
            "INSERT INTO channels(id, platform, name, handle, external_id, status, niche, language, system_prompt, persona, visual_style, image_source, thumbnail_prompt, publish_privacy, voice_name, voice_rate, voice_volume, subtitle_enabled, subtitle_position, font_size, text_fore_color, stroke_color, stroke_width, video_defaults_json, visual_guides_json, created_at, updated_at) VALUES (?, 'tiktok', 'TikTok (aguardando login)', '', ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (channel_id, username, input.niche.strip(), profile["language"], input.system_prompt.strip(), input.persona.strip(), input.visual_style.strip(), input.image_source, input.thumbnail_prompt.strip(), input.publish_privacy, profile["voice_name"], profile["voice_rate"], profile["voice_volume"], int(profile["subtitle_enabled"]), profile["subtitle_position"], profile["font_size"], profile["text_fore_color"], profile["stroke_color"], profile["stroke_width"], json.dumps(video_defaults), json.dumps(visual_guides), m.now(), m.now()),
        )
        channel = m.row_as_channel(m.channel_row_or_404(db, channel_id))
    return {"channel": channel, "access_url": access_url}


@router.post("/api/channels/{channel_id}/tiktok-link")
async def tiktok_reconnect_link(channel_id: str) -> dict[str, str]:
    with m.database() as db:
        channel = m.channel_row_or_404(db, channel_id)
    if channel["platform"] != "tiktok":
        raise HTTPException(status_code=409, detail="Este canal não é do TikTok")
    return {"access_url": await m.upload_post_connect_link(channel["external_id"])}


@router.post("/api/channels/{channel_id}/refresh")
async def refresh_channel(channel_id: str) -> dict[str, Any]:
    with m.database() as db:
        channel = m.channel_row_or_404(db, channel_id)
    if channel["platform"] == "youtube":
        if not m.youtube_token_file(channel_id).is_file():
            m.update_channel(channel_id, status="expired")
        else:
            try:
                info = await asyncio.to_thread(lambda: m.youtube_channel_info(m.youtube_credentials(channel_id)))
            except RuntimeError as exc:  # revoked token; youtube_credentials already marked the channel expired
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Não foi possível consultar o YouTube: {exc}") from exc
            if info:
                m.update_channel(channel_id, name=info["name"], handle=info["handle"], stats_json=json.dumps(info["stats"]), status="connected")
    else:
        account = m.tiktok_account(await m.upload_post_request("GET", f"/api/uploadposts/users/{channel['external_id']}"))
        if account:
            name = account.get("display_name") or account.get("handle") or "TikTok"
            m.update_channel(channel_id, name=name, handle=account.get("handle") or "", status="expired" if account.get("reauth_required") else "connected")
        elif channel["status"] == "connected":
            m.update_channel(channel_id, status="expired")
    with m.database() as db:
        return m.row_as_channel(m.channel_row_or_404(db, channel_id))
