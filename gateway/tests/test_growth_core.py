from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from pydantic import ValidationError

from gateway.growth.engines import OpportunityScoreInput, YPPGoalInput, calculate_opportunity_score, calculate_ypp_gap
from gateway.growth.events import DomainEvent
from gateway.growth.store import GrowthStore


class GrowthCoreTests(unittest.TestCase):
    def test_create_workflow_reads_columns_from_insert_result(self) -> None:
        workflow_id, channel_id = uuid4(), uuid4()
        cursor = MagicMock()
        cursor.fetchone.return_value = (workflow_id, "planned")
        cursor.description = [SimpleNamespace(name="id"), SimpleNamespace(name="state")]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value = cursor
        store = GrowthStore(dsn="postgresql://test")

        with patch.object(store, "_connect", return_value=connection):
            result = store.create_workflow(
                workflow_id=workflow_id, channel_id=channel_id,
                workflow_type="growth-planning-v1", input_data={"topic": "test"},
            )

        self.assertEqual(result, {"id": workflow_id, "state": "planned"})
        connection.execute.assert_called_once()

    def test_ypp_gap_uses_only_qualified_organic_metrics(self) -> None:
        result = calculate_ypp_gap(
            YPPGoalInput(
                subscribers=742,
                subscriber_target=1000,
                qualified_watch_hours=2860,
                watch_hour_target=4000,
                qualified_shorts_views=3_200_000,
                shorts_view_target=10_000_000,
                days_remaining=4,
                average_view_duration_minutes=6.8,
            )
        )

        self.assertEqual(result.subscribers.gap, 258)
        self.assertEqual(result.subscribers.required_daily_velocity, 64.5)
        self.assertEqual(result.watch_hours.gap, 1140)
        self.assertEqual(result.watch_hours.required_daily_velocity, 285)
        self.assertEqual(result.required_qualified_long_views_per_day, 2515)
        self.assertEqual(result.metric_scope, "qualified_organic")

    def test_ypp_gap_never_returns_negative_targets(self) -> None:
        result = calculate_ypp_gap(
            YPPGoalInput(
                subscribers=1200,
                subscriber_target=1000,
                qualified_watch_hours=4500,
                watch_hour_target=4000,
                qualified_shorts_views=12_000_000,
                shorts_view_target=10_000_000,
                days_remaining=2,
                average_view_duration_minutes=5,
            )
        )

        self.assertEqual(result.subscribers.gap, 0)
        self.assertEqual(result.watch_hours.gap, 0)
        self.assertEqual(result.required_qualified_long_views_per_day, 0)

    def test_opportunity_score_is_explainable_and_penalizes_cost_and_saturation(self) -> None:
        strong = OpportunityScoreInput(
            demand=92,
            audience_fit=90,
            historical_retention=84,
            subscriber_conversion=82,
            expected_avd=86,
            evergreen=75,
            trend_velocity=94,
            monetization=70,
            stock_availability=88,
            competition=55,
            production_cost=20,
            saturation=30,
        )
        weak = strong.model_copy(update={"production_cost": 95, "saturation": 95})

        strong_result = calculate_opportunity_score(strong)
        weak_result = calculate_opportunity_score(weak)

        self.assertGreater(strong_result.score, weak_result.score)
        self.assertAlmostEqual(sum(strong_result.contributions.values()), strong_result.score, places=1)
        self.assertEqual(strong_result.version, "opportunity-v1")

    def test_opportunity_dimensions_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            OpportunityScoreInput(
                demand=101,
                audience_fit=50,
                historical_retention=50,
                subscriber_conversion=50,
                expected_avd=50,
                evergreen=50,
                trend_velocity=50,
                monetization=50,
                stock_availability=50,
                competition=50,
                production_cost=50,
                saturation=50,
            )

    def test_domain_event_carries_traceable_versioned_contract(self) -> None:
        event = DomainEvent.create(
            event_type="opportunity.created",
            channel_id="channel-1",
            workflow_id="workflow-1",
            payload={"score": 87.4},
            correlation_id="correlation-1",
            causation_id="command-1",
            idempotency_key="score:channel-1:topic-1:v1",
        )

        self.assertTrue(event.event_id)
        self.assertEqual(event.schema_version, 1)
        self.assertEqual(event.timestamp.tzinfo, UTC)
        self.assertEqual(event.payload["score"], 87.4)

    def test_domain_event_rejects_naive_timestamp(self) -> None:
        with self.assertRaises(ValidationError):
            DomainEvent(
                event_id="event-1",
                event_type="topic.discovered",
                schema_version=1,
                channel_id="channel-1",
                workflow_id="workflow-1",
                timestamp=datetime.now(),
                payload={},
                correlation_id="correlation-1",
                causation_id=None,
                idempotency_key="topic:1",
            )


if __name__ == "__main__":
    unittest.main()
