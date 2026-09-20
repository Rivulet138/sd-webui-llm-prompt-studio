import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.modules.setdefault("gradio", types.ModuleType("gradio"))
import prompt_studio_core as core
import prompt_studio_ui as ui


class UnifiedGenerationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = core.StudioDB(Path(folder.name) / "cache.db")
        for patch in (mock.patch.object(ui, "DB", self.db),
                      mock.patch.object(ui.gr, "update", lambda **kw: kw, create=True),
                      mock.patch.object(ui, "_ensure_server_queue_worker")):
            patch.start()
            self.addCleanup(patch.stop)

    def args(self, count=3):
        return ["", False, True, "Danbooru Tags", "", "SDXL", "SFW", "", "",
                "OpenAI Compatible", "http://localhost:1234/v1", "test", "",
                0.2, 30, 1000, True, False, "", False, False, 0, "Plain Prompt", 1,
                "", 0, "全部", "全部", [], "task-test", count, "", "fixed subject", True, False]

    def test_cache_destination_never_enqueues_or_renders_and_uses_shared_request(self):
        with mock.patch.object(ui, "_generate", side_effect=[(f"scene {i}", "", "ok") for i in range(3)]) as generate, \
             mock.patch.object(ui, "_enqueue_server_queue") as enqueue, \
             mock.patch.object(ui, "_server_render_prompt") as render:
            output = list(ui._studio_generate("garden\nsoft light", "cache", "{}", "task-test", *self.args()))
        self.assertEqual(len(self.db.list_prompts()), 3)
        self.assertEqual(generate.call_count, 3)
        self.assertTrue(all("garden\nsoft light" in c.args[0] for c in generate.call_args_list))
        self.assertTrue(all(c.kwargs["preserve_inference_settings"] for c in generate.call_args_list))
        enqueue.assert_not_called()
        render.assert_not_called()
        self.assertEqual(output[-1][7], "scene 2")

    def test_queue_destination_automatically_enqueues_generated_results_once(self):
        settings = {"width": 768, "steps": 28, "negative_prompt": "blur"}
        with mock.patch.object(ui, "_generate", side_effect=[(f"scene {i}", "", "ok") for i in range(3)]):
            output = list(ui._studio_generate("garden", "queue", settings, "task-test", *self.args()))
        batch_id = output[-1][8]
        jobs = self.db.list_server_queue(batch_id)
        self.assertEqual(len(jobs), 3)
        self.assertEqual([j["request"] for j in jobs], ["scene 0", "scene 1", "scene 2"])
        self.assertTrue(all(j["config"]["direct_prompt"] and j["target"] == "txt2img" for j in jobs))
        self.assertTrue(all(j["config"]["generation_settings"] == settings for j in jobs))
        self.assertEqual(len(self.db.list_prompts()), 3)

    def test_failed_items_remain_retryable_and_successes_are_kept(self):
        with mock.patch.object(ui, "_generate", side_effect=[("scene", "", "ok"), ("", "", "provider failed")]):
            output = list(ui._studio_generate("garden", "cache", "{}", "task-test", *self.args(2)))
        self.assertEqual(len(self.db.list_prompts()), 1)
        self.assertEqual(output[-1][5][0]["status"], "生成错误")

    def test_cancelled_batch_does_not_start_image_generation(self):
        def cancel(*args, **kwargs):
            ui._BATCH_CANCEL.set()
            return "", "", "已取消"
        with mock.patch.object(ui, "_generate", side_effect=cancel), \
             mock.patch.object(ui, "_enqueue_server_queue") as enqueue:
            output = list(ui._studio_generate("garden", "queue", "{}", "task-test", *self.args()))
        enqueue.assert_not_called()
        self.assertIn("取消", output[-1][0])

    def test_stop_between_generation_completion_and_queue_handoff_is_respected(self):
        with mock.patch.object(ui, "_generate", return_value=("scene", "", "ok")), \
             mock.patch.object(ui, "_enqueue_server_queue") as enqueue:
            stream = ui._studio_generate("garden", "queue", "{}", "task-test", *self.args(1))
            next(stream)
            self.assertIn("完成", next(stream)[0])
            self.assertIn("取消", ui._cancel_batch_generation("task-test"))
            list(stream)
        enqueue.assert_not_called()

    def test_retry_keeps_a_multiline_request_as_one_task(self):
        with mock.patch.object(ui, "_generate", return_value=("", "", "provider failed")):
            failed = list(ui._studio_generate("garden\nsoft light", "cache", "{}", "task-test", *self.args(1)))[-1]
        issue = failed[5][0]
        base = self.args(1)
        retry_args = [[ui._batch_issue_key(issue)], failed[5], True, *base[3:28], "task-test"]
        with mock.patch.object(ui, "_generate", return_value=("scene", "", "ok")) as generate:
            list(ui._studio_retry("cache", "{}", "task-test", *retry_args))
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[0], issue["source"])

    def test_cancellation_during_queue_insert_marks_new_jobs_cancelled(self):
        import threading
        event = threading.Event()
        original = self.db.enqueue_server_queue
        def enqueue(*args):
            count = original(*args)
            event.set()
            return count
        with mock.patch.object(self.db, "enqueue_server_queue", side_effect=enqueue), \
             mock.patch.object(ui, "_ensure_server_queue_worker") as worker:
            snapshot = ui._enqueue_server_queue({"requests": ["scene"], "target": "txt2img", "config": {"direct_prompt": True}}, cancel_event=event)
        worker.assert_not_called()
        self.assertEqual(snapshot["counts"], {"cancelled": 1})

    def test_inline_cache_destination_overrides_disabled_optional_cache_setting(self):
        ui._save_workflow_values({"cache_result": False})
        with mock.patch.object(ui, "_inline_generate", return_value=("scene", "", "ok")) as generate:
            result = ui._api_inline_generate({"request": "garden", "slot": "txt2img", "cache_result": True})
        self.assertTrue(generate.call_args.args[-2])
        self.assertEqual(result["prompt"], "scene")
        with self.assertRaisesRegex(ValueError, "boolean"):
            ui._api_inline_generate({"slot": "txt2img", "cache_result": "false"})

    def test_zero_count_keeps_caching_until_stopped(self):
        with mock.patch.object(ui, "_generate", side_effect=lambda *a, **k: (f"scene {len(self.db.list_prompts())}", "", "ok")) as generate, \
             mock.patch.object(ui, "_enqueue_server_queue") as enqueue:
            stream = ui._studio_generate("garden", "cache", "{}", "task-test", *self.args(0))
            self.addCleanup(stream.close)
            for _ in range(3):
                item = next(stream)
                self.assertIn("无限", item[0])
            ui._cancel_batch_generation("task-test")
            final = list(stream)[-1]
        self.assertEqual(generate.call_count, 3)
        self.assertEqual(len(self.db.list_prompts()), 3)
        self.assertIn("取消", final[0])
        enqueue.assert_not_called()
        self.assertFalse(ui._BATCH_LOCK.locked())

    def test_zero_count_repeats_explicit_requests_with_bounded_history(self):
        args = self.args(0)
        args[0] = "forest\nbeach"
        lengths = []
        def generate(*args, **kwargs):
            lengths.append(len(ui._BATCH_CONTEXT.history))
            return f"scene {len(lengths)}", "", "ok"
        with mock.patch.object(ui, "_generate", side_effect=generate) as generated:
            stream = ui._studio_generate("unused", "cache", "{}", "task-test", *args)
            self.addCleanup(stream.close)
            for _ in range(205):
                item = next(stream)
                self.assertLessEqual(len(item[6]["value"]), 200)
            ui._cancel_batch_generation("task-test")
            list(stream)
        self.assertTrue(generated.call_args_list[0].args[0].endswith("forest"))
        self.assertTrue(generated.call_args_list[1].args[0].endswith("beach"))
        self.assertTrue(generated.call_args_list[2].args[0].endswith("forest"))
        self.assertLessEqual(max(lengths), 200)
        self.assertEqual(item[6]["value"][-1][0], 205)

    def test_zero_queue_keeps_submitting_once_per_index_after_preview_rolls_over(self):
        snapshot = {"batch_id": "done", "counts": {"completed": 1}, "status": "done", "jobs": []}
        with mock.patch.object(ui, "_generate", return_value=("same valid prompt", "", "ok")), \
             mock.patch.object(ui, "_filtered_cache_updates") as refresh, \
             mock.patch.object(ui, "_enqueue_server_queue", return_value=snapshot) as enqueue:
            stream = ui._studio_generate("garden", "queue", "{}", "task-test", *self.args(0))
            self.addCleanup(stream.close)
            for _ in range(205):
                next(stream)
            ui._cancel_batch_generation("task-test")
            list(stream)
        self.assertEqual(enqueue.call_count, 205)
        self.assertTrue(all(call.args[0]["requests"] == ["same valid prompt"] for call in enqueue.call_args_list))
        refresh.assert_not_called()

    def test_zero_queue_failure_cancellation_and_missing_job_stop_next_llm_request(self):
        for counts in ({"error": 1}, {"cancelled": 1}, {}):
            with self.subTest(counts=counts), \
                 mock.patch.object(ui, "_generate", return_value=("scene", "", "ok")) as generate, \
                 mock.patch.object(ui, "_enqueue_server_queue", return_value={
                     "batch_id": "failed", "counts": counts, "status": "not completed", "jobs": [],
                 }) as enqueue:
                result = list(ui._studio_generate("garden", "queue", "{}", "task-test", *self.args(0)))
            self.assertEqual(generate.call_count, 1)
            self.assertEqual(enqueue.call_count, 1)
            self.assertIn("暂停", result[-1][0])
            self.assertEqual(result[-1][8], "failed")
            self.assertFalse(ui._BATCH_LOCK.locked())

    def test_zero_repeated_llm_failures_stop_without_an_endless_retry_loop(self):
        with mock.patch.object(ui, "_generate", return_value=("", "", "provider failed")) as generate:
            result = list(ui._studio_generate("garden", "cache", "{}", "task-test", *self.args(0)))
        self.assertEqual(generate.call_count, 3)
        self.assertIn("错误停止", result[-1][0])
        self.assertFalse(ui._BATCH_LOCK.locked())

    def test_zero_queue_submits_before_generation_finishes_and_stop_blocks_next_round(self):
        snapshot = {"batch_id": "infinite-queue", "counts": {"pending": 1}, "status": "pending", "jobs": []}
        with mock.patch.object(ui, "_generate", return_value=("scene", "", "ok")) as generate, \
             mock.patch.object(ui, "_enqueue_server_queue", return_value=snapshot) as enqueue:
            stream = ui._studio_generate("garden", "queue", {"width": 768}, "task-test", *self.args(0))
            self.addCleanup(stream.close)
            item = next(stream)
            self.assertEqual(item[8], "infinite-queue")
            self.assertEqual(enqueue.call_args.args[0]["requests"], ["scene"])
            self.assertEqual(enqueue.call_args.args[0]["config"]["generation_settings"], {"width": 768})
            ui._cancel_batch_generation("task-test")
            list(stream)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(enqueue.call_count, 1)

    def test_zero_queue_error_keeps_cached_prompt_and_stops_producing(self):
        with mock.patch.object(ui, "_generate", return_value=("scene", "", "ok")) as generate, \
             mock.patch.object(ui, "_enqueue_server_queue", side_effect=ValueError("queue unavailable")):
            output = list(ui._studio_generate("garden", "queue", "{}", "task-test", *self.args(0)))
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(len(self.db.list_prompts()), 1)
        self.assertIn("入队失败", output[-1][9])
        self.assertFalse(ui._BATCH_LOCK.locked())

    def test_zero_count_all_cached_sources_stop_without_empty_loop(self):
        args = self.args(0)
        args[1] = True
        with mock.patch.object(self.db, "has_source_prompt", return_value=True), \
             mock.patch.object(ui, "_generate") as generate:
            output = list(ui._studio_generate("garden", "cache", "{}", "task-test", *args))
        generate.assert_not_called()
        self.assertIn("全部", output[-1][0])

    def test_zero_preview_is_bounded_and_invalid_counts_are_finite(self):
        rows, message = ui._preview_batch_sources("", False, "Danbooru Tags", "SDXL", 0)
        self.assertIn("无限", message)
        self.assertGreater(len(rows["value"]), 0)
        self.assertLessEqual(len(rows["value"]), 200)
        self.assertEqual(ui._normalize_generation_count(0), 0)
        self.assertEqual(ui._normalize_generation_count("0"), 0)
        for value in (None, "", -1, 0.5, float("nan"), float("inf"), False):
            self.assertGreater(ui._normalize_generation_count(value), 0)


if __name__ == "__main__":
    unittest.main()
