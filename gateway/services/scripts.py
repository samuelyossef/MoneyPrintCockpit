"""Script proposal, reference, short/long generation and visual terms."""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from fastapi import FastAPI, HTTPException, Request, Response

from gateway import main as m


def short_script_max_words(paragraph_number: int, voice_rate: float = 1.0, voice_name: str = "") -> int:
    return int(m.script_duration_budget("short", paragraph_number, voice_rate, voice_name)["maximum_words"])


def short_script_min_words(paragraph_number: int = 4, voice_rate: float = 1.0, voice_name: str = "") -> int:
    return int(m.script_duration_budget("short", paragraph_number, voice_rate, voice_name)["minimum_words"])


def script_duration_budget(format: str, target: int | float, voice_rate: float = 1.0, voice_name: str = "") -> dict[str, float | int]:
    """Returns one narration-time and word budget for every script-generation path."""
    if format == "short":
        target_seconds = m.SHORT_TARGET_SECONDS.get(int(target), max(m.SHORT_TARGET_SECONDS.values()))
        minimum_seconds = max(m.SHORT_MIN_SECONDS, target_seconds * 0.9)
        maximum_seconds = min(90, target_seconds * 1.1)
    elif format == "long":
        target_seconds = float(target) * 60
        minimum_seconds, maximum_seconds = target_seconds * 0.85, target_seconds
    else:
        raise ValueError(f"Formato de roteiro inválido: {format}")
    words_per_minute = m.voice_words_per_minute(voice_name)
    minimum_words = math.ceil(minimum_seconds * words_per_minute * voice_rate / 60)
    maximum_words = math.floor(maximum_seconds * words_per_minute * voice_rate / 60)
    if minimum_words > maximum_words:
        raise HTTPException(status_code=422, detail="A duração e a velocidade de voz não permitem uma faixa de roteiro válida.")
    target_words = round((minimum_words + maximum_words) / 2)
    return {
        "target_seconds": float(target_seconds), "minimum_seconds": float(minimum_seconds),
        "maximum_seconds": float(maximum_seconds), "minimum_words": minimum_words,
        "maximum_words": maximum_words, "target_words": target_words,
    }


def short_script_prompt(script_prompt: str, paragraph_number: int, voice_rate: float = 1.0, voice_name: str = "") -> str:
    """Carries the selected Short duration and voice-calibrated word range into Turbo's prompt."""
    budget = m.script_duration_budget("short", paragraph_number, voice_rate, voice_name)
    length_rule = (
        f"A narração deve durar entre {budget['minimum_seconds']:g} e {budget['maximum_seconds']:g} segundos "
        f"(alvo {(budget['minimum_seconds'] + budget['maximum_seconds']) / 2:g} s), com {budget['minimum_words']} a "
        f"{budget['maximum_words']} palavras na voz selecionada."
    )
    custom = (script_prompt or "").strip()
    from gateway.editorial_quality import writing_brief
    return "\n".join(part for part in (custom, length_rule, writing_brief("short")) if part)


def trim_to_words(script: str, max_words: int) -> str:
    """Caps an AI-written script at max_words, backing up to the end of the last full sentence
    so a generated narration never reads longer than the format's chosen duration. Never applied
    to a script the user typed or pasted themselves."""
    words = script.split()
    if len(words) <= max_words:
        return script.strip()
    truncated = " ".join(words[:max_words])
    match = re.search(r"^(.*[.!?])[^.!?]*$", truncated, re.DOTALL)
    return (match.group(1) if match else truncated).strip()


def normalize_proposal(raw: dict[str, Any]) -> dict[str, Any]:
    angle = str(raw.get("angle") or "").strip()[:300]
    questions = [str(item).strip()[:180] for item in raw.get("audience_questions") or [] if str(item).strip()][:5]
    references = [str(item).strip()[:180] for item in raw.get("references") or [] if str(item).strip()][:5]
    brief = str(raw.get("brief") or "").strip()[:1200]
    if not angle or not brief or len(questions) < 2:
        raise HTTPException(status_code=502, detail="A proposta de pesquisa veio incompleta. Tente de novo.")
    return {"angle": angle, "audience_questions": questions, "references": references, "brief": brief}


