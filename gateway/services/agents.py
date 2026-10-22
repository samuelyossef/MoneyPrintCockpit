"""Strategy, analytics, schedule and the operator loop."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import FastAPI, HTTPException, Request, Response

from gateway import main as m


def youtube_trends(language: str) -> list[str]:
    """Titles trending on YouTube in the channel's region. Only a hint for the strategist, so any failure means no trends."""
    from googleapiclient.discovery import build

    with m.database() as db:
        row = db.execute("SELECT id FROM channels WHERE platform='youtube' AND status='connected' AND deleted_at IS NULL LIMIT 1").fetchone()
    if not row:
        return []
    try:
        youtube = build("youtube", "v3", credentials=m.youtube_credentials(row["id"]), cache_discovery=False)
        region = m.REGION_BY_LANGUAGE.get(language.split("-")[0], "US")
        items = youtube.videos().list(part="snippet", chart="mostPopular", regionCode=region, maxResults=25).execute().get("items") or []
    except Exception as exc:
        m.logger.warning("Tendências do YouTube indisponíveis: %s", exc)
        return []
    return [item["snippet"]["title"] for item in items if (item.get("snippet") or {}).get("title")]


async def suggest_topics(channel: sqlite3.Row, format: str, count: int) -> list[dict[str, str]]:
    with m.database() as db:
        recent = [row["subject"] for row in db.execute(
            "SELECT jobs.subject FROM video_jobs AS jobs JOIN publications ON publications.job_id = jobs.id "
            "WHERE publications.channel_id=? AND jobs.deleted_at IS NULL ORDER BY jobs.created_at DESC LIMIT 30",
            (channel["id"],),
        )]
    trends = await asyncio.to_thread(m.youtube_trends, channel["language"])
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
    for item in m.parse_json_object(await m.llm_chat(prompt, json_mode=True)).get("topics") or []:
        topic = str(item.get("topic") or "").strip()[:500] if isinstance(item, dict) else ""
        if topic and topic.lower() not in seen:  # the model still repeats a produced subject now and then
            seen.add(topic.lower())
            topics.append({"topic": topic, "angle": str(item.get("angle") or ""), "rationale": str(item.get("rationale") or "")})
    if not topics:
        raise HTTPException(status_code=502, detail="A IA não sugeriu nenhum tema novo")
    return topics[:count]


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
        ids="channel==MINE", startDate=start_date, endDate=datetime.now(UTC).date().isoformat(), metrics=m.ANALYTICS_METRICS, filters=f"video=={video_id}",
    ).execute()
    rows = report.get("rows") or []
    if not rows:  # no views yet (Analytics lags a day or two)
        return {name: 0 for name in m.ANALYTICS_METRICS.split(",")}
    return dict(zip((header["name"] for header in report.get("columnHeaders") or []), rows[0]))


def next_publish_slot(operator: sqlite3.Row, db: sqlite3.Connection, reference: datetime | None = None) -> datetime:
    zone = ZoneInfo(operator["publish_timezone"])
    hour, minute = map(int, operator["publish_time"].split(":"))
    days = set(json.loads(operator["publish_days_json"]))
    start = m.as_utc(reference or datetime.now(UTC)) + timedelta(hours=1)
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
    raise HTTPException(status_code=409, detail="Não há janela livre nos próximos 60 dias.")


def pause_operator(channel_id: str, reason: str, job_id: str | None = None) -> None:
    with m.database() as db:
        db.execute("UPDATE operators SET paused_reason=?, updated_at=? WHERE channel_id=?", (reason[:500], m.now(), channel_id))
    m.notify(f"operator-paused:{channel_id}:{job_id or reason}", "operator_paused", "Automação pausada", reason, job_id)


def youtube_video_status(credentials: Any, video_id: str) -> dict[str, Any] | None:
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    items = youtube.videos().list(part="status", id=video_id).execute().get("items") or []
    return items[0].get("status") if items else None


