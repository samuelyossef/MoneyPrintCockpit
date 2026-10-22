"""Generation defaults and provider settings routes."""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4
import httpx
from fastapi import APIRouter, HTTPException

from gateway import main as m

router = APIRouter()


@router.get("/api/settings/generation-defaults")
def generation_defaults(format: Literal["long", "short"] | None = None) -> dict[str, Any]:
    """Without format: the shared base edited in Configurações. With format: base, then that
    format's preset, then what its own screen saved with "Salvar padrão"."""
    with m.database() as db:
        rows = {row["key"]: json.loads(row["value_json"]) for row in db.execute(
            "SELECT key, value_json FROM settings WHERE key IN (?, ?)", ("generation-defaults", m.settings_key(format))
        )}
    base = rows.get("generation-defaults", m.default_generation_settings())
    if base.get("video_source") not in {"pexels", "pixabay", "coverr"}:
        # Fontes de IA removidas (só stock): um padrão antigo salvo cai para o Pexels.
        base = {**base, "video_source": "pexels"}
    if not format:
        return base
    return {**base, **m.FORMAT_PRESETS[format], **rows.get(m.settings_key(format), {}), **m.FORMAT_LOCKED[format]}


@router.put("/api/settings/generation-defaults")
def put_generation_defaults(input: m.SettingsPatch, format: Literal["long", "short"] | None = None) -> dict[str, Any]:
    key = m.settings_key(format)
    with m.database() as db:
        row = db.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        current = json.loads(row["value_json"]) if row else ({} if format else m.default_generation_settings())
        merged = {**current, **input.value}
        db.execute(
            "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (key, json.dumps(merged), m.now()),
        )
    return m.generation_defaults(format)


@router.get("/api/settings/providers")
def provider_readiness() -> dict[str, Any]:
    config = m.DATA_DIR / "moneyprinter" / "config.toml"
    value = config.read_text(encoding="utf-8") if config.exists() else ""
    model_match = re.search(r'moonshot_model_name\s*=\s*"([^"]*)"', value)
    return {
        "moonshot": 'moonshot_api_key = ""' not in value and "moonshot_api_key" in value,
        "nvidia": 'nvidia_api_key = ""' not in value and "nvidia_api_key" in value,
        "pexels": 'pexels_api_keys = []' not in value and "pexels_api_keys" in value,
        "pixabay": 'pixabay_api_keys = []' not in value and "pixabay_api_keys" in value,
        "coverr": 'coverr_api_keys = []' not in value and "coverr_api_keys" in value,
        "upload_post": 'upload_post_api_key = ""' not in value and "upload_post_api_key" in value,
        "edge_tts": True,
        "moonshot_model": model_match.group(1) if model_match else "",
        "llm_provider": m._config_string(value, "cockpit_llm_provider") or "moonshot",
        "nvidia_text_model": m._config_string(value, "nvidia_text_model") or m.NVIDIA_DEFAULT_TEXT_MODEL,
    }


@router.get("/api/settings/providers/nvidia/models")
async def list_nvidia_models() -> dict[str, Any]:
    return await m.nvidia_model_catalog()


