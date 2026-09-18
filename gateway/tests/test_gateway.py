import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path


class GatewayContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        os.environ["APP_DATA_DIR"] = self.temporary.name
        from gateway import main
        self.main = main
        self.main.DATA_DIR = Path(self.temporary.name)
        self.main.DATABASE = self.main.DATA_DIR / "cockpit.sqlite3"
        self.main.RETRY_BASE_SECONDS = 0
        self.main.initialize_database()

    def tearDown(self):
        self.temporary.cleanup()

    def test_default_settings_disable_bgm_and_use_edge(self):
        defaults = self.main.default_generation_settings()
        self.assertEqual(defaults["bgm_type"], "")
        self.assertTrue(defaults["subtitle_enabled"])
        self.assertEqual(defaults["subtitle_provider"], "edge")
        # Equilíbrio velocidade/qualidade stock-only: sem transição, voz e clipe neutros.
        self.assertEqual(defaults["video_transition_mode"], "")
        self.assertEqual(defaults["voice_rate"], 1.0)
        self.assertEqual(defaults["video_source"], "pexels")
        self.assertEqual(defaults["video_concat_mode"], "script")

    def test_format_presets_balance_speed_and_quality(self):
        short = self.main.FORMAT_PRESETS["short"]
        long = self.main.FORMAT_PRESETS["long"]
        self.assertEqual((short["video_clip_duration"], short["subtitle_position"], short["font_size"]), (4, "center", 64))
        self.assertEqual((long["video_clip_duration"], long["subtitle_position"], long["font_size"]), (6, "bottom", 48))
        self.assertEqual(short["bgm_type"], "")
        self.assertEqual(long["video_transition_mode"], "")

    def test_german_language_has_voice_and_region(self):
        self.assertEqual(self.main.VOICE_BY_LANGUAGE["de"], "de-DE-KatjaNeural")
        self.assertEqual(self.main.REGION_BY_LANGUAGE["de"], "DE")

    def test_turbo_state_mapping_does_not_fake_completion(self):
        self.assertEqual(self.main.map_turbo_state({"state": "queued", "progress": 0})[0], "queued")
        self.assertEqual(self.main.map_turbo_state({"state": "processing", "progress": 42})[0], "running")
        self.assertEqual(self.main.map_turbo_state({"state": "failed", "error": "no key"})[0], "failed")
        self.assertEqual(self.main.map_turbo_state({"state": "done", "videos": ["x.mp4"]})[0], "completed")

    def test_database_creates_persistent_default_settings(self):
        db = self.main.connect()
        try:
            row = db.execute("SELECT value_json FROM settings WHERE key='generation-defaults'").fetchone()
        finally:
            db.close()
        self.assertIsNotNone(row)

    def test_partial_generation_defaults_update_preserves_other_fields(self):
        self.main.put_generation_defaults(self.main.SettingsPatch(value={"font_size": 64}))
        merged = self.main.put_generation_defaults(self.main.SettingsPatch(value={"video_aspect": "16:9"}))
        self.assertEqual(merged["font_size"], 64)
        self.assertEqual(merged["video_aspect"], "16:9")

    def test_stale_ai_video_source_falls_back_to_pexels(self):
        self.main.put_generation_defaults(self.main.SettingsPatch(value={"video_source": "ai_images"}))
        self.assertEqual(self.main.generation_defaults()["video_source"], "pexels")
        self.assertEqual(self.main.generation_defaults("short")["video_source"], "pexels")

    def test_validate_provider_key_reports_rejection(self):
        from unittest.mock import AsyncMock, patch
        fake_response = type("FakeResponse", (), {"status_code": 401})()
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)):
            result = asyncio.run(self.main.validate_provider_key(self.main.ProviderValidation(provider="pexels", api_key="bad-key")))
        self.assertFalse(result["valid"])
        self.assertTrue(result["detail"])

    def test_validate_pixabay_and_coverr_keys(self):
        from unittest.mock import AsyncMock, patch
        ok, bad = (type("R", (), {"status_code": s})() for s in (200, 401))
        with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=[ok, bad])):
            valid = asyncio.run(self.main.validate_provider_key(self.main.ProviderValidation(provider="pixabay", api_key="k")))
            invalid = asyncio.run(self.main.validate_provider_key(self.main.ProviderValidation(provider="coverr", api_key="k")))
        self.assertTrue(valid["valid"])
        self.assertFalse(invalid["valid"])

    def test_update_providers_writes_pixabay_and_coverr_keys(self):
        config_dir = self.main.DATA_DIR / "moneyprinter"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.toml").write_text('pixabay_api_keys = []\ncoverr_api_keys = []\n', encoding="utf-8")
        response = asyncio.run(self.main.update_providers(self.main.ProviderCredentials(pixabay_api_key="pix-secret", coverr_api_key="cov-secret")))
        self.assertTrue(response["pixabay"] and response["coverr"])
        written = (config_dir / "config.toml").read_text(encoding="utf-8")
        self.assertIn('pixabay_api_keys = ["pix-secret"]', written)
        self.assertIn('coverr_api_keys = ["cov-secret"]', written)

    def test_validate_upload_post_key_skips_unverifiable_provider(self):
        result = asyncio.run(self.main.validate_provider_key(self.main.ProviderValidation(provider="upload_post", api_key="whatever")))
        self.assertIsNone(result["valid"])

    def test_update_providers_writes_key_and_never_echoes_it_back(self):
        from unittest.mock import patch
        config_dir = self.main.DATA_DIR / "moneyprinter"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.toml").write_text('moonshot_api_key = ""\nmoonshot_base_url = ""\npexels_api_keys = []\n', encoding="utf-8")
        with patch.object(self.main, "detect_moonshot_region", new=self._async_return("china")):
            response = asyncio.run(self.main.update_providers(self.main.ProviderCredentials(moonshot_api_key="sk-test-secret", pexels_api_key="pex-secret")))
        self.assertEqual(response, {
            "moonshot": True, "nvidia": False, "pexels": True, "pixabay": False, "coverr": False, "upload_post": False,
            "edge_tts": True, "moonshot_model": "", "llm_provider": "moonshot",
            "nvidia_text_model": self.main.NVIDIA_DEFAULT_TEXT_MODEL,
        })
        self.assertNotIn("sk-test-secret", str(response))
        written = (config_dir / "config.toml").read_text(encoding="utf-8")
        self.assertIn('moonshot_api_key = "sk-test-secret"', written)
        self.assertIn('pexels_api_keys = ["pex-secret"]', written)

    def test_update_providers_sets_global_base_url_for_global_key(self):
        from unittest.mock import patch
        config_dir = self.main.DATA_DIR / "moneyprinter"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.toml").write_text('moonshot_api_key = ""\nmoonshot_base_url = ""\n', encoding="utf-8")
        with patch.object(self.main, "detect_moonshot_region", new=self._async_return("global")):
            asyncio.run(self.main.update_providers(self.main.ProviderCredentials(moonshot_api_key="sk-global-secret")))
        written = (config_dir / "config.toml").read_text(encoding="utf-8")
        self.assertIn(f'moonshot_base_url = "{self.main.MOONSHOT_ENDPOINTS["global"]}"', written)

    def test_update_providers_keeps_base_url_when_region_undetected(self):
        from unittest.mock import patch
        config_dir = self.main.DATA_DIR / "moneyprinter"
        config_dir.mkdir(parents=True, exist_ok=True)
        global_url = self.main.MOONSHOT_ENDPOINTS["global"]
        (config_dir / "config.toml").write_text(f'moonshot_api_key = "old"\nmoonshot_base_url = "{global_url}"\n', encoding="utf-8")
        with patch.object(self.main, "detect_moonshot_region", new=self._async_return(None)):
            asyncio.run(self.main.update_providers(self.main.ProviderCredentials(moonshot_api_key="sk-new")))
        self.assertIn(f'moonshot_base_url = "{global_url}"', (config_dir / "config.toml").read_text(encoding="utf-8"))

    @staticmethod
    def _async_return(value):
        async def _inner(*_args, **_kwargs):
            return value
        return _inner

    def test_update_providers_sets_moonshot_model_name(self):
        config_dir = self.main.DATA_DIR / "moneyprinter"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.toml").write_text('moonshot_api_key = "existing"\nmoonshot_model_name = ""\n', encoding="utf-8")
        asyncio.run(self.main.update_providers(self.main.ProviderCredentials(moonshot_model_name="kimi-k2.6")))
        written = (config_dir / "config.toml").read_text(encoding="utf-8")
        self.assertIn('moonshot_model_name = "kimi-k2.6"', written)

    def test_validate_nvidia_key_reports_rejection(self):
        from unittest.mock import AsyncMock, patch
        fake_response = type("FakeResponse", (), {"status_code": 401})()
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
            result = asyncio.run(self.main.validate_provider_key(self.main.ProviderValidation(provider="nvidia", api_key="bad-key")))
        self.assertFalse(result["valid"])

    def test_update_providers_switches_llm_provider_to_nvidia_and_back(self):
        config_dir = self.main.DATA_DIR / "moneyprinter"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.toml").write_text('llm_provider = "moonshot"\n', encoding="utf-8")
        response = asyncio.run(self.main.update_providers(self.main.ProviderCredentials(
            llm_provider="nvidia", nvidia_api_key="nv-secret", nvidia_text_model="google/gemma-4-31b-it",
        )))
        self.assertEqual(response["llm_provider"], "nvidia")
        self.assertNotIn("nv-secret", str(response))
        written = (config_dir / "config.toml").read_text(encoding="utf-8")
        self.assertIn('llm_provider = "openai"', written)
        self.assertIn('openai_api_key = "nv-secret"', written)
        self.assertIn(f'openai_base_url = "{self.main.NVIDIA_BASE_URL}"', written)
        self.assertIn('openai_model_name = "google/gemma-4-31b-it"', written)

        back = asyncio.run(self.main.update_providers(self.main.ProviderCredentials(llm_provider="moonshot")))
        self.assertEqual(back["llm_provider"], "moonshot")
        self.assertIn('llm_provider = "moonshot"', (config_dir / "config.toml").read_text(encoding="utf-8"))

    def test_update_providers_requires_nvidia_key_before_switching(self):
        from fastapi import HTTPException
        config_dir = self.main.DATA_DIR / "moneyprinter"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.toml").write_text('llm_provider = "moonshot"\n', encoding="utf-8")
        with self.assertRaises(HTTPException) as blocked:
            asyncio.run(self.main.update_providers(self.main.ProviderCredentials(llm_provider="nvidia")))
        self.assertEqual(blocked.exception.status_code, 409)

    def test_llm_chat_sends_json_response_format_only_for_moonshot(self):
        from unittest.mock import AsyncMock, patch
        config_dir = self.main.DATA_DIR / "moneyprinter"
        config_dir.mkdir(parents=True, exist_ok=True)
        fake_response = type("R", (), {"status_code": 200, "json": lambda self: {"choices": [{"message": {"content": "ok"}}]}})()

        (config_dir / "config.toml").write_text('moonshot_api_key = "mk"\n', encoding="utf-8")
        with patch.object(self.main, "send_with_retry", new=AsyncMock(return_value=fake_response)) as send:
            asyncio.run(self.main.llm_chat("prompt", json_mode=True))
        self.assertIn("response_format", send.call_args.kwargs["json"])

        (config_dir / "config.toml").write_text('cockpit_llm_provider = "nvidia"\nnvidia_api_key = "nk"\n', encoding="utf-8")
        with patch.object(self.main, "send_with_retry", new=AsyncMock(return_value=fake_response)) as send:
            asyncio.run(self.main.llm_chat("prompt", json_mode=True))
        self.assertNotIn("response_format", send.call_args.kwargs["json"])
        self.assertEqual(send.call_args.args[2], f"{self.main.NVIDIA_BASE_URL}/chat/completions")

    def test_build_turbo_payload_passes_video_params_and_drops_the_rest(self):
        payload = self.main.build_turbo_payload(" tema ", "", {
            "video_concat_mode": "sequential", "voice_rate": 1.2, "bgm_type": "random",
            "video_transition_mode": "", "video_terms": "", "subtitle_provider": "edge", "n_threads": 4,
            "paragraph_number": 20, "video_clip_duration": 0, "video_count": "2",
        })
        self.assertEqual(payload["n_threads"], 4)
        self.assertEqual(payload["paragraph_number"], 10)
        self.assertEqual(payload["video_clip_duration"], 1)
        self.assertEqual(payload["video_count"], 2)
        self.assertEqual(payload["video_subject"], "tema")
        self.assertEqual(payload["video_concat_mode"], "sequential")
        self.assertEqual(payload["voice_rate"], 1.2)
        self.assertNotIn("video_transition_mode", payload)
        self.assertNotIn("video_terms", payload)
        self.assertNotIn("subtitle_provider", payload)

    def test_sse_message_uses_real_line_breaks(self):
        self.assertEqual(self.main.sse_message("job.updated", '{"a":1}'), 'event: job.updated\ndata: {"a":1}\n\n')

    def test_turbo_relative_path_strips_tasks_uri_wrapping(self):
        self.assertEqual(self.main.turbo_relative_path("/tasks/abc123/final-1.mp4"), "abc123/final-1.mp4")
        self.assertEqual(self.main.turbo_relative_path("http://127.0.0.1:8080/tasks/abc123/final-1.mp4"), "abc123/final-1.mp4")
        self.assertEqual(self.main.turbo_relative_path("abc123/final-1.mp4"), "abc123/final-1.mp4")

    def test_stale_running_job_marked_interrupted_when_turbo_unreachable(self):
        self.main.TURBO_BASE_URL = "http://127.0.0.1:1"  # nothing listens here
        with self.main.database() as db:
            db.execute(
                "INSERT INTO projects VALUES ('p1', 'Projeto', '', 'pt-BR', '', '{}', '{}', ?, ?, NULL)",
                (self.main.now(), self.main.now()),
            )
            db.execute(
                "INSERT INTO video_jobs(id, project_id, turbo_task_id, subject, request_json, state, progress, created_at, updated_at) VALUES (?, 'p1', 'task-1', 'assunto', '{}', 'running', 10, ?, ?)",
                ("job-1", self.main.now(), self.main.now()),
            )
        asyncio.run(self.main.reconcile_stale_jobs())
        with self.main.database() as db:
            row = db.execute("SELECT state, error FROM video_jobs WHERE id='job-1'").fetchone()
        self.assertEqual(row["state"], "interrupted")
        self.assertTrue(row["error"])

    def test_cancel_job_stops_only_active_jobs(self):
        from unittest.mock import AsyncMock, patch
        from fastapi import HTTPException
        with self.main.database() as db:
            db.execute("INSERT INTO projects VALUES ('p1', 'Projeto', '', 'pt-BR', '', '{}', '{}', ?, ?, NULL)", (self.main.now(), self.main.now()))
            db.execute(
                "INSERT INTO video_jobs(id, project_id, turbo_task_id, subject, request_json, state, progress, created_at, updated_at) VALUES ('job-1', 'p1', 'task-1', 'assunto', '{}', 'running', 40, ?, ?)",
                (self.main.now(), self.main.now()),
            )
        fake_response = type("FakeResponse", (), {"status_code": 200, "json": lambda self: {}, "text": ""})()
        with patch("httpx.AsyncClient.send", new=AsyncMock(return_value=fake_response)):
            result = asyncio.run(self.main.cancel_job("job-1"))
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(result["error"], "Cancelado pelo usuário")
        # already interrupted (or any non-active state) can't be cancelled again
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(self.main.cancel_job("job-1"))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_youtube_metadata_is_cleaned_and_validated(self):
        value, errors, warnings = self.main.normalize_youtube_metadata({
            "title": "<b>" + "x" * 150, "description": "linha 1\nlinha 2\x00", "tags": ["#ia", "IA", "fatos curtos", "", "ciência"],
        })
        self.assertEqual(errors, [])
        self.assertEqual(len(value["title"]), 100)
        self.assertNotIn("<", value["title"])
        self.assertEqual(value["description"], "linha 1\nlinha 2")
        self.assertEqual(value["tags"], ["ia", "fatos curtos", "ciência"])
        self.assertEqual(warnings, [])
        _, errors, _ = self.main.normalize_youtube_metadata({"title": "  ", "language": "Português"})
        self.assertEqual(len(errors), 2)
        tags, _, _ = self.main.normalize_youtube_metadata({"title": "t", "tags": ",".join(f"tag{i:03d}" for i in range(100))})
        self.assertEqual(len(tags["tags"]), 30)

    def test_retry_never_repeats_a_request_that_may_have_been_processed(self):
        import httpx
        request = httpx.Request("POST", "http://turbo")
        self.assertTrue(self.main.is_retryable("POST", exc=httpx.ConnectError("down", request=request)))
        self.assertFalse(self.main.is_retryable("POST", exc=httpx.ReadTimeout("slow", request=request)))
        self.assertTrue(self.main.is_retryable("GET", exc=httpx.ReadTimeout("slow", request=request)))
        self.assertFalse(self.main.is_retryable("POST", status=503))
        self.assertTrue(self.main.is_retryable("POST", status=429))
        self.assertTrue(self.main.is_retryable("GET", status=503))
        self.assertFalse(self.main.is_retryable("GET", status=404))

    def _completed_job(self, channel_id=None):
        with self.main.database() as db:
            db.execute("INSERT INTO projects VALUES ('p1', 'Projeto', '', 'pt-BR', '', '{}', '{}', ?, ?, NULL)", (self.main.now(), self.main.now()))
            db.execute(
                "INSERT INTO video_jobs(id, project_id, turbo_task_id, subject, request_json, state, progress, created_at, updated_at, channel_id) VALUES ('job-1', 'p1', 'task-1', 'assunto', '{}', 'completed', 100, ?, ?, ?)",
                (self.main.now(), self.main.now(), channel_id),
            )
            db.execute("INSERT INTO artifacts VALUES ('a1', 'job-1', 'video', 'task-1/final-1.mp4', 'final-1.mp4', ?)", (self.main.now(),))
            if channel_id:  # same pending target create_job records for a channel picked at generation time
                db.execute(
                    "INSERT INTO publications(id, job_id, platform, channel_id, state, created_at, updated_at) SELECT 'pub-' || id, 'job-1', platform, id, 'pending', ?, ? FROM channels WHERE id=?",
                    (self.main.now(), self.main.now(), channel_id),
                )

    def test_approval_requires_valid_metadata(self):
        from fastapi import HTTPException
        self._completed_job()
        with self.assertRaises(HTTPException) as blocked:
            self.main.approve_job("job-1")
        self.assertEqual(blocked.exception.status_code, 409)
        self.main.put_metadata("job-1", self.main.MetadataInput(title="Título", description="d", tags="a,b,c"))
        self.assertEqual(self.main.approve_job("job-1")["state"], "approved")

    def test_job_language_follows_the_youtube_channel_not_the_stale_column(self):
        # Regression: video_jobs.channel_id is never written by create_job (only by the
        # pre-multi-channel migration backfill), so job_language must resolve the channel
        # via publications, not that column.
        channel_id = self._channel()
        with self.main.database() as db:
            db.execute("UPDATE channels SET language='en-US' WHERE id=?", (channel_id,))
        self._completed_job()  # as create_job leaves it: video_jobs.channel_id stays NULL
        with self.main.database() as db:
            db.execute(
                "INSERT INTO publications(id, job_id, platform, channel_id, state, created_at, updated_at) VALUES ('pub-1', 'job-1', 'youtube', ?, 'pending', ?, ?)",
                (channel_id, self.main.now(), self.main.now()),
            )
        with self.main.database() as db:
            job = self.main.job_row_or_404(db, "job-1")
            self.assertIsNone(job["channel_id"])
            self.assertEqual(self.main.job_language(db, job), "en-US")

    def test_job_language_falls_back_to_the_project_when_no_channel_was_picked(self):
        self._completed_job()  # no channel picked at all: falls back to the project's language
        with self.main.database() as db:
            job = self.main.job_row_or_404(db, "job-1")
            self.assertEqual(self.main.job_language(db, job), "pt-BR")

    def _channel(self, platform="youtube", status="connected", external_id="UC1"):
        with self.main.database() as db:
            db.execute(
                "INSERT INTO channels(id, platform, name, external_id, status, created_at, updated_at) VALUES ('ch-1', ?, 'Canal', ?, ?, ?, ?)",
                (platform, external_id, status, self.main.now(), self.main.now()),
            )
        token = self.main.youtube_token_file("ch-1")
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text("{}", encoding="utf-8")
        return "ch-1"

    def _approved_job(self, channel_id=None):
        self._completed_job(channel_id)
        self.main.put_metadata("job-1", self.main.MetadataInput(title="Título", description="d", tags="a,b,c"))
        self.main.approve_job("job-1")

    def _publication(self):
        with self.main.database() as db:
            return db.execute("SELECT * FROM publications WHERE job_id='job-1'").fetchone()

    def test_publish_uploads_once_and_blocks_after_unknown_outcome(self):
        from unittest.mock import patch
        from fastapi import HTTPException
        self._approved_job(self._channel())

        with patch.object(self.main, "download_turbo_file", new=self._async_return(None)), \
                patch.object(self.main, "youtube_credentials", return_value=object()), \
                patch.object(self.main, "youtube_upload", side_effect=ConnectionResetError("reset")):
            with self.assertRaises(HTTPException):
                asyncio.run(self.main.publish_job("job-1"))
        self.assertEqual(self._publication()["state"], "reconciliation_required")

        with self.assertRaises(HTTPException) as blocked:
            asyncio.run(self.main.publish_job("job-1"))
        self.assertEqual(blocked.exception.status_code, 409)

        with patch.object(self.main, "download_turbo_file", new=self._async_return(None)), \
                patch.object(self.main, "youtube_credentials", return_value=object()) as credentials, \
                patch.object(self.main, "youtube_upload", return_value="yt123") as upload:
            job = asyncio.run(self.main.publish_job("job-1", self.main.PublishInput(confirm_not_uploaded=True)))
        self.assertEqual(credentials.call_args.args[0], "ch-1")
        self.assertEqual(upload.call_args.args[2]["title"], "Título")
        self.assertEqual(len(upload.call_args.args), 3)
        self.assertEqual(job["publications"][0]["external_id"], "yt123")
        self.assertEqual(self._publication()["state"], "published")
        self.assertEqual(self._publication()["attempts"], 2)

    def test_publish_needs_a_connected_channel(self):
        from fastapi import HTTPException
        self._approved_job()
        with self.assertRaises(HTTPException) as missing:
            asyncio.run(self.main.publish_job("job-1"))
        self.assertIn("canal", missing.exception.detail)
        self._channel(status="expired")
        with self.assertRaises(HTTPException) as expired:
            asyncio.run(self.main.publish_job("job-1", self.main.PublishInput(channel_ids=["ch-1"])))
        self.assertEqual(expired.exception.status_code, 409)
        self.assertIsNone(self._publication())

    def test_create_job_rejects_expired_channel_before_rendering(self):
        from unittest.mock import patch
        from fastapi import HTTPException
        self._completed_job()
        self._channel(status="expired")
        with patch.object(self.main, "turbo_request") as turbo, self.assertRaises(HTTPException) as rejected:
            asyncio.run(self.main.create_job("p1", self.main.JobCreate(subject="tema", channel_ids=["ch-1"])))
        self.assertEqual(rejected.exception.status_code, 409)
        turbo.assert_not_called()

    def test_reconnecting_a_youtube_channel_updates_instead_of_duplicating(self):
        info = {"external_id": "UC9", "name": "Meu Canal", "handle": "@meucanal", "stats": {"followers": 10, "videos": 2, "views": 99}}
        first = self.main.save_youtube_channel(info, {"niche": "fatos", "language": "pt-BR"})
        second = self.main.save_youtube_channel({**info, "name": "Novo Nome"}, {"niche": "", "language": "en-US"})
        channels = self.main.list_channels()
        self.assertEqual(first, second)
        self.assertEqual(len(channels), 1)
        self.assertEqual((channels[0]["name"], channels[0]["niche"], channels[0]["language"]), ("Novo Nome", "fatos", "pt-BR"))
        asyncio.run(self.main.delete_channel(first))
        self.assertNotEqual(self.main.save_youtube_channel(info, {}), first)  # a deleted channel can be connected again

    def test_tiktok_publish_waits_for_upload_post_then_completes(self):
        from unittest.mock import patch
        self._approved_job(self._channel(platform="tiktok", external_id="mp-abc"))
        with patch.object(self.main, "download_turbo_file", new=self._async_return(None)), \
                patch.object(self.main, "tiktok_upload", new=self._async_return(("req-1", "processing"))):
            asyncio.run(self.main.publish_job("job-1"))
        self.assertEqual((self._publication()["platform"], self._publication()["state"], self._publication()["external_id"]), ("tiktok", "processing", "req-1"))
        done = {"status": "completed", "results": [{"platform": "tiktok", "success": True, "post_url": "https://tiktok.com/@x/video/1"}]}
        with patch.object(self.main, "upload_post_request", new=self._async_return(done)):
            asyncio.run(self.main.reconcile_publications("job-1"))
        self.assertEqual((self._publication()["state"], self._publication()["external_id"]), ("published", "https://tiktok.com/@x/video/1"))
        self.assertEqual(self.main.map_upload_post_status({"status": "failed", "message": "banido"})[:3:2], ("failed", "banido"))
        self.assertEqual(self.main.map_upload_post_status({"status": "in_progress", "results": [{"platform": "tiktok", "success": False}]})[0], "processing")

    def _connected(self, *channels):
        with self.main.database() as db:
            for channel_id, platform in channels:
                db.execute(
                    "INSERT INTO channels(id, platform, name, external_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'connected', ?, ?)",
                    (channel_id, platform, channel_id, channel_id, self.main.now(), self.main.now()),
                )

    def test_create_job_locks_the_format_and_records_channel_targets(self):
        from unittest.mock import patch
        from fastapi import HTTPException
        self._completed_job()
        self._connected(("yt", "youtube"), ("tt", "tiktok"), ("yt2", "youtube"))
        sent = []

        async def fake_turbo(method, path, **kwargs):
            if path == "/api/v1/scripts":  # "script" concat mode asks Turbo to write a Short's script first; empty here keeps this test focused on format-locking
                return type("Response", (), {"json": lambda self: {"data": {"video_script": ""}}})()
            sent.append(kwargs["json"])
            return type("Response", (), {"json": lambda self: {"data": {"task_id": f"t{len(sent)}"}}})()

        with patch.object(self.main, "turbo_request", new=fake_turbo):
            short = asyncio.run(self.main.create_job("p1", self.main.JobCreate(subject="s", format="short", channel_ids=["yt", "tt"], settings={"video_aspect": "16:9"})))
            long = asyncio.run(self.main.create_job("p1", self.main.JobCreate(subject="l", format="long", settings={"video_count": 3})))
            for format, channel_ids in (("long", ["tt"]), ("short", ["yt", "yt2"])):
                with self.assertRaises(HTTPException) as rejected:
                    asyncio.run(self.main.create_job("p1", self.main.JobCreate(subject="x", format=format, channel_ids=channel_ids)))
                self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(len(sent), 2)  # rejected picks never reach Turbo
        self.assertEqual((sent[0]["video_aspect"], sent[1]["video_aspect"], sent[1]["video_count"]), ("9:16", "16:9", 1))
        self.assertEqual(sent[0]["subtitle_position"], "center")
        self.assertEqual((short["format"], long["format"]), ("short", "long"))
        self.assertEqual(sorted((p["platform"], p["state"]) for p in short["publications"]), [("tiktok", "pending"), ("youtube", "pending")])

    def test_migration_backfills_format_and_pending_targets(self):
        now = self.main.now()
        with self.main.database() as db:  # rebuild the pre-split shape: no video_jobs.format, no publications.channel_id
            db.execute("ALTER TABLE video_jobs DROP COLUMN format")
            db.executescript(
                "DROP TABLE publications; CREATE TABLE publications (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, platform TEXT NOT NULL, state TEXT NOT NULL, "
                "attempts INTEGER NOT NULL DEFAULT 0, external_id TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(job_id, platform));"
            )
        self._connected(("ch-1", "youtube"))
        with self.main.database() as db:
            db.execute("INSERT INTO projects VALUES ('p1', 'Projeto', '', 'pt-BR', '', '{}', '{}', ?, ?, NULL)", (now, now))
            for job_id, aspect, channel_id in (("wide", "16:9", "ch-1"), ("tall", "9:16", None)):
                db.execute(
                    "INSERT INTO video_jobs(id, project_id, subject, request_json, state, created_at, updated_at, channel_id) VALUES (?, 'p1', 'x', ?, 'completed', ?, ?, ?)",
                    (job_id, json.dumps({"video_aspect": aspect}), now, now, channel_id),
                )
        self.main.initialize_database()
        with self.main.database() as db:
            formats = {row["id"]: row["format"] for row in db.execute("SELECT id, format FROM video_jobs")}
            targets = [tuple(row) for row in db.execute("SELECT job_id, channel_id, state FROM publications")]
        self.assertEqual(formats, {"wide": "long", "tall": "short"})
        self.assertEqual(targets, [("wide", "ch-1", "pending")])

    def test_long_script_is_written_section_by_section(self):
        from unittest.mock import patch
        outline = json.dumps({"title": "T", "sections": [{"heading": f"S{i}", "points": ["p"]} for i in range(4)]})
        prompts = []

        async def fake_chat(prompt, json_mode=False):
            prompts.append(prompt)
            return outline if json_mode else f"## Título\n- parte {len(prompts) - 1} com **texto**"

        with patch.object(self.main, "llm_chat", new=fake_chat):
            result = asyncio.run(self.main.long_script(self.main.LongScriptInput(subject="oceano", minutes=5)))
        self.assertEqual(len(prompts), 5)  # outline + one call per section
        self.assertIn("parte 1 com texto", prompts[2])  # each section continues from the previous ending
        self.assertNotIn("#", result["script"])
        self.assertNotIn("*", result["script"])
        self.assertNotIn("terms", result)
        self.assertEqual(result["sections"], ["S0", "S1", "S2", "S3"])

    def test_long_script_honors_custom_requirements_throughout(self):
        from unittest.mock import patch
        outline = json.dumps({"title": "T", "sections": [{"heading": "S0", "points": ["p"]}] * 4})
        prompts = []

        async def fake_chat(prompt, json_mode=False):
            prompts.append(prompt)
            return outline if json_mode else "narração"

        with patch.object(self.main, "llm_chat", new=fake_chat):
            asyncio.run(self.main.long_script(self.main.LongScriptInput(subject="oceano", minutes=5, requirements="tom leve, estilo TikTok")))
        self.assertTrue(all("tom leve, estilo TikTok" in prompt for prompt in prompts))

    def test_long_script_folds_system_prompt_into_every_call(self):
        from unittest.mock import patch
        outline = json.dumps({"title": "T", "sections": [{"heading": "S0", "points": ["p"]}] * 4})
        prompts = []

        async def fake_chat(prompt, json_mode=False):
            prompts.append(prompt)
            return outline if json_mode else "narração"

        with patch.object(self.main, "llm_chat", new=fake_chat):
            asyncio.run(self.main.long_script(self.main.LongScriptInput(subject="oceano", minutes=5, system_prompt="nunca use a palavra incrível")))
        self.assertTrue(all("nunca use a palavra incrível" in prompt for prompt in prompts))

    def test_short_publishes_to_youtube_and_tiktok_with_one_download(self):
        from unittest.mock import AsyncMock, patch
        self._connected(("tt", "tiktok"))
        self._approved_job(self._channel())
        with self.main.database() as db:
            db.execute("INSERT INTO publications(id, job_id, platform, channel_id, state, created_at, updated_at) VALUES ('p-tt', 'job-1', 'tiktok', 'tt', 'pending', ?, ?)", (self.main.now(), self.main.now()))
            db.execute("UPDATE video_jobs SET format='short' WHERE id='job-1'")
        download = AsyncMock()
        with patch.object(self.main, "download_turbo_file", new=download), \
                patch.object(self.main, "youtube_credentials", return_value=object()), \
                patch.object(self.main, "youtube_upload", side_effect=ConnectionResetError("reset")) as upload, \
                patch.object(self.main, "tiktok_upload", new=self._async_return(("req-1", "processing"))):
            job = asyncio.run(self.main.publish_job("job-1"))
        self.assertEqual({p["platform"]: p["state"] for p in job["publications"]}, {"youtube": "reconciliation_required", "tiktok": "processing"})
        self.assertEqual(download.await_count, 1)
        self.assertEqual([error["channel_id"] for error in job["errors"]], ["ch-1"])
        self.assertEqual(upload.call_args.args[2]["format"], "short")
        self.assertEqual(self.main.youtube_description({"description": "oi", "format": "short"}), "oi\n\n#Shorts")
        self.assertEqual(self.main.youtube_description({"description": "oi", "format": "long"}), "oi")

    def test_analytics_score_is_relative_to_the_channel_median(self):
        metrics = {"views": 200, "averageViewPercentage": 50, "likes": 10, "comments": 0}
        self.assertEqual(self.main.analytics_score(metrics, 100), 80)  # 200 views is a hit on a 100-view channel
        self.assertEqual(self.main.analytics_score(metrics, 10000), 50)  # and weak on a 10k-view one
        self.assertEqual(self.main.analytics_score({"views": 0}, 0), 0)

    def test_analytics_platform_overview_aggregates_only_real_rows(self):
        channel_id = self._channel()
        with self.main.database() as db:
            db.execute("UPDATE channels SET stats_json=? WHERE id=?", (json.dumps({"followers": 100, "videos": 3, "views": 1000}), channel_id))
        self._completed_job(channel_id)
        with self.main.database() as db:
            db.execute("UPDATE publications SET state='published', external_id='vid-1' WHERE job_id='job-1'")
            db.execute(
                "INSERT INTO video_analytics(publication_id, captured_at, metrics_json, score) VALUES ('pub-ch-1', ?, ?, 80)",
                (self.main.now(), json.dumps({"views": 200, "likes": 10, "comments": 2, "averageViewPercentage": 50})),
            )
        overview = self.main.analytics_platform_overview()
        self.assertEqual(overview["jobs"]["total"], 1)
        self.assertEqual(overview["jobs"]["completed"], 1)
        self.assertEqual(overview["followers_total"], 100)
        self.assertEqual(overview["youtube"]["total_views"], 200)
        self.assertEqual(overview["youtube"]["videos_measured"], 1)
        self.assertNotIn("revenue", overview)
        self.assertNotIn("revenue", json.dumps(overview))

    def test_analytics_platform_overview_empty_database_has_no_hardcoded_totals(self):
        overview = self.main.analytics_platform_overview()
        self.assertEqual((overview["jobs"]["total"], overview["followers_total"], overview["youtube"]["total_views"]), (0, 0, 0))
        self.assertEqual((overview["channels"], overview["youtube"]["top"]), ([], []))

    def test_scheduled_publish_waits_for_the_agent_loop(self):
        from datetime import UTC, datetime, timedelta
        from unittest.mock import AsyncMock, patch
        from fastapi import HTTPException
        self._approved_job(self._channel())
        schedule = lambda delta: self.main.PublishInput(publish_at=datetime.now(UTC) + delta)
        download = AsyncMock()
        with patch.object(self.main, "download_turbo_file", new=download):
            with self.assertRaises(HTTPException):
                asyncio.run(self.main.publish_job("job-1", schedule(timedelta(minutes=-1))))
            job = asyncio.run(self.main.publish_job("job-1", schedule(timedelta(hours=1))))
            self.assertEqual(job["publications"][0]["state"], "scheduled")
            self.assertEqual(self.main.cancel_schedule("job-1")["publications"][0]["state"], "pending")
            asyncio.run(self.main.publish_job("job-1", schedule(timedelta(hours=1))))
            asyncio.run(self.main.publish_due_schedules())  # not due yet
        download.assert_not_awaited()
        self.assertEqual((len(self.main.list_schedule()), self._publication()["state"]), (1, "scheduled"))
        with self.main.database() as db:
            db.execute("UPDATE publications SET scheduled_at=?", ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(),))
        with patch.object(self.main, "download_turbo_file", new=download), \
                patch.object(self.main, "youtube_credentials", return_value=object()), \
                patch.object(self.main, "youtube_upload", return_value="yt9"):
            asyncio.run(self.main.publish_due_schedules())
        self.assertEqual((self._publication()["state"], self._publication()["external_id"]), ("published", "yt9"))

    def test_operator_starts_one_video_then_skips_while_it_renders(self):
        from datetime import UTC, datetime
        from unittest.mock import patch
        channel_id = self._channel()
        self.main.put_operator(channel_id, self.main.OperatorInput(enabled=True, format="short", cadence_hours=24))
        sent = []

        async def fake_turbo(method, path, **kwargs):
            if path == "/api/v1/scripts":  # "script" concat mode asks Turbo to write the Short first when none was given
                return type("Response", (), {"json": lambda self: {"data": {"video_script": ""}}})()
            sent.append(kwargs["json"])
            return type("Response", (), {"json": lambda self: {"data": {"task_id": "t1"}}})()

        with patch.object(self.main, "suggest_topics", new=self._async_return([{"topic": "Tema do operador"}])), \
                patch.object(self.main, "turbo_request", new=fake_turbo):
            first = asyncio.run(self.main.run_operator(channel_id))
            second = asyncio.run(self.main.run_operator(channel_id))
        self.assertIsNone(first["error"])
        self.assertIn("Pulado", second["error"])
        self.assertEqual(len(sent), 1)
        with self.main.database() as db:
            job = db.execute("SELECT created_by, subject, state FROM video_jobs").fetchone()
            operator = db.execute("SELECT next_run_at FROM operators").fetchone()
        self.assertEqual(tuple(job), ("operator", "Tema do operador", "queued"))
        self.assertGreater(operator["next_run_at"], datetime.now(UTC).isoformat())
        self.assertTrue(self.main.list_operators()["operators"][0]["enabled"])

    def test_strategy_uses_trends_and_drops_subjects_already_made(self):
        from unittest.mock import patch
        channel_id = self._channel()
        self._completed_job(channel_id)  # subject "assunto", targeted at this channel
        prompts = []

        async def fake_chat(prompt, json_mode=False):
            prompts.append(prompt)
            return json.dumps({"topics": [{"topic": "Assunto"}, {"topic": "Tema novo", "angle": "a", "rationale": "r"}]})

        with patch.object(self.main, "youtube_trends", return_value=["Vídeo em alta"]), patch.object(self.main, "llm_chat", new=fake_chat):
            result = asyncio.run(self.main.strategy(self.main.StrategyInput(channel_id=channel_id)))
        self.assertEqual([topic["topic"] for topic in result["topics"]], ["Tema novo"])
        self.assertIn("Vídeo em alta", prompts[0])
        self.assertIn("assunto", prompts[0])

    def test_loop_writes_metadata_for_operator_videos_but_never_approves(self):
        from unittest.mock import patch
        self._completed_job()
        with self.main.database() as db:
            db.execute("UPDATE video_jobs SET created_by='operator'")

        async def fake_chat(prompt, json_mode=False):
            return json.dumps({"title": "Título IA", "description": "desc", "tags": ["a", "b", "c"]})

        with patch.object(self.main, "reconcile_job", new=self._async_return({"state": "completed"})), patch.object(self.main, "llm_chat", new=fake_chat):
            asyncio.run(self.main.agent_tick())
        with self.main.database() as db:
            state = db.execute("SELECT state FROM video_jobs").fetchone()["state"]
            metadata = json.loads(db.execute("SELECT value_json FROM job_metadata").fetchone()["value_json"])
        self.assertEqual((state, metadata["title"]), ("completed", "Título IA"))

    def test_split_script_preserves_every_word_in_order(self):
        script = " ".join(f"palavra{i}" for i in range(10))
        chunks = self.main.split_script(script, 3)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(" ".join(chunks).split(), script.split())  # nothing lost, nothing reordered
        self.assertEqual(self.main.split_script("", 5), [])
        self.assertEqual(self.main.split_script("uma palavra", 1), ["uma palavra"])

    def test_visual_terms_align_one_per_chunk_and_dedupe(self):
        from unittest.mock import patch
        script = " ".join(f"palavra{i}" for i in range(60))  # ~24s of narration at 150 wpm

        async def fake_chat(prompt, json_mode=False):
            self.assertIn("Trechos numerados", prompt)
            return json.dumps({"terms": ["ocean waves", "ocean waves", "coral reef"]})  # a duplicate the model repeated

        with patch.object(self.main, "llm_chat", new=fake_chat):
            terms, clip_seconds = asyncio.run(self.main.visual_terms("oceano", script, clip_seconds=5))
        self.assertEqual(clip_seconds, 5)
        self.assertEqual(len(terms), 5)  # one per ~5s chunk of the 24s narration
        self.assertEqual(len(set(terms)), len(terms))  # the repeated "ocean waves" got deduped, then padded back out distinctly

    def test_visual_terms_caps_search_count_and_stretches_clip_length(self):
        from unittest.mock import patch
        script = " ".join(f"palavra{i}" for i in range(4000))  # far more than MAX_VISUAL_TERMS slots at 1s clips

        async def fake_chat(prompt, json_mode=False):
            return json.dumps({"terms": ["ocean"]})

        with patch.object(self.main, "llm_chat", new=fake_chat):
            terms, clip_seconds = asyncio.run(self.main.visual_terms("oceano", script, clip_seconds=1))
        self.assertEqual(len(terms), self.main.MAX_VISUAL_TERMS)
        self.assertGreater(clip_seconds, 1)  # stretched so the capped term count still covers the whole narration

    def test_create_job_script_mode_aligns_terms_and_locks_sequential(self):
        from unittest.mock import patch
        self._completed_job()
        self._connected(("yt", "youtube"))
        sent = {}

        async def fake_turbo(method, path, **kwargs):
            if path == "/api/v1/videos":
                sent["video"] = kwargs["json"]
                return type("Response", (), {"json": lambda self: {"data": {"task_id": "t1"}}})()
            return type("Response", (), {"json": lambda self: {"data": {"video_script": "uma frase de teste bem curta"}}})()

        async def fake_visual_terms(subject, script, clip_seconds, voice_rate=1.0):
            sent["visual_terms_args"] = (subject, script, clip_seconds)
            return ["term one", "term two"], 4.0

        with patch.object(self.main, "turbo_request", new=fake_turbo), patch.object(self.main, "visual_terms", new=fake_visual_terms):
            job = asyncio.run(self.main.create_job("p1", self.main.JobCreate(subject="tema", format="short", channel_ids=["yt"])))
        self.assertEqual(sent["video"]["match_materials_to_script"], True)
        self.assertEqual(sent["video"]["video_concat_mode"], "sequential")
        self.assertEqual(sent["video"]["video_terms"], "term one, term two")
        self.assertEqual(sent["video"]["video_clip_duration"], 4)
        self.assertEqual(sent["visual_terms_args"][1], "uma frase de teste bem curta")  # the script Turbo just wrote, not blank
        self.assertEqual(job["script"], "uma frase de teste bem curta")

    def test_create_job_random_mode_skips_alignment(self):
        from unittest.mock import patch
        self._completed_job()
        self._connected(("yt", "youtube"))
        sent = {}

        async def fake_turbo(method, path, **kwargs):
            sent["video"] = kwargs["json"]
            return type("Response", (), {"json": lambda self: {"data": {"task_id": "t1"}}})()

        with patch.object(self.main, "turbo_request", new=fake_turbo), patch.object(self.main, "visual_terms") as terms_mock:
            asyncio.run(self.main.create_job("p1", self.main.JobCreate(subject="tema", format="short", channel_ids=["yt"], settings={"video_concat_mode": "random"})))
        terms_mock.assert_not_called()
        self.assertEqual(sent["video"]["video_concat_mode"], "random")
        self.assertNotIn("match_materials_to_script", sent["video"])

    def test_trim_to_words_cuts_at_the_last_full_sentence(self):
        script = "Primeira frase aqui. Segunda frase aqui. Terceira frase que não deveria caber."
        self.assertEqual(self.main.trim_to_words(script, 100), script)  # already fits, untouched
        self.assertEqual(self.main.trim_to_words(script, 6), "Primeira frase aqui. Segunda frase aqui.")
        self.assertEqual(self.main.trim_to_words("sem pontuacao nenhuma aqui de jeito nenhum", 3), "sem pontuacao nenhuma")

    def test_short_script_max_words_maps_paragraph_number_to_seconds(self):
        self.assertEqual(self.main.short_script_max_words(4), round(60 * 150 / 60))
        self.assertEqual(self.main.short_script_max_words(6), round(90 * 150 / 60))
        self.assertEqual(self.main.short_script_max_words(99), self.main.short_script_max_words(6))  # unmapped value falls back to the longest Short

    def test_short_script_endpoint_trims_an_oversized_turbo_response(self):
        from unittest.mock import patch
        long_script_text = " ".join(f"palavra{i}." for i in range(500))

        async def fake_turbo(method, path, **kwargs):
            return type("Response", (), {"json": lambda self: {"data": {"video_script": long_script_text}}})()

        with patch.object(self.main, "turbo_request", new=fake_turbo):
            result = asyncio.run(self.main.short_script(self.main.ShortScriptInput(subject="tema", paragraph_number=5)))
        self.assertTrue(result["trimmed"])
        self.assertLessEqual(result["words"], self.main.short_script_max_words(5))

        async def fake_turbo_short(method, path, **kwargs):
            return type("Response", (), {"json": lambda self: {"data": {"video_script": "uma frase curta."}}})()

        with patch.object(self.main, "turbo_request", new=fake_turbo_short):
            short_result = asyncio.run(self.main.short_script(self.main.ShortScriptInput(subject="tema", paragraph_number=5)))
        self.assertFalse(short_result["trimmed"])

    def test_create_job_trims_an_oversized_auto_generated_short_script(self):
        from unittest.mock import patch
        self._completed_job()
        self._connected(("yt", "youtube"))
        long_script_text = " ".join(f"palavra{i}." for i in range(500))
        sent = {}

        async def fake_turbo(method, path, **kwargs):
            if path == "/api/v1/videos":
                sent["video"] = kwargs["json"]
                return type("Response", (), {"json": lambda self: {"data": {"task_id": "t1"}}})()
            return type("Response", (), {"json": lambda self: {"data": {"video_script": long_script_text}}})()

        with patch.object(self.main, "turbo_request", new=fake_turbo), patch.object(self.main, "visual_terms", new=self._async_return((["term"], 5.0))):
            job = asyncio.run(self.main.create_job("p1", self.main.JobCreate(subject="tema", format="short", channel_ids=["yt"])))
        self.assertLess(len(job["script"].split()), 500)
        self.assertLessEqual(len(job["script"].split()), self.main.short_script_max_words(5))

    def test_create_job_trims_a_pasted_script_to_the_chosen_duration(self):
        from unittest.mock import patch
        self._completed_job()
        self._connected(("yt", "youtube"))
        oversized = " ".join(f"palavra{i}." for i in range(600))
        sent = {}

        async def fake_turbo(method, path, **kwargs):
            if path == "/api/v1/videos":
                sent["video"] = kwargs["json"]
                return type("Response", (), {"json": lambda self: {"data": {"task_id": "t1"}}})()
            return type("Response", (), {"json": lambda self: {"data": {"video_script": ""}}})()

        with patch.object(self.main, "turbo_request", new=fake_turbo), patch.object(self.main, "visual_terms", new=self._async_return((["term"], 5.0))):
            short = asyncio.run(self.main.create_job("p1", self.main.JobCreate(subject="tema", script=oversized, format="short", channel_ids=["yt"], settings={"paragraph_number": 4, "video_concat_mode": "random"})))  # the cap applies in every clip mode
            long = asyncio.run(self.main.create_job("p1", self.main.JobCreate(subject="tema", script=oversized, format="long", channel_ids=["yt"], settings={"target_minutes": 2})))
        self.assertLessEqual(len(short["script"].split()), self.main.short_script_max_words(4))  # 60 s Short
        self.assertLessEqual(len(long["script"].split()), 2 * self.main.WORDS_PER_MINUTE)
        self.assertEqual(sent["video"]["video_script"], long["script"])
        self.assertNotIn("target_minutes", sent["video"])  # Cockpit-only setting, never sent to Turbo

    def test_long_script_never_exceeds_the_chosen_minutes(self):
        from unittest.mock import patch
        outline = json.dumps({"title": "T", "sections": [{"heading": f"S{i}", "points": ["p"]} for i in range(4)]})
        oversized_section = " ".join(f"palavra{i}." for i in range(400))  # way more than 5 min / 4 sections calls for

        async def fake_chat(prompt, json_mode=False):
            return outline if json_mode else oversized_section

        with patch.object(self.main, "llm_chat", new=fake_chat):
            result = asyncio.run(self.main.long_script(self.main.LongScriptInput(subject="oceano", minutes=5)))
        self.assertTrue(result["trimmed"])
        self.assertLessEqual(len(result["script"].split()), 5 * self.main.WORDS_PER_MINUTE)
