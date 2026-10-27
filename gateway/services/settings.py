"""Generation defaults, provider config and LLM plumbing."""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, AsyncIterator, Literal
import httpx
from fastapi import FastAPI, HTTPException, Request, Response

from gateway import main as m


def default_generation_settings() -> dict[str, Any]:
    """Base compartilhada com Pexels como origem padrão; os perfis podem escolher NVIDIA."""
    return {
        "video_language": "pt-BR",
        "video_aspect": "9:16",
        "video_source": "pexels",
        "voice_name": "pt-BR-FranciscaNeural",
        "voice_rate": 1.0,
        "voice_volume": 1.0,
        "video_clip_speed": 1.0,
        "subtitle_enabled": True,
        "subtitle_provider": "edge",
        "stroke_width": 1.5,
        "bgm_type": "",
        "bgm_volume": 0.2,
        "video_transition_mode": "",
        "video_count": 1,
        "video_clip_duration": 5,
        "video_concat_mode": "script",
        "paragraph_number": 5,
        "font_size": 56,
        "text_fore_color": "#FFFFFF",
        "stroke_color": "#000000",
    }


def settings_key(format: str | None) -> str:
    return f"generation-defaults:{format}" if format else "generation-defaults"


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _toml_set(text: str, key: str, rendered_value: str) -> str:
    line = f"{key} = {rendered_value}"
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    return pattern.sub(line, text, count=1) if pattern.search(text) else text.rstrip("\n") + "\n" + line + "\n"


