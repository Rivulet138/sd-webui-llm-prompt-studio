"""Polish uses the selected model format and a faithful editing contract."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prompt_studio_core as core
from prompt_studio_core import (
    MODEL_PRESET_FORMATS, MODEL_PRESET_GUIDANCE, PRESETS, build_operation_instruction, build_system_prompt, build_user_message,
)


MODEL_PRESETS = tuple(MODEL_PRESET_FORMATS.items())


class FaithfulPolishProfileTests(unittest.TestCase):
    def system(self, model="Krea 2", **changes):
        values = dict(
            preset=dict(MODEL_PRESETS)[model], base_model=model, safety="SFW", nsfw_injection="",
            user_instruction="", examples=[], operation="Polish",
            operation_instruction=build_operation_instruction("Polish", model),
        )
        values.update(changes)
        return build_system_prompt(**values)

    def test_obsolete_layered_profile_symbols_are_removed(self):
        for name in ("KREA_ANIMA_POLISH_PROFILE", "LAYERED_POLISH_MODEL_GUIDANCE", "uses_layered_polish"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(core, name))

    def test_every_model_and_operation_uses_its_normal_output_profile(self):
        for model, preset in MODEL_PRESETS:
            for operation in ("", "Generate", "Convert", "Expand", "Polish"):
                with self.subTest(model=model, operation=operation):
                    system = self.system(
                        model, operation=operation,
                        operation_instruction=build_operation_instruction(operation or "Generate", model),
                    )
                    guidance = system.split("<model_preset>\n", 1)[1].split("</model_preset>", 1)[0]
                    self.assertEqual(guidance.strip(), PRESETS[preset])
                    self.assertEqual(guidance.strip(), MODEL_PRESET_GUIDANCE[model])
                    for marker in ("PART ONE:", "PART TWO:", "PART THREE:", "MASTER DESCRIPTION"):
                        self.assertNotIn(marker, system)
                    self.assertNotIn("three-part", system.lower())
                    self.assertEqual(system.count("Output profile:"), 1)

    def test_convert_and_polish_preserve_facts_and_follow_explicit_conflict_resolution(self):
        for model, _preset in MODEL_PRESETS:
            for operation in ("Convert", "Polish"):
                with self.subTest(model=model, operation=operation):
                    system = self.system(
                        model, operation=operation,
                        operation_instruction=build_operation_instruction(operation, model),
                    ).lower()
                    faithful = system.split("<faithful_edit_contract>\n", 1)[1].split(
                        "\n</faithful_edit_contract>", 1)[0]
                    self.assertIn("follow the user's explicit resolution", faithful)
                    self.assertIn("otherwise omit only the disputed detail", faithful)
                    self.assertIn("do not invent unsupported details", system)
                    self.assertIn("preserve explicit facts, counts, negations, spatial relations", system)
                    self.assertNotIn("<faithful_edit_contract>", faithful)

    def test_saved_normal_profile_keeps_only_additive_notes_and_user_requirements(self):
        for model, preset in MODEL_PRESETS:
            with self.subTest(model=model):
                system = self.system(
                    model, system_override=PRESETS[preset] + "\n\nKeep the kettle visibly dented.",
                    user_instruction="Keep the supplied blue book and plain background.",
                )
                self.assertIn(PRESETS[preset], system)
                self.assertEqual(system.count("Output profile:"), 1)
                self.assertIn("Keep the kettle visibly dented.", system)
                request = build_user_message("A reader.", "Keep the supplied blue book and plain background.")
                payload = json.loads(request.split("\n", 1)[1].rsplit("\n", 1)[0])
                self.assertEqual(payload["user_requirements"], "Keep the supplied blue book and plain background.")
                self.assertNotIn('priority="low"', system)

    def test_shared_safety_and_nsfw_injection_are_preserved(self):
        for model, _preset in MODEL_PRESETS:
            for mode in ("SFW", "NSFW"):
                with self.subTest(model=model, mode=mode):
                    system = self.system(model, safety=mode, nsfw_injection="LOCAL_MODE_MARKER")
                    self.assertIn("Safety mode: " + mode, system)
                    self.assertEqual("LOCAL_MODE_MARKER" in system, mode == "NSFW")

    def test_response_and_quality_retries_keep_normal_profiles_and_correction(self):
        for model, preset in MODEL_PRESETS:
            for reason in ("QUALITY", "RESPONSE"):
                with self.subTest(model=model, reason=reason):
                    correction = reason + " RETRY: preserve the source and return one valid prompt."
                    system = self.system(model, retry_instruction=correction)
                    self.assertIn(PRESETS[preset], system)
                    self.assertIn(MODEL_PRESET_GUIDANCE[model], system)
                    payload = system.split('<retry_instruction encoding="json">\n', 1)[1].split(
                        "\n</retry_instruction>", 1)[0]
                    self.assertEqual(json.loads(payload)["instruction"], correction)
                    self.assertNotIn("PART THREE:", system)

    def test_supplied_case_sensitive_anchors_and_style_requirements_are_not_rewritten(self):
        anchors = "<lora:MyStyleXL:0.8>, TriggerABC, (RedHair:1.35)"
        requirement = "Keep the supplied watercolor style and preserve exactly: " + anchors
        for model, _preset in MODEL_PRESETS:
            with self.subTest(model=model):
                system = self.system(model, user_instruction=requirement)
                request = build_user_message("A reader.", requirement)
                payload = request.split("\n", 1)[1].rsplit("\n", 1)[0]
                self.assertEqual(json.loads(payload)["user_requirements"], requirement)
                self.assertIn("technical tokens", system.lower())


if __name__ == "__main__":
    unittest.main()
