"""Isolated local UI smoke server; all model and Turbo responses are simulated."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

import uvicorn


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="moneyprint-auto-ui-") as temporary:
        os.environ["APP_DATA_DIR"] = temporary
        from gateway import auto_generation, main as gateway, visual_specialists as specialists

        gateway.DATA_DIR = Path(temporary)
        gateway.DATABASE = gateway.DATA_DIR / "cockpit.sqlite3"
        gateway.ADMIN_FILE = gateway.DATA_DIR / "secrets" / "admin.json"
        gateway.initialize_database()
        gateway.ADMIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        gateway.ADMIN_FILE.write_text(json.dumps({
            "name": "Teste local", "email": "teste@local.invalid", **gateway.password_hash("teste-local-123"),
        }), encoding="utf-8")
        with gateway.database() as db:
            for identifier, platform, language, name, prompt, seconds in (
                ("test-youtube", "youtube", "pt-BR", "Teste YouTube", "Tom educativo", 5),
                ("test-tiktok", "tiktok", "en-US", "Test TikTok", "Conversational tone", 6),
            ):
                profiles = gateway.default_channel_video_defaults(language)
                profiles["short"]["paragraph_number"] = seconds
                db.execute(
                    "INSERT INTO channels(id, platform, name, status, niche, language, system_prompt, voice_name, "
                    "video_defaults_json, created_at, updated_at) VALUES (?, ?, ?, 'connected', 'Natureza', ?, ?, ?, ?, ?, ?)",
                    (identifier, platform, name, language, prompt, gateway.default_voice_for_language(language),
                     json.dumps(profiles), gateway.now(), gateway.now()),
                )

        async def fake_chat(prompt: str, **_kwargs: object) -> str:
            if '"topic"' in prompt:
                return json.dumps({"topic": "Wildlife facts" if "Test TikTok" in prompt else "Fatos sobre a natureza"})
            if '"requirements"' in prompt:
                return json.dumps({"requirements": "Clear opening and concrete examples" if "Test TikTok" in prompt else "Abertura clara e exemplos concretos"})
            if '"script": "narração"' in prompt:
                words = 210 if "en-US" in prompt else 180
                return json.dumps({"script": " ".join(["wildlife" if "en-US" in prompt else "natureza"] * words)})
            if "Escreva um roteiro completo de vídeo de YouTube" in prompt:
                return json.dumps({"title": "Fatos sobre a natureza", "script": " ".join(["natureza"] * 1200)})
            if '"terms"' in prompt:
                return json.dumps({"terms": [f"nature footage {index}" for index in range(30)]})
            raise RuntimeError("Prompt inesperado no teste isolado")

        class FakeResponse:
            status_code = 200
            text = ""

            def __init__(self, payload: dict):
                self.payload = payload

            def json(self) -> dict:
                return self.payload

        async def fake_turbo(method: str, path: str, **_kwargs: object) -> FakeResponse:
            if path == "/ping":
                return FakeResponse({"status": "ok"})
            if method == "POST" and path == "/api/v1/videos":
                return FakeResponse({"data": {"task_id": str(uuid4())}})
            if method == "GET" and path.startswith("/api/v1/tasks/"):
                task_id = path.rsplit("/", 1)[-1]
                return FakeResponse({"data": {"state": "done", "progress": 100, "videos": [f"{task_id}/final-1.mp4"]}})
            raise RuntimeError(f"Turbo inesperado no teste isolado: {method} {path}")

        gateway.llm_chat = fake_chat
        gateway.turbo_request = fake_turbo
        specialists.vision_model = lambda: ("mock", "mock", "mock")

        async def fake_editorial(script, subject, requirements, context, *, manual):
            from gateway.editorial_quality import narration_diagnostics
            return {"approved": True, "reason": "Roteiro coerente no teste simulado", "estimated_seconds": round(gateway.narration_seconds(script, context["settings"]["voice_rate"])), "manual": manual, "narration": narration_diagnostics(script)}

        async def fake_curator(item_id, index, chunk, query, context):
            folder = specialists.material_root(item_id)
            folder.mkdir(parents=True, exist_ok=True)
            filename = f"scene{index:03d}option0.jpg"
            (folder / filename).write_bytes(__import__("base64").b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAT/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k="))
            return {"index": index, "narration": chunk, "query": query, "expected": chunk, "selected": f"mock-{index}", "candidates": [{"id": f"mock-{index}", "provider": "pexels", "kind": "image", "filename": filename, "source_page": "https://pexels.com", "status": "approved", "reason": "Cena simulada aprovada", "duration": 5}]}

        async def fake_final_review(path, scenes, settings, script):
            return {"approved": True, "checks": {"duration": True, "aspect": True, "audio": True, "black_frames": True}, "observations": [{"scene": scene["index"] + 1, "approved": True, "reason": "Quadro simulado"} for scene in scenes], "reason": "Montagem simulada aprovada"}

        async def fake_download(_remote, target):
            target.write_bytes(b"fake-video")

        specialists.review_script = fake_editorial
        specialists.curate_scene = fake_curator
        specialists.inspect_final_video = fake_final_review
        gateway.download_turbo_file = fake_download

        async def work() -> None:
            while True:
                try:
                    if not await auto_generation.process_next_item("ui-harness"):
                        with gateway.database() as db:
                            jobs = [row["job_id"] for row in db.execute("SELECT job_id FROM generation_quality WHERE state='pending'")]
                        for job_id in jobs:
                            await gateway.reconcile_job(job_id)
                        if not await auto_generation.review_next_completed_job():
                            await asyncio.sleep(0.2)
                except Exception:
                    await asyncio.sleep(0.5)

        threading.Thread(target=lambda: asyncio.run(work()), daemon=True).start()
        uvicorn.run(gateway.app, host="127.0.0.1", port=3001, lifespan="off", log_level="warning")


if __name__ == "__main__":
    main()