async def verify_auto_youtube_publication(job_id: str, channel_id: str, external_id: str, slot: datetime) -> None:
    credentials = await asyncio.to_thread(m.youtube_credentials, channel_id)
    status = await asyncio.to_thread(m.youtube_video_status, credentials, external_id)
    if not status:
        raise HTTPException(status_code=502, detail="O YouTube não retornou o vídeo enviado.")
    privacy = status.get("privacyStatus")
    if privacy == "public":
        if datetime.now(UTC) < slot - timedelta(minutes=2):
            raise HTTPException(status_code=502, detail="O vídeo ficou público antes do horário programado.")
        with m.database() as db:
            db.execute("UPDATE publications SET state='published', updated_at=? WHERE job_id=? AND channel_id=? AND state='platform_scheduled'",
                       (m.now(), job_id, channel_id))
        return
    published_at = status.get("publishAt")
    if privacy != "private" or not published_at or abs((m.parse_time(published_at) - slot).total_seconds()) > 60:
        raise HTTPException(status_code=502, detail="O YouTube não confirmou a publicação programada.")
    if datetime.now(UTC) > slot + timedelta(minutes=15):
        raise HTTPException(status_code=502, detail="O vídeo continua privado após o horário programado.")


async def auto_publish_operator_job(job_id: str) -> None:
    with m.database() as db:
        db.execute("BEGIN IMMEDIATE")
        job = m.job_row_or_404(db, job_id)
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
        if target["state"] == "auto_reserved" and target["auto_lease_until"] and target["auto_lease_until"] > m.now():
            return
        if job["state"] not in ("completed", "approved") or not job["auto_publish_authorized"]:
            return
        m.require_quality_approval(db, job)
        if target["channel_status"] != "connected" or not m.youtube_token_file(target["channel_id"]).is_file():
            raise HTTPException(status_code=409, detail="Canal sem autorização válida. Reconecte em Canais.")
        artifact = db.execute("SELECT remote_path FROM artifacts WHERE job_id=? AND kind='video' LIMIT 1", (job_id,)).fetchone()
        stored = db.execute("SELECT value_json FROM job_metadata WHERE job_id=?", (job_id,)).fetchone()
        if not artifact or not stored:
            raise HTTPException(status_code=409, detail="Vídeo final ou metadados ausentes.")
        metadata, errors, _ = m.normalize_youtube_metadata(json.loads(stored["value_json"]))
        if errors:
            raise HTTPException(status_code=409, detail="Metadados inválidos: " + ", ".join(errors))
        slot = m.parse_time(target["scheduled_at"]) if target["state"] == "auto_reserved" else m.next_publish_slot(target, db)
        if slot <= datetime.now(UTC) + timedelta(hours=1):
            slot = m.next_publish_slot(target, db)
        claimed = db.execute(
            "UPDATE publications SET state='auto_reserved', scheduled_at=?, auto_publish=1, auto_lease_owner=?, auto_lease_until=?, updated_at=? "
            "WHERE id=? AND state IN ('pending','auto_reserved') AND (auto_lease_until IS NULL OR auto_lease_until <= ? OR state='pending')",
            (slot.isoformat(), str(uuid4()), (datetime.now(UTC) + timedelta(hours=3)).isoformat(), m.now(), target["id"], m.now()),
        ).rowcount
        if not claimed:
            return
        channel = m.channel_row_or_404(db, target["channel_id"])
    with tempfile.TemporaryDirectory() as folder:
        video_path = Path(folder) / "video.mp4"
        async def video() -> Path:
            await m.download_turbo_file(artifact["remote_path"], video_path)
            if not video_path.is_file() or video_path.stat().st_size == 0:
                raise HTTPException(status_code=502, detail="O vídeo final está vazio.")
            with m.database() as db:
                current = db.execute(
                    "SELECT o.enabled, o.auto_publish, o.paused_reason, c.status FROM operators o "
                    "JOIN channels c ON c.id=o.channel_id WHERE o.channel_id=?", (channel["id"],)
                ).fetchone()
                if not current or not current["enabled"] or not current["auto_publish"] or current["paused_reason"] or current["status"] != "connected":
                    raise HTTPException(status_code=409, detail="Autorização automática ou conexão do canal mudou durante o preparo.")
                if not m.youtube_token_file(channel["id"]).is_file():
                    raise HTTPException(status_code=409, detail="Autorização do YouTube ausente.")
                db.execute("UPDATE video_jobs SET state='approved', updated_at=? WHERE id=? AND state='completed'", (m.now(), job_id))
            return video_path
        await m.publish_to_channel(job_id, channel, video, {**metadata, "format": job["format"]}, False, slot)
    with m.database() as db:
        publication = db.execute("SELECT external_id, scheduled_at FROM publications WHERE job_id=? AND channel_id=? AND state='platform_scheduled'",
                                 (job_id, channel["id"])).fetchone()
    if publication:
        await m.verify_auto_youtube_publication(job_id, channel["id"], publication["external_id"], m.parse_time(publication["scheduled_at"]))


