import types
import unittest
from unittest import mock

import sys

sys.modules.setdefault("gradio", types.ModuleType("gradio"))
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from scripts import prompt_studio_ui as ui


class _FakeCache:
    def __init__(self, records):
        self.records = records

    def list_prompts_after(self, _query, limit, after_id):
        return [record for record in self.records if record["id"] > after_id][:limit]


class NativeCacheBackendTests(unittest.TestCase):
    def setUp(self):
        with ui._NATIVE_CACHE_CONTEXT_LOCK:
            ui._NATIVE_CACHE_CONTEXTS.clear()
        self.raw = _FakeCache([
            {"id": 1, "prompt": "cache A"},
            {"id": 2, "prompt": "cache B"},
            {"id": 3, "prompt": "cache C"},
        ])
        self.processed = _FakeCache([
            {"id": 10, "prompt": "processed A"},
            {"id": 11, "prompt": "processed B"},
        ])
        self.db_patch = mock.patch.object(ui, "DB", self.raw)
        self.result_patch = mock.patch.object(ui, "RESULT_DB", self.processed)
        self.db_patch.start()
        self.result_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.result_patch.stop)
        self.addCleanup(lambda: ui._NATIVE_CACHE_CONTEXTS.clear())

    def test_prepare_and_apply_assign_one_cache_prompt_per_image(self):
        prepared = ui._native_cache_prepare({
            "slot": "txt2img",
            "source": "cache",
            "after_id": 0,
            "total_images": 3,
            "base_prompt": "fixed subject",
            "write_mode": "append_end",
        })
        self.assertEqual(prepared["next_cursor"], 3)
        processing = types.SimpleNamespace(
            prompt="fixed subject",
            negative_prompt="bad anatomy",
            batch_size=2,
            n_iter=2,
        )
        token = ui.apply_native_cache_context(processing)
        self.assertEqual(token, prepared["token"])
        self.assertEqual(processing.prompt, "fixed subject")
        self.assertEqual(processing.negative_prompt, "bad anatomy")
        self.assertEqual(processing.all_prompts, [
            "fixed subject, cache A",
            "fixed subject, cache B",
            "fixed subject, cache C",
        ])
        self.assertEqual(processing.all_negative_prompts, ["bad anatomy"] * 3)
        self.assertEqual(processing.n_iter, 2)

    def test_processed_cache_and_merge_modes_are_independent(self):
        prepared = ui._native_cache_prepare({
            "source": "processed_cache",
            "after_id": 10,
            "total_images": 1,
            "base_prompt": "fixed {{LLM}}",
            "write_mode": "marker",
        })
        processing = types.SimpleNamespace(prompt="fixed {{LLM}}", negative_prompt="", batch_size=1, n_iter=1)
        self.assertEqual(ui.apply_native_cache_context(processing), prepared["token"])
        self.assertEqual(processing.prompt, "fixed {{LLM}}")
        self.assertEqual(processing.all_prompts, ["fixed processed B"])

    def test_prepare_reads_more_than_one_page_without_a_storage_cap(self):
        self.raw.records = [{"id": index, "prompt": f"cache {index}"} for index in range(1, 1006)]
        prepared = ui._native_cache_prepare({
            "source": "cache", "after_id": 0, "total_images": 1005,
            "base_prompt": "fixed",
        })
        self.assertEqual(prepared["count"], 1005)
        self.assertEqual(prepared["next_cursor"], 1005)
        self.assertEqual(len(prepared["records"]), 1005)

    def test_insufficient_cache_does_not_create_context(self):
        with self.assertRaisesRegex(ValueError, "需要 4 条"):
            ui._native_cache_prepare({
                "source": "cache", "after_id": 0, "total_images": 4,
                "base_prompt": "fixed",
            })
        with ui._NATIVE_CACHE_CONTEXT_LOCK:
            self.assertEqual(ui._NATIVE_CACHE_CONTEXTS, {})

    def test_release_only_advances_after_successful_generation(self):
        prepared = ui._native_cache_prepare({
            "source": "cache", "after_id": 0, "total_images": 1,
            "base_prompt": "fixed",
        })
        pending = ui._native_cache_release(prepared["token"], True)
        self.assertEqual(pending["committed"], False)
        ui._native_cache_release(prepared["token"], False)
        prepared = ui._native_cache_prepare({
            "source": "cache", "after_id": 0, "total_images": 1,
            "base_prompt": "fixed",
        })
        processing = types.SimpleNamespace(prompt="fixed", negative_prompt="", batch_size=1, n_iter=1)
        ui.apply_native_cache_context(processing)
        committed = ui._native_cache_release(prepared["token"], True)
        self.assertEqual(committed["committed"], True)


if __name__ == "__main__":
    unittest.main()
