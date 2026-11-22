"""WD14 source identity, transform settings, and result queue UI regressions."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image

from test_queue_regressions import ROOT, StudioDB, ui_functions


class WD14SourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = StudioDB(Path(temporary.name) / "cache.db")
        self.api = ui_functions(
            "_wd14_interrogate", "_wd14_save_records", "_wd14_save_single",
            DB=self.db, Path=Path, gr=SimpleNamespace(update=lambda **kwargs: kwargs),
            _filtered_cache_updates=lambda *args: ({}, {}), _safe_error=str,
            interrogate_image_detailed=lambda image, model, threshold, character_threshold: {
                "prompt": "1girl, red_hair", "tags": [{"tag": "red_hair"}],
                "model_path": model, "family": "wd14", "threshold": threshold,
                "character_threshold": character_threshold,
            },
        )

    def test_distinct_images_preserve_records_and_same_image_updates(self):
        first = self.api._wd14_interrogate(Image.new("RGB", (2, 2), "red"), "model.onnx", 0.35)[2]
        second = self.api._wd14_interrogate(Image.new("RGB", (2, 2), "blue"), "model.onnx", 0.35)[2]
        self.api._wd14_save_single("first", first, "Anima")
        self.api._wd14_save_single("second", second, "Anima")
        self.assertEqual(len(self.db.list_prompts(limit=None)), 2)
        self.api._wd14_save_single("edited first", first, "Anima")
        rows = self.db.list_prompts(limit=None)
        self.assertEqual({row["prompt"] for row in rows}, {"edited first", "second"})
        self.assertEqual({row["tags"] for row in rows}, {"red_hair"})

    def test_missing_source_uses_content_deduplication(self):
        for prompt in ("first", "second", "first"):
            self.api._wd14_save_single(prompt, {}, "Anima")
        self.assertEqual(len(self.db.list_prompts(limit=None)), 2)

    def test_model_and_threshold_distinguish_image_sources(self):
        image = Image.new("RGB", (2, 2), "red")
        for model, threshold in (("a.onnx", 0.35), ("b.onnx", 0.35), ("a.onnx", 0.5)):
            record = self.api._wd14_interrogate(image, model, threshold)[2]
            self.api._wd14_save_single("same tags", record, "Anima")
        self.assertEqual(len(self.db.list_prompts(limit=None)), 3)

    def test_failed_inference_clears_source_state(self):
        tags, status, record = self.api._wd14_interrogate(None, "model.onnx", 0.35)
        self.assertEqual((tags, record), ("", {}))
        self.assertIn("失败", status)


class WD14TransformTests(unittest.TestCase):
    def test_visible_advanced_transform_settings_reach_llm(self):
        transform = Mock(return_value=("result", "ok"))
        snapshot = Mock(side_effect=lambda settings, *args: settings)
        api = ui_functions(
            "_wd14_transform", _expand_or_polish=transform,
            _png_batch_connection_snapshot=snapshot,
            _transform_llm_settings=lambda: {"thinking_enabled": True, "top_p": 0.9},
        )
        result = api._wd14_transform(
            "source", "Polish", "Danbooru Tags", "", "Anima", "SFW", "", "",
            "OpenAI Compatible", "https://example.com/v1", "model", "", 0.25, 90,
            1024, True, 0.7, 20, 1, False, 0, "low",
        )
        self.assertEqual(result, ("result", "ok"))
        settings = transform.call_args.kwargs["connection_settings"]
        self.assertFalse(settings["thinking_enabled"])
        self.assertEqual(settings["top_p"], 0.7)
        self.assertEqual(settings["top_k"], 20)
        self.assertEqual(settings["retry_count"], 1)
        self.assertTrue(transform.call_args.kwargs["preserve_inference_settings"])

    def test_interrogate_then_transform_preserves_source_record(self):
        record = {"path": "image:source", "details": {"model_path": "audit.onnx"}}
        transform = Mock(return_value=("edited", "ok"))
        api = ui_functions(
            "_wd14_process", _wd14_interrogate=Mock(return_value=("raw", "inferred", record)),
            _wd14_transform=transform,
        )
        self.assertEqual(api._wd14_process("image", "model", 0.35, 0.85, "Polish"),
                         ("raw", "edited", "inferred · ok", record))
        transform.assert_called_once_with("raw", "Polish")

    def test_replacing_image_clears_single_source_record(self):
        source = (ROOT / "scripts/prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn("image.change(lambda: {}, outputs=wd_single_record, queue=False)", source)

    def test_queue_management_controls_are_bound_for_both_views(self):
        source = (ROOT / "scripts/prompt_studio_ui.py").read_text(encoding="utf-8")
        for name in ("queue_refresh", "processed_queue_refresh"):
            self.assertIn(f"{name}.click(_inline_cache_queue_status", source)
        for name in ("queue_cancel", "processed_queue_cancel"):
            self.assertIn(f"{name}.click(_inline_cache_cancel_queues", source)


if __name__ == "__main__":
    unittest.main()
