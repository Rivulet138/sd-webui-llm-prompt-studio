"""Explicit model presets share one rule source across UI and requests."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

from test_queue_regressions import ui_functions

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prompt_studio_core as core


class ModelPresetSelectionTests(unittest.TestCase):
    def ui(self, workflow=None):
        settings = {"workflow_settings_v1": workflow or {}}
        database = SimpleNamespace(
            get_setting=lambda key, default=None: settings.get(key, default),
            set_setting=lambda key, value: settings.__setitem__(key, value),
        )
        return ui_functions(
            "_canonical_preset", "_canonical_base_model", "_resolve_preset_model",
            "_model_preset_value", "_model_preset_components", "_model_preset_state",
            "_workflow_settings", "_coerce_bool", "_canonical_output_mode",
            "_system_prompt_profile_key", "_system_prompt_profiles", "_stored_system_prompt_profile",
            "_api_generate", "_processed_kind_for_preset",
            constants=("PRESET_UI_CHOICES", "MODEL_UI_CHOICES", "MODEL_PRESET_SELECTIONS",
                       "MODEL_PRESET_UI_CHOICES", "PRESET_VALUE_ALIASES",
                       "BASE_MODEL_VALUE_ALIASES", "MODEL_PRESET_VALUE_ALIASES",
                       "WORKFLOW_DEFAULTS", "OUTPUT_UI_CHOICES", "OUTPUT_VALUE_ALIASES",
                       "_SYSTEM_PROMPT_PROFILES_SETTING"),
            MODEL_PRESET_FORMATS=core.MODEL_PRESET_FORMATS,
            MODEL_PRESET_GUIDANCE=core.MODEL_PRESET_GUIDANCE,
            PRESETS=core.PRESETS, DEFAULT_WILDCARDS="unused", DB=database,
            _connection_settings=lambda: dict(provider="fake", endpoint="local", model="fake",
                temperature=0, timeout=90, max_tokens=1000, send_temperature=False),
            _generate=Mock(return_value=("one cat", "system", "ok")),
        )

    def test_rules_have_one_canonical_definition(self):
        self.assertFalse(hasattr(core, "BASE_MODEL_GUIDANCE"))
        self.assertEqual(set(core.MODEL_PRESET_GUIDANCE),
                         {"Pony / Illustrious", "NoobAI", "Flux", "Anima", "Krea 2"})
        self.assertEqual(len(core.PRESETS), 8)
        for model, preset in core.MODEL_PRESET_FORMATS.items():
            self.assertIs(core.PRESETS[preset], core.MODEL_PRESET_GUIDANCE[model])

    def test_every_operation_sends_one_complete_model_preset(self):
        for model, preset in core.MODEL_PRESET_FORMATS.items():
            for operation in ("Generate", "Convert", "Expand", "Polish"):
                with self.subTest(model=model, operation=operation):
                    system = core.build_system_prompt(
                        preset, model, "SFW", "", "", [], operation=operation,
                        operation_instruction=core.build_operation_instruction(operation, model),
                    )
                    self.assertEqual(system.count("\n<model_preset>\n"), 1)
                    self.assertEqual(system.count(core.MODEL_PRESET_GUIDANCE[model]), 1)
                    self.assertEqual(system.count("Output profile:"), 1)
                    self.assertNotIn("<output_profile>", system)
                    self.assertNotIn("<model_profile>", system)

    def test_selected_model_name_works_through_the_request_entry_point(self):
        for model in core.MODEL_PRESET_GUIDANCE:
            system = core.build_system_prompt(model, "", "SFW", "", "", [])
            self.assertIn(core.MODEL_PRESET_GUIDANCE[model], system)

    def test_saved_builtin_rules_do_not_reappear_in_supplements(self):
        for model, preset in core.MODEL_PRESET_FORMATS.items():
            rules = core.MODEL_PRESET_GUIDANCE[model]
            system = core.build_system_prompt(
                preset, model, "SFW", "", "", [],
                system_override=rules + "\nKeep the brass kettle visibly dented.",
            )
            self.assertEqual(system.count(rules), 1)
            self.assertEqual(system.count("Keep the brass kettle visibly dented."), 1)

    def test_selector_contains_explicit_formats_and_models(self):
        api = self.ui()
        self.assertEqual([value for _, value in api.MODEL_PRESET_UI_CHOICES],
                         ["Danbooru Tags", "Danbooru + Natural", "Natural Language",
                          *core.MODEL_PRESET_GUIDANCE])
        for label, value in api.MODEL_PRESET_UI_CHOICES:
            self.assertNotIn("自动", label)
            self.assertNotIn("Auto", value)
            self.assertEqual(api._model_preset_state(value),
                             (core.MODEL_PRESET_FORMATS[value], value)
                             if value in core.MODEL_PRESET_FORMATS else (value, ""))

    def test_explicit_format_and_model_are_never_cross_adapted(self):
        api = self.ui()
        for model in core.MODEL_PRESET_GUIDANCE:
            for preset in core.PRESETS:
                expected_model = next((name for name, output in core.MODEL_PRESET_FORMATS.items()
                                       if output == preset), "")
                self.assertEqual(api._resolve_preset_model(preset, model), (preset, expected_model))
        self.assertEqual(api._resolve_preset_model("Flux", "Anima"),
                         ("Flux Natural", "Flux"))

    def test_internal_format_choices_use_supported_presets(self):
        api = self.ui()
        self.assertEqual({value for _, value in api.PRESET_UI_CHOICES}, set(core.PRESETS))
        self.assertEqual({value for _, value in api.MODEL_UI_CHOICES},
                         set(core.MODEL_PRESET_GUIDANCE))

    def test_generic_format_survives_saved_model_metadata(self):
        for preset in ("Danbooru Tags", "Danbooru + Natural", "Natural Language"):
            for model in ("Auto / checkpoint default", "Anima", "Flux", "Krea 2"):
                api = self.ui(dict(preset=preset, base_model=model))
                self.assertEqual(api._model_preset_value(preset, model), preset)
                values = api._workflow_settings()
                self.assertEqual((values["preset"], values["base_model"]), (preset, ""))

    def test_api_generation_uses_explicit_presets(self):
        api = self.ui()
        generate = api._api_generate.__globals__["_generate"]
        for selection, profile in api.MODEL_PRESET_SELECTIONS.items():
            with self.subTest(selection=selection):
                api._api_generate(dict(request="one cat", preset=selection, base_model="Anima"))
                self.assertEqual(generate.call_args.kwargs["preset"], profile[0])
                self.assertEqual(generate.call_args.kwargs["base_model"], profile[1])

    def test_generic_prompt_does_not_inherit_model_rules(self):
        for model in core.MODEL_PRESET_GUIDANCE:
            for preset in ("Danbooru Tags", "Danbooru + Natural", "Natural Language"):
                system = core.build_system_prompt(preset, model, "SFW", "", "", [])
                profile = system.split("\n<model_preset>\n", 1)[1].split("\n</model_preset>", 1)[0]
                self.assertEqual(profile, core.PRESETS[preset])

    def test_named_formats_keep_their_cache_content_kind(self):
        api = self.ui()
        self.assertEqual(api._processed_kind_for_preset("Flux Natural"), "natural")
        self.assertEqual(api._processed_kind_for_preset("Pony / Illustrious Tags"), "tags")
        self.assertEqual(api._processed_kind_for_preset("Danbooru + Natural"), "mixed")

    def test_reference_matches_the_rules_sent_to_the_model(self):
        reference = (Path(__file__).resolve().parents[1] / "docs/PROMPTS_REFERENCE_CN.md").read_text(
            encoding="utf-8")
        for rules in core.PRESETS.values():
            self.assertIn("```text\n" + rules + "\n```", reference)


if __name__ == "__main__":
    unittest.main()
