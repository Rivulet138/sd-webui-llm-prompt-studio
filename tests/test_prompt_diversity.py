"""Diversity layer: content-only duplicate rule, lexicon recipe, persistent memory.

These tests extract the real functions from ``prompt_studio_ui.py`` (the module
itself requires Forge + Gradio), so the rules under test are the shipped ones.
"""
import collections
import itertools
import random
import tempfile
import threading
import unittest
import uuid
from collections.abc import Iterable
from pathlib import Path

from types import SimpleNamespace

from test_queue_regressions import ROOT, StudioDB, ui_functions
from prompt_studio_core import (
    LLMRequestError,
    _cosine,
    _tokens,
    build_operation_instruction,
    build_system_prompt,
    build_user_message,
)
from prompt_studio_diversity import (
    CONCEPT_ALIASES,
    SKELETON_WORDS,
    STOP_TOKENS,
    duplicate_clusters as _duplicate_clusters,
)

DIVERSITY_CONSTANTS = (
    "_DIVERSITY_SKELETON_TOKENS",
    "_MAX_CONTENT_SIMILARITY",
    "_DIVERSITY_SETTINGS_KEY",
    "_DIVERSITY_MEMORY_SETTING",
    "_DIVERSITY_SETTINGS_DEFAULTS",
    "_DIVERSITY_MEMORY_SIZE",
    "_DIVERSITY_LEDGER_LIMIT",
    "_DIVERSITY_REFERENCE_LIMIT",
    "_MIN_INDEPENDENT_BATCH_TEMPERATURE",
    "_MAX_RESPONSE_RETRIES",
    "_MAX_DIVERSITY_RETRIES",
    "_RECIPE_DIMENSION_RULES",
    "_RECIPE_EXCLUDED_CATEGORY_HINTS",
    "_RECIPE_LABELS",
    "_RECIPE_USED_TERMS",
    "_RECIPE_USED_TERMS_LOCK",
    "_RECIPE_USED_TERMS_LIMIT",
    "_STATIC_REFERENCE_BLOCKLIST",
    "_KEMONOMIMI_MARKERS",
    "_KEMONOMIMI_REFERENCE_CATEGORY_HINTS",
    "_KEMONOMIMI_REFERENCE_EXCLUDED_HINTS",
    "_SOURCE_DUPLICATION_WINDOW",
    "_RECENT_BATCH_OUTPUTS",
    "_RECENT_BATCH_OUTPUTS_LOCK",
    "_DIVERSITY_MEMORY_LOADED",
    "_DIVERSITY_MEMORY_TOUCHED",
    "_DIVERSITY_MEMORY_LOCK",
)

DIVERSITY_FUNCTIONS = (
    "_is_kemonimimi_request",
    "_kemonimimi_reference_categories",
    "_coerce_bool",
    "_diversity_int",
    "_diversity_float",
    "_diversity_settings",
    "_canonical_diversity_tokens",
    "_distinctive_tokens",
    "_content_similarity",
    "_is_diversity_duplicate",
    "_worst_content_similarity",
    "_diversity_exclusion_terms_from_outputs",
    "_diversity_memory_key",
    "_diversity_memory_touch",
    "_load_diversity_memory",
    "_persist_diversity_memory",
    "_remember_diverse_output",
    "_recent_diverse_outputs",
    "_lexicon_recipe",
    "_weighted_category",
    "_recipe_schedule",
    "_source_duplication",
    "_recipe_directive_block",
    "_static_prompt_reference",
)

FIXED_SOURCE = "FIXED PROMPT: young girl"

KITCHEN_A = (
    "A young girl with soft animal ears and a fluffy tail stands in a sunlit wooden kitchen, "
    "wearing a cream sweater, holding a wooden spoon over a mixing bowl."
)
KITCHEN_B = (
    "A single girl with fluffy ears and a soft tail is standing inside a bright wooden kitchen "
    "in the morning, dressed in a cream sweater, with a wooden spoon in hand."
)
STREET_C = (
    "A girl with animal ears and a fluffy tail crouches on a rain-soaked stone street at midnight, "
    "wearing a yellow raincoat, holding a paper lantern above her head."
)


class DiversityLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = StudioDB(self.root / "diversity.db")
        self.api = self.api_for(self.db)

    @staticmethod
    def api_for(db):
        return ui_functions(
            *DIVERSITY_FUNCTIONS,
            constants=DIVERSITY_CONSTANTS,
            _DIVERSITY_SKELETON_WORDS=SKELETON_WORDS,
            _DIVERSITY_STOP_TOKENS=STOP_TOKENS,
            _DIVERSITY_CONCEPT_ALIASES=CONCEPT_ALIASES,
            DB=db,
            _tokens=_tokens,
            _cosine=_cosine,
            Counter=collections.Counter,
            Iterable=Iterable,
            random=random,
            itertools=itertools,
            uuid=uuid,
            threading=threading,
            _duplicate_clusters=_duplicate_clusters,
        )

    def make_lexicon(self):
        files = {
            "动作/基础动作/姿势.txt": ["crouching", "leaning forward", "kneeling"],
            "场景/室内场景/房间.txt": ["attic studio", "greenhouse", "library corner"],
            "物品/日用杂货/器具.txt": ["brass kettle", "paper lantern", "straw broom"],
            "表情/情绪.txt": ["gentle smile", "surprised blink"],
            "颜色/冷色.txt": ["teal", "amber", "crimson"],
            "人物/角色身份.txt": ["named original character"],
            "人物/人数.txt": ["1girl", "2girls", "3girls"],
            "服饰/上衣.txt": ["wool cardigan"],
        }
        for relative, terms in files.items():
            path = self.root / "lexicon" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(terms) + "\n", encoding="utf-8")
        self.db.index_wildcards(self.root / "lexicon")

    # -- duplicate rule -------------------------------------------------
    def test_shared_subject_skeleton_is_not_content(self):
        same_scene = self.api._content_similarity(KITCHEN_A, KITCHEN_B, FIXED_SOURCE)
        other_scene = self.api._content_similarity(KITCHEN_A, STREET_C, FIXED_SOURCE)
        self.assertGreater(same_scene, other_scene)
        self.assertTrue(self.api._is_diversity_duplicate(KITCHEN_A, KITCHEN_B, FIXED_SOURCE))
        self.assertFalse(self.api._is_diversity_duplicate(KITCHEN_A, STREET_C, FIXED_SOURCE))

    def test_near_copies_and_empty_input_are_handled(self):
        self.assertTrue(self.api._is_diversity_duplicate(KITCHEN_A, KITCHEN_A, FIXED_SOURCE))
        self.assertFalse(self.api._is_diversity_duplicate("", KITCHEN_A, FIXED_SOURCE))
        self.assertFalse(self.api._is_diversity_duplicate(KITCHEN_A, "", FIXED_SOURCE))

    def test_threshold_is_respected(self):
        self.assertTrue(self.api._is_diversity_duplicate(KITCHEN_A, KITCHEN_B, FIXED_SOURCE, 0.1))
        self.assertFalse(self.api._is_diversity_duplicate(KITCHEN_A, STREET_C, FIXED_SOURCE, 0.95))

    def test_ledger_no_longer_demands_avoiding_the_subject(self):
        terms = self.api._diversity_exclusion_terms_from_outputs(
            [KITCHEN_A, KITCHEN_B, STREET_C], FIXED_SOURCE)
        self.assertTrue(terms)
        forbidden = {"ears", "ear", "tail", "fluffy", "animal", "soft", "girl", "she", "her", "wearing"}
        self.assertFalse(forbidden & set(terms), f"ledger still contains skeleton words: {terms}")

    # -- lexicon recipe -------------------------------------------------
    def test_recipe_uses_visual_dimensions_only_and_never_repeats(self):
        self.make_lexicon()
        used = set()
        first = self.api._lexicon_recipe(used, 3)
        second = self.api._lexicon_recipe(used, 3)
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertLessEqual(len(first), 3)
        self.assertEqual(len({dimension for dimension, _name, _term in first}), len(first))
        self.assertFalse({term for _d, _n, term in first} & {term for _d, _n, term in second})
        catalog = self.db.wildcard_terms_by_category()
        for _dimension, category, term in [*first, *second]:
            self.assertIn(term, catalog[category])
            self.assertNotIn("人物", category)
            self.assertNotIn("服饰", category)

    def test_recipe_can_be_disabled_and_bounded(self):
        self.make_lexicon()
        self.db.set_setting(self.api._DIVERSITY_SETTINGS_KEY, {"recipe_enabled": False})
        self.assertEqual(self.api._lexicon_recipe(set(), 3), [])
        self.db.set_setting(self.api._DIVERSITY_SETTINGS_KEY, {"recipe_dimensions": 2})
        picks = self.api._lexicon_recipe(set())
        self.assertLessEqual(len(picks), 2)

    def test_recipe_block_states_fixed_content_wins(self):
        block = self.api._recipe_directive_block([("action", "动作/基础动作", "crouching")])
        self.assertIn("OPTIONAL LOCAL RECIPE", block)
        self.assertIn("action or pose: crouching", block)
        self.assertIn("drop that item", block)
        self.assertEqual(self.api._recipe_directive_block([]), "")

    def test_recipe_schedule_is_balanced_and_deterministic(self):
        first = [self.api._recipe_schedule(index, 12345) for index in range(1, 8)]
        again = [self.api._recipe_schedule(index, 12345) for index in range(1, 8)]
        self.assertEqual(first, again)
        self.assertTrue(all(len(item) == 3 for item in first))
        # Seven consecutive items must not repeat a dimension mix.
        self.assertEqual(len({tuple(sorted(item)) for item in first}), 7)
        other = [self.api._recipe_schedule(index, 999) for index in range(1, 8)]
        self.assertNotEqual(first, other)

    def test_recipe_honours_the_planned_dimensions(self):
        self.make_lexicon()
        picks = self.api._lexicon_recipe(set(), dimensions=("action", "setting"))
        self.assertEqual([dimension for dimension, _name, _term in picks], ["action", "setting"])

    def test_identical_sources_are_reported_and_optional_fold(self):
        same = "固定主体：young girl。创作一条完整的单图 Prompt"
        sources = [same, same, same, "a cat sleeping on a warm roof at noon"]
        kept, stats = self.api._source_duplication(list(sources))
        self.assertEqual(len(kept), 4)
        self.assertEqual(stats["content_duplicates"], 2)
        self.assertEqual(stats["content_duplicate_groups"], 1)
        self.db.set_setting(
            self.api._DIVERSITY_SETTINGS_KEY, {"source_fold_duplicates": True})
        kept, stats = self.api._source_duplication(list(sources))
        self.assertEqual(len(kept), 2)
        self.assertEqual(stats["content_folded"], 2)

    def test_count_tags_never_enter_the_optional_vocabulary(self):
        self.make_lexicon()
        reference = self.api._static_prompt_reference("young girl")
        blocked = {"1girl", "2girls", "3girls"}
        self.assertFalse({term.casefold() for term in reference} & blocked, reference)
        # The lexicon still contributes non-identity vocabulary.
        self.assertTrue(reference)

    # -- settings -------------------------------------------------------
    def test_settings_are_clamped_and_tolerate_bad_values(self):
        self.db.set_setting(self.api._DIVERSITY_SETTINGS_KEY, {
            "retry_limit": "many", "similarity_threshold": 9, "recipe_dimensions": 99,
            "recipe_enabled": "no", "memory_per_source": -4,
        })
        settings = self.api._diversity_settings()
        self.assertEqual(settings["retry_limit"], 1)
        self.assertLessEqual(settings["similarity_threshold"], 0.95)
        self.assertLessEqual(settings["recipe_dimensions"], 6)
        self.assertFalse(settings["recipe_enabled"])
        self.assertGreaterEqual(settings["memory_per_source"], 1)
        self.assertFalse(settings["batch_temperature_floor"])

    # -- persistence ----------------------------------------------------
    def test_memory_survives_a_process_restart(self):
        accepted = self.api._remember_diverse_output(FIXED_SOURCE, KITCHEN_A)
        self.assertTrue(accepted)
        stored = self.db.get_setting(self.api._DIVERSITY_MEMORY_SETTING, {})
        self.assertEqual(len(stored), 1)
        self.assertEqual(list(stored.values())[0], [KITCHEN_A])

        # Simulate a restart: drop every in-memory registry, keep the database.
        shared_globals = self.api._load_diversity_memory.__globals__
        shared_globals["_RECENT_BATCH_OUTPUTS"].clear()
        shared_globals["_DIVERSITY_MEMORY_LOADED"] = False
        self.assertEqual(self.api._recent_diverse_outputs(FIXED_SOURCE), [KITCHEN_A])
        # A duplicate of the reloaded item is now rejected by the same rule.
        self.assertFalse(self.api._remember_diverse_output(FIXED_SOURCE, KITCHEN_B))

    def test_memory_is_disabled_without_writing(self):
        self.db.set_setting(self.api._DIVERSITY_SETTINGS_KEY, {"memory_enabled": False})
        self.api._remember_diverse_output(FIXED_SOURCE, KITCHEN_A)
        self.assertEqual(self.db.get_setting(self.api._DIVERSITY_MEMORY_SETTING, {}), {})


