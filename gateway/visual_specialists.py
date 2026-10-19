"""Stock-media curation and independent editorial/final-video checks for auto jobs."""
from __future__ import annotations

import base64
import asyncio
import json
import math
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException


ALLOWED_MEDIA_HOSTS = ("pexels.com", "pixabay.com", "coverr.co", "coverr.com", "videos.pexels.com", "cdn.coverr.co")
MAX_DOWNLOAD_BYTES = 120 * 1024 * 1024


def main_module():
    from gateway import main
    return main


def material_root(item_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", item_id):
        raise HTTPException(status_code=422, detail="Identificador de geração inválido.")
    return main_module().DATA_DIR / "curated_materials" / item_id


def turbo_material_url(item_id: str, filename: str) -> str:
    if not re.fullmatch(r"[a-z0-9_-]+\.(mp4|jpg|jpeg|png|webp)", filename):
        raise HTTPException(status_code=422, detail="Nome de material inválido.")
    return f"/MoneyPrinterTurbo/storage/local_videos/curated/{item_id}/{filename}"


def _key(name: str) -> str:
    path = main_module().DATA_DIR / "moneyprinter" / "config.toml"
    if not path.is_file():
        return ""
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    app = config.get("app")
    value = app.get(name) if isinstance(app, dict) else None
    if value is None:
        value = config.get(name)
    return str(value[0]) if isinstance(value, list) and value else str(value or "") if not isinstance(value, list) else ""


def _safe_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == domain or host.endswith("." + domain) for domain in ALLOWED_MEDIA_HOSTS)


async def _get_json(url: str, *, params: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, dict) else {}


def _candidate(provider: str, kind: str, url: str, page: str, asset_id: Any, width: int, height: int, duration: float, query: str) -> dict[str, Any] | None:
    if not _safe_url(url) or width < 500 or height < 500:
        return None
    return {"id": f"{provider}-{kind}-{asset_id}", "provider": provider, "kind": kind,
            "url": url, "source_page": page if _safe_url(page) else "", "asset_id": str(asset_id),
            "width": width, "height": height, "duration": duration, "query": query}


def rendition_priority(rendition: dict[str, Any]) -> tuple[bool, int]:
    pixels = int(rendition.get("width") or 0) * int(rendition.get("height") or 0)
    target = 1920 * 1080
    return pixels < target, abs(pixels - target)


