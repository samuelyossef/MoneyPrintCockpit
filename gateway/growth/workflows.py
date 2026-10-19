from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .engines import OpportunityScoreInput, YPPGoalInput, calculate_opportunity_score, calculate_ypp_gap
    from .events import DomainEvent
    from .store import GrowthStore


@activity.defn
async def mark_workflow_started(value: dict[str, Any]) -> None:
    await asyncio.to_thread(
        GrowthStore().update_workflow,
        value["workflow_id"],
        state="running",
        current_step="planning",
    )


@activity.defn
async def calculate_growth_plan(value: dict[str, Any]) -> dict[str, Any]:
    ypp = calculate_ypp_gap(YPPGoalInput.model_validate(value["ypp_goal"]))
    opportunity = calculate_opportunity_score(OpportunityScoreInput.model_validate(value["opportunity"]))
    objective = "increase_watch_hours" if ypp.watch_hours.gap > 0 else "increase_subscribers"
    priority_format = "long" if ypp.watch_hours.gap > 0 else "short"
    return {
        "objective": objective,
        "priority_format": priority_format,
        "production_target": value.get("production_target", {"long": 1, "shorts": 3}),
        "ypp_gap": ypp.model_dump(mode="json"),
        "opportunity": opportunity.model_dump(mode="json"),
    }


@activity.defn
async def complete_growth_plan(value: dict[str, Any]) -> dict[str, Any]:
    store = GrowthStore()
    await asyncio.to_thread(
        store.update_workflow,
        value["workflow_id"],
        state="completed",
        current_step="planned",
        output=value["plan"],
    )
    event = DomainEvent.create(
        event_type="plan.completed",
        channel_id=value["channel_id"],
        workflow_id=value["workflow_id"],
        payload=value["plan"],
        correlation_id=value["correlation_id"],
        causation_id=value.get("causation_id"),
        idempotency_key=f"plan:{value['workflow_id']}:v1",
    )
    await asyncio.to_thread(store.record_event, event)
    return value["plan"]


@workflow.defn(name="growth-planning-v1")
class GrowthPlanningWorkflow:
    @workflow.run
    async def run(self, value: dict[str, Any]) -> dict[str, Any]:
        retry = RetryPolicy(maximum_attempts=5)
        await workflow.execute_activity(
            mark_workflow_started,
            value,
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=retry,
        )
        plan = await workflow.execute_activity(
            calculate_growth_plan,
            value,
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=retry,
        )
        return await workflow.execute_activity(
            complete_growth_plan,
            {**value, "plan": plan},
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=retry,
        )