async def prepare_auto_publications() -> None:
    with m.database() as db:
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
            await m.auto_publish_operator_job(row["id"])
        except Exception as exc:
            with m.database() as db:
                db.execute(
                    "UPDATE publications SET auto_lease_owner=NULL, auto_lease_until=NULL WHERE job_id=? AND channel_id=? AND state='auto_reserved'",
                    (row["id"], row["channel_id"]),
                )
            m.pause_operator(row["channel_id"], str(getattr(exc, "detail", exc)), row["id"])


async def reconcile_auto_publications() -> None:
    with m.database() as db:
        uncertain = [dict(row) for row in db.execute(
            "SELECT p.id, p.job_id, p.channel_id FROM publications p WHERE p.auto_publish=1 AND p.state='uploading' "
            "AND (p.auto_lease_until IS NULL OR p.auto_lease_until <= ?)", (m.now(),)
        )]
    for row in uncertain:
        with m.database() as db:
            db.execute("UPDATE publications SET state='reconciliation_required', error=?, updated_at=? WHERE id=? AND state='uploading'",
                       ("Envio interrompido; confira o YouTube antes de tentar novamente.", m.now(), row["id"]))
        m.pause_operator(row["channel_id"], "Resultado do upload incerto; confira o YouTube.", row["job_id"])
    with m.database() as db:
        scheduled = [dict(row) for row in db.execute(
            "SELECT p.job_id, p.channel_id, p.external_id, p.scheduled_at FROM publications p "
            "WHERE p.auto_publish=1 AND p.state='platform_scheduled' AND p.scheduled_at <= ?",
            ((datetime.now(UTC) + timedelta(minutes=15)).isoformat(),),
        )]
    for row in scheduled:
        try:
            await m.verify_auto_youtube_publication(row["job_id"], row["channel_id"], row["external_id"], m.parse_time(row["scheduled_at"]))
        except Exception as exc:
            m.pause_operator(row["channel_id"], str(getattr(exc, "detail", exc)), row["job_id"])


async def publish_due_schedules() -> None:
    with m.database() as db:
        due = db.execute("SELECT job_id, channel_id FROM publications WHERE state='scheduled' AND scheduled_at <= ?", (m.now(),)).fetchall()
    targets: dict[str, list[str]] = {}
    for row in due:
        targets.setdefault(row["job_id"], []).append(row["channel_id"])
    for job_id, channel_ids in targets.items():
        try:
            await m.publish_job(job_id, m.PublishInput(channel_ids=channel_ids))
        except Exception as exc:
            # A check failing before the claim (lost authorization, deleted video) would otherwise retry every minute forever.
            with m.database() as db:
                db.execute(
                    f"UPDATE publications SET state='failed', error=?, updated_at=? WHERE job_id=? AND state='scheduled' AND channel_id IN ({','.join('?' * len(channel_ids))})",
                    (str(getattr(exc, "detail", exc))[:500], m.now(), job_id, *channel_ids),
                )


async def reconcile_processing_publications() -> None:
    with m.database() as db:
        job_ids = [row["job_id"] for row in db.execute("SELECT DISTINCT job_id FROM publications WHERE state='processing'")]
    for job_id in job_ids:
        try:
            await m.reconcile_publications(job_id)
        except Exception as exc:
            m.logger.warning("Reconciliação: o trabalho %s falhou: %s", job_id, getattr(exc, "detail", exc))


async def run_due_operators() -> None:
    with m.database() as db:
        due = [row["channel_id"] for row in db.execute("SELECT channel_id FROM operators WHERE enabled=1 AND paused_reason IS NULL AND next_run_at <= ?", (m.now(),))]
    for channel_id in due:
        await m.run_operator(channel_id, scheduled=True)


