from __future__ import annotations

import os
import time


class ObjectStorageUnavailable(RuntimeError):
    pass


def ensure_media_bucket(attempts: int = 10, delay_seconds: float = 0.5) -> str:
    endpoint = os.getenv("OBJECT_STORAGE_ENDPOINT", "")
    bucket = os.getenv("OBJECT_STORAGE_BUCKET", "moneyprinter-media")
    if not endpoint:
        raise ObjectStorageUnavailable("OBJECT_STORAGE_ENDPOINT is not configured.")
    try:
        from minio import Minio
    except ImportError as exc:
        raise ObjectStorageUnavailable("The S3/MinIO client is not installed.") from exc
    client = Minio(
        endpoint,
        access_key=os.environ.get("MINIO_ROOT_USER", ""),
        secret_key=os.environ.get("MINIO_ROOT_PASSWORD", ""),
        secure=os.getenv("OBJECT_STORAGE_SECURE", "0") == "1",
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            return bucket
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
    raise ObjectStorageUnavailable(f"Object storage is unavailable: {last_error}")