async def search_stock(query: str, aspect: str, clip_seconds: float) -> list[dict[str, Any]]:
    """Use current stock-bank keys; only public media URLs enter the download path."""
    if not any(_key(name) for name in ("pexels_api_keys", "pixabay_api_keys", "coverr_api_keys")):
        raise HTTPException(status_code=409, detail="Configure uma chave de Pexels, Pixabay ou Coverr para a curadoria visual.")
    found: list[dict[str, Any]] = []
    orientation = "portrait" if aspect == "9:16" else "landscape" if aspect == "16:9" else "square"
    pexels = _key("pexels_api_keys")
    if pexels:
        headers = {"Authorization": pexels}
        try:
            data = await _get_json("https://api.pexels.com/v1/videos/search", params={"query": query, "per_page": 8, "orientation": orientation}, headers=headers)
            for video in data.get("videos", []):
                if float(video.get("duration") or 0) < clip_seconds:
                    continue
                for rendition in sorted(video.get("video_files") or [], key=rendition_priority):
                    candidate = _candidate("pexels", "video", str(rendition.get("link") or ""), str(video.get("url") or ""), video.get("id"), int(rendition.get("width") or 0), int(rendition.get("height") or 0), float(video.get("duration") or 0), query)
                    if candidate and aspect_matches(candidate, aspect):
                        found.append(candidate)
                        break
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        try:
            data = await _get_json("https://api.pexels.com/v1/search", params={"query": query, "per_page": 8, "orientation": orientation}, headers=headers)
            for photo in data.get("photos", []):
                candidate = _candidate("pexels", "image", str((photo.get("src") or {}).get("large2x") or ""), str(photo.get("url") or ""), photo.get("id"), int(photo.get("width") or 0), int(photo.get("height") or 0), clip_seconds, query)
                if candidate and aspect_matches(candidate, aspect):
                    found.append(candidate)
        except (httpx.HTTPError, ValueError, TypeError):
            pass
    pixabay = _key("pixabay_api_keys")
    if pixabay:
        try:
            data = await _get_json("https://pixabay.com/api/videos/", params={"key": pixabay, "q": query, "per_page": 8})
            for video in data.get("hits", []):
                if float(video.get("duration") or 0) < clip_seconds:
                    continue
                for rendition in sorted((video.get("videos") or {}).values(), key=rendition_priority):
                    candidate = _candidate("pixabay", "video", str(rendition.get("url") or ""), str(video.get("pageURL") or ""), video.get("id"), int(rendition.get("width") or 0), int(rendition.get("height") or 0), float(video.get("duration") or 0), query)
                    if candidate and aspect_matches(candidate, aspect):
                        found.append(candidate)
                        break
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        try:
            data = await _get_json("https://pixabay.com/api/", params={"key": pixabay, "q": query, "image_type": "photo", "per_page": 8})
            for photo in data.get("hits", []):
                candidate = _candidate("pixabay", "image", str(photo.get("largeImageURL") or ""), str(photo.get("pageURL") or ""), photo.get("id"), int(photo.get("imageWidth") or 0), int(photo.get("imageHeight") or 0), clip_seconds, query)
                if candidate and aspect_matches(candidate, aspect):
                    found.append(candidate)
        except (httpx.HTTPError, ValueError, TypeError):
            pass
    coverr = _key("coverr_api_keys")
    if coverr:
        try:
            data = await _get_json("https://api.coverr.co/videos", params={"query": query, "page_size": 8, "urls": "true", "filter": "is_vertical:true" if aspect == "9:16" else "is_vertical:false"}, headers={"Authorization": f"Bearer {coverr}"})
            for video in data.get("hits", []):
                duration = float(video.get("duration") or 0)
                if duration < clip_seconds:
                    continue
                candidate = _candidate("coverr", "video", str((video.get("urls") or {}).get("mp4_download") or ""), str(video.get("canonical_url") or ""), video.get("id"), int(video.get("max_width") or 0), int(video.get("max_height") or 0), duration, query)
                if candidate and aspect_matches(candidate, aspect):
                    found.append(candidate)
        except (httpx.HTTPError, ValueError, TypeError):
            pass
    return found


def aspect_matches(candidate: dict[str, Any], aspect: str) -> bool:
    ratio = candidate["width"] / candidate["height"]
    return (ratio <= 0.9 if aspect == "9:16" else ratio >= 1.1 if aspect == "16:9" else 0.75 <= ratio <= 1.33)


async def download_candidate(candidate: dict[str, Any], item_id: str, scene_index: int, alternative_index: int) -> Path:
    url = candidate["url"]
    if not _safe_url(url):
        raise HTTPException(status_code=502, detail="O banco retornou um endereço de mídia inválido.")
    extension = "jpg" if candidate["kind"] == "image" else "mp4"
    target = material_root(item_id) / f"scene{scene_index:03d}option{alternative_index}.{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), follow_redirects=False) as client:
        for _ in range(3):
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers.get("location", ""))
                    if not _safe_url(url):
                        raise HTTPException(status_code=502, detail="Redirecionamento de mídia fora dos bancos permitidos.")
                    continue
                response.raise_for_status()
                size = 0
                with target.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            target.unlink(missing_ok=True)
                            raise HTTPException(status_code=502, detail="Material visual excede o tamanho permitido.")
                        output.write(chunk)
                break
        else:
            raise HTTPException(status_code=502, detail="O banco redirecionou o material muitas vezes.")
    if size < 1024:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail="Material visual vazio.")
    return target


def probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=True)
    data = json.loads(result.stdout)
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("Arquivo sem imagem")
    audio = any(stream.get("codec_type") == "audio" for stream in data.get("streams", []))
    return {"width": int(video.get("width") or 0), "height": int(video.get("height") or 0), "duration": float(data.get("format", {}).get("duration") or 0), "audio": audio}


