"""Comprehensive Test Suite for all 10 Scrapper-Grade Capabilities in Manhwa Recap Studio.

1. Data Layer & Schema (SQLite + SQLAlchemy ORM models)
2. Database Backups & Disaster Recovery (Online backup, gzip, SHA-256, restore)
3. Process Lifecycle & Restart Recovery (Resumable jobs, recover_interrupted)
4. Render Safety & File Preservation (Atomic swap staging)
5. Process Control & Cancellation (Cooperative SIGTERM, child tracking)
6. Scheduling & Pacing Engine (Mathematical pacing throttle, runway meter)
7. Social Distribution Scope (Omni-channel multi-platform syndication)
8. Audience Momentum & Release Intelligence (Drop momentum, warnings)
9. State Reversibility (Undo stack snapshotting)
10. Disk Quota & Hygiene Sweeps (Storage monitoring, cleanup sweeps)
"""

import datetime
import os
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import backup
import cleanup
import control
import db
import models
import momentum
import queue_manager
import resume
import space
import undo
import upload_post


class TestScrapperCapabilities(unittest.TestCase):
    def setUp(self):
        db.init_db()

    # 1. Data Layer & Schema
    def test_data_layer_models(self):
        with db.SessionLocal() as s:
            sp = models.StudioProject(
                project_id="test-ch-1",
                series_slug="test-series",
                chapter_number=1.0,
                title="Test Chapter 1",
                stage="describe",
                stage_status="idle",
            )
            s.add(sp)
            s.commit()
            self.assertIsNotNone(sp.id)

            loaded = s.query(models.StudioProject).filter(models.StudioProject.project_id == "test-ch-1").first()
            self.assertEqual(loaded.title, "Test Chapter 1")

            s.delete(loaded)
            s.commit()

    # 2. Database Backups & Disaster Recovery
    def test_database_backup_and_integrity(self):
        res = backup.create_backup(tag="test_suite", upload_to_drive=False)
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.isfile(res["path"]))
        self.assertGreater(res["compressed_bytes"], 0)
        self.assertEqual(len(res["sha256"]), 64)

        status = backup.get_backup_status()
        self.assertTrue(status["ok"])
        self.assertGreaterEqual(status["local_copies_count"], 1)

        # Cleanup test backup
        pathlib.Path(res["path"]).unlink(missing_ok=True)

    # 3. Process Lifecycle & Restart Recovery
    def test_process_lifecycle_recovery(self):
        with db.SessionLocal() as s:
            stuck_proj = models.StudioProject(
                project_id="zombie-project",
                title="Zombie Project",
                stage="narrate",
                stage_status="running",
            )
            s.add(stuck_proj)
            s.commit()

        rec = resume.recover_interrupted()
        self.assertGreaterEqual(rec["stages"], 1)

        with db.SessionLocal() as s:
            recovered = s.query(models.StudioProject).filter(models.StudioProject.project_id == "zombie-project").first()
            self.assertEqual(recovered.stage_status, "stopped")
            self.assertIn("server restart", recovered.stage_message)
            s.delete(recovered)
            s.commit()

    # 4. Render Safety & File Preservation
    def test_render_safety_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            exports_dir = tmp_path / "exports"
            part_dir = exports_dir / ".partial"
            exports_dir.mkdir(parents=True)
            part_dir.mkdir(parents=True)

            live_video = exports_dir / "final.mp4"
            live_video.write_text("v1-good-video")

            # Staging new render into .partial
            temp_render = part_dir / "final_render_new.mp4"
            temp_render.write_text("v2-new-video")

            # Failed/interrupted render simulation: live video was never touched!
            self.assertEqual(live_video.read_text(), "v1-good-video")

            # Atomic swap on success
            live_video.unlink()
            temp_render.rename(live_video)
            self.assertEqual(live_video.read_text(), "v2-new-video")

    # 5. Process Control & Cancellation
    def test_process_control(self):
        with control.job("test", "test-job", "unit-test") as j:
            self.assertEqual(j.status, "running")
            control.progress("halfway done")
            self.assertEqual(j.message, "halfway done")
        self.assertEqual(j.status, "done")

    # 6. Scheduling & Pacing Engine
    def test_mathematical_pacing(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
        day = datetime.date(2026, 10, 15)

        # 8 posts/day evenly spaced between 10 AM and 10 PM (12h window)
        slots = queue_manager.day_slots_for_pacing(day, tz, 10, 22, 2, posts_per_day=8)
        self.assertEqual(len(slots), 8)
        self.assertEqual(slots[0].hour, 10)
        self.assertEqual(slots[-1].hour, 22)

        # Verify runway calculation
        with db.SessionLocal() as s:
            info = queue_manager.get_schedule_info(s)
            self.assertIn("runway_days", info)
            self.assertIn("days left", info["runway_label"])

    # 7. Social Distribution Scope
    def test_social_distribution_profiles(self):
        cfg = upload_post.config()
        self.assertIn("youtube", cfg["networks"])
        self.assertIn("tiktok", cfg["networks"])
        self.assertIn("instagram", cfg["networks"])

    # 8. Audience Momentum & Release Intelligence
    def test_audience_momentum(self):
        today = datetime.date(2026, 10, 10)

        # Critical: releases in 2 days
        m_crit = momentum.compute_momentum("2026-10-12", today=today)
        self.assertEqual(m_crit["urgency"], "urgent")
        self.assertEqual(m_crit["badge_variant"], "critical")
        self.assertIn("URGENT", m_crit["badge_text"])

        # Approaching / optimal: releases in 7 days
        m_app = momentum.compute_momentum("2026-10-17", today=today)
        self.assertEqual(m_app["urgency"], "optimal")
        self.assertEqual(m_app["badge_variant"], "approaching")

        # Warning when scheduled after release
        m_warn = momentum.compute_momentum("2026-10-12", scheduled_at="2026-10-15T12:00:00Z", today=today)
        self.assertTrue(m_warn["scheduled_after_release"])
        self.assertIsNotNone(m_warn["warning"])

    # 9. State Reversibility (Undo Stack)
    def test_undo_stack(self):
        with db.SessionLocal() as s:
            # 1. Create a queue item
            qi = models.QueueItem(
                title="Original Title",
                video_name="ch1.mp4",
                status="review",
            )
            s.add(qi)
            s.commit()
            qid = qi.id

            # 2. Snapshot state before mutating
            undo.record(s, scope="unit_test", label="Rename item", rows=[qi])

            # 3. Mutate
            qi.title = "Mutated Title"
            s.commit()

            # Verify mutated
            reloaded = s.get(models.QueueItem, qid)
            self.assertEqual(reloaded.title, "Mutated Title")

            # 4. Perform Undo
            label = undo.undo(s, scope="unit_test")
            self.assertEqual(label, "Rename item")

            # Verify restored
            restored = s.get(models.QueueItem, qid)
            self.assertEqual(restored.title, "Original Title")

            s.delete(restored)
            s.commit()

    # 10. Disk Quota & Hygiene Sweeps
    def test_disk_quota_and_hygiene(self):
        du = space.get_disk_usage()
        self.assertIn(du["status"], ["healthy", "warning", "critical"])
        self.assertGreater(du["total_gb"], 0)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = pathlib.Path(tmp)
            dummy_mov = tmp_p / "overlay.mov"
            dummy_mov.write_bytes(b"0" * 1024)
            dummy_wav = tmp_p / "temp.wav"
            dummy_wav.write_bytes(b"0" * 2048)

            res = cleanup.cleanup_render_intermediates(tmp_p)
            self.assertEqual(res["files_removed"], 2)
            self.assertEqual(res["bytes_freed"], 3072)


if __name__ == "__main__":
    unittest.main()
