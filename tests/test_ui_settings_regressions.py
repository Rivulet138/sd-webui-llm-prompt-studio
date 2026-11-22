"""Standalone UI settings regressions with temporary stores and fake providers."""
import ast
import logging
from pathlib import Path
import random
import sys
import tempfile
import threading
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prompt_studio_core import LLMRequestError, StudioDB
from prompt_studio_generation import run_llm_generation


def ui_functions(*names, constants=(), **context):
    """Load only the requested callbacks, without importing Forge or Gradio."""
    source_path = ROOT / "scripts/prompt_studio_ui.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            selected.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(target, "id", None) in constants for target in targets):
                selected.append(node)
    namespace = dict(Any=Any, LOGGER=logging.getLogger(__name__))
    namespace.update(context)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source_path), "exec"), namespace)  # noqa: S102 - loads selected repository functions into an isolated test namespace
    return SimpleNamespace(**namespace)


class TemplateDimensionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database_path = Path(temporary.name) / "settings.db"
        self.db = StudioDB(self.database_path)
        self.api = ui_functions(
            "_coerce_bool", "_template_record", "_builtin_template_defaults",
            "_builtin_templates", "_custom_templates", "_save_custom_template",
            constants=("BUILTIN_TEMPLATE_CHOICES", "BUILTIN_TEMPLATES_SETTING",
                       "CUSTOM_TEMPLATES_SETTING"),
            DB=self.db, GENERAL_CREATIVE_REQUEST_TEMPLATE="General prompt",
            KEMONOMIMI_TEMPLATE="Character prompt", _INLINE_TEMPLATE_COMPONENTS={},
            _template_dropdown_update=lambda **values: values,
        )

    def save_and_reload(self, name, dimensions):
        message, *_ = self.api._save_custom_template(
            name, "Updated prompt", variation_dimensions=dimensions,
        )
        self.assertIn("已保存", message)
        reopened = StudioDB(self.database_path)
        builtin = name in {"general", "kemonimimi"}
        key = self.api.BUILTIN_TEMPLATES_SETTING if builtin else self.api.CUSTOM_TEMPLATES_SETTING
        self.assertEqual(reopened.get_setting(key)[name]["content"], "Updated prompt")
        return reopened.get_setting(key)[name]["variation_dimensions"]

    def test_builtin_and_custom_dimensions_accept_editor_text(self):
        for name in ("general", "kemonimimi", "My template"):
            with self.subTest(template=name):
                self.assertEqual(self.save_and_reload(name, " action, camera , , lighting "),
                                 ["action", "camera", "lighting"])
                records = (self.api._builtin_templates() if name != "My template"
                           else self.api._custom_templates())
                self.assertEqual(records[name]["variation_dimensions"],
                                 ["action", "camera", "lighting"])

    def test_dimensions_can_be_explicitly_cleared(self):
        for name in ("general", "kemonimimi", "My template"):
            for empty in ("", " , , ", []):
                with self.subTest(template=name, empty=empty):
                    self.save_and_reload(name, ["action"])
                    self.assertEqual(self.save_and_reload(name, empty), [])

    def test_list_dimensions_use_the_same_normalization(self):
        for name in ("general", "My template"):
            with self.subTest(template=name):
                self.assertEqual(self.save_and_reload(name, [" action ", "", " camera ", 7]),
                                 ["action", "camera", "7"])

    def test_record_normalizes_saved_text_and_retains_missing_field_fallback(self):
        fallback = {"variation_dimensions": ["lighting"]}
        self.assertEqual(self.api._template_record({}, fallback)["variation_dimensions"],
                         ["lighting"])
        for value, expected in ((" action, camera ", ["action", "camera"]),
                                ("", []), ([], []), (None, []), (17, [])):
            with self.subTest(value=value):
                self.assertEqual(self.api._template_record(
                    {"variation_dimensions": value}, fallback)["variation_dimensions"], expected)


class WildcardDirectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database_path = self.root / "settings.db"
        self.db = StudioDB(self.database_path)
        self.default = self.root / "default"
        self.custom = self.root / "custom"
        for directory, term in ((self.default, "default_term"), (self.custom, "custom_term")):
            directory.mkdir()
            (directory / "terms.txt").write_text(term + "\n", encoding="utf-8")
        self.workflow = dict(preset="Danbooru Tags", base_model="", system_profile_key="profile",
                             wildcard_path=str(self.default), user_instruction="Keep this instruction",
                             wd_model="custom-tagger")
        self.db.set_setting("workflow_settings_v1", self.workflow)

    def api(self, database):
        return ui_functions(
            "_index_wildcards", "_auto_index_wildcards", "_save_workflow_values",
            DB=database, DEFAULT_WILDCARDS=self.default,
            _workflow_settings=lambda: database.get_setting("workflow_settings_v1", {}),
            _resolve_preset_model=lambda preset, model: (preset, model),
            _system_prompt_profile_key=lambda *args: "profile",
            _stored_system_prompt_profile=lambda *args: "",
            gr=SimpleNamespace(update=lambda **values: values), _safe_error=str,
        )

    def test_successful_index_persists_directory_and_restart_keeps_custom_terms(self):
        status, matches, categories = self.api(self.db)._index_wildcards(str(self.custom))
        self.assertIn("索引完成", status)
        self.assertEqual(matches["choices"], ["custom_term"])
        self.assertIn("terms", categories["choices"])
        reopened = StudioDB(self.database_path)
        workflow = reopened.get_setting("workflow_settings_v1")
        self.assertEqual(workflow["wildcard_path"], str(self.custom))
        for key, value in self.workflow.items():
            if key != "wildcard_path":
                self.assertEqual(workflow[key], value)
        self.assertEqual(self.api(reopened)._auto_index_wildcards(), (0, 1))
        self.assertEqual(reopened.wildcard_matches(""), ["custom_term"])

    def test_failed_index_keeps_previous_directory_and_index(self):
        self.db.index_wildcards(self.default)
        status, *_ = self.api(self.db)._index_wildcards(str(self.root / "missing"))
        self.assertIn("索引失败", status)
        self.assertEqual(self.db.get_setting("workflow_settings_v1"), self.workflow)
        self.assertEqual(self.db.wildcard_matches(""), ["default_term"])

    def test_blank_selection_persists_effective_default_directory(self):
        self.db.set_setting("workflow_settings_v1", {**self.workflow, "wildcard_path": str(self.custom)})
        status, *_ = self.api(self.db)._index_wildcards("")
        self.assertIn("索引完成", status)
        self.assertEqual(self.db.get_setting("workflow_settings_v1")["wildcard_path"],
                         str(self.default))


