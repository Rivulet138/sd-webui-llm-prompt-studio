"""Regression tests for technical-token order and scene clause boundaries."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from prompt_studio_core import compile_prompt_constraints, prompt_constraint_conflicts


class TechnicalTokenBoundaryTests(unittest.TestCase):
    def test_mixed_tokens_follow_source_order_and_keep_original_spelling(self):
        source = (
            "first_trigger, <lyco:Lyco_3:0.7>, second_2\uff0c"
            "<lora:Style_42:0.8>, <hypernet:Net_7:0.4>, first_trigger"
        )
        self.assertEqual(compile_prompt_constraints(source)["technical_tokens"], [
            "<lyco:Lyco_3:0.7>", "<lora:Style_42:0.8>",
            "<hypernet:Net_7:0.4>",
        ])

    def test_token_only_inside_loader_tag_is_not_a_separate_trigger(self):
        source = "<lora:some_trigger_1:0.8>, independent_2"
        self.assertEqual(compile_prompt_constraints(source)["technical_tokens"],
                         ["<lora:some_trigger_1:0.8>"])

    def test_comma_separates_negation_from_later_scene_marker(self):
        for source in ("no indoor scene, indoor", "no indoor scene\uff0cindoor",
                       "\u4e0d\u8981\u5ba4\u5185\u573a\u666f,\u5ba4\u5185",
                       "\u4e0d\u8981\u5ba4\u5185\u573a\u666f\uff0c\u5ba4\u5185"):
            with self.subTest(source=source):
                constraints = compile_prompt_constraints(source)
                self.assertTrue(constraints["scene"]["indoor"])
                self.assertTrue(constraints["forbidden_scene"]["indoor"])
                self.assertIn("required/forbidden:indoor", prompt_constraint_conflicts(source))

    def test_negation_remains_local_within_a_single_clause(self):
        for source in ("no indoor scene", "avoid indoor", "\u4e0d\u8981\u5ba4\u5185\u573a\u666f", "\u7981\u6b62\u5ba4\u5185"):
            with self.subTest(source=source):
                constraints = compile_prompt_constraints(source)
                self.assertFalse(constraints["scene"]["indoor"])
                self.assertTrue(constraints["forbidden_scene"]["indoor"])

    def test_comma_keeps_independent_scene_markers(self):
        for source in ("indoor, night", "indoor\uff0cnight", "\u5ba4\u5185,\u591c\u665a", "\u5ba4\u5185\uff0c\u591c\u665a"):
            with self.subTest(source=source):
                constraints = compile_prompt_constraints(source)
                self.assertTrue(constraints["scene"]["indoor"])
                self.assertTrue(constraints["scene"]["night"])
                self.assertFalse(prompt_constraint_conflicts(source))

    def test_negation_applies_to_coordinated_scenes_in_one_clause(self):
        for source in ("no indoor or outdoor", "\u4e0d\u8981\u5ba4\u5185\u6216\u5ba4\u5916"):
            with self.subTest(source=source):
                constraints = compile_prompt_constraints(source)
                self.assertTrue(constraints["forbidden_scene"]["indoor"])
                self.assertTrue(constraints["forbidden_scene"]["outdoor"])
                self.assertFalse(constraints["scene"]["indoor"])
                self.assertFalse(constraints["scene"]["outdoor"])


if __name__ == "__main__":
    unittest.main()
