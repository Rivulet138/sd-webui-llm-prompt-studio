"""The Krea/Anima layered format is a model-specific Polish operation preset."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prompt_studio_core import (
    PRESETS, build_operation_instruction, build_system_prompt,
)


class LayeredPolishProfileTests(unittest.TestCase):
    def system(self, model="Krea 2", **changes):
        values = dict(preset="Krea 2 Natural" if model == "Krea 2" else "Anima Tags",
            base_model=model, safety="SFW", nsfw_injection="", user_instruction="", examples=[],
            operation="Polish", operation_instruction=build_operation_instruction("Polish", model))
        values.update(changes)
        return build_system_prompt(**values)

    def test_polish_restores_all_three_parts_and_detailed_subsections(self):
        for model in ("Krea 2", "Anima"):
            with self.subTest(model=model):
                system = self.system(model)
                for marker in ("PART ONE: TAG ANCHORS", "BREAK", "PART TWO: EXTREME LAYERED DETAIL",
                    "Composition & Pose:", "Hair & Head Accessories:", "Face & Expression:",
                    "Clothing & Details:", "Props & Floating Elements:", "Lighting & Color:",
                    "Background:", "PART THREE: REFINED + MASTER", "SUBJECT", "MASTER DESCRIPTION"):
                    self.assertIn(marker, system)
                self.assertEqual(system.count("Output profile:"), 1)

    def test_polish_does_not_receive_the_normal_format_or_budget(self):
        for model in ("Krea 2", "Anima"):
            with self.subTest(model=model):
                system = self.system(model)
                self.assertNotIn("70-160 words", system)
                self.assertNotIn("Return one comma-separated Danbooru-first prompt, 6-50 tags", system)
                self.assertNotIn("One compact natural-language description in profile order", system)
                self.assertIn(model, system.split("<model_profile>\n", 1)[1].split("</model_profile>", 1)[0])

    def test_saved_normal_profile_cannot_override_polish_structure(self):
        for model in ("Krea 2", "Anima"):
            with self.subTest(model=model):
                system = self.system(model, system_override=PRESETS["Krea 2 Natural"] +
                    "\n\nKeep the kettle visibly dented.", user_instruction="Use a blue cover on the book.")
                self.assertIn("PART TWO: EXTREME LAYERED DETAIL", system)
                self.assertNotIn("70-160 words", system)
                self.assertIn("Keep the kettle visibly dented.", system)
                self.assertIn("Use a blue cover on the book.", system)
                self.assertNotIn('priority="low"', system)

    def test_shared_safety_and_nsfw_injection_are_preserved(self):
        for model in ("Krea 2", "Anima"):
            for mode in ("SFW", "NSFW"):
                with self.subTest(model=model, mode=mode):
                    system = self.system(model, safety=mode, nsfw_injection="LOCAL_MODE_MARKER")
                    self.assertIn("Safety mode: " + mode, system)
                    self.assertEqual("LOCAL_MODE_MARKER" in system, mode == "NSFW")

    def test_retries_keep_the_operation_profile_and_independent_correction(self):
        for model in ("Krea 2", "Anima"):
            for reason in ("QUALITY", "DIVERSITY", "RESPONSE"):
                with self.subTest(model=model, reason=reason):
                    system = self.system(model, batch_directive="BATCH_CONTEXT " + "x" * 4500,
                                         retry_instruction=reason + " RETRY: preserve all three parts.")
                    self.assertIn("BATCH_CONTEXT", system)
                    self.assertIn(reason + " RETRY", system)
                    self.assertIn("PART THREE: REFINED + MASTER", system)
                    self.assertNotIn("70-160 words", system)

    def test_other_operations_and_models_keep_their_normal_output_profile(self):
        for model, preset in (("Krea 2", "Krea 2 Natural"), ("Anima", "Anima Tags"),
                              ("Flux", "Natural Language")):
            for operation in ("", "Generate", "Convert", "Expand", "Polish"):
                if operation == "Polish" and model in {"Krea 2", "Anima"}:
                    continue
                with self.subTest(model=model, operation=operation):
                    system = self.system(model, preset=preset, operation=operation,
                        operation_instruction=build_operation_instruction(operation or "Generate", model))
                    self.assertIn(PRESETS[preset], system)
                    self.assertNotIn("PART ONE: TAG ANCHORS", system)

    def test_polish_does_not_force_english_or_discard_supplied_weights_and_style(self):
        system = self.system(user_instruction="Write in Chinese and keep the supplied watercolor style.")
        self.assertNotIn("Use long but clear English sentences", system)
        self.assertNotIn("Never name a style, medium, studio, artist or quality level", system)
        self.assertNotIn("Do not use weight syntax", system)
        self.assertIn("Preserve supplied weights and technical tokens", system)

    def test_lowercase_tags_rule_exempts_supplied_case_sensitive_anchors(self):
        anchors = "<lora:MyStyleXL:0.8>, TriggerABC, (RedHair:1.35)"
        for model in ("Krea 2", "Anima"):
            with self.subTest(model=model):
                system = self.system(model, user_instruction="Preserve exactly: " + anchors)
                self.assertNotIn("Tags must be lowercase.", system)
                self.assertIn("with their original spelling and case", system)
                payload = system.split('<user_requirement encoding="json">\n', 1)[1].split('\n</user_requirement>', 1)[0]
                self.assertEqual(json.loads(payload)["requirement"], "Preserve exactly: " + anchors)


if __name__ == "__main__":
    unittest.main()
