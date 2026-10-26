"""Regression tests for the provider-response finalization boundary."""
import json
from pathlib import Path
import sys
import unittest

TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(TEST_ROOT.parent / "scripts"))
from prompt_studio_core import is_sfw_output, process_tags, regional_format
from test_queue_regressions import ui_functions


class PromptFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.api = ui_functions(
            "_coerce_bool",
            "_finalize_generated_prompt",
            _canonical_preset=lambda value: str(value or "").strip(),
            _canonical_output_mode=lambda value: str(value or "").strip(),
            is_sfw_output=is_sfw_output,
            process_tags=process_tags,
            regional_format=regional_format,
        )

    def test_tag_output_is_cleaned_and_bounded(self):
        result = self.api._finalize_generated_prompt(
            "1girl, watermark, red_hair, red hair, blue_eyes",
            "Danbooru Tags",
            "SFW",
            True,
            "",
            False,
            True,
            3,
            "Plain Prompt",
            1,
        )
        self.assertEqual(result, "1girl, red hair, blue eyes")

    def test_string_booleans_are_parsed_instead_of_truthiness_checked(self):
        result = self.api._finalize_generated_prompt(
            "watermark, red_hair",
            "Danbooru Tags",
            "SFW",
            "false",
            "",
            "false",
            "false",
            0,
            "Plain Prompt",
            1,
        )
        self.assertEqual(result, "watermark, red_hair")

    def test_regional_json_is_applied_after_tag_processing(self):
        result = self.api._finalize_generated_prompt(
            "1girl, blue_eyes",
            "Danbooru Tags",
            "SFW",
            True,
            "",
            False,
            False,
            0,
            "Regional JSON",
            2,
        )
        payload = json.loads(result)
        self.assertEqual(payload["base_prompt"], "1girl, blue_eyes")
        self.assertEqual(len(payload["regions"]), 2)

    def test_sfw_and_meta_content_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "SFW"):
            self.api._finalize_generated_prompt(
                "1girl, nude",
                "Danbooru Tags",
                "SFW",
            )
        with self.assertRaisesRegex(ValueError, "Prompt"):
            self.api._finalize_generated_prompt(
                "Here is the corrected prompt: 1girl, blue_eyes",
                "Danbooru Tags",
                "NSFW",
            )

    def test_empty_result_after_cleanup_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Prompt"):
            self.api._finalize_generated_prompt(
                "watermark, logo",
                "Danbooru Tags",
                "SFW",
                True,
            )


if __name__ == "__main__":
    unittest.main()
