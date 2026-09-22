from __future__ import annotations

import math

from pydantic import BaseModel, Field


class YPPGoalInput(BaseModel):
    """Qualified organic progress. Paid traffic must never be supplied here."""

    subscribers: int = Field(ge=0)
    subscriber_target: int = Field(gt=0)
    qualified_watch_hours: float = Field(ge=0)
    watch_hour_target: float = Field(gt=0)
    qualified_shorts_views: int = Field(ge=0)
    shorts_view_target: int = Field(gt=0)
    days_remaining: int = Field(gt=0)
    average_view_duration_minutes: float | None = Field(default=None, gt=0)


class GoalProgress(BaseModel):
    current: float
    target: float
    gap: float
    days_remaining: int
    required_daily_velocity: float


class YPPGoalResult(BaseModel):
    metric_scope: str = "qualified_organic"
    subscribers: GoalProgress
    watch_hours: GoalProgress
    shorts_views: GoalProgress
    required_qualified_long_views_per_day: int | None


def _progress(current: float, target: float, days: int) -> GoalProgress:
    gap = max(target - current, 0)
    return GoalProgress(
        current=current,
        target=target,
        gap=gap,
        days_remaining=days,
        required_daily_velocity=round(gap / days, 2),
    )


def calculate_ypp_gap(value: YPPGoalInput) -> YPPGoalResult:
    watch_hours = _progress(value.qualified_watch_hours, value.watch_hour_target, value.days_remaining)
    views = None
    if value.average_view_duration_minutes is not None:
        daily_watch_minutes = watch_hours.required_daily_velocity * 60
        views = math.ceil(daily_watch_minutes / value.average_view_duration_minutes)
    return YPPGoalResult(
        subscribers=_progress(value.subscribers, value.subscriber_target, value.days_remaining),
        watch_hours=watch_hours,
        shorts_views=_progress(value.qualified_shorts_views, value.shorts_view_target, value.days_remaining),
        required_qualified_long_views_per_day=views,
    )


class OpportunityScoreInput(BaseModel):
    demand: float = Field(ge=0, le=100)
    audience_fit: float = Field(ge=0, le=100)
    historical_retention: float = Field(ge=0, le=100)
    subscriber_conversion: float = Field(ge=0, le=100)
    expected_avd: float = Field(ge=0, le=100)
    evergreen: float = Field(ge=0, le=100)
    trend_velocity: float = Field(ge=0, le=100)
    monetization: float = Field(ge=0, le=100)
    stock_availability: float = Field(ge=0, le=100)
    competition: float = Field(ge=0, le=100)
    production_cost: float = Field(ge=0, le=100)
    saturation: float = Field(ge=0, le=100)


class OpportunityScoreResult(BaseModel):
    score: float
    version: str = "opportunity-v1"
    contributions: dict[str, float]


_OPPORTUNITY_WEIGHTS = {
    "demand": 0.15,
    "audience_fit": 0.14,
    "historical_retention": 0.13,
    "subscriber_conversion": 0.10,
    "expected_avd": 0.10,
    "evergreen": 0.07,
    "trend_velocity": 0.10,
    "monetization": 0.05,
    "stock_availability": 0.06,
    "competition": 0.04,
    "production_cost": 0.03,
    "saturation": 0.03,
}
_INVERTED_DIMENSIONS = {"competition", "production_cost", "saturation"}


def calculate_opportunity_score(value: OpportunityScoreInput) -> OpportunityScoreResult:
    contributions: dict[str, float] = {}
    raw = value.model_dump()
    for dimension, weight in _OPPORTUNITY_WEIGHTS.items():
        normalized = 100 - raw[dimension] if dimension in _INVERTED_DIMENSIONS else raw[dimension]
        contributions[dimension] = round(normalized * weight, 4)
    return OpportunityScoreResult(score=round(sum(contributions.values()), 2), contributions=contributions)
