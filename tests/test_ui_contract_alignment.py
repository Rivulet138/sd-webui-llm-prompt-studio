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
    def test_template_picker_returns_the_selected_template(self):
        self.assertEqual(ui._template_request("general"), ui.GENERAL_CREATIVE_REQUEST_TEMPLATE)
        self.assertEqual(ui._template_request("kemonimimi"), ui.KEMONOMIMI_LOLI_BATCH_TEMPLATE)

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
        self.assertEqual({value for _, value in preset_choices}, set(core.PRESETS))
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