def sample_frame(path: Path, second: float = 0) -> bytes:
    seek = [] if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ["-ss", str(max(0, second))]
    result = subprocess.run(["ffmpeg", "-v", "error", *seek, "-i", str(path), "-frames:v", "1", "-vf", "scale=640:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"], capture_output=True, timeout=45, check=True)
    if not result.stdout:
        raise ValueError("Quadro vazio")
    return result.stdout


def detect_black_segments(path: Path) -> list[str]:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=1.0:pix_th=0.08", "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=900, check=False)
    if result.returncode:
        raise ValueError("Não foi possível analisar quadros pretos no vídeo final.")
    return re.findall(r"black_start:[^\r\n]+", result.stderr)


def inspect_audio(path: Path) -> dict[str, Any]:
    """Measure the rendered soundtrack; do not infer audio quality from a video frame."""
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af",
        "silencedetect=noise=-45dB:d=3,volumedetect", "-f", "null", "-",
    ], capture_output=True, text=True, timeout=900, check=False)
    if result.returncode:
        raise ValueError("Não foi possível medir o áudio do vídeo final.")
    peak = re.search(r"max_volume:\s*(-?[\d.]+|[-+]?inf) dB", result.stderr)
    if not peak:
        raise ValueError("A análise não retornou o volume do áudio.")
    peak_db = float(peak.group(1))
    silences = [float(value) for value in re.findall(r"silence_duration:\s*([\d.]+)", result.stderr)]
    longest = max(silences, default=0)
    return {"audible": math.isfinite(peak_db) and peak_db > -40,
            "peak_db": round(peak_db, 1) if math.isfinite(peak_db) else None,
            "longest_silence_seconds": round(longest, 2), "continuous": longest <= 4}


def vision_model() -> tuple[str, str, str]:
    m = main_module()
    provider, base_url, key, model = m.llm_config()
    if not key:
        raise HTTPException(status_code=409, detail="Configure a chave do provedor de IA selecionado antes da curadoria visual.")
    if provider == "nvidia":
        capable = any(entry["id"] == model and entry["capabilities"].get("image") for entry in m.NVIDIA_MODELS)
    else:
        capable = "vision" in model.lower() or "kimi-k2.5" in model.lower()
    if not capable:
        raise HTTPException(status_code=409, detail=f"O modelo configurado ({model}) não declara suporte a imagens. Selecione um modelo com visão no mesmo provedor.")
    return base_url, key, model


async def judge_frame(frame: bytes, prompt: str) -> dict[str, Any]:
    m = main_module()
    base_url, key, model = vision_model()
    body = {"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt + '\nResponda apenas JSON: {"approved": true/false, "score": 0-100, "reason": "motivo observável"}.'},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(frame).decode("ascii")}},
    ]}], "max_tokens": 300}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120)) as client:
            for attempt in range(3):
                try:
                    response = await client.post(f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)
                    response.raise_for_status()
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                        raise
                    retry_after = exc.response.headers.get("Retry-After", "")
                    delay = min(20.0, float(retry_after)) if retry_after.replace(".", "", 1).isdigit() else 2.0 * (attempt + 1)
                    await asyncio.sleep(delay)
                except httpx.TransportError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2.0 * (attempt + 1))
        content = response.json()["choices"][0]["message"]["content"]
        verdict = m.parse_json_object(content)
        if type(verdict.get("approved")) is not bool or type(verdict.get("score")) not in (int, float) or not 0 <= verdict["score"] <= 100 or not isinstance(verdict.get("reason"), str):
            raise ValueError("Resposta de visão incompleta")
        return {"approved": bool(verdict["approved"]) and float(verdict["score"]) >= 70, "score": float(verdict["score"]), "reason": verdict["reason"][:500]}
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        status = f" HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else ""
        raise HTTPException(status_code=502, detail=f"O modelo de visão configurado não avaliou a imagem: {type(exc).__name__}{status}.") from exc


