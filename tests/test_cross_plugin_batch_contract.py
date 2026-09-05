from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class CrossPluginBatchContractTests(unittest.TestCase):
    def test_ranbooru_cache_has_direct_batch_receiver(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn("def _load_ranbooru_to_png_batch(", source)
        self.assertIn('elem_id="llm_prompt_studio_ranbooru_batch_load"', source)
        self.assertIn("outputs=[png_batch_payload, ranbooru_status]", source)

    def test_batch_operations_keep_shared_schema(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn('"schema_version": PNG_BATCH_SCHEMA', source)
        for label, action in (("格式转换", "Convert"), ("扩写", "Expand"), ("润色", "Polish")):
            self.assertIn(f'("{label}", "{action}")', source)

    def test_inspiration_batch_supports_empty_input_and_locked_subject(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn("def _build_inspiration_sources(", source)
        self.assertIn("_INSPIRATION_TOPIC_POOL", source)
        self.assertIn("batch_generation_count", source)
        self.assertIn("batch_base_prompt", source)
        self.assertIn("batch_lock_known", source)
        self.assertIn("batch_sample_static", source)
        self.assertIn("保留身份、LoRA、权重", source)


if __name__ == "__main__":
    unittest.main()
