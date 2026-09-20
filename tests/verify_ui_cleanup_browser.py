"""Run against studio_browser_harness --inline --mock-llm --seed-cache 60."""
import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7868")
    args = parser.parse_args()
    artifacts = ROOT / "user" / "ui-cleanup"
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(args.url) else route.abort())
        page.goto(args.url + "/?__theme=dark")
        def wait_ready():
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except PlaywrightTimeoutError:
                pass  # Gradio keeps a heartbeat connection open.
            expect(page.locator("#llm_prompt_studio_main_tabs")).to_be_visible(timeout=30000)

        wait_ready()
        page.add_script_tag(path=str(ROOT / "javascript/llm_prompt_studio_auto_loop.js"))
        page.evaluate("""() => {
            window.__generateClicks = 0;
            const button = document.createElement('button');
            button.id = 'txt2img_generate';
            button.textContent = 'Test native generation';
            button.addEventListener('click', () => window.__generateClicks++);
            document.body.appendChild(button);
        }""")
        studio = page.locator("#llm_prompt_studio_main_tabs")
        native = page.locator("#txt2img_prompt textarea")
        output = page.locator("#llm_prompt_studio_output textarea")
        prefix = "#llm_prompt_studio_txt2img_cache"
        cache = page.locator(prefix)
        expect(page.locator(prefix + "_source textarea")).to_be_hidden()
        expect(page.locator(prefix + "_table")).to_have_count(0)
        expect(page.locator("#llm_prompt_studio_preset_editor")).to_have_count(0)

        def choose(selector, name):
            page.locator(selector + " input").click()
            page.get_by_role("option", name=name, exact=True).click()

        studio.get_by_text("编辑 System Prompt", exact=True).click()
        editor = page.locator("#llm_prompt_studio_system_override textarea")
        expect(editor).not_to_have_value("")
        editor.fill("custom system directions")
        choose("#llm_prompt_studio_preset", "Flux 自然语言")
        expect(editor).not_to_have_value("custom system directions")
        expect(editor).not_to_have_value("")
        natural_text = editor.input_value()
        choose("#llm_prompt_studio_preset", "Pony / Illustrious 标签")
        expect(editor).not_to_have_value("")
        choose("#llm_prompt_studio_preset", "Danbooru 标签")
        choose("#llm_prompt_studio_base_model", "Flux")
        expect(editor).to_have_value(natural_text)
        editor.fill("saved custom directions")
        choose("#llm_prompt_studio_base_model", "自动 / 使用底模默认规则")
        expect(editor).to_have_value("saved custom directions")
        studio.get_by_text("工作参数", exact=True).click()
        studio.get_by_role("button", name="保存当前参数", exact=True).click()
        expect(page.locator("#llm_prompt_studio_workflow_status")).to_contain_text("保存")
        page.reload(wait_until="domcontentloaded")
        wait_ready()
        page.add_script_tag(path=str(ROOT / "javascript/llm_prompt_studio_auto_loop.js"))
        page.evaluate("""() => {
            window.__generateClicks = 0;
            const button = document.createElement('button');
            button.id = 'txt2img_generate';
            button.addEventListener('click', () => window.__generateClicks++);
            document.body.appendChild(button);
        }""")
        studio.get_by_text("编辑 System Prompt", exact=True).click()
        expect(editor).to_have_value("saved custom directions")
        page.locator("#llm_prompt_studio_restore_preset").click()
        expect(editor).not_to_have_value("saved custom directions")
        studio.get_by_text("编辑 System Prompt", exact=True).click()

        cache.get_by_text("缓存 Prompt 处理", exact=True).click()
        page.locator(prefix + "_next").click()
        expect(page.locator(prefix + "_page input")).to_have_value("2")
        query = page.locator(prefix + "_query textarea")
        query.fill("scene 60,")
        query.press("Enter")
        expect(page.locator(prefix + "_page input")).to_have_value("1")
        page.locator(prefix + "_selection input").click()
        page.get_by_role("option", name=re.compile(r"^#60 ·")).click()
        source = page.locator(prefix + "_source textarea")
        expect(source).to_have_value(re.compile(r"^scene 60,"))
        cache.get_by_text("查看 / 编辑本次输入", exact=True).click()
        source.fill("edited cache prompt")
        page.locator(prefix + "_run").click()
        result = page.locator(prefix + "_result textarea")
        expect(result).to_have_value("edited cache prompt, soft daylight", timeout=30000)
        expect(page.locator(prefix + "_status")).to_contain_text("已存入处理结果库")
        expect(page.locator("#llm_prompt_studio_result_queue")).to_contain_text("尚未提交生图队列")
        result.fill("reviewed positive prompt")
        page.locator(prefix + "_write").click()
        expect(native).to_have_value("fixed subject\nreviewed positive prompt")
        expect(output).to_have_value("")
        page.locator(prefix + "_write_mode").get_by_text("替换", exact=True).click()
        page.locator(prefix + "_write").click()
        expect(native).to_have_value("reviewed positive prompt")

        # Original record is unchanged until the explicit overwrite action.
        studio.get_by_role("tab", name="缓存", exact=True).click()
        expect(page.locator("#llm_prompt_studio_cache_table")).not_to_contain_text("reviewed positive prompt")
        studio.get_by_role("tab", name="生成", exact=True).click()
        cache.get_by_text("覆盖原始缓存", exact=True).click()
        page.locator(prefix + "_save_one").click()
        expect(page.locator(prefix + "_status")).to_contain_text("已保存 1 条")
        expect(source).to_have_value("reviewed positive prompt")
        page.locator(prefix + "_run").click()
        expect(result).to_have_value("reviewed positive prompt, soft daylight")
        page.locator(prefix + "_enqueue_images").click()
        queue_status = page.locator("#llm_prompt_studio_processed_result_queue_status")
        expect(queue_status).to_contain_text("总计 1", timeout=30000)
        page.locator(prefix + "_enqueue_images").click()
        expect(page.locator(prefix + "_queue_status")).to_contain_text("没有新的未入队")
        expect(queue_status).to_contain_text("总计 1")

        studio.get_by_role("tab", name="处理结果库", exact=True).click()
        studio.get_by_role("button", name="刷新结果库", exact=True).click()
        expect(page.locator("#llm_prompt_studio_processed_result_status")).to_contain_text("2 条")
        page.locator("#llm_prompt_studio_processed_result_enqueue").click()
        expect(page.locator("#llm_prompt_studio_processed_result_status")).to_contain_text("请先选择")
        expect(queue_status).to_contain_text("总计 1")
        page.locator("#llm_prompt_studio_processed_result_scope").get_by_text("全部筛选结果", exact=True).click()
        page.locator("#llm_prompt_studio_processed_result_enqueue").click()
        expect(queue_status).to_contain_text("总计 3")
        page.locator("#llm_prompt_studio_processed_result_enqueue").click()
        expect(page.locator("#llm_prompt_studio_processed_result_status")).to_contain_text("已入队")
        expect(queue_status).to_contain_text("总计 3")
        page.locator("#llm_prompt_studio_result_queue").get_by_role("button", name="取消等待中的生图", exact=True).click()
        expect(queue_status).to_contain_text("取消 3")

        studio.get_by_role("tab", name="更多", exact=True).click()
        wildcard_query = page.locator("#llm_prompt_studio_wildcard_query textarea")
        wildcard_query.fill("hair")
        wildcard_query.press("Enter")
        page.locator("#llm_prompt_studio_wildcard_results input").click()
        option = page.get_by_role("option").first
        term = option.inner_text().strip()
        option.click()
        page.keyboard.press("Escape")
        page.locator("#llm_prompt_studio_wildcard_apply").click()
        expect(page.locator("#llm_prompt_studio_wildcard_apply_status")).to_contain_text("已追加")
        studio.get_by_role("tab", name="生成", exact=True).click()
        expect(page.locator("#llm_prompt_studio_source_tags textarea")).to_have_value(term)

        # Inspect every top-level panel at desktop and narrow widths.
        widths = []
        for width in (1440, 720, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            for tab in ("生成", "缓存", "处理结果库", "设置", "更多"):
                studio.get_by_role("tab", name=tab, exact=True).click()
                bounds = page.evaluate("({width: innerWidth, scroll: document.documentElement.scrollWidth})")
                assert bounds["scroll"] <= bounds["width"] + 1, (tab, bounds)
            widths.append(width)
            studio.get_by_role("tab", name="生成", exact=True).click()
            page.screenshot(path=str(artifacts / f"studio-{width}.png"), full_page=True)
        duplicate_ids = page.evaluate("""() => {
            const ids = [...document.querySelectorAll('[id]')].map(e => e.id).filter(Boolean);
            return ids.filter((id, index) => ids.indexOf(id) !== index);
        }""")
        assert not duplicate_ids, duplicate_ids
        assert page.evaluate("window.__generateClicks") == 0
        assert not errors, errors
        (artifacts / "result.json").write_text(json.dumps({
            "checks": ["one effective System Prompt editor", "preset/model alignment and saved custom text",
                       "compact paginated source picker", "explicit overwrite of original cache", "native txt2img write",
                       "shared queue state and cancellation", "empty selection safe", "explicit filtered scope",
                       "repeat enqueue dedupe", "wildcard selection applies", "all main tabs responsive"],
            "widths": widths, "page_errors": errors, "duplicate_ids": duplicate_ids,
            "mock_llm": True, "worker_disabled": True, "gpu_invoked": False,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
        print("UI cleanup browser checks passed (temporary databases, mock LLM, no GPU).")  # noqa: T201


if __name__ == "__main__":
    main()
