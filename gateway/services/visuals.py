"""NVIDIA image generation and advisory visual review for completed video jobs."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException

from gateway import main as m

REVIEW_FRAMES = 5


def thumbnail_path(job_id: str) -> Path:
    try:
        safe_id = str(UUID(job_id))
    except ValueError:
        safe_id = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    return m.DATA_DIR / "thumbnails" / f"{safe_id}.jpg"


async def generate_image(prompt: str, width: int, height: int) -> bytes:
    key = m.config_value("nvidia_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure a chave NVIDIA em Configurações para gerar imagens.")
    model = m.config_value("nvidia_image_model") or m.NVIDIA_DEFAULT_IMAGE_MODEL
    schnell = model.endswith("flux.1-schnell")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120)) as client:
            response = await m.send_with_retry(
                client, "POST", f"{m.NVIDIA_GENAI_BASE_URL}/{model}",
                headers={"Authorization": f"Bearer {key}"},
                json={"prompt": prompt[:8000], "width": width, "height": height, "steps": 4 if schnell else 30, "cfg_scale": 0 if schnell else 3.5},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"NVIDIA indisponível: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"NVIDIA respondeu com erro {response.status_code}.")
    try:
        artifact = response.json()["artifacts"][0]
        if artifact.get("finishReason") != "SUCCESS":
            raise HTTPException(status_code=502, detail=f"A NVIDIA recusou a imagem: {artifact.get('finishReason')}.")
        image = base64.b64decode(artifact["base64"], validate=True)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="A NVIDIA devolveu uma imagem inválida.") from exc
    if not image or len(image) > 20 * 1024 * 1024:
        raise HTTPException(status_code=502, detail="A NVIDIA devolveu uma imagem vazia ou acima de 20 MB.")
    return image


async def ai_image_materials(subject: str, script: str, format: str, clip_seconds: float, voice_rate: float, visual_style: str) -> tuple[list[dict[str, Any]], int]:
    seconds = m.narration_seconds(script, voice_rate)
    count = max(1, min(math.ceil(seconds / max(clip_seconds, 1)), m.MAX_VISUAL_TERMS))
    clip = max(1, math.ceil(seconds / count))
    chunks = m.split_script(script, count) or [subject]
    numbered = "\n".join(f"{index + 1}. {chunk}" for index, chunk in enumerate(chunks))
    result = await m.llm_json_object(
        f"Tema: {subject}\nTrechos da narração, em ordem:\n{numbered}\n"
        "Defina estilo visual único em inglês e uma cena concreta em inglês para cada trecho. "
        "Sem texto, letras ou marcas na imagem. "
        + (f"Estilo obrigatório do canal: {visual_style[:1000]}. " if visual_style else "")
        + f'Responda JSON: {{"style":"...","scenes":[...]}}, com exatamente {len(chunks)} cenas na mesma ordem.',
        label="as cenas das imagens de IA",
    )
    style = str(result.get("style") or "cinematic digital illustration, rich colors")[:500]
    raw_scenes = result.get("scenes")
    if not isinstance(raw_scenes, list) or len(raw_scenes) != len(chunks) or any(not isinstance(scene, str) or not scene.strip() for scene in raw_scenes):
        raise HTTPException(status_code=502, detail="A IA não devolveu cenas válidas para as imagens.")
    scenes = [scene.strip()[:1000] for scene in raw_scenes]
    width, height = (768, 1344) if format == "short" else (1344, 768)
    batch_id = uuid4().hex
    semaphore = asyncio.Semaphore(4)

    async def render(index: int, scene: str) -> str | None:
        async with semaphore:
            for _ in range(2):
                try:
                    image = await generate_image(f"{style}. {scene}. No text, no letters.", width, height)
                    response = await m.turbo_request(
                        "POST", "/api/v1/video_materials",
                        files={"file": (f"{batch_id}-{index:03}.jpg", image, "image/jpeg")},
                    )
                    file = m.unwrap(response.json())
                    if isinstance(file, dict) and file.get("file"):
                        return str(file["file"])
                except (HTTPException, ValueError, TypeError) as exc:
                    m.logger.warning("Imagem de IA %s falhou: %s", index, getattr(exc, "detail", type(exc).__name__))
            return None

    files = await asyncio.gather(*(render(index, scene) for index, scene in enumerate(scenes)))
    good = [index for index, item in enumerate(files) if item]
    if not good:
        raise HTTPException(status_code=502, detail="Nenhuma imagem pôde ser gerada. Tente de novo ou escolha filmagens de banco.")
    filled = [files[index] or files[min(good, key=lambda candidate: abs(candidate - index))] for index in range(len(files))]
    return ([{"provider": "local", "url": item, "duration": clip} for item in filled], clip)


async def nvidia_vision(content: list[dict[str, Any]]) -> str:
    key = m.config_value("nvidia_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure a chave NVIDIA em Configurações para revisar imagens.")
    body = {
        "model": m.config_value("nvidia_vision_model") or m.NVIDIA_DEFAULT_VISION_MODEL,
        "messages": [{"role": "user", "content": content}], "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=180)) as client:
            response = await m.send_with_retry(client, "POST", f"{m.NVIDIA_BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="NVIDIA indisponível para revisão visual.") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"NVIDIA respondeu com erro {response.status_code}.")
    try:
        return str(response.json()["choices"][0]["message"].get("content") or "")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="A NVIDIA devolveu uma resposta inválida.") from exc


def _extract_frames(video_path: Path, duration: float, folder: Path) -> list[Path]:
    frames = []
    for index in range(REVIEW_FRAMES):
        frame = folder / f"frame-{index}.jpg"
        timestamp = duration * (index + 1) / (REVIEW_FRAMES + 1)
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.2f}", "-i", str(video_path), "-frames:v", "1", "-vf", "scale=512:-2", "-y", str(frame)],
            capture_output=True, timeout=60, check=False,
        )
        if result.returncode == 0 and frame.is_file() and frame.stat().st_size:
            frames.append(frame)
    return frames


async def review_job(job_id: str) -> dict[str, Any]:
    if not m.config_value("nvidia_api_key"):
        raise HTTPException(status_code=409, detail="Configure a chave NVIDIA em Configurações para revisar imagens.")
    with m.database() as db:
        job = m.job_row_or_404(db, job_id)
        artifact = db.execute("SELECT * FROM artifacts WHERE job_id=? AND kind='video' ORDER BY created_at LIMIT 1", (job_id,)).fetchone()
    if job["state"] not in m.METADATA_STATES or not artifact:
        raise HTTPException(status_code=409, detail="A revisão visual exige um vídeo concluído.")
    turbo = json.loads(job["turbo_payload_json"])
    script = str(job["script"] or turbo.get("video_script") or "")
    duration = max(1, float(turbo.get("audio_duration") or m.narration_seconds(script)))
    with tempfile.TemporaryDirectory() as folder:
        temp = Path(folder)
        video = temp / "video.mp4"
        await m.download_turbo_file(artifact["remote_path"], video)
        frames = await asyncio.to_thread(_extract_frames, video, duration, temp)
        if not frames:
            raise HTTPException(status_code=502, detail="Não foi possível extrair imagens do vídeo para revisão.")
        chunks = m.split_script(script, len(frames)) if script.strip() else []
        content: list[dict[str, Any]] = [{"type": "text", "text": 'Compare cada quadro ao trecho narrado e responda apenas JSON: {"frames":[{"index":0,"matches":true,"reason":"..."}],"summary":"..."}.'}]
        for index, frame in enumerate(frames):
            content.extend([
                {"type": "text", "text": f"Quadro {index}; narração: {chunks[index]}" if index < len(chunks) else f"Quadro {index}"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(frame.read_bytes()).decode('ascii')}"}},
            ])
        parsed = m.parse_json_object(await nvidia_vision(content))
    normalized = []
    for item in parsed.get("frames", []):
        if isinstance(item, dict) and isinstance(item.get("index"), int) and 0 <= item["index"] < len(frames):
            normalized.append({"index": item["index"], "matches": bool(item.get("matches")), "reason": str(item.get("reason") or "")[:300]})
    value = {"frames": normalized, "summary": str(parsed.get("summary") or "")[:1000]}
    with m.database() as db:
        db.execute("INSERT INTO job_reviews(job_id,value_json,created_at) VALUES (?,?,?) ON CONFLICT(job_id) DO UPDATE SET value_json=excluded.value_json,created_at=excluded.created_at", (job_id, json.dumps(value), m.now()))
        return m.row_as_job(m.job_row_or_404(db, job_id), db)