class BatchTemperatureFloorTests(unittest.TestCase):
    def setUp(self):
        self.connection = dict(retry_count=3, top_p=0.72, top_k=21, thinking_enabled=True,
                               thinking_budget=1024, reasoning_effort="high")
        self.settings = dict(batch_temperature_floor=True, recipe_enabled=False,
                             retry_limit=1, similarity_threshold=0.82)
        self.provider = Mock(return_value="1girl, reading")
        self.similarity = Mock(side_effect=[1.0, 0.0])
        self.runner = Mock(side_effect=run_llm_generation)
        self.api = ui_functions(
            "_coerce_bool", "_generate", "_batch_generate", "_run_llm_generation",
            constants=("_MIN_INDEPENDENT_BATCH_TEMPERATURE", "_MAX_CONTENT_SIMILARITY",
                       "_MAX_RESPONSE_RETRIES"),
            _canonical_provider=lambda value: value,
            _resolve_preset_model=lambda preset, model: (preset, model),
            _canonical_output_mode=lambda value: value,
            _connection_settings=lambda *args: dict(self.connection),
            _diversity_settings=lambda: dict(self.settings),
            _recent_diverse_outputs=lambda source: [],
            _diversity_exclusion_terms_from_outputs=lambda *args: [],
            _remember_diverse_output=Mock(),
            build_operation_instruction=lambda *args: "Generate",
            build_system_prompt=lambda *args, **kwargs: "system",
            _finalize_generated_prompt=lambda raw, *args: raw,
            LLMRequestError=LLMRequestError, _safe_error=str,
            _BATCH_CONTEXT=threading.local(), _BATCH_LOCK=threading.Lock(),
            _BATCH_CONTROL_LOCK=threading.Lock(), _BATCH_CANCEL=threading.Event(),
            _BATCH_ACTIVE_TASK_ID="", random=random,
            _normalize_generation_count=int,
            _build_inspiration_sources=lambda *args: (["a girl reading"], {}),
            _parse_batch_sources=lambda text: ([text], {}),
            _independent_batch_directive=lambda *args, **kwargs: "Vary allowed details.",
            _filtered_cache_updates=lambda *args: ({}, {}),
            _batch_output=lambda *args, **kwargs: (args, kwargs),
            gr=SimpleNamespace(update=lambda **values: values),
            DB=SimpleNamespace(save_prompts_batch=Mock(return_value=dict(
                inserted=1, duplicates=0, updated=0))),
            _run_llm_generation_impl=self.runner,
            CREDENTIALS=SimpleNamespace(resolve=lambda *args: "resolved-key"),
            call_llm=self.provider, build_user_message=lambda *args: "user message",
            _fallback_request_kwargs=lambda connection: {},
            _worst_content_similarity=self.similarity,
        )
        self.arguments = dict(
            preset="Danbooru Tags", system_override="", base_model="", safety="SFW",
            nsfw_injection="", user_instruction="", provider="OpenAI Compatible",
            endpoint="http://localhost/v1", model="fake", api_key="", temperature=0.0,
            timeout=73, max_tokens=1234, send_temperature=True,
            remove_bad=False, remove_terms="", shuffle=False, spaces=False, max_tags=0,
            structured_mode="Plain Prompt", region_count=1,
        )

    def generate(self, **changes):
        arguments = dict(self.arguments, request="a girl reading", source_tags="",
                         save_score=0, cache_result=False, batch_directive="Vary allowed details.",
                         preserve_inference_settings=True)
        arguments.update(changes)
        result, _, status = self.api._generate(**arguments)
        self.assertEqual(result, "1girl, reading", status)

    def assert_inference_settings(self, temperature, preserved=True):
        self.assertTrue(self.provider.called)
        for call in self.provider.call_args_list:
            self.assertEqual(call.args[6:10], (temperature, 73, 1234, self.arguments["send_temperature"]))
            for key, value in self.connection.items():
                provider_key = "max_retries" if key == "retry_count" else key
                self.assertEqual(call.kwargs[provider_key], value)
        for call in self.runner.call_args_list:
            self.assertEqual(call.args[14], preserved)
        self.assertEqual(self.connection, dict(retry_count=3, top_p=0.72, top_k=21,
                                             thinking_enabled=True, thinking_budget=1024,
                                             reasoning_effort="high"))

    def test_explicit_floor_applies_with_preserved_inference_settings(self):
        self.generate()
        self.assert_inference_settings(self.api._MIN_INDEPENDENT_BATCH_TEMPERATURE)

    def test_floor_stays_constant_across_diversity_retries(self):
        self.generate(batch_history=["1girl, reading"])
        self.assertEqual(self.provider.call_count, 2)
        self.assert_inference_settings(self.api._MIN_INDEPENDENT_BATCH_TEMPERATURE)

    def test_batch_entry_point_honors_opt_in_and_preserves_other_settings(self):
        output = list(self.api._batch_generate(
            source_text="a girl reading", skip_existing=False, skip_failed=False,
            generation_count=1, **self.arguments,
        ))
        self.assertTrue(output)
        self.assertEqual(output[-1][1]["result_rows"][0][2], "1girl, reading")
        self.assert_inference_settings(self.api._MIN_INDEPENDENT_BATCH_TEMPERATURE)
        self.api.DB.save_prompts_batch.assert_called_once()
        self.assertFalse(self.api._BATCH_LOCK.locked())

    def test_disabled_floor_keeps_zero_temperature(self):
        self.settings["batch_temperature_floor"] = False
        self.generate(batch_history=["1girl, reading"])
        self.assertEqual(self.provider.call_count, 2)
        self.assert_inference_settings(0.0)

    def test_ordinary_generation_keeps_zero_temperature(self):
        self.generate(batch_directive="")
        self.assert_inference_settings(0.0)

    def test_inline_delta_keeps_zero_temperature(self):
        self.generate(inline_delta=True)
        self.assert_inference_settings(0.0)

    def test_floor_does_not_reduce_higher_temperature(self):
        self.generate(temperature=1.4)
        self.assert_inference_settings(1.4)

    def test_floor_preserves_send_temperature_switch(self):
        self.arguments["send_temperature"] = False
        self.generate()
        self.assert_inference_settings(self.api._MIN_INDEPENDENT_BATCH_TEMPERATURE)

    def test_floor_still_applies_when_inference_preservation_is_disabled(self):
        self.generate(preserve_inference_settings=False)
        self.assert_inference_settings(self.api._MIN_INDEPENDENT_BATCH_TEMPERATURE, preserved=False)


if __name__ == "__main__":
    unittest.main()
