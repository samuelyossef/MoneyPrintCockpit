import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from gateway import auto_generation, main, visual_specialists as specialists


class VisualSpecialistsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        main.DATA_DIR = Path(self.temp.name)
        main.DATABASE = main.DATA_DIR / "cockpit.sqlite3"
        main.ADMIN_FILE = main.DATA_DIR / "secrets" / "admin.json"
        main.initialize_database()

    def tearDown(self):
        self.temp.cleanup()

    def test_stock_keys_use_existing_app_section(self):
        config = main.DATA_DIR / "moneyprinter" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[app]\npexels_api_keys = ["test-token"]\n', encoding="utf-8")
        self.assertEqual(specialists._key("pexels_api_keys"), "test-token")
        self.assertEqual(specialists._key("pixabay_api_keys"), "")

    def test_still_image_sampling_does_not_seek_past_only_frame(self):
        path = Path(self.temp.name) / "still.jpg"
        import shutil
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg unavailable")
        import subprocess
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=720x1280:d=0.1", "-frames:v", "1", str(path)], capture_output=True, check=True)
        self.assertGreater(len(specialists.sample_frame(path)), 1000)

    def test_topic_and_editorial_review_retry_invalid_json(self):
        calls = []
        async def chat(prompt, **kwargs):
            calls.append((prompt, kwargs))
            if len(calls) in (1, 3):
                return ""
            return '{"topic":"Morning light"}' if len(calls) == 2 else '{"approved":true,"reason":"Dentro da duração"}'
        context = {"format": "short", "settings": {"video_language": "en-US", "voice_rate": 1, "paragraph_number": 4},
                   "channel": {"name": "Deto", "niche": "Sleep"}, "editorial": "Natural tone", "visual_guide": ""}
        with patch.object(main, "llm_chat", new=chat):
            topic = asyncio.run(auto_generation._json_generation('Responda {"topic":"tema"}', "topic", 500))
            review = asyncio.run(specialists.review_script("word " * 150, topic, "Clear opening", context, manual=True))
        self.assertEqual(topic, "Morning light")
        self.assertTrue(review["approved"])
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(kwargs["task"] == "json" for _, kwargs in calls))

    def test_short_manual_script_outside_duration_skips_model(self):
        context = {"format": "short", "settings": {"video_language": "en-US", "voice_rate": 1, "paragraph_number": 4},
                   "channel": {"name": "Deto", "niche": "Sleep"}, "editorial": "Natural tone", "visual_guide": ""}
        with patch.object(main, "llm_chat") as chat:
            report = asyncio.run(specialists.review_script("word " * 142, "Morning light", "", context, manual=True))
        self.assertFalse(report["approved"])
        self.assertIn("57s", report["reason"])
        chat.assert_not_called()

    def test_curator_rejects_deceptive_first_result_and_selects_second(self):
        item_id = "00000000-0000-0000-0000-000000000001"
        candidates = [{"id": "first", "provider": "pexels", "kind": "image", "url": "https://images.pexels.com/a.jpg", "source_page": "https://pexels.com/a", "asset_id": "a", "width": 900, "height": 1600, "duration": 5, "query": "sunrise"},
                      {"id": "second", "provider": "pixabay", "kind": "image", "url": "https://cdn.pixabay.com/b.jpg", "source_page": "https://pixabay.com/b", "asset_id": "b", "width": 900, "height": 1600, "duration": 5, "query": "sunrise"}]
        judged = []
        async def search(*_args):
            return candidates
        async def download(candidate, _item, index, alternative):
            path = specialists.material_root(item_id) / f"scene{index:03d}option{alternative}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"image")
            return path
        async def judge(_frame, _prompt):
            judged.append(True)
            return {"approved": len(judged) == 2, "score": 90 if len(judged) == 2 else 10, "reason": "Sol da manhã" if len(judged) == 2 else "Tema diferente"}
        context = {"settings": {"video_aspect": "9:16", "video_clip_duration": 5}, "channel": {"niche": "Saúde"}, "visual_guide": "luz natural", "subject": "Sol matinal"}
        with patch.object(specialists, "search_stock", new=search), patch.object(specialists, "download_candidate", new=download), \
             patch.object(specialists, "probe", return_value={"width": 900, "height": 1600, "duration": 5}), \
             patch.object(specialists, "sample_frame", return_value=b"frame"), patch.object(specialists, "judge_frame", new=judge):
            scene = asyncio.run(specialists.curate_scene(item_id, 0, "Sunlight resets the clock", "sunrise", context))
        self.assertEqual(scene["selected"], "second")
        self.assertEqual(scene["candidates"][0]["status"], "rejected")
        self.assertEqual(scene["candidates"][1]["status"], "approved")
        self.assertEqual(specialists.approved_materials(item_id, [scene])[0]["url"], specialists.turbo_material_url(item_id, "scene000option1.jpg"))

    def test_curator_retries_with_query_grounded_in_narration(self):
        item_id = "00000000-0000-0000-0000-000000000003"
        searches = []

        async def search(query, _aspect, _seconds):
            searches.append(query)
            return [{"id": query, "provider": "pexels", "kind": "image", "url": "https://images.pexels.com/a.jpg",
                     "source_page": "https://pexels.com/a", "asset_id": query, "width": 900, "height": 1600,
                     "duration": 5, "query": query}]

        async def download(_candidate, _item, index, alternative):
            path = specialists.material_root(item_id) / f"scene{index:03d}option{alternative}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"image")
            return path

        async def judge(_frame, _prompt):
            return {"approved": len(searches) == 2, "score": 90 if len(searches) == 2 else 10,
                    "reason": "Corresponde à narração" if len(searches) == 2 else "Assunto diferente"}

        async def repair(prompt, **_kwargs):
            self.assertIn("morning sunlight", prompt)
            return {"query": "person walking in morning sunlight"}

        context = {"settings": {"video_aspect": "9:16", "video_clip_duration": 5},
                   "channel": {"niche": "Saúde"}, "visual_guide": "luz natural", "subject": "Luz matinal"}
        with patch.object(specialists, "search_stock", new=search), patch.object(specialists, "download_candidate", new=download), \
             patch.object(specialists, "probe", return_value={"width": 900, "height": 1600, "duration": 5}), \
             patch.object(specialists, "sample_frame", return_value=b"frame"), patch.object(specialists, "judge_frame", new=judge), \
             patch.object(main, "llm_json_object", new=repair):
            scene = asyncio.run(specialists.curate_scene(item_id, 0, "morning sunlight sets the circadian clock", "drinking water", context))
        self.assertEqual(searches, ["drinking water", "person walking in morning sunlight"])
        self.assertEqual(scene["query"], searches[-1])
        self.assertEqual(scene["selected"], searches[-1])
        self.assertEqual([candidate["status"] for candidate in scene["candidates"]], ["rejected", "approved"])

    def test_vision_retries_transient_http_error(self):
        import httpx
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"approved":true,"score":90,"reason":"Luz natural"}'}}]})

        real_client = httpx.AsyncClient

        def client(*_args, **_kwargs):
            return real_client(transport=httpx.MockTransport(handler))

        with patch.object(specialists, "vision_model", return_value=("https://example.com/v1", "key", "model")), \
             patch.object(specialists.httpx, "AsyncClient", new=client):
            result = asyncio.run(specialists.judge_frame(b"frame", "Check sunlight"))
        self.assertEqual(len(calls), 2)
        self.assertTrue(result["approved"])

    def test_quality_gate_requires_approved_report_for_new_job(self):
        with main.database() as db:
            db.execute("INSERT INTO projects(id, name, created_at, updated_at) VALUES ('p', 'P', ?, ?)", (main.now(), main.now()))
            db.execute("INSERT INTO channels(id, platform, name, status, created_at, updated_at) VALUES ('c', 'youtube', 'C', 'connected', ?, ?)", (main.now(), main.now()))
            db.execute("INSERT INTO generation_batches(id, project_id, client_request_id, request_hash, input_json, created_at) VALUES ('b', 'p', 'request-1', 'h', '{}', ?)", (main.now(),))
            db.execute("INSERT INTO generation_batch_items(id, batch_id, channel_id, channel_name, state, stage, context_json, created_at, updated_at) VALUES ('i', 'b', 'c', 'C', 'submitted', 'rendering', '{}', ?, ?)", (main.now(), main.now()))
            db.execute("INSERT INTO video_jobs(id, project_id, subject, request_json, state, generation_item_id, created_at, updated_at) VALUES ('j', 'p', 'Tema', '{}', 'completed', 'i', ?, ?)", (main.now(), main.now()))
            job = db.execute("SELECT * FROM video_jobs WHERE id='j'").fetchone()
            with self.assertRaises(HTTPException):
                main.require_quality_approval(db, job)
            db.execute("INSERT INTO generation_quality(item_id, job_id, state, updated_at) VALUES ('i', 'j', 'blocked', ?)", (main.now(),))
            with self.assertRaises(HTTPException):
                main.require_quality_approval(db, job)
            db.execute("UPDATE generation_quality SET state='approved' WHERE item_id='i'")
            main.require_quality_approval(db, job)

    def test_missing_vision_capability_pauses_on_same_provider(self):
        with patch.object(main, "llm_config", return_value=("nvidia", "https://integrate.api.nvidia.com/v1", "key", "z-ai/glm-5.3")):
            with self.assertRaises(HTTPException) as raised:
                specialists.vision_model()
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("mesmo provedor", raised.exception.detail)

    def test_blocked_final_video_creates_review_batch_for_selected_alternative(self):
        original_id = "00000000-0000-0000-0000-000000000002"
        scene = {"index": 0, "narration": "sun", "query": "sunrise", "selected": "first", "candidates": [
            {"id": "first", "filename": "scene000option0.jpg", "status": "approved", "duration": 5},
            {"id": "second", "filename": "scene000option1.jpg", "status": "rejected", "duration": 5}]}
        folder = specialists.material_root(original_id)
        folder.mkdir(parents=True)
        for candidate in scene["candidates"]:
            (folder / candidate["filename"]).write_bytes(b"image")
        request = {"format": "short", "channel_ids": ["c"], "client_request_id": "original01"}
        with main.database() as db:
            db.execute("INSERT INTO projects(id, name, created_at, updated_at) VALUES ('p', 'P', ?, ?)", (main.now(), main.now()))
            db.execute("INSERT INTO channels(id, platform, name, status, created_at, updated_at) VALUES ('c', 'youtube', 'C', 'connected', ?, ?)", (main.now(), main.now()))
            db.execute("INSERT INTO generation_batches(id, project_id, client_request_id, request_hash, input_json, created_at) VALUES ('b', 'p', 'original01', 'h', ?, ?)", (json.dumps(request), main.now()))
            db.execute("INSERT INTO generation_batch_items(id, batch_id, channel_id, channel_name, state, stage, context_json, generated_json, created_at, updated_at) VALUES (?, 'b', 'c', 'C', 'submitted', 'rendering', '{}', ?, ?, ?)",
                       (original_id, json.dumps({"subject": "Sun", "script": "Sun", "requirements": "clear", "terms": "sunrise", "scenes": [scene]}), main.now(), main.now()))
            db.execute("INSERT INTO video_jobs(id, project_id, subject, request_json, state, generation_item_id, created_at, updated_at) VALUES ('j', 'p', 'Sun', '{}', 'completed', ?, ?, ?)", (original_id, main.now(), main.now()))
            db.execute("INSERT INTO generation_quality(item_id, job_id, state, updated_at) VALUES (?, 'j', 'blocked', ?)", (original_id, main.now()))
        changed = auto_generation.review_auto_item("b", original_id, auto_generation.ManualReviewInput(scene_index=0, candidate_id="second"))
        repeated = auto_generation.review_auto_item("b", original_id, auto_generation.ManualReviewInput(scene_index=0, candidate_id="second"))
        self.assertEqual(changed["batch_id"], repeated["batch_id"])
        with main.database() as db:
            new = db.execute("SELECT id, generated_json FROM generation_batch_items WHERE batch_id=?", (changed["batch_id"],)).fetchone()
        self.assertEqual(json.loads(new["generated_json"])["scenes"][0]["selected"], "second")
        self.assertTrue((specialists.material_root(new["id"]) / "scene000option1.jpg").is_file())

    def test_final_review_reports_bad_frame(self):
        path = Path(self.temp.name) / "final.mp4"
        path.write_bytes(b"mp4")
        scenes = [{"narration": "sun", "selected": "a", "candidates": [{"id": "a", "source_page": "https://pexels.com/a"}]}]
        async def judge(*_args):
            return {"approved": False, "score": 10, "reason": "Tela preta"}
        with patch.object(specialists, "probe", return_value={"width": 1080, "height": 1920, "duration": 60, "audio": True}), \
             patch.object(specialists, "sample_frame", return_value=b"frame"), patch.object(specialists, "judge_frame", new=judge), \
             patch.object(specialists, "detect_black_segments", return_value=[]), \
             patch.object(specialists, "inspect_audio", return_value={"audible": True, "continuous": True}), \
             patch.object(main, "narration_seconds", return_value=60):
            report = asyncio.run(specialists.inspect_final_video(path, scenes, {"video_aspect": "9:16", "voice_rate": 1}, "script"))
        self.assertFalse(report["approved"])
        self.assertEqual(report["observations"][0]["reason"], "Tela preta")
        self.assertEqual(report["failed_checks"], ["visual_scenes"])

    def test_final_review_enforces_actual_short_duration(self):
        path = Path(self.temp.name) / "final.mp4"
        path.write_bytes(b"mp4")
        with patch.object(specialists, "probe", return_value={"width": 1080, "height": 1920, "duration": 56.43, "audio": True}), \
             patch.object(specialists, "detect_black_segments", return_value=[]), \
             patch.object(specialists, "inspect_audio", return_value={"audible": True, "continuous": True}), \
             patch.object(main, "narration_seconds", return_value=64), patch.object(specialists, "judge_frame") as judge:
            report = asyncio.run(specialists.inspect_final_video(
                path, [{"narration": "sunlight", "selected": "a", "candidates": [{"id": "a", "source_page": "https://pexels.com/a"}]}],
                {"video_aspect": "9:16", "voice_rate": 1}, "script", (60, 66)))
        self.assertFalse(report["approved"])
        self.assertFalse(report["checks"]["duration"])
        self.assertEqual(report["failed_checks"], ["duration"])
        self.assertEqual(report["measurements"]["duration_seconds"], 56.43)
        self.assertEqual((report["measurements"]["minimum_seconds"], report["measurements"]["maximum_seconds"]), (60, 66))
        self.assertIn("56.4 s", report["reason"])
        judge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
