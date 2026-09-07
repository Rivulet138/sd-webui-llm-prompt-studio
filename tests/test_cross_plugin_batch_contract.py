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

    def test_queue_write_is_explicit_and_system_preview_is_hidden(self):
        ui = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        js = (ROOT / "javascript" / "llm_prompt_studio_auto_loop.js").read_text(encoding="utf-8")
        png_js = (ROOT / "javascript" / "llm_prompt_studio_png_batch.js").read_text(encoding="utf-8")
        self.assertIn('system_preview = gr.Textbox(visible=False', ui)
        self.assertIn("writeSelectedToPositive", ui)
        self.assertIn("selectAllRows", js)
        self.assertIn("请先勾选待使用的 Prompt", js)
        self.assertIn('const target = "txt2img";', js)
        self.assertIn("_png_batch_selection_choices", ui)
        self.assertIn('label="选择要写入的结果（仅写入 txt2img 正面 Prompt）"', ui)
        self.assertIn("appendSelectedToPrompt", png_js)


if __name__ == "__main__":
    unittest.main()
