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
        async def proofread(script, language):
            return {"script": script, "changed": False, "corrections": []}
        for target, replacement in (("review_script", editorial), ("curate_scene", curate), ("vision_model", lambda: ("base", "key", "model")), ("approved_materials", lambda item_id, scenes: [{"provider": "local", "url": f"mock-{scene['index']}.mp4", "duration": 5} for scene in scenes]), ("proofread_script", proofread)):
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

    def test_channel_persona_and_image_source_reach_generation_context(self):
        config = main.DATA_DIR / "moneyprinter" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('nvidia_api_key = "test-key"\n', encoding="utf-8")
        with main.database() as db:
            db.execute("UPDATE channels SET persona=?, visual_style=?, image_source=? WHERE id='yt'", ("Narrador direto", "cinematic blue", "ai_images"))
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"], client_request_id="request-image"))
        with main.database() as db:
            row = db.execute("SELECT context_json FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()
        context = json.loads(row["context_json"])
        self.assertIn("Narrador direto", context["editorial"])
        self.assertEqual(context["channel"]["visual_style"], "cinematic blue")
        self.assertEqual(context["settings"]["video_source"], "ai_images")

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
        budget = main.script_duration_budget("short", context["settings"]["paragraph_number"],
                                             context["settings"]["voice_rate"], context["settings"]["voice_name"])
        async def fake_chat(prompt, **_kwargs):
            prompts.append(prompt)
            count = 142 if len(prompts) < 3 else 158
            return json.dumps({"script": " ".join(["morning"] * count)})
        with patch.object(main, "llm_chat", new=fake_chat):
            script = asyncio.run(auto_generation._generate_script("Morning sunlight", "Explain circadian rhythm", context, ""))
        self.assertEqual(len(prompts), 3)
        self.assertIn(str(round(budget["minimum_words"] + (budget["maximum_words"] - budget["minimum_words"]) * 0.8)), prompts[1])
        self.assertIn(f"{budget['maximum_words']} palavras", prompts[2])
        self.assertEqual(len(script.split()), 158)
        auto_generation._validate_script(script, context)

    def test_editorial_correction_does_not_reuse_off_topic_script_as_source(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"], subject="jejum intermitente"))
        with main.database() as db:
            context = json.loads(db.execute("SELECT context_json FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()["context_json"])
        budget = main.script_duration_budget("short", context["settings"]["paragraph_number"],
                                             context["settings"]["voice_rate"], context["settings"]["voice_name"])
        observed = {}
        async def fake_json(prompt, **_kwargs):
            observed["prompt"] = prompt
            return {"script": " ".join(["jejum"] * budget["maximum_words"])}
        with patch.object(main, "llm_json_object", new=fake_json):
            script = asyncio.run(auto_generation._generate_script(
                "jejum intermitente", "Explique com precisão", context, "",
                editorial_feedback="O roteiro anterior falava de sono, não do tema pedido.",
            ))
        self.assertIn("reescreva o roteiro do zero", observed["prompt"].lower())
        self.assertIn("jejum intermitente", observed["prompt"].lower())
        self.assertNotIn("acordar sob a luz da manhã", observed["prompt"].lower())
        self.assertTrue(script.startswith("jejum"))

    def test_off_topic_script_is_rewritten_from_scratch_until_editor_approves(self):
        batch = auto_generation.create_auto_batch("project", self.payload(
            channel_ids=["yt"], subject="jejum intermitente",
            script="Roteiro anterior sobre higiene do sono e ciclo circadiano.", script_is_manual=False))
        generation_calls = []

        async def fake_chat(prompt, **_kwargs):
            if '"requirements"' in prompt:
                return json.dumps({"requirements": "Explique jejum intermitente com precisão e sem alegações médicas absolutas."})
            raise AssertionError(prompt)

        async def fake_script(subject, requirements, context, source_summary, *, editorial_feedback=""):
            generation_calls.append((subject, source_summary, editorial_feedback))
            self.assertTrue(editorial_feedback)
            self.assertEqual(source_summary, "")
            self.assertIn("jejum intermitente", editorial_feedback)
            return ("Segunda tentativa ainda sobre sono." if len(generation_calls) == 1
                    else "Jejum intermitente alterna períodos definidos para comer e para jejuar.")

        review_calls = []
        async def fake_review(script, subject, requirements, context, *, manual):
            review_calls.append(script)
            approved = "jejum intermitente" in script.lower()
            return {"approved": approved, "reason": "O roteiro trata de higiene do sono, tema diferente de jejum intermitente." if not approved else "Tema correto."}

        async def fake_terms(*_args, **_kwargs):
            return ["intermittent fasting"], 5

        async def fake_turbo(*_args, **_kwargs):
            return type("Response", (), {"json": lambda self: {"data": {"task_id": "editorial-repair-task"}}})()

        with patch.object(main, "llm_chat", new=fake_chat), \
             patch.object(auto_generation, "_generate_script", new=fake_script), \
             patch.object(auto_generation.specialists, "review_script", new=fake_review), \
             patch.object(main, "visual_terms", new=fake_terms), \
             patch.object(main, "visual_term_count", return_value=1), \
             patch.object(main, "split_script", side_effect=lambda script, _slots: [script]), \
             patch.object(main, "turbo_request", new=fake_turbo):
            self.assertTrue(asyncio.run(auto_generation.process_next_item("editorial-worker")))

        self.assertEqual(len(generation_calls), 2)
        self.assertTrue(all(call[1] == "" for call in generation_calls))
        self.assertEqual(len(review_calls), 3)
        with main.database() as db:
            item = db.execute("SELECT state, generated_json FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()
        generated = json.loads(item["generated_json"])
        self.assertEqual(item["state"], "submitted")
        self.assertTrue(generated["editorial_review"]["approved"])
        self.assertEqual(generated["editorial_repair_attempts"], 2)
        self.assertIn("jejum intermitente", generated["script"].lower())

    def test_explicit_regeneration_can_replace_legacy_rejected_script(self):
        batch = auto_generation.create_auto_batch("project", self.payload(
            channel_ids=["yt"], subject="jejum intermitente", script="old user text"))
        with main.database() as db:
            item_id = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()["id"]
            rejected = {"subject": "jejum intermitente", "requirements": "Fale do protocolo 16/8.",
                        "script": "Roteiro sobre sono", "editorial_review": {"approved": False,
                        "manual": True, "reason": "O roteiro trata de sono, não de jejum intermitente."}}
            db.execute("UPDATE generation_batch_items SET state='paused', generated_json=? WHERE id=?",
                       (json.dumps(rejected), item_id))
        queued = auto_generation.regenerate_paused_script(batch["batch_id"], item_id)
        self.assertEqual(queued["status"], "queued")
        with main.database() as db:
            row = db.execute("SELECT state, generated_json FROM generation_batch_items WHERE id=?", (item_id,)).fetchone()
        generated = json.loads(row["generated_json"])
        self.assertEqual(row["state"], "queued")
        self.assertTrue(generated["force_script_regeneration"])
        self.assertTrue(generated["editorial_script_regenerated_from_review"])
        self.assertEqual(generated["editorial_rejected_script"], "Roteiro sobre sono")
        self.assertNotIn("script", generated)
        generated.update({"script": "Jejum intermitente usa períodos definidos de alimentação e jejum.",
                          "editorial_review": {"approved": True}, "terms": "sleep, sunlight",
                          "scenes": [{"index": 0, "selected": "old-sleep-clip"}]})
        with main.database() as db:
            db.execute("UPDATE generation_batch_items SET state='paused', generated_json=? WHERE id=?",
                       (json.dumps(generated), item_id))
        auto_generation.retry_auto_item(batch["batch_id"], item_id)
        with main.database() as db:
            resumed = db.execute("SELECT generated_json FROM generation_batch_items WHERE id=?", (item_id,)).fetchone()
        resumed_generated = json.loads(resumed["generated_json"])
        self.assertTrue(resumed_generated["editorial_script_regenerated_from_review"])
        self.assertNotIn("terms", resumed_generated)
        self.assertNotIn("scenes", resumed_generated)

    def test_short_writer_extends_final_slightly_short_draft(self):
        auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            context = json.loads(db.execute("SELECT context_json FROM generation_batch_items").fetchone()["context_json"])
        prompts = []

        async def fake_json(prompt, **_kwargs):
            prompts.append(prompt)
            return {"script": " ".join(["morning"] * (148 if len(prompts) == 1 else 158))}

        with patch.object(main, "llm_json_object", new=fake_json):
            script = asyncio.run(auto_generation._generate_script("Morning sunlight", "Explain circadian rhythm", context, ""))
        self.assertEqual(len(prompts), 2)
        self.assertIn("148 palavras", prompts[-1])
        self.assertEqual(len(script.split()), 158)
        auto_generation._validate_script(script, context)

    def test_short_writer_fails_clearly_after_two_duration_corrections(self):
        auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            context = json.loads(db.execute("SELECT context_json FROM generation_batch_items").fetchone()["context_json"])
        calls = []

        async def always_short(_prompt, **_kwargs):
            calls.append(1)
            return {"script": " ".join(["morning"] * 142)}

        with patch.object(main, "llm_json_object", new=always_short):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(auto_generation._generate_script("Morning sunlight", "Explain circadian rhythm", context, ""))
        self.assertEqual(len(calls), 3)
        self.assertIn("57", str(raised.exception.detail))
        self.assertIn("entre 60 e 66 s", str(raised.exception.detail))

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

    def test_ai_script_adapts_to_both_channels_but_forced_regeneration_ignores_it(self):
        supplied = "Roteiro de IA sobre a luz da manhã."
        batch = auto_generation.create_auto_batch("project", self.payload(
            script=supplied, script_is_manual=False,
        ))
        generated = {"subject": "Luz", "requirements": "Explicar a luz da manhã"}
        with main.database() as db:
            db.execute("UPDATE generation_batch_items SET state='processing', lease_owner='script-test', generated_json=? WHERE batch_id=?",
                       (json.dumps(generated), batch["batch_id"]))
            items = [dict(row) for row in db.execute(
                "SELECT * FROM generation_batch_items WHERE batch_id=? ORDER BY channel_id", (batch["batch_id"],))]

        class StopAtScript(Exception):
            pass

        summarized = []
        adapted = []

        async def summarize(script):
            summarized.append(script)
            return "ideias da luz matinal" if script else ""

        async def write_script(subject, requirements, context, source_summary, *, editorial_feedback=""):
            adapted.append((context["channel"]["id"], source_summary, editorial_feedback))
            raise StopAtScript

        with patch.object(auto_generation, "_source_summary", new=summarize), \
             patch.object(auto_generation, "_generate_script", new=write_script):
            for item in items:
                with self.assertRaises(StopAtScript):
                    asyncio.run(auto_generation._prepare_and_submit(item, "script-test"))

            with main.database() as db:
                db.execute("UPDATE generation_batch_items SET generated_json=? WHERE id=?",
                           (json.dumps({**generated, "force_script_regeneration": True,
                                        "force_script_regeneration_feedback": "O rascunho antigo saiu do tema."}), items[0]["id"]))
                forced = dict(db.execute("SELECT * FROM generation_batch_items WHERE id=?", (items[0]["id"],)).fetchone())
            with self.assertRaises(StopAtScript):
                asyncio.run(auto_generation._prepare_and_submit(forced, "script-test"))

        self.assertEqual(summarized, [supplied, supplied, ""])
        self.assertEqual({channel for channel, _, _ in adapted[:2]}, {"yt", "tt"})
        self.assertTrue(all(summary == "ideias da luz matinal" and not feedback
                            for _, summary, feedback in adapted[:2]))
        self.assertEqual(adapted[2], (items[0]["channel_id"], "", "O rascunho antigo saiu do tema."))

    def test_two_channels_prepare_and_render_independently(self):
        auto_generation.create_auto_batch("project", self.payload(
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

    def test_clear_completed_reviews_hides_only_approved_batches_and_keeps_jobs(self):
        completed = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        pending = auto_generation.create_auto_batch("project", self.payload(channel_ids=["tt"], client_request_id="request-pending"))
        with main.database() as db:
            completed_item = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (completed["batch_id"],)).fetchone()["id"]
            pending_item = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (pending["batch_id"],)).fetchone()["id"]
            timestamp = main.now()
            db.execute("UPDATE generation_batch_items SET state='submitted' WHERE id=?", (completed_item,))
            db.execute("INSERT INTO generation_quality(item_id, state, report_json, updated_at) VALUES (?, 'approved', '{}', ?)", (completed_item, timestamp))
            db.execute("UPDATE generation_batch_items SET state='paused' WHERE id=?", (pending_item,))
        self.assertEqual(auto_generation.clear_completed_reviews(), {"cleared": 1})
        self.assertEqual([batch["batch_id"] for batch in auto_generation.list_auto_batches()], [pending["batch_id"]])
        with main.database() as db:
            self.assertIsNotNone(db.execute("SELECT archived_at FROM generation_batches WHERE id=?", (completed["batch_id"],)).fetchone()["archived_at"])
            self.assertEqual(db.execute("SELECT state FROM generation_batch_items WHERE id=?", (completed_item,)).fetchone()["state"], "submitted")
        self.assertEqual(auto_generation.clear_completed_reviews(), {"cleared": 0})

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

    def test_final_duration_review_queues_one_repair_and_exposes_current_item(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            item = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()
            item_id = item["id"]
            generated = {"subject": "Luz", "requirements": "Explicar com clareza", "script": "luz " * 160,
                         "terms": "sunlight", "scenes": [{"index": 0, "narration": "luz", "query": "sunlight",
                         "selected": "asset", "candidates": [{"id": "asset", "status": "approved", "filename": "scene000option0.jpg"}]}]}
            db.execute("INSERT INTO video_jobs(id, project_id, subject, request_json, state, generation_item_id, created_at, updated_at) "
                       "VALUES ('job', 'project', 'Luz', '{}', 'completed', ?, ?, ?)", (item_id, main.now(), main.now()))
            db.execute("UPDATE generation_batch_items SET generated_json=?, state='submitted', job_id='job' WHERE id=?",
                       (json.dumps(generated), item_id))
            db.execute("INSERT INTO artifacts(id, job_id, kind, remote_path, filename, created_at) "
                       "VALUES ('artifact', 'job', 'video', 'final.mp4', 'final.mp4', ?)", (main.now(),))
            db.execute("INSERT INTO generation_quality(item_id, job_id, state, updated_at) VALUES (?, 'job', 'pending', ?)",
                       (item_id, main.now()))
        async def download(_remote, _path):
            return None
        async def inspect(*_args):
            return {"approved": False, "checks": {"duration": False, "aspect": True, "audio": True},
                    "failed_checks": ["duration"], "measurements": {"duration_seconds": 56.4},
                    "observations": [], "reason": "O vídeo final tem 56.4 s; exige 60 a 66 s."}
        async def no_reconcile(_job_id):
            return {}
        with patch.object(main, "download_turbo_file", new=download), \
             patch.object(auto_generation.specialists, "inspect_final_video", new=inspect), \
             patch.object(main, "reconcile_job", new=no_reconcile):
            self.assertTrue(asyncio.run(auto_generation.review_next_completed_job()))
            result = asyncio.run(auto_generation.get_auto_batch(batch["batch_id"]))
        with main.database() as db:
            root_quality = db.execute("SELECT state FROM generation_quality WHERE item_id=?", (item_id,)).fetchone()["state"]
            child = db.execute("SELECT id, batch_id, repair_attempt, repair_root_item_id FROM generation_batch_items WHERE repair_parent_item_id=?", (item_id,)).fetchone()
        self.assertEqual(root_quality, "repairing")
        self.assertEqual((child["repair_attempt"], child["repair_root_item_id"]), (1, item_id))
        self.assertEqual(result["status"], "processing")
        self.assertEqual((result["items"][0]["id"], result["items"][0]["active_batch_id"]), (child["id"], child["batch_id"]))
        self.assertFalse(asyncio.run(auto_generation.review_next_completed_job()))
        with main.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM generation_batch_items WHERE repair_root_item_id=?", (item_id,)).fetchone()[0], 1)
            db.execute("INSERT INTO video_jobs(id, project_id, subject, request_json, state, generation_item_id, created_at, updated_at) "
                       "VALUES ('repaired-job', 'project', 'Luz', '{}', 'completed', ?, ?, ?)", (child["id"], main.now(), main.now()))
            db.execute("UPDATE generation_batch_items SET generated_json=?, state='submitted', job_id='repaired-job' WHERE id=?",
                       (json.dumps(generated), child["id"]))
            db.execute("INSERT INTO artifacts(id, job_id, kind, remote_path, filename, created_at) "
                       "VALUES ('repaired-artifact', 'repaired-job', 'video', 'repaired.mp4', 'repaired.mp4', ?)", (main.now(),))
            db.execute("INSERT INTO generation_quality(item_id, job_id, state, updated_at) VALUES (?, 'repaired-job', 'pending', ?)",
                       (child["id"], main.now()))
        async def approved(*_args):
            return {"approved": True, "checks": {"duration": True}, "failed_checks": [],
                    "measurements": {"duration_seconds": 63}, "observations": [], "reason": "Aprovado"}
        with patch.object(main, "download_turbo_file", new=download), \
             patch.object(auto_generation.specialists, "inspect_final_video", new=approved), \
             patch.object(main, "reconcile_job", new=no_reconcile):
            self.assertTrue(asyncio.run(auto_generation.review_next_completed_job()))
            finished = asyncio.run(auto_generation.get_auto_batch(batch["batch_id"]))
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["items"][0]["job_id"], "repaired-job")

    def test_duration_repair_uses_rendered_seconds_and_preserves_selected_voice(self):
        context = {"format": "short", "settings": {"paragraph_number": 4, "voice_rate": 1.0,
                   "voice_name": "en-US-JennyNeural", "video_language": "en-US"}, "editorial": "Natural tone"}
        generated = {"subject": "Sunlight", "requirements": "Explain accurately", "repair_previous_script": "sun " * 160,
                     "repair_plan": {"duration_seconds": 56.4}}
        async def respond(prompt, **_kwargs):
            self.assertIn("160 palavras", prompt)
            self.assertIn("en-US-JennyNeural", prompt)
            return {"script": "sun " * 179}
        with patch.object(main, "llm_json_object", new=respond):
            script = asyncio.run(auto_generation._repair_duration_script(generated, context))
        self.assertEqual(len(script.split()), 179)
        self.assertGreaterEqual(main.narration_seconds(script, 1.0, "en-US-JennyNeural"), 60)

    def test_duration_repair_corrects_provider_reply_that_is_still_too_short(self):
        context = {"format": "short", "settings": {"paragraph_number": 4, "voice_rate": 1.0,
                   "voice_name": "en-US-JennyNeural", "video_language": "en-US"}, "editorial": "Natural tone"}
        generated = {"subject": "Sunlight", "requirements": "Explain accurately", "repair_previous_script": "sun " * 160,
                     "repair_plan": {"duration_seconds": 56.4}}
        prompts = []
        async def respond(prompt, **_kwargs):
            prompts.append(prompt)
            return {"script": "sun " * (168 if len(prompts) < 3 else 179)}
        with patch.object(main, "llm_json_object", new=respond):
            script = asyncio.run(auto_generation._repair_duration_script(generated, context))
        self.assertEqual(len(prompts), 3)
        self.assertIn("59.3s", prompts[1])
        budget = main.script_duration_budget("short", 4, 1.0, "en-US-JennyNeural")
        self.assertIn(f"{budget['maximum_words']} palavras", prompts[1])
        self.assertGreaterEqual(main.narration_seconds(script, 1.0, "en-US-JennyNeural"), 60)

    def test_manual_script_is_not_changed_by_automatic_duration_repair(self):
        report = {"videos": [{"approved": False, "failed_checks": ["duration"],
                  "measurements": {"duration_seconds": 56.4}, "reason": "Curto"}]}
        generated = {"script": "Manual script", "scenes": []}
        self.assertIsNone(auto_generation._repair_decision(report, generated, manual_script=True))

    def test_single_channel_duration_repair_respects_script_origin(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                batch = auto_generation.create_auto_batch("project", self.payload(
                    channel_ids=["yt"], client_request_id=f"script-origin-{manual}",
                    script="luz " * 160, script_is_manual=manual))
                report = {"videos": [{"approved": False, "failed_checks": ["duration"],
                           "measurements": {"duration_seconds": 56.4}, "reason": "Curto"}]}
                with main.database() as db:
                    item_id = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?",
                                         (batch["batch_id"],)).fetchone()["id"]
                    db.execute("UPDATE generation_batch_items SET generated_json=?, state='submitted' WHERE id=?",
                               (json.dumps({"subject": "Luz", "requirements": "Correto", "script": "luz " * 160}), item_id))
                    db.execute("INSERT INTO generation_quality(item_id, state, report_json, updated_at) VALUES (?, 'blocked', ?, ?)",
                               (item_id, json.dumps(report), main.now()))
                repair = auto_generation._queue_auto_repair(item_id, report)
                self.assertEqual(repair is None, manual)
                with main.database() as db:
                    count = db.execute("SELECT COUNT(*) FROM generation_batch_items WHERE repair_parent_item_id=?",
                                       (item_id,)).fetchone()[0]
                self.assertEqual(count, 0 if manual else 1)

    def test_repair_is_limited_to_two_new_renders(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        report = {"approved": False, "videos": [{"approved": False, "failed_checks": ["duration"],
                  "measurements": {"duration_seconds": 56.4}, "reason": "Curto"}]}
        with main.database() as db:
            root_id = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()["id"]
            generated = {"subject": "Luz", "requirements": "Correto", "script": "luz " * 160, "scenes": []}
            db.execute("UPDATE generation_batch_items SET generated_json=?, state='submitted' WHERE id=?",
                       (json.dumps(generated), root_id))
            db.execute("INSERT INTO generation_quality(item_id, state, report_json, updated_at) VALUES (?, 'blocked', ?, ?)",
                       (root_id, json.dumps(report), main.now()))
        first = auto_generation._queue_auto_repair(root_id, report)
        self.assertEqual(first["attempt"], 1)
        with main.database() as db:
            db.execute("UPDATE generation_batch_items SET generated_json=?, state='submitted' WHERE id=?",
                       (json.dumps(generated), first["item_id"]))
            db.execute("INSERT INTO generation_quality(item_id, state, report_json, updated_at) VALUES (?, 'blocked', ?, ?)",
                       (first["item_id"], json.dumps(report), main.now()))
        second = auto_generation._queue_auto_repair(first["item_id"], report)
        self.assertEqual(second["attempt"], 2)
        with main.database() as db:
            db.execute("INSERT INTO generation_quality(item_id, state, report_json, updated_at) VALUES (?, 'blocked', ?, ?)",
                       (second["item_id"], json.dumps(report), main.now()))
        self.assertIsNone(auto_generation._queue_auto_repair(second["item_id"], report))
        with main.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM generation_batch_items WHERE repair_root_item_id=?", (root_id,)).fetchone()[0], 2)

    def test_visual_and_audio_failures_choose_targeted_actions(self):
        generated = {"scenes": [{"selected": "a"}, {"selected": "b"}, {"selected": "c"}]}
        report = {"videos": [{"approved": False, "failed_checks": ["visual_scenes", "audible"],
                  "observations": [{"scene": 2, "approved": False, "reason": "Cena alheia"}],
                  "reason": "Áudio baixo e cena fora do tema"}]}
        decision = auto_generation._repair_decision(report, generated, manual_script=False)
        self.assertEqual(decision["scene_indexes"], [1])
        self.assertEqual(decision["failed_checks"], ["audible", "visual_scenes"])

    def test_visual_repair_recurates_only_failed_scene_and_raises_voice_volume(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            item_id = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()["id"]
            scenes = [{"index": index, "narration": "luz", "query": "sunlight", "selected": f"asset-{index}",
                       "candidates": [{"id": f"asset-{index}", "status": "approved", "filename": f"scene{index:03d}option0.jpg"}]}
                      for index in range(2)]
            generated = {"subject": "Luz", "requirements": "Correto", "script": "luz " * 160,
                         "terms": "sunlight, sky", "scenes": scenes}
            db.execute("UPDATE generation_batch_items SET generated_json=?, state='submitted' WHERE id=?",
                       (json.dumps(generated), item_id))
            report = {"videos": [{"approved": False, "failed_checks": ["visual_scenes", "audible"],
                      "observations": [{"scene": 2, "approved": False}], "reason": "Cena e voz"}]}
            db.execute("INSERT INTO generation_quality(item_id, state, report_json, updated_at) VALUES (?, 'blocked', ?, ?)",
                       (item_id, json.dumps(report), main.now()))
        folder = auto_generation.specialists.material_root(item_id)
        folder.mkdir(parents=True)
        for scene in scenes:
            (folder / scene["candidates"][0]["filename"]).write_bytes(b"image")
        queued = auto_generation._queue_auto_repair(item_id, report)
        with main.database() as db:
            child = db.execute("SELECT generated_json, context_json FROM generation_batch_items WHERE id=?", (queued["item_id"],)).fetchone()
        revised = json.loads(child["generated_json"])
        settings = json.loads(child["context_json"])["settings"]
        self.assertEqual(revised["scenes"][0]["selected"], "asset-0")
        self.assertIsNone(revised["scenes"][1]["selected"])
        self.assertEqual(revised["scenes"][1]["excluded_ids"], ["asset-1"])
        self.assertGreater(settings["voice_volume"], 1.0)
        self.assertTrue((auto_generation.specialists.material_root(queued["item_id"]) / "scene000option0.jpg").is_file())

    def test_repair_of_one_channel_does_not_change_second_channel(self):
        batch = auto_generation.create_auto_batch("project", self.payload())
        with main.database() as db:
            rows = list(db.execute("SELECT id, channel_id, state FROM generation_batch_items WHERE batch_id=? ORDER BY channel_id", (batch["batch_id"],)))
            first = rows[0]
            generated = {"subject": "Luz", "requirements": "Correto", "script": "luz " * 160, "scenes": []}
            db.execute("UPDATE generation_batch_items SET generated_json=?, state='submitted' WHERE id=?",
                       (json.dumps(generated), first["id"]))
            report = {"videos": [{"approved": False, "failed_checks": ["duration"],
                      "measurements": {"duration_seconds": 56.4}, "reason": "Curto"}]}
            db.execute("INSERT INTO generation_quality(item_id, state, report_json, updated_at) VALUES (?, 'blocked', ?, ?)",
                       (first["id"], json.dumps(report), main.now()))
        child = auto_generation._queue_auto_repair(first["id"], report)
        with main.database() as db:
            other = db.execute("SELECT state, job_id FROM generation_batch_items WHERE id=?", (rows[1]["id"],)).fetchone()
            revised = db.execute("SELECT channel_id FROM generation_batch_items WHERE id=?", (child["item_id"],)).fetchone()
        self.assertEqual((other["state"], other["job_id"]), ("queued", None))
        self.assertEqual(revised["channel_id"], first["channel_id"])

    def test_operator_publication_waits_for_final_quality_approval(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        with main.database() as db:
            item_id = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()["id"]
            db.execute("INSERT INTO video_jobs(id, project_id, subject, request_json, state, generation_item_id, created_by, auto_publish_authorized, created_at, updated_at) "
                       "VALUES ('job', 'project', 'Luz', '{}', 'completed', ?, 'operator', 1, ?, ?)",
                       (item_id, main.now(), main.now()))
            db.execute("INSERT INTO operators(channel_id, enabled, auto_publish, updated_at) VALUES ('yt', 1, 1, ?)", (main.now(),))
            db.execute("INSERT INTO publications(id, job_id, platform, channel_id, state, created_at, updated_at) "
                       "VALUES ('publication', 'job', 'youtube', 'yt', 'pending', ?, ?)", (main.now(), main.now()))
            db.execute("INSERT INTO job_metadata(job_id, value_json, updated_at) VALUES ('job', '{}', ?)", (main.now(),))
            db.execute("INSERT INTO generation_quality(item_id, job_id, state, updated_at) VALUES (?, 'job', 'pending', ?)",
                       (item_id, main.now()))
        calls = []
        async def publish(job_id):
            calls.append(job_id)
        with patch.object(main, "auto_publish_operator_job", new=publish):
            asyncio.run(main.prepare_auto_publications())
            self.assertEqual(calls, [])
            with main.database() as db:
                db.execute("UPDATE generation_quality SET state='approved' WHERE item_id=?", (item_id,))
            asyncio.run(main.prepare_auto_publications())
        self.assertEqual(calls, ["job"])

    def test_explicit_recovery_of_legacy_blocked_review_uses_cached_video(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"]))
        legacy = {"approved": False, "videos": [{"approved": False,
                  "checks": {"duration": False, "aspect": True, "audio": True},
                  "observations": [], "reason": "O vídeo final tem 56.4 s; exige 60 a 66 s."}]}
        with main.database() as db:
            item_id = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()["id"]
            generated = {"subject": "Luz", "requirements": "Correto", "script": "luz " * 160, "scenes": []}
            db.execute("UPDATE generation_batch_items SET generated_json=?, state='submitted' WHERE id=?",
                       (json.dumps(generated), item_id))
            db.execute("INSERT INTO generation_quality(item_id, state, report_json, updated_at) VALUES (?, 'blocked', ?, ?)",
                       (item_id, json.dumps(legacy), main.now()))
        cached = auto_generation.specialists.material_root(item_id) / "final-review-0.mp4"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"cached video")
        with patch.object(auto_generation.specialists, "probe", return_value={"duration": 56.4}):
            first = asyncio.run(auto_generation.resume_blocked_review(batch["batch_id"], item_id))
            second = asyncio.run(auto_generation.resume_blocked_review(batch["batch_id"], item_id))
        self.assertEqual(first["batch_id"], second["batch_id"])
        self.assertEqual(first["item_id"], second["item_id"])
        with main.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM generation_batch_items WHERE repair_parent_item_id=?", (item_id,)).fetchone()[0], 1)
