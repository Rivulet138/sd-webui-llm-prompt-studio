"""Regressions for preset priority, inline delta, and independent retry instructions."""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from test_queue_regressions import ui_functions
from test_prompt_generation_quality import quality_api
from prompt_studio_core import (
    build_system_prompt, build_user_message, build_operation_instruction,
    is_sfw_output, process_tags, regional_format, uses_layered_polish,
    KREA_ANIMA_POLISH_PROFILE, PRESETS,
)
from prompt_studio_generation import CandidateQualityError, run_llm_generation


LAYERED_PROMPT = """PART ONE: TAG ANCHORS
1girl, reading, red_hair, by_window

BREAK
Warm light frames a quiet reader in a green coat.

PART TWO: EXTREME LAYERED DETAIL
Composition & Pose:
She leans toward the open book; the window frames her hands.
Hair & Head Accessories:
Red hair catches soft light along the loose strands.
Face & Expression:
Her green eyes follow the printed page with a quiet smile.
Clothing & Details:
A green coat folds at her elbows, its brushed wool catching warm highlights.
Props & Floating Elements:
Her hands support a worn paper book with a leather cover.
Lighting & Color:
Warm window light crosses the green cloth and the pale paper.
Background:
A wooden desk fills the foreground, with soft shelves behind her.

PART THREE: REFINED + MASTER
SUBJECT
A red-haired woman in a green coat holds an open book beside a window.
MASTER DESCRIPTION
A red-haired woman reads beside a window, her green wool coat gathering at the elbows;
her hands support a leather-bound book as warm light crosses the pale paper and wooden desk."""