async def review_script(script: str, subject: str, requirements: str, context: dict[str, Any], *, manual: bool) -> dict[str, Any]:
    m = main_module()
    from gateway.editorial_quality import narration_diagnostics, writing_brief
    from gateway.auto_generation import _duration_bounds
    seconds = m.narration_seconds(script, float(context["settings"]["voice_rate"]),
                                  str(context["settings"].get("voice_name") or ""))
    minimum, maximum = _duration_bounds(context)
    if not minimum <= seconds <= maximum:
        return {
            "approved": False,
            "reason": f"A narração estimada ({seconds:.0f}s) está fora da duração selecionada ({minimum:.0f}-{maximum:.0f}s).",
            "estimated_seconds": round(seconds), "manual": manual,
            "narration": narration_diagnostics(script),
        }
    prompt = (f"Atue como revisor editorial independente. Tema: {subject}. Idioma: {context['settings']['video_language']}. "
              f"Canal/nicho: {context['channel']['name']} / {context['channel']['niche']}. Requisitos: {requirements}. "
              f"Instruções editoriais: {context['editorial']}. Guia visual: {context.get('visual_guide', '')}. "
              f"Duração estimada: {seconds:.0f}s; faixa permitida: {minimum:.0f}-{maximum:.0f}s. "
              "Verifique primeiro se o conteúdo inteiro responde ao tema solicitado; reprove se o roteiro tratar de outro assunto, "
              "mesmo que seja bem escrito. Depois verifique idioma, instruções, abertura, progressão, conclusão e coerência. "
              "Revise cada frase quanto a ortografia, acentuação, gramática, concordância e pontuação no idioma indicado. "
              "Não aprove erros linguísticos; indique no motivo o trecho e a correção necessária para que o roteiro seja refeito. "
              + writing_brief(context["format"]) + " "
              "Não reescreva texto manual. Responda JSON com approved boolean e reason string. Roteiro:\n" + script[:40000])
    result = await m.llm_json_object(prompt, task="json", max_tokens=450, label="revisão editorial")
    approved = result.get("approved") is True
    reason = str(result.get("reason") or "Revisão editorial sem justificativa.")[:600]
    return {"approved": approved, "reason": reason, "estimated_seconds": round(seconds), "manual": manual,
            "narration": narration_diagnostics(script)}


async def proofread_script(script: str, language: str) -> dict[str, Any]:
    """Correct only language errors before this exact text feeds TTS and subtitles."""
    result = await main_module().llm_json_object(
        f"Atue como revisor ortográfico e gramatical. Revise este roteiro no idioma {language}. "
        "Corrija ortografia, acentuação, gramática, concordância e pontuação. Preserve integralmente o sentido, "
        "os fatos, a ordem, o tom, o tamanho aproximado e as quebras de parágrafo; não acrescente, resuma ou reescreva ideias. "
        "Use formas naturais e inequívocas para a voz e para a legenda. Se já estiver correto, devolva o texto sem alterações. "
        'Responda JSON: {"script": "roteiro integral revisado", "corrections": ["descrições curtas"]}.\nRoteiro:\n'
        + script[:40000],
        task="json", max_tokens=4096, label="revisão ortográfica",
    )
    corrected = str(result.get("script") or "").strip()
    if not corrected:
        raise HTTPException(status_code=502, detail="O agente ortográfico não devolveu o roteiro revisado.")
    corrections = result.get("corrections")
    return {"script": corrected, "changed": corrected != script.strip(),
            "corrections": [str(value)[:200] for value in corrections[:10]] if isinstance(corrections, list) else []}


