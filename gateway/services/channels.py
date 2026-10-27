"""Channel profiles, platform credentials, uploads and publication flow."""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4
import httpx
from fastapi import FastAPI, HTTPException, Request, Response

from gateway import main as m


def youtube_client_file() -> Path:
    return m.DATA_DIR / "secrets" / "youtube_client_secret.json"


def youtube_token_file(channel_id: str) -> Path:
    return m.DATA_DIR / "secrets" / "youtube_tokens" / f"{channel_id}.json"


def clean_language(value: str) -> str:
    return value if re.fullmatch(m.LANGUAGE_PATTERN, value or "") else "pt-BR"


def default_voice_for_language(language: str) -> str:
    prefix = m.clean_language(language).split("-", 1)[0]
    return m.VOICE_BY_LANGUAGE.get(prefix, "pt-BR-FranciscaNeural")


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
    profiles = {format: m.video_profile_from_settings(format, m.generation_defaults(format)) for format in ("short", "long")}
    for profile in profiles.values():
        profile["voice_name"] = m.default_voice_for_language(language)
    return profiles


def channel_video_profile_fallback(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Copy the legacy shared channel profile to both formats for older API clients."""
    defaults = m.default_channel_video_defaults(str(profile.get("language") or "pt-BR"))
    media = {field: profile[field] for field in m.CHANNEL_PROFILE_FIELDS if field in profile}
    return {format: {**defaults[format], **media} for format in ("short", "long")}


def normalize_channel_video_defaults(values: Any, *, fallback: dict[str, Any] | None = None, language: str = "pt-BR") -> dict[str, dict[str, Any]]:
    """Validate voice, captions and render settings stored independently per format."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise HTTPException(status_code=422, detail="Os parâmetros de vídeo do canal devem ser um objeto.")
    base = fallback if fallback is not None else m.default_channel_video_defaults(language)
    result: dict[str, dict[str, Any]] = {}
    for format in ("short", "long"):
        raw = values.get(format, {})
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail=f"Os parâmetros de {format} devem ser um objeto.")
        allowed = set(m.CHANNEL_VIDEO_PROFILE_FIELDS[format])
        unknown = set(raw) - allowed
        if unknown:
            raise HTTPException(status_code=422, detail=f"Parâmetro de vídeo não suportado: {sorted(unknown)[0]}.")
        current = dict(base.get(format, {}))
        current.update(raw)
        media = m.normalize_channel_profile({"language": language, **current})
        media.pop("language")  # language remains channel-wide; only voice and captions vary by format.
        try:
            clip_duration = int(current["video_clip_duration"])
            clip_speed = float(current["video_clip_speed"])
            bgm_volume = float(current["bgm_volume"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Parâmetros de vídeo numéricos inválidos.") from None
        if not 1 <= clip_duration <= 30:
            raise HTTPException(status_code=422, detail="A duração de cada clipe deve estar entre 1 e 30 segundos.")
        if clip_speed not in m.VIDEO_CLIP_SPEEDS:
            raise HTTPException(status_code=422, detail="Velocidade de clipe inválida.")
        if bgm_volume not in m.VIDEO_BGM_VOLUMES:
            raise HTTPException(status_code=422, detail="Volume da música inválido.")
        concat_mode = str(current.get("video_concat_mode", ""))
        transition = str(current.get("video_transition_mode", ""))
        bgm_type = str(current.get("bgm_type", ""))
        if concat_mode not in m.VIDEO_CONCAT_MODES:
            raise HTTPException(status_code=422, detail="Modo de imagens inválido.")
        if transition not in m.VIDEO_TRANSITIONS:
            raise HTTPException(status_code=422, detail="Transição inválida.")
        if bgm_type not in m.VIDEO_BGM_TYPES:
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
    legacy = m.normalize_channel_profile(dict(channel))
    fallback = m.channel_video_profile_fallback(legacy)
    return m.normalize_channel_video_defaults(values, fallback=fallback, language=legacy["language"])


def normalize_channel_profile(values: dict[str, Any], *, require_all: bool = True) -> dict[str, Any]:
    """Validate one render profile shared by channel forms, direct API calls and OAuth setup."""
    language = m.clean_language(str(values.get("language", "pt-BR")))
    voice_name = str(values.get("voice_name") or m.default_voice_for_language(language))
    allowed_voices = m.VOICE_CHOICES.get(language)
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
    if profile["subtitle_position"] not in m.CHANNEL_PROFILE_POSITIONS:
        raise HTTPException(status_code=422, detail="Posição de legenda inválida.")
    for field in m.CHANNEL_PROFILE_COLORS:
        color = str(values.get(field, "#FFFFFF" if field == "text_fore_color" else "#000000")).upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", color):
            raise HTTPException(status_code=422, detail=f"{field} deve usar a cor hexadecimal #RRGGBB.")
        profile[field] = color
    return profile


def channel_generation_settings(channel: sqlite3.Row | dict[str, Any], format: Literal["long", "short"]) -> dict[str, Any]:
    values = dict(channel)
    values.update(m.channel_video_defaults(channel)[format])
    profile = m.normalize_channel_profile(values)
    return {"video_language": profile.pop("language"), **profile, **m.channel_video_defaults(channel)[format]}


def conflicting_channel_profile_fields(channels: list[sqlite3.Row], format: Literal["long", "short"]) -> list[str]:
    if len(channels) < 2:
        return []
    profiles = [m.channel_generation_settings(channel, format) for channel in channels]
    fields = ("video_language", *m.CHANNEL_PROFILE_FIELDS, *m.CHANNEL_VIDEO_PROFILE_FIELDS[format])
    return [field for field in fields if any(profile[field] != profiles[0][field] for profile in profiles[1:])]


def row_as_channel(row: sqlite3.Row) -> dict[str, Any]:
    result = {key: row[key] for key in ("id", "platform", "name", "handle", "external_id", "status", "niche", "language", "system_prompt", "persona", "visual_style", "image_source", "thumbnail_prompt", "publish_privacy", *m.CHANNEL_PROFILE_FIELDS, "created_at", "updated_at")}
    result["subtitle_enabled"] = bool(result["subtitle_enabled"])
    result["video_defaults"] = m.channel_video_defaults(row)
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
    with m.database() as db:
        db.execute(f"UPDATE channels SET {assignments}, updated_at=? WHERE id=?", (*fields.values(), m.now(), channel_id))


async def upload_post_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    key = m.config_value("upload_post_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure a chave Upload-Post em Configurações para usar canais TikTok.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            response = await m.send_with_retry(client, method, f"{m.UPLOAD_POST_BASE}{path}", headers={"Authorization": f"Apikey {key}"}, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Upload-Post indisponível: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Upload-Post respondeu {response.status_code}: {response.text[:300]}")
    return response.json()


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
    with m.database() as db:
        existing = db.execute("SELECT id FROM channels WHERE platform='youtube' AND external_id=? AND deleted_at IS NULL", (info["external_id"],)).fetchone()
        if existing:
            db.execute(
                "UPDATE channels SET name=?, handle=?, status='connected', stats_json=?, updated_at=? WHERE id=?",
                (info["name"], info["handle"], json.dumps(info["stats"]), m.now(), existing["id"]),
            )
            return existing["id"]
        profile = m.normalize_channel_profile(options)
        video_defaults = m.normalize_channel_video_defaults(options.get("video_defaults", {}), fallback=m.channel_video_profile_fallback(profile), language=profile["language"])
        channel_id = str(uuid4())
        db.execute(
            "INSERT INTO channels(id, platform, name, handle, external_id, status, niche, language, system_prompt, persona, visual_style, image_source, thumbnail_prompt, publish_privacy, voice_name, voice_rate, voice_volume, subtitle_enabled, subtitle_position, font_size, text_fore_color, stroke_color, stroke_width, video_defaults_json, visual_guides_json, stats_json, created_at, updated_at) VALUES (?, 'youtube', ?, ?, ?, 'connected', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (channel_id, info["name"], info["handle"], info["external_id"], options.get("niche", ""), profile["language"], options.get("system_prompt", ""), options.get("persona", ""), options.get("visual_style", ""), options.get("image_source", ""), options.get("thumbnail_prompt", ""), options.get("publish_privacy", "private"), profile["voice_name"], profile["voice_rate"], profile["voice_volume"], int(profile["subtitle_enabled"]), profile["subtitle_position"], profile["font_size"], profile["text_fore_color"], profile["stroke_color"], profile["stroke_width"], json.dumps(video_defaults), json.dumps(m.normalize_visual_guides(options.get("visual_guides", {}))), json.dumps(info["stats"]), m.now(), m.now()),
        )
        return channel_id


def youtube_credentials(channel_id: str) -> Any:
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials

    token_file = m.youtube_token_file(channel_id)
    # The scopes granted at connect time, not YOUTUBE_SCOPES: refreshing with a scope the token never got
    # (e.g. Analytics, added later) fails with invalid_scope and would wrongly mark the channel expired.
    credentials = Credentials.from_authorized_user_file(str(token_file))
    if not credentials.valid:
        try:
            credentials.refresh(GoogleRequest())
        except RefreshError as exc:
            m.update_channel(channel_id, status="expired")
            raise RuntimeError("O acesso ao canal expirou ou foi revogado. Reconecte em Canais.") from exc
        token_file.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


async def upload_post_connect_link(username: str) -> str:
    link = await m.upload_post_request("POST", "/api/uploadposts/users/generate-jwt", json={"username": username, "platforms": ["tiktok"]})
    if not link.get("access_url"):
        raise HTTPException(status_code=502, detail="O Upload-Post não devolveu o link de conexão")
    return link["access_url"]


def tiktok_account(payload: dict[str, Any]) -> dict[str, Any] | None:
    profile = payload.get("profile") or payload
    account = (profile.get("social_accounts") or {}).get("tiktok")
    return account if isinstance(account, dict) and account else None


def youtube_description(metadata: dict[str, Any]) -> str:
    description = metadata["description"]
    if metadata.get("format") == "short" and "#shorts" not in description.lower():
        description = f"{description}\n\n#Shorts".strip()
    return description[:5000]


def youtube_upload(credentials: Any, video_path: Path, metadata: dict[str, Any], publish_at: datetime | None = None, publish_privacy: str = "private", thumbnail: Path | None = None) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    # YouTube's language list uses "pt" for Brazilian Portuguese, not "pt-BR".
    language = metadata["language"].split("-")[0]
    body = {
        "snippet": {
            "title": metadata["title"], "description": m.youtube_description(metadata), "tags": metadata["tags"],
            "categoryId": metadata["category_id"], "defaultLanguage": language, "defaultAudioLanguage": language,
        },
        "status": {"privacyStatus": "private" if publish_at else publish_privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True},
    }
    if publish_at:
        body["status"]["publishAt"] = m.as_utc(publish_at).isoformat().replace("+00:00", "Z")
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    upload = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    try:
        response = None
        while response is None:
            _, response = upload.next_chunk(num_retries=3)
    except HttpError as exc:
        if exc.resp.status < 500:
            raise m.PublishRejected(str(exc)) from exc
        raise
    video_id = response["id"]
    if thumbnail and thumbnail.is_file():
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg")).execute()
        except Exception as exc:
            m.logger.warning("Não foi possível definir a thumbnail do vídeo %s: %s", video_id, exc)
    return video_id


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
        response = await client.post(f"{m.UPLOAD_POST_BASE}/api/upload", headers={"Authorization": f"Apikey {m.config_value('upload_post_api_key')}"}, data=form, files=files)
    if 400 <= response.status_code < 500:
        raise m.PublishRejected(f"Upload-Post recusou o envio ({response.status_code}): {response.text[:300]}")
    response.raise_for_status()
    body = response.json()
    if body.get("request_id"):
        return body["request_id"], "processing"
    result = (body.get("results") or {}).get("tiktok") or {}
    if not (body.get("success") and result.get("success")):
        raise m.PublishRejected(result.get("error") or body.get("message") or "O Upload-Post não aceitou o envio")
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
    with m.database() as db:
        pending = db.execute("SELECT external_id FROM publications WHERE job_id=? AND state='processing'", (job_id,)).fetchone()
    if not pending:
        return
    try:
        payload = await m.upload_post_request("GET", "/api/uploadposts/status", params={"request_id": pending["external_id"]})
    except HTTPException:
        return  # stays "processing"; the next job read tries again
    state, external_id, error = m.map_upload_post_status(payload)
    if state == "processing":
        return
    with m.database() as db:
        db.execute(
            "UPDATE publications SET state=?, external_id=COALESCE(?, external_id), error=?, updated_at=? WHERE job_id=? AND state='processing'",
            (state, external_id, error, m.now(), job_id),
        )


async def publish_to_channel(job_id: str, channel: sqlite3.Row, video: Any, metadata: dict[str, Any], confirm_not_uploaded: bool, auto_publish_at: datetime | None = None) -> None:
    """`video` is an async callable returning the local MP4, so the download happens only after a claim succeeds."""
    platform, label = channel["platform"], m.PLATFORM_LABELS[channel["platform"]]
    # A "reconciliation_required" upload may already be live; only the operator can say it isn't.
    blocked = ("uploading", "processing", "published") + (() if confirm_not_uploaded else ("reconciliation_required",))
    with m.database() as db:
        # Atomic claim, so a double click or a second tab can't start a parallel upload.
        claimed = db.execute(
            "INSERT INTO publications(id, job_id, platform, channel_id, state, attempts, auto_publish, created_at, updated_at) VALUES (?, ?, ?, ?, 'uploading', 1, ?, ?, ?) "
            "ON CONFLICT(job_id, platform) DO UPDATE SET channel_id=excluded.channel_id, state='uploading', attempts=publications.attempts+1, "
            "auto_publish=excluded.auto_publish, scheduled_at=CASE WHEN excluded.auto_publish=0 THEN NULL ELSE publications.scheduled_at END, error=NULL, updated_at=excluded.updated_at "
            f"WHERE publications.state NOT IN ({','.join('?' * len(blocked))})",
            (str(uuid4()), job_id, platform, channel["id"], int(auto_publish_at is not None), m.now(), m.now(), *blocked),
        ).rowcount
    if not claimed:
        raise HTTPException(status_code=409, detail=f"{channel['name']}: já está sendo enviado, já foi publicado ou tem resultado incerto. Confira no {label} antes de reenviar.")

    def finish(state: str, external_id: str | None = None, error: str | None = None) -> None:
        with m.database() as db:
            db.execute(
                "UPDATE publications SET state=?, external_id=?, error=?, updated_at=? WHERE job_id=? AND platform=?",
                (state, external_id, error, m.now(), job_id, platform),
            )

    try:
        video_path = await video()
        credentials = await asyncio.to_thread(m.youtube_credentials, channel["id"]) if platform == "youtube" else None
        if auto_publish_at:
            with m.database() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM operators WHERE channel_id=?", (channel["id"],)).fetchone()
                if not current or not current["enabled"] or not current["auto_publish"] or current["paused_reason"]:
                    raise HTTPException(status_code=409, detail="A publicação automática foi desativada ou pausada.")
                if auto_publish_at <= datetime.now(UTC) + timedelta(minutes=5):
                    auto_publish_at = m.next_publish_slot(current, db)
                    db.execute("UPDATE publications SET scheduled_at=?, updated_at=? WHERE job_id=? AND channel_id=? AND state='uploading'",
                               (auto_publish_at.isoformat(), m.now(), job_id, channel["id"]))
    except BaseException as exc:  # nothing has reached the platform yet, so a plain retry stays safe
        message = str(getattr(exc, "detail", exc))[:500] or "Envio cancelado"
        finish("failed", error=message)
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(status_code=502, detail=f"{channel['name']}: não foi possível preparar o envio: {message}") from exc
    try:
        # ponytail: uploads run inside the request, one channel after another; move to a background task if long videos hit browser/proxy timeouts.
        if platform == "youtube":
            external_id, state = await asyncio.to_thread(m.youtube_upload, credentials, video_path, metadata, auto_publish_at, "private" if auto_publish_at else channel["publish_privacy"], m.thumbnail_path(job_id)), "platform_scheduled" if auto_publish_at else "published"
        else:
            external_id, state = await m.tiktok_upload(channel["external_id"], video_path, metadata, channel["publish_privacy"])
    except m.PublishRejected as exc:
        finish("failed", error=str(exc)[:500])
        raise HTTPException(status_code=502, detail=f"{channel['name']}: a plataforma recusou o envio: {exc}") from exc
    except BaseException as exc:
        finish("reconciliation_required", error=str(exc)[:500] or "Envio interrompido")
        if not isinstance(exc, Exception):
            raise
        raise HTTPException(status_code=502, detail=f"{channel['name']}: o envio foi interrompido e pode ter chegado ao canal. Confira no {label} antes de repetir.") from exc
    finish(state, external_id=external_id)


def schedule_publications(job_id: str, channels: list[sqlite3.Row], publish_at: datetime) -> dict[str, Any]:
    with m.database() as db:
        for channel in channels:
            claimed = db.execute(
                "INSERT INTO publications(id, job_id, platform, channel_id, state, scheduled_at, created_at, updated_at) VALUES (?, ?, ?, ?, 'scheduled', ?, ?, ?) "
                "ON CONFLICT(job_id, platform) DO UPDATE SET channel_id=excluded.channel_id, state='scheduled', scheduled_at=excluded.scheduled_at, error=NULL, updated_at=excluded.updated_at "
                "WHERE publications.state IN ('pending', 'failed', 'scheduled')",
                (str(uuid4()), job_id, channel["platform"], channel["id"], publish_at.isoformat(), m.now(), m.now()),
            ).rowcount
            if not claimed:  # the database() block rolls back the channels already scheduled
                raise HTTPException(status_code=409, detail=f"{channel['name']}: já foi enviado ou tem resultado incerto, não dá para agendar.")
        return {**m.row_as_job(m.job_row_or_404(db, job_id), db), "errors": []}
