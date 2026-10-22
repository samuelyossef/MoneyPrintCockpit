"""Analytics, strategy, schedule, and operator routes."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gateway.deps import (
    as_utc,
    channel_row_or_404,
    database,
    now,
    row_as_channel,
    row_as_project,
    runtime,
)

router = APIRouter()
logger = logging.getLogger("cockpit.agents")

PROJECT_NAMES = {"long": "Vídeos longos", "short": "Shorts"}  # stored project names shared with the Generator and Shorts screens
REGION_BY_LANGUAGE = {"pt": "BR", "en": "US", "es": "ES", "de": "DE"}
ANALYTICS_METRICS = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,subscribersGained"



def youtube_trends(language: str) -> list[str]:
    """Titles trending on YouTube in the channel's region. Only a hint for the strategist, so any failure means no trends."""
    from googleapiclient.discovery import build

    with database() as db:
        row = db.execute("SELECT id FROM channels WHERE platform='youtube' AND status='connected' AND deleted_at IS NULL LIMIT 1").fetchone()
    if not row:
        return []
    try:
        youtube = build("youtube", "v3", credentials=runtime().youtube_credentials(row["id"]), cache_discovery=False)
        region = REGION_BY_LANGUAGE.get(language.split("-")[0], "US")
        items = youtube.videos().list(part="snippet", chart="mostPopular", regionCode=region, maxResults=25).execute().get("items") or []
    except Exception as exc:
        logger.warning("YouTube trends are unavailable: %s", exc)
        return []
    return [item["snippet"]["title"] for item in items if (item.get("snippet") or {}).get("title")]


async def suggest_topics(channel: sqlite3.Row, format: str, count: int) -> list[dict[str, str]]:
    with database() as db:
        recent = [row["subject"] for row in db.execute(
            "SELECT jobs.subject FROM video_jobs AS jobs JOIN publications ON publications.job_id = jobs.id "
            "WHERE publications.channel_id=? AND jobs.deleted_at IS NULL ORDER BY jobs.created_at DESC LIMIT 30",
            (channel["id"],),
        )]
    trends = await asyncio.to_thread(runtime().youtube_trends, channel["language"])
    length = "long videos of 5 to 15 minutes" if format == "long" else "vertical Shorts of 60 to 90 seconds"
    prompt = (
        f"You are a channel content strategist. Suggest {count} topics for {length}, in {channel['language']}."
        + (f" Channel niche: {channel['niche']}." if channel["niche"] else "")
        + ("\nTrending on YouTube now (inspiration only, adapt them to the niche):\n- " + "\n- ".join(trends) if trends else "")
        + ("\nAlready produced, do not repeat:\n- " + "\n- ".join(recent) if recent else "")
        + "\nDo not invent numbers, statistics, or sources."
        + " Write each topic, angle, and rationale in the channel language above."
        + ' Respond with JSON only: {"topics": [{"topic": "a topic ready to use as a job title", "angle": "the hook or angle", "rationale": "why it should work on this channel"}]}'
    )
    seen = {subject.strip().lower() for subject in recent}
    topics: list[dict[str, str]] = []
    for item in runtime().parse_json_object(await runtime().llm_chat(prompt, json_mode=True)).get("topics") or []:
        topic = str(item.get("topic") or "").strip()[:500] if isinstance(item, dict) else ""
        if topic and topic.lower() not in seen:  # the model still repeats a produced subject now and then
            seen.add(topic.lower())
            topics.append({"topic": topic, "angle": str(item.get("angle") or ""), "rationale": str(item.get("rationale") or "")})
    if not topics:
        raise HTTPException(status_code=502, detail="The model did not suggest any new topic")
    return topics[:count]


class StrategyInput(BaseModel):
    channel_id: str
    format: Literal["long", "short"] = "short"
    count: int = Field(default=5, ge=1, le=10)


@router.post("/api/agents/strategy")
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


@router.get("/api/analytics/overview")
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


@router.get("/api/agents/analytics")
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