async def detect_moonshot_region(client: httpx.AsyncClient, api_key: str) -> str | None:
    """Moonshot/Kimi keys are platform-specific: a key from the China platform
    (platform.kimi.com) only works against api.moonshot.cn, a Global-platform key
    (platform.kimi.ai) only against api.moonshot.ai. Turbo defaults to the China
    endpoint when moonshot_base_url is blank, so a Global key looks "wrong" unless
    we detect it and point moonshot_base_url there ourselves."""
    for region, base_url in m.MOONSHOT_ENDPOINTS.items():
        try:
            response = await client.get(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"})
        except httpx.HTTPError:
            continue
        if response.status_code == 200:
            return region
    return None


def nvidia_model_or_422(model: str) -> str:
    if model not in m.NVIDIA_MODEL_IDS:
        raise HTTPException(status_code=422, detail="Escolha um dos modelos NVIDIA disponíveis no Cockpit.")
    return model


async def nvidia_model_catalog() -> dict[str, Any]:
    """Returns only supported chat models and never exposes NVIDIA credentials.

    /v1/models is a discovery endpoint, not an authentication probe.  Availability
    is therefore advisory; the explicit test endpoint is the proof that a selected
    model can complete a request with the configured key.
    """
    config_path = m.DATA_DIR / "moneyprinter" / "config.toml"
    config = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    key = m._config_string(config, "nvidia_api_key")
    selected = m._config_string(config, "nvidia_text_model") or m.NVIDIA_DEFAULT_TEXT_MODEL
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest() if key else ""
    cached = m._nvidia_catalog_cache
    if cached.get("fingerprint") == fingerprint and cached.get("expires_at", 0.0) > time.monotonic():
        return {**cached["value"], "selected_model": selected}

    checked_at = m.now()
    available_ids: set[str] | None = None
    catalog_status = "not_configured" if not key else "unavailable"
    if key:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
                response = await client.get(f"{m.NVIDIA_BASE_URL}/models", headers={"Authorization": f"Bearer {key}"})
            if response.status_code == 200:
                data = response.json().get("data", [])
                available_ids = {str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")}
                catalog_status = "available"
            elif response.status_code in {401, 403}:
                catalog_status = "unauthorized"
        except (httpx.HTTPError, ValueError, TypeError):
            # Deliberately do not put transport/provider details into a browser response.
            catalog_status = "unavailable"

    models = [{**model, "capabilities": dict(model["capabilities"]), "available": (model["id"] in available_ids) if available_ids is not None else None}
              for model in m.NVIDIA_MODELS]
    value = {"models": models, "catalog_status": catalog_status, "checked_at": checked_at}
    m._nvidia_catalog_cache.clear()
    m._nvidia_catalog_cache.update({"fingerprint": fingerprint, "expires_at": time.monotonic() + m.NVIDIA_CATALOG_CACHE_SECONDS, "value": value})
    return {**value, "selected_model": selected}


def nvidia_chat_body(
    model: str,
    prompt: str,
    task: Literal["json", "outline", "script", "test"] = "json",
    *,
    max_tokens: int | None = None,
    fast_script: bool = False,
) -> dict[str, Any]:
    """Builds only the parameters documented for each supported NIM model."""
    model = m.nvidia_model_or_422(model)
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens if max_tokens is not None else m.NVIDIA_TASK_TOKEN_LIMITS[task],
        "stream": False,
    }
    if model == "moonshotai/kimi-k3":
        body.update({"temperature": 1, "reasoning_effort": "medium" if fast_script else "max"})
    elif model in {"z-ai/glm-5.3", "z-ai/glm-5.3-flash"}:
        body.update({"temperature": 0.5, "top_p": 1})
    elif model == "nvidia/nemotron-3.5-lightning-30b-a3b":
        body.update({"temperature": 1, "top_p": 0.95, "chat_template_kwargs": {"enable_thinking": True},
                     "reasoning_budget": body["max_tokens"]})
    elif model == "meta/muse-glimmer-30b":
        body.update({"temperature": 1, "top_p": 0.95})
    elif model == "google/diffusiongemma-26b-a4b-it":
        # Structured outputs (visual terms, metadata and outlines) need the
        # final JSON within a bounded response.  DiffusionGemma can spend that
        # entire budget in private thinking, leaving content=null and causing a
        # misleading 502.  Keep thinking for narration, where it improves the
        # prose, but turn it off for JSON contracts.
        body.update({"temperature": 1, "top_p": 0.95,
                     "chat_template_kwargs": {"enable_thinking": task != "json"}})
    return body


def _config_string(text: str, key: str) -> str:
    match = re.search(rf'^{key}\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def config_value(key: str) -> str:
    config = m.DATA_DIR / "moneyprinter" / "config.toml"
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    return m._config_string(text, key)


def moonshot_config() -> tuple[str, str, str]:
    # Blank base_url means the China endpoint, same default Turbo applies.
    base_url = (m.config_value("moonshot_base_url") or m.MOONSHOT_ENDPOINTS["china"]).rstrip("/")
    return m.config_value("moonshot_api_key"), base_url, m.config_value("moonshot_model_name") or "moonshot-v1-8k"


def llm_config() -> tuple[str, str, str, str]:
    """Which provider/base_url/key/model backs the gateway's own text calls (metadata, long
    script, strategy, analytics) - independent of what Turbo itself uses for the Short's script."""
    if m.config_value("cockpit_llm_provider") == "nvidia":
        return "nvidia", m.NVIDIA_BASE_URL, m.config_value("nvidia_api_key"), m.config_value("nvidia_text_model") or m.NVIDIA_DEFAULT_TEXT_MODEL
    key, base_url, model = m.moonshot_config()
    return "moonshot", base_url, key, model


def llm_task(prompt: str, json_mode: bool) -> Literal["json", "outline", "script"]:
    """Keep existing call sites stable while enforcing task-sized output limits."""
    if "Planeje um vídeo de YouTube" in prompt:
        return "outline"
    if "Escreva a narração da seção" in prompt:
        return "script"
    return "json" if json_mode else "script"


async def llm_chat(
    prompt: str,
    json_mode: bool = False,
    *,
    task: Literal["json", "outline", "script", "test"] | None = None,
    max_tokens: int | None = None,
    fast_script: bool = False,
) -> str:
    provider, base_url, key, model = m.llm_config()
    label = "Moonshot/Kimi" if provider == "moonshot" else "NVIDIA"
    if not key:
        raise HTTPException(status_code=409, detail=f"Configure a chave {label} em Configurações.")
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    # response_format support varies across NIM models; the prompts already ask for plain JSON
    # and parse_json_object() tolerates prose or a code fence around it.
    if json_mode and provider == "moonshot":
        body["response_format"] = {"type": "json_object"}
    if provider == "nvidia":
        body = m.nvidia_chat_body(model, prompt, task or m.llm_task(prompt, json_mode), max_tokens=max_tokens, fast_script=fast_script)
    elif max_tokens is not None:
        body["max_tokens"] = max_tokens
    read_timeout = m.NVIDIA_GENERATION_READ_TIMEOUT_SECONDS if provider == "nvidia" else 180
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=read_timeout)) as client:
            response = await m.send_with_retry(client, "POST", f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)
    except httpx.ReadTimeout as exc:
        if provider == "nvidia":
            raise HTTPException(
                status_code=504,
                detail=(
                    "A NVIDIA não concluiu esta etapa do roteiro em 5 minutos. "
                    "A conexão básica está disponível; tente novamente ou escolha "
                    "GLM 5.3 Flash em Configurações para roteiros mais rápidos."
                ),
            ) from exc
        raise HTTPException(status_code=504, detail=f"{label} demorou demais para responder.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"{label} indisponível: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"{label} respondeu com erro {response.status_code}.")
    try:
        message = response.json()["choices"][0]["message"]
        # NVIDIA reasoning models may send their private chain-of-thought in a
        # separate field.  The Cockpit keeps only the final content for scripts
        # and metadata; reasoning is intentionally neither persisted nor shown.
        _reasoning = message.get("reasoning_content")
        content = message.get("content")
        return str(content) if content is not None else ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"O {label} devolveu uma resposta inesperada") from exc


def parse_json_object(content: str) -> dict[str, Any]:
    """Find a complete JSON object even when the model adds prose or code fences."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", content or ""):
        try:
            parsed, _ = decoder.raw_decode(content, match.start())
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise HTTPException(status_code=502, detail="A IA não devolveu JSON válido")


async def llm_json_object(
    prompt: str,
    *,
    task: Literal["json", "outline", "script", "test"] = "json",
    max_tokens: int | None = None,
    fast_script: bool = False,
    label: str = "esta etapa",
) -> dict[str, Any]:
    """Retry malformed structured output with the selected provider and model."""
    for attempt in range(3):
        retry_prompt = prompt
        if attempt:
            retry_prompt += (
                "\nA resposta anterior veio vazia ou com JSON inválido. "
                "Responda somente com um objeto JSON completo, sem markdown, comentários ou texto adicional."
            )
        content = await m.llm_chat(
            retry_prompt, json_mode=True, task=task,
            max_tokens=(max_tokens or m.NVIDIA_TASK_TOKEN_LIMITS[task]) + attempt * 512,
            fast_script=fast_script,
        )
        try:
            return m.parse_json_object(content)
        except HTTPException as exc:
            if exc.detail != "A IA não devolveu JSON válido":
                raise
    raise HTTPException(
        status_code=502,
        detail=f"A IA não devolveu JSON válido para {label} após 3 tentativas. Repita este canal ou escolha outro modelo no provedor configurado.",
    )