class PromptReviewFixTests(unittest.TestCase):
    def test_inline_delta_need_not_repeat_fixed_technical_tokens(self):
        api = quality_api()
        source = "FIXED PROMPT (IMMUTABLE):\n1girl, blue_eyes, <lora:foxStyle:0.8>"
        self.assertTrue(math.isfinite(api._prompt_quality_score(
            source, "reading, by_window, sunlight", "Danbooru Tags", output_contract="delta")))
        self.assertEqual(api._prompt_quality_score(
            source, "reading, by_window, sunlight", "Danbooru Tags"), float("-inf"))

    def test_layered_contract_cannot_bypass_the_preset_single_line_contract(self):
        api = quality_api()
        result = "PART ONE: TAG ANCHORS\n1girl, reading\n\nPART THREE: REFINED + MASTER\nA girl reads by a window."
        self.assertEqual(api._prompt_quality_score(
            "a girl reading", result, "Krea 2 Natural", output_contract="layered"), float("-inf"))
        self.assertEqual(api._prompt_quality_score(
            "a girl reading", result, "Krea 2 Natural"), float("-inf"))

    def test_weighted_alias_duplicates_are_rejected(self):
        self.assertEqual(quality_api()._prompt_quality_score(
            "", "(1girl:1.1), (sole_female:1.2)", "Danbooru Tags"), float("-inf"))

    def generation_api(self, candidate):
        calls = []
        quality = quality_api()
        def provider(*args, **kwargs):
            calls.append(args)
            return candidate[min(len(calls) - 1, len(candidate) - 1)] if isinstance(candidate, list) else candidate
        def generate(*args, **kwargs):
            return run_llm_generation(
                *args, **kwargs, credentials=SimpleNamespace(resolve=lambda *args: ""),
                call_llm=provider, build_user_message=build_user_message, coerce_bool=bool,
                fallback_request_kwargs=lambda connection: {},
                worst_content_similarity=lambda *args: 0.0, max_response_retries=1)
        connection = dict(provider="OpenAI Compatible", endpoint="http://localhost/v1", model="fake",
                          temperature=0.0, timeout=90, max_tokens=1000, send_temperature=False, retry_count=0)
        workflow = dict(preset="Danbooru Tags", base_model="Auto / checkpoint default", safety="SFW")
        api = ui_functions(
            "_coerce_bool", "_finalize_generated_prompt", "_generate", "_expand_or_polish", "_inline_generate",
            "_canonical_preset", "_canonical_base_model", "_resolve_preset_model", "_aligned_preset",
            constants=("INLINE_DELTA_DIRECTIVE", "PRESET_UI_CHOICES", "MODEL_UI_CHOICES",
                       "PRESET_VALUE_ALIASES", "BASE_MODEL_VALUE_ALIASES", "PRESET_MODEL_OVERRIDES",
                       "MODEL_PRESET_ALIGNMENT"),
            _canonical_output_mode=lambda value: value,
            _canonical_provider=lambda value: value, _canonical_action=lambda value: value,
            _static_prompt_reference=lambda *args, **kwargs: [],
            _recent_diverse_outputs=lambda *args: [],
            _diversity_exclusion_terms_from_outputs=lambda *args: [],
            _diversity_settings=lambda: dict(retry_limit=0, similarity_threshold=0.82, batch_temperature_floor=False),
            _connection_settings=lambda *args: connection,
            _workflow_settings=lambda: workflow, _save_workflow_values=Mock(),
            _lexicon_recipe=lambda **kwargs: [], _independent_creative_directive=lambda **kwargs: "",
            _immutable_technical_tokens=quality._immutable_technical_tokens,
            _prompt_quality_score=quality._prompt_quality_score,
            _run_llm_generation=generate, CandidateQualityError=CandidateQualityError,
            build_system_prompt=build_system_prompt, build_operation_instruction=build_operation_instruction,
            uses_layered_polish=uses_layered_polish,
            is_sfw_output=is_sfw_output, process_tags=process_tags,
            canonicalize_booru_tags=quality._prompt_quality_score.__globals__["canonicalize_booru_tags"],
            regional_format=regional_format, _MIN_INDEPENDENT_BATCH_TEMPERATURE=0.8,
            _MAX_CONTENT_SIMILARITY=0.82, _safe_error=str, LLMRequestError=RuntimeError,
            _BATCH_CONTEXT=SimpleNamespace(), DB=Mock())
        return api, calls

    def test_actual_inline_generation_accepts_delta_on_first_request(self):
        api, calls = self.generation_api("reading, by_window, sunlight")
        result, system, status = api._inline_generate(
            "reading by a window", "1girl, blue_eyes, <lora:foxStyle:0.8>",
            "Danbooru Tags", "", "Auto / checkpoint default", "SFW", "", "",
            False, "", False, False, 0, "Plain Prompt", 1, 0, False)
        self.assertEqual(result, "reading, by_window, sunlight", status)
        self.assertEqual(len(calls), 1)
        self.assertIn("return ONLY one concise English expansion", system)

    def test_inline_retry_keeps_the_delta_contract(self):
        api, calls = self.generation_api(["reading; sunlight", "reading, sunlight"])
        result, system, status = api._inline_generate(
            "reading", "1girl, <lora:foxStyle:0.8>", "Danbooru Tags", "",
            "Auto / checkpoint default", "SFW", "", "",
            False, "", False, False, 0, "Plain Prompt", 1, 0, False)
        self.assertEqual(result, "reading, sunlight", status)
        self.assertEqual(len(calls), 2)
        self.assertIn("must not be repeated", calls[-1][4])

    def test_polish_rejects_meta_output_and_unsafe_content(self):
        for candidate in ("Let me explain.\nA girl reads.", "PART ONE: TAG ANCHORS\nnude"):
            with self.subTest(candidate=candidate):
                api, calls = self.generation_api(candidate)
                result, status = api._expand_or_polish(
                    "a girl reading", "Polish", "Natural Language", "", "Krea 2", "SFW", "", "",
                    "OpenAI Compatible", "http://localhost/v1", "fake", "", 0.0, 90, 1000, False)
                self.assertEqual(result, "")
                self.assertIn("失败", status)

    def test_normal_generation_still_requires_complete_single_line_output(self):
        cases = (
            ("FIXED PROMPT (IMMUTABLE):\n<lora:foxStyle:0.8>", "reading, sunlight"),
            ("a girl reading", "reading\nsunlight"),
        )
        for source, candidate in cases:
            with self.subTest(source=source):
                api, calls = self.generation_api(candidate)
                result, system, status = api._generate(
                    "", source, "Danbooru Tags", "", "Auto / checkpoint default", "SFW", "", "",
                    "OpenAI Compatible", "http://localhost/v1", "fake", "", 0.0, 90, 1000, False,
                    False, "", False, False, 0, "Plain Prompt", 1, 0, False)
                self.assertEqual(result, "")
                self.assertIn("失败", status)
                self.assertEqual(len(calls), 2)

    def test_transforms_reject_multiline_payloads(self):
        for action, model in (("Expand", "Krea 2"), ("Polish", "Flux"), ("Convert", "Anima")):
            with self.subTest(action=action, model=model):
                api, calls = self.generation_api("A girl reads.\nSunlight falls through a window.")
                result, status = api._expand_or_polish(
                    "a girl reading", action, "Natural Language", "", model, "SFW", "", "",
                    "OpenAI Compatible", "http://localhost/v1", "fake", "", 0.0, 90, 1000, False)
                self.assertEqual(result, "")
                self.assertIn("失败", status)
                self.assertEqual(len(calls), 2)

    def test_krea_and_anima_polish_reject_missing_part(self):
        candidate = "PART ONE: TAG ANCHORS\n1girl, reading\n\nPART THREE: REFINED + MASTER\nA girl reads."
        for model in ("Krea 2", "Anima"):
            with self.subTest(model=model):
                api, calls = self.generation_api(candidate)
                result, status = api._expand_or_polish(
                    "a girl reading", "Polish", "Natural Language", "", model, "SFW", "", "",
                    "OpenAI Compatible", "http://localhost/v1", "fake", "", 0.0, 90, 1000, False)
                self.assertEqual(result, "", status)
                self.assertEqual(len(calls), 2)

    def test_polish_uses_model_preset_operation_and_shared_safety_fields(self):
        cases = (("Krea 2", "Krea 2 Natural", LAYERED_PROMPT),
                 ("Anima", "Anima Tags", LAYERED_PROMPT),
                 ("Flux", "Natural Language", "A girl reads beside a window."))
        for model, preset, candidate in cases:
            for safety in ("SFW", "NSFW"):
                with self.subTest(model=model, safety=safety):
                    api, calls = self.generation_api(candidate)
                    result, status = api._expand_or_polish(
                        "a girl reading", "Polish", "Natural Language", PRESETS[preset] + "\n\nUse warm colors.", model,
                        safety, "LOCAL_NSFW_RULE", "Include a book.",
                        "OpenAI Compatible", "http://localhost/v1", "fake", "", 0.0, 90, 1000, False,
                        remove_bad=False)
                    self.assertEqual(result, candidate, status)
                    self.assertEqual(len(calls), 1)
                    system = calls[0][4]
                    if uses_layered_polish("Polish", model):
                        self.assertIn(KREA_ANIMA_POLISH_PROFILE, system)
                        self.assertNotIn(PRESETS[preset], system)
                    else:
                        self.assertIn(PRESETS[preset], system)
                    self.assertIn(build_operation_instruction("Polish", model), system)
                    self.assertIn("Safety mode: " + safety, system)
                    self.assertEqual("LOCAL_NSFW_RULE" in system, safety == "NSFW")
                    self.assertIn("Use warm colors.", system)
                    self.assertIn("Include a book.", system)

    def test_layered_quality_requires_exact_structure_and_nonempty_sections(self):
        api = quality_api()
        malformed = [
            LAYERED_PROMPT.replace("PART TWO: EXTREME LAYERED DETAIL", "DETAIL"),
            LAYERED_PROMPT.replace("BREAK", ""),
            "PART ONE: TAG ANCHORS\n\nBREAK\nMood.\nPART TWO: EXTREME LAYERED DETAIL\nDetail.\nPART THREE: REFINED + MASTER\nMaster.",
            "PART ONE: TAG ANCHORS\n1girl\nBREAK\n\nPART TWO: EXTREME LAYERED DETAIL\nDetail.\nPART THREE: REFINED + MASTER\nMaster.",
            "PART ONE: TAG ANCHORS\n1girl\nBREAK\nMood.\nPART TWO: EXTREME LAYERED DETAIL\n\nPART THREE: REFINED + MASTER\nMaster.",
            "PART ONE: TAG ANCHORS\n1girl\nBREAK\nMood.\nPART TWO: EXTREME LAYERED DETAIL\nDetail.\nPART THREE: REFINED + MASTER\n",
            "A girl reads.\nSunlight enters the window.",
        ]
        for model, preset in (("Krea 2", "Krea 2 Natural"), ("Anima", "Anima Tags")):
            with self.subTest(model=model):
                self.assertTrue(math.isfinite(api._prompt_quality_score(
                    "a girl reading", LAYERED_PROMPT, preset, model, output_contract="layered")))
                self.assertEqual(api._prompt_quality_score(
                    "a girl reading", LAYERED_PROMPT, preset, model), float("-inf"))
                self.assertEqual(api._prompt_quality_score(
                    "a girl reading", LAYERED_PROMPT, preset, model, output_contract="delta"), float("-inf"))
            for candidate in malformed:
                with self.subTest(model=model, candidate=candidate):
                    self.assertEqual(api._prompt_quality_score(
                        "a girl reading", candidate, preset, model, output_contract="layered"), float("-inf"))

    def test_layered_finalizer_preserves_text_and_shared_validation(self):
        for preset in ("Krea 2 Natural", "Anima Tags"):
            api, _ = self.generation_api(LAYERED_PROMPT)
            namespace = api._finalize_generated_prompt.__globals__
            namespace["process_tags"] = Mock(side_effect=AssertionError("tag cleanup used"))
            namespace["canonicalize_booru_tags"] = Mock(side_effect=AssertionError("tag normalization used"))
            with self.subTest(preset=preset):
                self.assertEqual(api._finalize_generated_prompt(
                    LAYERED_PROMPT, preset, "SFW", True, "reading", True, True, 1,
                    output_contract="layered"), LAYERED_PROMPT)
                wrapper = Mock(return_value="wrapped")
                namespace["regional_format"] = wrapper
                self.assertEqual(api._finalize_generated_prompt(
                    LAYERED_PROMPT, preset, "SFW", structured_mode="Regional JSON",
                    region_count=2, output_contract="layered"), "wrapped")
                wrapper.assert_called_once_with(LAYERED_PROMPT, "Regional JSON", 2)
                for unsafe in ("Let me explain.", "daylight, midnight", "indoors, outdoors", "nude"):
                    with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                        api._finalize_generated_prompt(
                            LAYERED_PROMPT + "\n" + unsafe, preset, "SFW", output_contract="layered")
                self.assertEqual(api._finalize_generated_prompt(
                    LAYERED_PROMPT + "\nnude", preset, "NSFW", output_contract="layered"),
                    LAYERED_PROMPT + "\nnude")

    def test_layered_keeps_fixed_technical_token_checks(self):
        api = quality_api()
        source = "FIXED PROMPT (IMMUTABLE):\nfox_maid, <lora:foxStyle:0.8>"
        for model, preset in (("Krea 2", "Krea 2 Natural"), ("Anima", "Anima Tags")):
            for suffix in ("", "fox_maid", "<lora:foxStyle:0.8>"):
                with self.subTest(model=model, suffix=suffix):
                    self.assertEqual(api._prompt_quality_score(
                        source, LAYERED_PROMPT + suffix, preset, model, output_contract="layered"), float("-inf"))
            self.assertTrue(math.isfinite(api._prompt_quality_score(
                source, LAYERED_PROMPT + " fox_maid, <lora:foxStyle:0.8>", preset, model,
                output_contract="layered")))

    def test_non_layered_entrypoints_reject_complete_layered_payload(self):
        for model in ("Krea 2", "Anima"):
            for action in ("Generate", "Convert", "Expand", "Inline"):
                with self.subTest(model=model, action=action):
                    api, calls = self.generation_api(LAYERED_PROMPT)
                    if action == "Generate":
                        result, _, status = api._generate(
                            "a girl reading", "", "Natural Language", "", model, "SFW", "", "",
                            "OpenAI Compatible", "http://localhost/v1", "fake", "", 0.0, 90, 1000, False,
                            False, "", False, False, 0, "Plain Prompt", 1, 0, False)
                    elif action == "Inline":
                        result, _, status = api._inline_generate(
                            "reading", "1girl", "Natural Language", "", model, "SFW", "", "",
                            False, "", False, False, 0, "Plain Prompt", 1, 0, False)
                    else:
                        result, status = api._expand_or_polish(
                            "a girl reading", action, "Natural Language", "", model, "SFW", "", "",
                            "OpenAI Compatible", "http://localhost/v1", "fake", "", 0.0, 90, 1000, False)
                    self.assertEqual(result, "", status)
                    self.assertEqual(len(calls), 2)

    def test_layered_polish_retries_keep_operation_profile_and_shared_fields(self):
        directive = "Vary the lighting."
        for model in ("Krea 2", "Anima"):
            for reason in ("response", "quality", "diversity"):
                with self.subTest(model=model, reason=reason):
                    api, _ = self.generation_api(LAYERED_PROMPT)
                    namespace = api._expand_or_polish.__globals__
                    builder = Mock(wraps=build_system_prompt)
                    namespace["build_system_prompt"] = builder
                    namespace["_remember_diverse_output"] = Mock()
                    def run(*args, **kwargs):
                        retry = args[6](reason, 1)
                        self.assertTrue(math.isfinite(kwargs["candidate_quality"](args[0], LAYERED_PROMPT)))
                        return args[7](LAYERED_PROMPT), retry, 0
                    namespace["_run_llm_generation"] = run
                    result, status = api._expand_or_polish(
                        "a girl reading", "Polish", "Natural Language", "Use warm colors.", model,
                        "NSFW", "LOCAL_NSFW_RULE", "Include a book.",
                        "OpenAI Compatible", "http://localhost/v1", "fake", "", 0.0, 90, 1000, False,
                        batch_directive=directive)
                    self.assertEqual(result, LAYERED_PROMPT, status)
                    self.assertEqual(builder.call_count, 2)
                    for call in builder.call_args_list:
                        self.assertEqual(call.kwargs["operation"], "Polish")
                        self.assertEqual(call.kwargs["batch_directive"], directive)
                        self.assertEqual(call.args[2:5], ("NSFW", "LOCAL_NSFW_RULE", "Include a book."))
                        self.assertIn(build_operation_instruction("Polish", model), call.kwargs["operation_instruction"])
                    self.assertEqual(builder.call_args_list[0].kwargs["retry_instruction"], "")
                    self.assertIn(reason.upper() + " RETRY", builder.call_args_list[1].kwargs["retry_instruction"])

    def test_all_retry_reasons_keep_batch_and_pass_correction_separately(self):
        directive = "BATCH_ORIGINAL " + "x" * 4500
        for transform in (False, True):
            for reason in ("response", "quality", "diversity"):
                with self.subTest(transform=transform, reason=reason):
                    api, _ = self.generation_api("1girl, reading")
                    namespace = api._generate.__globals__
                    builder = Mock(return_value="system")
                    namespace["build_system_prompt"] = builder
                    def run(*args, **kwargs):
                        return "1girl, reading", args[6](reason, 1), 0
                    namespace["_run_llm_generation"] = run
                    namespace["_remember_diverse_output"] = Mock()
                    if transform:
                        result, status = api._expand_or_polish(
                            "a girl reading", "Polish", "Danbooru Tags", "", "Auto / checkpoint default",
                            "SFW", "", "", "OpenAI Compatible", "http://localhost/v1", "fake", "",
                            0.0, 90, 1000, False, batch_directive=directive)
                    else:
                        result, _, status = api._generate(
                            "a girl reading", "", "Danbooru Tags", "", "Auto / checkpoint default",
                            "SFW", "", "", "OpenAI Compatible", "http://localhost/v1", "fake", "",
                            0.0, 90, 1000, False, False, "", False, False, 0, "Plain Prompt", 1, 0, False,
                            batch_directive=directive)
                    self.assertEqual(result, "1girl, reading", status)
                    initial, retry = builder.call_args_list
                    initial_batch = initial.kwargs.get("batch_directive", initial.args[8] if len(initial.args) > 8 else "")
                    retry_batch = retry.kwargs.get("batch_directive", retry.args[8] if len(retry.args) > 8 else "")
                    self.assertEqual(initial_batch, directive)
                    self.assertEqual(retry_batch, directive)
                    self.assertIn(reason.upper() + " RETRY", retry.kwargs["retry_instruction"])


if __name__ == "__main__":
    unittest.main()
