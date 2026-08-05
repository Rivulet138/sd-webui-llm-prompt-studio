from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).parents[1]


class PromptStudioUiContractTests(unittest.TestCase):
    def test_main_workflows_have_stable_tabs_and_status_targets(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(
            encoding="utf-8"
        )

        expected_tabs = {
            "生成": "llm_prompt_studio_generate_tab",
            "批处理": "llm_prompt_studio_batch_tab",
            "缓存与联动": "llm_prompt_studio_library_tab",
            "连接设置": "llm_prompt_studio_connection_tab",
            "工具": "llm_prompt_studio_tools_tab",
        }
        for label, elem_id in expected_tabs.items():
            self.assertIn(f'gr.Tab("{label}", elem_id="{elem_id}")', source)

        for elem_id in (
            "llm_prompt_studio_status",
            "llm_prompt_studio_cache_status",
            "llm_prompt_studio_handoff_status",
            "llm_prompt_studio_cache_table",
            "llm_prompt_studio_generate_button",
            "llm_prompt_studio_output",
            "llm_prompt_studio_model_id",
            "llm_prompt_studio_cache_result",
            "llm_prompt_studio_auto_score",
            "llm_prompt_studio_auto_loop_tab",
            "llm_prompt_studio_auto_loop_target",
            "llm_prompt_studio_auto_loop_request",
            "llm_prompt_studio_auto_loop_start",
            "llm_prompt_studio_auto_loop_cache",
            "llm_prompt_studio_auto_loop_continuous",
            "llm_prompt_studio_auto_loop_cycles",
            "llm_prompt_studio_auto_loop_generate_run",
            "llm_prompt_studio_auto_loop_dispatch",
            "llm_prompt_studio_auto_loop_run",
            "llm_prompt_studio_auto_loop_clear",
            "llm_prompt_studio_auto_loop_cancel",
            "llm_prompt_studio_auto_loop_status",
            "llm_prompt_studio_auto_loop_log",
        ):
            self.assertIn(f'elem_id="{elem_id}"', source)

        self.assertNotIn("_save_inline_workflow_settings", source)
        self.assertNotIn('workflow["batch_score"]', source)
        self.assertNotIn("batch_score, False, False", source)
        self.assertIn("validate_endpoint(endpoint) + \"/tagger/v1/interrogate\"", source)
        self.assertIn("response.read(4 * 1024 * 1024 + 1)", source)

    def test_auto_loop_javascript_contract(self):
        script = (ROOT / "javascript" / "llm_prompt_studio_auto_loop.js").read_text(encoding="utf-8")
        for marker in (
            "window.llmPromptStudioAutoLoop",
            "async function generateBatch",
            "async function runStored",
            "waitForStudioGeneration",
            "waitForForgeGeneration",
            "run.cancelled",
            "run.phase === \"forge\"",
            "MAX_LOG_ROWS = 100",
            "localStorage",
            "findButton(\"llm_prompt_studio_auto_loop_dispatch\")",
            "find(`${target}_generate`)",
            "target === \"img2img\"",
            "canonicalPrompt",
            "migrateQueue",
            "seen.has(id)",
            "state.requestIds.has(requestId)",
            "duplicateOutputCount",
            "async function generateAndRun",
            "allowRepeat: continuous",
            "cycleLimit === 0",
            "setValue(\"llm_prompt_studio_output\", \"\")",
            "writePrompt(row.prompt, target, mode, basePrompt)",
            "requestIds: Array.from(state.requestIds)",
            "state.lastBatchRowIds.slice()",
            "not persistent",
        ):
            self.assertIn(marker, script)
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn("structured_mode, region_count, 0, False, False", source)
        self.assertIn("auto_loop_cache_result", source)
        self.assertIn('"auto_loop", source_ref', source)

    def test_auto_loop_queue_migration_and_generated_output_deduplication(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the browser-script regression test")
        result = subprocess.run(
            [node, str(ROOT / "tests" / "auto_loop_runtime_test.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, (result.stdout or "") + (result.stderr or ""))

    def test_extension_styles_cover_workflows_tables_and_mobile(self):
        css = (ROOT / "style.css").read_text(encoding="utf-8")

        self.assertIn("#llm_prompt_studio_main_tabs", css)
        self.assertIn(".lps-form-row", css)
        self.assertIn(".lps-table", css)
        self.assertIn("#llm_prompt_studio_auto_loop_log > .prose", css)
        self.assertIn("#llm_prompt_studio_auto_loop_dispatch", css)
        self.assertIn("@media (max-width: 900px)", css)


if __name__ == "__main__":
    unittest.main()