def reference_structure(path: Path) -> dict[str, Any]:
    """Describe pacing only. The source pictures and words are not copied into the script."""
    import subprocess
    from gateway.visual_specialists import probe

    try:
        info = probe(path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Não foi possível ler o vídeo de referência.") from exc
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise HTTPException(status_code=422, detail="O vídeo de referência não tem duração.")
    scene = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "select='gt(scene,0.3)',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    cuts = len(re.findall(r"pts_time:", scene.stderr))
    rate = cuts / duration
    pace = "rápido" if rate >= 0.35 else "moderado" if rate >= 0.12 else "calmo"
    hook = max(1.0, min(3.0, duration * 0.12))
    brief = (
        f"Abra nos primeiros {hook:.0f}s, mantenha ritmo {pace} "
        f"({cuts} trocas de cena em {duration:.0f}s) e reserve o último décimo para o fechamento. "
        "Não copie falas, imagens nem a marca do vídeo de referência."
    )
    return {"duration_seconds": round(duration, 1), "hook_seconds": round(hook, 1), "cuts": cuts, "pace": pace, "brief": brief}


def clean_narration(text: str) -> str:
    """Kimi still slips in headings, bullets or bold now and then; the TTS would read the symbols aloud."""
    text = re.sub(r"(?m)^\s*```[^\n]*$", "", text)
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"\[(?:pausa|pause|música|music|sfx|cena|scene)(?:\s*[:\-][^\]]*)?\]", "", text, flags=re.IGNORECASE)
    lines = (re.sub(r"^\s*(#+|[-*•]|\d+[.)])\s+", "", line).replace("**", "").strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def long_script_tokens(words: int, ceiling: int) -> int:
    """Reserve enough output for Portuguese narration without inviting needless reasoning.

    The estimate is intentionally below the provider-wide script cap.  A final
    word trim remains the authoritative duration guardrail.
    """
    return min(ceiling, max(768, round(words * 1.8)))


async def _fit_long_script_duration(input: m.LongScriptInput, result: dict[str, Any]) -> dict[str, Any]:
    budget = m.script_duration_budget("long", input.minutes, input.voice_rate, input.voice_name)
    minimum, maximum = budget["minimum_seconds"], budget["maximum_seconds"]
    words_per_minute = m.voice_words_per_minute(input.voice_name)
    script = m.clean_narration(str(result.get("script") or "")).strip()
    trimmed = bool(result.get("trimmed"))
    for attempt in range(3):
        if not script:
            raise HTTPException(status_code=502, detail="A IA não devolveu um roteiro válido.")
        if len(script.split()) > budget["maximum_words"]:
            shorter = m.trim_to_words(script, int(budget["maximum_words"]))
            if len(shorter.split()) >= budget["minimum_words"]:
                script = shorter
                trimmed = True
        seconds = m.narration_seconds(script, input.voice_rate, input.voice_name)
        if budget["minimum_words"] <= len(script.split()) and minimum <= seconds <= maximum:
            return {
                **result, "script": script, "words": len(script.split()), "trimmed": trimmed,
                "estimated_minutes": round(len(script.split()) / (words_per_minute * input.voice_rate), 1),
            }
        if attempt == 2:
            break
        response = m.parse_json_object(await m.llm_chat(
            f"Ajuste este roteiro para um vídeo de YouTube com duração selecionada de {input.minutes} minutos, "
            f"voz {input.voice_name or 'padrão'} a {input.voice_rate}x. A narração anterior foi estimada em {seconds:.1f} s. "
            f"Escreva o roteiro completo com cerca de {budget['target_words']} palavras, obrigatoriamente entre "
            f"{budget['minimum_words']} e {budget['maximum_words']} palavras ({minimum:.0f}–{maximum:.0f} s estimados). "
            "Preserve o tema, os fatos, a estrutura, a conclusão e as instruções editoriais. "
            f"Requisitos personalizados: {input.requirements}. Instruções do canal: {input.system_prompt}. "
            "Se estiver curto, desenvolva os pontos pedidos; condense repetições se estiver longo. Não use texto de enchimento. "
            "Use somente narração em parágrafos corridos, sem títulos, marcadores, Markdown, emojis ou indicações de cena. "
            f'Responda apenas JSON: {{"script": "narração completa"}}. Roteiro atual:\n{script}',
            json_mode=True, task="script", max_tokens=m.long_script_tokens(int(budget["maximum_words"]), m.FAST_LONG_SCRIPT_MAX_TOKENS),
            fast_script=True,
        ))
        script = m.clean_narration(str(response.get("script") or "")).strip()
        trimmed = False
    final_seconds = m.narration_seconds(script, input.voice_rate, input.voice_name) if script else 0
    raise HTTPException(
        status_code=502,
        detail=f"O provedor não ajustou o roteiro à duração: {round(final_seconds)} s estimados; a faixa selecionada é {round(minimum)}–{round(maximum)} s. Gere novamente ou ajuste a duração/voz.",
    )


async def long_script_events(input: m.LongScriptInput) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Writes an editable long script and reports only safe UI progress events."""
    from gateway.editorial_quality import writing_brief
    language = m.clean_language(input.language)
    requirements = input.requirements.strip()
    system_prompt = input.system_prompt.strip()
    # ponytail: unlike Turbo's own custom_system_prompt (a full replacement), this can't replace the
    # whole instruction set here - the outline call must stay valid JSON and each section call must
    # stay narration-only, or the multi-step pipeline breaks. It's folded in as an extra, higher-weight
    # instruction on every call instead, same mechanism as `requirements` but framed as a standing rule.
    style = (f" Instruções de estilo do sistema, siga em tudo o que escrever: {system_prompt}." if system_prompt else "")
    style += " " + writing_brief("long")
    if input.proposal.strip():
        style += " Proposta editorial confirmada, siga este ângulo sem copiar fontes: " + input.proposal.strip()
    if input.reference_brief.strip():
        style += " Estrutura de referência, use só o ritmo: " + input.reference_brief.strip()
    budget = m.script_duration_budget("long", input.minutes, input.voice_rate, input.voice_name)
    target_words = int(budget["target_words"])

    # The common 5-8 minute case used to incur an outline plus 4-5 serial
    # writes.  One bounded completion is both faster and coherent because the
    # entire requested narration fits in the selected model's context.
    if input.minutes <= 8:
        yield "progress", {"stage": "writing", "section": 1, "total": 1, "message": "Escrevendo o roteiro completo…"}
        generated = m.parse_json_object(await m.llm_chat(
            f"Escreva um roteiro completo de vídeo de YouTube de cerca de {input.minutes} minutos no idioma {language} sobre: {input.subject}."
            + (f" Nicho do canal: {input.niche}." if input.niche else "")
            + (f" Requisitos personalizados: {requirements}." if requirements else "") + style
            + f' Responda apenas JSON: {{"title": título conciso, "script": narração com cerca de {target_words} palavras, entre {budget["minimum_words"]} e {budget["maximum_words"]}}}. '
            "A narração deve ter abertura forte, desenvolvimento coerente e conclusão com chamada para se inscrever. "
            "Use apenas texto falado em parágrafos corridos: sem títulos no roteiro, marcadores, markdown, emojis nem indicações de cena.",
            json_mode=True,
            task="script",
            max_tokens=m.long_script_tokens(target_words, m.FAST_LONG_SCRIPT_MAX_TOKENS),
            fast_script=True,
        ))
        script = m.clean_narration(str(generated.get("script") or ""))
        if not script:
            raise HTTPException(status_code=502, detail="A IA não devolveu um roteiro válido")
        trimmed_script = script
        if len(script.split()) > budget["maximum_words"]:
            candidate = m.trim_to_words(script, int(budget["maximum_words"]))
            if len(candidate.split()) >= budget["minimum_words"]:
                trimmed_script = candidate
        words = len(trimmed_script.split())
        result = {
            "title": str(generated.get("title") or input.subject), "script": trimmed_script,
            "trimmed": trimmed_script != script, "sections": ["Roteiro completo"],
            "words": words, "estimated_minutes": round(words / (m.voice_words_per_minute(input.voice_name) * input.voice_rate), 1),
        }
        result = await m._fit_long_script_duration(input, result)
        yield "progress", {"stage": "finalizing", "section": 1, "total": 1, "message": "Finalizando roteiro…"}
        yield "completed", result
        return

    section_count = max(4, round(input.minutes / 1.5))
    words_per_section = target_words // section_count
    yield "progress", {"stage": "planning", "section": 0, "total": section_count, "message": "Planejando as seções…"}
    outline = m.parse_json_object(await m.llm_chat(
        f"Planeje um vídeo de YouTube de {input.minutes} minutos no idioma {language} sobre: {input.subject}."
        + (f" Nicho do canal: {input.niche}." if input.niche else "")
        + (f" Requisitos personalizados: {requirements}." if requirements else "") + style
        + f' Responda apenas JSON: {{"title": título do vídeo, "sections": lista com {section_count} itens '
        '{"heading": título da seção, "points": lista de 2 a 4 pontos}}. '
        "A primeira seção é um gancho de abertura e a última uma conclusão com chamada para se inscrever.",
        json_mode=True,
        task="outline",
        max_tokens=m.long_script_tokens(section_count * 180, m.FAST_LONG_SECTION_MAX_TOKENS),
        fast_script=True,
    ))
    sections = [section for section in outline.get("sections") or [] if isinstance(section, dict) and section.get("heading")][:section_count]
    if not sections:
        raise HTTPException(status_code=502, detail="A IA não devolveu o roteiro em seções")
    parts: list[str] = []
    for index, section in enumerate(sections, start=1):
        yield "progress", {"stage": "writing", "section": index, "total": len(sections), "message": f"Escrevendo seção {index} de {len(sections)}…"}
        previous = f"\nFinal da seção anterior (continue a partir dele, sem repetir): {parts[-1][-600:]}" if parts else ""
        parts.append(m.clean_narration(await m.llm_chat(
            f"Escreva a narração da seção {index} de {len(sections)} de um vídeo de YouTube no idioma {language} sobre: {input.subject}.\n"
            f"Seção: {section['heading']}\nPontos: {'; '.join(map(str, section.get('points') or []))}\n"
            f"Cerca de {words_per_section} palavras. Somente o texto falado, em parágrafos corridos: sem títulos, marcadores, markdown, emojis nem indicações de cena."
            + (f" Requisitos personalizados: {requirements}." if requirements else "") + style + previous,
            task="script",
            max_tokens=m.long_script_tokens(words_per_section, m.FAST_LONG_SECTION_MAX_TOKENS),
            fast_script=True,
        )))
    script = "\n\n".join(part for part in parts if part)
    trimmed_script = script
    if len(script.split()) > budget["maximum_words"]:
        candidate = m.trim_to_words(script, int(budget["maximum_words"]))
        if len(candidate.split()) >= budget["minimum_words"]:
            trimmed_script = candidate
    words = len(trimmed_script.split())
    result = {
        "title": str(outline.get("title") or input.subject), "script": trimmed_script, "trimmed": trimmed_script != script,
        "sections": [str(section["heading"]) for section in sections], "words": words, "estimated_minutes": round(words / (m.voice_words_per_minute(input.voice_name) * input.voice_rate), 1),
    }
    result = await m._fit_long_script_duration(input, result)
    yield "progress", {"stage": "finalizing", "section": len(sections), "total": len(sections), "message": "Finalizando roteiro…"}
    yield "completed", result


async def generate_long_script(input: m.LongScriptInput) -> dict[str, Any]:
    async for event, data in m.long_script_events(input):
        if event == "completed":
            return data
    raise HTTPException(status_code=502, detail="A IA não concluiu o roteiro")


def split_script(script: str, parts: int) -> list[str]:
    """Splits narration into `parts` contiguous, roughly-equal chunks by word count, preserving
    order and every word - used to line up one visual search term per script chunk."""
    words = script.split()
    if parts <= 1 or not words:
        return [script.strip()] if script.strip() else []
    parts = min(parts, len(words))
    chunk = -(-len(words) // parts)  # ceil division without importing math
    return [" ".join(words[i:i + chunk]) for i in range(0, len(words), chunk)]


def voice_words_per_minute(voice_name: str = "") -> int:
    # The connected en-US Jenny voice produced 160 spoken words in 56.4 s on a real render.
    return 170 if voice_name.casefold() == "en-us-jennyneural" else m.WORDS_PER_MINUTE


def narration_seconds(script: str, voice_rate: float = 1.0, voice_name: str = "") -> float:
    return len(script.split()) / (m.voice_words_per_minute(voice_name) / 60) / max(voice_rate, 0.1)


def visual_term_count(script: str, clip_seconds: float, voice_rate: float = 1.0) -> int:
    if clip_seconds <= 0:
        raise HTTPException(status_code=422, detail="A duração de cada clipe deve ser maior que zero.")
    slots = max(1, math.ceil(m.narration_seconds(script, voice_rate) / clip_seconds))
    if slots > m.MAX_VISUAL_TERMS:
        raise HTTPException(status_code=422, detail=(
            f"A duração escolhida exige {slots} buscas de filmagens, acima do limite de {m.MAX_VISUAL_TERMS}. "
            "Aumente a duração de cada clipe ou reduza a duração do vídeo."
        ))
    return slots


def _fill_visual_terms(terms: list[str], count: int) -> list[str]:
    """Turbo drops a term's candidates once its URLs are already claimed by an earlier term
    in the list, so duplicates silently lose their slot; dedupe, then pad or truncate to
    exactly `count` so every script chunk still gets a term of its own."""
    seen: set[str] = set()
    unique = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            unique.append(term)
    if not unique:
        unique = ["b-roll footage"]
    result = list(unique[:count])
    suffixes = ["close up", "wide shot", "detail shot"]
    index = 0
    while len(result) < count:
        result.append(f"{unique[index % len(unique)]} {suffixes[(index // len(unique)) % len(suffixes)]}")
        index += 1
    return result[:count]


async def visual_terms(subject: str, script: str, clip_seconds: float, voice_rate: float = 1.0, context: str = "") -> tuple[list[str], float]:
    """One stock-footage search term per script chunk, in narration order. Combined with Turbo's
    match_materials_to_script + sequential mode, this makes the footage follow what's being said
    instead of cycling a handful of subject-wide terms across the whole video."""
    slots = m.visual_term_count(script, clip_seconds, voice_rate)
    count = slots
    chunks = m.split_script(script, count) or [subject]
    numbered = "\n".join(f"{index + 1}. {chunk}" for index, chunk in enumerate(chunks))
    generated = await m.llm_json_object(
        f"Tema do vídeo: {subject}\nContexto editorial: {context[:5000]}\nTrechos numerados da narração, na ordem em que aparecem:\n{numbered}\n"
        "Para cada trecho, gere uma busca de banco de vídeos em INGLÊS (1 a 3 palavras) que mostre algo filmável e "
        "concreto do que é dito naquele trecho: uma cena, objeto, ação ou lugar. Nada abstrato, sem nomes próprios, "
        "sem texto na tela. Os termos devem ser diferentes entre si - não repita o tema geral em todos.\n"
        f'Responda apenas JSON: {{"terms": lista com exatamente {len(chunks)} strings, na mesma ordem dos trechos}}',
        label="as palavras-chave das filmagens",
    )
    raw_terms = [str(term).strip() for term in generated.get("terms") or [] if str(term).strip()]
    return m._fill_visual_terms(raw_terms, len(chunks)), clip_seconds
