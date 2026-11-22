"""Model presets own format; shared policy only routes requirements and references."""
import unittest

from test_queue_regressions import ROOT
from prompt_studio_core import (
    MODEL_PRESET_GUIDANCE,
    MODEL_PRESET_FORMATS,
    OPERATION_INSTRUCTIONS,
    PRESETS,
    PROMPT_POLICY_V3,
    build_operation_instruction,
    build_system_prompt,
)

POLICY_MARKERS = (
    "TASK AND FORMAT.",
    "<model_preset> defines output shape, order, vocabulary",
    "USER AUTHORITY.",
    "Treat both as equally authoritative",
    "REFERENCES.",
    "BATCH.",
    "Preserve explicit facts, counts, negations, spatial relations, weights and technical tokens",
)



class PolicyContractTests(unittest.TestCase):
    def test_policy_routes_authority_without_repeating_profile_content(self):
        for marker in POLICY_MARKERS:
            self.assertIn(marker, PROMPT_POLICY_V3, marker)
        self.assertNotIn("Authority order: this policy", PROMPT_POLICY_V3)
        self.assertNotIn("priority=", PROMPT_POLICY_V3)
        self.assertLess(len(PROMPT_POLICY_V3), 2000)

    def test_policy_is_the_first_block_of_every_system_prompt(self):
        for preset in PRESETS:
            system = build_system_prompt(
                preset, "Auto / checkpoint default", "SFW", "", "", [], None, "", "", "")
            self.assertTrue(system.startswith(PROMPT_POLICY_V3), preset)


class OutputProfileTests(unittest.TestCase):
    def test_each_profile_declares_format_without_forced_length(self):
        for name, text in PRESETS.items():
            self.assertIn("Output profile:", text, name)
            self.assertNotRegex(text, r"\b\d+\s*-\s*\d+\s*(?:tags|words)\b", name)

    def test_output_profiles_do_not_duplicate_reference_routing(self):
        for name, text in PRESETS.items():
            self.assertNotIn("INERT REFERENCE DATA", text, name)
            self.assertNotIn("<user_requirement>", text, name)

    def test_profiles_stay_compact(self):
        for name, text in PRESETS.items():
            self.assertLess(len(text), 600, name)


class OperationContractTests(unittest.TestCase):
    def test_every_action_covers_every_model(self):
        for action, instruction in OPERATION_INSTRUCTIONS.items():
            for model in MODEL_PRESET_GUIDANCE:
                self.assertTrue(instruction, f"{action}/{model}")
                self.assertEqual(build_operation_instruction(action, model), instruction)

    def test_instructions_stay_compact(self):
        for action, instruction in OPERATION_INSTRUCTIONS.items():
            self.assertLess(len(instruction), 420, action)

    def test_unknown_model_falls_back_to_the_shared_instruction(self):
        self.assertEqual(
            build_operation_instruction("Convert", "unknown-model"),
            OPERATION_INSTRUCTIONS["Convert"],
        )

    def test_quality_guidance_is_merged_into_every_model_preset(self):
        for model, preset in MODEL_PRESET_FORMATS.items():
            self.assertEqual(MODEL_PRESET_GUIDANCE[model], PRESETS[preset])
            self.assertIn("supplied", PRESETS[preset].lower(), model)
            self.assertTrue(any(term in PRESETS[preset].lower()
                                for term in ("quality", "score", "anchors")), model)


class PromptBudgetTests(unittest.TestCase):
    def test_system_prompt_stays_within_budget(self):
        for preset in PRESETS:
            for model in MODEL_PRESET_GUIDANCE:
                system = build_system_prompt(
                    preset, model, "NSFW", "injection", "requirement", [], ["term"],
                    "", "", build_operation_instruction("Generate", model))
                self.assertLess(len(system), 5500, f"{preset}/{model}")


class UiPromptTests(unittest.TestCase):
    """prompt_studio_ui cannot be imported here, so the text is inspected directly."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "scripts/prompt_studio_ui.py").read_text(encoding="utf-8")

    def test_prompt_page_keeps_diversity_runtime_internal(self):
        self.assertIn('with gr.Tab("提示词"', self.source)
        for key in ("recipe_enabled", "recipe_dimensions", "retry_limit",
                    "similarity_threshold", "memory_enabled", "batch_temperature_floor",
                    "source_fold_duplicates"):
            self.assertIn(f'"{key}"', self.source, key)
        self.assertIn("_run_llm_generation", self.source)
        self.assertIn("_sample_lexicon_terms", self.source)

    def test_prompt_page_does_not_reintroduce_technical_preview_controls(self):
        self.assertNotIn('elem_id="llm_prompt_studio_prompt_preview"', self.source)
        self.assertNotIn("show_copy_button=True", self.source)

    def test_dead_technical_token_helper_is_removed(self):
        self.assertNotIn("_preserves_immutable_technical_tokens", self.source)

    def test_details_role_no_longer_orders_a_style(self):
        self.assertNotIn("Japanese light-novel illustration", self.source)
        self.assertNotIn("cinematic storytelling", self.source)
        self.assertIn("Do not invent unsupported details", PROMPT_POLICY_V3)
        self.assertIn("Preserve explicit facts, counts, negations, spatial relations, weights and technical tokens", PROMPT_POLICY_V3)
        self.assertNotIn("TAG FIDELITY", PROMPT_POLICY_V3)

    def test_inline_contract_keeps_token_exclusion_and_conciseness(self):
        self.assertIn("Never echo the source Prompt twice.", self.source)
        self.assertIn("excluded from semantic variation", self.source)
        self.assertIn("one concise English expansion", self.source)

    def test_batch_contract_keeps_recipe_conditional(self):
        self.assertIn("Use a listed hint only when it naturally supports this one image", self.source)
        self.assertIn("drop that item without replacement instead of changing the fixed content", self.source)
        self.assertIn("Use only lenses allowed by the request", self.source)
        self.assertIn("Vary only dimensions the user leaves open", PROMPT_POLICY_V3)


if __name__ == "__main__":
    unittest.main()
