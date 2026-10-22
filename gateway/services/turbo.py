"""HTTP client for MoneyPrinterTurbo (retry, state mapping, downloads)."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Literal
import httpx
from fastapi import FastAPI, HTTPException, Request, Response

from gateway import main as m


def api_key() -> str:
    if m.KEY_FILE and Path(m.KEY_FILE).is_file():
        return Path(m.KEY_FILE).read_text(encoding="utf-8").strip()
    return os.getenv("MPT_API_KEY", "")


def turbo_headers() -> dict[str, str]:
    key = m.api_key()
    return {"x-api-key": key} if key else {}


def is_retryable(method: str, status: int | None = None, exc: Exception | None = None) -> bool:
    """Only repeats a request that can't double-apply. A connect failure never reached the
    server and a 429 was refused, so both are safe for any method. A read timeout or 5xx may
    already have been processed (a repeated POST /api/v1/videos would start a second render),
    so those are retried only for idempotent methods."""
    idempotent = method.upper() in {"GET", "HEAD", "PUT", "DELETE"}
    if exc is not None:
        return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) or (idempotent and isinstance(exc, httpx.TransportError))
    return status == 429 or (idempotent and status is not None and (status in {408, 425} or status >= 500))


async def send_with_retry(client: httpx.AsyncClient, method: str, url: str, retries: int = 2, **kwargs: Any) -> httpx.Response:
    for attempt in range(retries + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if attempt == retries or not m.is_retryable(method, exc=exc):
                raise
        else:
            if attempt == retries or not m.is_retryable(method, status=response.status_code):
                return response
        await asyncio.sleep(m.RETRY_BASE_SECONDS * 2 ** attempt)
    raise AssertionError("unreachable")


async def turbo_request(method: str, path: str, retries: int = 2, **kwargs: Any) -> httpx.Response:
    headers = {**m.turbo_headers(), **kwargs.pop("headers", {})}
    started = time.perf_counter()
    status_code = 503
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=90)) as client:
            response = await m.send_with_retry(client, method, f"{m.TURBO_BASE_URL}{path}", retries=retries, headers=headers, **kwargs)
            status_code = response.status_code
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"MoneyPrinterTurbo indisponível: {exc}") from exc
    finally:
        trace = m.ACTIVE_TRACE.get()
        if trace is not None:
            trace["spans"].append({
                "name": f"Turbo {method.upper()} {path}",
                "service": "moneyprinterturbo",
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "status_code": status_code,
            })
    if response.status_code >= 400:
        detail = response.text[:500]
        raise HTTPException(status_code=502, detail=f"MoneyPrinterTurbo respondeu {response.status_code}: {detail}")
    return response


def unwrap(payload: Any) -> Any:
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def map_turbo_state(payload: dict[str, Any]) -> tuple[str, int, str | None, str | None]:
    raw = str(payload.get("state", "")).lower()
    progress = max(0, min(100, int(float(payload.get("progress", 0) or 0))))
    error = payload.get("error") or None
    stage = payload.get("failed_stage") or None
    if error or raw in {"-1", "failed", "failure", "error"}:
        return "failed", progress, stage, str(error or "Falha no Turbo")
    if raw in {"1", "completed", "complete", "success", "done", "finished"} or payload.get("videos") or payload.get("combined_videos"):
        return "completed", 100, None, None
    if raw in {"queued", "pending", "waiting"}:
        return "queued", progress, None, None
    # MoneyPrinterTurbo v1.3.7 uses state=4 for active processing.
    if raw in {"4", "processing", "running", "rendering"}:
        return "running", progress, None, None
    return "running", progress, None, None


def turbo_relative_path(uri: str) -> str:
    """Turbo's GET /api/v1/tasks/{id} wraps videos/combined_videos through
    _task_file_to_uri: an absolute "{endpoint}/tasks/<rel>" URL if `endpoint` is
    configured, otherwise a root-relative "/tasks/<rel>" path (confirmed against
    MoneyPrinterTurbo v1.3.7 source and its own schema example). Its stream/download
    routes instead take the bare "<rel>" (task_id/filename.mp4) relative to the
    tasks dir - passing the wrapped form back in gives a 404 on every download."""
    marker = "/tasks/"
    index = uri.find(marker)
    return uri[index + len(marker):] if index != -1 else uri.lstrip("/")


async def download_turbo_file(remote_path: str, target: Path) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=None)) as client:
        async with client.stream("GET", f"{m.TURBO_BASE_URL}/api/v1/download/{remote_path}", headers=m.turbo_headers()) as response:
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail="O Turbo não encontrou o artefato")
            with target.open("wb") as file:
                async for chunk in response.aiter_bytes():
                    file.write(chunk)
