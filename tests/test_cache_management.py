"""Cache callbacks tested without loading Forge or touching the user database."""
import ast
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from test_queue_regressions import ROOT, StudioDB, ui_functions
from prompt_studio_core import normalize_sampling
from prompt_studio_generation import run_llm_generation

GR = SimpleNamespace(update=lambda **kwargs: kwargs)


class ProcessedResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = StudioDB(Path(self.tmp.name) / "results.db")
        self.ids = [self.db.save_prompt(p) for p in ("cat red", "cat blue", "dog green")]
        self.api = ui_functions("_processed_result_records", "_processed_result_choices", "_processed_result_rows",
                                "_processed_result_view", "_processed_result_manage", "_selected_values",
                                RESULT_DB=self.db, gr=GR, _safe_error=str)

    def manage(self, action, selected=(), query="", scope="selected", **kwargs):
        return self.api._processed_result_manage(action, kwargs.get("operation", "append"),
            kwargs.get("find", ""), kwargs.get("value", ", detailed"), list(selected), query, scope)

    def test_delete_selected_only_and_refresh_outputs(self):
        result = self.manage("delete", [str(self.ids[0])])
        self.assertEqual(len(result), 3)
        self.assertIsNone(self.db.get_prompt(self.ids[0]))
        self.assertIsNotNone(self.db.get_prompt(self.ids[1]))
        self.assertEqual(len(result[0]["value"]), 2)
        self.assertEqual(result[1]["value"], [])
        self.assertIn("1", result[2])

    def test_delete_filtered_does_not_touch_other_records(self):
        self.manage("delete", query="cat", scope="filtered")
        self.assertEqual([r["id"] for r in self.db.list_prompts(limit=None)], [self.ids[2]])
        self.assertEqual(self.db.undo_last_delete(), 2)

    def test_empty_selection_never_means_all(self):
        for action in ("edit", "delete"):
            with self.subTest(action=action):
                result = self.manage(action)
                self.assertEqual(len(self.db.list_prompts(limit=None)), 3)
                self.assertIn("选择", result[2])

    def test_selected_ids_respect_current_filter(self):
        self.manage("delete", [str(self.ids[2])], query="cat")
        self.assertEqual(len(self.db.list_prompts(limit=None)), 3)

    def test_edit_selected_and_filtered(self):
        self.manage("edit", [str(self.ids[0])], value=", selected")
        self.assertEqual(self.db.get_prompt(self.ids[0])["prompt"], "cat red, selected")
        self.assertEqual(self.db.get_prompt(self.ids[1])["prompt"], "cat blue")
        self.manage("edit", query="cat", scope="filtered", operation="replace", find="cat", value="kitten")
        self.assertTrue(self.db.get_prompt(self.ids[1])["prompt"].startswith("kitten"))
        self.assertEqual(self.db.get_prompt(self.ids[2])["prompt"], "dog green")

    def test_invalid_edit_does_not_blank_prompts(self):
        self.manage("edit", [str(self.ids[0])], operation="set", value="")
        self.assertEqual(self.db.get_prompt(self.ids[0])["prompt"], "cat red")

    def test_filtered_delete_is_not_limited_to_displayed_1000(self):
        for i in range(1002):
            self.db.save_prompt(f"extra result {i}")
        self.manage("delete", query="extra result", scope="filtered")
        self.assertEqual(len(self.db.list_prompts(limit=None)), 3)


class CacheInferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = StudioDB(Path(self.tmp.name) / "settings.db")
        self.base = dict(provider="OpenAI Compatible", endpoint="http://localhost/v1", model="test-model", temperature=1.7,
                         top_p=0.99, top_k=80, timeout=250, max_tokens=5000, retry_count=4, thinking_enabled=True,
                         thinking_budget=4000, reasoning_effort="high", send_temperature=False,
                         fallback_provider="Other", fallback_endpoint="http://localhost/other", fallback_model="other")
        self.llm = Mock(return_value="one cat")
        workflow = dict(system_override="", safety="SFW", nsfw_injection="", remove_bad=False, remove_terms="", shuffle=False, spaces=False, max_tags=0)
        self.api = ui_functions("_coerce_bool", "_processed_cache_inference_settings", "_transform_llm_settings",
            "_save_transform_llm_settings", "_save_processed_cache_inference_settings",
            "_run_llm_generation", "_inline_cache_transform", "_expand_or_polish", DB=self.db, normalize_sampling=normalize_sampling,
            TRANSFORM_LLM_SETTINGS_VERSION=1, TRANSFORM_LLM_SETTINGS_KEY="transform_llm_settings_v1",
            _run_llm_generation_impl=run_llm_generation,
            DEFAULT_LLM_SETTINGS={"provider": self.base["provider"]},
            PROVIDER_PROFILES={self.base["provider"]: {"default_endpoint": self.base["endpoint"]}},
            get_provider_profile=lambda provider: {"default_endpoint": self.base["endpoint"]},
            validate_endpoint=lambda value: value,
            PROCESSED_CACHE_INFERENCE_SETTINGS_VERSION=1, _connection_settings=lambda *args: self.base,
            _safe_error=str, _workflow_settings=lambda: workflow,
            _canonical_action=lambda x: x, _canonical_preset=lambda x: x, _canonical_base_model=lambda x: x,
            _canonical_provider=lambda x: x, _canonical_output_mode=lambda x: x,
            _resolve_preset_model=lambda p, b: (p, b), _aligned_preset=lambda p, b: (p, False),
            build_operation_instruction=lambda *args: "Keep source", _static_prompt_reference=lambda x: [],
            build_system_prompt=lambda *args, **kwargs: "system", build_user_message=lambda x: x,
            CREDENTIALS=SimpleNamespace(resolve=Mock(return_value="fake-test-key"), save=Mock(return_value=False), has_matching=Mock(return_value=False)), call_llm=self.llm,
            _MAX_DIVERSITY_RETRIES=1, _MAX_RESPONSE_RETRIES=1,
            _MAX_CONTENT_SIMILARITY=0.55,
            _diversity_settings=lambda: {"retry_limit": 0, "similarity_threshold": 0.55,
                                         "batch_temperature_floor": False},
            _worst_content_similarity=lambda *args, **kwargs: 0.0,
            _remember_diverse_output=lambda *args, **kwargs: True,
            _fallback_request_kwargs=lambda connection: {}, _finalize_generated_prompt=lambda r, *args: r)

    def test_defaults_are_conservative_and_only_connection_is_inherited(self):
        settings = self.api._processed_cache_inference_settings()
        self.assertEqual(settings["temperature"], 0.25)
        self.assertEqual(settings["top_p"], 0.9)
        self.assertFalse(settings["thinking_enabled"])
        self.assertTrue(settings["send_temperature"])
        for key in ("provider", "endpoint", "model"):
            self.assertEqual(settings[key], self.base[key])
        self.assertEqual(settings["fallback_model"], "")
        self.assertEqual(self.base["temperature"], 1.7)

    def test_legacy_cache_profile_is_migrated_once_into_transform_profile(self):
        self.db.set_setting("processed_cache_inference_v1", {
            "temperature": 0.4, "top_p": 0.65, "top_k": 18,
            "timeout": 110, "max_tokens": 2200, "retry_count": 1,
        })
        settings = self.api._transform_llm_settings()
        self.assertEqual((settings["temperature"], settings["top_p"], settings["top_k"]), (0.4, 0.65, 18))
        saved = self.db.get_setting("transform_llm_settings_v1")
        self.assertEqual(saved["provider"], self.base["provider"])
        self.assertEqual(saved["model"], self.base["model"])

    def test_save_and_reload_does_not_overwrite_main_settings(self):
        self.db.set_setting("llm_connections_v2", {"sentinel": "untouched"})
        message = self.api._save_processed_cache_inference_settings(0.15, 120, 2000, 0, "false", 0, "low", "true", 0.8, 25)
        self.assertIn("已保存", message)
        settings = self.api._processed_cache_inference_settings()
        self.assertEqual((settings["temperature"], settings["top_p"], settings["top_k"]), (0.15, 0.8, 25))
        self.assertFalse(settings["thinking_enabled"])
        self.assertEqual(settings["retry_count"], 0)
        self.assertEqual(self.db.get_setting("llm_connections_v2"), {"sentinel": "untouched"})

    def test_transform_connection_is_independent_from_generation_connection(self):
        message = self.api._save_transform_llm_settings(
            "OpenAI Compatible", "http://transform.local/v1", "transform-model", "",
            0.35, 140, 2400, True, 1, False, 0, "medium", 0.7, 30,
        )
        self.assertIn("转换 LLM 配置已保存", message)
        settings = self.api._transform_llm_settings()
        self.assertEqual(
            (settings["endpoint"], settings["model"], settings["temperature"]),
            ("http://transform.local/v1", "transform-model", 0.35),
        )
        self.assertEqual(self.base["endpoint"], "http://localhost/v1")
        self.api._inline_cache_transform("one cat", "Polish", "Danbooru Tags", "Auto", "", None)
        call = self.llm.call_args
        self.assertEqual(call.args[:4], ("OpenAI Compatible", "http://transform.local/v1", "transform-model", "fake-test-key"))
        self.assertEqual(call.args[6], 0.35)

    def test_cache_processing_uses_saved_parameters_in_actual_llm_call(self):
        self.api._save_processed_cache_inference_settings(0.2, 100, 1500, 0, False, 0, "low", True, 0.75, 20)
        result = self.api._inline_cache_transform("one cat", "Polish", "Danbooru Tags", "Auto", "", None)
        self.assertEqual(result[0], "one cat")
        call = self.llm.call_args
        self.assertEqual(call.args[6:10], (0.2, 100, 1500, True))
        self.assertEqual(call.kwargs["top_p"], 0.75)
        self.assertEqual(call.kwargs["top_k"], 20)
        self.assertEqual(call.kwargs["max_retries"], 0)
        self.assertEqual(call.args[:4], (self.base["provider"], self.base["endpoint"], self.base["model"], "fake-test-key"))

    def test_temperature_zero_and_retry_do_not_increase_conservative_sampling(self):
        self.api._save_processed_cache_inference_settings(0, 90, 2000, 0, False, 0, "", True, 0.9, None)
        self.llm.side_effect = ["", "one cat"]
        self.api._inline_cache_transform("one cat", "Polish", "Danbooru Tags", "Auto", "", None)
        self.assertEqual(len(self.llm.call_args_list), 2)
        self.assertEqual([call.args[6] for call in self.llm.call_args_list], [0.0, 0.0])


class UIEventContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((ROOT / "scripts/prompt_studio_ui.py").read_text(encoding="utf-8"))

    def event(self, owner, method="click"):
        return next(n for n in ast.walk(self.tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == owner and n.func.attr == method)

    def test_bulk_management_and_parameter_save_callback_arity(self):
        for owner, inputs, outputs in (("processed_result_manage_apply", 7, 3), ("cache_save", 10, 1)):
            with self.subTest(owner=owner):
                call = self.event(owner)
                args = {kw.arg: kw.value for kw in call.keywords}
                self.assertEqual(len(args["inputs"].elts), inputs)
                self.assertEqual(len(args["outputs"].elts) if isinstance(args["outputs"], ast.List) else 1, outputs)
                callback = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == call.args[0].id)
                self.assertEqual(len(callback.args.args), inputs)

    def test_delete_mode_hides_all_edit_controls(self):
        event = self.event("processed_result_manage_action", "change")
        callback = eval(compile(ast.Expression(event.args[0]), "ui-change", "eval"), {"gr": GR})
        self.assertEqual(callback("delete"), [{"visible": False}] * 3)
        self.assertEqual(callback("edit"), [{"visible": True}] * 3)
        self.assertEqual(len(next(kw.value for kw in event.keywords if kw.arg == "outputs").elts), 3)

    def test_single_and_bulk_accordions_are_consolidated(self):
        titles = [n.args[0].value for n in ast.walk(self.tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "Accordion" and n.args and isinstance(n.args[0], ast.Constant)]
        self.assertIn("编辑 / 删除当前记录", titles)
        self.assertIn("批量管理结果（编辑 / 删除）", titles)
        self.assertNotIn("删除当前记录", titles)
        self.assertNotIn("按全库序号批量管理", titles)

    def test_original_cache_callbacks_keep_output_contracts(self):
        api = ui_functions("_load_selected_record", "_selected_values", "_preview_selected", "_delete_previewed_records", "_apply_cache_edit",
                           DB=Mock(), gr=GR, _load_record=lambda ident: tuple(range(8)),
                           _cache_records=lambda *args: [{"id": 1, "prompt": "one cat"}],
                           _filtered_cache_updates=lambda *args: ({}, []),
                           CACHE_EDIT_OPERATIONS={"追加": "append", "查找替换": "replace"})
        self.assertEqual(len(api._load_selected_record([])), 8)
        self.assertEqual(len(api._load_selected_record(["7"])), 8)
        self.assertEqual(len(api._preview_selected([])), 2)
        self.assertEqual(len(api._delete_previewed_records(["7"], [])), 3)
        self.assertEqual(len(api._apply_cache_edit("追加", "", ", x", "", 0, "全部", "全部")), 3)
        for owner, expected in (("batch_preview_button", 2), ("delete_selected", 3), ("edit_apply", 3), ("delete_positions", 3)):
            event = self.event(owner)
            self.assertEqual(len(next(kw.value for kw in event.keywords if kw.arg == "outputs").elts), expected)


if __name__ == "__main__":
    unittest.main()
