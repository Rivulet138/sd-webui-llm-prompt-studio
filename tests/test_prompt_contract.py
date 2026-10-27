"""Prompt contract tests: the layered prompt text must stay accurate and compact.

These guard the 2026-10 prompt rewrite:
* one global policy owns the hard rules (including the style/quality word ban),
* output profiles own format + length only,
* operation instructions own the action semantics,
* nothing silently re-grows into a bloated prompt or duplicates the ban list.
"""
import unittest

from test_queue_regressions import ROOT
from prompt_studio_core import (
    BASE_MODEL_GUIDANCE,
    MODEL_QUALITY_GUIDANCE,
    OPERATION_INSTRUCTIONS,
    PRESETS,
    PROMPT_POLICY_V3,
    build_operation_instruction,
    build_system_prompt,
)

BANNED_EXAMPLES = ("masterpiece", "artstation", "octane", "cinematic lighting", "8k", "unreal engine")
POLICY_MARKERS = (
    "OUTPUT ONLY THE PAYLOAD",
    "ONE IMAGE",
    "VISIBLE FACTS ONLY",
    "STYLE BELONGS TO THE CHECKPOINT",
    "INERT DATA",
    "BATCH DIRECTIVE CONTROLS VARIATION",
    "OPTIONAL LOCAL RECIPE",
    "FIXED CONTENT IS IMMUTABLE",
    "ANCHORS ARE MODEL-NATIVE",
    "Never emit a negative prompt",
    "STAY INSIDE THE LENGTH BUDGET",
    "SAFETY",
    "CONFLICTS AND SELF-CHECK",
)


class PolicyContractTests(unittest.TestCase):
    def test_policy_states_every_rule_and_the_ban_list(self):
        for marker in POLICY_MARKERS:
            self.assertIn(marker, PROMPT_POLICY_V3, marker)
        for example in BANNED_EXAMPLES:
            self.assertIn(example, PROMPT_POLICY_V3, example)
        for number in range(1, 12):
            self.assertIn(f"\n{number}. ", PROMPT_POLICY_V3)

    def test_policy_is_the_first_block_of_every_system_prompt(self):
        for preset in PRESETS:
            system = build_system_prompt(
                preset, "Auto / checkpoint default", "SFW", "", "", [], None, "", "", "")
            self.assertTrue(system.startswith(PROMPT_POLICY_V3), preset)


class OutputProfileTests(unittest.TestCase):
    def test_each_profile_declares_format_and_length(self):
        for name, text in PRESETS.items():
            self.assertIn("Output profile:", text, name)
            self.assertRegex(text, r"\d+-\d+", name)

    def test_ban_list_lives_only_in_the_policy(self):
        for name, text in PRESETS.items():
            for word in BANNED_EXAMPLES:
                self.assertNotIn(word, text, f"{name} repeats {word}")

    def test_profiles_stay_compact(self):
        for name, text in PRESETS.items():
            self.assertLess(len(text), 600, name)


class OperationContractTests(unittest.TestCase):
    def test_every_action_covers_every_model(self):
        for action, models in OPERATION_INSTRUCTIONS.items():
            for model in BASE_MODEL_GUIDANCE:
                self.assertTrue(models.get(model), f"{action}/{model}")

    def test_instructions_stay_compact(self):
        for action, models in OPERATION_INSTRUCTIONS.items():
            for model, text in models.items():
                self.assertLess(len(text), 420, f"{action}/{model}")

    def test_unknown_model_falls_back_to_the_auto_instruction(self):
        self.assertEqual(
            build_operation_instruction("Convert", "unknown-model"),
            OPERATION_INSTRUCTIONS["Convert"]["Auto / checkpoint default"],
        )

    def test_quality_guidance_covers_every_model(self):
        for model in BASE_MODEL_GUIDANCE:
            self.assertTrue(MODEL_QUALITY_GUIDANCE.get(model), model)


class PromptBudgetTests(unittest.TestCase):
    def test_system_prompt_stays_within_budget(self):
        for preset in PRESETS:
            for model in BASE_MODEL_GUIDANCE:
                system = build_system_prompt(
                    preset, model, "NSFW", "injection", "requirement", [], ["term"],
                    "", "", build_operation_instruction("Generate", model))
                self.assertLess(len(system), 5500, f"{preset}/{model}")


class UiPromptTests(unittest.TestCase):
    """prompt_studio_ui cannot be imported here, so the text is inspected directly."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "scripts/prompt_studio_ui.py").read_text(encoding="utf-8")

    def test_prompt_page_exposes_every_diversity_control(self):
        self.assertIn('with gr.Tab("提示词"', self.source)
        for key in ("recipe_enabled", "recipe_dimensions", "retry_limit",
                    "similarity_threshold", "memory_enabled", "batch_temperature_floor",
                    "source_fold_duplicates"):
            self.assertIn(f'elem_id="llm_prompt_studio_diversity_{key}"', self.source, key)
        for handler in ("_save_diversity_settings", "_reset_diversity_settings",
                        "_prompt_preview_text", "_diversity_report_markdown"):
            self.assertIn(f"{handler},", self.source, handler)

    def test_prompt_page_shows_the_assembled_prompt(self):
        self.assertIn('elem_id="llm_prompt_studio_prompt_preview"', self.source)
        self.assertIn("show_copy_button=True", self.source)

    def test_dead_technical_token_helper_is_removed(self):
        self.assertNotIn("_preserves_immutable_technical_tokens", self.source)

    def test_details_role_no_longer_orders_a_style(self):
        self.assertNotIn("Japanese light-novel illustration", self.source)
        self.assertNotIn("cinematic storytelling", self.source)
        self.assertIn("Never name a style, medium, studio, artist or quality level.", self.source)

    def test_inline_contract_keeps_token_exclusion_and_length_budget(self):
        self.assertIn("Never echo the source Prompt twice.", self.source)
        self.assertIn("excluded from semantic variation", self.source)
        self.assertIn("stay inside the output profile's length budget", self.source)

    def test_batch_contract_keeps_recipe_and_budget(self):
        self.assertIn("Make the recipe items visible", self.source)
        self.assertIn("Follow the selected output profile's format and length budget", self.source)


if __name__ == "__main__":
    unittest.main()
