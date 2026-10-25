"""Tests for the prompt page: diversity controls, prompt preview, library report."""
import collections
import random
import tempfile
import threading
import unittest
import uuid
from collections.abc import Iterable
from pathlib import Path

from test_queue_regressions import ROOT, StudioDB, ui_functions
from prompt_studio_core import _cosine, _tokens, build_operation_instruction, build_system_prompt
from prompt_studio_diversity import diversity_report, format_report

CONSTANTS = (
    "_DIVERSITY_SETTINGS_KEY",
    "_DIVERSITY_SETTINGS_DEFAULTS",
    "_DIVERSITY_CONTROL_KEYS",
    "_MAX_CONTENT_SIMILARITY",
    "_MAX_DIVERSITY_RETRIES",
)

FUNCTIONS = (
    "_coerce_bool",
    "_diversity_int",
    "_diversity_float",
    "_save_diversity_settings",
    "_reset_diversity_settings",
    "_prompt_preview_text",
    "_diversity_report_markdown",
)

WORKFLOW = {
    "preset": "Krea 2 Natural",
    "base_model": "Krea 2",
    "safety": "SFW",
    "nsfw_injection": "",
    "user_instruction": "",
    "system_override": "",
}


class PromptPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = StudioDB(Path(self.tmp.name) / "prompt-page.db")
        self.api = ui_functions(
            *FUNCTIONS,
            constants=CONSTANTS,
            DB=self.db,
            _tokens=_tokens,
            _cosine=_cosine,
            Counter=collections.Counter,
            Iterable=Iterable,
            random=random,
            uuid=uuid,
            threading=threading,
            _safe_error=str,
            build_system_prompt=build_system_prompt,
            build_operation_instruction=build_operation_instruction,
            diversity_report=diversity_report,
            format_report=format_report,
            _workflow_settings=lambda: dict(WORKFLOW),
            _resolve_preset_model=lambda preset, model: (preset, model),
            _aligned_preset=lambda preset, model: (preset, False),
        )

    # -- diversity controls -------------------------------------------------
    def test_save_clamps_out_of_range_values(self):
        message = self.api._save_diversity_settings(
            "yes", 99, "many", 5.0, "no", "true", 0)
        self.assertIn("已保存", message)
        stored = self.db.get_setting(self.api._DIVERSITY_SETTINGS_KEY, {})
        self.assertEqual(stored["recipe_dimensions"], 6)
        self.assertEqual(stored["retry_limit"], 1)
        self.assertLessEqual(stored["similarity_threshold"], 0.95)
        self.assertIs(stored["recipe_enabled"], True)
        self.assertIs(stored["memory_enabled"], False)
        self.assertIs(stored["batch_temperature_floor"], True)
        self.assertIs(stored["source_fold_duplicates"], False)

    def test_save_reports_the_effective_values(self):
        message = self.api._save_diversity_settings(True, 4, 2, 0.6, True, False, True)
        self.assertIn("维度 4", message)
        self.assertIn("重试 2", message)
        self.assertIn("阈值 0.60", message)
        self.assertIn("自动折叠重复输入", message)

    def test_reset_writes_defaults_in_control_order(self):
        result = self.api._reset_diversity_settings()
        self.assertEqual(len(result), len(self.api._DIVERSITY_CONTROL_KEYS) + 1)
        *values, message = result
        self.assertEqual(list(values), [self.api._DIVERSITY_SETTINGS_DEFAULTS[key]
                                        for key in self.api._DIVERSITY_CONTROL_KEYS])
        self.assertIn("默认", message)
        stored = self.db.get_setting(self.api._DIVERSITY_SETTINGS_KEY, {})
        self.assertEqual(stored["recipe_dimensions"], 3)

    # -- system prompt preview ---------------------------------------------
    def test_preview_returns_the_assembled_policy_prompt(self):
        text = self.api._prompt_preview_text()
        self.assertTrue(text.startswith("PROMPT POLICY V3"), text[:60])
        self.assertIn("<output_profile>", text)
        self.assertIn("<operation_template>", text)
        self.assertNotIn("预演失败", text)

    # -- library report -----------------------------------------------------
    def test_report_uses_the_shared_metrics_and_stays_read_only(self):
        for prompt in (
            "a young girl stands in a wooden kitchen holding a spoon",
            "a knight crouches on a stone bridge under heavy rain",
        ):
            self.db.save_prompt(prompt)
        before = (Path(self.tmp.name) / "prompt-page.db").read_bytes()
        markdown = self.api._diversity_report_markdown(100)
        self.assertIn("缓存库多样性", markdown)
        self.assertIn("Vendi", markdown)
        self.assertEqual((Path(self.tmp.name) / "prompt-page.db").read_bytes(), before)

    def test_report_handles_an_empty_library(self):
        self.assertIn("空", self.api._diversity_report_markdown(50))

    def test_report_limit_is_clamped(self):
        self.db.save_prompt("a lone lighthouse on a cliff at dusk")
        self.assertIn("缓存库多样性", self.api._diversity_report_markdown(999999))


if __name__ == "__main__":
    unittest.main()
