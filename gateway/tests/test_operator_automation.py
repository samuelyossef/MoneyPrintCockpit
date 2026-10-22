from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch


class OperatorAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        os.environ["APP_DATA_DIR"] = self.temporary.name
        from gateway import main
        self.main = main
        main.DATA_DIR = Path(self.temporary.name)
        main.DATABASE = main.DATA_DIR / "cockpit.sqlite3"
        main.ADMIN_FILE = main.DATA_DIR / "secrets" / "admin.json"
        main.initialize_database()
        with main.database() as db:
            db.execute(
                "INSERT INTO channels(id, platform, name, status, language, created_at, updated_at) VALUES ('yt','youtube','Canal','connected','pt-BR',?,?)",
                (main.now(), main.now()),
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def configure(self, **kwargs):
        m = self.main
        return m.put_operator("yt", m.OperatorInput(enabled=True, auto_publish=True, **kwargs))

    def completed_job(self) -> None:
        m = self.main
        with m.database() as db:
            db.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES ('p','P',?,?)", (m.now(), m.now()))
            db.execute(
                "INSERT INTO video_jobs(id,project_id,subject,request_json,state,format,created_by,created_at,updated_at) "
                "VALUES ('j','p','Tema','{}','completed','short','operator',?,?)", (m.now(), m.now()),
            )
            db.execute("UPDATE video_jobs SET auto_publish_authorized=1 WHERE id='j'")
            db.execute("INSERT INTO artifacts(id,job_id,kind,remote_path,filename,created_at) VALUES ('a','j','video','output.mp4','video.mp4',?)", (m.now(),))
            db.execute("INSERT INTO publications(id,job_id,platform,channel_id,state,created_at,updated_at) VALUES ('pub','j','youtube','yt','pending',?,?)", (m.now(), m.now()))
            db.execute("INSERT INTO job_metadata(job_id,value_json,updated_at) VALUES ('j',?,?)",
                       (json.dumps({"title": "Título", "description": "Descrição", "tags": ["um", "dois", "três"]}), m.now()))
            db.execute(
                "INSERT INTO generation_batches(id, project_id, client_request_id, request_hash, input_json, created_at) VALUES ('b','p','request','hash','{}',?)",
                (m.now(),),
            )
            db.execute(
                "INSERT INTO generation_batch_items(id, batch_id, channel_id, channel_name, state, stage, context_json, created_at, updated_at) "
                "VALUES ('item','b','yt','Canal','submitted','done','{}',?,?)",
                (m.now(), m.now()),
            )
            db.execute("UPDATE video_jobs SET generation_item_id='item' WHERE id='j'")
            db.execute("INSERT INTO generation_quality(item_id, job_id, state, updated_at) VALUES ('item','j','approved',?)", (m.now(),))

    def token(self) -> None:
        path = self.main.youtube_token_file("yt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    def test_youtube_opt_in_only_and_tiktok_rejected(self) -> None:
        self.assertFalse(self.main.list_operators()["operators"][0]["auto_publish"])
        self.configure(publish_days=[0, 2], publish_time="14:30")
        op = self.main.list_operators()["operators"][0]
        self.assertEqual((op["auto_publish"], op["publish_days"], op["publish_time"]), (True, [0, 2], "14:30"))
        with self.main.database() as db:
            db.execute("INSERT INTO channels(id,platform,name,status,created_at,updated_at) VALUES ('tt','tiktok','T','connected',?,?)",
                       (self.main.now(), self.main.now()))
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            self.main.put_operator("tt", self.main.OperatorInput(auto_publish=True))

    def test_slot_skips_occupied_window_and_uses_channel_timezone(self) -> None:
        self.configure(publish_days=[0], publish_time="12:00", publish_timezone="America/Sao_Paulo")
        with self.main.database() as db:
            op = db.execute("SELECT * FROM operators WHERE channel_id='yt'").fetchone()
            first = self.main.next_publish_slot(op, db, datetime(2026, 9, 20, 12, tzinfo=UTC))
            self.assertEqual(first, datetime(2026, 9, 21, 15, tzinfo=UTC))
            self.completed_job()
        with self.main.database() as db:
            db.execute("UPDATE publications SET scheduled_at=?, state='platform_scheduled' WHERE id='pub'", (first.isoformat(),))
            op = db.execute("SELECT * FROM operators WHERE channel_id='yt'").fetchone()
            second = self.main.next_publish_slot(op, db, datetime(2026, 9, 20, 12, tzinfo=UTC))
        self.assertEqual(second, first + timedelta(days=7))

    def test_dst_gap_moves_to_next_valid_local_window(self) -> None:
        self.configure(publish_days=[6], publish_time="02:30", publish_timezone="America/New_York")
        with self.main.database() as db:
            op = db.execute("SELECT * FROM operators WHERE channel_id='yt'").fetchone()
            slot = self.main.next_publish_slot(op, db, datetime(2026, 3, 7, tzinfo=UTC))
        self.assertEqual(slot, datetime(2026, 3, 15, 6, 30, tzinfo=UTC))

    def test_disabling_automation_during_download_prevents_upload(self) -> None:
        self.configure()
        self.completed_job()
        self.token()
        async def download(_remote, target):
            target.write_bytes(b"mp4")
            with self.main.database() as db:
                db.execute("UPDATE operators SET auto_publish=0 WHERE channel_id='yt'")
        with patch.object(self.main, "download_turbo_file", new=download), \
             patch.object(self.main, "youtube_credentials", return_value=object()), \
             patch.object(self.main, "youtube_upload") as upload:
            asyncio.run(self.main.prepare_auto_publications())
        upload.assert_not_called()
        with self.main.database() as db:
            self.assertEqual(db.execute("SELECT state FROM video_jobs WHERE id='j'").fetchone()[0], "completed")
            self.assertTrue(db.execute("SELECT paused_reason FROM operators WHERE channel_id='yt'").fetchone()[0])

    def test_concurrent_run_claims_once(self) -> None:
        self.main.put_operator("yt", self.main.OperatorInput(enabled=True))
        async def concurrent():
            return await asyncio.gather(self.main.run_operator("yt"), self.main.run_operator("yt"))
        results = asyncio.run(concurrent())
        self.assertEqual(sum(result.get("batch_id") is not None for result in results), 1)
        with self.main.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM operator_runs").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM generation_batches").fetchone()[0], 1)

    def test_two_workers_cannot_prepare_same_auto_upload(self) -> None:
        self.configure()
        self.completed_job()
        self.token()
        async def scenario():
            started = asyncio.Event()
            release = asyncio.Event()
            async def download(_remote, target):
                started.set()
                await release.wait()
                target.write_bytes(b"mp4")
            def status(_credentials, _video_id):
                with self.main.database() as db:
                    slot = db.execute("SELECT scheduled_at FROM publications WHERE id='pub'").fetchone()[0]
                return {"privacyStatus": "private", "publishAt": slot}
            with patch.object(self.main, "download_turbo_file", new=download), \
                 patch.object(self.main, "youtube_credentials", return_value=object()), \
                 patch.object(self.main, "youtube_upload", return_value="video-1") as upload, \
                 patch.object(self.main, "youtube_video_status", side_effect=status):
                first = asyncio.create_task(self.main.auto_publish_operator_job("j"))
                await started.wait()
                await self.main.auto_publish_operator_job("j")
                release.set()
                await first
            return upload.call_count
        self.assertEqual(asyncio.run(scenario()), 1)

    def test_auto_upload_schedules_private_youtube_video(self) -> None:
        self.configure(publish_days=list(range(7)), publish_time="12:00")
        self.completed_job()
        self.token()
        async def download(_remote, target):
            target.write_bytes(b"mp4")
        def status(_credentials, _video_id):
            with self.main.database() as db:
                slot = db.execute("SELECT scheduled_at FROM publications WHERE id='pub'").fetchone()[0]
            return {"privacyStatus": "private", "publishAt": slot}
        with patch.object(self.main, "download_turbo_file", new=download), \
             patch.object(self.main, "youtube_credentials", return_value=object()), \
             patch.object(self.main, "youtube_upload", return_value="video-1") as upload, \
             patch.object(self.main, "youtube_video_status", side_effect=status):
            asyncio.run(self.main.auto_publish_operator_job("j"))
        with self.main.database() as db:
            pub = db.execute("SELECT * FROM publications WHERE id='pub'").fetchone()
            job = db.execute("SELECT state FROM video_jobs WHERE id='j'").fetchone()
        self.assertEqual((pub["state"], pub["external_id"], job["state"]), ("platform_scheduled", "video-1", "approved"))
        self.assertEqual(upload.call_args.args[3], self.main.parse_time(pub["scheduled_at"]))

    def test_failed_preflight_pauses_without_approval_or_upload(self) -> None:
        self.configure()
        self.completed_job()
        with patch.object(self.main, "youtube_upload") as upload:
            asyncio.run(self.main.prepare_auto_publications())
        upload.assert_not_called()
        with self.main.database() as db:
            self.assertEqual(db.execute("SELECT state FROM video_jobs WHERE id='j'").fetchone()[0], "completed")
            self.assertTrue(db.execute("SELECT paused_reason FROM operators WHERE channel_id='yt'").fetchone()[0])

    def test_blocked_final_review_pauses_and_does_not_upload(self) -> None:
        self.configure()
        self.completed_job()
        self.token()
        with self.main.database() as db:
            db.execute("UPDATE generation_quality SET state='blocked' WHERE item_id='item'")
        with patch.object(self.main, "youtube_upload") as upload:
            self.main.pause_unapproved_operator_reviews()
            asyncio.run(self.main.prepare_auto_publications())
        upload.assert_not_called()
        with self.main.database() as db:
            self.assertEqual(db.execute("SELECT state FROM video_jobs WHERE id='j'").fetchone()[0], "completed")
            self.assertIn("final review", db.execute("SELECT paused_reason FROM operators WHERE channel_id='yt'").fetchone()[0])

    def test_old_pending_job_is_not_published_after_opt_in(self) -> None:
        self.configure()
        self.completed_job()
        self.token()
        with self.main.database() as db:
            db.execute("UPDATE video_jobs SET auto_publish_authorized=0 WHERE id='j'")
        with patch.object(self.main, "youtube_upload") as upload:
            asyncio.run(self.main.prepare_auto_publications())
        upload.assert_not_called()
        with self.main.database() as db:
            self.assertEqual(db.execute("SELECT state FROM publications WHERE id='pub'").fetchone()[0], "pending")

    def test_youtube_upload_uses_private_resumable_publish_at(self) -> None:
        slot = datetime(2026, 9, 25, 15, tzinfo=UTC)
        metadata = {"title": "Título", "description": "Descrição", "tags": ["tag"], "category_id": "22", "language": "pt-BR", "format": "short"}
        with patch("googleapiclient.discovery.build") as build, patch("googleapiclient.http.MediaFileUpload") as media:
            insert = build.return_value.videos.return_value.insert.return_value
            insert.next_chunk.return_value = (None, {"id": "video-1"})
            result = self.main.youtube_upload(object(), Path("video.mp4"), metadata, slot)
        self.assertEqual(result, "video-1")
        body = build.return_value.videos.return_value.insert.call_args.kwargs["body"]
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertEqual(body["status"]["publishAt"], "2026-09-25T15:00:00Z")
        self.assertTrue(media.call_args.kwargs["resumable"])

    def test_manual_retry_clears_auto_upload_marker(self) -> None:
        self.configure()
        self.completed_job()
        self.token()
        with self.main.database() as db:
            db.execute("UPDATE video_jobs SET state='approved' WHERE id='j'")
            db.execute("UPDATE publications SET auto_publish=1, state='failed', scheduled_at=? WHERE id='pub'",
                       ((datetime.now(UTC) + timedelta(days=1)).isoformat(),))
        with patch.object(self.main, "download_turbo_file", new=AsyncMock()), \
             patch.object(self.main, "youtube_credentials", return_value=object()), \
             patch.object(self.main, "youtube_upload", return_value="manual-1"):
            asyncio.run(self.main.publish_job("j"))
        with self.main.database() as db:
            pub = db.execute("SELECT auto_publish, scheduled_at, state FROM publications WHERE id='pub'").fetchone()
        self.assertEqual(tuple(pub), (0, None, "published"))

    def test_active_upload_lease_is_not_mistaken_for_crash(self) -> None:
        self.configure()
        self.completed_job()
        with self.main.database() as db:
            db.execute("UPDATE publications SET state='uploading', auto_publish=1, auto_lease_until=? WHERE id='pub'",
                       ((datetime.now(UTC) + timedelta(hours=1)).isoformat(),))
        asyncio.run(self.main.reconcile_auto_publications())
        with self.main.database() as db:
            self.assertEqual(db.execute("SELECT state FROM publications WHERE id='pub'").fetchone()[0], "uploading")
            db.execute("UPDATE publications SET auto_lease_until=? WHERE id='pub'",
                       ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(),))
        asyncio.run(self.main.reconcile_auto_publications())
        with self.main.database() as db:
            self.assertEqual(db.execute("SELECT state FROM publications WHERE id='pub'").fetchone()[0], "reconciliation_required")

    def test_restart_marks_unknown_upload_for_manual_reconciliation(self) -> None:
        self.configure()
        self.completed_job()
        with self.main.database() as db:
            db.execute("UPDATE publications SET state='uploading', auto_publish=1 WHERE id='pub'")
        asyncio.run(self.main.reconcile_auto_publications())
        with self.main.database() as db:
            self.assertEqual(db.execute("SELECT state FROM publications WHERE id='pub'").fetchone()[0], "reconciliation_required")
            self.assertTrue(db.execute("SELECT paused_reason FROM operators WHERE channel_id='yt'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()

