from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InfinitePromptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser_source = (ROOT / "javascript" / "llm_prompt_studio_auto_loop.js").read_text(encoding="utf-8")
        cls.ui_source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        cls.entry_source = (ROOT / "scripts" / "llm_prompt_studio.py").read_text(encoding="utf-8")

    def test_inline_panel_exposes_checkbox_and_temporary_merge_positions(self):
        self.assertIn('label="LLM 无限生成（勾选后，Forge 每轮生成前自动等待 LLM）"', self.ui_source)
        self.assertIn('label="本轮 LLM Prompt 合并位置"', self.ui_source)
        for value in ("append_end", "append_start", "replace", "marker"):
            self.assertIn(f'"{value}"', self.ui_source)
        self.assertIn("正面 Prompt 框保持不变", self.ui_source)

    def test_hidden_gradio_generation_bridge_is_removed(self):
        panel_start = self.ui_source.index("def _create_inline_panel")
        panel_end = self.ui_source.index("def _wd14_interrogate", panel_start)
        panel_source = self.ui_source[panel_start:panel_end]
        for stale_declaration in (
            "inline_generate = gr.Button", "inline_output = gr.Textbox",
            "inline_system_preview = gr.Textbox", "inline_status = gr.Markdown",
            "inline_prompt_update = gr.Textbox", "inline_generation_token = gr.Textbox",
            "inline_cache_button = gr.Button", "inline_cache_output = gr.Textbox",
            "inline_cache_status = gr.Textbox", "inline_cache_cursor = gr.State",
        ):
            self.assertNotIn(stale_declaration, panel_source)
        self.assertNotIn("持续生成并入队", panel_source)
        self.assertNotIn("轮数（0 = 持续）", panel_source)

    def test_forge_click_waits_for_a_prepared_llm_prompt(self):
        linked_start = self.browser_source.index("const infiniteModes")
        linked_end = self.browser_source.index("async function inlineOnce", linked_start)
        linked_source = self.browser_source[linked_start:linked_end]
        self.assertIn("preparedPrompt", linked_source)
        self.assertIn("interceptForgeGenerate", linked_source)
        self.assertIn("event.stopImmediatePropagation()", linked_source)
        self.assertIn("await ensureLinkedPrompt(run)", linked_source)
        self.assertIn("setValue(`${slot}_prompt`, original)", linked_source)
        self.assertNotIn("inlineLoop", self.browser_source)

    def test_forge_submission_is_owned_by_the_extension(self):
        self.assertNotIn("forgeConsumePromptOverride", self.browser_source)
        self.assertNotIn("forgeInfiniteBeforeGenerate", self.browser_source)
        self.assertIn("async function submitLinkedForgeGeneration", self.browser_source)
        self.assertIn("startLinkedGenerationLoop(run)", self.browser_source)
        self.assertIn("setValue(`${slot}_prompt`, override)", self.browser_source)
        self.assertIn("setValue(`${slot}_prompt`, original)", self.browser_source)
        self.assertIn("source_tags: promptValue(slot)", self.browser_source)
        self.assertIn("removePromptOverlap", self.browser_source)

    def test_inline_requests_are_serialized_and_cancellable_by_request_id(self):
        self.assertIn("_INLINE_REQUEST_LOCKS", self.ui_source)
        self.assertIn("with _INLINE_REQUEST_LOCKS[slot]", self.ui_source)
        self.assertIn('"request_id"', self.ui_source)
        self.assertIn('@app.post("/llm-prompt-studio/v1/inline-generate"', self.ui_source)
        self.assertIn('@app.post("/llm-prompt-studio/v1/inline-cancel"', self.ui_source)
        self.assertIn('window.fetch("/llm-prompt-studio/v1/inline-cancel"', self.browser_source)
        self.assertIn("cancelInlineRequest(run)", self.browser_source)

    def test_loopback_requests_bypass_the_configured_http_proxy(self):
        self.assertIn('os.environ["NO_PROXY"]', self.entry_source)
        self.assertIn('os.environ["no_proxy"]', self.entry_source)
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertIn(f'"{host}"', self.entry_source)

    def test_extension_infinite_scheduler_waits_for_each_forge_generation(self):
        self.assertIn("while (infiniteModes[run.slot]?.enabled", self.browser_source)
        self.assertIn("await submitLinkedForgeGeneration(run)", self.browser_source)
        self.assertIn("await runForgeGeneration(slot, run, generate, restore)", self.browser_source)
        self.assertIn("interrupt.addEventListener", self.browser_source)

    def test_inline_source_contains_fixed_prompt_and_variation_request(self):
        self.assertIn("INLINE VARIATION REQUEST:", self.ui_source)
        self.assertIn("Never echo the source Prompt twice", self.ui_source)
        self.assertIn("INLINE_DELTA_DIRECTIVE", self.ui_source)

    def test_prefetched_prompt_is_bound_to_current_source_and_configuration(self):
        self.assertIn("preparedSource", self.browser_source)
        self.assertIn("run.preparedSource === promptValue(run.slot)", self.browser_source)
        self.assertIn("run.preparedSource !== promptValue(slot)", self.browser_source)
        for field in ("preset", "baseModel", "safety"):
            self.assertIn(field, self.browser_source)
        self.assertIn("inline_preset, inline_base_model, inline_safety", self.ui_source)
        self.assertIn("preset, baseModel, safety, cacheResult", self.ui_source)

    def test_browser_batch_rounds_are_explicitly_bounded(self):
        self.assertIn('label="循环轮数（1-100）"', self.ui_source)
        self.assertIn("minimum=1, maximum=100", self.ui_source)
        self.assertIn("Math.min(100, Math.max(1", self.browser_source)
        self.assertNotIn("0 表示持续到取消", self.ui_source)

    def test_browser_script_loads_without_runtime_reference_errors(self):
        harness = r'''
const fs = require("fs");
const vm = require("vm");
const emptyRoot = { querySelector: () => null };
global.document = { hidden: false, querySelector: () => null };
global.gradioApp = () => emptyRoot;
global.window = {
    addEventListener: () => {},
    setTimeout: () => 0,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), { filename: process.argv[1] });
if (typeof window.llmPromptStudioAutoLoop?.setInfiniteMode !== "function") process.exit(2);
'''
        result = subprocess.run(
            ["node", "-e", harness, str(ROOT / "javascript" / "llm_prompt_studio_auto_loop.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_replace_mode_keeps_complete_generated_prompt(self):
        compose_start = self.browser_source.index("function composePrompt")
        compose_end = self.browser_source.index("async function readInlineCache", compose_start)
        compose_source = self.browser_source[compose_start:compose_end]
        replace_position = compose_source.index('if (mode === "replace") return generatedText;')
        overlap_position = compose_source.index("removePromptOverlap")
        self.assertLess(replace_position, overlap_position)

    def test_ranbooru_handoff_focus_bridge_is_exposed(self):
        self.assertIn("function focusHandoff", self.browser_source)
        self.assertIn('openAccordionByLabel("Ranbooru 缓存联动")', self.browser_source)
        self.assertIn('openAccordionByLabel("Ranbooru 实时交接箱")', self.browser_source)
        self.assertIn('findButtonByText("刷新交接箱")', self.browser_source)


if __name__ == "__main__":
    unittest.main()
