import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gateway import auto_generation, editorial_quality, main, visual_specialists as specialists
from gateway.tests import test_auto_generation as generation_tests


class QualityTests(unittest.TestCase):
    def test_generated_narration_removes_stage_directions_and_spoken_markup(self):
        text = '```text\n**Veja** [este exemplo](https://example.com). [pausa] Agora ouça.\n```'
        self.assertEqual(main.clean_narration(text), 'Veja este exemplo.  Agora ouça.')

    def test_narration_diagnostics_detect_repeated_and_long_sentences(self):
        text = "Uma ideia concreta para começar. Uma ideia concreta para começar. " + " ".join(["palavra"] * 40) + "."
        result = editorial_quality.narration_diagnostics(text)
        self.assertEqual(result["long_sentences"], 1)
        self.assertEqual(len(result["repeated_sentences"]), 1)

    def test_best_approved_scene_wins_and_variety_breaks_close_scores(self):
        scene = {"selected": "first", "candidates": [
            {"id": "first", "status": "approved", "score": 72, "kind": "video"},
            {"id": "best", "status": "approved", "score": 95, "kind": "video"},
            {"id": "alternative", "status": "approved", "score": 90, "kind": "video"},
            {"id": "rejected", "status": "rejected", "score": 100},
        ]}
        specialists.choose_scene_material(scene)
        self.assertEqual(scene["selected"], "best")
        specialists.choose_scene_material(scene, {"best"})
        self.assertEqual(scene["selected"], "alternative")

    def test_full_hd_preferred_over_larger_download_without_choosing_low_resolution(self):
        sizes = [{"width": 3840, "height": 2160}, {"width": 1280, "height": 720}, {"width": 1920, "height": 1080}]
        self.assertEqual(sorted(sizes, key=specialists.rendition_priority)[0], sizes[2])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required for actual audio measurement")
    def test_real_audio_measurement_distinguishes_tone_and_silence(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, source, audible in [("tone", "sine=frequency=440:duration=5", True), ("silence", "anullsrc=r=44100:cl=mono:d=5", False)]:
                path = Path(folder) / f"{name}.wav"
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source, str(path)], check=True, capture_output=True)
                result = specialists.inspect_audio(path)
                self.assertEqual(result["audible"], audible)
                self.assertEqual(result["continuous"], audible)


