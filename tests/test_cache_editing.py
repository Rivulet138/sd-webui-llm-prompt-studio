import sys
import tempfile
import types
from unittest import mock
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prompt_studio_core import StudioDB


class CacheEditingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = StudioDB(Path(self.temp.name) / "cache.db")

    def test_unlimited_filter_and_pagination(self):
        self.db.save_prompts_batch([{"prompt": f"cat {i}"} for i in range(1005)])
        self.assertEqual(len(self.db.list_prompts("cat", limit=None)), 1005)
        page = self.db.list_prompts("cat", limit=50, offset=1000)
        self.assertEqual(len(page), 5)
        self.assertEqual(page[0]["visible_position"], 1001)

    def test_edits_preserve_metadata_and_rehash(self):
        record_id = self.db.save_prompt("cat", source_kind="ranbooru", source_ref="12", score=5)
        old = self.db.get_prompt(record_id)
        self.assertEqual(self.db.edit_prompts([old], "prompt", "replace", "cat", "dog"), 1)
        new = self.db.get_prompt(record_id)
        self.assertEqual(new["prompt"], "dog")
        self.assertEqual(new["source_ref"], "12")
        self.assertEqual(new["score"], 5)
        self.assertNotEqual(new["content_hash"], old["content_hash"])

    def test_stale_preview_rolls_back_entire_batch(self):
        a = self.db.save_prompt("cat a")
        b = self.db.save_prompt("cat b")
        snapshot = self.db.list_prompts()
        self.db.save_prompt("changed", record_id=b)
        with self.assertRaises(ValueError):
            self.db.edit_prompts(snapshot, "prompt", "append", "", " suffix")
        self.assertEqual(self.db.get_prompt(a)["prompt"], "cat a")

    def test_empty_prompt_is_rejected_atomically(self):
        record_id = self.db.save_prompt("cat")
        with self.assertRaises(ValueError):
            self.db.edit_prompts(self.db.list_prompts(), "prompt", "replace", "cat", "")
        self.assertEqual(self.db.get_prompt(record_id)["prompt"], "cat")


class CacheUiTests(CacheEditingTests):
    def setUp(self):
        super().setUp()
        sys.modules.setdefault("gradio", types.ModuleType("gradio"))
        import prompt_studio_ui
        self.ui = prompt_studio_ui
        patch = mock.patch.object(self.ui, "DB", self.db)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(self.ui.gr, "update", lambda **kwargs: kwargs, create=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_all_filtered_scope_edits_beyond_page_and_thousand(self):
        self.db.save_prompts_batch([{"prompt": f"cat {i}"} for i in range(1005)])
        untouched = self.db.save_prompt("dog")
        args = ["全部筛选结果", "", "正向提示词", "查找替换", "cat", "kitten", "cat", 0, "全部", "全部"]
        message, snapshot = self.ui._preview_cache_edit(*args)
        self.assertIn("1005", message)
        result = self.ui._apply_cache_edit(snapshot, *args)
        self.assertIn("1005", result[0])
        self.assertEqual(len(self.db.list_prompts("kitten", limit=None)), 1005)
        self.assertEqual(self.db.get_prompt(untouched)["prompt"], "dog")

    def test_page_summary_keeps_id_and_full_record_load(self):
        full = "long\n" * 80
        record_id = self.db.save_prompt(full)
        rows = self.ui._as_rows(self.db.list_prompts())
        self.assertEqual(self.ui._table_row_id(rows, 0), str(record_id))
        self.assertLessEqual(len(rows[0][4]), 600)
        self.assertEqual(len(rows[0]), 6)
        self.assertEqual(rows[0][5], "")
        self.assertNotIn("\n", rows[0][4])
        self.assertEqual(self.ui._load_record(record_id)[1], full)

    def test_page_two_opens_correct_database_record(self):
        self.db.save_prompts_batch([{"prompt": f"cat {i}"} for i in range(55)])
        table, choices, message = self.ui._refresh_cache(page=2)
        self.assertEqual(len(table["value"]), 5)
        self.assertIn("2/2", message)
        record_id = self.ui._table_row_id(table["value"], 0)
        self.assertEqual(self.ui._load_record(record_id)[1], "cat 50")
        self.assertEqual(len(choices["choices"]), 5)

    def test_single_save_preserves_source_and_score(self):
        record_id = self.db.save_prompt("cat", score=7, score_source="llm", score_model="judge", source_kind="ranbooru", source_ref="12")
        self.ui._save_record(record_id, "dog", "", "Danbooru Tags", "Auto / checkpoint default", 0, "")
        saved = self.db.get_prompt(record_id)
        self.assertEqual((saved["source_kind"], saved["source_ref"], saved["score"], saved["score_model"]), ("ranbooru", "12", 7, "judge"))

    def test_changed_operation_requires_new_preview(self):
        self.db.save_prompt("cat")
        args = ["全部筛选结果", "", "正向提示词", "查找替换", "cat", "dog", "", 0, "全部", "全部"]
        _, snapshot = self.ui._preview_cache_edit(*args)
        args[5] = "bird"
        self.assertIn("请先预览", self.ui._apply_cache_edit(snapshot, *args)[0])
        self.assertEqual(self.db.list_prompts()[0]["prompt"], "cat")