async def prepare_operator_jobs() -> None:
    """Keeps operator renders tracked without a browser open and writes their metadata; they stay "completed" for approval."""
    with m.database() as db:
        job_ids = [row["id"] for row in db.execute(
            "SELECT jobs.id FROM video_jobs AS jobs LEFT JOIN job_metadata AS meta ON meta.job_id = jobs.id "
            "WHERE jobs.created_by='operator' AND jobs.deleted_at IS NULL AND meta.job_id IS NULL AND jobs.state IN ('queued', 'running', 'completed')"
        )]
    for job_id in job_ids:
        try:
            if (await m.reconcile_job(job_id)).get("state") == "completed":
                await m.generate_metadata(job_id)
        except Exception as exc:
            m.logger.warning("Operador: o trabalho %s não avançou: %s", job_id, getattr(exc, "detail", exc))
            with m.database() as db:
                channel = db.execute(
                    "SELECT p.channel_id FROM publications p JOIN operators o ON o.channel_id=p.channel_id "
                    "WHERE p.job_id=? AND o.auto_publish=1 LIMIT 1", (job_id,)
                ).fetchone()
            if channel:
                m.pause_operator(channel["channel_id"], str(getattr(exc, "detail", exc)), job_id)


def pause_unapproved_operator_reviews() -> None:
    """A blocked final review must stop the channel instead of waiting silently for upload."""
    with m.database() as db:
        blocked = [dict(row) for row in db.execute(
            "SELECT j.id, p.channel_id, q.state FROM video_jobs j "
            "JOIN publications p ON p.job_id=j.id JOIN operators o ON o.channel_id=p.channel_id "
            "JOIN generation_quality q ON q.job_id=j.id "
            "WHERE j.created_by='operator' AND j.auto_publish_authorized=1 AND j.deleted_at IS NULL "
            "AND q.state IN ('blocked','error') AND o.enabled=1 AND o.auto_publish=1 AND o.paused_reason IS NULL"
        )]
    for row in blocked:
        m.pause_operator(row["channel_id"], "A revisão final não aprovou o vídeo. Corrija o relatório antes de reativar.", row["id"])


def pause_failed_operator_jobs() -> None:
    with m.database() as db:
        failed = [dict(row) for row in db.execute(
            "SELECT j.id, j.error, p.channel_id FROM video_jobs j JOIN publications p ON p.job_id=j.id "
            "JOIN operators o ON o.channel_id=p.channel_id WHERE j.created_by='operator' AND j.state IN ('failed','interrupted') "
            "AND NOT EXISTS (SELECT 1 FROM generation_quality q WHERE q.item_id=j.generation_item_id AND q.state IN ('pending','reviewing','repairing')) "
            "AND o.enabled=1 AND o.auto_publish=1 AND o.paused_reason IS NULL AND j.updated_at > o.updated_at"
        )]
    for row in failed:
        m.pause_operator(row["channel_id"], row["error"] or "Geração falhou; confira o trabalho.", row["id"])


def recover_operator_leases() -> None:
    with m.database() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT channel_id, lease_owner FROM operators WHERE lease_owner IS NOT NULL AND lease_until <= ?", (m.now(),)
        )]
        for row in rows:
            db.execute("UPDATE operator_runs SET state='interrupted', error=?, finished_at=? WHERE id=? AND state='running'",
                       ("Worker interrompido antes da conclusão da execução.", m.now(), row["lease_owner"]))
            db.execute("UPDATE operators SET lease_owner=NULL, lease_until=NULL, paused_reason=?, updated_at=? WHERE channel_id=? AND lease_owner=?",
                       ("Execução interrompida; confira o trabalho antes de reativar.", m.now(), row["channel_id"], row["lease_owner"]))
    for row in rows:
        m.notify(f"operator-interrupted:{row['lease_owner']}", "operator_paused", "Automação pausada",
               "Worker interrompido; confira o trabalho antes de reativar.")


async def agent_tick() -> None:
    for check in (m.recover_operator_leases, m.pause_failed_operator_jobs, m.pause_unapproved_operator_reviews):
        try:
            check()
        except Exception:
            m.logger.exception("Passo %s do laço de agentes falhou", check.__name__)
    for step in (m.reconcile_stale_jobs, m.reconcile_auto_publications, m.publish_due_schedules, m.reconcile_processing_publications, m.run_due_operators, m.prepare_operator_jobs, m.prepare_auto_publications):
        try:
            await step()
        except Exception:
            m.logger.exception("Passo %s do laço de agentes falhou", step.__name__)


async def agent_loop() -> None:
    # ponytail: in-process loop, fine while the Dockerfile runs a single uvicorn worker; several workers would need a DB lock.
    while True:
        await m.agent_tick()
        await asyncio.sleep(m.AGENT_LOOP_SECONDS)