@router.post("/api/agents/analytics/refresh")
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
            credentials = await asyncio.to_thread(runtime().youtube_credentials, channel_id)
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
                detail = "Reconnect the channel in Channels to enable Analytics." if exc.resp.status == 403 else f"YouTube Analytics returned {exc.resp.status}."
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
            advice = runtime().parse_json_object(await runtime().llm_chat(
                "You analyze the performance of a YouTube channel's videos. YouTube Analytics metrics per video "
                f"(averageViewDuration in seconds, averageViewPercentage in %):\n{table}\n"
                "Compare the videos and give 3 to 5 practical recommendations for the next ones, citing only numbers from this table. "
                "Write the recommendations in the same language as the video titles in the table. "
                'Respond with JSON only: {"recommendations": ["short text"]}',
                json_mode=True,
            ))
        except HTTPException as exc:
            errors.append({"channel_id": channel_id, "detail": f"Recommendations are unavailable: {exc.detail}"})
            continue
        items = [str(item).strip() for item in advice.get("recommendations") or [] if str(item).strip()][:5]
        with database() as db:
            db.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (f"analytics-recommendations:{channel_id}", json.dumps(items), now()),
            )
    return {**analytics_overview(), "errors": errors}


@router.get("/api/agents/schedule")
def list_schedule() -> list[dict[str, Any]]:
    with database() as db:
        return [dict(row) for row in db.execute(
            "SELECT p.job_id, p.channel_id, p.platform, p.state, p.auto_publish, p.external_id, p.scheduled_at, jobs.subject, channels.name AS channel_name, json_extract(meta.value_json, '$.title') AS title "
            "FROM publications AS p JOIN video_jobs AS jobs ON jobs.id = p.job_id LEFT JOIN channels ON channels.id = p.channel_id "
            "LEFT JOIN job_metadata AS meta ON meta.job_id = jobs.id WHERE p.state IN ('scheduled','auto_reserved','platform_scheduled') AND jobs.deleted_at IS NULL ORDER BY p.scheduled_at"
        )]


def get_or_create_project(name: str, language: str) -> dict[str, Any]:
    with database() as db:
        row = db.execute("SELECT * FROM projects WHERE name=? AND deleted_at IS NULL ORDER BY created_at LIMIT 1", (name,)).fetchone()
    if row:
        return row_as_project(row)
    return runtime().create_project(runtime().ProjectCreate(name=name, language=language, publish={"auto_publish": False, "platforms": [], "youtube_privacy": "private"}))


class OperatorInput(BaseModel):
    enabled: bool = False
    format: Literal["long", "short"] = "short"
    cadence_hours: Literal[12, 24, 48, 168] = 24
    next_run_at: datetime | None = None
    auto_publish: bool = False
    publish_days: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    publish_time: str = "12:00"
    publish_timezone: str = "America/Sao_Paulo"