def choose_scene_material(scene: dict[str, Any], used: set[str] | None = None) -> None:
    """Prefer relevance, then motion and variety; rejected assets never enter the ranking."""
    approved = [candidate for candidate in scene["candidates"] if candidate.get("status") == "approved"]
    if approved:
        selected = max(approved, key=lambda candidate: (
            float(candidate.get("score", 0)) + (3 if candidate.get("kind") == "video" else 0)
            - (12 if candidate["id"] in (used or set()) else 0)
        ))
        scene["selected"] = selected["id"]
        discarded = [
            {"id": candidate["id"], "reason": candidate.get("reason") or ("pontuação menor ou clipe repetido" if candidate.get("status") == "approved" else "rejeitado na curadoria")}
            for candidate in scene["candidates"] if candidate["id"] != selected["id"]
        ][:5]
        motion = "movimento de vídeo" if selected.get("kind") == "video" else "imagem estática"
        provider = selected.get("provider") or "banco de stock"
        scene["selection"] = {
            "chosen_id": selected["id"], "score": selected.get("score"), "kind": selected.get("kind"),
            "provider": provider, "cost": "gratuito", "discarded": discarded,
        }
        scene["selection_reason"] = (
            f"Relevância {float(selected.get('score') or 0):.0f}, {motion}, stock gratuito ({provider}). "
            + ("Descartados: " + ", ".join(f"{item['id']} ({item['reason']})" for item in discarded[:3]) if discarded else "Nenhuma alternativa restante.")
        )
        scene["selection"]["reason"] = scene["selection_reason"]


async def curate_scene(item_id: str, index: int, chunk: str, query: str, context: dict[str, Any],
                       excluded_ids: set[str] | None = None) -> dict[str, Any]:
    settings = context["settings"]
    aspect, clip_seconds = settings["video_aspect"], float(settings["video_clip_duration"])
    scene = {"index": index, "narration": chunk, "query": query, "expected": chunk, "candidates": [], "selected": None}
    seen = set(excluded_ids or set())
    for search_index in range(2):
        if search_index:
            try:
                replacement = await main_module().llm_json_object(
                    f"Tema do vídeo: {context.get('subject', '')}. Narração do corte: {chunk}. "
                    f"A busca anterior '{query}' só encontrou cenas rejeitadas. "
                    "Escreva uma busca visual alternativa em inglês, com 2 a 8 palavras, "
                    "que represente diretamente a narração. Sem conceitos abstratos nem ações alheias ao trecho. "
                    'Responda apenas JSON: {"query": "visual stock search"}.',
                    task="json", max_tokens=128, label="busca visual alternativa",
                )
                replacement = str(replacement.get("query") or "").strip()
                if not replacement or len(replacement) > 120 or replacement.casefold() == query.casefold():
                    break
                scene["query"] = replacement
            except (HTTPException, ValueError, TypeError) as exc:
                scene["search_repair_error"] = str(getattr(exc, "detail", exc))[:300]
                break
        candidates = await search_stock(scene["query"], aspect, clip_seconds)
        candidates.sort(key=lambda candidate: candidate.get("kind") != "video")
        considered = 0
        for candidate in candidates:
            if candidate["id"] in seen or considered >= 5:
                continue
            considered += 1
            seen.add(candidate["id"])
            record = {key: value for key, value in candidate.items() if key != "url"}
            record["status"] = "rejected"
            try:
                path = await download_candidate(candidate, item_id, index, len(scene["candidates"]))
                info = await asyncio.to_thread(probe, path)
                if info["width"] < 500 or info["height"] < 500 or not aspect_matches(info, aspect) or (candidate["kind"] == "video" and info["duration"] < clip_seconds):
                    record["reason"] = "Resolução, proporção ou duração insuficiente."
                else:
                    prompt = (f"Avalie o quadro REAL de um material de banco para um vídeo sobre {context['channel']['niche']}. "
                              f"Tema: {context.get('subject', '')}. Narração deste corte: {chunk}. "
                              f"Estilo visual e elementos proibidos: {context.get('visual_guide', '')}. "
                              "Aprove somente se o conteúdo visível corresponder ao assunto concreto deste trecho e ao estilo. "
                              "Rejeite coincidências superficiais, marcas indesejadas e cenas fora do tema.")
                    frame = await asyncio.to_thread(sample_frame, path, info["duration"] / 2 if candidate["kind"] == "video" else 0)
                    verdict = await judge_frame(frame, prompt)
                    record.update(verdict)
                    record["status"] = "approved" if verdict["approved"] else "rejected"
                    record["filename"] = path.name
                    if record["status"] == "approved" and scene["selected"] is None:
                        scene["selected"] = record["id"]
            except (HTTPException, OSError, ValueError, subprocess.SubprocessError) as exc:
                record["reason"] = str(getattr(exc, "detail", exc))[:500]
            scene["candidates"].append(record)
            if scene["selected"] and considered >= 3:
                break
        if scene["selected"]:
            break
    choose_scene_material(scene)
    return scene


