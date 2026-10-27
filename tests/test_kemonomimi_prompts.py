"""Keep character-focused requests coherent without changing generic sampling."""
import random
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import test_prompt_diversity as diversity
from test_queue_regressions import StudioDB, ui_functions


class KemonomimiPromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = StudioDB(self.root / "test.db")
        self.api = diversity.DiversityLayerTests.api_for(self.db)

    make_lexicon = diversity.DiversityLayerTests.make_lexicon

    def inspiration_api(self):
        return ui_functions(
            "_build_inspiration_sources", "_parse_batch_sources",
            "_normalize_generation_count", "_is_kemonimimi_request",
            "_kemonimimi_reference_categories",
            constants=(
                "_KEMONOMIMI_MARKERS", "_KEMONOMIMI_TOPIC_POOL",
                "_KEMONOMIMI_VARIATION_HINTS", "_KEMONOMIMI_REFERENCE_CATEGORY_HINTS",
                "_KEMONOMIMI_REFERENCE_EXCLUDED_HINTS", "_INSPIRATION_TOPIC_POOL",
                "_INSPIRATION_VARIATION_HINTS", "KEMONOMIMI_LOLI_BATCH_TEMPLATE",
            ),
            DB=self.db, random=random,
            _source_duplication=self.api._source_duplication,
        )

    def test_request_detection_handles_tags_and_does_not_classify_every_girl(self):
        api = self.inspiration_api()
        for text in ("兽耳小萝莉", "fox ears", "cat_ears", "kemonomimi", "animal ears"):
            self.assertTrue(api._is_kemonimimi_request(text), text)
        self.assertFalse(api._is_kemonimimi_request("girl reading in a library"))

    def test_character_references_are_bounded_and_do_not_inject_identity_or_props(self):
        self.make_lexicon()
        for _ in range(8):
            refs = self.api._static_prompt_reference("fox ears, young girl")
            self.assertTrue(refs)
            self.assertLessEqual(len(refs), 2)
            self.assertFalse(any(term in " ".join(refs) for term in (
                "named original character", "wool cardigan", "brass kettle",
                "paper lantern", "straw broom", "1girl", "2girls", "3girls",
            )), refs)

    def test_related_identity_match_is_not_allowed_back_in(self):
        self.make_lexicon()
        refs = self.api._static_prompt_reference("fox ears, named original character")
        self.assertNotIn("named original character", " ".join(refs))

    def test_no_eligible_categories_does_not_mean_sample_everything(self):
        path = self.root / "lexicon" / "人物" / "角色身份.txt"
        path.parent.mkdir(parents=True)
        path.write_text("named original character\n", encoding="utf-8")
        self.db.index_wildcards(self.root / "lexicon")
        self.assertEqual(self.api._static_prompt_reference("fox ears, named original character"), [])
        api = self.inspiration_api()
        sources, _stats = api._build_inspiration_sources("", 2, base_prompt="fox ears")
        self.assertTrue(all("抽样参考" not in source for source in sources))

    def test_reference_limits_and_generic_sampling_remain_supported(self):
        self.make_lexicon()
        self.assertEqual(self.api._static_prompt_reference("fox ears", 0, 0), [])
        self.assertLessEqual(len(self.api._static_prompt_reference("fox ears", 0, 1)), 1)
        self.assertGreater(len(self.api._static_prompt_reference("library")), 2)

    def test_random_sources_use_focused_topics_without_forcing_an_age(self):
        self.make_lexicon()
        api = self.inspiration_api()
        sources, _stats = api._build_inspiration_sources(
            "", 6, base_prompt="adult woman, fox ears",
        )
        self.assertEqual(len(sources), 6)
        for source in sources:
            self.assertIn("adult woman, fox ears", source)
            self.assertTrue(any(f"随机题材：{topic}；" in source for topic in api._KEMONOMIMI_TOPIC_POOL))
            self.assertLessEqual(source.count("["), 2)
            self.assertNotIn("主动补足动作、表情、服装、道具", source)
            self.assertNotIn("小萝莉", source)
            self.assertNotIn("named original character", source)

    def test_explicit_topic_and_shared_template_are_preserved(self):
        api = self.inspiration_api()
        sources, _stats = api._build_inspiration_sources(
            "", 2, topic_pool="月下花园", base_prompt="fox ears", sample_static=False,
        )
        self.assertTrue(all("随机题材：月下花园；" in source for source in sources))
        template = api.KEMONOMIMI_LOLI_BATCH_TEMPLATE
        sources, _stats = api._build_inspiration_sources(
            "", 2, base_prompt="cat ears", shared_request=template,
        )
        self.assertEqual(len(sources), 2)
        self.assertTrue(all(template in source for source in sources))

    def test_recipe_is_small_for_characters_and_respects_a_smaller_user_limit(self):
        self.make_lexicon()
        recipe = self.api._lexicon_recipe(set(), source="fox ears")
        self.assertTrue(recipe)
        self.assertLessEqual(len(recipe), 2)
        self.assertNotIn("prop", {dimension for dimension, _name, _term in recipe})
        self.db.set_setting(self.api._DIVERSITY_SETTINGS_KEY, {"recipe_dimensions": 1})
        self.assertLessEqual(len(self.api._lexicon_recipe(set(), source="fox ears")), 1)

    def test_character_schedule_and_planned_recipe_are_bounded(self):
        self.make_lexicon()
        for index in range(1, 15):
            planned = self.api._recipe_schedule(index, 123, source="fox ears")
            self.assertEqual(len(planned), 2)
            self.assertNotIn("prop", planned)
        recipe = self.api._lexicon_recipe(
            set(), dimensions=("action", "setting", "prop", "expression"), source="cat_ears",
        )
        self.assertEqual([item[0] for item in recipe], ["action", "setting"])
        self.db.set_setting(self.api._DIVERSITY_SETTINGS_KEY, {"recipe_dimensions": 1})
        self.assertEqual(len(self.api._recipe_schedule(1, source="fox ears")), 1)
        self.db.set_setting(self.api._DIVERSITY_SETTINGS_KEY, {"recipe_enabled": False})
        self.assertEqual(self.api._lexicon_recipe(set(), source="fox ears"), [])

    def test_template_is_character_focused_and_allows_explicit_fantasy_species(self):
        api = ui_functions("_template_request", constants=("KEMONOMIMI_LOLI_BATCH_TEMPLATE",))
        template = api._template_request("kemonimimi")
        for rule in (
            "圆润幼态的脸颊", "清澈大眼", "完整得体的服装", "避免性化",
            "未指定时每条只选择一种", "不机械添加尾巴", "明确的幻想种族设定",
            "人物占据主要画面", "最多一个关键道具", "变化一到两项",
        ):
            self.assertIn(rule, template)

    def test_reference_category_exclusions_override_category_hints(self):
        for name in ("人物/表情", "物品/场景道具", "服饰/颜色"):
            path = self.root / "lexicon" / name / "terms.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("unrelated identity or prop\n", encoding="utf-8")
        self.db.index_wildcards(self.root / "lexicon")
        self.assertEqual(self.api._kemonimimi_reference_categories(), [])
        self.assertEqual(self.api._static_prompt_reference("fox ears"), [])
        self.assertEqual(self.api._lexicon_recipe(set(), source="fox ears"), [])

    def caller_api(self):
        self.make_lexicon()
        options = dict(
            preset="Krea 2 Natural", system_override="", base_model="Krea 2", safety="SFW",
            nsfw_injection="", user_instruction="", provider="OpenAI Compatible",
            endpoint="http://127.0.0.1:1234/v1", model="fake", api_key="", temperature=1,
            timeout=30, max_tokens=1024, send_temperature=True, remove_bad=True,
            remove_terms="", shuffle=False, spaces=False, max_tags=0,
            structured_mode="Plain Prompt", region_count=1,
        )
        generated = Mock(return_value=("A fox-eared girl reads a book.", "system", "生成完成"))
        recipe = Mock(wraps=self.api._lexicon_recipe)
        schedule = Mock(wraps=self.api._recipe_schedule)
        inspiration = self.inspiration_api()
        api = ui_functions(
            "_generate_auto_loop", "_inline_generate", "_batch_generate",
            constants=("INLINE_DELTA_DIRECTIVE",),
            DB=self.db, random=random, _generate=generated, _lexicon_recipe=recipe,
            _recipe_schedule=schedule, _coerce_bool=self.api._coerce_bool,
            _canonical_provider=lambda value: value,
            _canonical_preset=lambda value: value,
            _canonical_output_mode=lambda value: value,
            _resolve_preset_model=lambda preset, model: (preset, model),
            _aligned_preset=lambda preset, model: (preset, False),
            _independent_creative_directive=lambda recipe: self.api._recipe_directive_block(recipe),
            _independent_batch_directive=lambda index, lenses, recipe: self.api._recipe_directive_block(recipe),
            _connection_settings=lambda: options,
            _workflow_settings=lambda: options,
            _save_workflow_values=Mock(), _immutable_technical_tokens=lambda source: [],
            _AUTO_LOOP_CANCEL=threading.Event(), _BATCH_CANCEL=threading.Event(),
            _BATCH_LOCK=threading.Lock(), _BATCH_CONTROL_LOCK=threading.Lock(),
            _BATCH_CONTEXT=SimpleNamespace(), _BATCH_ACTIVE_TASK_ID="",
            _normalize_generation_count=inspiration._normalize_generation_count,
            _build_inspiration_sources=inspiration._build_inspiration_sources,
            _parse_batch_sources=inspiration._parse_batch_sources,
            _filtered_cache_updates=lambda *args: ({}, {}),
            _batch_output=lambda status, *args, **kwargs: status,
            gr=SimpleNamespace(update=lambda **kwargs: kwargs), _safe_error=str,
        )
        return api, options.copy(), generated, recipe, schedule

    def test_auto_loop_passes_source_to_the_recipe_with_and_without_caching(self):
        api, options, generated, recipe, _schedule = self.caller_api()
        for cache in (False, True):
            with self.subTest(cache=cache):
                api._generate_auto_loop(request="fox ears", cache_result=cache, **options)
                self.assertEqual(recipe.call_args.kwargs["source"], "fox ears")
                self.assertIn("OPTIONAL LOCAL RECIPE", generated.call_args.args[-1])

    def test_inline_generation_passes_the_fixed_prompt_to_the_recipe(self):
        api, options, generated, recipe, _schedule = self.caller_api()
        for key in ("provider", "endpoint", "model", "api_key", "temperature", "timeout", "max_tokens", "send_temperature"):
            options.pop(key)
        api._inline_generate(
            request="午后阅读", source_tags="fox ears, white hair",
            save_score=0, cache_result=False, **options,
        )
        self.assertIn("fox ears, white hair", recipe.call_args.kwargs["source"])
        self.assertIn("OPTIONAL LOCAL RECIPE", generated.call_args.args[-1])

    def test_batch_generation_passes_source_to_schedule_and_recipe(self):
        api, options, generated, recipe, schedule = self.caller_api()
        results = list(api._batch_generate(
            source_text="", skip_existing=False, skip_failed=False,
            generation_count=1, base_prompt="fox ears, white hair",
            shared_request=self.inspiration_api().KEMONOMIMI_LOLI_BATCH_TEMPLATE, **options,
        ))
        generated.assert_called_once()
        source = generated.call_args.args[0]
        self.assertEqual(schedule.call_args.kwargs["source"], source)
        self.assertEqual(recipe.call_args.kwargs["source"], source)
        self.assertIn("失败 0", results[-1])
        self.assertIn("新增 1", results[-1])

    def test_recipe_hints_can_be_omitted_without_forcing_a_replacement(self):
        block = self.api._recipe_directive_block([("prop", "物品", "paper lantern")])
        self.assertNotIn("Make every listed item", block)
        self.assertNotIn("pick a different compatible option", block)
        self.assertIn("drop that item", block)


if __name__ == "__main__":
    unittest.main()
