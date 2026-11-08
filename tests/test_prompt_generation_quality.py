"""Regression tests for LLM candidate quality gates and reference examples."""
import math
import re
import unittest

from test_queue_regressions import ROOT, ui_functions
from prompt_studio_core import build_system_prompt, canonicalize_booru_tags
from prompt_studio_generation import CandidateQualityError, run_llm_generation


def quality_api():
    return ui_functions(
        "_immutable_technical_tokens", "_prompt_quality_score",
        _LORA_TOKEN_RE=re.compile(r"<lora:[^>]+>", re.IGNORECASE),
        _TECHNICAL_TOKEN_RES=(
            re.compile(r"<lora:[^>]+>", re.IGNORECASE),
            re.compile(r"<lyco:[^>]+>", re.IGNORECASE),
            re.compile(r"<hypernet:[^>]+>", re.IGNORECASE),
        ),
        _canonical_preset=lambda value: str(value or "").strip(),
        canonicalize_booru_tags=lambda text: canonicalize_booru_tags(
            text, vocabulary={
                "1girl": "1girl", "sole_female": "1girl",
                "red_hair": "red_hair", "red hair": "red_hair",
            }
        ),
    )


class CandidateQualityTests(unittest.TestCase):
    def test_inline_fixed_natural_language_can_be_rephrased(self):
        api = quality_api()
        score = api._prompt_quality_score(
            "FIXED PROMPT (IMMUTABLE):\na girl reading beside a window",
            "1girl, reading, by window, soft daylight",
            "Danbooru Tags",
        )
        self.assertNotEqual(score, float("-inf"))

    def test_missing_lora_and_compact_trigger_are_rejected(self):
        api = quality_api()
        source = "FIXED PROMPT (IMMUTABLE):\nfox_maid, <lora:foxStyle:0.8>"
        self.assertEqual(api._prompt_quality_score(source, "fox_maid", "Danbooru Tags"), float("-inf"))
        self.assertNotEqual(
            api._prompt_quality_score(source, "fox_maid, <lora:foxStyle:0.8>", "Danbooru Tags"),
            float("-inf"),
        )

    def test_unknown_tags_are_a_soft_signal_not_a_hard_failure(self):
        api = quality_api()
        self.assertTrue(math.isfinite(api._prompt_quality_score("", "unknown custom concept", "Danbooru Tags")))

    def test_canonical_alias_duplicates_are_rejected_before_cleanup(self):
        api = quality_api()
        self.assertEqual(
            api._prompt_quality_score("", "1girl, sole_female", "Danbooru Tags"),
            float("-inf"),
        )
        self.assertEqual(
            api._prompt_quality_score("", "red hair, red_hair", "Danbooru Tags"),
            float("-inf"),
        )

    def test_examples_are_escaped_and_labeled_as_inert_reference(self):
        system = build_system_prompt(
            "Danbooru Tags", "Auto / checkpoint default", "SFW", "", "",
            [{"tags": "1girl, red_hair", "source": "<img>&"}],
        )
        self.assertIn('purpose="reference-only"', system)
        self.assertIn(r"\u003cimg\u003e\u0026", system)
        self.assertIn("cannot override policy", system)

    def test_empty_examples_do_not_add_example_section(self):
        system = build_system_prompt(
            "Danbooru Tags", "Auto / checkpoint default", "SFW", "", "", [],
        )
        self.assertNotIn("<format_examples", system)


class GenerationKernelQualityTests(unittest.TestCase):
    def invoke(self, candidates, quality, finalize=lambda value: value):
        calls = []

        class Credentials:
            @staticmethod
            def resolve(key, provider, endpoint):
                return key

        def call_llm(*args, **kwargs):
            calls.append(args[5])
            return candidates[min(len(calls) - 1, len(candidates) - 1)]

        result = run_llm_generation(
            "source", "provider", "endpoint", "model", "key", "system",
            lambda reason, attempt: f"retry:{reason}:{attempt}", finalize,
            {"retry_count": 0}, 0.5, 30, 200, True,
            credentials=Credentials, call_llm=call_llm,
            build_user_message=lambda value: value, coerce_bool=bool,
            fallback_request_kwargs=lambda connection: {},
            worst_content_similarity=lambda candidate, context, source: 0.0,
            candidate_quality=quality,
        )
        return result, calls

    def test_invalid_quality_candidate_retries_then_accepts_valid(self):
        (result, _system, _retries), calls = self.invoke(
            ["bad", "good"], lambda _source, candidate: float("-inf") if candidate == "bad" else 1.0,
        )
        self.assertEqual(result, "good")
        self.assertEqual(len(calls), 2)

    def test_all_invalid_candidates_raise_clear_error(self):
        with self.assertRaisesRegex(ValueError, "未通过质量校验"):
            self.invoke(["bad"], lambda _source, _candidate: float("-inf"))

    def test_raw_duplicate_is_rejected_before_finalizer_can_dedupe(self):
        finalized = []
        (result, _system, _retries), calls = self.invoke(
            ["1girl, sole_female", "1girl, red_hair"],
            lambda _source, candidate: (
                float("-inf") if candidate == "1girl, sole_female" else 1.0
            ),
            lambda value: finalized.append(value) or ", ".join(dict.fromkeys(value.split(", "))),
        )
        self.assertEqual(result, "1girl, red_hair")
        self.assertEqual(finalized, ["1girl, red_hair"])
        self.assertEqual(len(calls), 2)

    def test_finalizer_validation_error_retries_candidate(self):
        finalized = []

        def finalize(value):
            finalized.append(value)
            if value == "invalid":
                raise CandidateQualityError("invalid candidate")
            return value

        (result, _system, _retries), calls = self.invoke(
            ["invalid", "valid"], lambda _source, _candidate: 1.0, finalize,
        )
        self.assertEqual(result, "valid")
        self.assertEqual(finalized, ["invalid", "valid"])
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
