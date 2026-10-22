"""Job lifecycle, artifacts, metadata, review and publication routes."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from gateway import main as m

router = APIRouter()


@router.post("/api/projects/{project_id}/jobs", status_code=202)
async def create_job(project_id: str, input: m.JobCreate, *, generation_item_id: str | None = None) -> dict[str, Any]:
    if input.settings.get("video_source") == "local" and not generation_item_id:
        raise HTTPException(status_code=422, detail="Materiais locais só podem ser enviados pelo curador da geração.")
    if generation_item_id:
        with m.database() as db:
            existing = db.execute("SELECT * FROM video_jobs WHERE generation_item_id=?", (generation_item_id,)).fetchone()
            if existing:
                return m.row_as_job(existing, db)
    project = m.row_as_project(m.get_project_or_404(project_id))
    with m.database() as db:
        channels = [m.channel_row_or_404(db, channel_id) for channel_id in dict.fromkeys(input.channel_ids)]
    # Checked before Turbo starts a render that could then never be published where it was meant to go.
    m.validate_publish_channels(channels, input.format)
    conflicting_profile_fields = m.conflicting_channel_profile_fields(channels, input.format)
    if conflicting_profile_fields:
        names = ", ".join(channel["name"] for channel in channels)
        labels = {"video_language": "idioma", "voice_name": "voz", "voice_rate": "velocidade", "voice_volume": "volume", "subtitle_enabled": "legendas", "subtitle_position": "posição", "font_size": "tamanho", "text_fore_color": "cor do texto", "stroke_color": "cor da borda", "stroke_width": "espessura da borda", "paragraph_number": "duração do Short", "target_minutes": "duração", "video_clip_duration": "duração do clipe", "video_count": "quantidade de vídeos", "video_concat_mode": "modo de imagens", "video_transition_mode": "transição", "video_clip_speed": "velocidade do clipe", "bgm_type": "trilha sonora", "bgm_volume": "volume da música"}
        fields = ", ".join(labels.get(field, field) for field in conflicting_profile_fields)
        raise HTTPException(status_code=409, detail=f"Os canais {names} têm perfis de voz, legendas ou parâmetros de vídeo diferentes ({fields}). Alinhe os perfis ou gere vídeos separados.")
    channel_settings = m.channel_generation_settings(channels[0], input.format) if channels else {}
    merged = {**m.generation_defaults(input.format), **project["defaults"], **channel_settings, **input.settings, **m.FORMAT_LOCKED[input.format]}
    effective_profile = m.normalize_channel_profile({
        "language": merged.get("video_language", "pt-BR"),
        **{field: merged.get(field) for field in m.CHANNEL_PROFILE_FIELDS},
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
            script_response = await m.short_script(m.ShortScriptInput(
                subject=input.subject, language=merged.get("video_language", "pt-BR"),
                paragraph_number=int(merged.get("paragraph_number", 5) or 5),
                voice_rate=float(merged.get("voice_rate") or 1.0), voice_name=str(merged.get("voice_name") or ""),
                script_prompt=str(merged.get("video_script_prompt", "")),
                system_prompt=str(merged.get("custom_system_prompt", "")),
            ))
            script_text = script_response["script"]
    # The chosen duration wins over whatever was written, pasted or generated, in every mode: a
    # longer script would stretch the narration past it, and the visuals are cut to fit the script.
    max_words = m.job_max_words(input.format, merged)
    if script_text and max_words and not generation_item_id:
        script_text = m.trim_to_words(script_text, max_words)
    if follow_script and script_text and not str(merged.get("video_terms", "")).strip():
        terms, clip_seconds = await m.visual_terms(input.subject, script_text, float(merged.get("video_clip_duration") or 5), float(merged.get("voice_rate") or 1))
        merged["video_terms"], merged["video_clip_duration"] = ", ".join(terms), max(1, round(clip_seconds))
    request_payload = m.build_turbo_payload(input.subject, script_text, merged)
    response = await m.turbo_request("POST", "/api/v1/videos", json=request_payload)
    turbo_result = m.unwrap(response.json())
    if not isinstance(turbo_result, dict) or not turbo_result.get("task_id"):
        raise HTTPException(status_code=502, detail="Resposta inválida do MoneyPrinterTurbo ao criar tarefa")
    job_id, timestamp = str(uuid4()), m.now()
    persisted_request = {**request_payload, **({"target_minutes": merged["target_minutes"]} if "target_minutes" in merged else {})}
    with m.database() as db:
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
        return m.row_as_job(db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), db)


@router.get("/api/projects/{project_id}/jobs")
async def list_jobs(project_id: str) -> list[dict[str, Any]]:
    m.get_project_or_404(project_id)
    with m.database() as db:
        identifiers = [row["id"] for row in db.execute("SELECT id FROM video_jobs WHERE project_id=? AND deleted_at IS NULL ORDER BY created_at DESC", (project_id,))]
    results = []
    for job_id in identifiers:
        await m.reconcile_publications(job_id)
        try:
            results.append(await m.reconcile_job(job_id))
        except HTTPException:
            with m.database() as db:
                row = db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
                if row:
                    results.append(m.row_as_job(row, db))
    return results


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    with m.database() as db:
        row = db.execute("SELECT * FROM video_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    await m.reconcile_publications(job_id)
    try:
        return await m.reconcile_job(job_id)
    except HTTPException:
        with m.database() as db:
            return m.row_as_job(db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), db)


@router.post("/api/jobs/{job_id}/retry", status_code=202)
async def retry_job(job_id: str) -> dict[str, Any]:
    with m.database() as db:
        row = db.execute("SELECT * FROM video_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row or row["state"] not in {"failed", "interrupted"}:
        raise HTTPException(status_code=409, detail="Apenas trabalhos interrompidos ou com falha podem ser repetidos")
    request, warnings = m.normalize_retry_payload(row)
    with m.database() as db:
        channels = [item for item in db.execute(
            "SELECT channels.* FROM publications JOIN channels ON channels.id=publications.channel_id WHERE publications.job_id=? AND publications.channel_id IS NOT NULL",
            (job_id,),
        )]
    m.validate_publish_channels(channels, row["format"])
    response = await m.turbo_request("POST", "/api/v1/videos", json=request)
    created = m.unwrap(response.json())
    task_id = created.get("task_id") if isinstance(created, dict) else None
    if not task_id:
        raise HTTPException(status_code=502, detail="O Turbo não retornou task_id")
    with m.database() as db:
        db.execute("UPDATE video_jobs SET turbo_task_id=?, request_json=?, state='queued', progress=0, last_observed_progress=0, progress_updated_at=?, error=NULL, failed_stage=NULL, interruption_reason=NULL, updated_at=? WHERE id=?", (task_id, json.dumps(request), m.now(), m.now(), job_id))
        if row["generation_item_id"]:
            db.execute("UPDATE generation_quality SET state='pending', report_json='{}', updated_at=? WHERE item_id=? AND job_id=?", (m.now(), row["generation_item_id"], job_id))
        result = m.row_as_job(db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), db)
    result["warnings"] = warnings
    return result


@router.post("/api/jobs/{job_id}/cancel", status_code=202)
async def cancel_job(job_id: str) -> dict[str, Any]:
    with m.database() as db:
        row = db.execute("SELECT * FROM video_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row or row["state"] not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Apenas trabalhos na fila ou em geração podem ser parados")
    if row["turbo_task_id"]:
        try:
            await m.turbo_request("DELETE", f"/api/v1/tasks/{row['turbo_task_id']}")
        except HTTPException:
            pass
    with m.database() as db:
        db.execute(
            "UPDATE video_jobs SET state='interrupted', error=COALESCE(error, 'Cancelado pelo usuário'), updated_at=? WHERE id=?",
            (m.now(), job_id),
        )
        return m.row_as_job(db.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), db)


@router.delete("/api/jobs/{job_id}", status_code=204, response_model=None)
async def delete_job(job_id: str) -> None:
    with m.database() as db:
        row = db.execute("SELECT * FROM video_jobs WHERE id=? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado")
    if row["state"] in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Não é permitido excluir uma geração ativa")
    if row["turbo_task_id"]:
        try:
            await m.turbo_request("DELETE", f"/api/v1/tasks/{row['turbo_task_id']}")
        except HTTPException:
            pass
    with m.database() as db:
        db.execute("UPDATE video_jobs SET state='deleted', deleted_at=?, updated_at=? WHERE id=?", (m.now(), m.now(), job_id))


@router.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        previous = ""
        for _ in range(3600):
            current = await m.get_job(job_id)
            encoded = json.dumps(current)
            if encoded != previous:
                yield m.sse_message("job.updated", encoded)
                previous = encoded
            if current["state"] in {"completed", "approved", "failed", "interrupted", "deleted"}:
                break
            await asyncio.sleep(2)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/jobs/{job_id}/artifacts/{artifact_id}/{mode}", response_model=None)
async def artifact_proxy(job_id: str, artifact_id: str, mode: Literal["stream", "download"], request: Request) -> StreamingResponse | FileResponse:
    artifact = m.artifact_or_404(job_id, artifact_id)
    local_copy = m.derived_video(artifact) or m.review_copy_for_artifact(job_id, artifact)
    headers = m.turbo_headers()
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    # Turbo mounts these as router.get("/stream/...") under the /api/v1 prefix - no /tasks/ segment.
    path = f"/api/v1/{mode}/{artifact['remote_path']}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
    try:
        response = await client.send(client.build_request("GET", f"{m.TURBO_BASE_URL}{path}", headers=headers), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        if local_copy:
            return m.local_video_response(local_copy, artifact, mode)
        raise HTTPException(status_code=503, detail=f"Falha ao obter artefato: {exc}") from exc
    if response.status_code >= 400:
        await response.aclose()
        await client.aclose()
        if local_copy:
            return m.local_video_response(local_copy, artifact, mode)
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


@router.put("/api/jobs/{job_id}/metadata")
def put_metadata(job_id: str, input: m.MetadataInput) -> dict[str, Any]:
    return m.store_metadata(job_id, input.model_dump())


@router.post("/api/jobs/{job_id}/metadata/generate")
async def generate_metadata(job_id: str) -> dict[str, Any]:
    with m.database() as db:
        job = m.job_row_or_404(db, job_id)
        language = m.job_language(db, job)
    if job["state"] not in m.METADATA_STATES:
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
    generated = m.parse_json_object(await m.llm_chat(prompt, json_mode=True))
    return m.store_metadata(job_id, {field: generated.get(field) for field in ("title", "description", "tags")})


@router.post("/api/jobs/{job_id}/approve")
def approve_job(job_id: str) -> dict[str, Any]:
    with m.database() as db:
        job = m.job_row_or_404(db, job_id)
        if job["state"] != "completed":
            raise HTTPException(status_code=409, detail="Só vídeos concluídos podem ser aprovados")
        m.require_quality_approval(db, job)
        if not db.execute("SELECT 1 FROM artifacts WHERE job_id=? AND kind='video'", (job_id,)).fetchone():
            raise HTTPException(status_code=409, detail="O vídeo final não foi encontrado")
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        if not stored or m.normalize_youtube_metadata(json.loads(stored["value_json"]))[1]:
            raise HTTPException(status_code=409, detail="Defina título, descrição e tags válidos antes de aprovar")
        db.execute("UPDATE video_jobs SET state='approved', updated_at=? WHERE id=?", (m.now(), job_id))
        return m.row_as_job(m.job_row_or_404(db, job_id), db)


@router.post("/api/jobs/{job_id}/shorts", status_code=202)
async def derive_shorts(job_id: str) -> dict[str, Any]:
    """Cut an approved landscape video into vertical Shorts and queue them for the same channel."""
    import subprocess

    with m.database() as db:
        job = m.job_row_or_404(db, job_id)
        if job["format"] != "long":
            raise HTTPException(status_code=409, detail="Só um vídeo longo aprovado pode virar Shorts.")
        if job["state"] not in {"completed", "approved"}:
            raise HTTPException(status_code=409, detail="O vídeo longo ainda não está pronto.")
        if job["generation_item_id"]:
            m.require_quality_approval(db, job)
        elif job["state"] != "approved":
            raise HTTPException(status_code=409, detail="Aprove o vídeo longo antes de cortar Shorts.")
        artifact = db.execute("SELECT * FROM artifacts WHERE job_id=? AND kind='video' ORDER BY created_at, id LIMIT 1", (job_id,)).fetchone()
        publication = db.execute("SELECT channel_id FROM publications WHERE job_id=? LIMIT 1", (job_id,)).fetchone()
    if not artifact:
        raise HTTPException(status_code=409, detail="O vídeo longo não tem arquivo final.")
    source = m.derived_video(artifact) or m.review_copy_for_artifact(job_id, artifact)
    temporary = None
    if source is None:
        temporary = tempfile.TemporaryDirectory()
        source = Path(temporary.name) / "source.mp4"
        await m.download_turbo_file(artifact["remote_path"], source)
    try:
        from gateway.visual_specialists import probe
        duration = float(probe(source).get("duration") or 0)
    except Exception as exc:
        if temporary:
            temporary.cleanup()
        raise HTTPException(status_code=422, detail="Não foi possível medir o vídeo longo.") from exc
    if duration < 20:
        if temporary:
            temporary.cleanup()
        raise HTTPException(status_code=409, detail="O vídeo longo é curto demais para gerar Shorts.")
    length = min(45.0, max(20.0, duration / 3))
    starts = [0.0]
    if duration > length + 15:
        starts.append(max(0.0, duration * 0.35))
    if duration > length * 2 + 15:
        starts.append(max(0.0, duration * 0.7))
    channel_id = job["channel_id"] or (publication["channel_id"] if publication else None)
    created: list[str] = []
    root = m.DATA_DIR / "derived_shorts"
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
                raise HTTPException(status_code=502, detail=f"Não foi possível cortar o Short {index}.")
            timestamp = m.now()
            with m.database() as db:
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


@router.post("/api/jobs/{job_id}/publish")
async def publish_job(job_id: str, input: m.PublishInput | None = None) -> dict[str, Any]:
    input = input or m.PublishInput()
    if input.publish_at and m.as_utc(input.publish_at) <= datetime.now(UTC):
        raise HTTPException(status_code=422, detail="Escolha uma data de agendamento no futuro.")
    with m.database() as db:
        job = m.job_row_or_404(db, job_id)
        if job["state"] != "approved":
            raise HTTPException(status_code=409, detail="Aprove o vídeo antes de publicar.")
        m.require_quality_approval(db, job)
        artifact = db.execute("SELECT * FROM artifacts WHERE job_id=? AND kind='video' ORDER BY created_at LIMIT 1", (job_id,)).fetchone()
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        if not artifact or not stored:
            raise HTTPException(status_code=409, detail="Vídeo final ou metadados ausentes.")
        channel_ids = input.channel_ids or [row["channel_id"] for row in db.execute(
            "SELECT channel_id FROM publications WHERE job_id=? AND channel_id IS NOT NULL AND state IN ('pending', 'failed', 'reconciliation_required')", (job_id,)
        )]
        if not channel_ids:
            raise HTTPException(status_code=409, detail="Escolha o canal onde o vídeo será publicado.")
        channels = [m.channel_row_or_404(db, channel_id) for channel_id in dict.fromkeys(channel_ids)]
    # Every target is checked before the first upload, so a bad pick doesn't leave the video half-published.
    m.validate_publish_channels(channels, job["format"])
    for channel in channels:
        if channel["platform"] == "youtube" and not m.youtube_token_file(channel["id"]).is_file():
            raise HTTPException(status_code=409, detail=f"O canal {channel['name']} perdeu a autorização. Reconecte em Canais.")
    if input.publish_at:
        return m.schedule_publications(job_id, channels, m.as_utc(input.publish_at))
    metadata ={**m.normalize_youtube_metadata(json.loads(stored["value_json"]))[0], "format": job["format"]}
    failures: list[tuple[str, HTTPException]] = []
    with tempfile.TemporaryDirectory() as folder:
        video_path = Path(folder) / "video.mp4"
        downloaded = False

        async def video() -> Path:  # downloaded once, on the first channel that actually claims an upload
            nonlocal downloaded
            if not downloaded:  # a failed download leaves this False, so the next channel retries it
                await m.download_turbo_file(artifact["remote_path"], video_path)
                downloaded = True
            return video_path

        for channel in channels:
            try:
                await m.publish_to_channel(job_id, channel, video, metadata, input.confirm_not_uploaded)
            except HTTPException as exc:  # one channel failing must not stop the others
                failures.append((channel["id"], exc))
    if len(failures) == len(channels):
        raise failures[0][1] if len(failures) == 1 else HTTPException(status_code=502, detail=" | ".join(str(exc.detail) for _, exc in failures))
    with m.database() as db:
        job_view = m.row_as_job(m.job_row_or_404(db, job_id), db)
    return {**job_view, "errors": [{"channel_id": channel_id, "detail": exc.detail} for channel_id, exc in failures]}


@router.delete("/api/jobs/{job_id}/schedule")
def cancel_schedule(job_id: str, channel_id: str | None = None) -> dict[str, Any]:
    with m.database() as db:
        job = m.job_row_or_404(db, job_id)
        db.execute(
            "UPDATE publications SET state='pending', scheduled_at=NULL, updated_at=? WHERE job_id=? AND state='scheduled' AND (? IS NULL OR channel_id=?)",
            (m.now(), job_id, channel_id, channel_id),
        )
        return m.row_as_job(job, db)
