"""Run against harness --inline --mock-llm --seed-cache 55 --port 7864."""
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if "127.0.0.1" in route.request.url else route.abort())
        page.goto("http://127.0.0.1:7864/?__theme=dark")
        expect(page.locator("#llm_prompt_studio_main_tabs")).to_be_visible(timeout=30000)
        page.add_script_tag(path=str(ROOT / "javascript/llm_prompt_studio_png_batch.js"))
        page.add_script_tag(path=str(ROOT.parent / "sd-webui-png-prompt-collector/javascript/png_prompt_collector.js"))
        expect(page.get_by_text("灵感批量生成", exact=True)).to_have_count(0)
        expect(page.locator("#llm_prompt_studio_txt2img_inline")).to_contain_text("Prompt 批量生成")
        expect(page.locator("#llm_prompt_studio_txt2img_cache")).to_be_visible()
        assert not page.locator("#llm_prompt_studio_txt2img_inline_batch").evaluate("element => element.open")
        page.get_by_role("tab", name="批处理", exact=True).click()
        page.get_by_role("tab", name="导入与插件批次", exact=True).click()
        page.locator("#llm_prompt_studio_png_collector_pull").click()
        batch = page.locator("#llm_prompt_studio_png_batch_tab")
        expect(batch).to_be_visible()
        expect(page.locator("#llm_prompt_studio_png_batch_status")).to_contain_text("已载入 2 条")
        expect(batch.get_by_text("garden.png", exact=True).last).to_be_visible()
        page.locator("#llm_prompt_studio_png_batch_run").click()
        current = page.locator("#llm_prompt_studio_png_batch_current textarea")
        expect(current).to_have_value("a quiet garden, soft daylight", timeout=30000)
        page.locator("#llm_prompt_studio_png_batch_append").get_by_text("覆盖", exact=True).click()
        page.locator("#llm_prompt_studio_png_batch_append_button").click()
        expect(page.locator("#txt2img_prompt textarea")).to_have_value("a quiet garden, soft daylight")

        page.locator("#llm_prompt_studio_ranbooru_batch_load").click()
        expect(page.locator("#llm_prompt_studio_png_batch_status")).to_contain_text("已载入 2 条")
        expect(batch.get_by_text("ranbooru-42.png", exact=True).last).to_be_visible()
        page.locator("#llm_prompt_studio_png_batch_run").click()
        expect(current).to_have_value("a green garden, soft daylight", timeout=30000)
        page.locator("#llm_prompt_studio_png_batch_next").click()
        expect(current).to_have_value("a sandy beach, soft daylight")
        result_style = current.evaluate("element => ({color: getComputedStyle(element).color, textFill: getComputedStyle(element).webkitTextFillColor, opacity: getComputedStyle(element).opacity})")
        batch.screenshot(path=str(ROOT / "user/exposed-batch.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator("#llm_prompt_studio_png_collector_pull")).to_be_visible()
        expect(page.locator("#llm_prompt_studio_ranbooru_batch_load")).to_be_visible()
        mobile_layout = page.evaluate("() => ({client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
        assert mobile_layout["scroll"] <= mobile_layout["client"] + 1, mobile_layout
        batch.screenshot(path=str(ROOT / "user/exposed-batch-mobile.png"))
        page.set_viewport_size({"width": 1440, "height": 1000})

        page.get_by_role("tab", name="设置", exact=True).click()
        settings = page.locator("#llm_prompt_studio_connection_tab")
        settings.get_by_text("请求与推理", exact=True).click()
        fallback = page.locator("#llm_prompt_studio_fallback_model textarea")
        expect(fallback).to_be_visible()
        fallback.fill("backup-model")
        model = page.locator("#llm_prompt_studio_model_id input")
        model.fill("primary-model")
        model.press("Enter")
        settings.get_by_role("button", name="保存并应用", exact=True).click()
        expect(page.locator("#llm_prompt_studio_connection_status")).to_contain_text("回退到 backup-model")
        settings.screenshot(path=str(ROOT / "user/exposed-settings.png"))
        page.reload()
        page.get_by_role("tab", name="设置", exact=True).click()
        settings.get_by_text("请求与推理", exact=True).click()
        expect(fallback).to_have_value("backup-model")

        page.get_by_role("tab", name="批处理", exact=True).click()
        page.get_by_role("tab", name="Prompt 批量生成", exact=True).click()
        page.get_by_text("服务端后台队列", exact=True).click()
        target = page.locator("#llm_prompt_studio_server_queue_target")
        target.get_by_text("生成 Prompt 并生图", exact=True).click()
        expect(page.get_by_text("使用当前 txt2img 设置生成", exact=False)).to_be_visible()
        page.locator("#llm_prompt_studio_auto_loop_request textarea").fill("a quiet garden")
        # Harness disables worker launch; this only enqueues into a temporary database.
        # The standalone harness has no Forge queue API, so retain the enqueue status.
        page.evaluate("window.llmPromptStudioAutoLoop = {watchServerQueue: () => document.querySelector('#llm_prompt_studio_server_queue_status').textContent}")
        page.locator("#llm_prompt_studio_server_queue_start").click()
        expect(page.locator("#llm_prompt_studio_server_queue_status")).to_contain_text("等待中")
        expect(page.locator("#llm_prompt_studio_server_queue_id textarea")).not_to_have_value("")

        page.get_by_role("tab", name="缓存", exact=True).click()
        cache = page.locator("#llm_prompt_studio_library_tab")
        cache.get_by_label("搜索 Prompt、负面词、源标签或外部来源", exact=True).fill("scene 1,")
        cache.get_by_role("button", name="应用筛选", exact=True).click()
        expect(page.locator("#llm_prompt_studio_cache_status")).to_contain_text("匹配 1 条")
        cache.get_by_text("JSON / CSV 导入导出", exact=True).click()
        expect(cache.get_by_role("button", name="导出所选", exact=True)).to_have_count(0)
        cache.get_by_role("button", name="导出全部缓存", exact=True).click()
        download = cache.locator("a[download]")
        expect(download).to_be_visible()
        exported = page.request.get(download.get_attribute("href")).json()
        records = exported if isinstance(exported, list) else exported["records"]
        assert len(records) >= 55, len(records)
        assert not errors, errors
        (ROOT / "user/exposed-workflows-verification.json").write_text(json.dumps({
            "verified": ["first-level inline cache processing", "active PNG cache read, preview, processing and writeback", "active Ranbooru cache read and processing",
                         "fallback persistence across reload", "server txt2img option and enqueue", "unfiltered full export"],
            "exported_records": len(records), "result_style": result_style, "mobile_layout": mobile_layout, "page_errors": errors,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()


if __name__ == "__main__":
    main()
