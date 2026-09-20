import shlex
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.modules.setdefault("gradio", types.ModuleType("gradio"))
import prompt_studio_core as core
import prompt_studio_ui as ui


class WD14WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.db = core.StudioDB(self.folder / "cache.db")
        for patch in (mock.patch.object(ui, "DB", self.db),
                      mock.patch.object(ui, "_WD14_BATCH_JOBS", {}),
                      mock.patch.object(ui.gr, "update", lambda **kw: kw, create=True)):
            patch.start()
            self.addCleanup(patch.stop)
        for name in ("a.PNG", "b.jpg", "c.webp"):
            Image.new("RGB", (4, 4), "blue").save(self.folder / name)

    def args(self):
        return [str(self.folder), True, "model.onnx", 0.35, 0.85, "session-a"]

    def test_stop_resume_keeps_completed_results_and_session_isolation(self):
        with mock.patch.object(ui, "interrogate_local", return_value=("blue sky", "ok")) as infer:
            run = ui._wd14_run_batch(*self.args())
            next(run)
            next(run)
            self.assertEqual(infer.call_count, 1)
            ui._wd14_cancel_batch("other-session")
            self.assertFalse(ui._WD14_BATCH_JOBS["session-a"].cancel.is_set())
            ui._wd14_cancel_batch("session-a")
            stopped = list(run)[-1]
            self.assertIn("未处理 2", stopped[0])
            resumed = list(ui._wd14_run_batch(*self.args(), mode="resume"))[-1]
        self.assertEqual(infer.call_count, 3)
        self.assertIn("已完成 3", resumed[0])
        self.assertIn("未处理 0", resumed[0])

    def test_changed_model_rejects_resume_and_preserves_prior_results(self):
        with mock.patch.object(ui, "interrogate_local", return_value=("blue sky", "ok")):
            list(ui._wd14_run_batch(*self.args()))
        args = self.args()
        args[2] = "different.onnx"
        result = list(ui._wd14_run_batch(*args, mode="resume"))[-1]
        self.assertIn("参数已变化", result[0])
        self.assertEqual(len(ui._WD14_BATCH_JOBS["session-a"].records), 3)

    def test_cache_routes_only_completed_results_and_keeps_source_metadata(self):
        records = [{"path": "a.png", "prompt": "blue hair", "status": "completed"},
                   {"path": "bad.png", "prompt": "", "status": "failed"}]
        with mock.patch.object(ui, "_enqueue_server_queue") as enqueue:
            ui._wd14_save_records(records, "SDXL")
            ui._wd14_save_records(records, "SDXL")
        rows = self.db.list_prompts()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["source_kind"], rows[0]["source_ref"]), ("wd14", "a.png"))
        self.assertEqual(rows[0]["score_source"], "unrated")
        enqueue.assert_not_called()

    def test_single_llm_result_retains_its_output_format_in_cache(self):
        ui._wd14_save_single("a quiet blue garden", "SDXL", "Natural Language")
        self.assertEqual(self.db.list_prompts()[0]["output_mode"], "Natural Language")

    def test_native_handoff_quotes_prompt_content_without_starting_generation(self):
        prompt = r"blue hair, character \(series\), artist's style --steps 1"
        with mock.patch.object(ui, "_enqueue_server_queue") as enqueue, \
             mock.patch.object(ui, "_server_render_prompt") as render:
            lines, script, message = ui._wd14_write_batch([
                {"status": "completed", "prompt": prompt}, {"status": "failed", "prompt": "ignored"}])
        self.assertEqual(shlex.split(lines), ["--prompt", prompt])
        self.assertEqual(script["value"], "Prompts from File or Textbox")
        self.assertIn("1 条", message)
        enqueue.assert_not_called()
        render.assert_not_called()

    def test_failed_interrogation_does_not_call_llm(self):
        with mock.patch.object(ui, "interrogate_local", return_value=("", "模型失败")), \
             mock.patch.object(ui, "_expand_or_polish") as llm:
            self.assertEqual(ui._wd14_process(None, "model.onnx", .35, .85), ("", "", "模型失败"))
        llm.assert_not_called()

    def test_single_handoff_exits_previous_batch_without_changing_other_scripts(self):
        prompt, status, script = ui._wd14_write_single("blue sky", "fixed", "append", "Prompts from File or Textbox")
        self.assertEqual(prompt, "fixed, blue sky")
        self.assertEqual(script, {"value": "None"})
        self.assertIn("txt2img", status)
        self.assertEqual(ui._wd14_write_single("blue sky", "fixed", "replace", "Other")[2], {})
        self.assertEqual(ui._wd14_write_single("", "fixed", "replace", "Prompts from File or Textbox")[2], {})

    def test_streamed_result_view_escapes_filenames_prompts_and_errors(self):
        job = ui.ImageBatchJob(self.folder, "model.onnx")
        job.records[0].update(path='<img src=x onerror="bad()">.png', prompt="<script>bad()</script>",
                              status="failed", error="<failed>")
        status, view, records = ui._wd14_batch_view(job.snapshot())
        self.assertIn("失败 1", status)
        self.assertNotIn("<script>", view)
        self.assertNotIn("<img", view)
        self.assertIn("&lt;failed&gt;", view)
        self.assertEqual(records[0]["prompt"], "<script>bad()</script>")

    def test_model_detection_preserves_selection_and_expands_download_when_missing(self):
        choices = [("vit", "vit.onnx"), ("convnext", "convnext.onnx")]
        with mock.patch.object(ui, "discover_local_models", return_value=(choices, "vit.onnx")):
            model, status, panel = ui._wd14_model_choices("custom", "convnext.onnx")
        self.assertEqual(model["value"], "convnext.onnx")
        self.assertIn("2", status)
        self.assertFalse(panel["open"])
        with mock.patch.object(ui, "discover_local_models", return_value=([], None)):
            model, status, panel = ui._wd14_model_choices("custom", "missing.onnx")
        self.assertIsNone(model["value"])
        self.assertTrue(panel["open"])

    def test_download_requires_an_explicit_model_choice(self):
        with mock.patch.object(ui, "download_model") as download:
            result = list(ui._wd14_download_model(None))[-1]
        download.assert_not_called()
        self.assertIn("选择", result[1])
        self.assertFalse(result[3]["interactive"])

    def test_download_details_match_selected_cl_model(self):
        links, directory, button = ui._wd14_download_details("cl_tagger_1_01")
        self.assertIn("tag_mapping.json", links)
        self.assertNotIn("selected_tags.csv", links)
        self.assertIn("cl_tagger_1_01", directory)
        self.assertTrue(button["interactive"])

    def test_download_selects_result_and_reports_retryable_failure(self):
        with mock.patch.object(ui, "discover_local_models", return_value=([("vit", "vit.onnx")], "vit.onnx")), \
             mock.patch.object(ui, "download_model", return_value=iter([("下载中", None), ("完成", "vit.onnx")])) as download:
            steps = list(ui._wd14_download_model("wd-vit-tagger-v3"))
        download.assert_called_once_with("wd-vit-tagger-v3", "")
        self.assertFalse(steps[0][3]["interactive"])
        self.assertEqual(steps[-1][0]["value"], "vit.onnx")
        self.assertFalse(steps[-1][2]["open"])
        self.assertTrue(steps[-1][3]["interactive"])
        with mock.patch.object(ui, "discover_local_models", return_value=([], None)), \
             mock.patch.object(ui, "download_model", side_effect=RuntimeError("offline")):
            result = list(ui._wd14_download_model("cl_tagger_1_01"))[-1]
        self.assertIn("下载失败", result[1])
        self.assertTrue(result[2]["open"])
        self.assertTrue(result[3]["interactive"])


if __name__ == "__main__":
    unittest.main()