@router.get("/api/agents/operators")
def list_operators() -> dict[str, Any]:
    with database() as db:
        operators = [
            {"channel_id": row["id"], "channel_name": row["name"], "platform": row["platform"], "enabled": bool(row["enabled"]),
             "format": row["format"] or "short", "cadence_hours": row["cadence_hours"] or 24,
             "next_run_at": row["next_run_at"], "last_run_at": row["last_run_at"], "last_error": row["last_error"],
             "auto_publish": bool(row["auto_publish"]), "publish_days": json.loads(row["publish_days_json"] or "[0,1,2,3,4,5,6]"),
             "publish_time": row["publish_time"], "publish_timezone": row["publish_timezone"], "paused_reason": row["paused_reason"],
             "last_run": dict(db.execute("SELECT id, state, job_id, batch_id, error, started_at FROM operator_runs WHERE channel_id=? ORDER BY started_at DESC LIMIT 1", (row["id"],)).fetchone() or {}),
             "next_publication": dict(db.execute(
                 "SELECT p.job_id, p.state, p.scheduled_at, p.external_id, j.subject FROM publications p JOIN video_jobs j ON j.id=p.job_id "
                 "WHERE p.channel_id=? AND p.auto_publish=1 AND p.state IN ('auto_reserved','uploading','platform_scheduled') "
                 "ORDER BY p.scheduled_at LIMIT 1", (row["id"],)
             ).fetchone() or {})}
            for row in db.execute(
                "SELECT channels.id, channels.name, channels.platform, operators.enabled, operators.format, operators.cadence_hours, "
                "operators.next_run_at, operators.last_run_at, operators.last_error, operators.auto_publish, operators.publish_days_json, operators.publish_time, operators.publish_timezone, operators.paused_reason FROM channels LEFT JOIN operators ON operators.channel_id = channels.id "
                "WHERE channels.status='connected' AND channels.deleted_at IS NULL ORDER BY channels.created_at"
            )
        ]
        operator_jobs = (
            "FROM video_jobs AS jobs JOIN publications AS p ON p.job_id = jobs.id "
            "LEFT JOIN channels ON channels.id = p.channel_id "
            "WHERE jobs.created_by='operator' AND jobs.deleted_at IS NULL"
        )
        awaiting = [dict(row) for row in db.execute(
            "SELECT jobs.id, jobs.subject, jobs.format, jobs.completed_at, channels.name AS channel_name "
            f"{operator_jobs} AND jobs.state='completed' AND EXISTS ("
            "SELECT 1 FROM generation_quality q WHERE q.item_id=jobs.generation_item_id AND q.job_id=jobs.id AND q.state='approved') "
            "ORDER BY jobs.completed_at DESC LIMIT 50"
        )]
        in_progress = [dict(row) for row in db.execute(
            "SELECT jobs.id, jobs.subject, jobs.format, jobs.state, jobs.error, jobs.created_at, channels.name AS channel_name "
            f"{operator_jobs} AND jobs.state IN ('queued', 'running', 'failed', 'interrupted') "
            "ORDER BY jobs.created_at DESC LIMIT 50"
        )]
    return {"operators": operators, "awaiting_approval": awaiting, "in_progress": in_progress}


@router.put("/api/agents/operators/{channel_id}")
def put_operator(channel_id: str, input: OperatorInput) -> dict[str, Any]:
    with database() as db:
        channel = channel_row_or_404(db, channel_id)
        if channel["status"] != "connected":
            raise HTTPException(status_code=409, detail="The channel is not connected. Reconnect it in Channels.")
        if channel["platform"] != "youtube" and input.format == "long":
            raise HTTPException(status_code=409, detail="TikTok recebe apenas Shorts.")
        if input.auto_publish and channel["platform"] != "youtube":
            raise HTTPException(status_code=422, detail="Automatic publishing is available on YouTube only.")
        if len(set(input.publish_days)) != len(input.publish_days) or any(day not in range(7) for day in input.publish_days):
            raise HTTPException(status_code=422, detail="Invalid schedule days.")
        if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", input.publish_time):
            raise HTTPException(status_code=422, detail="Invalid schedule time.")
        try:
            ZoneInfo(input.publish_timezone)
        except ZoneInfoNotFoundError:
            raise HTTPException(status_code=422, detail="Invalid time zone.") from None
        next_run = as_utc(input.next_run_at) if input.next_run_at else datetime.now(UTC) + timedelta(hours=input.cadence_hours)
        db.execute(
            "INSERT INTO operators(channel_id, enabled, format, cadence_hours, next_run_at, auto_publish, publish_days_json, publish_time, publish_timezone, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET enabled=excluded.enabled, format=excluded.format, cadence_hours=excluded.cadence_hours, next_run_at=excluded.next_run_at, auto_publish=excluded.auto_publish, publish_days_json=excluded.publish_days_json, publish_time=excluded.publish_time, publish_timezone=excluded.publish_timezone, paused_reason=NULL, updated_at=excluded.updated_at",
            (channel_id, int(input.enabled), input.format, input.cadence_hours, next_run.isoformat(), int(input.auto_publish), json.dumps(input.publish_days), input.publish_time, input.publish_timezone, now()),
        )
    return next(operator for operator in list_operators()["operators"] if operator["channel_id"] == channel_id)


