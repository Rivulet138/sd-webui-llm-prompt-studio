"""Queue status stays lightweight even when legacy records contain images."""
from contextlib import contextmanager
import html
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_queue_regressions import StudioDB, ui_functions


class QueueImageRemovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = StudioDB(Path(self.tmp.name) / "queue.db")
        self.db.enqueue_server_queue("batch", [{"id": "job", "request": "one cat", "target": "txt2img"}])

    def test_status_does_not_read_or_return_legacy_images(self):
        legacy_images = json.dumps(["data:image/png;base64," + "A" * 1024 * 1024])
        with self.db._connection() as conn:
            conn.execute("UPDATE server_queue_jobs SET images_json=?", (legacy_images,))
        original_connection = self.db._connection

        @contextmanager
        def status_connection():
            with original_connection() as conn:
                def authorize(action, table, column, database, source):
                    if action == sqlite3.SQLITE_READ and table == "server_queue_jobs" and column == "images_json":
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                conn.set_authorizer(authorize)
                yield conn

        api = ui_functions("_server_queue_snapshot", DB=self.db)
        with patch.object(self.db, "_connection", status_connection):
            snapshot = api._server_queue_snapshot("batch")
        self.assertEqual(snapshot["counts"], {"pending": 1})
        self.assertEqual(snapshot["jobs"][0]["request"], "one cat")
        self.assertNotIn("images", snapshot["jobs"][0])
        self.assertNotIn("images_json", snapshot["jobs"][0])
        self.assertLess(len(json.dumps(snapshot)), 4096)
        with original_connection() as conn:
            self.assertEqual(conn.execute("SELECT images_json FROM server_queue_jobs").fetchone()[0], legacy_images)

    def test_worker_claim_does_not_read_legacy_images(self):
        legacy_images = json.dumps(["data:image/png;base64," + "A" * 1024 * 1024])
        with self.db._connection() as conn:
            conn.execute("UPDATE server_queue_jobs SET images_json=?", (legacy_images,))
        original_connection = self.db._connection

        @contextmanager
        def claim_connection():
            with original_connection() as conn:
                def authorize(action, table, column, database, source):
                    if action == sqlite3.SQLITE_READ and table == "server_queue_jobs" and column == "images_json":
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                conn.set_authorizer(authorize)
                yield conn

        with patch.object(self.db, "_connection", claim_connection):
            claimed = self.db.claim_server_queue_job()
        self.assertEqual(claimed["id"], "job")
        self.assertNotIn("images_json", claimed)
        self.assertNotIn("images", claimed)

    def test_completed_job_tracks_prompt_without_storing_images(self):
        job = self.db.claim_server_queue_job()
        self.assertTrue(self.db.update_server_queue_job("job", "completed", prompt="one cat", expected_claim_token=job["claim_token"]))
        with self.db._connection() as conn:
            row = conn.execute("SELECT status,prompt,images_json FROM server_queue_jobs").fetchone()
        self.assertEqual(tuple(row), ("completed", "one cat", "[]"))

    def test_server_html_ignores_legacy_images_and_keeps_status(self):
        api = ui_functions("_server_queue_html", "_parse_generation_settings", html=html)
        markup = api._server_queue_html({"jobs": [{"position": 1, "status": "completed", "prompt": "cat <tree>",
            "images": ["data:image/png;base64,legacy"], "error": ""}]})
        self.assertIn("completed", markup)
        self.assertIn("cat &lt;tree&gt;", markup)
        self.assertNotIn("<img", markup)
        self.assertNotIn("base64", markup)

    def test_inline_enqueue_returns_only_stage_ids_and_status(self):
        enqueue = Mock(return_value={"batch_id": "new-batch"})
        api = ui_functions("_inline_cache_enqueue_results", "_parse_generation_settings",
                           _enqueue_server_queue=enqueue, _safe_error=str)
        for stage in ({}, {"items": []}, {"items": [{"record": {"id": 1}, "result": ""}]}):
            with self.subTest(stage=stage):
                self.assertEqual(len(api._inline_cache_enqueue_results(stage)), 3)
        stage = {"items": [{"record": {"id": 1}, "result": "one cat"}]}
        updated, ids, status = api._inline_cache_enqueue_results(stage)
        self.assertEqual(ids, ["new-batch"])
        self.assertEqual(updated["queued_ids"], ["1"])
        self.assertIn("1", status)
        enqueue.side_effect = RuntimeError("failed")
        self.assertEqual(len(api._inline_cache_enqueue_results(stage)), 3)


if __name__ == "__main__":
    unittest.main()
