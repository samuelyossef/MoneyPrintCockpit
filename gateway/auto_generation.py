"""Durable, channel-specific preparation before submitting a Turbo render."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from gateway import visual_specialists as specialists

router = APIRouter()
logger = logging.getLogger("cockpit.auto_generation")
MAX_AUTO_REPAIRS = 2


class PauseGeneration(Exception):
    """An editorial or visual decision needs a person before rendering."""

EDITABLE_SETTINGS = {
    "video_language", "voice_name", "voice_rate", "voice_volume", "paragraph_number",
    "target_minutes", "video_clip_duration", "video_count", "video_concat_mode",
    "video_transition_mode", "video_clip_speed", "subtitle_enabled",
    "subtitle_position", "font_size", "text_fore_color", "stroke_color",
    "stroke_width", "bgm_type", "bgm_volume",
}
LEASE_SECONDS = 90


def main_module():
    from gateway import main
    return main


class AutoGenerationInput(BaseModel):
    format: Literal["long", "short"]
    channel_ids: list[str] = Field(min_length=1, max_length=2)
    client_request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9-]+$")
    subject: str = Field(default="", max_length=500)
    script: str = Field(default="", max_length=40000)
    # None preserves the legacy API contract: a supplied script is user-authored.
    # The cockpit marks AI-generated scripts explicitly so they can be corrected
    # automatically without changing scripts entered by the user.
    script_is_manual: bool | None = None
    requirements: str = Field(default="", max_length=2000)
    terms: str = Field(default="", max_length=4000)
    screen_system_prompt: str = Field(default="", max_length=4000)
    settings: dict[str, Any] = Field(default_factory=dict)


def _unique_text(*parts: str) -> str:
    return "\n\n".join(dict.fromkeys(part.strip() for part in parts if part and part.strip()))


def _effective_context(project: dict[str, Any], channel: dict[str, Any], request: AutoGenerationInput) -> dict[str, Any]:
    m = main_module()
    if set(request.settings) - EDITABLE_SETTINGS:
        raise HTTPException(status_code=422, detail="Os parâmetros enviados contêm campos não editáveis nesta tela.")
    format = request.format
    settings = {
        **m.generation_defaults(format), **project["defaults"],
        **m.channel_generation_settings(channel, format), **request.settings,
        **m.FORMAT_LOCKED[format],
    }
    if settings.get("video_language") not in m.VOICE_CHOICES:
        raise HTTPException(status_code=422, detail="Idioma de narração não suportado.")
    if "video_language" in request.settings and "voice_name" not in request.settings:
        settings["voice_name"] = m.default_voice_for_language(settings["video_language"])
    canonical = m.normalize_channel_video_defaults({
        format: {key: settings.get(key) for key in m.CHANNEL_VIDEO_PROFILE_FIELDS[format]}
    }, language=settings["video_language"])
    settings.update(canonical[format])
    profile = m.normalize_channel_profile({
        "language": settings.get("video_language", "pt-BR"),
        **{key: settings.get(key) for key in m.CHANNEL_PROFILE_FIELDS},
    })
    settings.update({"video_language": profile.pop("language"), **profile})
    try:
        clip_seconds = int(settings["video_clip_duration"])
        target = int(settings["paragraph_number"] if format == "short" else settings["target_minutes"])
        count = int(settings.get("video_count", 1))
    except (TypeError, ValueError, KeyError):
        raise HTTPException(status_code=422, detail="Duração, clipe ou quantidade de vídeos inválida.") from None
    if not 1 <= clip_seconds <= 30:
        raise HTTPException(status_code=422, detail="A duração de cada clipe deve estar entre 1 e 30 segundos.")
    if format == "short" and (target not in {4, 5, 6} or not 1 <= count <= 5):
        raise HTTPException(status_code=422, detail="Shorts aceitam 60, 75 ou 90 segundos e de 1 a 5 vídeos.")
    if format == "long" and target not in {5, 8, 10, 15}:
        raise HTTPException(status_code=422, detail="Vídeos longos aceitam 5, 8, 10 ou 15 minutos.")
    if settings.get("video_concat_mode") not in m.VIDEO_CONCAT_MODES:
        raise HTTPException(status_code=422, detail="Modo de imagens inválido.")
    base_prompt = _unique_text(str(settings.get("custom_system_prompt") or ""), project.get("prompt_base") or "", request.screen_system_prompt)
    editorial = _unique_text(
        channel.get("system_prompt") or "", base_prompt,
        f"Requisitos padrão do roteiro: {settings['video_script_prompt']}" if settings.get("video_script_prompt") else "",
    )
    if len(editorial) > 12000:
        raise HTTPException(status_code=422, detail="Os prompts do canal e do gerador juntos excedem 12.000 caracteres.")
    return {
        "format": format,
        "channel": {key: channel[key] for key in ("id", "name", "platform", "niche", "language", "system_prompt")},
        "settings": settings,
        "editorial": editorial,
        "render_system_prompt": base_prompt,
        "visual_guide": json.loads(channel.get("visual_guides_json") or "{}").get(format, ""),
    }


@router.post("/api/projects/{project_id}/jobs/auto", status_code=202)
def create_auto_batch(project_id: str, request: AutoGenerationInput) -> dict[str, Any]:
    m = main_module()
    project = m.row_as_project(m.get_project_or_404(project_id))
    ids = list(dict.fromkeys(request.channel_ids))
    if len(ids) != len(request.channel_ids):
        raise HTTPException(status_code=422, detail="Selecione cada canal apenas uma vez.")
    if request.format == "long" and len(ids) != 1:
        raise HTTPException(status_code=422, detail="Vídeos longos aceitam um canal do YouTube por vez.")
    input_json = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(input_json.encode("utf-8")).hexdigest()
    timestamp = m.now()
    with m.database() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT id, request_hash FROM generation_batches WHERE project_id=? AND client_request_id=?",
            (project_id, request.client_request_id),
        ).fetchone()
        if existing:
            if existing["request_hash"] != digest:
                raise HTTPException(status_code=409, detail="Esta solicitação já foi usada com outros dados.")
            return {"batch_id": existing["id"], "status": "accepted"}
        channels = [dict(m.channel_row_or_404(db, channel_id)) for channel_id in ids]
        for channel in channels:
            m.validate_publish_channels([channel], request.format)
        contexts = [_effective_context(project, channel, request) for channel in channels]
        batch_id = str(uuid4())
        db.execute(
            "INSERT INTO generation_batches(id, project_id, client_request_id, request_hash, input_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (batch_id, project_id, request.client_request_id, digest, input_json, timestamp),
        )
        for context in contexts:
            db.execute(
                "INSERT INTO generation_batch_items(id, batch_id, channel_id, channel_name, state, stage, context_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?, ?)",
                (str(uuid4()), batch_id, context["channel"]["id"], context["channel"]["name"],
                 json.dumps(context, ensure_ascii=False), timestamp, timestamp),
            )
    return {"batch_id": batch_id, "status": "accepted"}


@router.get("/api/generation-batches/{batch_id}")
async def get_auto_batch(batch_id: str) -> dict[str, Any]:
    m = main_module()
    with m.database() as db:
        batch = db.execute("SELECT * FROM generation_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="Geração não encontrada.")
        items = [dict(row) for row in db.execute(
            "SELECT * FROM generation_batch_items WHERE batch_id=? ORDER BY created_at, id", (batch_id,)
        )]
    for item_index, item in enumerate(items):
        origin_item_id = item["id"]
        with m.database() as db:
            origin_quality = db.execute("SELECT state, report_json FROM generation_quality WHERE item_id=?", (origin_item_id,)).fetchone()
            revisions = [dict(row) for row in db.execute(
                "SELECT i.id, i.batch_id, i.job_id, i.repair_attempt, i.state, i.stage, "
                "q.state AS quality_state, q.report_json FROM generation_batch_items i "
                "LEFT JOIN generation_quality q ON q.item_id=i.id "
                "WHERE i.repair_root_item_id=? ORDER BY i.repair_attempt", (origin_item_id,))]
            if revisions:
                item = dict(db.execute("SELECT * FROM generation_batch_items WHERE id=?", (revisions[-1]["id"],)).fetchone())
        item["active_batch_id"] = item["batch_id"]
        item["origin_item_id"] = origin_item_id
        history = [{"item_id": origin_item_id, "job_id": items[item_index]["job_id"], "attempt": 0,
                    "quality_state": origin_quality["state"] if origin_quality else None,
                    "reason": (json.loads(origin_quality["report_json"] or "{}").get("reason") if origin_quality else "") or ""}]
        history.extend({"item_id": revision["id"], "job_id": revision["job_id"],
                        "attempt": revision["repair_attempt"], "quality_state": revision["quality_state"],
                        "state": revision["state"],
                        "reason": (json.loads(revision["report_json"] or "{}").get("reason") or "")}
                       for revision in revisions)
        item["repair_history"] = history
        item["repair_max_attempts"] = MAX_AUTO_REPAIRS
        items[item_index] = item
        job_id = item["job_id"]
        if job_id:
            try:
                await m.reconcile_job(job_id)
            except Exception:
                logger.warning("Não foi possível atualizar o trabalho %s", job_id)
        with m.database() as db:
            job = db.execute("SELECT state, progress, error FROM video_jobs WHERE id=?", (job_id,)).fetchone() if job_id else None
            artifact = db.execute("SELECT id FROM artifacts WHERE job_id=? AND kind='video' LIMIT 1", (job_id,)).fetchone() if job_id else None
            quality = db.execute("SELECT state, report_json FROM generation_quality WHERE item_id=?", (item["id"],)).fetchone()
        item["generated"] = json.loads(item.pop("generated_json"))
        item["quality"] = {"state": quality["state"], "report": json.loads(quality["report_json"])} if quality else None
        item["job_state"] = job["state"] if job else None
        item["job_progress"] = job["progress"] if job else None
        item["job_error"] = job["error"] if job else None
        item["artifact_id"] = artifact["id"] if artifact else None
        for key in ("context_json", "lease_owner", "lease_until", "submission_started_at"):
            item.pop(key, None)
    outcomes = [
        item["state"] if item["state"] != "submitted" else item["job_state"]
        for item in items
    ]
    finished = {"completed", "approved", "failed", "interrupted", "deleted", "reconciliation_required", "paused"}
    outcomes = ["processing" if item["state"] == "submitted" and (item.get("quality") or {}).get("state") in ("pending", "reviewing") else outcome for item, outcome in zip(items, outcomes)]
    outcomes = ["paused" if item["state"] == "submitted" and (item.get("quality") or {}).get("state") in ("blocked", "error") else outcome for item, outcome in zip(items, outcomes)]
    if outcomes and all(outcome in finished for outcome in outcomes):
        successes = sum(outcome in {"completed", "approved"} for outcome in outcomes)
        status = "completed" if successes == len(outcomes) else "partial_failure" if successes else "failed"
    else:
        status = "processing"
    return {"batch_id": batch_id, "format": json.loads(batch["input_json"])["format"], "status": status, "items": items}


@router.get("/api/generation-batches")
def list_auto_batches(limit: int = 15) -> list[dict[str, Any]]:
    m = main_module()
    limit = max(1, min(50, limit))
    with m.database() as db:
        rows = db.execute(
            "SELECT b.id, b.created_at, b.input_json, o.id AS operator_run_id FROM generation_batches b "
            "LEFT JOIN operator_runs o ON o.batch_id=b.id WHERE b.archived_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM generation_batch_items child WHERE child.batch_id=b.id AND child.repair_parent_item_id IS NOT NULL) "
            "ORDER BY b.created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for row in rows:
            items = [dict(item) for item in db.execute(
                "SELECT i.id, i.channel_name, i.state, i.stage, i.error, i.job_id, q.state AS quality_state, q.report_json AS quality_report_json "
                "FROM generation_batch_items i LEFT JOIN generation_quality q ON q.item_id=i.id WHERE i.batch_id=? ORDER BY i.created_at", (row["id"],)
            )]
            for index, item in enumerate(items):
                latest = db.execute(
                    "SELECT i.id, i.channel_name, i.state, i.stage, i.error, i.job_id, i.batch_id, i.repair_attempt, "
                    "q.state AS quality_state, q.report_json AS quality_report_json "
                    "FROM generation_batch_items i LEFT JOIN generation_quality q ON q.item_id=i.id "
                    "WHERE i.repair_root_item_id=? ORDER BY i.repair_attempt DESC LIMIT 1", (item["id"],)
                ).fetchone()
                if latest:
                    items[index] = item = dict(latest)
                report = json.loads(item.pop("quality_report_json") or "{}")
                item["quality_reason"] = (report.get("reason") or " · ".join(
                    video.get("reason", "") for video in report.get("videos", []) if not video.get("approved")
                ))[:500]
                item["repair_max_attempts"] = MAX_AUTO_REPAIRS
            result.append({"batch_id": row["id"], "created_at": row["created_at"], "format": json.loads(row["input_json"])["format"],
                           "source": "operator" if row["operator_run_id"] else "generator", "items": items})
    return result


@router.delete("/api/generation-batches/reviews")
def clear_completed_reviews() -> dict[str, int]:
    """Hide completed, approved review batches without deleting their jobs or audit history."""
    m = main_module()
    with m.database() as db:
        cursor = db.execute(
            "UPDATE generation_batches SET archived_at=? WHERE archived_at IS NULL "
            "AND EXISTS (SELECT 1 FROM generation_batch_items i WHERE i.batch_id=generation_batches.id) "
            "AND NOT EXISTS (SELECT 1 FROM generation_batch_items i "
            "LEFT JOIN generation_quality q ON q.item_id=i.id WHERE i.batch_id=generation_batches.id "
            "AND COALESCE((SELECT child_q.state FROM generation_batch_items child "
            "LEFT JOIN generation_quality child_q ON child_q.item_id=child.id "
            "WHERE child.repair_root_item_id=i.id ORDER BY child.repair_attempt DESC LIMIT 1), q.state, '')!='approved')",
            (m.now(),),
        )
        return {"cleared": cursor.rowcount}


@router.post("/api/generation-batches/{batch_id}/items/{item_id}/retry", status_code=202)
def retry_auto_item(batch_id: str, item_id: str) -> dict[str, str]:
    m = main_module()
    with m.database() as db:
        row = db.execute("SELECT state, generated_json FROM generation_batch_items WHERE id=? AND batch_id=?", (item_id, batch_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Canal desta geração não encontrado.")
        if row["state"] not in {"failed", "paused"}:
            raise HTTPException(status_code=409, detail="Apenas canais com falha ou pausados podem ser retomados.")
        generated = json.loads(row["generated_json"] or "{}")
        rejected_script = generated.get("editorial_rejected_script")
        if (rejected_script and generated.get("script") and generated["script"] != rejected_script
                and not generated.get("editorial_assets_refreshed")):
            generated["editorial_script_regenerated_from_review"] = True
            if not generated.get("terms_manually_reviewed"):
                generated.pop("terms", None)
            generated.pop("scenes", None)
        db.execute(
            "UPDATE generation_batch_items SET state='queued', error=NULL, lease_owner=NULL, lease_until=NULL, "
            "stage='queued', generated_json=?, updated_at=? WHERE id=?",
            (json.dumps(generated, ensure_ascii=False), m.now(), item_id),
        )
    return {"status": "queued"}


@router.post("/api/generation-batches/{batch_id}/items/{item_id}/regenerate-script", status_code=202)
def regenerate_paused_script(batch_id: str, item_id: str) -> dict[str, str]:
    """Explicitly replace a rejected script and resume the same channel pipeline."""
    m = main_module()
    with m.database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT generated_json, state FROM generation_batch_items WHERE id=? AND batch_id=?",
            (item_id, batch_id),
        ).fetchone()
        if not row or row["state"] != "paused":
            raise HTTPException(status_code=409, detail="Apenas um roteiro pausado pode ser regenerado.")
        generated = json.loads(row["generated_json"] or "{}")
        review = generated.get("editorial_review") or {}
        if review.get("approved") is not False or not generated.get("script"):
            raise HTTPException(status_code=409, detail="Não há um roteiro reprovado disponível para substituir.")
        generated["editorial_rejected_script"] = generated["script"]
        generated["force_script_regeneration"] = True
        generated["force_script_regeneration_feedback"] = str(review.get("reason") or "Mantenha o tema solicitado.")
        generated["editorial_script_regenerated_from_review"] = True
        for key in ("script", "editorial_review", "terms", "scenes", "editorial_repair_attempted",
                    "editorial_repair_attempts", "script_manually_reviewed", "orthographic_review"):
            generated.pop(key, None)
        db.execute(
            "UPDATE generation_batch_items SET generated_json=?, state='queued', error=NULL, lease_owner=NULL, "
            "lease_until=NULL, stage='queued', updated_at=? WHERE id=?",
            (json.dumps(generated, ensure_ascii=False), m.now(), item_id),
        )
    return {"status": "queued", "batch_id": batch_id, "item_id": item_id}


@router.post("/api/generation-batches/{batch_id}/items/{item_id}/retry-quality", status_code=202)
def retry_quality_review(batch_id: str, item_id: str) -> dict[str, str]:
    m = main_module()
    with m.database() as db:
        row = db.execute(
            "SELECT q.state, j.state AS job_state FROM generation_quality q "
            "JOIN generation_batch_items i ON i.id=q.item_id "
            "JOIN video_jobs j ON j.id=q.job_id WHERE i.id=? AND i.batch_id=?",
            (item_id, batch_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Revisão final não encontrada.")
        if row["state"] != "error" or row["job_state"] != "completed":
            raise HTTPException(status_code=409, detail="Apenas uma revisão com erro de um vídeo concluído pode ser repetida.")
        db.execute("UPDATE generation_quality SET state='pending', report_json='{}', updated_at=? WHERE item_id=?",
                   (m.now(), item_id))
    return {"status": "pending"}


@router.post("/api/generation-batches/{batch_id}/items/{item_id}/auto-repair", status_code=202)
async def resume_blocked_review(batch_id: str, item_id: str) -> dict[str, Any]:
    """Explicitly resume an older blocked review, even if its original job was deleted."""
    m = main_module()
    with m.database() as db:
        row = db.execute(
            "SELECT q.state, q.report_json FROM generation_quality q JOIN generation_batch_items i ON i.id=q.item_id "
            "WHERE i.id=? AND i.batch_id=?", (item_id, batch_id),
        ).fetchone()
    if not row or row["state"] not in {"blocked", "repairing"}:
        raise HTTPException(status_code=409, detail="Não há uma revisão bloqueada para retomar.")
    if row["state"] == "repairing":
        with m.database() as db:
            child = db.execute("SELECT batch_id, id FROM generation_batch_items WHERE repair_parent_item_id=?", (item_id,)).fetchone()
        if child:
            return {"status": "queued", "batch_id": child["batch_id"], "item_id": child["id"]}
    report = json.loads(row["report_json"] or "{}")
    for index, video in enumerate(report.get("videos", [])):
        if video.get("checks", {}).get("duration") is False and not video.get("measurements", {}).get("duration_seconds"):
            path = specialists.material_root(item_id) / f"final-review-{index}.mp4"
            if not path.is_file():
                raise HTTPException(status_code=409, detail="O vídeo original não está disponível para medir a correção.")
            info = await asyncio.to_thread(specialists.probe, path)
            video["measurements"] = {"duration_seconds": info["duration"]}
    repair = _queue_auto_repair(item_id, report)
    if not repair:
        raise HTTPException(status_code=409, detail="Esta revisão não admite outra correção automática segura. Use a revisão manual.")
    return {"status": "queued", **repair}


class ManualReviewInput(BaseModel):
    scene_index: int | None = Field(default=None, ge=0)
    candidate_id: str | None = None
    script: str | None = Field(default=None, max_length=40000)
    terms: str | None = Field(default=None, max_length=4000)


@router.post("/api/generation-batches/{batch_id}/items/{item_id}/review", status_code=202)
def review_auto_item(batch_id: str, item_id: str, input: ManualReviewInput) -> dict[str, str]:
    m = main_module()
    with m.database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM generation_batch_items WHERE id=? AND batch_id=?", (item_id, batch_id)).fetchone()
        quality = db.execute("SELECT state FROM generation_quality WHERE item_id=?", (item_id,)).fetchone() if row else None
        final_blocked = bool(row and row["state"] == "submitted" and quality and quality["state"] in {"blocked", "error"})
        if not row or row["state"] != "paused" and not final_blocked:
            raise HTTPException(status_code=409, detail="Apenas uma geração pausada pode receber revisão manual.")
        if final_blocked and input.script is None and input.terms is None and input.scene_index is None:
            raise HTTPException(status_code=422, detail="Altere o roteiro, a busca ou um material para criar nova montagem.")
        generated = json.loads(row["generated_json"])
        if input.script is not None:
            if not input.script.strip():
                raise HTTPException(status_code=422, detail="O roteiro revisado não pode ficar vazio.")
            generated["script"] = input.script.strip()
            generated["script_manually_reviewed"] = True
            generated.pop("editorial_review", None)
            generated.pop("orthographic_review", None)
            generated.pop("terms", None)
            generated.pop("scenes", None)
        if input.terms is not None:
            if not input.terms.strip():
                raise HTTPException(status_code=422, detail="Informe palavras-chave concretas para a nova busca.")
            generated["terms"] = input.terms.strip()
            generated["terms_manually_reviewed"] = True
            generated.pop("scenes", None)
        if input.scene_index is not None:
            scenes = generated.get("scenes") or []
            if input.scene_index >= len(scenes) or not input.candidate_id:
                raise HTTPException(status_code=422, detail="Escolha uma alternativa visual válida.")
            scene = scenes[input.scene_index]
            candidate = next((candidate for candidate in scene["candidates"] if candidate["id"] == input.candidate_id), None)
            if not candidate or not candidate.get("filename") or not (specialists.material_root(item_id) / candidate["filename"]).is_file():
                raise HTTPException(status_code=422, detail="A alternativa visual não está disponível.")
            candidate["status"] = "approved"
            candidate["manual_override"] = True
            candidate["reason"] = "Escolha manual do usuário; será verificada novamente na montagem."
            scene["selected"] = candidate["id"]
        if final_blocked:
            original = db.execute("SELECT * FROM generation_batches WHERE id=?", (batch_id,)).fetchone()
            request_data = json.loads(original["input_json"])
            request_data["channel_ids"] = [row["channel_id"]]
            fingerprint = hashlib.sha256(json.dumps(generated, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]
            request_data["client_request_id"] = f"review-{item_id}-{fingerprint}"[:100]
            existing = db.execute("SELECT id FROM generation_batches WHERE project_id=? AND client_request_id=?", (original["project_id"], request_data["client_request_id"])).fetchone()
            if existing:
                return {"status": "queued", "batch_id": existing["id"]}
            new_batch_id, new_item_id = str(uuid4()), str(uuid4())
            source = specialists.material_root(item_id)
            destination = specialists.material_root(new_item_id)
            destination.mkdir(parents=True, exist_ok=True)
            for scene in generated.get("scenes") or []:
                candidate = next((candidate for candidate in scene["candidates"] if candidate["id"] == scene.get("selected")), None)
                if candidate and candidate.get("filename"):
                    filename = candidate["filename"]
                    source_file = source / filename
                    if not source_file.is_file():
                        raise HTTPException(status_code=409, detail="O material escolhido não está mais disponível.")
                    shutil.copy2(source_file, destination / filename)
            request_json = json.dumps(request_data, ensure_ascii=False, sort_keys=True)
            db.execute("INSERT INTO generation_batches(id, project_id, client_request_id, request_hash, input_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (new_batch_id, original["project_id"], request_data["client_request_id"], hashlib.sha256(request_json.encode()).hexdigest(), request_json, m.now()))
            db.execute("INSERT INTO generation_batch_items(id, batch_id, channel_id, channel_name, state, stage, context_json, generated_json, created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?, ?, ?)",
                       (new_item_id, new_batch_id, row["channel_id"], row["channel_name"], row["context_json"], json.dumps(generated, ensure_ascii=False), m.now(), m.now()))
            return {"status": "queued", "batch_id": new_batch_id}
        db.execute("UPDATE generation_batch_items SET generated_json=?, state='queued', stage='queued', error=NULL, updated_at=? WHERE id=?", (json.dumps(generated, ensure_ascii=False), m.now(), item_id))
    return {"status": "queued", "batch_id": batch_id}


@router.get("/api/generation-batches/{batch_id}/items/{item_id}/scenes/{scene_index}/preview")
def scene_preview(batch_id: str, item_id: str, scene_index: int, candidate_id: str) -> FileResponse:
    m = main_module()
    with m.database() as db:
        row = db.execute("SELECT generated_json FROM generation_batch_items WHERE id=? AND batch_id=?", (item_id, batch_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Geração não encontrada.")
    scenes = json.loads(row["generated_json"]).get("scenes") or []
    if scene_index < 0 or scene_index >= len(scenes):
        raise HTTPException(status_code=404, detail="Trecho não encontrado.")
    candidate = next((entry for entry in scenes[scene_index]["candidates"] if entry["id"] == candidate_id), None)
    if not candidate or not candidate.get("filename"):
        raise HTTPException(status_code=404, detail="Prévia não encontrada.")
    path = specialists.material_root(item_id) / candidate["filename"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    if path.suffix == ".mp4":
        frame = path.with_suffix(".jpg")
        if not frame.is_file():
            frame.write_bytes(specialists.sample_frame(path, 1))
        path = frame
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=60"})


def _recover_expired(db: Any, current: str) -> None:
    for item in db.execute(
        "SELECT id, submission_started_at FROM generation_batch_items WHERE state='processing' AND lease_until<?",
        (current,),
    ).fetchall():
        if item["submission_started_at"]:
            job = db.execute("SELECT id FROM video_jobs WHERE generation_item_id=?", (item["id"],)).fetchone()
            if job:
                db.execute(
                    "UPDATE generation_batch_items SET state='submitted', stage='rendering', job_id=?, lease_owner=NULL, lease_until=NULL, updated_at=? WHERE id=?",
                    (job["id"], current, item["id"]),
                )
                db.execute("INSERT OR IGNORE INTO generation_quality(item_id, job_id, state, updated_at) VALUES (?, ?, 'pending', ?)", (item["id"], job["id"], current))
                run = db.execute("SELECT o.id, o.channel_id, p.auto_publish, p.enabled FROM operator_runs o JOIN operators p ON p.channel_id=o.channel_id JOIN generation_batch_items i ON i.batch_id=o.batch_id WHERE i.id=COALESCE((SELECT repair_root_item_id FROM generation_batch_items WHERE id=?), ?)", (item["id"], item["id"])).fetchone()
                if run:
                    db.execute("UPDATE video_jobs SET created_by='operator', auto_publish_authorized=? WHERE id=?", (int(bool(run["auto_publish"] and run["enabled"])), job["id"]))
                    db.execute("UPDATE operator_runs SET state='submitted', job_id=?, finished_at=? WHERE id=?", (job["id"], current, run["id"]))
                    db.execute("UPDATE operators SET lease_owner=NULL, lease_until=NULL, updated_at=? WHERE channel_id=? AND lease_owner=?", (current, run["channel_id"], run["id"]))
            else:
                db.execute(
                    "UPDATE generation_batch_items SET state='reconciliation_required', stage='reconciliation_required', "
                    "error=?, lease_owner=NULL, lease_until=NULL, updated_at=? WHERE id=?",
                    ("O envio ao Turbo pode ter começado. Confira os trabalhos antes de repetir para evitar vídeo duplicado.", current, item["id"]),
                )
        else:
            db.execute(
                "UPDATE generation_batch_items SET state='queued', stage='queued', lease_owner=NULL, lease_until=NULL, updated_at=? WHERE id=?",
                (current, item["id"]),
            )


def claim_next_item(owner: str) -> dict[str, Any] | None:
    m = main_module()
    current = m.now()
    until = (datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)).isoformat()
    with m.database() as db:
        db.execute("BEGIN IMMEDIATE")
        _recover_expired(db, current)
        row = db.execute("SELECT * FROM generation_batch_items WHERE state='queued' ORDER BY created_at, id LIMIT 1").fetchone()
        if not row:
            return None
        db.execute(
            "UPDATE generation_batch_items SET state='processing', lease_owner=?, lease_until=?, updated_at=? WHERE id=? AND state='queued'",
            (owner, until, current, row["id"]),
        )
        return dict(row)


async def _heartbeat(item_id: str, owner: str) -> None:
    m = main_module()
    while True:
        await asyncio.sleep(15)
        until = (datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)).isoformat()
        try:
            with m.database() as db:
                db.execute(
                    "UPDATE generation_batch_items SET lease_until=? WHERE id=? AND lease_owner=? AND state='processing'",
                    (until, item_id, owner),
                )
        except Exception:
            logger.exception("Não foi possível renovar a reserva da geração %s", item_id)


def _save(item_id: str, owner: str, stage: str, generated: dict[str, Any] | None = None) -> None:
    m = main_module()
    with m.database() as db:
        if generated is None:
            result = db.execute(
                "UPDATE generation_batch_items SET stage=?, updated_at=? WHERE id=? AND lease_owner=? AND state='processing'",
                (stage, m.now(), item_id, owner),
            )
        else:
            result = db.execute(
                "UPDATE generation_batch_items SET stage=?, generated_json=?, updated_at=? WHERE id=? AND lease_owner=? AND state='processing'",
                (stage, json.dumps(generated, ensure_ascii=False), m.now(), item_id, owner),
            )
        if result.rowcount != 1:
            raise RuntimeError("A geração perdeu a reserva deste canal.")


def _context_line(context: dict[str, Any]) -> str:
    channel = context["channel"]
    settings = context["settings"]
    target = (f"{settings['target_minutes']} minutos" if context["format"] == "long"
              else f"{main_module().SHORT_TARGET_SECONDS[int(settings['paragraph_number'])]} segundos")
    return (
        f"Canal: {channel['name']}. Nicho: {channel['niche']}. Idioma: {settings['video_language']}. "
        f"Formato: {context['format']}. Duração alvo: {target}. Velocidade da voz: {settings['voice_rate']}. "
        f"Fonte de filmagens: {settings.get('video_source', 'pexels')}. "
        f"Instruções editoriais: {context['editorial']}. Guia visual: {context.get('visual_guide', '')}"
    )


async def _json_generation(prompt: str, key: str, limit: int) -> str:
    m = main_module()
    result = await m.llm_json_object(prompt, task="json", max_tokens=900, label=key)
    value = str(result.get(key) or "").strip()
    if not value:
        raise HTTPException(status_code=502, detail=f"A IA não devolveu {key} para este canal.")
    if len(value) > limit:
        raise HTTPException(status_code=502, detail=f"A IA devolveu {key} acima do limite de {limit} caracteres.")
    return value


async def _source_summary(script: str) -> str:
    if not script.strip():
        return ""
    parts = [script[index:index + 3000] for index in range(0, len(script), 3000)]
    summaries = []
    for part in parts:
        summaries.append(await _json_generation(
            "Resuma fielmente as ideias e a estrutura deste trecho de roteiro para adaptação a outro canal. "
            "Responda apenas JSON com a chave summary. Trecho:\n" + part, "summary", 600,
        ))
    return " ".join(summaries)[:5000]


def _duration_bounds(context: dict[str, Any]) -> tuple[float, float]:
    settings = context["settings"]
    target = int(settings["paragraph_number"]) if context["format"] == "short" else int(settings["target_minutes"])
    budget = main_module().script_duration_budget(
        context["format"], target, float(settings["voice_rate"]), str(settings.get("voice_name") or ""),
    )
    return float(budget["minimum_seconds"]), float(budget["maximum_seconds"])


def _validate_script(script: str, context: dict[str, Any]) -> None:
    m = main_module()
    if not script.strip():
        raise HTTPException(status_code=502, detail="O roteiro gerado ficou vazio.")
    seconds = m.narration_seconds(script, float(context["settings"]["voice_rate"]),
                                  str(context["settings"].get("voice_name") or ""))
    minimum, maximum = _duration_bounds(context)
    if not minimum <= seconds <= maximum:
        raise HTTPException(
            status_code=422,
            detail=f"O roteiro tem cerca de {round(seconds)} s de narração; a duração selecionada exige entre {round(minimum)} e {round(maximum)} s.",
        )


async def _generate_script(subject: str, requirements: str, context: dict[str, Any], source_summary: str,
                          *, editorial_feedback: str = "") -> str:
    m = main_module()
    from gateway.editorial_quality import writing_brief
    settings = context["settings"]
    editorial = _unique_text(_context_line(context), f"Ideias do roteiro de referência: {source_summary}" if source_summary else "")
    correction = ""
    if editorial_feedback:
        correction = (
            f"\nCorreção editorial obrigatória: {editorial_feedback}. Reescreva o roteiro do zero. "
            f"O tema pedido é exclusivamente {subject}; mantenha-se nele do começo ao fim e descarte qualquer assunto "
            "ou afirmação do rascunho reprovado que não responda diretamente a esse tema."
        )
    if len(editorial) > 12000:
        raise HTTPException(status_code=422, detail="Os prompts e o roteiro de referência excedem o limite de contexto editorial.")
    last_error: HTTPException | None = None
    rate = float(settings["voice_rate"])
    voice_name = str(settings.get("voice_name") or "")
    target = int(settings["paragraph_number"]) if context["format"] == "short" else int(settings["target_minutes"])
    budget = m.script_duration_budget(context["format"], target, rate, voice_name)
    minimum_seconds, maximum_seconds = float(budget["minimum_seconds"]), float(budget["maximum_seconds"])
    minimum_words, maximum_words = int(budget["minimum_words"]), int(budget["maximum_words"])
    target_words = int(budget["target_words"])
    previous_script = ""
    for attempt in range(3):
        if attempt and previous_script:
            current_seconds = m.narration_seconds(previous_script, rate, voice_name)
            # Providers often undershoot requested word counts. Move the first
            # correction into the upper part of the allowed band, then use its
            # safe maximum for the final correction so rounding cannot leave a
            # valid script just below the minimum duration.
            correction_target = maximum_words if attempt >= 2 else round(
                minimum_words + (maximum_words - minimum_words) * 0.8
            )
            prompt = (
                f"Reescreva o roteiro de {context['format']} no idioma {settings['video_language']} sobre: {subject}. "
                f"A duração selecionada requer {minimum_seconds:.0f}–{maximum_seconds:.0f} segundos na voz {voice_name or 'padrão'} "
                f"a {rate}x; a versão atual tem {len(previous_script.split())} palavras e foi estimada em {current_seconds:.1f} segundos. "
                f"Entregue a narração completa com cerca de {correction_target} palavras, obrigatoriamente entre {minimum_words} e {maximum_words}. "
                f"Preserve tema, fatos, idioma, requisitos e instruções editoriais: {requirements}. {editorial}.{correction} "
                "Expanda com conteúdo útil se estiver curto; condense repetições e detalhes secundários se estiver longo. "
                "Não use texto de enchimento. Use apenas texto falado, sem títulos, Markdown, marcadores, emojis ou indicações de cena. "
                f'Roteiro atual:\n{previous_script}\nResponda apenas JSON: {{"script": "narração completa"}}.'
            )
            result = await m.llm_json_object(
                prompt, task="json", max_tokens=m.long_script_tokens(maximum_words, m.FAST_LONG_SCRIPT_MAX_TOKENS),
                fast_script=True, label=f"ajuste de duração do roteiro {context['format']}",
            )
        elif context["format"] == "short":
            result = await m.llm_json_object(
                f"Escreva um roteiro de Short no idioma {settings['video_language']} sobre: {subject}. "
                f"A duração selecionada exige entre {minimum_seconds:.0f} e {maximum_seconds:.0f} segundos "
                f"com voz {voice_name or 'padrão'} na velocidade {rate}x. Escreva cerca de {target_words} palavras de narração, "
                f"entre {minimum_words} e {maximum_words} palavras. Conte as palavras antes de responder. "
                f"Requisitos personalizados: {requirements}. Instruções do canal e do gerador: {editorial}.{correction} "
                " Use apenas texto falado, sem título, Markdown, marcadores, emojis ou indicações de cena. "
                + writing_brief("short")
                + ' Responda apenas JSON: {"script": "narração"}.',
                task="json", max_tokens=max(1024, round(maximum_words * 4)), fast_script=True,
                label="roteiro do Short",
            )
            script = m.clean_narration(str(result.get("script") or "")).strip()
            result = {"script": script}
        else:
            result = await m.generate_long_script(m.LongScriptInput(
                subject=subject, language=settings["video_language"], minutes=int(settings["target_minutes"]),
                voice_rate=rate, voice_name=voice_name, niche=context["channel"]["niche"],
                requirements=(f"{requirements}\n{correction}" if correction else requirements), system_prompt=editorial,
            ))
        script = str(result.get("script") or "").strip()
        if not script:
            raise HTTPException(status_code=502, detail="O provedor não devolveu um roteiro válido para ajustar à duração.")
        try:
            _validate_script(script, context)
            return script
        except HTTPException as exc:
            last_error = exc
            previous_script = script
    assert last_error is not None
    raise last_error


async def _repair_duration_script(generated: dict[str, Any], context: dict[str, Any]) -> str:
    m = main_module()
    previous = generated.get("repair_previous_script") or ""
    measured = float(generated["repair_plan"]["duration_seconds"] or 0)
    if not previous or measured <= 0:
        raise PauseGeneration("A duração real da narração não está disponível para corrigir o roteiro.")
    settings = context["settings"]
    budget = m.script_duration_budget(context["format"],
        int(settings["paragraph_number"] if context["format"] == "short" else settings["target_minutes"]),
        float(settings["voice_rate"]), str(settings.get("voice_name") or ""))
    minimum_words, maximum_words = int(budget["minimum_words"]), int(budget["maximum_words"])
    last_error: HTTPException | None = None
    candidate = previous
    for attempt in range(3):
        estimated = m.narration_seconds(candidate, float(settings["voice_rate"]), str(settings.get("voice_name") or ""))
        correction_target = maximum_words if attempt >= 1 else max(
            minimum_words,
            min(maximum_words, round(len(candidate.split()) * budget["maximum_seconds"] / max(estimated, 1))),
        )
        correction = (
            f"Corrija a duração de um roteiro para {context['format']} no idioma {settings['video_language']}. "
            f"A versão atual tem {len(candidate.split())} palavras e foi estimada em {estimated:.1f}s com a voz "
            f"{settings.get('voice_name') or 'padrão'} na velocidade {settings['voice_rate']}x. "
            f"A faixa exigida é {budget['minimum_seconds']:.0f}–{budget['maximum_seconds']:.0f}s; "
            f"escreva entre {minimum_words} e {maximum_words} palavras, mirando {correction_target} palavras "
            f"(faixa de duração segura: {budget['minimum_seconds']:.0f}–{budget['maximum_seconds']:.0f}s). Preserve tema {generated['subject']}, fatos, idioma, "
            f"instruções do canal {context['editorial']} e requisitos {generated['requirements']}. "
            "Se a versão atual continuar curta ou longa, ajuste-a na direção e proporção indicadas pela estimativa. "
            "Não acrescente enchimento, cabeçalhos, Markdown ou indicações de cena. Use apenas narração falada. "
            f'Roteiro atual:\n{candidate}\nResponda apenas JSON: {{"script": "narração corrigida"}}.'
        )
        result = await m.llm_json_object(correction, task="json",
                                         max_tokens=m.long_script_tokens(maximum_words, m.FAST_LONG_SCRIPT_MAX_TOKENS),
                                         fast_script=True, label="correção da duração real")
        candidate = m.clean_narration(str(result.get("script") or "")).strip()
        try:
            _validate_script(candidate, context)
            return candidate
        except HTTPException as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


async def _prepare_and_submit(item: dict[str, Any], owner: str) -> None:
    m = main_module()
    item_id = item["id"]
    with m.database() as db:
        batch = db.execute("SELECT * FROM generation_batches WHERE id=?", (item["batch_id"],)).fetchone()
    request = AutoGenerationInput.model_validate_json(batch["input_json"])
    context = json.loads(item["context_json"])
    generated = json.loads(item["generated_json"])
    multi = len(request.channel_ids) > 1
    manual_subject = request.subject.strip()
    supplied_script = request.script.strip()
    force_script_regeneration = bool(generated.pop("force_script_regeneration", False))
    forced_feedback = str(generated.pop("force_script_regeneration_feedback", ""))
    manual_script = supplied_script if request.script_is_manual is not False and not force_script_regeneration else ""
    generated_script_candidate = supplied_script if request.script_is_manual is False and not force_script_regeneration else ""
    if not generated.get("subject"):
        _save(item_id, owner, "topic")
        if manual_subject and not multi:
            generated["subject"] = manual_subject
        else:
            generated["subject"] = await _json_generation(
                _context_line(context) + "\nSugira um tema específico, novo e filmável para este canal. "
                + (f"Adapte esta ideia do usuário: {manual_subject}. " if manual_subject else "")
                + 'Responda apenas JSON: {"topic": "tema"}.', "topic", 500,
            )
        _save(item_id, owner, "topic_done", generated)
    if not generated.get("requirements"):
        _save(item_id, owner, "requirements")
        if request.requirements.strip() and not multi:
            generated["requirements"] = request.requirements.strip()
        else:
            adapted = await _json_generation(
                _context_line(context) + f"\nTema: {generated['subject']}. "
                + (f"Preserve estes requisitos do usuário: {request.requirements.strip()}. " if request.requirements.strip() else "")
                + "Crie requisitos objetivos de estrutura, tom e narrativa compatíveis com o canal e a duração. "
                + 'Responda apenas JSON: {"requirements": "instruções"}.', "requirements", 2000,
            )
            generated["requirements"] = _unique_text(request.requirements, adapted) if multi else adapted
            if len(generated["requirements"]) > 4000:
                raise HTTPException(status_code=422, detail="Os requisitos adaptados excedem o limite do roteiro.")
        _save(item_id, owner, "requirements_done", generated)
    if not generated.get("script"):
        _save(item_id, owner, "script")
        if generated.get("repair_plan") and "duration" in generated["repair_plan"].get("failed_checks", []):
            generated["script"] = await _repair_duration_script(generated, context)
        elif manual_script and not multi:
            generated["script"] = manual_script
        elif generated_script_candidate and not multi:
            generated["script"] = generated_script_candidate
        else:
            source_summary = await _source_summary(manual_script or generated_script_candidate)
            generated["script"] = await _generate_script(
                generated["subject"], generated["requirements"], context,
                "" if force_script_regeneration else source_summary,
                editorial_feedback=forced_feedback,
            )
        _save(item_id, owner, "script_done", generated)
    if not generated.get("orthographic_review", {}).get("approved"):
        _save(item_id, owner, "orthographic_review", generated)
        proofread = await specialists.proofread_script(
            generated["script"], str(context["settings"]["video_language"]),
        )
        preserve_script = bool((manual_script and not multi) or generated.get("script_manually_reviewed"))
        if proofread["changed"] and preserve_script:
            generated["orthographic_review"] = {
                "approved": False, "reason": "O agente encontrou ajustes ortográficos. Revise o texto sugerido antes de gerar voz e legendas.",
                "suggested_script": proofread["script"], "corrections": proofread["corrections"],
            }
            _save(item_id, owner, "orthographic_review_done", generated)
            raise PauseGeneration(generated["orthographic_review"]["reason"])
        if proofread["changed"]:
            generated["script"] = proofread["script"]
            generated.pop("editorial_review", None)
            generated.pop("terms", None)
            generated.pop("scenes", None)
        generated["orthographic_review"] = {
            "approved": True, "corrected": proofread["changed"], "corrections": proofread["corrections"],
        }
        _save(item_id, owner, "orthographic_review_done", generated)
    if not generated.get("editorial_review", {}).get("approved"):
        _save(item_id, owner, "editorial_review")
        preserve_script = bool((manual_script and not multi) or generated.get("script_manually_reviewed"))
        generated["editorial_review"] = await specialists.review_script(
            generated["script"], generated["subject"], generated["requirements"], context,
            manual=preserve_script,
        )
        _save(item_id, owner, "editorial_review_done", generated)
        repairs = int(generated.get("editorial_repair_attempts") or int(bool(generated.get("editorial_repair_attempted"))))
        while not generated["editorial_review"]["approved"] and not preserve_script and repairs < 2:
            generated["editorial_repair_attempts"] = repairs + 1
            generated["editorial_repair_attempted"] = True
            generated["editorial_review_before_repair"] = generated["editorial_review"]
            _save(item_id, owner, "editorial_repair", generated)
            feedback = str(generated["editorial_review"].get("reason") or "Revise o roteiro segundo as instruções editoriais.")
            # Do not feed an off-topic script back as source material: that anchors
            # the writer to the very subject the reviewer rejected.
            generated["script"] = await _generate_script(
                generated["subject"], generated["requirements"], context, "", editorial_feedback=feedback,
            )
            proofread = await specialists.proofread_script(generated["script"], str(context["settings"]["video_language"]))
            generated["script"] = proofread["script"]
            generated["orthographic_review"] = {
                "approved": True, "corrected": proofread["changed"], "corrections": proofread["corrections"],
            }
            generated["editorial_review"] = await specialists.review_script(
                generated["script"], generated["subject"], generated["requirements"], context, manual=False,
            )
            repairs += 1
            _save(item_id, owner, "editorial_review_done", generated)
        if not generated["editorial_review"]["approved"]:
            raise PauseGeneration("O revisor editorial pediu revisão: " + generated["editorial_review"]["reason"])
    if not generated.get("terms"):
        _save(item_id, owner, "terms")
        manual_terms = "" if generated.get("editorial_script_regenerated_from_review") else request.terms.strip()
        m.visual_term_count(
            generated["script"], float(context["settings"]["video_clip_duration"]),
            float(context["settings"]["voice_rate"]),
        )
        if manual_terms and not multi:
            generated["terms"] = manual_terms
        else:
            terms_context = _unique_text(_context_line(context), f"Preferências de busca do usuário: {manual_terms}" if manual_terms else "")
            terms, _ = await m.visual_terms(
                generated["subject"], generated["script"], float(context["settings"]["video_clip_duration"]),
                float(context["settings"]["voice_rate"]), context=terms_context,
            )
            generated["terms"] = ", ".join(terms)
        if generated.get("editorial_script_regenerated_from_review"):
            generated["editorial_assets_refreshed"] = True
        if not generated["terms"].strip() or "b-roll footage" in generated["terms"].lower():
            raise PauseGeneration("As palavras-chave não descrevem cenas concretas do roteiro. Revise os termos antes da curadoria.")
        _save(item_id, owner, "terms_done", generated)
    if not generated.get("scenes") or any(not scene.get("selected") for scene in generated["scenes"]):
        _save(item_id, owner, "visual_curator")
        specialists.vision_model()
        slots = m.visual_term_count(generated["script"], float(context["settings"]["video_clip_duration"]), float(context["settings"]["voice_rate"]))
        chunks = m.split_script(generated["script"], slots)
        terms = m._fill_visual_terms([term.strip() for term in generated["terms"].split(",") if term.strip()], slots)
        scenes = generated.get("scenes") or []
        while len(scenes) < len(chunks):
            index = len(scenes)
            scenes.append({"index": index, "narration": chunks[index], "query": terms[index], "candidates": [], "selected": None})
        missing = [index for index, scene in enumerate(scenes) if not scene.get("selected")]
        # Bounded batches keep provider load predictable and preserve completed scenes on failure.
        for start in range(0, len(missing), 3):
            indices = missing[start:start + 3]
            results = await asyncio.gather(*(
                specialists.curate_scene(item_id, index, chunks[index], scenes[index].get("query") or terms[index],
                                         {**context, "subject": generated["subject"]}, set(scenes[index]["excluded_ids"]))
                if scenes[index].get("excluded_ids") else
                specialists.curate_scene(item_id, index, chunks[index], scenes[index].get("query") or terms[index],
                                         {**context, "subject": generated["subject"]})
                for index in indices
            ), return_exceptions=True)
            used = {scene["selected"] for scene in scenes if scene.get("selected")}
            failures = []
            for index, result in zip(indices, results):
                if isinstance(result, BaseException):
                    failures.append(result)
                    continue
                specialists.choose_scene_material(result, used)
                scenes[index] = result
                if result.get("selected"):
                    used.add(result["selected"])
                else:
                    failures.append(PauseGeneration(f"O trecho {index + 1} não tem imagem ou vídeo coerente. Escolha uma alternativa ou ajuste a busca."))
            generated["scenes"] = scenes
            _save(item_id, owner, "visual_curator", generated)
            if failures:
                raise failures[0]
        _save(item_id, owner, "visual_curator_done", generated)
    materials = specialists.approved_materials(item_id, generated["scenes"])
    settings = {**context["settings"], "video_script_prompt": generated["requirements"],
                "custom_system_prompt": context["render_system_prompt"], "video_terms": generated["terms"],
                "video_source": "local", "video_materials": materials, "match_materials_to_script": True,
                "video_concat_mode": "sequential"}
    with m.database() as db:
        result = db.execute(
            "UPDATE generation_batch_items SET stage='submitting', submission_started_at=?, updated_at=? "
            "WHERE id=? AND lease_owner=? AND state='processing'",
            (m.now(), m.now(), item_id, owner),
        )
        if result.rowcount != 1:
            raise RuntimeError("A geração perdeu a reserva antes do envio.")
    job = await m.create_job(
        batch["project_id"],
        m.JobCreate(subject=generated["subject"], script=generated["script"], format=context["format"],
                    channel_ids=[context["channel"]["id"]], settings=settings),
        generation_item_id=item_id,
    )
    with m.database() as db:
        db.execute(
            "UPDATE generation_batch_items SET state='submitted', stage='rendering', job_id=?, error=NULL, "
            "lease_owner=NULL, lease_until=NULL, updated_at=? WHERE id=? AND lease_owner=?",
            (job["id"], m.now(), item_id, owner),
        )
        db.execute("INSERT OR IGNORE INTO generation_quality(item_id, job_id, state, updated_at) VALUES (?, ?, 'pending', ?)", (item_id, job["id"], m.now()))
        root_batch_id = db.execute("SELECT batch_id FROM generation_batch_items WHERE id=COALESCE(?, ?)",
                                   (item.get("repair_root_item_id"), item_id)).fetchone()
        operator_run = db.execute("SELECT id, channel_id FROM operator_runs WHERE batch_id=?",
                                  (root_batch_id["batch_id"] if root_batch_id else item["batch_id"],)).fetchone()
        if operator_run:
            operator = db.execute("SELECT auto_publish, enabled FROM operators WHERE channel_id=?", (operator_run["channel_id"],)).fetchone()
            db.execute("UPDATE video_jobs SET created_by='operator', auto_publish_authorized=? WHERE id=?",
                       (int(bool(operator and operator["auto_publish"] and operator["enabled"])), job["id"]))
            db.execute("UPDATE operator_runs SET state='submitted', job_id=?, finished_at=? WHERE id=?", (job["id"], m.now(), operator_run["id"]))
            db.execute("UPDATE operators SET lease_owner=NULL, lease_until=NULL, last_error=NULL, updated_at=? WHERE channel_id=? AND lease_owner=?",
                       (m.now(), operator_run["channel_id"], operator_run["id"]))


async def process_next_item(owner: str) -> bool:
    m = main_module()
    item = claim_next_item(owner)
    if not item:
        return False
    heartbeat = asyncio.create_task(_heartbeat(item["id"], owner))
    try:
        await _prepare_and_submit(item, owner)
    except Exception as exc:
        if isinstance(exc, (HTTPException, PauseGeneration)):
            logger.warning("Falha esperada na preparação do canal %s: %s", item["channel_id"], getattr(exc, "detail", str(exc)))
        else:
            logger.exception("Falha na preparação do canal %s", item["channel_id"])
        with m.database() as db:
            current = db.execute("SELECT submission_started_at FROM generation_batch_items WHERE id=?", (item["id"],)).fetchone()
            uncertain = bool(current and current["submission_started_at"])
            persisted = db.execute("SELECT id FROM video_jobs WHERE generation_item_id=?", (item["id"],)).fetchone() if uncertain else None
            if persisted:
                db.execute(
                    "UPDATE generation_batch_items SET state='submitted', stage='rendering', job_id=?, error=NULL, "
                    "lease_owner=NULL, lease_until=NULL, updated_at=? WHERE id=? AND lease_owner=?",
                    (persisted["id"], m.now(), item["id"], owner),
                )
                db.execute("INSERT OR IGNORE INTO generation_quality(item_id, job_id, state, updated_at) VALUES (?, ?, 'pending', ?)", (item["id"], persisted["id"], m.now()))
                return True
            detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc) if isinstance(exc, PauseGeneration) else "Não foi possível preparar o vídeo para este canal."
            if uncertain:
                detail = "O envio ao Turbo pode ter começado. Confira os trabalhos antes de repetir para evitar vídeo duplicado."
            db.execute(
                "UPDATE generation_batch_items SET state=?, stage=?, error=?, lease_owner=NULL, lease_until=NULL, updated_at=? "
                "WHERE id=? AND lease_owner=?",
                ("reconciliation_required" if uncertain else "paused" if isinstance(exc, PauseGeneration) or isinstance(exc, HTTPException) and exc.status_code == 409 else "failed", "reconciliation_required" if uncertain else "paused" if isinstance(exc, PauseGeneration) or isinstance(exc, HTTPException) and exc.status_code == 409 else "failed",
                 detail[:1000], m.now(), item["id"], owner),
            )
            root_batch_id = db.execute(
                "SELECT batch_id FROM generation_batch_items WHERE id=COALESCE(?, ?)",
                (item.get("repair_root_item_id"), item["id"]),
            ).fetchone()
            operator_run = db.execute("SELECT id, channel_id FROM operator_runs WHERE batch_id=?",
                                      (root_batch_id["batch_id"] if root_batch_id else item["batch_id"],)).fetchone()
            if operator_run:
                db.execute("UPDATE operator_runs SET state=?, error=?, finished_at=? WHERE id=?", ("paused" if isinstance(exc, PauseGeneration) else "failed", detail[:500], m.now(), operator_run["id"]))
                db.execute("UPDATE operators SET lease_owner=NULL, lease_until=NULL, paused_reason=?, last_error=?, updated_at=? WHERE channel_id=? AND lease_owner=?",
                           (detail[:500], detail[:500], m.now(), operator_run["channel_id"], operator_run["id"]))
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
    return True


def _repair_decision(report: dict[str, Any], generated: dict[str, Any], *, manual_script: bool) -> dict[str, Any] | None:
    """Turn observable failures into bounded, deterministic repair actions."""
    videos = [video for video in report.get("videos", []) if not video.get("approved")]
    failures = {code for video in videos for code in (
        video.get("failed_checks") or [name for name, passed in video.get("checks", {}).items() if not passed]
        + (["visual_scenes"] if any(not entry.get("approved") for entry in video.get("observations", [])) else []))}
    if not failures:
        return None
    if "duration" in failures and manual_script:
        return None
    allowed = {"duration", "aspect", "audible", "black_frames", "visual_scenes", "render_failed"}
    if failures - allowed:
        return None
    scenes = generated.get("scenes") or []
    affected: set[int] = set()
    for video in videos:
        affected.update(int(entry["scene"]) - 1 for entry in video.get("observations", [])
                        if not entry.get("approved") and isinstance(entry.get("scene"), int))
        duration = float((video.get("measurements") or {}).get("duration_seconds") or 0)
        for segment in video.get("black_segments") or []:
            if duration > 0:
                try:
                    start = float(segment.split("black_start:", 1)[1].split()[0])
                    affected.add(min(len(scenes) - 1, int(start / duration * len(scenes))))
                except (IndexError, ValueError):
                    affected.update(range(len(scenes)))
    if "black_frames" in failures and not affected:
        affected.update(range(len(scenes)))
    measured = next((video.get("measurements", {}).get("duration_seconds") for video in videos
                     if "duration" in failures and video.get("measurements", {}).get("duration_seconds")), None)
    return {"failed_checks": sorted(failures), "scene_indexes": sorted(index for index in affected if 0 <= index < len(scenes)),
            "duration_seconds": measured, "reason": " · ".join(video.get("reason", "") for video in videos)[:500]}


def _queue_auto_repair(item_id: str, report: dict[str, Any]) -> dict[str, Any] | None:
    """Create exactly one successor per attempt, including across worker restarts."""
    m = main_module()
    with m.database() as db:
        db.execute("BEGIN IMMEDIATE")
        item = db.execute("SELECT * FROM generation_batch_items WHERE id=?", (item_id,)).fetchone()
        quality = db.execute("SELECT state FROM generation_quality WHERE item_id=?", (item_id,)).fetchone()
        if not item or not quality or quality["state"] != "blocked":
            return None
        existing = db.execute("SELECT batch_id, id FROM generation_batch_items WHERE repair_parent_item_id=?", (item_id,)).fetchone()
        if existing:
            db.execute("UPDATE generation_quality SET state='repairing', updated_at=? WHERE item_id=?", (m.now(), item_id))
            return {"batch_id": existing["batch_id"], "item_id": existing["id"]}
        if item["repair_attempt"] >= MAX_AUTO_REPAIRS:
            return None
        original = db.execute("SELECT * FROM generation_batches WHERE id=?", (item["batch_id"],)).fetchone()
        original_request = json.loads(original["input_json"])
        root_id = item["repair_root_item_id"] or item_id
        root_batch = db.execute("SELECT b.input_json FROM generation_batches b JOIN generation_batch_items i ON i.batch_id=b.id WHERE i.id=?",
                                (root_id,)).fetchone()
        root_request = json.loads(root_batch["input_json"]) if root_batch else original_request
        generated = json.loads(item["generated_json"])
        manual_script = bool((root_request.get("script") and root_request.get("script_is_manual") is not False
                              and len(root_request.get("channel_ids") or []) == 1)
                             or generated.get("script_manually_reviewed"))
        decision = _repair_decision(report, generated, manual_script=manual_script)
        if not decision:
            return None
        attempt = int(item["repair_attempt"]) + 1
        request_data = {**original_request, "channel_ids": [item["channel_id"]],
                        "client_request_id": f"auto-repair-{root_id}-{attempt}"}
        if len(root_request.get("channel_ids") or []) > 1:
            request_data["script"] = ""
        request_json = json.dumps(request_data, ensure_ascii=False, sort_keys=True)
        new_batch_id, new_item_id = str(uuid4()), str(uuid4())
        context = json.loads(item["context_json"])
        failures = set(decision["failed_checks"])
        generated["repair_plan"] = decision
        if "duration" in failures:
            generated["repair_previous_script"] = generated.get("script", "")
            for key in ("script", "editorial_review", "orthographic_review", "terms", "scenes", "editorial_repair_attempted"):
                generated.pop(key, None)
        else:
            if "visual_scenes" in failures or "black_frames" in failures:
                for index in decision["scene_indexes"]:
                    scene = generated["scenes"][index]
                    scene["excluded_ids"] = list(dict.fromkeys([*(scene.get("excluded_ids") or []), scene.get("selected")]))
                    scene["selected"] = None
            if "audible" in failures:
                settings = context["settings"]
                settings["voice_volume"] = min(2.0, max(0.5, float(settings.get("voice_volume") or 0) * 1.5))
            if "aspect" in failures:
                context["settings"]["video_aspect"] = "9:16" if context["format"] == "short" else "16:9"
            source = specialists.material_root(item_id)
            destination = specialists.material_root(new_item_id)
            for scene in generated.get("scenes") or []:
                selected = next((candidate for candidate in scene.get("candidates", []) if candidate["id"] == scene.get("selected")), None)
                if selected and selected.get("filename"):
                    source_file = source / selected["filename"]
                    if not source_file.is_file():
                        raise PauseGeneration("O material aprovado não está mais disponível para remontar o vídeo.")
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_file, destination / selected["filename"])
        timestamp = m.now()
        db.execute("INSERT INTO generation_batches(id, project_id, client_request_id, request_hash, input_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (new_batch_id, original["project_id"], request_data["client_request_id"], hashlib.sha256(request_json.encode()).hexdigest(), request_json, timestamp))
        db.execute("INSERT INTO generation_batch_items(id, batch_id, channel_id, channel_name, state, stage, context_json, generated_json, repair_parent_item_id, repair_root_item_id, repair_attempt, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?, 'queued', 'repairing', ?, ?, ?, ?, ?, ?, ?)",
                   (new_item_id, new_batch_id, item["channel_id"], item["channel_name"], json.dumps(context, ensure_ascii=False),
                    json.dumps(generated, ensure_ascii=False), item_id, root_id, attempt, timestamp, timestamp))
        report["repair"] = {"status": "queued", "batch_id": new_batch_id, "item_id": new_item_id, "attempt": attempt, "max_attempts": MAX_AUTO_REPAIRS}
        db.execute("UPDATE generation_quality SET state='repairing', report_json=?, updated_at=? WHERE item_id=?",
                   (json.dumps(report, ensure_ascii=False), timestamp, item_id))
        return report["repair"]


async def review_next_completed_job() -> bool:
    """A durable quality claim; a restarted worker can reclaim an expired review."""
    m = main_module()
    cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with m.database() as db:
        row = db.execute(
            "SELECT q.item_id, q.job_id, q.report_json, i.generated_json, i.context_json FROM generation_quality q "
            "JOIN generation_batch_items i ON i.id=q.item_id "
            "JOIN video_jobs j ON j.id=q.job_id WHERE ((q.state='pending' AND q.updated_at<=?) OR (q.state='reviewing' AND q.updated_at<?)) "
            "AND j.state IN ('completed','failed','interrupted') ORDER BY q.updated_at LIMIT 1", (m.now(), cutoff)
        ).fetchone()
        if not row:
            return False
        claimed = db.execute("UPDATE generation_quality SET state='reviewing', updated_at=? WHERE item_id=? AND (state='pending' OR updated_at<?)",
                             (m.now(), row["item_id"], cutoff)).rowcount
        if not claimed:
            return False
    retry_at = None
    try:
        with m.database() as db:
            job = db.execute("SELECT state FROM video_jobs WHERE id=?", (row["job_id"],)).fetchone()
            artifacts = db.execute("SELECT remote_path FROM artifacts WHERE job_id=? AND kind='video' ORDER BY created_at, id", (row["job_id"],)).fetchall()
        generated = json.loads(row["generated_json"])
        context = json.loads(row["context_json"])
        expected_count = int(context["settings"].get("video_count", 1))
        if job["state"] != "completed" or len(artifacts) != expected_count:
            reason = (f"O render entregou {len(artifacts)} vídeo(s), mas o canal solicitou {expected_count}."
                      if job["state"] == "completed" else "O render falhou antes de entregar o vídeo completo.")
            reports = [{"approved": False, "failed_checks": ["render_failed"], "reason": reason,
                        "checks": {}, "measurements": {}, "observations": []}]
        else:
            reports = []
            for index, artifact in enumerate(artifacts):
                path = specialists.material_root(row["item_id"]) / f"final-review-{index}.mp4"
                await m.download_turbo_file(artifact["remote_path"], path)
                reports.append(await specialists.inspect_final_video(path, generated["scenes"], context["settings"],
                                                                    generated["script"], _duration_bounds(context)))
        approved = all(report["approved"] for report in reports)
        report = {"approved": approved, "videos": reports, "reviewed_at": m.now(),
                  "reason": " · ".join(video.get("reason", "") for video in reports if not video.get("approved"))[:500]}
        state = "approved" if approved else "blocked"
    except Exception as exc:
        reason = str(getattr(exc, "detail", exc))[:500]
        previous = json.loads(row["report_json"] or "{}")
        attempts = int(previous.get("transient_attempts") or 0) + 1
        if isinstance(exc, HTTPException) and exc.status_code in {429, 502, 503, 504} and attempts <= 3:
            state = "pending"
            retry_at = (datetime.now(UTC) + timedelta(seconds=60 * attempts)).isoformat()
            report = {"approved": False, "reason": reason, "transient_attempts": attempts, "next_retry_at": retry_at}
        else:
            state = "error"
            report = {"approved": False, "reason": reason, "reviewed_at": m.now()}
    with m.database() as db:
        db.execute("UPDATE generation_quality SET state=?, report_json=?, updated_at=? WHERE item_id=?",
                   (state, json.dumps(report, ensure_ascii=False), retry_at or m.now(), row["item_id"]))
        root = db.execute("SELECT COALESCE(repair_root_item_id, id) AS root_id FROM generation_batch_items WHERE id=?",
                          (row["item_id"],)).fetchone()
        operator_run = db.execute("SELECT o.channel_id FROM operator_runs o JOIN generation_batch_items i ON i.batch_id=o.batch_id WHERE i.id=?",
                                  (root["root_id"],)).fetchone() if root else None
    repair = None
    if state == "blocked":
        try:
            repair = _queue_auto_repair(row["item_id"], report)
        except Exception as exc:
            logger.exception("Falha ao preparar correção automática do item %s", row["item_id"])
            report["repair_error"] = str(getattr(exc, "detail", exc))[:500]
        if not repair:
            failed_checks = {check for video in report.get("videos", []) for check in video.get("failed_checks", [])}
            if failed_checks & {"audio", "continuous_audio"}:
                report.setdefault("repair_error", "A correção automática não ajusta a trilha de áudio. Revise voz ou música e gere novamente.")
            else:
                report.setdefault("repair_error", "Limite de correções atingido ou intervenção manual necessária.")
            with m.database() as db:
                db.execute("UPDATE generation_quality SET report_json=?, updated_at=? WHERE item_id=? AND state='blocked'",
                           (json.dumps(report, ensure_ascii=False), m.now(), row["item_id"]))
    if operator_run and state not in {"approved", "pending"} and not repair:
        m.pause_operator(operator_run["channel_id"], report.get("reason") or "A revisão final reprovou o vídeo.", row["job_id"])
    return True
