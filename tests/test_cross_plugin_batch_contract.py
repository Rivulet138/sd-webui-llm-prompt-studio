from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).parents[1]
COLLECTOR_ROOT = ROOT.parent / "sd-webui-png-prompt-collector"
if str(COLLECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(COLLECTOR_ROOT))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.modules.setdefault("gradio", types.ModuleType("gradio"))

from png_prompt_collector.service import import_prompt_batch
import prompt_studio_ui as ui


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
        self.assertIn("only clear the explicit selection", js)
        self.assertNotIn("持续自动生图已完成", js)
        self.assertIn("_png_batch_selection_choices", ui)
        self.assertIn('label="选择结果（写回范围为“已选结果”时生效）"', ui)
        self.assertIn("appendSelectedToPrompt", png_js)

    def test_png_collector_bridge_targets_nested_textbox_inputs(self):
        source = (ROOT / "javascript" / "llm_prompt_studio_png_batch.js").read_text(encoding="utf-8")
        self.assertIn("function componentInput(id)", source)
        self.assertIn('host.querySelector("textarea, input")', source)
        self.assertIn('componentInput("llm_prompt_studio_png_batch_payload")', source)

    def test_standalone_json_batch_binds_variation_mode_before_cancel_id(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        binding_start = source.index("png_batch_run.click(")
        binding_end = source.index("png_batch_cancel.click", binding_start)
        binding = source[binding_start:binding_end]
        self.assertIn('gr.State("faithful"), png_batch_cancel_id', binding)

    def test_processed_target_metadata_survives_collector_round_trip(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "LLM Prompt Studio"},
            "records": [{
                "record_id": "record-1",
                "image": {"filename": "one.png", "sha256": ""},
                "prompt": {
                    "positive": "1girl",
                    "processed": "A portrait of a girl.",
                    "processed_kind": "converted",
                    "output_kind": "positive_prompt",
                    "processed_preset": "Krea 2 Natural",
                    "processed_base_model": "Krea 2",
                },
            }],
        }
        collector_record = import_prompt_batch(payload)["records"][0]
        studio_record = ui._normalize_png_batch_payload({
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "PNG Prompt Collector"},
            "records": [collector_record],
        })["records"][0]
        self.assertEqual(studio_record["prompt"]["processed_preset"], "Krea 2 Natural")
        self.assertEqual(studio_record["prompt"]["processed_base_model"], "Krea 2")


if __name__ == "__main__":
    unittest.main()
