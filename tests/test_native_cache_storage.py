"""Durable native cache jobs tested only against temporary SQLite databases."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prompt_studio_core import StudioDB


class NativeCacheStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "control.db"
        self.db = StudioDB(self.path)
        self.context = {
            "token": "cache-job", "slot": "txt2img", "source": "processed_cache",
            "after_id": 4, "next_cursor": 29, "base_prompt": "fixed prompt",
            "records": [{"id": ident, "prompt": f"prompt {ident}"} for ident in (7, 13, 29)],
            "state": "pending", "created_at": 123.5,
        }
        self.cursor_key = "native_cache_cursor:txt2img:processed_cache"

    def activate(self):
        self.db.save_native_cache_job(self.context)
        self.assertTrue(self.db.transition_native_cache_job(self.context["token"], "pending", "applying"))
        self.assertTrue(self.db.transition_native_cache_job(self.context["token"], "applying", "active"))

    def test_save_and_reinitialize_preserve_context_and_existing_data(self):
        prompt_id = self.db.save_prompt("existing prompt")
        self.db.set_setting("existing-setting", {"keep": True})
        self.assertIsNone(self.db.save_native_cache_job(self.context))
        reopened = StudioDB(self.path)
        self.assertEqual(reopened.get_native_cache_job("cache-job"), self.context)
        self.assertEqual(reopened.get_prompt(prompt_id)["prompt"], "existing prompt")
        self.assertEqual(reopened.get_setting("existing-setting"), {"keep": True})
        with reopened._connection() as conn:
            columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(native_cache_jobs)")}
            stored = conn.execute("SELECT * FROM native_cache_jobs WHERE token='cache-job'").fetchone()
        self.assertEqual(set(columns), {"token", "context_json", "state", "created_at", "updated_at"})
        self.assertEqual(columns["token"]["pk"], 1)
        self.assertEqual(json.loads(stored["context_json"])["records"], self.context["records"])
        self.assertGreater(stored["created_at"], 0)
        self.assertGreaterEqual(stored["updated_at"], stored["created_at"])

    def test_save_starts_pending_without_mutating_input(self):
        self.context["state"] = "active"
        self.db.save_native_cache_job(self.context)
        self.assertEqual(self.context["state"], "active")
        self.assertEqual(self.db.get_native_cache_job("cache-job")["state"], "pending")

    def test_duplicate_token_does_not_replace_existing_job(self):
        self.activate()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.save_native_cache_job({**self.context, "base_prompt": "replacement"})
        stored = self.db.get_native_cache_job("cache-job")
        self.assertEqual(stored["state"], "active")
        self.assertEqual(stored["base_prompt"], "fixed prompt")

    def test_reads_return_independent_dicts_and_authoritative_state(self):
        self.db.save_native_cache_job(self.context)
        self.assertTrue(self.db.transition_native_cache_job("cache-job", "pending", "applying"))
        stored = self.db.get_native_cache_job("cache-job")
        stored["records"][0]["prompt"] = "local mutation"
        self.assertFalse(self.db.transition_native_cache_job("cache-job", "pending", "active"))
        fresh = self.db.get_native_cache_job("cache-job")
        self.assertEqual(fresh["state"], "applying")
        self.assertEqual(fresh["records"], self.context["records"])
        self.assertTrue(self.db.transition_native_cache_job("cache-job", "applying", "failed"))
        failed = self.db.get_native_cache_job("cache-job")
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(self.db.complete_native_cache_job("cache-job", 3), failed)
        self.assertIsNone(self.db.get_native_cache_cursor("txt2img", "processed_cache"))

    def test_missing_jobs_and_cursor(self):
        self.assertIsNone(self.db.get_native_cache_job("missing"))
        self.assertFalse(self.db.transition_native_cache_job("missing", "pending", "active"))
        self.assertIsNone(self.db.complete_native_cache_job("missing", 3))
        self.assertIsNone(self.db.get_native_cache_cursor("txt2img", "processed_cache"))

    def test_cas_allows_only_one_claim_across_instances(self):
        self.db.save_native_cache_job(self.context)
        other = StudioDB(self.path)
        barrier = threading.Barrier(2)

        def claim(db):
            barrier.wait(timeout=5)
            return db.transition_native_cache_job("cache-job", "pending", "applying")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, [self.db, other]))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(other.get_native_cache_job("cache-job")["state"], "applying")

    def test_pending_and_applying_cannot_complete(self):
        self.db.save_native_cache_job(self.context)
        for state in ("pending", "applying"):
            with self.subTest(state=state):
                self.assertIsNone(self.db.complete_native_cache_job("cache-job", 3))
                self.assertEqual(self.db.get_native_cache_job("cache-job")["state"], state)
                self.assertIsNone(self.db.get_native_cache_cursor("txt2img", "processed_cache"))
            if state == "pending":
                self.assertTrue(self.db.transition_native_cache_job("cache-job", "pending", "applying"))

    def test_full_completion_commits_and_survives_reinitialize(self):
        self.activate()
        result = self.db.complete_native_cache_job("cache-job", 3)
        self.assertEqual(result, {**self.context, "state": "completed", "completed_count": 3,
                                  "committed": True, "next_cursor": 29})
        reopened = StudioDB(self.path)
        self.assertEqual(reopened.get_native_cache_job("cache-job"), result)
        self.assertEqual(reopened.get_native_cache_cursor("txt2img", "processed_cache"), 29)
        self.assertIsNone(reopened.get_native_cache_cursor("img2img", "processed_cache"))
        self.assertIsNone(reopened.get_native_cache_cursor("txt2img", "cache"))
        with reopened._connection() as conn:
            value = conn.execute("SELECT value FROM settings WHERE key=?", (self.cursor_key,)).fetchone()[0]
        self.assertEqual(json.loads(value), 29)

    def test_partial_completion_uses_last_consumed_prefix_id(self):
        self.activate()
        result = self.db.complete_native_cache_job("cache-job", 2)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["completed_count"], 2)
        self.assertTrue(result["committed"])
        self.assertEqual(result["next_cursor"], 13)
        self.assertEqual(result["records"], self.context["records"])
        self.assertEqual(self.db.get_native_cache_cursor("txt2img", "processed_cache"), 13)

    def test_wrapped_prefix_uses_record_order_not_maximum_id(self):
        self.context["records"] = [{"id": ident, "prompt": "cached"} for ident in (29, 7, 13)]
        self.context["after_id"] = 13
        self.activate()
        result = self.db.complete_native_cache_job("cache-job", 2)
        self.assertEqual(result["next_cursor"], 7)
        self.assertEqual(self.db.get_native_cache_cursor("txt2img", "processed_cache"), 7)

    def test_count_above_length_is_clamped(self):
        self.activate()
        result = self.db.complete_native_cache_job("cache-job", 100)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["completed_count"], 3)
        self.assertEqual(result["next_cursor"], 29)

    def test_zero_count_fails_without_creating_cursor(self):
        self.activate()
        result = self.db.complete_native_cache_job("cache-job", 0)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["completed_count"], 0)
        self.assertFalse(result["committed"])
        self.assertEqual(result["next_cursor"], 4)
        self.assertIsNone(self.db.get_native_cache_cursor("txt2img", "processed_cache"))

    def test_negative_count_is_zero_and_preserves_existing_cursor(self):
        self.db.set_setting(self.cursor_key, 2)
        self.activate()
        result = self.db.complete_native_cache_job("cache-job", -10)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["completed_count"], 0)
        self.assertFalse(result["committed"])
        self.assertEqual(result["next_cursor"], 4)
        self.assertEqual(self.db.get_native_cache_cursor("txt2img", "processed_cache"), 2)

    def test_empty_records_fail_even_with_positive_count(self):
        self.context["records"] = []
        self.activate()
        result = self.db.complete_native_cache_job("cache-job", 3)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["completed_count"], 0)
        self.assertEqual(result["next_cursor"], 4)
        self.assertFalse(result["committed"])

    def test_terminal_completion_is_idempotent_without_rewriting_cursor(self):
        for count in (0, 1, 3):
            with self.subTest(count=count):
                self.context["token"] = f"job-{count}"
                self.activate()
                original = self.db.complete_native_cache_job(self.context["token"], count)
                self.db.set_setting(self.cursor_key, 99)
                reopened = StudioDB(self.path)
                self.assertEqual(reopened.complete_native_cache_job(self.context["token"], 2), original)
                self.assertEqual(reopened.get_native_cache_job(self.context["token"]), original)
                self.assertEqual(reopened.get_native_cache_cursor("txt2img", "processed_cache"), 99)

    def test_concurrent_completions_return_same_terminal_result(self):
        self.activate()
        other = StudioDB(self.path)
        barrier = threading.Barrier(2)

        def complete(item):
            db, count = item
            barrier.wait(timeout=5)
            return db.complete_native_cache_job("cache-job", count)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(complete, [(self.db, 1), (other, 3)]))
        self.assertEqual(first, second)
        self.assertEqual(first, self.db.get_native_cache_job("cache-job"))
        self.assertEqual(self.db.get_native_cache_cursor("txt2img", "processed_cache"), first["next_cursor"])

    def test_cursor_reads_integer_and_string_settings(self):
        for value in (0, 13, "29"):
            with self.subTest(value=value):
                self.db.set_setting(self.cursor_key, value)
                result = self.db.get_native_cache_cursor("txt2img", "processed_cache")
                self.assertIs(type(result), int)
                self.assertEqual(result, int(value))

    def test_cursor_write_failure_rolls_back_job_and_cursor(self):
        self.activate()
        self.db.set_setting(self.cursor_key, 4)
        before = self.db.get_native_cache_job("cache-job")
        with self.db._connection() as conn:
            conn.execute("""CREATE TRIGGER fail_cursor BEFORE UPDATE ON settings
                            WHEN NEW.key LIKE 'native_cache_cursor:%'
                            BEGIN SELECT RAISE(ABORT, 'cursor write failed'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "cursor write failed"):
            self.db.complete_native_cache_job("cache-job", 2)
        self.assertEqual(self.db.get_native_cache_job("cache-job"), before)
        self.assertEqual(self.db.get_native_cache_cursor("txt2img", "processed_cache"), 4)
        with self.db._connection() as conn:
            conn.execute("DROP TRIGGER fail_cursor")
        self.assertEqual(self.db.complete_native_cache_job("cache-job", 2)["state"], "partial")

    def test_job_write_failure_does_not_commit_cursor(self):
        self.activate()
        self.db.set_setting(self.cursor_key, 4)
        before = self.db.get_native_cache_job("cache-job")
        with self.db._connection() as conn:
            conn.execute("""CREATE TRIGGER fail_job BEFORE UPDATE ON native_cache_jobs
                            BEGIN SELECT RAISE(ABORT, 'job write failed'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "job write failed"):
            self.db.complete_native_cache_job("cache-job", 2)
        self.assertEqual(self.db.get_native_cache_job("cache-job"), before)
        self.assertEqual(self.db.get_native_cache_cursor("txt2img", "processed_cache"), 4)


class EditPromptScoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = StudioDB(Path(temporary.name) / "scores.db")

    def scored_prompt(self, source="llm"):
        ident = self.db.save_prompt("one cat", negative="blurry", output_mode="Danbooru Tags",
                                    base_model="model", tags="cat", score=9, score_source=source,
                                    score_reason="original reason", score_model="judge")
        return self.db.get_prompt(ident)

    def test_changed_content_invalidates_llm_scoring(self):
        for field in ("prompt", "negative_prompt", "tags", "output_mode", "base_model"):
            with self.subTest(field=field):
                original = self.scored_prompt()
                self.assertEqual(self.db.edit_prompts([original], field, "append", "", " changed"), 1)
                record = self.db.get_prompt(original["id"])
                self.assertEqual(record[field], original[field] + " changed")
                self.assertEqual((record["score"], record["score_source"], record["score_reason"],
                                  record["score_model"]), (0, "unrated", "", ""))
                self.assertEqual(record["content_hash"], self.db._record_hash(record))

    def test_noop_preserves_llm_scoring(self):
        original = self.scored_prompt()
        self.assertEqual(self.db.edit_prompts([original], "prompt", "replace", "absent", "new"), 0)
        self.assertEqual(self.db.get_prompt(original["id"]), original)

    def test_manual_scoring_is_preserved(self):
        original = self.scored_prompt(source="manual")
        self.assertEqual(self.db.edit_prompts([original], "prompt", "append", "", " changed"), 1)
        record = self.db.get_prompt(original["id"])
        for field in ("score", "score_source", "score_reason", "score_model"):
            self.assertEqual(record[field], original[field])

    def test_stale_snapshot_rolls_back_content_and_score_changes(self):
        first, second = self.scored_prompt(), self.scored_prompt()
        self.db.edit_prompts([second], "prompt", "append", "", " changed elsewhere")
        with self.assertRaises(ValueError):
            self.db.edit_prompts([first, second], "prompt", "append", "", " changed")
        self.assertEqual(self.db.get_prompt(first["id"]), first)


if __name__ == "__main__":
    unittest.main()
