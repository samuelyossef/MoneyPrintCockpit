import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from gateway import auto_generation, main


class AutoGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        os.environ["APP_DATA_DIR"] = self.temporary.name
        main.DATA_DIR = Path(self.temporary.name)
        main.DATABASE = main.DATA_DIR / "cockpit.sqlite3"
        main.ADMIN_FILE = main.DATA_DIR / "secrets" / "admin.json"
        main.initialize_database()
        self.calls = []
        with main.database() as db:
            db.execute("INSERT INTO projects(id, name, niche, language, prompt_base, defaults_json, publish_json, created_at, updated_at) VALUES ('project', 'Projeto', 'Ciência', 'pt-BR', 'Prompt do projeto', '{}', '{}', ?, ?)", (main.now(), main.now()))
        self.add_channel("yt", "youtube", "pt-BR", "Canal português", "Prompt YouTube", 4)
        self.add_channel("tt", "tiktok", "en-US", "English channel", "TikTok prompt", 6)
        async def editorial(*args, **kwargs):
            return {"approved": True, "reason": "Roteiro revisado", "estimated_seconds": 60, "manual": False}
        async def curate(item_id, index, chunk, query, context):
            return {"index": index, "narration": chunk, "query": query, "candidates": [{"id": f"mock-{index}", "status": "approved"}], "selected": f"mock-{index}"}
        for target, replacement in (("review_script", editorial), ("curate_scene", curate), ("vision_model", lambda: ("base", "key", "model")), ("approved_materials", lambda item_id, scenes: [{"provider": "local", "url": f"mock-{scene['index']}.mp4", "duration": 5} for scene in scenes])):
            patcher = patch.object(auto_generation.specialists, target, new=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def add_channel(self, identifier, platform, language, name, prompt, paragraphs):
        profiles = main.default_channel_video_defaults(language)
        profiles["short"]["paragraph_number"] = paragraphs
        profiles["short"]["video_clip_duration"] = 5
        with main.database() as db:
            db.execute(
                "INSERT INTO channels(id, platform, name, status, niche, language, system_prompt, voice_name, video_defaults_json, created_at, updated_at) "
                "VALUES (?, ?, ?, 'connected', 'Natureza', ?, ?, ?, ?, ?, ?)",
                (identifier, platform, name, language, prompt, main.default_voice_for_language(language),
                 json.dumps(profiles), main.now(), main.now()),
            )

    def payload(self, **changes):
        return auto_generation.AutoGenerationInput(**{
            "format": "short", "channel_ids": ["yt", "tt"], "client_request_id": "request-0001", **changes,
        })

    def test_idempotency_and_channel_specific_precedence(self):
        request = self.payload(screen_system_prompt="Prompt Shorts", settings={"video_clip_duration": 8})
        first = auto_generation.create_auto_batch("project", request)
        second = auto_generation.create_auto_batch("project", request)
        self.assertEqual(first["batch_id"], second["batch_id"])
        with main.database() as db:
            rows = list(db.execute("SELECT context_json FROM generation_batch_items ORDER BY channel_id"))
        self.assertEqual(len(rows), 2)
        contexts = [json.loads(row["context_json"]) for row in rows]
        self.assertEqual({context["settings"]["video_clip_duration"] for context in contexts}, {8})
        self.assertEqual({context["settings"]["paragraph_number"] for context in contexts}, {4, 6})
        self.assertEqual({context["settings"]["video_language"] for context in contexts}, {"pt-BR", "en-US"})
        self.assertTrue(all("Prompt Shorts" in context["editorial"] for context in contexts))
        self.assertTrue(any("TikTok prompt" in context["editorial"] for context in contexts))
        with main.database() as db:
            db.execute("UPDATE channels SET status='disconnected' WHERE id='tt'")
        self.assertEqual(auto_generation.create_auto_batch("project", request)["batch_id"], first["batch_id"])
        with self.assertRaises(HTTPException) as raised:
            auto_generation.create_auto_batch("project", request.model_copy(update={"subject": "outro"}))
        self.assertEqual(raised.exception.status_code, 409)

    def test_no_channel_invalid_voice_and_duration_are_rejected(self):
        with self.assertRaises(Exception):
            self.payload(channel_ids=[])
        with self.assertRaises(HTTPException) as voice:
            auto_generation.create_auto_batch("project", self.payload(settings={"voice_name": "voz-errada"}))
        self.assertEqual(voice.exception.status_code, 422)
        with self.assertRaises(HTTPException) as duration:
            auto_generation.create_auto_batch("project", self.payload(settings={"paragraph_number": 7}))
        self.assertEqual(duration.exception.status_code, 422)

    def test_short_writer_repairs_57_second_draft(self):
        auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            context = json.loads(db.execute("SELECT context_json FROM generation_batch_items").fetchone()["context_json"])
        prompts = []
        async def fake_chat(prompt, **_kwargs):
            prompts.append(prompt)
            return json.dumps({"script": " ".join(["morning"] * (142 if len(prompts) == 1 else 158))})
        with patch.object(main, "llm_chat", new=fake_chat):
            script = asyncio.run(auto_generation._generate_script("Morning sunlight", "Explain circadian rhythm", context, ""))
        self.assertEqual(len(prompts), 2)
        self.assertIn("142 palavras", prompts[1])
        self.assertEqual(len(script.split()), 158)
        auto_generation._validate_script(script, context)

    def test_short_writer_extends_final_slightly_short_draft(self):
        auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            context = json.loads(db.execute("SELECT context_json FROM generation_batch_items").fetchone()["context_json"])
        prompts = []

        async def fake_json(prompt, **_kwargs):
            prompts.append(prompt)
            if '"addition"' in prompt:
                return {"addition": "That morning light also helps set a steadier bedtime."}
            return {"script": " ".join(["morning"] * 148)}

        with patch.object(main, "llm_json_object", new=fake_json):
            script = asyncio.run(auto_generation._generate_script("Morning sunlight", "Explain circadian rhythm", context, ""))
        self.assertEqual(len(prompts), 4)
        self.assertIn('"addition"', prompts[-1])
        self.assertTrue(script.endswith("steadier bedtime."))
        auto_generation._validate_script(script, context)

    def test_jenny_voice_uses_measured_faster_narration_rate(self):
        self.add_channel("en-short", "youtube", "en-US", "English Short", "Natural voice", 4)
        auto_generation.create_auto_batch("project", self.payload(channel_ids=["en-short"]))
        with main.database() as db:
            context = json.loads(db.execute("SELECT context_json FROM generation_batch_items").fetchone()["context_json"])
        self.assertEqual(context["settings"]["voice_name"], "en-US-JennyNeural")
        with self.assertRaises(HTTPException):
            auto_generation._validate_script(" ".join(["word"] * 160), context)
        auto_generation._validate_script(" ".join(["word"] * 178), context)
        self.assertAlmostEqual(main.narration_seconds(" ".join(["word"] * 160), 1, "en-US-JennyNeural"), 56.47, places=2)

    def test_two_shorts_can_target_distinct_channels_on_the_same_platform(self):
        self.add_channel("yt-2", "youtube", "en-US", "Segundo YouTube", "Outro prompt", 5)
        batch = auto_generation.create_auto_batch("project", self.payload(
            channel_ids=["yt", "yt-2"], client_request_id="same-platform-0001",
        ))
        with main.database() as db:
            rows = list(db.execute(
                "SELECT channel_id FROM generation_batch_items WHERE batch_id=? ORDER BY channel_id",
                (batch["batch_id"],),
            ))
        self.assertEqual([row["channel_id"] for row in rows], ["yt", "yt-2"])

    def test_two_channels_prepare_and_render_independently(self):
        batch = auto_generation.create_auto_batch("project", self.payload(
            subject="Ideia de base", requirements="Preserve a abertura", script="Texto base para adaptar.",
        ))
        sent = []

        async def fake_chat(prompt, **kwargs):
            self.calls.append(prompt)
            if '"topic"' in prompt:
                return json.dumps({"topic": "Tema " + ("TikTok" if "English channel" in prompt else "YouTube")})
            if "chave summary" in prompt:
                return json.dumps({"summary": "ideias principais"})
            if '"requirements"' in prompt:
                return json.dumps({"requirements": "Tom adequado ao canal"})
            if '"script": "narração"' in prompt:
                self.assertIn("Natureza", prompt)
                self.assertIn("Prompt do projeto", prompt)
                return json.dumps({"script": " ".join(["palavra"] * (240 if "en-US" in prompt else 150))})
            raise AssertionError(prompt)

        async def fake_turbo(method, path, **kwargs):
            if path == "/api/v1/videos":
                sent.append(kwargs["json"])
                return type("Response", (), {"json": lambda self: {"data": {"task_id": f"task-{len(sent)}"}}})()
            raise AssertionError(path)

        async def fake_terms(subject, script, clip_seconds, voice_rate=1.0, context=""):
            self.assertEqual(clip_seconds, 5)
            self.assertIn("Prompt Shorts", context) if "Prompt Shorts" in context else None
            return ["nature footage", "trees"], clip_seconds

        with patch.object(main, "llm_chat", new=fake_chat), patch.object(main, "turbo_request", new=fake_turbo), patch.object(main, "visual_terms", new=fake_terms):
            self.assertTrue(asyncio.run(auto_generation.process_next_item("worker")))
            self.assertTrue(asyncio.run(auto_generation.process_next_item("worker")))
        with main.database() as db:
            rows = list(db.execute("SELECT state, generated_json, job_id FROM generation_batch_items"))
            jobs = list(db.execute("SELECT id, generation_item_id FROM video_jobs"))
        self.assertEqual(len(sent), len(jobs))
        self.assertTrue(all(payload["video_source"] == "local" for payload in sent))
        self.assertTrue(all([material["url"] for material in payload["video_materials"]] == [f"mock-{index}.mp4" for index in range(len(payload["video_materials"]))] for payload in sent))
        self.assertEqual({row["state"] for row in rows}, {"submitted"})
        self.assertEqual({json.loads(row["generated_json"])["subject"] for row in rows}, {"Tema TikTok", "Tema YouTube"})
        self.assertTrue(all("Preserve a abertura" in json.loads(row["generated_json"])["requirements"] for row in rows))
        self.assertEqual({payload["video_language"] for payload in sent}, {"pt-BR", "en-US"})
        self.assertEqual({payload["video_clip_duration"] for payload in sent}, {5})
        self.assertEqual(len({job["generation_item_id"] for job in jobs}), 2)
        self.assertFalse(asyncio.run(auto_generation.process_next_item("worker")))

    def test_failed_channel_can_retry_without_repeating_success(self):
        batch = auto_generation.create_auto_batch("project", self.payload())
        with main.database() as db:
            items = list(db.execute("SELECT id FROM generation_batch_items ORDER BY id"))
            db.execute("UPDATE generation_batch_items SET state='failed', stage='failed', error='falha' WHERE id=?", (items[0]["id"],))
            db.execute("UPDATE generation_batch_items SET state='submitted', stage='rendering' WHERE id=?", (items[1]["id"],))
        auto_generation.retry_auto_item(batch["batch_id"], items[0]["id"])
        with main.database() as db:
            states = [row["state"] for row in db.execute("SELECT state FROM generation_batch_items ORDER BY id")]
        self.assertEqual(states, ["queued", "submitted"])
        with self.assertRaises(HTTPException):
            auto_generation.retry_auto_item(batch["batch_id"], items[1]["id"])

    def test_quality_error_retries_review_without_new_render(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            item_id = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()["id"]
            db.execute("INSERT INTO video_jobs(id, project_id, subject, request_json, state, generation_item_id, created_at, updated_at) "
                       "VALUES ('finished-job', 'project', 'Sunlight', '{}', 'completed', ?, ?, ?)", (item_id, main.now(), main.now()))
            db.execute("UPDATE generation_batch_items SET state='submitted', job_id='finished-job' WHERE id=?", (item_id,))
            db.execute("INSERT INTO generation_quality(item_id, job_id, state, report_json, updated_at) "
                       "VALUES (?, 'finished-job', 'error', ?, ?)",
                       (item_id, json.dumps({"videos": [{"approved": False, "reason": "56.4 s fora da duração"}]}), main.now()))
        self.assertIn("56.4 s", auto_generation.list_auto_batches()[0]["items"][0]["quality_reason"])
        self.assertEqual(auto_generation.retry_quality_review(batch["batch_id"], item_id), {"status": "pending"})
        with main.database() as db:
            quality = db.execute("SELECT state, report_json FROM generation_quality WHERE item_id=?", (item_id,)).fetchone()
            jobs = db.execute("SELECT COUNT(*) FROM video_jobs WHERE generation_item_id=?", (item_id,)).fetchone()[0]
        self.assertEqual((quality["state"], quality["report_json"], jobs), ("pending", "{}", 1))
        with self.assertRaises(HTTPException) as raised:
            auto_generation.retry_quality_review(batch["batch_id"], item_id)
        self.assertEqual(raised.exception.status_code, 409)

    def test_rate_limited_final_review_waits_before_retry(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            item_id = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()["id"]
            db.execute("UPDATE generation_batch_items SET generated_json=? WHERE id=?", (json.dumps({"scenes": [], "script": "sunlight"}), item_id))
            db.execute("INSERT INTO video_jobs(id, project_id, subject, request_json, state, generation_item_id, created_at, updated_at) "
                       "VALUES ('finished-job', 'project', 'Sunlight', '{}', 'completed', ?, ?, ?)", (item_id, main.now(), main.now()))
            db.execute("INSERT INTO generation_quality(item_id, job_id, state, updated_at) "
                       "VALUES (?, 'finished-job', 'pending', ?)", (item_id, main.now()))
            db.execute("INSERT INTO artifacts(id, job_id, kind, remote_path, filename, created_at) "
                       "VALUES ('artifact', 'finished-job', 'video', 'final.mp4', 'final.mp4', ?)", (main.now(),))

        async def download(_remote, _path):
            return None

        async def rate_limited(*_args):
            raise HTTPException(status_code=502, detail="O modelo de visão configurado não avaliou a imagem: HTTPStatusError HTTP 429.")

        with patch.object(main, "download_turbo_file", new=download), \
             patch.object(auto_generation.specialists, "inspect_final_video", new=rate_limited):
            self.assertTrue(asyncio.run(auto_generation.review_next_completed_job()))
            self.assertFalse(asyncio.run(auto_generation.review_next_completed_job()))
        with main.database() as db:
            quality = db.execute("SELECT state, report_json, updated_at FROM generation_quality WHERE item_id=?", (item_id,)).fetchone()
        self.assertEqual(quality["state"], "pending", quality["report_json"])
        self.assertEqual(json.loads(quality["report_json"])["transient_attempts"], 1)
        self.assertGreater(quality["updated_at"], main.now())

    def test_long_video_uses_target_minutes_and_voice_rate(self):
        request = auto_generation.AutoGenerationInput(
            format="long", channel_ids=["yt"], client_request_id="long-request-0001",
            settings={"target_minutes": 5, "voice_rate": 1.2, "video_clip_duration": 8},
        )
        auto_generation.create_auto_batch("project", request)
        observed = {}

        async def fake_chat(prompt, **kwargs):
            if '"topic"' in prompt:
                return json.dumps({"topic": "Tema longo"})
            if '"requirements"' in prompt:
                return json.dumps({"requirements": "Estrutura de cinco minutos"})
            raise AssertionError(prompt)

        async def fake_long(input):
            observed["minutes"] = input.minutes
            observed["voice_rate"] = input.voice_rate
            observed["system_prompt"] = input.system_prompt
            return {"script": " ".join(["narração"] * 900)}

        async def fake_terms(subject, script, clip_seconds, voice_rate=1.0, context=""):
            observed["clip_seconds"] = clip_seconds
            observed["terms_voice_rate"] = voice_rate
            return ["forest"], clip_seconds

        async def fake_turbo(method, path, **kwargs):
            observed["render"] = kwargs["json"]
            return type("Response", (), {"json": lambda self: {"data": {"task_id": "long-task"}}})()

        with patch.object(main, "llm_chat", new=fake_chat), patch.object(main, "generate_long_script", new=fake_long), \
                patch.object(main, "visual_terms", new=fake_terms), patch.object(main, "turbo_request", new=fake_turbo):
            asyncio.run(auto_generation.process_next_item("worker"))
        self.assertEqual((observed["minutes"], observed["voice_rate"], observed["clip_seconds"], observed["terms_voice_rate"]), (5, 1.2, 8, 1.2))
        self.assertIn("Prompt YouTube", observed["system_prompt"])
        self.assertEqual(observed["render"]["video_clip_duration"], 8)
        with main.database() as db:
            self.assertEqual(db.execute("SELECT state FROM generation_batch_items").fetchone()["state"], "submitted")

    def test_one_channel_preserves_manual_fields(self):
        words = " ".join(["escrito"] * 150)
        request = self.payload(channel_ids=["yt"], subject="Meu tema", requirements="Minha regra", script=words, terms="my footage")
        auto_generation.create_auto_batch("project", request)

        async def forbidden(*args, **kwargs):
            raise AssertionError("Campos manuais não exigem IA")

        async def fake_turbo(method, path, **kwargs):
            self.assertEqual(kwargs["json"]["video_terms"], "my footage")
            return type("Response", (), {"json": lambda self: {"data": {"task_id": "manual-task"}}})()

        with patch.object(main, "llm_chat", new=forbidden), patch.object(main, "visual_terms", new=forbidden), patch.object(main, "turbo_request", new=fake_turbo):
            asyncio.run(auto_generation.process_next_item("worker"))
        with main.database() as db:
            item = db.execute("SELECT state, generated_json FROM generation_batch_items").fetchone()
        self.assertEqual(item["state"], "submitted")
        generated = json.loads(item["generated_json"])
        self.assertEqual({key: generated[key] for key in ("subject", "requirements", "script", "terms")}, {"subject": "Meu tema", "requirements": "Minha regra", "script": words, "terms": "my footage"})
        self.assertTrue(generated["editorial_review"]["approved"])
        self.assertTrue(generated["scenes"])

    def test_manual_terms_cannot_bypass_footage_search_limit(self):
        request = self.payload(channel_ids=["tt"], subject="Tema", requirements="Regra",
                               script=" ".join(["spoken"] * 210), terms="manual footage",
                               settings={"video_clip_duration": 1})
        auto_generation.create_auto_batch("project", request)

        async def forbidden(*args, **kwargs):
            raise AssertionError("O render não deve começar acima do limite")

        with patch.object(main, "turbo_request", new=forbidden):
            asyncio.run(auto_generation.process_next_item("worker"))
        with main.database() as db:
            item = db.execute("SELECT state, error, job_id FROM generation_batch_items").fetchone()
        self.assertEqual(item["state"], "failed")
        self.assertIn("limite de 80", item["error"])
        self.assertIsNone(item["job_id"])

    def test_expired_pre_submission_lease_resumes_saved_stages(self):
        auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            item_id = db.execute("SELECT id FROM generation_batch_items").fetchone()["id"]
            db.execute("UPDATE generation_batch_items SET state='processing', stage='script_done', generated_json=?, lease_owner='old-worker', lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
                       (json.dumps({"subject": "Tema salvo", "requirements": "Regra salva", "script": " ".join(["texto"] * 150)}), item_id))
        claimed = auto_generation.claim_next_item("new-worker")
        self.assertEqual(claimed["id"], item_id)
        self.assertEqual(json.loads(claimed["generated_json"])["subject"], "Tema salvo")
        with main.database() as db:
            row = db.execute("SELECT state, lease_owner FROM generation_batch_items WHERE id=?", (item_id,)).fetchone()
        self.assertEqual(tuple(row), ("processing", "new-worker"))

    def test_expired_submission_reconciles_existing_job_without_new_render(self):
        auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            item_id = db.execute("SELECT id FROM generation_batch_items").fetchone()["id"]
            db.execute("INSERT INTO video_jobs(id, project_id, subject, script, request_json, state, created_at, updated_at, format, generation_item_id) VALUES ('saved-job', 'project', 'Tema', 'Texto', '{}', 'queued', ?, ?, 'short', ?)",
                       (main.now(), main.now(), item_id))
            db.execute("UPDATE generation_batch_items SET state='processing', stage='submitting', submission_started_at=?, lease_owner='old-worker', lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (main.now(), item_id))
        self.assertIsNone(auto_generation.claim_next_item("new-worker"))
        with main.database() as db:
            row = db.execute("SELECT state, job_id FROM generation_batch_items WHERE id=?", (item_id,)).fetchone()
        self.assertEqual(tuple(row), ("submitted", "saved-job"))

    def test_batch_read_reports_per_channel_result_and_partial_failure(self):
        batch = auto_generation.create_auto_batch("project", self.payload())
        with main.database() as db:
            items = list(db.execute("SELECT id FROM generation_batch_items ORDER BY id"))
            db.execute("INSERT INTO video_jobs(id, project_id, subject, script, request_json, state, created_at, updated_at, format, generation_item_id) VALUES ('done-job', 'project', 'Tema', 'Texto', '{}', 'completed', ?, ?, 'short', ?)",
                       (main.now(), main.now(), items[0]["id"]))
            db.execute("UPDATE generation_batch_items SET state='submitted', stage='rendering', job_id='done-job', generated_json=? WHERE id=?",
                       (json.dumps({"subject": "Tema pronto", "requirements": "Regra", "script": "Texto", "terms": "forest"}), items[0]["id"]))
            db.execute("UPDATE generation_batch_items SET state='failed', stage='failed', error='Falha do provedor' WHERE id=?", (items[1]["id"],))

        async def no_reconcile(_job_id):
            return {}

        with patch.object(main, "reconcile_job", new=no_reconcile):
            result = asyncio.run(auto_generation.get_auto_batch(batch["batch_id"]))
        self.assertEqual(result["status"], "partial_failure")
        self.assertEqual({item["state"] for item in result["items"]}, {"submitted", "failed"})
        self.assertTrue(any(item["generated"].get("subject") == "Tema pronto" for item in result["items"]))
        self.assertTrue(any(item["error"] == "Falha do provedor" for item in result["items"]))
