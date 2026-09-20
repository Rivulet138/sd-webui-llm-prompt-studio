import json
import sys
import tempfile
import types
import urllib.request
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.modules.setdefault("gradio", types.ModuleType("gradio"))
import prompt_studio_core as core
import prompt_studio_ui as ui


class ExposedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = core.StudioDB(Path(self.temp.name) / "studio.db")
        patch = mock.patch.object(ui, "DB", self.db)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(ui.gr, "update", lambda **values: values, create=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_fallback_setting_round_trips_and_blank_disables_it(self):
        credentials = core.CredentialStore(Path(self.temp.name) / "credentials.json")
        with mock.patch.object(ui, "CREDENTIALS", credentials):
            for fallback in ("backup-model", ""):
                result = ui._save_llm_settings(
                    "OpenAI Compatible", "http://127.0.0.1:1234/v1", "primary", "", fallback,
                    "", "", 1, 30, 512, True, 0)
                self.assertIn("设置已保存", result[0])
                self.assertEqual(ui._connection_settings()["fallback_model"], fallback)

    def test_failed_primary_calls_backup_but_success_does_not(self):
        response = {"choices": [{"message": {"content": "a quiet garden"}}]}
        with mock.patch.object(core, "_request_json", side_effect=[RuntimeError("primary unavailable"), response]) as request:
            result = core.call_llm("OpenAI Compatible", "http://localhost:1234/v1", "primary", "", "system", "garden",
                                   fallback_model="backup", max_retries=0)
        self.assertEqual(result, "a quiet garden")
        self.assertEqual([call.args[1]["model"] for call in request.call_args_list], ["primary", "backup"])
        with mock.patch.object(core, "_request_json", return_value=response) as request:
            core.call_llm("OpenAI Compatible", "http://localhost:1234/v1", "primary", "", "system", "garden",
                          fallback_model="backup", max_retries=0)
        self.assertEqual(request.call_count, 1)

    def test_failed_primary_can_use_an_independent_fallback_service(self):
        response = {"message": {"content": "backup ready"}}
        with mock.patch.object(core, "_request_json", side_effect=[RuntimeError("primary unavailable"), response]) as request:
            result = core.call_llm(
                "OpenAI Compatible", "http://localhost:1234/v1", "primary", "primary-key",
                "system", "garden", max_retries=0,
                fallback_provider="Ollama", fallback_endpoint="http://localhost:11434",
                fallback_model="qwen-backup", fallback_api_key="",
            )
        self.assertEqual(result, "backup ready")
        primary_request, fallback_request = request.call_args_list
        self.assertEqual(primary_request.args[1]["model"], "primary")
        self.assertEqual(fallback_request.args[0], "http://localhost:11434/api/chat")
        self.assertEqual(fallback_request.args[1]["model"], "qwen-backup")
        self.assertNotIn("Authorization", fallback_request.kwargs["headers"])

    def test_independent_fallback_connection_round_trips_with_separate_credentials(self):
        credentials = core.CredentialStore(Path(self.temp.name) / "credentials.json")
        with mock.patch.object(ui, "CREDENTIALS", credentials):
            result = ui._save_llm_settings(
                "OpenAI Compatible", "http://127.0.0.1:1234/v1", "primary", "primary-key", "backup-model",
                "", "", 0.7, 30, 512, True, 1,
                fallback_provider="Ollama", fallback_endpoint="http://127.0.0.1:11434",
                fallback_api_key="backup-key",
            )
        self.assertIn("Ollama", result[0])
        saved = ui._connection_settings("OpenAI Compatible")
        self.assertEqual(saved["fallback_provider"], "Ollama")
        self.assertEqual(saved["fallback_endpoint"], "http://127.0.0.1:11434")
        self.assertEqual(saved["fallback_model"], "backup-model")
        self.assertEqual(credentials.resolve("", "OpenAI Compatible", "http://127.0.0.1:1234/v1"), "primary-key")
        self.assertEqual(credentials.resolve("", "Ollama", "http://127.0.0.1:11434"), "backup-key")

    def test_cancelled_request_never_calls_backup(self):
        with mock.patch.object(core, "_request_json", side_effect=core.LLMRequestError("LLM request cancelled")) as request:
            with self.assertRaises(core.LLMRequestError):
                core.call_llm("OpenAI Compatible", "http://localhost:1234/v1", "primary", "", "system", "garden",
                              fallback_model="backup", max_retries=0)
        self.assertEqual(request.call_count, 1)

    def test_queue_ui_preserves_requested_target_without_starting_worker(self):
        with mock.patch.object(ui, "_ensure_server_queue_worker"), mock.patch.object(ui._SERVER_QUEUE_WAKE, "set"):
            for target in ("none", "txt2img"):
                batch_id, status, _ = ui._server_queue_start_ui("garden\nbeach", target)
                jobs = self.db.list_server_queue(batch_id)
                self.assertEqual(len(jobs), 2, status)
                self.assertEqual({job["target"] for job in jobs}, {target})

    def _internal_render_modules(self):
        class FakeProcessing:
            calls = []

            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.extra_generation_params = {}
                self.script_args = []
                self.force_task_id = None
                self.close = mock.Mock()

        class FakeLock:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeProcessed:
            class Image:
                def save(self, buffer, format):
                    self.format = format
                    buffer.write(b"png")

            images = [Image()]

            def js(self):
                return "{}"

        def process_images(processing):
            FakeProcessing.calls.append(processing)
            return FakeProcessed()

        processing_module = types.ModuleType("modules.processing")
        processing_module.StableDiffusionProcessingTxt2Img = FakeProcessing
        processing_module.process_images = process_images
        scripts_module = types.ModuleType("modules.scripts")
        scripts_module.scripts_txt2img = types.SimpleNamespace(scripts=[])
        shared_module = types.ModuleType("modules.shared")
        shared_module.sd_model = object()
        shared_module.opts = types.SimpleNamespace(outdir_txt2img_samples="samples", outdir_txt2img_grids="grids")
        shared_module.state = types.SimpleNamespace(begin=mock.Mock(), end=mock.Mock())
        shared_module.total_tqdm = types.SimpleNamespace(clear=mock.Mock())
        call_queue_module = types.ModuleType("modules.call_queue")
        call_queue_module.queue_lock = FakeLock()
        progress_module = types.ModuleType("modules.progress")
        progress_module.add_task_to_queue = mock.Mock()
        progress_module.start_task = mock.Mock()
        progress_module.finish_task = mock.Mock()
        modules = types.ModuleType("modules")
        modules.processing = processing_module
        modules.scripts = scripts_module
        modules.shared = shared_module
        modules.call_queue = call_queue_module
        modules.progress = progress_module
        return {
            "modules": modules,
            "modules.processing": processing_module,
            "modules.scripts": scripts_module,
            "modules.shared": shared_module,
            "modules.call_queue": call_queue_module,
            "modules.progress": progress_module,
        }, FakeProcessing

    def test_server_render_uses_forge_process_without_http_api(self):
        modules, processing = self._internal_render_modules()
        with mock.patch.dict(sys.modules, modules), mock.patch.object(urllib.request, "urlopen") as request:
            self.assertEqual(ui._server_render_prompt("garden", "none", "7"), {})
            result = ui._server_render_prompt("garden", "txt2img", "7")
        request.assert_not_called()
        self.assertEqual(result["images"], ["cG5n"])
        self.assertEqual(processing.calls[0].prompt, "garden")
        self.assertEqual(processing.calls[0].force_task_id, "server-queue-7")

    def test_server_render_uses_captured_txt2img_settings(self):
        modules, processing = self._internal_render_modules()
        with mock.patch.dict(sys.modules, modules):
            result = ui._server_render_prompt(
                "garden", "txt2img", "8", {"generation_settings": {
                    "negative_prompt": "bad anatomy", "steps": 8, "width": 832, "height": 1216,
                    "cfg_scale": 5.5, "sampler_name": "Euler a", "seed": 123,
                    "batch_size": 2, "batch_count": 3,
                }},
            )
        p = processing.calls[0]
        self.assertEqual(p.negative_prompt, "bad anatomy")
        self.assertEqual((p.steps, p.width, p.height), (8, 832, 1216))
        self.assertEqual((p.cfg_scale, p.sampler_name, p.seed), (5.5, "Euler a", 123))
        self.assertEqual((p.batch_size, p.n_iter), (2, 3))
        self.assertEqual(result["images"], ["cG5n"])

    def test_ranbooru_batch_is_previewable_and_preserves_source_metadata(self):
        loaded = {"records": [{"prompt": "garden", "_ranbooru_id": "42", "_ranbooru_variant": "natural",
                               "_ranbooru_score": 8, "_ranbooru_rating": "g"}]}
        with mock.patch.object(ui, "_ranbooru_load", return_value=loaded):
            payload, status = ui._load_ranbooru_to_png_batch(*([""] * 9))
        self.assertIn("已载入 Ranbooru", status)
        data = json.loads(payload)
        self.assertEqual(data["records"][0]["record_id"], "ranbooru-42-natural")
        rows, _, _, message, _ = ui._png_batch_refresh(payload)
        self.assertEqual(len(rows), 1)
        self.assertIn("1 条", message)

    def test_full_export_includes_records_beyond_current_page(self):
        self.db.save_prompts_batch([{"prompt": f"garden {i}"} for i in range(55)])
        path = self.db.export_records("json", directory=Path(self.temp.name))
        exported = json.loads(Path(path).read_text(encoding="utf-8"))
        records = exported if isinstance(exported, list) else exported["records"]
        self.assertEqual(len(records), 55)


if __name__ == "__main__":
    unittest.main()
