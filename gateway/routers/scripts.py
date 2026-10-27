"""Script proposal and generation routes."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from gateway import main as m

router = APIRouter()


@router.post("/api/scripts/proposal")
async def script_proposal(input: m.ScriptProposalInput) -> dict[str, Any]:
    generated = await m.llm_json_object(
        f"Pesquise um ângulo editorial para um vídeo {input.format} no idioma {input.language}. "
        f"Tema: {input.subject}. Contexto do canal: {input.channel_context[:1500] or 'não informado'}. "
        "Não invente URLs, números de audiência nem citações verificáveis. Referências são temas ou perguntas, não links. "
        'Responda JSON: {"angle": "ângulo em uma frase", "audience_questions": ["pergunta", "pergunta"], '
        '"references": ["tema de referência"], "brief": "instrução de 4 a 8 linhas para o roteiro, com abertura, progressão e fechamento"}',
        task="json", max_tokens=700, label="a proposta de pesquisa",
    )
    return m.normalize_proposal(generated)


@router.post("/api/scripts/reference")
async def script_reference(request: Request) -> dict[str, Any]:
    payload = await request.body()
    if not 1024 <= len(payload) <= 80 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Envie um vídeo local entre 1 KB e 80 MB.")
    folder = m.DATA_DIR / "references"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid4()}.mp4"
    path.write_bytes(payload)
    try:
        return m.reference_structure(path)
    finally:
        path.unlink(missing_ok=True)


@router.post("/api/scripts/short")
async def short_script(input: m.ShortScriptInput) -> dict[str, Any]:
    budget = m.script_duration_budget("short", input.paragraph_number, input.voice_rate, input.voice_name)
    minimum, maximum = budget["minimum_seconds"], budget["maximum_seconds"]
    previous = ""
    for attempt in range(3):
        trimmed_this_attempt = False
        prompt = m.short_script_prompt(input.script_prompt, input.paragraph_number, input.voice_rate, input.voice_name)
        if input.persona.strip():
            prompt = f"Identidade e tom do canal, siga durante todo o roteiro: {input.persona.strip()}.\n{prompt}"
        if input.proposal.strip():
            prompt += "\nProposta editorial confirmada. Siga este ângulo sem copiar fontes:\n" + input.proposal.strip()
        if input.reference_brief.strip():
            prompt += "\nEstrutura observada em um vídeo de referência. Use só o ritmo, não o conteúdo:\n" + input.reference_brief.strip()
        if previous:
            seconds = m.narration_seconds(previous, input.voice_rate, input.voice_name)
            prompt += (
                f"\nA versão anterior tinha {len(previous.split())} palavras e foi estimada em {seconds:.1f} segundos. "
                f"Reescreva o roteiro completo para ficar na faixa de {budget['minimum_words']} a {budget['maximum_words']} palavras "
                f"e de {minimum:.0f} a {maximum:.0f} segundos. Preserve o tema, os fatos e as instruções; não acrescente texto de enchimento. "
                f"Roteiro anterior:\n{previous}"
            )
        response = await m.turbo_request("POST", "/api/v1/scripts", json={
            "video_subject": input.subject, "video_language": m.clean_language(input.language),
            "paragraph_number": input.paragraph_number,
            "video_script_prompt": prompt,
            "custom_system_prompt": input.system_prompt,
        })
        generated = m.clean_narration(str((m.unwrap(response.json()) or {}).get("video_script", ""))).strip()
        if not generated:
            raise HTTPException(status_code=502, detail="O provedor não devolveu um roteiro válido.")
        candidate = generated
        if len(candidate.split()) > budget["maximum_words"]:
            candidate = m.trim_to_words(candidate, int(budget["maximum_words"]))
            trimmed_this_attempt = True
        words = len(candidate.split())
        seconds = m.narration_seconds(candidate, input.voice_rate, input.voice_name)
        if words >= budget["minimum_words"] and minimum <= seconds <= maximum:
            return {"script": candidate, "words": words, "trimmed": trimmed_this_attempt,
                    "too_short": False, "min_words": budget["minimum_words"],
                    "estimated_seconds": round(seconds)}
        previous = candidate
    seconds = m.narration_seconds(previous, input.voice_rate, input.voice_name)
    raise HTTPException(
        status_code=502,
        detail=f"O provedor não ajustou o roteiro à duração: {round(seconds)} s estimados; a faixa selecionada é {round(minimum)}–{round(maximum)} s. Gere novamente ou ajuste a duração/voz.",
    )


@router.post("/api/scripts/long")
async def long_script(input: m.LongScriptInput) -> dict[str, Any]:
    """Compatibility endpoint for clients that do not consume script progress."""
    return await m.generate_long_script(input)


@router.post("/api/scripts/long/stream")
async def long_script_stream(input: m.LongScriptInput, request: Request) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        try:
            async for event, data in m.long_script_events(input):
                if await request.is_disconnected():
                    return
                yield m.sse_message(event, json.dumps(data, ensure_ascii=False))
        except HTTPException as exc:
            yield m.sse_message("error", json.dumps({"code": exc.status_code, "message": str(exc.detail)}, ensure_ascii=False))
        except Exception:
            logging.exception("long script stream failed")
            yield m.sse_message("error", json.dumps({"code": 500, "message": "Não foi possível gerar o roteiro."}, ensure_ascii=False))
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/scripts/terms")
async def script_terms(input: m.VisualTermsInput) -> dict[str, Any]:
    terms, clip_seconds = await m.visual_terms(input.subject, input.script, input.clip_seconds, input.voice_rate)
    return {"terms": terms, "clip_seconds": round(clip_seconds, 1), "cuts": len(terms)}
