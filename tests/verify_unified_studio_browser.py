"""Real Gradio check; run harness with --inline --mock-llm (no GPU/LLM calls)."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7868")
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if "127.0.0.1" in route.request.url else route.abort())
        page.goto(args.url + "/?__theme=dark", wait_until="domcontentloaded")
        studio = page.locator("#llm_prompt_studio_main_tabs")
        expect(studio).to_be_visible(timeout=30000)
        expect(studio.get_by_role("tab", name="批处理", exact=True)).to_have_count(0)
        inline = page.locator("#llm_prompt_studio_txt2img_inline")
        inline.get_by_text("Prompt 批量生成", exact=True).click()
        inline.get_by_text("编辑 / 保存自定义模板", exact=True).click()
        studio.get_by_text("自定义快速模板", exact=True).click()
        inline_name = page.locator("#llm_prompt_studio_txt2img_inline_template_name input")
        inline_content = page.locator("#llm_prompt_studio_txt2img_inline_template_content textarea")
        studio_name = page.locator("#llm_prompt_studio_template_name input")
        studio_content = page.locator("#llm_prompt_studio_template_content textarea")
        inline_name.fill("共享夜景")
        inline_content.fill("cinematic night scene")
        page.locator("#llm_prompt_studio_txt2img_inline_template_save").click()
        expect(studio_name).to_have_value("共享夜景")
        expect(studio_content).to_have_value("cinematic night scene")
        expect(page.locator("#llm_prompt_studio_template_choice input")).to_have_value("共享夜景")
        studio_content.fill("updated cinematic garden")
        page.locator("#llm_prompt_studio_template_save").click()
        expect(inline_content).to_have_value("updated cinematic garden")
        expect(page.locator("#llm_prompt_studio_txt2img_inline_template_choice input")).to_have_value("共享夜景")
        page.locator("#llm_prompt_studio_txt2img_inline_template_button").click()
        expect(page.locator("#llm_prompt_studio_txt2img_inline_request textarea")).to_have_value("updated cinematic garden")
        page.locator("#llm_prompt_studio_txt2img_inline_template_default").click()
        expect(page.locator("#llm_prompt_studio_template_status")).to_contain_text("默认模板已设为")
        page.reload(wait_until="domcontentloaded")
        inline.get_by_text("Prompt 批量生成", exact=True).click()
        inline.get_by_text("编辑 / 保存自定义模板", exact=True).click()
        studio.get_by_text("自定义快速模板", exact=True).click()
        expect(inline_name).to_have_value("共享夜景")
        expect(studio_content).to_have_value("updated cinematic garden")
        page.locator("#llm_prompt_studio_template_delete").click()
        expect(inline_name).to_have_value("")
        expect(studio_name).to_have_value("")
        expect(page.locator("#llm_prompt_studio_txt2img_inline_template_choice input")).to_have_value("通用创作")

        # Close editors so the main operation is easy to inspect.
        studio.get_by_text("自定义快速模板", exact=True).click()
        inline.get_by_text("Prompt 批量生成", exact=True).click()
        page.locator("#llm_prompt_studio_request textarea").fill("a peaceful garden")
        page.locator("#llm_prompt_studio_batch_generation_count input").fill("2")
        destination = page.locator("#llm_prompt_studio_generation_destination")
        destination.locator('input[value="cache"]').check()
        page.locator("#llm_prompt_studio_generate_button").click()
        expect(page.locator("#llm_prompt_studio_batch_status")).to_contain_text("批量任务完成", timeout=30000)
        expect(page.locator("#llm_prompt_studio_output textarea")).not_to_have_value("")
        expect(page.locator("#llm_prompt_studio_server_queue_id textarea")).to_have_value("")
        studio.get_by_role("tab", name="缓存", exact=True).click()
        cache_search = studio.get_by_label("搜索 Prompt、负面词、源标签或外部来源", exact=True)
        cache_search.fill("garden scene")
        cache_search.press("Enter")
        expect(page.locator("#llm_prompt_studio_cache_table")).to_contain_text("garden scene")
        expect(page.locator("#llm_prompt_studio_library_tab")).to_contain_text("导入与插件批次")

        studio.get_by_role("tab", name="生成", exact=True).click()
        destination.locator('input[value="queue"]').check()
        page.locator("#llm_prompt_studio_batch_generation_count input").fill("1")
        page.locator("#llm_prompt_studio_request textarea").fill("a forest garden")
        page.locator("#llm_prompt_studio_generate_button").click()
        expect(page.locator("#llm_prompt_studio_server_queue_id textarea")).not_to_have_value("", timeout=30000)
        expect(page.locator("#llm_prompt_studio_server_queue_log")).to_contain_text("garden scene")
        output = ROOT / "user/unified-studio-verification"
        output.mkdir(parents=True, exist_ok=True)
        studio.screenshot(path=str(output / "desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        dimensions = page.evaluate("({width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
        assert dimensions["scroll"] <= dimensions["width"] + 1, dimensions
        studio.screenshot(path=str(output / "mobile.png"))
        assert not errors, errors
        (output / "result.json").write_text(json.dumps({
            "checks": ["two-way template save and same-selection update", "template apply/default/delete", "unified generation tab",
                       "import under cache", "cache-only generation", "explicit image queue", "mobile width"],
            "mock_llm": True, "server_worker_disabled": True, "live_gpu_tested": False,
            "page_errors": errors, "mobile": dimensions,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
        print("Unified Studio browser checks passed (mock LLM; no GPU rendering).")


if __name__ == "__main__":
    main()
