"""Video job lifecycle: payloads, reconciliation, artifacts, metadata, review."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from gateway import main as m


def row_as_job(row: sqlite3.Row, db: sqlite3.Connection | None = None) -> dict[str, Any]:
    result = {key: row[key] for key in row.keys() if key not in {"request_json", "turbo_payload_json"}}
    result["request"] = json.loads(row["request_json"])
    result["turbo"] = json.loads(row["turbo_payload_json"])
    thumbnail = m.thumbnail_path(row["id"])
    result["thumbnail_updated_at"] = thumbnail.stat().st_mtime if thumbnail.is_file() else None
    if db:
        result["artifacts"] = [dict(item) for item in db.execute("SELECT * FROM artifacts WHERE job_id = ?", (row["id"],))]
        result["publications"] = [dict(item) for item in db.execute("SELECT * FROM publications WHERE job_id = ?", (row["id"],))]
        metadata = db.execute("SELECT value_json FROM job_metadata WHERE job_id = ?", (row["id"],)).fetchone()
        result["metadata"] = json.loads(metadata["value_json"]) if metadata else None
        review = db.execute("SELECT value_json FROM job_reviews WHERE job_id=?", (row["id"],)).fetchone()
        result["review"] = json.loads(review["value_json"]) if review else None
    return result


def stale_minutes_for(job: sqlite3.Row) -> int:
    return m.SHORT_STALE_MINUTES if job["format"] == "short" else m.LONG_STALE_MINUTES


async def interrupt_job(job_id: str, reason: str, cancel_turbo: bool = True) -> None:
    with m.database() as db:
        job = db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
    if not job or job["state"] not in {"queued", "running"}:
        return
    if cancel_turbo and job["turbo_task_id"]:
        try:
            await m.turbo_request("DELETE", f"/api/v1/tasks/{job['turbo_task_id']}", retries=0)
        except HTTPException:
            pass
    with m.database() as db:
        db.execute(
            "UPDATE video_jobs SET state='interrupted', error=?, interruption_reason=?, updated_at=? WHERE id=?",
            (reason, reason, m.now(), job_id),
        )
    m.notify(f"job-interrupted:{job_id}:{reason}", "job_interrupted", f"Geração interrompida: {job['subject']}", reason, job_id)


def sse_message(event: str, data: str) -> str:
    # Real newlines: EventSource ignores a frame whose separators are a literal "\n".
    return f"event: {event}\ndata: {data}\n\n"


async def reconcile_job(job_id: str) -> dict[str, Any]:
    with m.database() as db:
        job = db.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        return {}
    # "approved" is a Cockpit-only state; re-reading Turbo would downgrade it to "completed".
    if not job["turbo_task_id"] or job["state"] in {"deleted", "interrupted", "approved"}:
        with m.database() as db:
            return m.row_as_job(job, db)
    response = await m.turbo_request("GET", f"/api/v1/tasks/{job['turbo_task_id']}")
    payload = m.unwrap(response.json())
    if not isinstance(payload, dict):
        return m.row_as_job(job)
    state, progress, stage, error = m.map_turbo_state(payload)
    completed_at = m.now() if state == "completed" else None
    notification: tuple[str, str, str] | None = None
    with m.database() as db:
        # A successful poll is not progress. Preserve a distinct heartbeat timestamp so
        # state=4 at the same percentage can be detected as stalled.
        advanced = state != job["state"] or progress > int(job["last_observed_progress"] or 0)
        progress_updated_at = m.now() if advanced else job["progress_updated_at"]
        db.execute(
            "UPDATE video_jobs SET state=?, progress=?, last_observed_progress=?, progress_updated_at=?, failed_stage=?, error=?, turbo_payload_json=?, updated_at=?, completed_at=COALESCE(completed_at, ?) WHERE id=?",
            (state, progress, max(progress, int(job["last_observed_progress"] or 0)), progress_updated_at, stage, error, json.dumps(payload), m.now(), completed_at, job_id),
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
                remote_path = m.turbo_relative_path(str(raw_path))
                filename = Path(remote_path).name
                db.execute(
                    "INSERT OR IGNORE INTO artifacts(id, job_id, kind, remote_path, filename, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid4()), job_id, kind, remote_path, filename, m.now()),
                )
        db.commit()
        current = db.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,)).fetchone()
        result = m.row_as_job(current, db)
    if notification:
        m.notify(f"{notification[0]}:{job_id}", notification[0], notification[1], notification[2], job_id)
    return result


async def reconcile_stale_jobs() -> None:
    """Jobs left in queued/running when the gateway stops are unverifiable on restart.
    Try to reconcile with Turbo; if that fails, mark them interrupted so the UI
    never shows a job as still processing when nothing is tracking it anymore."""
    with m.database() as db:
        stale_ids = [row["id"] for row in db.execute(
            "SELECT id FROM video_jobs WHERE state IN ('queued','running') AND deleted_at IS NULL"
        )]
    for job_id in stale_ids:
        try:
            await m.reconcile_job(job_id)
        except HTTPException:
            await m.interrupt_job(job_id, "Interrompido: MoneyPrinterTurbo indisponível durante a reconciliação.", cancel_turbo=False)
            continue
        with m.database() as db:
            current = db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
        if not current or current["state"] not in {"queued", "running"}:
            continue
        reference = m.parse_time(current["created_at"] if current["state"] == "queued" else current["progress_updated_at"] or current["updated_at"])
        age_minutes = (datetime.now(UTC) - reference).total_seconds() / 60
        timeout = m.QUEUE_STALE_MINUTES if current["state"] == "queued" else m.stale_minutes_for(current)
        if age_minutes >= timeout:
            label = "fila" if current["state"] == "queued" else "sem avanço de progresso"
            await m.interrupt_job(current["id"], f"Interrompido após {int(age_minutes)} min {label} (limite: {timeout} min).")


def build_turbo_payload(subject: str, script: str, settings: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"video_subject": subject.strip(), "video_script": script.strip(), "font_name": "Arial.ttf"}
    for key in m.TURBO_VIDEO_FIELDS:
        value = settings.get(key)
        # Turbo treats a blank transition as invalid and blank terms as "use these
        # (empty) terms"; omitting them lets Turbo apply its own defaults.
        if value is None or (value == "" and key in {"video_transition_mode", "video_terms"}):
            continue
        if key in m.BOUNDED_INT_FIELDS:
            low, high = m.BOUNDED_INT_FIELDS[key]
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
    raw_settings = {key: legacy[key] for key in m.TURBO_VIDEO_FIELDS if key in legacy}
    if "target_minutes" in legacy:
        raw_settings["target_minutes"] = legacy["target_minutes"]
    warnings: list[str] = []
    curated = bool(job["generation_item_id"]) and raw_settings.get("video_source") == "local"
    if raw_settings.get("video_source") not in ({None, "pexels", "pixabay", "coverr", "local"} if curated else {None, "pexels", "pixabay", "coverr"}):
        warnings.append("Fonte antiga convertida para Pexels.")
    raw_settings["video_source"] = raw_settings.get("video_source") if raw_settings.get("video_source") in ({"pexels", "pixabay", "coverr", "local"} if curated else {"pexels", "pixabay", "coverr"}) else "pexels"
    format = job["format"] if job["format"] in m.FORMAT_LOCKED else "short"
    settings = {**m.generation_defaults(format), **raw_settings, **m.FORMAT_LOCKED[format]}
    payload = m.build_turbo_payload(str(legacy.get("video_subject") or job["subject"]), str(legacy.get("video_script") or job["script"]), settings)
    if set(legacy) - set(m.TURBO_VIDEO_FIELDS) - {"video_subject", "video_script", "target_minutes"}:
        warnings.append("Campos legados não suportados foram removidos.")
    return payload, warnings


def job_max_words(format: str, settings: dict[str, Any]) -> int | None:
    """The narration budget the screen asked for: the Short's "Duração alvo" select, or the long
    video's target in minutes. None means no target was sent, so nothing is trimmed."""
    try:
        target = int(settings.get("paragraph_number", 5) or 5) if format == "short" else float(settings.get("target_minutes") or 0)
        rate = float(settings.get("voice_rate") or 1.0)
    except (TypeError, ValueError):
        return None
    return m.script_duration_budget(format, target, rate, str(settings.get("voice_name") or ""))["maximum_words"] or None


