from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .engines import OpportunityScoreInput, YPPGoalInput, calculate_opportunity_score, calculate_ypp_gap
from .events import DomainEvent
from .store import GrowthStore, GrowthStoreUnavailable


router = APIRouter(prefix="/api/v1/growth", tags=["growth"])
store = GrowthStore()


class YPPGoalRequest(BaseModel):
    channel_id: UUID
    workflow_id: UUID | None = None
    metrics: YPPGoalInput


class OpportunityScoreRequest(BaseModel):
    channel_id: UUID
    topic: str = Field(min_length=3, max_length=500)
    workflow_id: UUID | None = None
    dimensions: OpportunityScoreInput


class PlanningWorkflowRequest(BaseModel):
    channel_id: UUID
    topic: str = Field(min_length=3, max_length=500)
    ypp_goal: YPPGoalInput
    opportunity: OpportunityScoreInput
    production_target: dict[str, int] = Field(default_factory=lambda: {"long": 1, "shorts": 3})
    publish_mode: str = Field(default="review", pattern=r"^(review|automatic)$")


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


@router.get("/health")
def growth_health() -> dict[str, Any]:
    try:
        return {"status": "ok", "postgres": store.ping(), "temporal": os.getenv("TEMPORAL_ADDRESS", "") != ""}
    except GrowthStoreUnavailable as exc:
        raise _unavailable(exc) from exc


@router.post("/goals/evaluate")
def evaluate_goal(input: YPPGoalRequest) -> dict[str, Any]:
    workflow_id = input.workflow_id or uuid4()
    result = calculate_ypp_gap(input.metrics)
    event = DomainEvent.create(
        event_type="goal.evaluated",
        channel_id=str(input.channel_id),
        workflow_id=str(workflow_id),
        payload=result.model_dump(mode="json"),
        correlation_id=str(workflow_id),
        causation_id=None,
        idempotency_key=f"goal:{workflow_id}:v1",
    )
    try:
        inserted = store.record_event(event)
    except GrowthStoreUnavailable as exc:
        raise _unavailable(exc) from exc
    return {"result": result, "event_id": event.event_id, "recorded": inserted}


@router.post("/opportunities/score")
def score_opportunity(input: OpportunityScoreRequest) -> dict[str, Any]:
    workflow_id = input.workflow_id or uuid4()
    result = calculate_opportunity_score(input.dimensions)
    payload = {"topic": input.topic, **result.model_dump(mode="json")}
    event = DomainEvent.create(
        event_type="opportunity.created",
        channel_id=str(input.channel_id),
        workflow_id=str(workflow_id),
        payload=payload,
        correlation_id=str(workflow_id),
        causation_id=None,
        idempotency_key=f"opportunity:{input.channel_id}:{input.topic.casefold()}:{result.version}",
    )
    try:
        inserted = store.record_event(event)
    except GrowthStoreUnavailable as exc:
        raise _unavailable(exc) from exc
    return {"result": result, "event_id": event.event_id, "recorded": inserted}


@router.post("/workflows", status_code=202)
async def start_planning_workflow(input: PlanningWorkflowRequest) -> dict[str, Any]:
    from temporalio.client import Client

    workflow_id = uuid4()
    temporal_id = f"growth-{workflow_id}"
    body = {
        "workflow_id": str(workflow_id),
        "channel_id": str(input.channel_id),
        "topic": input.topic,
        "ypp_goal": input.ypp_goal.model_dump(mode="json"),
        "opportunity": input.opportunity.model_dump(mode="json"),
        "production_target": input.production_target,
        "publish_mode": input.publish_mode,
        "correlation_id": str(workflow_id),
        "causation_id": None,
    }
    try:
        await asyncio.to_thread(
            store.create_workflow,
            workflow_id=workflow_id,
            channel_id=input.channel_id,
            workflow_type="growth-planning-v1",
            input_data=body,
        )
        client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233"))
        await client.start_workflow(
            "growth-planning-v1",
            body,
            id=temporal_id,
            task_queue=os.getenv("GROWTH_TASK_QUEUE", "growth-intelligence"),
        )
    except Exception as exc:
        try:
            await asyncio.to_thread(
                store.update_workflow,
                str(workflow_id),
                state="failed",
                current_step="start",
                error={"type": type(exc).__name__, "message": str(exc)[:500]},
            )
        except Exception:
            pass
        raise HTTPException(status_code=503, detail=f"Durable workflow is unavailable: {exc}") from exc
    return {"id": workflow_id, "temporal_workflow_id": temporal_id, "state": "planned"}


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: UUID) -> dict[str, Any]:
    try:
        value = store.workflow(workflow_id)
    except GrowthStoreUnavailable as exc:
        raise _unavailable(exc) from exc
    if not value:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return value
