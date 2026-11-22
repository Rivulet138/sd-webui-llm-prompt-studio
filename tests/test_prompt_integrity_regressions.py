"""Independent regressions for prompt cleanup, conversion and saved rules."""
import ast
import json
import logging
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from prompt_studio_core import (
    PRESETS, build_operation_instruction, build_system_prompt, build_user_message,
    canonicalize_booru_tags, is_sfw_output,
    extract_technical_tokens, process_tags, prompt_constraint_conflicts, prompt_constraint_violations,
    regional_format,
)
from prompt_studio_generation import CandidateQualityError, run_llm_generation


def ui_api(**overrides):
    """Load the real UI boundary functions without importing Gradio or databases."""
    names = {"_coerce_bool", "_finalize_generated_prompt", "_prompt_quality_score",
             "_immutable_technical_tokens", "_strip_immutable_technical_tokens",
             "_restore_immutable_technical_tokens", "_run_llm_generation", "_expand_or_polish"}
    tree = ast.parse((ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8"))
    module = ast.Module(body=[node for node in tree.body
                             if isinstance(node, ast.FunctionDef) and node.name in names],
                        type_ignores=[])
    namespace = dict(
        re=re, LOGGER=logging.getLogger(__name__), CandidateQualityError=CandidateQualityError,
        _canonical_preset=lambda value: str(value or "").strip(),
        _canonical_output_mode=lambda value: str(value or "").strip(),
        canonicalize_booru_tags=lambda text: canonicalize_booru_tags(text, vocabulary={}),
        process_tags=process_tags, is_sfw_output=is_sfw_output, regional_format=regional_format,
        prompt_constraint_conflicts=prompt_constraint_conflicts,
        prompt_constraint_violations=prompt_constraint_violations,
        _run_llm_generation_impl=run_llm_generation,
        CREDENTIALS=SimpleNamespace(resolve=lambda *args: ""),
        call_llm=Mock(), build_user_message=build_user_message,
        _fallback_request_kwargs=lambda connection: {},
        _worst_content_similarity=lambda *args: 0.0,
        _MAX_CONTENT_SIMILARITY=0.82, _MAX_RESPONSE_RETRIES=1,
        _canonical_action=lambda value: value,
        _canonical_provider=lambda value: value,
        _resolve_preset_model=lambda preset, model: (preset, model),
        _connection_settings=lambda provider: {"retry_count": 0},
        _safe_error=str, build_operation_instruction=build_operation_instruction,
        build_system_prompt=build_system_prompt,
    )
    namespace.update(overrides)
    exec(compile(module, str(ROOT / "scripts" / "prompt_studio_ui.py"), "exec"), namespace)  # noqa: S102 - loads selected repository functions into an isolated test namespace
    return SimpleNamespace(**namespace)


def generate(source, responses, finalize, quality=None, **options):
    calls = []
    responses = iter(responses)

    def call_llm(*args, **kwargs):
        calls.append(args[4])
        return next(responses)

    result = run_llm_generation(
        source, "test", "http://localhost", "test", "", "initial",
        lambda reason, attempt: f"{reason}-{attempt}", finalize,
        {"retry_count": 0}, 0.5, 10, 200, False,
        credentials=SimpleNamespace(resolve=lambda *args: ""), call_llm=call_llm,
        build_user_message=lambda value: value, coerce_bool=bool,
        fallback_request_kwargs=lambda connection: {}, candidate_quality=quality,
        **options,
    )
    return result, calls


class LoaderIntegrityTests(unittest.TestCase):
    def test_cleanup_and_limit_preserve_all_loaders_exactly(self):
        loaders = ["<lora:My_Style:0.8>", "<lyco:other_model:0.5>", "<hypernet:Net_7:1>"]
        raw = "watermark, " + loaders[0] + " red_hair, blue_eyes, " + ", ".join(loaders[1:])
        result = process_tags(raw, underscores_to_spaces=True, max_tags=1)
        self.assertEqual(result, loaders[0] + ", red hair, " + ", ".join(loaders[1:]))

    def test_case_distinct_loader_names_are_preserved(self):
        source = "1girl, <lora:Style:1>, <lora:style:1>, <lora:Style:1>"
        self.assertEqual(process_tags(source, underscores_to_spaces=True, max_tags=1),
                         "1girl, <lora:Style:1>, <lora:style:1>")
        api = ui_api()
        tokens = api._immutable_technical_tokens(source)
        self.assertEqual(tokens, ["<lora:Style:1>", "<lora:style:1>"])
        self.assertEqual(api._restore_immutable_technical_tokens("A girl.", tokens),
                         "A girl., <lora:Style:1>, <lora:style:1>")

    def test_case_distinct_loaders_pass_real_quality_and_finalizer(self):
        api = ui_api()
        source = "1girl, <lora:Style:1>, <lora:style:1>"
        result, calls = generate(
            source, [source],
            lambda raw: api._finalize_generated_prompt(raw, "Danbooru Tags", "SFW", max_tags=1),
            lambda original, raw: api._prompt_quality_score(original, raw, "Danbooru Tags"),
            max_response_retries=0,
        )
        self.assertEqual(result[0], source)
        self.assertEqual(len(calls), 1)

    def test_adjacent_loaders_preserve_literal_boundaries(self):
        api = ui_api()
        source = "portrait<lora:Style:1><lora:style:1>smiling"
        tokens = api._immutable_technical_tokens(source)
        self.assertEqual(prompt_constraint_violations(source, source), [])
        self.assertEqual(api._strip_immutable_technical_tokens(source, tokens), "portrait  smiling")
        self.assertEqual(api._restore_immutable_technical_tokens(source, tokens), source)

    def test_loader_type_spelling_is_preserved(self):
        api = ui_api()
        source = "1girl, <LoRA:Style:1>"
        result, calls = generate(
            source, [source],
            lambda raw: api._finalize_generated_prompt(raw, "Danbooru Tags", "SFW"),
            lambda original, raw: api._prompt_quality_score(original, raw, "Danbooru Tags"),
            max_response_retries=0,
        )
        self.assertEqual(result[0], source)

    def test_wildcard_cleanup_and_shuffle_keep_loader_names(self):
        raw = "red_hair, <lora:my_style:0.8>, blue_eyes, <lora:my_style:0.8>"
        with patch("prompt_studio_core.random.SystemRandom.shuffle", lambda self, items: items.reverse()):
            result = process_tags(raw, remove_terms="*", shuffle=True,
                                  underscores_to_spaces=True, max_tags=1)
        self.assertEqual(result, "<lora:my_style:0.8>")

    def test_real_finalizer_and_generation_keep_loader_beyond_tag_limit(self):
        api = ui_api()
        source = "1girl, <lora:my_style:0.8>"
        result, calls = generate(
            source, [source],
            lambda raw: api._finalize_generated_prompt(
                raw, "Danbooru Tags", "SFW", spaces=True, max_tags=1),
            lambda source, candidate: api._prompt_quality_score(source, candidate, "Danbooru Tags"),
        )
        self.assertEqual(result[0], source)
        self.assertEqual(len(calls), 1)

    def test_generation_retries_loader_damage_introduced_by_finalizer(self):
        source = "1girl, <lora:my_style:0.8>"
        responses = [source, source + ", smiling"]
        result, calls = generate(
            source, responses,
            lambda raw: "1girl" if raw == source else raw,
            lambda source, candidate: 1.0 if not prompt_constraint_violations(source, candidate)
            else float("-inf"),
        )
        self.assertEqual(result[0], responses[1])
        self.assertEqual(calls, ["initial", "quality-1"])

    def test_generation_rejects_finalizer_damage_without_quality_callback(self):
        source = "1girl, <lora:my_style:0.8>"
        with self.assertRaises(CandidateQualityError):
            generate(source, [source], lambda raw: "1girl", max_response_retries=0)

    def test_generation_recovers_after_damage_without_quality_callback(self):
        source = "1girl, <lora:my_style:0.8>"
        responses = [source, source + ", smiling"]
        result, calls = generate(source, responses, lambda raw: "1girl" if raw == source else raw)
        self.assertEqual(result[0], responses[1])
        self.assertEqual(calls, ["initial", "quality-1"])

    def test_structured_finalization_keeps_loaders(self):
        api = ui_api()
        source = r"1girl, <lora:folder\my_style:0.8>"
        for mode in ("Regional JSON", "Regional Markdown"):
            with self.subTest(mode=mode):
                result, calls = generate(
                    source, [source],
                    lambda raw: api._finalize_generated_prompt(
                        raw, "Danbooru Tags", "SFW", spaces=True, max_tags=1,
                        structured_mode=mode, region_count=2),
                    lambda source, candidate: api._prompt_quality_score(source, candidate, "Danbooru Tags"),
                )
                text = json.loads(result[0])["base_prompt"] if mode == "Regional JSON" else result[0]
                self.assertIn(r"<lora:folder\my_style:0.8>", text)
                self.assertEqual(len(calls), 1)


class NaturalConversionIntegrityTests(unittest.TestCase):
    def test_ordinary_booru_words_can_be_rendered_as_natural_phrases(self):
        api = ui_api()
        source = "1girl, blue_eyes, long_hair"
        natural = "A girl with blue eyes and long hair."
        result, calls = generate(
            source, [natural], lambda raw: raw,
            lambda source, candidate: api._prompt_quality_score(source, candidate, "Krea 2 Natural"),
        )
        self.assertEqual(result[0], natural)
        self.assertEqual(len(calls), 1)

    def test_ordinary_tag_phrases_can_be_paraphrased(self):
        self.assertEqual(prompt_constraint_violations(
            "blue_eyes, long_hair, model_42",
            "A woman whose eyes are blue and whose hair falls to her waist, model 42."), [])

    def test_conversion_still_rejects_missing_or_changed_loaders(self):
        source = "blue_eyes, <lora:my_style:0.8>"
        for candidate in ("blue eyes", "blue eyes, <lora:my style:0.8>"):
            with self.subTest(candidate=candidate):
                self.assertIn("missing-token:<lora:my_style:0.8>",
                              prompt_constraint_violations(source, candidate))

    def test_explicit_trigger_declarations_require_exact_spelling(self):
        for declaration in ("Trigger word: fox_maid", "Keep trigger word fox_maid unchanged.",
                            "保留触发词 fox_maid 不变。", "触发词：fox_maid"):
            with self.subTest(declaration=declaration):
                self.assertEqual(extract_technical_tokens(declaration), ["fox_maid"])
                self.assertEqual(prompt_constraint_violations(declaration, "A fox maid."),
                                 ["missing-token:fox_maid"])
                self.assertEqual(prompt_constraint_violations(declaration, "A fox. fox_maid"), [])

    def test_explicit_trigger_lists_and_quoted_phrases_keep_source_order(self):
        source = 'Trigger words: fox_maid, "Silver Fox", abc42\n<lora:Style:1>'
        self.assertEqual(extract_technical_tokens(source),
                         ["fox_maid", "Silver Fox", "abc42", "<lora:Style:1>"])
        self.assertEqual(prompt_constraint_violations(source, "fox_maid, Silver Fox, abc42, <lora:Style:1>"), [])

    def test_declared_trigger_survives_conversion_and_polish_finalization(self):
        api = ui_api()
        source = "Trigger word: fox_maid\n1girl, blue_eyes, <lora:Style:1>"
        natural = "A girl with blue eyes. fox_maid, <lora:Style:1>"
        converted, calls = generate(
            source, [natural], lambda raw: raw,
            lambda source, candidate: api._prompt_quality_score(source, candidate, "Krea 2 Natural"))
        polished = "A blue-eyed girl. fox_maid, <lora:Style:1>"
        finalized = []

        def finalize(raw):
            finalized.append(raw)
            return raw.replace("fox_maid", "fox maid") if len(finalized) == 1 else raw

        result, calls = generate(
            converted[0], [polished, polished], finalize,
            protected_tokens=api._immutable_technical_tokens(source),
        )
        self.assertEqual(result[0], polished)
        self.assertEqual(len(calls), 2)

    def test_ui_token_helpers_keep_ordinary_booru_in_conversion_input(self):
        api = ui_api()
        source = "1girl, blue_eyes, long_hair, <lora:My_Style:0.8>"
        tokens = api._immutable_technical_tokens(source)
        self.assertEqual(tokens, ["<lora:My_Style:0.8>"])
        semantic = api._strip_immutable_technical_tokens(source, tokens)
        self.assertEqual(semantic, "1girl, blue_eyes, long_hair")
        converted = api._restore_immutable_technical_tokens("A girl with long hair and blue eyes.", tokens)
        self.assertEqual(api._immutable_technical_tokens(converted), tokens)
        self.assertNotIn("blue_eyes", converted)


class UiGenerationIntegrityTests(unittest.TestCase):
    def transform(self, responses, requirements="", action="ConvertThenPolish"):
        provider = Mock(side_effect=responses)
        api = ui_api(call_llm=provider)
        result = api._expand_or_polish(
            "1girl, blue_eyes, <lora:Style:1>, <lora:style:1>", action,
            "Krea 2 Natural", "", "Auto / checkpoint default", "SFW", "", requirements,
            "test", "http://localhost", "test", "", 0.5, 10, 200, False,
        )
        return result, provider

    def test_chain_retries_missing_original_loader_in_second_step(self):
        converted = "A girl with blue eyes. <lora:Style:1>, <lora:style:1>"
        invalid = "A blue-eyed girl. <lora:style:1>"
        polished = "A blue-eyed girl. <lora:Style:1>, <lora:style:1>"
        result, provider = self.transform([converted, invalid, polished])
        self.assertEqual(result[0], polished)
        self.assertEqual(provider.call_count, 3)
        self.assertIn("QUALITY RETRY", provider.call_args_list[-1].args[4])

    def test_chain_rejects_missing_loader_after_retries(self):
        converted = "A girl with blue eyes. <lora:Style:1>, <lora:style:1>"
        invalid = "A blue-eyed girl. <lora:style:1>"
        result, provider = self.transform([converted, invalid, invalid])
        self.assertEqual(result[0], "")
        self.assertEqual(provider.call_count, 3)

    def test_chain_keeps_explicit_user_trigger_requirements(self):
        converted = "A girl with blue eyes. fox_maid, <lora:Style:1>, <lora:style:1>"
        invalid = "A blue-eyed girl. fox maid, <lora:Style:1>, <lora:style:1>"
        polished = "A blue-eyed girl. fox_maid, <lora:Style:1>, <lora:style:1>"
        result, provider = self.transform(
            [invalid, converted, invalid, polished], "保留触发词 fox_maid 不变。")
        self.assertEqual(result[0], polished)
        self.assertEqual(provider.call_count, 4)
        for call in provider.call_args_list:
            self.assertIn("fox_maid", call.args[5])

    def test_wrapper_retries_finalizer_damage_to_explicit_trigger(self):
        candidate = "A girl with blue eyes. fox_maid"
        provider = Mock(side_effect=[candidate, candidate])
        api = ui_api(call_llm=provider)
        finalized = Mock(side_effect=[candidate.replace("fox_maid", "fox maid"), candidate])
        result = api._run_llm_generation(
            "1girl, blue_eyes", "test", "http://localhost", "test", "", "initial",
            lambda reason, attempt: f"{reason}-{attempt}", finalized,
            {"retry_count": 0}, 0.5, 10, 200, False,
            candidate_quality=lambda original, raw: api._prompt_quality_score(
                original, raw, "Krea 2 Natural"),
            user_requirements="Keep trigger word fox_maid unchanged.",
        )
        self.assertEqual(result[0], candidate)
        self.assertEqual(provider.call_count, 2)

    def test_inline_delta_does_not_repeat_fixed_tokens(self):
        provider = Mock(return_value="smiling")
        api = ui_api(call_llm=provider)
        result = api._run_llm_generation(
            "FIXED PROMPT (IMMUTABLE):\n1girl, <lora:Style:1>\n\nINLINE VARIATION REQUEST:\nsmile",
            "test", "http://localhost", "test", "", "initial",
            lambda reason, attempt: f"{reason}-{attempt}", lambda raw: raw,
            {"retry_count": 0}, 0.5, 10, 200, False,
            candidate_quality=lambda original, raw: api._prompt_quality_score(
                original, raw, "Danbooru Tags", output_contract="delta"),
            user_requirements="Keep trigger word fox_maid unchanged.",
        )
        self.assertEqual(result[0], "smiling")
        self.assertEqual(provider.call_count, 1)


class HistoricalRuleIntegrityTests(unittest.TestCase):
    def build(self, override, preset="Krea 2 Natural"):
        return build_system_prompt(preset, "Auto / checkpoint default", "SFW", "", "", [],
                                   system_override=override)

    def test_real_historical_profiles_do_not_reintroduce_retired_rules(self):
        rules = {
            "Krea 2 Natural": (
                "Return one descriptive paragraph of 70-160 words, no markdown, one coherent daily-life image.",
                "Ground every sentence in something visible; never invent plot, backstory, inner thoughts, dialogue or narration."),
            "Anima Tags": (
                "Return one comma-separated Danbooru-first prompt, 6-50 tags.",
                "Keep character fidelity and a readable composition ahead of any boilerplate. Weight only a genuinely important feature, and only when the user asks."),
        }
        for preset, (output_rule, content_rule) in rules.items():
            with self.subTest(preset=preset):
                old = PRESETS[preset].splitlines()
                override = "\n".join([old[0], output_rule, old[2], content_rule])
                system = self.build(override, preset)
                self.assertNotIn("<system_supplement", system)

    def test_custom_notes_on_separate_or_same_line_survive_migration(self):
        legacy = "Return one descriptive paragraph of 70-160 words, no markdown, one coherent daily-life image."
        for separator in (" ", "\n", "\n\n"):
            with self.subTest(separator=separator):
                system = self.build("Output profile: Krea 2 natural-language prompt.\n" + legacy
                                    + separator + "Keep the kettle dented.")
                self.assertIn("Keep the kettle dented.", system)
                self.assertNotIn("70-160 words", system)
                self.assertNotIn("one coherent daily-life image", system)

    def test_content_inserted_into_historical_format_line_survives(self):
        system = self.build(
            "Output profile: Krea 2 natural-language prompt.\n"
            "Return one descriptive paragraph of 70-160 words, no markdown, "
            "one coherent daily-life image of a black cat beside a brass kettle.")
        self.assertIn("black cat beside a brass kettle", system)
        self.assertNotIn("70-160 words", system)
        self.assertNotIn("daily-life image", system)

    def test_user_authored_return_order_and_length_preferences_survive(self):
        notes = ("Return a portrait of a black cat, in one concise paragraph without markdown.\n"
                 "Order the visible books by their color.\n"
                 "Use 70-160 words for my detailed panorama.")
        system = self.build(PRESETS["Krea 2 Natural"] + "\n" + notes)
        for line in notes.splitlines():
            self.assertIn(line, system)


if __name__ == "__main__":
    unittest.main()