def approved_materials(item_id: str, scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for scene in scenes:
        selected = next((candidate for candidate in scene["candidates"] if candidate["id"] == scene.get("selected") and candidate.get("status") == "approved"), None)
        if not selected:
            raise HTTPException(status_code=409, detail=f"O trecho {scene['index'] + 1} está sem material visual aprovado.")
        filename = selected.get("filename")
        if not filename or not (material_root(item_id) / filename).is_file():
            raise HTTPException(status_code=409, detail=f"O material aprovado do trecho {scene['index'] + 1} não está disponível.")
        result.append({"provider": "local", "url": turbo_material_url(item_id, filename), "duration": math.ceil(float(selected["duration"]))})
    return result


async def inspect_final_video(path: Path, scenes: list[dict[str, Any]], settings: dict[str, Any], script: str,
                              duration_bounds: tuple[float, float] | None = None) -> dict[str, Any]:
    if not scenes:
        raise ValueError("Não há cenas aprovadas para comparar com a montagem.")
    info = await asyncio.to_thread(probe, path)
    expected = main_module().narration_seconds(script, float(settings["voice_rate"]),
                                                str(settings.get("voice_name") or ""))
    black_segments = await asyncio.to_thread(detect_black_segments, path)
    audio = await asyncio.to_thread(inspect_audio, path) if info["audio"] else {"audible": False, "continuous": False}
    minimum, maximum = duration_bounds or (expected * 0.8, expected * 1.2)
    checks = {"duration": minimum <= info["duration"] <= maximum,
              "aspect": aspect_matches(info, settings["video_aspect"]), "audio": info["audio"],
              "black_frames": not black_segments, "audible": audio["audible"], "continuous_audio": audio["continuous"]}
    measurements = {"duration_seconds": round(info["duration"], 2),
                    "minimum_seconds": minimum, "maximum_seconds": maximum,
                    "width": info["width"], "height": info["height"],
                    "estimated_narration_seconds": round(expected, 2)}
    failed_checks = [name for name, passed in checks.items() if not passed]
    observations = []
    if not all(checks.values()):
        reason = (f"O vídeo final tem {info['duration']:.1f} s; o formato exige entre {minimum:.0f} e {maximum:.0f} s."
                  if not checks["duration"] else "Proporção, áudio baixo, silêncio excessivo ou trechos pretos incompatíveis.")
        return {"approved": False, "checks": checks, "failed_checks": failed_checks,
                "measurements": measurements, "audio_analysis": audio,
                "black_segments": black_segments, "observations": observations, "reason": reason}
    async def review_frame(index: int, scene: dict[str, Any]) -> dict[str, Any]:
        offset = min(info["duration"] - 0.2, (index + 0.5) * info["duration"] / len(scenes))
        frame = await asyncio.to_thread(sample_frame, path, offset)
        verdict = await judge_frame(frame, f"Revisor de montagem. Quadro do trecho {index + 1}/{len(scenes)}. Narração esperada: {scene['narration']}. Material aprovado: {next(c['source_page'] for c in scene['candidates'] if c['id'] == scene['selected'])}. Verifique imagem coerente, sem tela preta, ordem da cena e legenda legível. Informe apenas o que o quadro permite observar; não presuma sincronia de áudio.")
        return {"scene": index + 1, **verdict}
    for start in range(0, len(scenes), 3):
        observations.extend(await asyncio.gather(*(review_frame(index, scenes[index]) for index in range(start, min(start + 3, len(scenes))))))
    approved = all(checks.values()) and all(entry["approved"] for entry in observations)
    return {"approved": approved, "checks": checks,
            "failed_checks": failed_checks + (["visual_scenes"] if any(not entry["approved"] for entry in observations) else []),
            "measurements": measurements, "audio_analysis": audio, "black_segments": black_segments, "observations": observations,
            "reason": "Montagem aprovada nas amostras verificadas." if approved else "Uma ou mais cenas não passaram na revisão visual."}