class PipelineQualityTests(unittest.TestCase):
    # Reuse the isolated database and provider fixtures, without copying their contract.
    setUp = generation_tests.AutoGenerationTests.setUp
    tearDown = generation_tests.AutoGenerationTests.tearDown
    add_channel = generation_tests.AutoGenerationTests.add_channel
    payload = generation_tests.AutoGenerationTests.payload

    def test_curation_failure_keeps_parallel_successes_and_resumes_only_missing(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"], subject="Tema", script="manual " * 160, requirements="Clareza", terms="forest"))
        calls = []
        reject = True
        async def curate(item_id, index, chunk, query, context):
            calls.append(index)
            await asyncio.sleep(0)
            approved = not (reject and index == 1)
            return {"index": index, "narration": chunk, "query": query, "selected": f"clip-{index}" if approved else None, "candidates": [{"id": f"clip-{index}", "status": "approved" if approved else "rejected"}]}
        async def turbo(*args, **kwargs):
            return type("Response", (), {"json": lambda self: {"data": {"task_id": "resumed"}}})()
        with patch.object(specialists, "curate_scene", new=curate), patch.object(main, "turbo_request", new=turbo):
            asyncio.run(auto_generation.process_next_item("worker"))
            with main.database() as db:
                row = db.execute("SELECT id,state,generated_json FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()
            self.assertEqual(row["state"], "paused")
            self.assertEqual([scene["selected"] for scene in json.loads(row["generated_json"])["scenes"][:3]], ["clip-0", None, "clip-2"])
            reject = False
            auto_generation.retry_auto_item(batch["batch_id"], row["id"])
            asyncio.run(auto_generation.process_next_item("worker"))
        self.assertEqual(calls.count(0), 1)
        self.assertEqual(calls.count(2), 1)
        self.assertEqual(calls.count(1), 2)

    def test_script_edited_during_review_is_preserved(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"], subject="Tema", requirements="Clareza"))
        with main.database() as db:
            db.execute("UPDATE generation_batch_items SET state='paused' WHERE batch_id=?", (batch["batch_id"],))
            item_id = db.execute("SELECT id FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()["id"]
        auto_generation.review_auto_item(batch["batch_id"], item_id, auto_generation.ManualReviewInput(script="Minha edição manual"))
        async def reject(*args, **kwargs):
            self.assertTrue(kwargs["manual"])
            return {"approved": False, "reason": "Texto insuficiente"}
        with patch.object(specialists, "review_script", new=reject), patch.object(auto_generation, "_generate_script") as writer:
            asyncio.run(auto_generation.process_next_item("worker"))
        writer.assert_not_called()

    def test_generated_script_gets_one_editorial_repair(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"], subject="Tema", requirements="Clareza", terms="forest"))
        with main.database() as db:
            db.execute("UPDATE generation_batch_items SET generated_json=? WHERE batch_id=?", (json.dumps({"subject": "Tema", "requirements": "Clareza", "script": "primeira versão"}), batch["batch_id"]))
        reviews = 0
        async def review(*args, **kwargs):
            nonlocal reviews
            reviews += 1
            return {"approved": reviews > 1, "reason": "Inclua um exemplo concreto"}
        async def turbo(*args, **kwargs):
            return type("Response", (), {"json": lambda self: {"data": {"task_id": "task"}}})()
        with patch.object(specialists, "review_script", new=review), patch.object(auto_generation, "_generate_script", return_value="versão corrigida") as writer, patch.object(main, "turbo_request", new=turbo):
            asyncio.run(auto_generation.process_next_item("worker"))
        self.assertEqual(writer.call_count, 1)
        self.assertIn("exemplo concreto", writer.call_args.args[3])
        with main.database() as db:
            row = db.execute("SELECT state,generated_json FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()
        self.assertEqual(row["state"], "submitted")
        self.assertTrue(json.loads(row["generated_json"])["editorial_repair_attempted"])

    def test_parallel_curation_is_bounded_and_preserves_scene_order(self):
        active = maximum = 0
        async def curate(item_id, index, chunk, query, context):
            nonlocal active, maximum
            active += 1
            maximum = max(active, maximum)
            await asyncio.sleep(0.01 * (3 - index % 3))
            active -= 1
            return {"index": index, "narration": chunk, "query": query, "selected": f"clip-{index}", "candidates": [{"id": f"clip-{index}", "status": "approved", "score": 90}]}
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"], subject="Tema", script="manual " * 160, requirements="Clareza", terms="forest, river"))
        async def turbo(*args, **kwargs):
            return type("Response", (), {"json": lambda self: {"data": {"task_id": "task"}}})()
        with patch.object(specialists, "curate_scene", new=curate), patch.object(main, "turbo_request", new=turbo):
            asyncio.run(auto_generation.process_next_item("worker"))
        with main.database() as db:
            row = db.execute("SELECT generated_json,state FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()
        scenes = json.loads(row["generated_json"])["scenes"]
        self.assertEqual(row["state"], "submitted")
        self.assertEqual(maximum, 3)
        self.assertEqual([scene["index"] for scene in scenes], list(range(len(scenes))))

    def test_manual_script_is_not_rewritten_after_editorial_rejection(self):
        batch = auto_generation.create_auto_batch("project", self.payload(channel_ids=["yt"], subject="Tema", script="Texto manual", requirements="Clareza"))
        async def reject(*args, **kwargs):
            return {"approved": False, "reason": "Precisa de exemplos"}
        with patch.object(specialists, "review_script", new=reject), patch.object(auto_generation, "_generate_script") as writer:
            asyncio.run(auto_generation.process_next_item("worker"))
        writer.assert_not_called()
        with main.database() as db:
            row = db.execute("SELECT state,generated_json FROM generation_batch_items WHERE batch_id=?", (batch["batch_id"],)).fetchone()
        self.assertEqual(row["state"], "paused")
        self.assertEqual(json.loads(row["generated_json"])["script"], "Texto manual")