@router.post("/api/settings/providers/nvidia/test")
async def test_nvidia_model(input: m.NvidiaModelTest) -> dict[str, Any]:
    model = m.nvidia_model_or_422(input.model)
    config_path = m.DATA_DIR / "moneyprinter" / "config.toml"
    config = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    key = m._config_string(config, "nvidia_api_key")
    if not key:
        raise HTTPException(status_code=409, detail="Configure a chave NVIDIA antes de testar um modelo.")
    started = time.monotonic()
    # A connectivity probe must stay cheap even when the selected generation
    # profile enables deep reasoning (Kimi/Nemotron).  It validates the same
    # model and key without consuming a full reasoning budget.
    probe_body = {"model": model, "messages": [{"role": "user", "content": "Responda somente: OK."}], "max_tokens": 16, "stream": False}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=60)) as client:
            response = await asyncio.wait_for(
                client.post(f"{m.NVIDIA_BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=probe_body),
                timeout=m.NVIDIA_PROBE_TIMEOUT_SECONDS,
            )
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return {"ok": False, "model": model, "latency_ms": round((time.monotonic() - started) * 1000),
                "detail": f"A NVIDIA não concluiu o teste em {m.NVIDIA_PROBE_TIMEOUT_SECONDS} segundos."}
    except httpx.HTTPError:
        return {"ok": False, "model": model, "latency_ms": round((time.monotonic() - started) * 1000),
                "detail": "Não foi possível alcançar a NVIDIA agora."}
    latency_ms = round((time.monotonic() - started) * 1000)
    if response.status_code == 200:
        return {"ok": True, "model": model, "latency_ms": latency_ms, "detail": "Modelo respondeu com sucesso."}
    detail = "A NVIDIA recusou a chave ou o modelo." if response.status_code in {401, 403, 404} else "A NVIDIA não pôde concluir o teste agora."
    return {"ok": False, "model": model, "latency_ms": latency_ms, "detail": detail}


@router.post("/api/settings/providers/validate")
async def validate_provider_key(input: m.ProviderValidation) -> dict[str, Any]:
    """Calls the real provider API to check the key works, before it's ever saved.
    The key is used for this one outbound request and never logged or persisted here.
    Upload-Post has no publicly documented lightweight auth-check endpoint, so it's
    accepted without verification rather than guessing at one and risking a false
    rejection of a good key (or false acceptance of a bad one)."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            if input.provider == "moonshot":
                region = await m.detect_moonshot_region(client, input.api_key)
                detail = None if region else "Chave rejeitada pelo Moonshot/Kimi (testei as plataformas China e Global)."
                return {"valid": region is not None, "detail": detail, "region": region}
            if input.provider == "nvidia":
                # /v1/models on the NIM gateway answers without checking the key at all, so a
                # cheap real completion is the only reliable way to tell a bad key from a good one.
                response = await client.post(
                    f"{m.NVIDIA_BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {input.api_key}"},
                    json={"model": m.NVIDIA_DEFAULT_TEXT_MODEL, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                )
                return {"valid": response.status_code == 200, "detail": None if response.status_code == 200 else "Chave rejeitada pela NVIDIA."}
            if input.provider == "pexels":
                # A common query string like "test" gets served from Cloudflare's edge
                # cache regardless of the Authorization header, making every key look
                # valid; a random query forces a real cache bypass to Pexels' origin.
                response = await client.get("https://api.pexels.com/v1/search", params={"query": str(uuid4()), "per_page": 1}, headers={"Authorization": input.api_key})
                return {"valid": response.status_code == 200, "detail": None if response.status_code == 200 else "Chave rejeitada pelo Pexels."}
            if input.provider == "pixabay":
                response = await client.get("https://pixabay.com/api/videos/", params={"key": input.api_key, "q": str(uuid4()), "per_page": 3})
                return {"valid": response.status_code == 200, "detail": None if response.status_code == 200 else "Chave rejeitada pelo Pixabay."}
            if input.provider == "coverr":
                response = await client.get("https://api.coverr.co/videos", params={"query": str(uuid4())}, headers={"Authorization": f"Bearer {input.api_key}"})
                return {"valid": response.status_code == 200, "detail": None if response.status_code == 200 else "Chave rejeitada pelo Coverr."}
            return {"valid": None, "detail": "Upload-Post não tem verificação automática — a chave será salva sem checar antes."}
    except httpx.HTTPError as exc:
        return {"valid": None, "detail": f"Não foi possível verificar agora: {exc}"}


@router.put("/api/settings/providers")
async def update_providers(input: m.ProviderCredentials) -> dict[str, Any]:
    """Writes credentials straight to config.toml - never logged, never echoed back
    (the response is only the boolean readiness map, same as GET). Turbo reads this
    file once at startup, so a running moneyprinterturbo container needs a restart
    (`docker compose restart moneyprinterturbo`) to pick up a change made here."""
    config_path = m.DATA_DIR / "moneyprinter" / "config.toml"
    if not config_path.exists():
        raise HTTPException(status_code=409, detail="config.toml não encontrado. Rode setup.ps1 primeiro.")
    text = config_path.read_text(encoding="utf-8")
    if input.nvidia_text_model:
        m.nvidia_model_or_422(input.nvidia_text_model)
    active_provider = m._config_string(text, "cockpit_llm_provider") or "moonshot"
    effective_provider = input.llm_provider or active_provider
    turbo_changed = bool(input.llm_provider) or (
        effective_provider == "nvidia" and bool(input.nvidia_api_key or input.nvidia_text_model)
    ) or (
        effective_provider == "moonshot" and bool(input.moonshot_api_key or input.moonshot_model_name)
    )
    if input.moonshot_api_key:
        text = m._toml_set(text, "moonshot_api_key", f'"{m._toml_escape(input.moonshot_api_key)}"')
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
            region = await m.detect_moonshot_region(client, input.moonshot_api_key)
        # Blank base_url makes Turbo default to the China endpoint; a Global-platform
        # key only works if we point it at api.moonshot.ai explicitly. When detection is
        # inconclusive (network blip, rejected key saved anyway) keep the current value.
        if region:
            text = m._toml_set(text, "moonshot_base_url", f'"{m.MOONSHOT_ENDPOINTS["global"]}"' if region == "global" else '""')
    if input.moonshot_model_name:
        text = m._toml_set(text, "moonshot_model_name", f'"{m._toml_escape(input.moonshot_model_name)}"')
    if input.nvidia_api_key:
        text = m._toml_set(text, "nvidia_api_key", f'"{m._toml_escape(input.nvidia_api_key)}"')
    if input.nvidia_text_model:
        text = m._toml_set(text, "nvidia_text_model", f'"{m._toml_escape(input.nvidia_text_model)}"')
    if input.llm_provider:
        text = m._toml_set(text, "cockpit_llm_provider", f'"{input.llm_provider}"')
        if input.llm_provider == "nvidia":
            # The Short's own script generator lives in Turbo, not here - point its "openai"
            # provider slot at the NIM gateway too, so both scripts come from the same model.
            nvidia_key = m._config_string(text, "nvidia_api_key")
            if not nvidia_key:
                raise HTTPException(status_code=409, detail="Configure a chave NVIDIA antes de trocar o provedor de IA.")
            text = m._toml_set(text, "llm_provider", '"openai"')
            text = m._toml_set(text, "openai_api_key", f'"{m._toml_escape(nvidia_key)}"')
            text = m._toml_set(text, "openai_base_url", f'"{m.NVIDIA_BASE_URL}"')
            text = m._toml_set(text, "openai_model_name", f'"{m._toml_escape(m._config_string(text, "nvidia_text_model") or m.NVIDIA_DEFAULT_TEXT_MODEL)}"')
        else:
            text = m._toml_set(text, "llm_provider", '"moonshot"')
    # Saving a different NVIDIA model used to update only the Gateway setting.  Keep
    # Turbo's OpenAI-compatible slot in sync even when the provider was already NVIDIA.
    if m._config_string(text, "cockpit_llm_provider") == "nvidia":
        nvidia_key = m._config_string(text, "nvidia_api_key")
        if not nvidia_key:
            raise HTTPException(status_code=409, detail="Configure a chave NVIDIA antes de usar este provedor.")
        text = m._toml_set(text, "llm_provider", '"openai"')
        text = m._toml_set(text, "openai_api_key", f'"{m._toml_escape(nvidia_key)}"')
        text = m._toml_set(text, "openai_base_url", f'"{m.NVIDIA_BASE_URL}"')
        text = m._toml_set(text, "openai_model_name", f'"{m._toml_escape(m._config_string(text, "nvidia_text_model") or m.NVIDIA_DEFAULT_TEXT_MODEL)}"')
    if input.pexels_api_key:
        text = m._toml_set(text, "pexels_api_keys", f'["{m._toml_escape(input.pexels_api_key)}"]')
    if input.pixabay_api_key:
        text = m._toml_set(text, "pixabay_api_keys", f'["{m._toml_escape(input.pixabay_api_key)}"]')
    if input.coverr_api_key:
        text = m._toml_set(text, "coverr_api_keys", f'["{m._toml_escape(input.coverr_api_key)}"]')
    if input.upload_post_api_key:
        text = m._toml_set(text, "upload_post_api_key", f'"{m._toml_escape(input.upload_post_api_key)}"')
    if input.upload_post_username:
        text = m._toml_set(text, "upload_post_username", f'"{m._toml_escape(input.upload_post_username)}"')
    config_path.write_text(text, encoding="utf-8")
    return {**m.provider_readiness(), "restart_required": turbo_changed, "turbo_pending_restart": turbo_changed}
