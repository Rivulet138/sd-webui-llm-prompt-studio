"""Regressions for faithful edits, inference settings, and shared validation."""
import json
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_queue_regressions import ui_functions
from test_prompt_generation_quality import quality_api
from prompt_studio_core import (
    MODEL_PRESET_FORMATS, PRESETS, build_operation_instruction, build_system_prompt, build_user_message,
    is_sfw_output, process_tags, regional_format,
)
from prompt_studio_generation import CandidateQualityError, run_llm_generation


LEGACY_LAYERED_PROMPT = """PART ONE: TAG ANCHORS
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

MODEL_OUTPUTS = (
    ("Krea 2", "Krea 2 Natural", "A girl reads beside a window."),
    ("Anima", "Anima Tags", "1girl, reading, by_window"),
    ("Flux", "Flux Natural", "A girl reads beside a window."),
)


class PromptReviewFixTests(unittest.TestCase):
    def generation_api(self, candidates):
        calls = []
        quality = quality_api()
        similarity = Mock(return_value=0.0)

        def provider(*args, **kwargs):
            calls.append(args)
            candidate = candidates[min(len(calls) - 1, len(candidates) - 1)] if isinstance(candidates, list) else candidates
            if isinstance(candidate, Exception):
                raise candidate
            return candidate

        def generate(*args, **kwargs):
            user_requirements = kwargs.pop("user_requirements", "")
            return run_llm_generation(
                *args, **kwargs, credentials=SimpleNamespace(resolve=lambda *args: ""),
                call_llm=provider,
                build_user_message=lambda request: build_user_message(request, user_requirements),
                coerce_bool=bool,
                fallback_request_kwargs=lambda connection: {},
                worst_content_similarity=similarity, max_response_retries=1,
            )

        connection = dict(
            provider="OpenAI Compatible", endpoint="http://localhost/v1", model="fake",
            temperature=0.0, timeout=90, max_tokens=1000, send_temperature=True, retry_count=0,
        )
        workflow = dict(preset="Danbooru Tags", base_model="Auto / checkpoint default", safety="SFW")
        api = ui_functions(
            "_coerce_bool", "_finalize_generated_prompt", "_generate", "_expand_or_polish", "_inline_generate",
            "_canonical_preset", "_canonical_base_model", "_resolve_preset_model",
            constants=("INLINE_DELTA_DIRECTIVE", "PRESET_UI_CHOICES", "MODEL_UI_CHOICES",
                       "PRESET_VALUE_ALIASES", "BASE_MODEL_VALUE_ALIASES", "PRESET_MODEL_OVERRIDES",
                       "MODEL_PRESET_SELECTIONS"),
            _canonical_output_mode=lambda value: value,
            _canonical_provider=lambda value: value, _canonical_action=lambda value: value,
            _static_prompt_reference=Mock(return_value=[]),
            _recent_diverse_outputs=Mock(return_value=[]),
            _diversity_exclusion_terms_from_outputs=Mock(return_value=[]),
            _remember_diverse_output=Mock(),
            _diversity_settings=Mock(return_value=dict(
                retry_limit=0, similarity_threshold=0.82, batch_temperature_floor=False)),
            _connection_settings=lambda *args: connection,
            _workflow_settings=lambda: workflow, _save_workflow_values=Mock(),
            _lexicon_recipe=Mock(return_value=[]), _independent_creative_directive=lambda **kwargs: "",
            _immutable_technical_tokens=quality._immutable_technical_tokens,
            _prompt_quality_score=quality._prompt_quality_score,
            _run_llm_generation=Mock(side_effect=generate), CandidateQualityError=CandidateQualityError,
            build_system_prompt=build_system_prompt, build_operation_instruction=build_operation_instruction,
            is_sfw_output=is_sfw_output, process_tags=process_tags,
            canonicalize_booru_tags=quality._prompt_quality_score.__globals__["canonicalize_booru_tags"],
            regional_format=regional_format, _MIN_INDEPENDENT_BATCH_TEMPERATURE=0.8,
            _MAX_CONTENT_SIMILARITY=0.82, _safe_error=str, LLMRequestError=RuntimeError,
            _BATCH_CONTEXT=SimpleNamespace(), DB=Mock(),
        )
        api.similarity = similarity
        api.connection = connection
        return api, calls

    def invoke(self, api, action="Generate", **changes):
        values = dict(
            preset="Danbooru Tags", system_override="", base_model="Auto / checkpoint default",
            safety="SFW", nsfw_injection="", user_instruction="", remove_bad=False,
            remove_terms="", shuffle=False, spaces=False, max_tags=0,
            structured_mode="Plain Prompt", region_count=1,
        )
        values.update(changes)
        if "base_model" in changes and "preset" not in changes:
            values["preset"] = MODEL_PRESET_FORMATS.get(changes["base_model"], values["preset"])
        if action == "Inline":
            return api._inline_generate(
                values.pop("request", "reading by a window"), values.pop("source", "1girl"),
                save_score=0, cache_result=False, **values,
            )
        connection = dict(
            provider="OpenAI Compatible", endpoint="http://localhost/v1", model="fake", api_key="",
            temperature=0.0, timeout=90, max_tokens=1000, send_temperature=True,
        )
        connection.update(values)
        if action == "Generate":
            source = connection.pop("source", "a girl reading")
            return api._generate(
                request=connection.pop("request", ""), source_tags=source,
                save_score=0, cache_result=False, **connection,
            )
        return api._expand_or_polish(
            source=connection.pop("source", "a girl reading"), action=action, **connection,
        )

    def test_inline_delta_need_not_repeat_fixed_technical_tokens(self):
        api = quality_api()
        source = "FIXED PROMPT (IMMUTABLE):\n1girl, blue_eyes, <lora:foxStyle:0.8>"
        self.assertTrue(math.isfinite(api._prompt_quality_score(
            source, "reading, by_window, sunlight", "Danbooru Tags", output_contract="delta")))
        self.assertEqual(api._prompt_quality_score(
            source, "reading, by_window, sunlight", "Danbooru Tags"), float("-inf"))

    def test_legacy_layered_contract_has_no_special_quality_admission(self):
        quality = quality_api()._prompt_quality_score
        for model, preset, valid in MODEL_OUTPUTS:
            for contract in ("complete", "layered", "delta"):
                with self.subTest(model=model, contract=contract):
                    self.assertEqual(quality(
                        "a girl reading", LEGACY_LAYERED_PROMPT, preset, model,
                        output_contract=contract), float("-inf"))
                    self.assertTrue(math.isfinite(quality(
                        "a girl reading", valid, preset, model, output_contract=contract)))

    def test_weighted_alias_duplicates_are_rejected_for_all_contracts(self):
        for contract in ("complete", "layered", "delta"):
            with self.subTest(contract=contract):
                self.assertEqual(quality_api()._prompt_quality_score(
                    "", "(1girl:1.1), (sole_female:1.2)", "Danbooru Tags",
                    output_contract=contract), float("-inf"))

    def test_inline_generation_accepts_delta_and_keeps_zero_temperature(self):
        api, calls = self.generation_api("reading, by_window, sunlight")
        result, system, status = self.invoke(
            api, "Inline", source="1girl, blue_eyes, <lora:foxStyle:0.8>")
        self.assertEqual(result, "reading, by_window, sunlight", status)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][6], 0.0)
        self.assertIn("return ONLY one concise English expansion", system)

    def test_inline_retry_keeps_delta_contract_and_zero_temperature(self):
        api, calls = self.generation_api(["reading; sunlight", "reading, sunlight"])
        result, _system, status = self.invoke(api, "Inline", source="1girl, <lora:foxStyle:0.8>")
        self.assertEqual(result, "reading, sunlight", status)
        self.assertEqual([call[6] for call in calls], [0.0, 0.0])
        self.assertIn("must not be repeated", calls[-1][4])

    def test_polish_uses_model_preset_and_shared_safety_fields(self):
        for model, preset, candidate in MODEL_OUTPUTS:
            for safety in ("SFW", "NSFW"):
                with self.subTest(model=model, safety=safety):
                    api, calls = self.generation_api(candidate)
                    result, status = self.invoke(
                        api, "Polish", preset=preset, base_model=model, safety=safety,
                        system_override=PRESETS[preset] + "\n\nKeep the supplied colors.",
                        nsfw_injection="LOCAL_NSFW_RULE", user_instruction="Keep the supplied book.",
                    )
                    self.assertEqual(result, candidate, status)
                    self.assertEqual(len(calls), 1)
                    system = calls[0][4]
                    self.assertIn(PRESETS[preset], system)
                    self.assertIn(build_operation_instruction("Polish", model), system)
                    self.assertIn("Safety mode: " + safety, system)
                    self.assertEqual("LOCAL_NSFW_RULE" in system, safety == "NSFW")
                    self.assertIn("Keep the supplied colors.", system)
                    payload = json.loads(calls[0][5].split("\n", 1)[1].rsplit("\n", 1)[0])
                    self.assertEqual(payload["user_requirements"], "Keep the supplied book.")
                    self.assertNotIn("PART ONE:", system)

    def test_convert_and_polish_never_read_lexicon_or_use_batch_diversity(self):
        for action in ("Convert", "Polish"):
            for model, _preset, candidate in MODEL_OUTPUTS:
                with self.subTest(action=action, model=model):
                    api, calls = self.generation_api(candidate)
                    namespace = api._expand_or_polish.__globals__
                    namespace["_static_prompt_reference"].return_value = ["UNRELATED_LEXICON_PROP"]
                    namespace["_diversity_exclusion_terms_from_outputs"].return_value = ["EXCLUDED_PROP"]
                    namespace["_diversity_settings"].return_value.update(
                        retry_limit=2, batch_temperature_floor=True)
                    result, status = self.invoke(
                        api, action, base_model=model,
                        batch_directive="CREATIVE_BATCH_MARKER: change the weather and props.",
                        previous_outputs=[candidate, "PREVIOUS_OUTPUT_MARKER"],
                    )
                    self.assertEqual(result, candidate, status)
                    self.assertEqual(len(calls), 1)
                    for name in ("_static_prompt_reference", "_lexicon_recipe",
                                 "_diversity_exclusion_terms_from_outputs", "_remember_diverse_output"):
                        namespace[name].assert_not_called()
                    api.similarity.assert_not_called()
                    invocation = namespace["_run_llm_generation"].call_args.args
                    self.assertFalse(invocation[15])
                    self.assertEqual(invocation[16], 0)
                    for marker in ("UNRELATED_LEXICON_PROP", "EXCLUDED_PROP", "CREATIVE_BATCH_MARKER",
                                   "PREVIOUS_OUTPUT_MARKER", "<static_tag_lexicon purpose=",
                                   "<batch_generation_directive purpose="):
                        self.assertNotIn(marker, calls[0][4])
                    self.assertEqual(calls[0][6], 0.0)

    def test_convert_then_polish_passes_converted_text_to_polish(self):
        api, _calls = self.generation_api("unused")
        chained_call = Mock(side_effect=[("converted prompt", "converted"), ("polished prompt", "polished")])
        namespace = api._expand_or_polish.__globals__
        with patch.dict(namespace, {"_expand_or_polish": chained_call}):
            result, status = self.invoke(api, "ConvertThenPolish", source="source prompt")
        self.assertEqual(result, "polished prompt")
        self.assertIn("格式转换完成", status)
        self.assertEqual(chained_call.call_args_list[0].args[1], "Convert")
        self.assertEqual(chained_call.call_args_list[0].args[0], "source prompt")
        self.assertEqual(chained_call.call_args_list[1].args[1], "Polish")
        self.assertEqual(chained_call.call_args_list[1].args[0], "converted prompt")

    def test_convert_then_expand_passes_converted_text_to_expand(self):
        api, _calls = self.generation_api("unused")
        chained_call = Mock(side_effect=[("converted prompt", "converted"), ("expanded prompt", "expanded")])
        namespace = api._expand_or_polish.__globals__
        with patch.dict(namespace, {"_expand_or_polish": chained_call}):
            result, status = self.invoke(api, "ConvertThenExpand", source="source prompt")
        self.assertEqual(result, "expanded prompt")
        self.assertIn("格式转换完成", status)
        self.assertEqual(chained_call.call_args_list[0].args[1], "Convert")
        self.assertEqual(chained_call.call_args_list[1].args[1], "Expand")
        self.assertEqual(chained_call.call_args_list[1].args[0], "converted prompt")

    def test_chained_post_processing_stops_when_conversion_fails(self):
        api, _calls = self.generation_api("unused")
        chained_call = Mock(return_value=("", "conversion failed"))
        namespace = api._expand_or_polish.__globals__
        with patch.dict(namespace, {"_expand_or_polish": chained_call}):
            result, status = self.invoke(api, "ConvertThenPolish", source="source prompt")
        self.assertEqual(result, "")
        self.assertIn("格式转换失败", status)
        chained_call.assert_called_once()
    def test_expand_keeps_creative_lexicon_and_diversity_context(self):
        api, calls = self.generation_api("1girl, reading, by_window")
        namespace = api._expand_or_polish.__globals__
        namespace["_diversity_exclusion_terms_from_outputs"].return_value = ["EXCLUDED_PROP"]
        previous = ["1girl, standing, garden"]
        result, status = self.invoke(
            api, "Expand", batch_directive="CREATIVE_BATCH_MARKER", previous_outputs=previous)
        self.assertEqual(result, "1girl, reading, by_window", status)
        namespace["_static_prompt_reference"].assert_not_called()
        self.assertEqual(namespace["_run_llm_generation"].call_args.args[15], previous)
        api.similarity.assert_called_once()
        namespace["_remember_diverse_output"].assert_called_once()
        for marker in ("CREATIVE_BATCH_MARKER", "EXCLUDED_PROP"):
            self.assertIn(marker, calls[0][4])

    def test_batch_generate_does_not_sample_static_lexicon_again(self):
        api, calls = self.generation_api("1girl, reading")
        namespace = api._generate.__globals__
        namespace["_static_prompt_reference"].return_value = ["SECOND_RANDOM_LEXICON_DRAW"]
        result, system, status = self.invoke(api, batch_directive="ALREADY_SAMPLED_RECIPE")
        self.assertEqual(result, "1girl, reading", status)
        namespace["_static_prompt_reference"].assert_not_called()
        self.assertNotIn("SECOND_RANDOM_LEXICON_DRAW", system)
        self.assertIn("ALREADY_SAMPLED_RECIPE", calls[0][4])

    def test_single_generate_requests_only_source_related_lexicon(self):
        api, _calls = self.generation_api("1girl, reading")
        namespace = api._generate.__globals__
        result, system, status = self.invoke(api)
        self.assertEqual(result, "1girl, reading", status)
        reference = namespace["_static_prompt_reference"]
        reference.assert_not_called()
        self.assertNotIn("<static_tag_lexicon purpose=", system)

    def test_all_entrypoints_preserve_zero_without_batch(self):
        for action in ("Generate", "Inline", "Convert", "Polish", "Expand"):
            with self.subTest(action=action):
                api, calls = self.generation_api("1girl, reading")
                result = self.invoke(api, action)
                self.assertEqual(result[0], "1girl, reading", result[-1])
                self.assertEqual([call[6] for call in calls], [0.0])

    def test_batch_temperature_floor_requires_explicit_opt_in_and_never_applies_to_edits(self):
        for action in ("Generate", "Inline", "Convert", "Polish", "Expand"):
            for preserve in (False, True):
                for floor in (False, True):
                    with self.subTest(action=action, preserve=preserve, floor=floor):
                        api, calls = self.generation_api("1girl, reading")
                        namespace = api._generate.__globals__
                        namespace["_diversity_settings"].return_value["batch_temperature_floor"] = floor
                        changes = {} if action == "Inline" else dict(
                            batch_directive="Vary the scene.", preserve_inference_settings=preserve)
                        result = self.invoke(api, action, **changes)
                        self.assertEqual(result[0], "1girl, reading", result[-1])
                        applies_floor = floor and (
                            action == "Generate" or (action == "Expand" and not preserve)
                        )
                        expected = 0.8 if applies_floor else 0.0
                        self.assertEqual(calls[0][6], expected)

    def test_long_instruction_returns_status_without_calling_provider(self):
        for action in ("Generate", "Convert", "Polish", "Expand"):
            with self.subTest(action=action):
                api, calls = self.generation_api("1girl, reading")
                result = self.invoke(api, action, user_instruction="x" * 8001)
                self.assertEqual(result[0], "")
                self.assertIn("8000", result[-1])
                self.assertEqual(calls, [])

    def test_coverage_context_does_not_require_new_scene_or_props(self):
        for action in ("Generate", "Expand"):
            with self.subTest(action=action):
                api, calls = self.generation_api("1girl, reading")
                namespace = api._generate.__globals__
                namespace["_diversity_exclusion_terms_from_outputs"].return_value = ["garden"]
                changes = dict(batch_directive="Keep the scene.")
                if action == "Expand":
                    changes["previous_outputs"] = ["1girl, garden"]
                result = self.invoke(api, action, **changes)
                self.assertEqual(result[0], "1girl, reading", result[-1])
                self.assertIn("Preserve explicit facts, counts, negations, spatial relations", calls[0][4])
                self.assertNotIn("different combination of action, place", calls[0][4])

    def test_zero_temperature_survives_response_and_quality_retries(self):
        first_candidates = (
            ("response", RuntimeError("response did not contain assistant text")),
            ("quality", "reading; sunlight"),
        )
        for action in ("Generate", "Inline", "Convert", "Polish", "Expand"):
            for reason, first in first_candidates:
                with self.subTest(action=action, reason=reason):
                    api, calls = self.generation_api([first, "reading, sunlight"])
                    result = self.invoke(api, action)
                    self.assertEqual(result[0], "reading, sunlight", result[-1])
                    self.assertEqual([call[6] for call in calls], [0.0, 0.0])
                    self.assertIn(reason.upper() + " RETRY", calls[1][4])

    def test_every_entrypoint_rejects_complete_legacy_three_part_output(self):
        for model, _preset, _candidate in MODEL_OUTPUTS:
            for action in ("Generate", "Convert", "Expand", "Inline", "Polish"):
                with self.subTest(model=model, action=action):
                    api, calls = self.generation_api(LEGACY_LAYERED_PROMPT)
                    result = self.invoke(api, action, base_model=model)
                    self.assertEqual(result[0], "", result[-1])
                    self.assertEqual(len(calls), 2)

    def test_multiline_and_missing_technical_tokens_still_fail(self):
        cases = (
            ("FIXED PROMPT (IMMUTABLE):\n<lora:foxStyle:0.8>", "reading, sunlight"),
            ("a girl reading", "reading\nsunlight"),
            ("a girl reading", "reading\rsunlight"),
        )
        for action in ("Generate", "Convert", "Polish", "Expand"):
            for source, candidate in cases:
                with self.subTest(action=action, candidate=candidate):
                    api, calls = self.generation_api(candidate)
                    result = self.invoke(api, action, source=source)
                    self.assertEqual(result[0], "", result[-1])
                    self.assertEqual(len(calls), 2)

    def test_legacy_contract_keeps_tag_cleanup_and_technical_tokens(self):
        for preset in ("Danbooru Tags", "NoobAI Tags", "Anima Tags"):
            for contract in ("complete", "layered"):
                with self.subTest(preset=preset, contract=contract):
                    api, _calls = self.generation_api("")
                    result = api._finalize_generated_prompt(
                        "1girl, watermark, reading, <lora:MyStyleXL:0.8>", preset, "SFW",
                        remove_bad=True, remove_terms="reading", output_contract=contract)
                    self.assertEqual(result, "1girl, <lora:MyStyleXL:0.8>")

    def test_legacy_contract_does_not_bypass_tag_syntax_validation(self):
        for contract in ("complete", "layered"):
            for candidate in ("reading; sunlight", "1girl,", "(reading:1.2", "reading:strong"):
                with self.subTest(contract=contract, candidate=candidate):
                    api, _calls = self.generation_api("")
                    with self.assertRaises(ValueError):
                        api._finalize_generated_prompt(
                            candidate, "Anima Tags", "SFW", output_contract=contract)

    def test_shared_finalizer_safety_conflicts_and_regional_format_are_unchanged(self):
        for contract in ("complete", "layered"):
            api, _calls = self.generation_api("")
            for candidate in ("Let me explain.", "daylight, midnight", "indoors, outdoors", "nude"):
                with self.subTest(contract=contract, candidate=candidate), self.assertRaises(ValueError):
                    api._finalize_generated_prompt(
                        candidate, "Natural Language", "SFW", output_contract=contract)
            self.assertEqual(api._finalize_generated_prompt(
                "nude", "Natural Language", "NSFW", output_contract=contract), "nude")
            wrapper = Mock(return_value="wrapped")
            api._finalize_generated_prompt.__globals__["regional_format"] = wrapper
            self.assertEqual(api._finalize_generated_prompt(
                "A girl reads.", "Natural Language", "SFW", structured_mode="Regional JSON",
                region_count=2, output_contract=contract), "wrapped")
            wrapper.assert_called_once_with("A girl reads.", "Regional JSON", 2)

    def test_complete_contracts_preserve_fixed_technical_tokens(self):
        source = "FIXED PROMPT (IMMUTABLE):\nTriggerABC, <lora:MyStyleXL:0.8>"
        quality = quality_api()._prompt_quality_score
        for contract in ("complete", "layered"):
            for model, preset, candidate in MODEL_OUTPUTS:
                with self.subTest(contract=contract, model=model):
                    self.assertEqual(quality(source, candidate, preset, model,
                                             output_contract=contract), float("-inf"))
                    self.assertTrue(math.isfinite(quality(
                        source, candidate + ", TriggerABC, <lora:MyStyleXL:0.8>", preset, model,
                        output_contract=contract)))

    def test_polish_rejects_meta_output_unsafe_content_and_conflicts(self):
        for model, _preset, _candidate in MODEL_OUTPUTS:
            for candidate in ("Let me explain.\nA girl reads.", "nude", "daylight, midnight", "indoors, outdoors"):
                with self.subTest(model=model, candidate=candidate):
                    api, _calls = self.generation_api(candidate)
                    result, _status = self.invoke(api, "Polish", base_model=model)
                    self.assertEqual(result, "")

    def test_normal_quality_retry_accepts_valid_model_output(self):
        for action in ("Convert", "Polish", "Expand"):
            for model, preset, candidate in MODEL_OUTPUTS:
                with self.subTest(action=action, model=model):
                    api, calls = self.generation_api([LEGACY_LAYERED_PROMPT, candidate])
                    result, status = self.invoke(api, action, base_model=model)
                    self.assertEqual(result, candidate, status)
                    self.assertEqual(len(calls), 2)
                    self.assertIn("QUALITY RETRY", calls[1][4])
                    self.assertIn(PRESETS[preset], calls[1][4])
                    self.assertNotIn("PART THREE:", calls[1][4])

    def test_retries_preserve_model_operation_safety_and_independent_correction(self):
        for action in ("Convert", "Polish", "Expand"):
            for model, preset, candidate in MODEL_OUTPUTS:
                for reason in ("response", "quality"):
                    with self.subTest(action=action, model=model, reason=reason):
                        first = RuntimeError("response did not contain assistant text") if reason == "response" else "Let me explain."
                        api, calls = self.generation_api([first, candidate])
                        result, status = self.invoke(
                            api, action, base_model=model, safety="NSFW", nsfw_injection="LOCAL_NSFW_RULE",
                            user_instruction="Keep the supplied book.", system_override="Keep the supplied colors.",
                            batch_directive="CREATIVE_BATCH_MARKER",
                        )
                        self.assertEqual(result, candidate, status)
                        self.assertEqual(len(calls), 2)
                        self.assertNotIn("<retry_instruction", calls[0][4])
                        self.assertIn(reason.upper() + " RETRY", calls[1][4])
                        for call in calls:
                            system = call[4]
                            self.assertIn(PRESETS[preset], system)
                            self.assertIn(build_operation_instruction(action, model), system)
                            for marker in ("Safety mode: NSFW", "LOCAL_NSFW_RULE",
                                           "Keep the supplied colors."):
                                self.assertIn(marker, system)
                            payload = json.loads(call[5].split("\n", 1)[1].rsplit("\n", 1)[0])
                            self.assertEqual(payload["user_requirements"], "Keep the supplied book.")
                            self.assertEqual("CREATIVE_BATCH_MARKER" in system, action == "Expand")

    def test_creative_retries_keep_batch_and_pass_correction_separately(self):
        directive = "BATCH_ORIGINAL " + "x" * 4500
        for action in ("Generate", "Expand"):
            for reason in ("response", "quality", "diversity"):
                with self.subTest(action=action, reason=reason):
                    api, _calls = self.generation_api("1girl, reading")
                    namespace = api._generate.__globals__
                    builder = Mock(return_value="system")
                    namespace["build_system_prompt"] = builder
                    namespace["_run_llm_generation"] = Mock(side_effect=lambda *args, **kwargs: (
                        "1girl, reading", args[6](reason, 1), 0))
                    result = self.invoke(api, action, batch_directive=directive)
                    self.assertEqual(result[0], "1girl, reading", result[-1])
                    initial, retry = builder.call_args_list
                    for call in (initial, retry):
                        batch = call.kwargs.get("batch_directive", call.args[8] if len(call.args) > 8 else "")
                        self.assertEqual(batch, directive)
                    self.assertIn(reason.upper() + " RETRY", retry.kwargs["retry_instruction"])


if __name__ == "__main__":
    unittest.main()
