"""AST-isolated profile routing tests; no Forge UI or user database is loaded."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from test_queue_regressions import ui_functions
from prompt_studio_core import PRESETS


class SystemProfileRoutingTests(unittest.TestCase):
    def profile_api(self, workflow=None, profiles=None):
        settings = {"workflow_settings_v1": dict(workflow or {}),
                    "prompt_system_profiles_v1": dict(profiles or {})}
        db = SimpleNamespace(get_setting=lambda key, default=None: settings.get(key, default),
                             set_setting=lambda key, value: settings.__setitem__(key, value))
        api = ui_functions(
            "_canonical_preset", "_canonical_base_model", "_resolve_preset_model",
            "_model_preset_value", "_model_preset_components",
            "_canonical_output_mode", "_coerce_bool", "_workflow_settings", "_save_workflow_values",
            "_preset_text", "_system_prompt_editor_value", "_system_prompt_override_value",
            "_system_prompt_profile_key", "_system_prompt_profiles", "_stored_system_prompt_profile", "_load_system_prompt_profile",
            "_save_system_prompt_profile", "_reset_system_prompt_profile",
            "_prompt_profile_change", "_prompt_profile_save", "_prompt_profile_reset",
            "_inline_cache_transform", "_prompt_preview_text",
            constants=("PRESET_UI_CHOICES", "MODEL_UI_CHOICES", "OUTPUT_UI_CHOICES",
                       "PRESET_VALUE_ALIASES", "BASE_MODEL_VALUE_ALIASES", "OUTPUT_VALUE_ALIASES",
                       "MODEL_PRESET_SELECTIONS", "MODEL_PRESET_UI_CHOICES",
                       "MODEL_PRESET_VALUE_ALIASES", "WORKFLOW_DEFAULTS",
                       "_SYSTEM_PROMPT_PROFILES_SETTING"),
            DB=db, PRESETS=PRESETS, DEFAULT_WILDCARDS="unused", gr=SimpleNamespace(update=lambda: {"update": True}),
            _canonical_action=lambda value: value,
            _transform_llm_settings=lambda: dict(provider="fake", endpoint="local", model="fake",
                temperature=0.0, timeout=90, max_tokens=1000, send_temperature=False),
            _expand_or_polish=Mock(return_value=("result", "ok")),
            build_system_prompt=Mock(return_value="preview"),
            build_operation_instruction=lambda action, model: action + ":" + model)
        return api, settings

    def test_keys_follow_the_selected_format_without_model_adaptation(self):
        api, _ = self.profile_api()
        for preset, model, expected in (
            ("Natural Language", "Anima", "Natural Language\x1f"),
            ("Danbooru Tags", "Krea 2", "Danbooru Tags\x1f"),
            ("Flux Natural", "Anima", "Flux Natural\x1fFlux"),
            ("Anima", "Krea 2", "Anima Tags\x1fAnima"),
        ):
            with self.subTest(preset=preset, model=model):
                self.assertEqual(api._system_prompt_profile_key(preset, model), expected)

    def test_workflow_clears_builtin_rules_saved_as_an_unowned_supplement(self):
        api, _ = self.profile_api(dict(preset="Anima Tags", base_model="Anima",
                                       system_override=PRESETS["Krea 2 Natural"]))
        self.assertEqual(api._workflow_settings()["system_override"], "")

    def test_workflow_loads_selected_target_profile(self):
        api, _ = self.profile_api(dict(preset="Anima Tags", base_model="Anima"),
                                  {"Anima Tags\x1fAnima": "anima supplement"})
        self.assertEqual(api._workflow_settings()["system_override"], "anima supplement")

    def test_current_owned_unsaved_edit_takes_precedence_over_saved_profile(self):
        key = "Anima Tags\x1fAnima"
        api, _ = self.profile_api(dict(preset="Anima Tags", base_model="Anima",
            system_override="unsaved edit", system_profile_key=key), {key: "saved supplement"})
        self.assertEqual(api._workflow_settings()["system_override"], "unsaved edit")
        self.assertEqual(api._load_system_prompt_profile("Anima Tags", "Anima"),
                         ("unsaved edit", "unsaved edit"))
        self.assertEqual(api._prompt_profile_change("Anima")[2:4], ("unsaved edit", "unsaved edit"))

    def test_workflow_switch_loads_target_and_preserves_same_target_edit(self):
        key = "Danbooru Tags\x1f"
        api, settings = self.profile_api(dict(preset="Danbooru Tags", base_model="",
            system_override="unsaved", system_profile_key=key), {"Anima Tags\x1fAnima": "anima"})
        api._save_workflow_values({"safety": "NSFW"})
        self.assertEqual(api._workflow_settings()["system_override"], "unsaved")
        api._save_workflow_values({"preset": "Anima Tags", "base_model": "Anima"})
        self.assertEqual(api._workflow_settings()["system_override"], "anima")
        api._save_workflow_values({"preset": "Krea 2 Natural", "base_model": "Krea 2"})
        self.assertEqual(api._workflow_settings()["system_override"], "")
        self.assertEqual(settings["workflow_settings_v1"]["system_profile_key"],
                         "Krea 2 Natural\x1fKrea 2")

    def test_save_and_reset_preserve_other_profiles(self):
        other, target = "Natural Language\x1f", "Anima Tags\x1fAnima"
        api, settings = self.profile_api(profiles={other: "keep"})
        api._save_system_prompt_profile("Anima Tags", "Anima", "warm colors")
        self.assertEqual(settings["prompt_system_profiles_v1"], {other: "keep", target: "warm colors"})
        self.assertEqual(api._workflow_settings()["system_override"], "warm colors")
        override, editor, _ = api._reset_system_prompt_profile("Anima Tags", "Anima")
        self.assertEqual((override, editor), ("", ""))
        self.assertEqual(settings["prompt_system_profiles_v1"], {other: "keep"})

    def test_saving_blank_clears_only_the_target_supplement(self):
        target, other = "Anima Tags\x1fAnima", "Krea 2 Natural\x1fKrea 2"
        api, settings = self.profile_api(profiles={target: "old", other: "keep"})
        override, _ = api._save_system_prompt_profile("Anima Tags", "Anima", "   ")
        self.assertEqual(override, "")
        self.assertEqual(settings["prompt_system_profiles_v1"], {other: "keep"})
        self.assertEqual(api._workflow_settings()["system_override"], "")

    def test_reading_saved_profiles_does_not_rewrite_the_database(self):
        key = "Krea 2 Natural\x1fKrea 2"
        saved = PRESETS["Krea 2 Natural"] + "\nUse warm colors."
        api, settings = self.profile_api(profiles={key: saved})
        self.assertEqual(api._load_system_prompt_profile("Krea 2 Natural", "Krea 2"), (saved, saved))
        self.assertEqual(settings["prompt_system_profiles_v1"], {key: saved})
        self.assertEqual(settings["workflow_settings_v1"], {})

    def test_profile_editor_defaults_to_empty_supplement(self):
        api, _ = self.profile_api()
        self.assertEqual(api._load_system_prompt_profile("Krea 2 Natural", "Krea 2"), ("", ""))

    def test_inline_cache_uses_the_explicit_preset_and_its_supplement(self):
        key = "Anima Tags\x1fAnima"
        api, _ = self.profile_api(dict(preset="Anima Tags", base_model="Anima",
            system_override="unsaved anima", system_profile_key=key),
            {"Krea 2 Natural\x1fKrea 2": "krea supplement"})
        transform = api._inline_cache_transform.__globals__["_expand_or_polish"]
        for selection, preset, model, supplement in (
                ("Anima", "Anima Tags", "Anima", "unsaved anima"),
                ("Krea 2", "Krea 2 Natural", "Krea 2", "krea supplement"),
                ("Natural Language", "Natural Language", "", "")):
            with self.subTest(selection=selection):
                api._inline_cache_transform("source", "Polish", selection, "", "user request", None)
                self.assertEqual(transform.call_args.args[2:5], (preset, supplement, model))

    def test_generic_and_named_presets_have_independent_supplements(self):
        api, _ = self.profile_api(profiles={"Natural Language\x1f": "generic",
                                          "Anima Tags\x1fAnima": "anima"})
        self.assertEqual(api._load_system_prompt_profile("Natural Language", "Anima"),
                         ("generic", "generic"))
        self.assertEqual(api._load_system_prompt_profile("Anima Tags", "Anima"), ("anima", "anima"))

    def test_workflow_only_user_edit_survives_read_and_unrelated_save(self):
        api, settings = self.profile_api(dict(preset="Anima Tags", base_model="Anima",
                                             system_override="workflow-only edit"))
        self.assertEqual(api._workflow_settings()["system_override"], "workflow-only edit")
        api._save_workflow_values({"safety": "NSFW"})
        self.assertEqual(settings["workflow_settings_v1"]["system_override"], "workflow-only edit")
        self.assertEqual(settings["workflow_settings_v1"]["system_profile_key"], "Anima Tags\x1fAnima")

    def test_canonical_generic_profile_takes_precedence_over_legacy_metadata(self):
        api, _ = self.profile_api(profiles={"Natural Language\x1fAuto / checkpoint default": "legacy",
                                          "Natural Language\x1f": "canonical"})
        self.assertEqual(api._load_system_prompt_profile("Natural Language", "")[0], "canonical")

    def test_clear_removes_same_format_legacy_keys_and_preserves_named_models(self):
        other = "Anima Tags\x1fAnima"
        api, settings = self.profile_api(profiles={"Natural Language\x1fAuto / checkpoint default": "old",
            "Natural Language\x1f": "canonical", other: "keep"})
        api._save_system_prompt_profile("Natural Language", "", "")
        self.assertEqual(api._load_system_prompt_profile("Natural Language", ""), ("", ""))
        self.assertEqual(settings["prompt_system_profiles_v1"], {other: "keep"})

    def test_explicit_foreign_ownership_is_not_migrated(self):
        api, _ = self.profile_api(dict(preset="Anima Tags", base_model="Anima",
            system_override="foreign", system_profile_key="Krea 2 Natural\x1fKrea 2"))
        self.assertEqual(api._workflow_settings()["system_override"], "")

    def test_preview_uses_selected_profile_and_current_owned_edit(self):
        key = "Anima Tags\x1fAnima"
        api, _ = self.profile_api(dict(preset="Anima Tags", base_model="Anima",
            system_override="unsaved", system_profile_key=key))
        self.assertEqual(api._prompt_preview_text(), "preview")
        call = api._prompt_preview_text.__globals__["build_system_prompt"].call_args
        self.assertEqual(call.args[:2], ("Anima Tags", "Anima"))
        self.assertEqual(call.args[7], "unsaved")


if __name__ == "__main__":
    unittest.main()
