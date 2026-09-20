import ast
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.modules.setdefault("gradio", types.ModuleType("gradio"))

import prompt_studio_core as core
import prompt_studio_ui as ui


def literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"assignment not found: {name}")


class ChoiceContractTests(unittest.TestCase):
    def test_css_scope_does_not_restyle_forge_page(self):
        source = (ROOT / "style.css").read_text(encoding="utf-8")
        self.assertNotIn("body:has(#llm_prompt_studio_main_tabs)", source)
        self.assertIn("#llm_prompt_studio button:not(:disabled)", source)

    def test_shared_workflow_controls_are_independent(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        ui_block = source[source.index("save_workflow.click"):source.index("provider.change(")]
        self.assertNotIn("_bind_workflow_sync(", ui_block)
        self.assertNotIn("shared_workflow_fields", ui_block)
        self.assertNotIn("downstream_preset_components", ui_block)
        self.assertNotIn("downstream_base_model_components", ui_block)

    def test_preset_and_base_model_have_no_cross_panel_alignment(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        ui_block = source[source.index("save_workflow.click"):source.index("provider.change(")]
        self.assertNotIn("_preset_alignment_update", ui_block)
        self.assertNotIn("_preset_base_model_update", ui_block)
        self.assertNotIn("batch_base_model.change(", ui_block)
        self.assertNotIn("batch_preset.change(", ui_block)

    def test_cache_processing_lives_in_studio_and_retained_payload_ignores_backend_writeback(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        inline_panel = source[source.index("def _create_inline_panel"):source.index("def _wd14_interrogate")]
        self.assertNotIn("_create_inline_cache_panel(slot, prompt_target, embedded=True)", inline_panel)
        self.assertIn('_create_inline_cache_panel("txt2img", _PROMPT_TARGETS.get("txt2img"), result_queue_controls)', source)
        self.assertNotIn("json_payload", inline_panel)
        self.assertIn("png_batch_payload.input(", source)
        self.assertNotIn("json_payload.change(", source)
        self.assertNotIn("png_batch_payload.change(", source)

    def test_plugin_batch_is_the_only_import_workspace_and_can_pull_plugin_caches(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        batch_panel = source[source.index('with gr.Accordion("导入与插件批次"'):source.index('with gr.Tab("处理结果库"')]
        cache_panel = source[source.index('with gr.Tab("缓存"'):source.index('with gr.Tab("设置"')]
        self.assertIn('"读取 PNG Collector 当前缓存"', batch_panel)
        self.assertIn('"读取 Ranbooru 缓存"', batch_panel)
        self.assertIn('with gr.Accordion("Ranbooru 读取范围"', batch_panel)
        self.assertNotIn('with gr.Tab("直接导入")', source)
        self.assertNotIn('with gr.Accordion("Ranbooru 缓存联动"', cache_panel)

    def test_inline_cache_panel_is_collapsed_by_default(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        cache_panel = source[source.index("def _create_inline_cache_panel"):source.index("def _create_inline_panel")]
        self.assertIn('gr.Accordion("缓存 Prompt 处理", open=False', cache_panel)
        self.assertIn('"继续处理未完成"', cache_panel)
        self.assertIn('"将已处理结果加入生图队列"', cache_panel)

    def test_txt2img_prompt_batch_is_separate_from_studio_cache_processing(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        inline = source[source.index("def _create_inline_panel"):source.index("def _wd14_interrogate")]
        self.assertIn('with gr.Column(elem_id=f"llm_prompt_studio_{slot}_inline", elem_classes=["lps-inline-workbench"])', inline)
        self.assertIn('with gr.Accordion("Prompt 批量生成", open=False, elem_id=f"llm_prompt_studio_{slot}_inline_batch")', inline)
        studio = source[source.index("def on_ui_tabs"):source.index("workflow_inputs = [")]
        self.assertIn('with gr.Column(elem_id="llm_prompt_studio_cache_processing"', studio)
        self.assertIn('_create_inline_cache_panel("txt2img", _PROMPT_TARGETS.get("txt2img"), result_queue_controls)', studio)
        self.assertIn('("原始缓存库", "cache")', inline)
        self.assertIn('("处理结果库", "processed_cache")', inline)

    def test_processed_results_have_an_independent_library_and_queue_entry(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn("RESULT_DB = StudioDB(PROCESSED_DB_PATH)", source)
        self.assertIn('with gr.Tab("处理结果库"', source)
        self.assertIn("processed_result_enqueue.click(", source)
        self.assertIn('("全部筛选结果", "filtered")', source)
        self.assertNotIn("processed_result_enqueue_all", source)
        self.assertIn("_save_inline_processed_result", source)
        auto_loop = (ROOT / "javascript" / "llm_prompt_studio_auto_loop.js").read_text(encoding="utf-8")
        self.assertIn("readTxt2imgSettings", auto_loop)
        self.assertIn('"negative_prompt"', auto_loop)
        self.assertIn('"sampler_name"', auto_loop)

    def test_png_collector_pull_reads_the_shared_payload_component(self):
        source = (ROOT / "javascript" / "llm_prompt_studio_png_batch.js").read_text(encoding="utf-8")
        self.assertIn('componentValue("ppc_prompt_batch_cache")', source)
        self.assertIn('setValue(target, JSON.stringify(batch))', source)
        self.assertIn("loadCollectorCache", source)

    def test_inline_workbench_is_injected_only_into_txt2img(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        capture = source[source.index("def capture_prompt_component"):source.index("def inject_inline_before_negative")]
        inject = source[source.index("def inject_inline_before_negative"):source.index("def _inline_cache_transform")]
        self.assertIn('if elem_id != "txt2img_prompt"', capture)
        self.assertIn('if elem_id != "txt2img_neg_prompt_row"', inject)
        self.assertNotIn("img2img_prompt", capture)
        self.assertNotIn("img2img_neg_prompt_row", inject)

    def test_settings_expose_an_independent_fallback_connection(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        settings = source[source.index('with gr.Tab("设置"'):source.index('with gr.Tab("更多"')]
        for elem_id in (
            "llm_prompt_studio_fallback_provider", "llm_prompt_studio_fallback_endpoint",
            "llm_prompt_studio_fallback_model", "llm_prompt_studio_fallback_api_key",
            "llm_prompt_studio_fallback_test",
        ):
            self.assertIn(elem_id, settings)
        self.assertIn("fallback_provider.input(", source)
        self.assertNotIn("fallback_provider.change(", source)
        self.assertIn("fallback_discover_models.click(", source)

    def test_inline_generation_preserves_the_saved_inference_snapshot(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        inline = source[source.index("def _inline_generate"):source.index("def _unwrap_component")]
        self.assertIn("connection_settings=connection", inline)
        self.assertIn("preserve_inference_settings=True", inline)

    def test_template_picker_returns_the_selected_template(self):
        self.assertEqual(ui._template_request("general"), ui.GENERAL_CREATIVE_REQUEST_TEMPLATE)
        self.assertEqual(ui._template_request("kemonimimi"), ui.KEMONOMIMI_LOLI_BATCH_TEMPLATE)

    def test_kemonimimi_template_enforces_reference_scene_contract(self):
        template = ui.KEMONOMIMI_LOLI_BATCH_TEMPLATE
        for phrase in ("参考图共同方向", "前景/中景/背景", "批次规划", "至少三类高层因素", "LoRA 或触发词", "单个可见", "完整得体"):
            self.assertIn(phrase, template)
        self.assertIn("禁止输出分镜、拼图、多面板", template)

    def test_prompt_quality_gate_rejects_meta_and_scene_conflicts(self):
        with self.assertRaises(ValueError):
            ui._finalize_generated_prompt("Let me restart inspection of the prompt", "Natural Language", "SFW")
        with self.assertRaises(ValueError):
            ui._finalize_generated_prompt("a person outdoors in daylight under midnight moonlight", "Natural Language", "SFW")

    def test_immutable_technical_tokens_are_extracted_without_interpretation(self):
        source = "<lora:style_x:0.75> char_trigger red coat"
        tokens = ui._immutable_technical_tokens(source)
        self.assertIn("<lora:style_x:0.75>", tokens)
        self.assertIn("char_trigger", tokens)
        self.assertTrue(ui._preserves_immutable_technical_tokens(source, source + ", standing in a studio"))
        self.assertFalse(ui._preserves_immutable_technical_tokens(source, "red coat, standing in a studio"))

    def test_inline_empty_source_has_autonomous_generation_fallback(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn("Create one original, directly usable English image-generation prompt", source)
        self.assertIn("TECHNICAL TOKENS (verbatim, excluded from semantic expansion)", source)

    def test_custom_templates_are_persistent_and_callable(self):
        stored = {}
        with mock.patch.object(ui.gr, "update", side_effect=lambda **kwargs: kwargs, create=True), \
             mock.patch.object(ui.DB, "get_setting", side_effect=lambda key, default=None: stored.get(key, default)), \
             mock.patch.object(ui.DB, "set_setting", side_effect=lambda key, value: stored.__setitem__(key, value)):
            status, update = ui._save_custom_template("夜景", "cinematic night scene")
            self.assertIn("已保存", status)
            self.assertIn("cinematic night scene", ui._template_request("custom:夜景"))
            status, _ = ui._save_custom_template("夜景", "updated scene")
            self.assertIn("已保存", status)
            self.assertIn("updated scene", ui._template_request("custom:夜景"))
            status, _ = ui._save_custom_template("general", "bad")
            self.assertIn("内置模板", status)
            status, _ = ui._delete_custom_template("custom:夜景")
            self.assertIn("已删除", status)
            self.assertEqual(ui._template_request("custom:夜景"), "")

    def test_custom_template_controls_are_on_generate_panel(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        generate_panel = source[source.index('with gr.Tab("生成"'):source.index('with gr.Tab("缓存"')]
        self.assertIn('elem_id="llm_prompt_studio_template_name"', generate_panel)
        self.assertIn('elem_id="llm_prompt_studio_template_save"', generate_panel)
        self.assertIn('elem_id="llm_prompt_studio_template_delete"', generate_panel)
        self.assertIn('elem_id="llm_prompt_studio_template_default"', generate_panel)
        self.assertIn('CUSTOM_TEMPLATES_SETTING = "prompt_templates_v1"', source)

    def test_json_writeback_scope_keeps_component_output_arity(self):
        payload = {"schema_version": ui.PNG_BATCH_SCHEMA, "producer": {}, "records": []}
        with mock.patch.object(ui, "_png_batch_advance_after_append", return_value=(payload, 1, "", "ok", [])):
            result = ui._png_batch_mark_appended(payload, 1, [], "current", True)
        self.assertEqual(len(result), 6)

    def test_inline_json_handlers_match_declared_output_arity(self):
        with mock.patch.object(ui, "_png_batch_load", return_value=(1, 2, 3, 4, 5, 6)):
            self.assertEqual(ui._inline_png_batch_load("batch.json"), (1, 2, 3, 4, 5))
        with mock.patch.object(ui, "_png_batch_refresh", return_value=(1, 2, 3, 4, 5)):
            self.assertEqual(ui._inline_png_batch_refresh("{}", 1), (1, 2, 3, 4))

    def test_server_queue_cancel_events_are_isolated_by_batch(self):
        first = ui._server_queue_cancel_event("batch-a")
        second = ui._server_queue_cancel_event("batch-b")
        first.set()
        self.assertTrue(first.is_set())
        self.assertFalse(second.is_set())
        with mock.patch.object(ui.DB, "list_server_queue", return_value=[]):
            ui._release_server_queue_cancel_event("batch-a")
            ui._release_server_queue_cancel_event("batch-b")
        self.assertNotIn("batch-a", ui._SERVER_QUEUE_CANCEL_EVENTS)
        self.assertNotIn("batch-b", ui._SERVER_QUEUE_CANCEL_EVENTS)

    def test_server_queue_worker_only_starts_after_explicit_enqueue(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        app_started = source[source.index("def on_app_started"):source.index("def on_ui_tabs")]
        enqueue = source[source.index("def _enqueue_server_queue"):source.index("def _server_queue_html")]
        self.assertNotIn("_ensure_server_queue_worker()", app_started)
        self.assertIn("_ensure_server_queue_worker()", enqueue)

    def test_legacy_connection_settings_are_migrated_before_deletion(self):
        legacy = {
            "provider": "OpenAI Compatible", "endpoint": "http://127.0.0.1:1234/v1",
            "model": "legacy-model", "temperature": 0.8, "timeout": 30,
        }
        settings = {"llm_connection": legacy}

        def get_setting(key, default=None):
            return settings.get(key, default)

        def set_setting(key, value):
            settings[key] = value

        def delete_setting(key):
            settings.pop(key, None)

        with mock.patch.object(ui.DB, "get_setting", side_effect=get_setting), mock.patch.object(
            ui.DB, "set_setting", side_effect=set_setting
        ), mock.patch.object(ui.DB, "delete_setting", side_effect=delete_setting):
            migrated = ui._connection_store()
        self.assertEqual(migrated["providers"]["OpenAI Compatible"]["model"], "legacy-model")
        self.assertNotIn("llm_connection", settings)
        self.assertIn("llm_connections_v2", settings)

    def test_ui_choice_values_match_backend_contracts(self):
        path = SCRIPTS / "prompt_studio_ui.py"
        preset_choices = literal_assignment(path, "PRESET_UI_CHOICES")
        model_choices = literal_assignment(path, "MODEL_UI_CHOICES")
        action_choices = literal_assignment(path, "ACTION_UI_CHOICES")
        preset_values = {value for _, value in preset_choices}
        self.assertTrue(set(core.PRESETS).issubset(preset_values))
        self.assertIn("Pony / Illustrious Tags", preset_values)
        self.assertIn("Flux Natural", preset_values)
        self.assertEqual({value for _, value in model_choices}, set(core.BASE_MODEL_GUIDANCE))
        self.assertEqual({value for _, value in action_choices}, {"Convert", "Expand", "Polish"})

    def test_localized_labels_normalize_to_internal_values(self):
        for label, value in ui.PRESET_UI_CHOICES:
            self.assertEqual(ui._canonical_preset(label), value)
        for label, value in ui.MODEL_UI_CHOICES:
            self.assertEqual(ui._canonical_base_model(label), value)
        for label, value in ui.OUTPUT_UI_CHOICES:
            self.assertEqual(ui._canonical_output_mode(label), value)
        for label, value in ui.PROVIDER_UI_CHOICES:
            self.assertEqual(ui._canonical_provider(label), value)
        for label, value in ui.ACTION_UI_CHOICES:
            self.assertEqual(ui._canonical_action(label), value)
        for label, value in ui.JSON_VARIATION_MODE_CHOICES:
            self.assertEqual(ui._canonical_variation_mode(label), value)

    def test_combined_presets_resolve_their_model_profile(self):
        self.assertEqual(ui._resolve_preset_model("Pony / Illustrious Tags", "Auto / checkpoint default"), ("Danbooru Tags", "Pony / Illustrious"))
        self.assertEqual(ui._resolve_preset_model("Flux Natural", "Auto / checkpoint default"), ("Natural Language", "Flux"))
        self.assertEqual(ui._resolve_preset_model("Krea 2 Natural", "Auto / checkpoint default"), ("Krea 2 Natural", "Krea 2"))

    def test_generate_api_forwards_fields_by_name(self):
        connection = {
            "provider": "OpenAI Compatible", "endpoint": "http://127.0.0.1:1234/v1",
            "model": "test-model", "temperature": 0.7, "timeout": 45,
            "max_tokens": 2048, "send_temperature": True,
        }
        payload = {
            "request": "request", "source_tags": "tags", "preset": "Krea 2 自然语言",
            "base_model": "Krea 2", "provider": "自定义 OpenAI 兼容接口", "safety": "SFW", "remove_bad": False,
            "remove_terms": "bad", "shuffle": True, "spaces": True, "max_tags": 17,
            "structured_mode": "普通提示词", "region_count": 3,
            "save_score": 4.5, "cache_result": True,
        }
        with mock.patch.object(ui, "_connection_settings", return_value=connection), mock.patch.object(
            ui, "_generate", return_value=("result", "system", "ok")
        ) as generate:
            result = ui._api_generate(payload)
        self.assertEqual(result["prompt"], "result")
        kwargs = generate.call_args.kwargs
        self.assertEqual(kwargs["preset"], "Krea 2 Natural")
        self.assertEqual(kwargs["provider"], "OpenAI Compatible")
        self.assertEqual(kwargs["structured_mode"], "Plain Prompt")
        for field in (
            "request", "source_tags", "base_model", "safety", "remove_bad", "remove_terms",
            "shuffle", "spaces", "max_tags", "region_count", "save_score",
            "cache_result",
        ):
            self.assertEqual(kwargs[field], payload[field])

    def test_generate_combines_source_tags_and_creative_request(self):
        captured = {}

        def fake_call(*args, **kwargs):
            captured["user"] = args[5]
            return "one girl standing in a rainy street"

        with mock.patch.object(ui.CREDENTIALS, "resolve", return_value=""), mock.patch.object(
            ui, "call_llm", side_effect=fake_call
        ), mock.patch.object(ui, "_static_prompt_reference", return_value=[]), mock.patch.object(
            ui, "_connection_settings", return_value={"retry_count": 0}
        ):
            generated, _system, status = ui._generate(
                "change the setting to a rainy street", "1girl, red_hair", "Natural Language", "",
                "Flux", "SFW", "", "", "OpenAI Compatible", "http://127.0.0.1:1234/v1",
                "test-model", "", 1.0, 30, 512, True, True, "", False, False, 0,
                "Plain Prompt", 1, 0, False,
            )
        self.assertTrue(generated, status)
        self.assertIn("1girl, red_hair", captured["user"])
        self.assertIn("change the setting to a rainy street", captured["user"])


if __name__ == "__main__":
    unittest.main()
