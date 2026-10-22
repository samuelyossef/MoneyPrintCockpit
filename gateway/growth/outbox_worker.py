from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import psycopg
import redis


LOGGER = logging.getLogger("growth.outbox")


def publish_batch(connection: psycopg.Connection[Any], client: redis.Redis, limit: int = 50) -> int:
    with connection.transaction():
        rows = connection.execute(
            "SELECT id, event_id, topic, payload, attempts FROM outbox "
            "WHERE published_at IS NULL AND available_at <= now() ORDER BY id "
            "FOR UPDATE SKIP LOCKED LIMIT %s",
            (limit,),
        ).fetchall()
        for row in rows:
            outbox_id, event_id, topic, payload, attempts = row
            try:
                serialized = json.dumps(payload) if not isinstance(payload, str) else payload
                client.xadd("growth:events", {"event_id": str(event_id), "event_type": topic, "payload": serialized})
                client.xadd(f"growth:{topic}", {"event_id": str(event_id), "payload": serialized})
                connection.execute("UPDATE outbox SET published_at=now(), attempts=attempts+1 WHERE id=%s", (outbox_id,))
            except Exception as exc:
                delay = min(2 ** (attempts + 1), 300)
                connection.execute(
                    "UPDATE outbox SET attempts=attempts+1, available_at=now() + (%s * interval '1 second'), last_error=%s WHERE id=%s",
                    (delay, str(exc)[:500], outbox_id),
                )
                LOGGER.exception("Falha ao publicar evento %s", event_id)
        return len(rows)


def run() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    dsn = os.environ["GROWTH_DATABASE_URL"]
    client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    while True:
        try:
            with psycopg.connect(dsn) as connection:
                processed = publish_batch(connection, client)
            time.sleep(0.25 if processed else 1.0)
        except Exception:
            LOGGER.exception("Outbox temporariamente indisponível")
            time.sleep(5)


if __name__ == "__main__":
    run()