def validate_publish_channels(channels: list[sqlite3.Row], format: str | None) -> None:
    for channel in channels:
        if channel["status"] != "connected":
            raise HTTPException(status_code=409, detail=f"O canal {channel['name']} não está conectado. Reconecte em Canais.")
        if format == "long" and channel["platform"] != "youtube":
            raise HTTPException(status_code=409, detail="TikTok recebe apenas Shorts. Escolha um canal do YouTube para vídeos longos.")
    # publications is UNIQUE(job_id, platform): one post per platform per video.
    if len({channel["platform"] for channel in channels}) < len(channels):
        raise HTTPException(status_code=409, detail="Escolha no máximo um canal por plataforma.")


def artifact_or_404(job_id: str, artifact_id: str) -> sqlite3.Row:
    with m.database() as db:
        row = db.execute("SELECT * FROM artifacts WHERE id=? AND job_id=?", (artifact_id, job_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Artefato não encontrado")
    return row


def derived_video(artifact: sqlite3.Row) -> Path | None:
    remote = str(artifact["remote_path"])
    if not remote.startswith("derived/"):
        return None
    root = (m.DATA_DIR / "derived_shorts").resolve()
    path = (root / remote.removeprefix("derived/")).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def review_copy_for_artifact(job_id: str, artifact: sqlite3.Row) -> Path | None:
    """Quality review keeps a local copy of each final render. The Turbo task
    volume can lose that file; playback and download use the copy instead."""
    if artifact["kind"] != "video":
        return None
    with m.database() as db:
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
    root = (m.DATA_DIR / "curated_materials").resolve()
    path = (root / str(job["generation_item_id"]) / f"final-review-{index}.mp4").resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def local_video_response(path: Path, artifact: sqlite3.Row, mode: Literal["stream", "download"]) -> FileResponse:
    filename = str(artifact["filename"]).replace('"', "")
    if mode == "download":
        return FileResponse(path, media_type="video/mp4", filename=filename)
    return FileResponse(path, media_type="video/mp4")


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


def store_metadata(job_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    with m.database() as db:
        job = m.job_row_or_404(db, job_id)
        if job["state"] not in m.METADATA_STATES:
            raise HTTPException(status_code=409, detail="Metadados só podem ser definidos para vídeos concluídos")
        value, errors, warnings = m.normalize_youtube_metadata({**raw, "language": m.job_language(db, job)})
        if errors:
            raise HTTPException(status_code=422, detail=" ".join(errors))
        db.execute(
            "INSERT INTO job_metadata(job_id, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (job_id, json.dumps(value), m.now()),
        )
        return {**m.row_as_job(job, db), "warnings": warnings}


def require_quality_approval(db: sqlite3.Connection, job: sqlite3.Row) -> None:
    if not job["generation_item_id"]:
        if job["auto_publish_authorized"]:
            raise HTTPException(status_code=409, detail="A revisão final ainda não aprovou este trabalho. Confira o relatório da geração.")
        return
    quality = db.execute("SELECT state FROM generation_quality WHERE item_id=? AND job_id=?", (job["generation_item_id"], job["id"])).fetchone()
    if not quality or quality["state"] != "approved":
        raise HTTPException(status_code=409, detail="A revisão final do vídeo ainda não aprovou este trabalho. Confira o relatório da geração.")


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
