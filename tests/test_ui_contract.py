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
            "llm_prompt_studio_batch_preset",
            "llm_prompt_studio_batch_base_model",
            "llm_prompt_studio_batch_safety",
            "llm_prompt_studio_request",
        ):
            self.assertIn(f'elem_id="{elem_id}"', source)

        self.assertNotIn("_save_inline_workflow_settings", source)
        self.assertIn("inline_once = gr.Button", source)
        self.assertIn("inline_start = gr.Button", source)
        self.assertIn("inline_cancel = gr.Button", source)
        inline_source = source[source.index("def _create_inline_panel"):source.index("def _wd14_interrogate")]
        self.assertNotIn("inline_write_mode = gr.Radio", inline_source)
        self.assertNotIn('label="写入方式"', inline_source)
        self.assertNotIn('("覆盖当前 Prompt", "replace")', inline_source)
        self.assertIn('label="System Prompt 预设", choices=PRESET_UI_CHOICES, value=workflow["preset"]', source)
        self.assertIn('label="目标底模", choices=MODEL_UI_CHOICES, value=workflow["base_model"]', source)
        self.assertIn('label="内容模式", choices=["SFW", "NSFW"], value=workflow["safety"]', source)
        self.assertIn('elem_id=f"llm_prompt_studio_{slot}_inline_generate"', source)
        self.assertIn("inline_generate.click(", source)
        self.assertIn("GENERAL_CREATIVE_REQUEST_TEMPLATE", source)
        self.assertIn("KEMONOMIMI_LOLI_BATCH_TEMPLATE", source)
        self.assertIn("def _create_inline_json_batch_panel", source)
        self.assertIn('if slot == "txt2img":', source)
        self.assertIn('prefix = f"llm_prompt_studio_{slot}_json_batch"', source)
        self.assertIn("_ranbooru_handoff_to_png_batch", source)
        self.assertIn("_inline_ranbooru_handoff_views", source)
        self.assertIn("json_ranbooru_handoff", source)
        self.assertIn("json_png_receive", source)
        self.assertIn("receiveCollectorBatch", source)
        self.assertIn('label="转换 System Prompt 预设"', source)
        self.assertIn('label="转换目标底模"', source)
        self.assertIn('JSON_VARIATION_MODE_CHOICES', source)
        self.assertIn('label="批量多样性模式", choices=JSON_VARIATION_MODE_CHOICES, value="independent"', source)
        self.assertIn('elem_id=f"{prefix}_variation_mode"', source)
        self.assertIn('value="Convert", elem_id=f"{prefix}_action"', source)
        self.assertIn("_recommended_base_model_for_preset", source)
        self.assertIn("inputs=[json_payload, json_action, json_preset, json_base_model, json_variation_mode]", source)
        self.assertIn("填入通用创作需求", source)
        self.assertIn("填入萌系兽耳批量模板", source)
        self.assertIn("批量结果应自然地彼此不同", source)
        self.assertIn("不要套用固定场景清单", source)
        self.assertIn("一条全新、独立成图的日系插画 Prompt", source)
        self.assertIn("二次元可爱兽耳小萝莉", source)
        self.assertIn("静态词库只作参考", source)
        self.assertIn("KREA_ANIMA_POLISH_ROLE", source)
        self.assertIn('processed_kind = "mixed" if action == "Polish"', source)
        self.assertIn("Composition & Pose", source)
        self.assertIn("MASTER DESCRIPTION", source)
        self.assertIn("Previous outputs", source)
        self.assertIn("Recent outputs are exclusion references only", source)
        self.assertIn("not a storyboard", source)
        template_source = source[
            source.index("GENERAL_CREATIVE_REQUEST_TEMPLATE"):
            source.index("PRESET_UI_CHOICES")
        ]
        self.assertNotIn("SFW 内容 Prompt", template_source)
        self.assertIn("独立成图的日系插画 Prompt", template_source)
        self.assertIn("outputs=[inline_output, inline_system_preview, inline_status, inline_prompt_update]", source)
        self.assertIn('_INLINE_WORKFLOW_COMPONENTS[slot] = {', source)
        self.assertIn('_bind_workflow_sync(generate_component, [batch_component, *inline_components], event="change")', source)
        self.assertIn('_bind_workflow_sync(batch_component, [generate_component, *inline_components])', source)
        self.assertIn('gr.Accordion("Prompt 批量生成"', source)
        self.assertIn('gr.Tab("服务端批量生成（仅 Prompt）")', source)
        self.assertIn("只生成并缓存 Prompt，不会自动启动 Forge 生图", source)
        self.assertNotIn('gr.Accordion("标签处理与 RAG", open=False):', source)
        self.assertNotIn("Few-Shot", source)
        self.assertNotIn('gr.Accordion("RAG 与缓存"', source)
        self.assertNotIn('workflow["batch_score"]', source)
        self.assertNotIn("batch_score, False, False", source)
        self.assertIn("validate_endpoint(endpoint) + \"/tagger/v1/interrogate\"", source)
        self.assertIn("response.read(4 * 1024 * 1024 + 1)", source)
        self.assertIn("ui.load(\n            _load_active_connection_settings", source)
        self.assertIn("ui.load(\n            _index_wildcards", source)
        self.assertIn("wildcard_path.change(\n            _index_wildcards", source)
        self.assertNotIn("建立 / 刷新本地索引", source)
        self.assertNotIn('elem_id="llm_prompt_studio_wildcard_index"', source)
        self.assertIn("DB.recover_stale_handoffs()", source)
        self.assertIn("DB.existing_source_prompts(sources, preset, base_model)", source)
        self.assertIn('gr.Accordion("另一条路径：浏览器生图队列（可选）", open=False', source)
        self.assertIn('headers=["序号", "输入", "生成结果", "状态"]', source)
        self.assertIn('gr.Accordion("Ranbooru 实时交接箱", open=False', source)

    def test_png_collector_cross_plugin_contract(self):
        script = (ROOT / "javascript" / "llm_prompt_studio_png_batch.js").read_text(encoding="utf-8")
        self.assertIn("receiveCollectorBatch", script)
        self.assertIn("_json_batch_payload", script)
        collector = (ROOT.parent / "sd-webui-png-prompt-collector" / "javascript" / "png_prompt_collector.js").read_text(encoding="utf-8")
        self.assertIn("llm_prompt_studio_txt2img_json_batch_payload", collector)
        self.assertIn("openInlineJsonPanel", collector)

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
            "findButton(`${target}_generate`)",
            "target === \"img2img\"",
            "canonicalPrompt",
            "migrateQueue",
            "seen.has(id)",
            "state.requestIds.has(requestId)",
            "duplicateOutputCount",
            "async function generateAndRun",
            "allowRepeat: true",
            "cycleLimit === 0",
            "setValue(\"llm_prompt_studio_output\", \"\")",
            "writePrompt(row.prompt, target, mode, basePrompt)",
            "requestIds: Array.from(state.requestIds)",
            "state.lastBatchRowIds.slice()",
            "not persistent",
            "inlineOnce",
            "inlineLoop",
            "cancelInline",
            "const inlineRuns = { txt2img: null, img2img: null }",
            "beginInlineRun",
            "finishInlineRun",
            'writePrompt(prompt, target, "append", basePrompt)',
            "读取缓存超时",
            "内嵌连续生成已完成",
        ):
            self.assertIn(marker, script)
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn("structured_mode, region_count, 0, False", source)
        self.assertIn("auto_loop_cache_result", source)
        self.assertIn('"auto_loop", source_ref', source)
        self.assertIn('"preset": (preset, batch_preset)', source)
        self.assertIn('"base_model": (base_model, batch_base_model)', source)
        self.assertIn('"safety": (safety, batch_safety)', source)

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
            timeout=30,
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