@router.post("/api/agents/operators/{channel_id}/run")
async def run_operator(channel_id: str, scheduled: bool = False) -> dict[str, Any]:
    """Claim a channel run before calling providers so two workers cannot generate twice."""
    run_id = str(uuid4())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        operator = db.execute("SELECT * FROM operators WHERE channel_id=?", (channel_id,)).fetchone()
        if not operator:
            raise HTTPException(status_code=404, detail="Salve o operador deste canal antes de rodar.")
        channel = db.execute("SELECT * FROM channels WHERE id=? AND deleted_at IS NULL", (channel_id,)).fetchone()
        if scheduled and (not operator["enabled"] or operator["paused_reason"] or not operator["next_run_at"] or operator["next_run_at"] > now()):
            return {"job": None, "error": "The operator is not ready for the scheduled run."}
        busy = db.execute(
            "SELECT 1 FROM video_jobs AS jobs JOIN publications ON publications.job_id=jobs.id "
            "WHERE publications.channel_id=? AND jobs.created_by='operator' AND jobs.state IN ('queued','running') AND jobs.deleted_at IS NULL",
            (channel_id,),
        ).fetchone()
        pending_batch = db.execute("SELECT 1 FROM operator_runs WHERE channel_id=? AND state IN ('running','queued') LIMIT 1", (channel_id,)).fetchone()
        lease_active = operator["lease_until"] and operator["lease_until"] > now()
        if busy or pending_batch or lease_active:
            return {"job": None, "error": "Skipped: the operator's previous video is still being generated."}
        slot_key = operator["next_run_at"] if scheduled else f"manual:{run_id}"
        db.execute(
            "INSERT OR IGNORE INTO operator_runs(id, channel_id, slot_key, state, started_at) VALUES (?, ?, ?, 'running', ?)",
            (run_id, channel_id, slot_key, now()),
        )
        if not db.execute("SELECT 1 FROM operator_runs WHERE id=?", (run_id,)).fetchone():
            return {"job": None, "error": "This window has already run."}
        next_run = (datetime.now(UTC) + timedelta(hours=operator["cadence_hours"])).isoformat()
        db.execute(
            "UPDATE operators SET lease_owner=?, lease_until=?, next_run_at=CASE WHEN ? THEN ? ELSE next_run_at END, last_run_at=?, updated_at=? WHERE channel_id=?",
            (run_id, (datetime.now(UTC) + timedelta(hours=1)).isoformat(), int(scheduled), next_run, now(), now(), channel_id),
        )
    batch, error = None, None
    try:
        if not channel or channel["status"] != "connected":
            raise HTTPException(status_code=409, detail="The channel is not connected. Reconnect it in Channels.")
        from gateway.auto_generation import AutoGenerationInput, create_auto_batch
        format = operator["format"]
        project = get_or_create_project(PROJECT_NAMES[format], channel["language"])
        batch = create_auto_batch(project["id"], AutoGenerationInput(format=format, channel_ids=[channel_id], client_request_id=run_id))
    except Exception as exc:
        error = str(getattr(exc, "detail", exc))[:500] or "Operator failed"
    with database() as db:
        db.execute("UPDATE operator_runs SET state=?, batch_id=?, error=?, finished_at=? WHERE id=?",
                   ("failed" if error else "queued", batch["batch_id"] if batch else None, error, now() if error else None, run_id))
        if error:
            db.execute("UPDATE operators SET lease_owner=NULL, lease_until=NULL, last_error=?, updated_at=? WHERE channel_id=? AND lease_owner=?",
                       (error, now(), channel_id, run_id))
    if error and scheduled:
        runtime().pause_operator(channel_id, error)
    return {"job": None, "batch_id": batch["batch_id"] if batch else None, "error": error}