GENERATE_FUNCTIONS = DIVERSITY_FUNCTIONS + ("_generate",)
CONNECTION = {
    "retry_count": 0, "thinking_enabled": False, "thinking_budget": 0,
    "reasoning_effort": "", "top_p": None, "top_k": None,
}


class GenerateGateTests(unittest.TestCase):
    """The retry loop in ``_generate`` is the only place the gate can act."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = StudioDB(Path(self.tmp.name) / "gate.db")
        self.calls = []

    def build(self, responses):
        pending = list(responses)

        def fake_call(*args, **kwargs):
            self.calls.append(SimpleNamespace(system=args[4], temperature=args[6], kwargs=kwargs))
            if isinstance(pending[0], Exception):
                raise pending.pop(0)
            return pending.pop(0)

        return ui_functions(
            *GENERATE_FUNCTIONS,
            constants=DIVERSITY_CONSTANTS,
            _DIVERSITY_SKELETON_WORDS=SKELETON_WORDS,
            _DIVERSITY_STOP_TOKENS=STOP_TOKENS,
            _DIVERSITY_CONCEPT_ALIASES=CONCEPT_ALIASES,
            DB=self.db,
            _tokens=_tokens,
            _cosine=_cosine,
            Counter=collections.Counter,
            Iterable=Iterable,
            random=random,
            itertools=itertools,
            uuid=uuid,
            threading=threading,
            _duplicate_clusters=_duplicate_clusters,
            _BATCH_CONTEXT=threading.local(),
            call_llm=fake_call,
            CREDENTIALS=SimpleNamespace(resolve=lambda *args: "fake-key"),
            build_system_prompt=build_system_prompt,
            build_user_message=build_user_message,
            build_operation_instruction=build_operation_instruction,
            _canonical_provider=lambda value: value or "OpenAI Compatible",
            _resolve_preset_model=lambda preset, model: (preset, model),
            _canonical_output_mode=lambda value: value or "Plain Prompt",
            _aligned_preset=lambda preset, model: (preset, False),
            _fallback_request_kwargs=lambda connection: {},
            _finalize_generated_prompt=lambda result, *args: result,
            _safe_error=str,
            LLMRequestError=LLMRequestError,
        )

    def generate(self, api, directive="Independent batch item.", history=None, preserve=True):
        return api._generate(
            request="", source_tags="young girl", preset="Krea 2 Natural", system_override="",
            base_model="Krea 2", safety="SFW", nsfw_injection="", user_instruction="",
            provider="OpenAI Compatible", endpoint="http://127.0.0.1:1234/v1", model="fake-model",
            api_key="fake-key", temperature=1.1, timeout=30, max_tokens=1024, send_temperature=True,
            remove_bad=True, remove_terms="", shuffle=False, spaces=False, max_tags=0,
            structured_mode="Plain Prompt", region_count=1, save_score=0, cache_result=False,
            batch_directive=directive, batch_history=history or [],
            connection_settings=CONNECTION, preserve_inference_settings=preserve,
        )

    def test_single_generation_is_untouched_by_the_gate(self):
        api = self.build([STREET_C])
        prompt, _system, status = self.generate(api, directive="")
        self.assertEqual(prompt, STREET_C)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("DIVERSITY RETRY", self.calls[0].system)
        self.assertEqual(status, "生成完成")

    def test_similar_candidate_is_retried_and_replaced(self):
        api = self.build([KITCHEN_B, STREET_C])
        prompt, _system, status = self.generate(api, history=[KITCHEN_A])
        self.assertEqual(prompt, STREET_C)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("DIVERSITY RETRY", self.calls[0].system)
        self.assertIn("DIVERSITY RETRY", self.calls[1].system)
        self.assertIn("重试 1 次", status)
        # The retry also escalates sampling, so the second call cannot repeat the first.
        self.assertGreater(self.calls[1].temperature, self.calls[0].temperature)

    def test_exhausted_retries_still_return_the_best_candidate(self):
        api = self.build([KITCHEN_A, KITCHEN_B])
        prompt, _system, status = self.generate(api, history=[KITCHEN_A])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(prompt)
        self.assertNotEqual(prompt, KITCHEN_A)
        self.assertIn("重试", status)

    def test_accepted_prompt_is_remembered_for_the_next_run(self):
        api = self.build([STREET_C])
        self.generate(api, history=[KITCHEN_A])
        stored = self.db.get_setting(api._DIVERSITY_MEMORY_SETTING, {})
        self.assertEqual(len(stored), 1)
        self.assertEqual(list(stored.values())[0], [STREET_C])

    def test_user_temperature_is_preserved_unless_the_floor_is_enabled(self):
        api = self.build([STREET_C])
        self.generate(api, history=[KITCHEN_A], preserve=True)
        self.assertAlmostEqual(self.calls[0].temperature, 1.1)
        self.db.set_setting(api._DIVERSITY_SETTINGS_KEY, {"batch_temperature_floor": True})
        self.calls.clear()
        api = self.build([STREET_C])
        self.generate(api, history=[KITCHEN_A], preserve=True)
        self.assertAlmostEqual(self.calls[0].temperature, api._MIN_INDEPENDENT_BATCH_TEMPERATURE)

    def test_gate_prompt_maps_coverage_instead_of_only_prohibiting(self):
        api = self.build([STREET_C])
        # The extracted real ``_static_prompt_reference`` would query the (empty)
        # test lexicon, so inject the fresh-vocabulary branch explicitly.
        api._generate.__globals__["_static_prompt_reference"] = (
            lambda *args, **kwargs: ["[动作] crouching", "[颜色] teal"])
        _prompt, _system, _status = self.generate(api, history=[KITCHEN_A, KITCHEN_B])
        system = self.calls[0].system
        self.assertIn("COVERAGE SO FAR", system)
        self.assertIn("UNUSED DIRECTIONS", system)
        self.assertNotIn("DIVERSITY LEDGER", system)
        self.assertNotIn("Do not reuse their scene structure", system)

    def test_gate_can_be_disabled_and_keeps_one_call(self):
        api = self.build([STREET_C])
        self.db.set_setting(api._DIVERSITY_SETTINGS_KEY, {"retry_limit": 0})
        api = self.build([KITCHEN_B])
        prompt, _system, _status = self.generate(api, history=[KITCHEN_A])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(prompt, KITCHEN_B)


if __name__ == "__main__":
    unittest.main()
