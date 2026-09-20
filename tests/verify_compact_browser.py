"""Run against studio_browser_harness.py --seed-cache 120; uses temporary data."""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright, expect, TimeoutError


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if "127.0.0.1" in route.request.url else route.abort())
        page.goto("http://127.0.0.1:7863/?__theme=dark")
        page.add_style_tag(path=str(Path("style.css").resolve()))
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except TimeoutError:
            # Gradio's event stream can remain open after rendering.
            expect(page.get_by_role("tab", name="缓存", exact=True)).to_be_visible()
        page.get_by_role("tab", name="缓存", exact=True).click()
        panel = page.locator("#llm_prompt_studio_library_tab")
        status = page.locator("#llm_prompt_studio_cache_status")
        table = page.locator("#llm_prompt_studio_cache_table")
        expect(page.locator(".lps-footer-nav")).to_have_count(0)
        button_height = panel.get_by_role("button", name="应用筛选", exact=True).bounding_box()["height"]
        assert 36 <= button_height <= 48, button_height
        panel.get_by_role("button", name="下一页", exact=True).click()
        expect(status).to_contain_text("第 2/3 页")
        panel.get_by_role("button", name="下一页", exact=True).click()
        expect(status).to_contain_text("第 3/3 页")
        expect(panel.get_by_label("页码", exact=True)).to_have_value("3")
        query = panel.get_by_label("搜索 Prompt、负面词、源标签或外部来源", exact=True)
        query.fill("scene 120,")
        query.press("Enter")
        expect(status).to_contain_text("匹配 1 条")
        expect(panel.get_by_label("页码", exact=True)).to_have_value("1")
        table.locator("td").filter(has_text="scene 120,").last.click()
        expect(panel.get_by_label("当前内部 ID", exact=True)).to_have_value("120")
        prompt = panel.get_by_label("正向提示词", exact=True)
        assert len(prompt.input_value()) > 120
        prompt.fill("edited scene 120, long prompt")
        panel.get_by_role("button", name="保存当前记录", exact=True).click()
        expect(status).to_contain_text("已保存缓存记录 #120")
        panel.get_by_role("button", name="清除筛选", exact=True).click()
        expect(status).to_contain_text("匹配 120 条")
        panel.get_by_text("批量修改 · 当前记录 / 全部筛选结果", exact=True).click()
        panel.get_by_label("查找文字（精确匹配）", exact=True).fill("scene")
        panel.get_by_label("替换 / 写入内容（追加时自行包含空格或逗号）", exact=True).fill("landscape")
        panel.get_by_role("button", name="预览影响范围", exact=True).click()
        expect(panel.get_by_label("修改预览", exact=True)).to_have_value(re.compile("实际变化 120 条"))
        panel.get_by_role("button", name="应用预览中的修改", exact=True).click()
        expect(prompt).to_have_value("edited landscape 120, long prompt")
        query.fill("landscape 120,")
        query.press("Enter")
        expect(status).to_contain_text("匹配 1 条")
        panel.get_by_role("button", name="清除筛选", exact=True).click()
        expect(status).to_contain_text("匹配 120 条")
        table.scroll_into_view_if_needed()
        scroll = table.locator("table.table")
        scroll.evaluate("e => {e.scrollTop = 600}")
        page.wait_for_timeout(300)
        samples = []
        for _ in range(10):
            samples.append(scroll.evaluate("e => ({top:e.scrollTop, height:e.scrollHeight, viewport:e.clientHeight})"))
            page.wait_for_timeout(100)
        assert len({s["top"] for s in samples}) == 1, samples
        assert len({s["height"] for s in samples}) == 1, samples
        assert samples[-1]["top"] > 0
        scroll.hover()
        page.mouse.wheel(0, 450)
        page.wait_for_timeout(400)
        after_wheel = scroll.evaluate("e => e.scrollTop")
        page.wait_for_timeout(400)
        assert scroll.evaluate("e => e.scrollTop") == after_wheel
        assert after_wheel > 600
        scroll.evaluate("e => {e.scrollTop = 0}")
        table.locator("table.table th").nth(1).click()
        table.locator("table.table th").nth(1).click()
        row = table.locator("table.table tbody tr").first
        clicked_id = row.locator("td").nth(1).inner_text().strip()
        row.locator("td").nth(4).click()
        expect(panel.get_by_label("当前内部 ID", exact=True)).to_have_value(clicked_id)
        panel.get_by_text("批量修改 · 当前记录 / 全部筛选结果", exact=True).click()
        page.screenshot(path=str(Path("user/compact-cache.png").resolve()), full_page=True)
        page.get_by_role("tab", name="设置", exact=True).click()
        settings = page.locator("#llm_prompt_studio_connection_tab")
        expect(settings.get_by_label("Top P（留空用模型默认）", exact=True)).to_be_visible()
        expect(settings.get_by_label("Top K（留空用模型默认）", exact=True)).to_be_visible()
        expect(settings.locator('input[type="file"]')).to_have_count(0)
        settings.get_by_label("Top P（留空用模型默认）", exact=True).fill("0.8")
        settings.get_by_label("Top K（留空用模型默认）", exact=True).fill("40")
        page.locator("#llm_prompt_studio_model_id input").fill("test-model")
        page.locator("#llm_prompt_studio_model_id input").press("Enter")
        settings.get_by_role("button", name="保存并应用", exact=True).click()
        expect(page.locator("#llm_prompt_studio_connection_status")).to_contain_text("设置已保存")
        page.screenshot(path=str(Path("user/compact-settings.png").resolve()), full_page=True)
        page.get_by_role("tab", name="更多", exact=True).click()
        category = page.locator("#llm_prompt_studio_wildcard_category")
        category.locator("input").click()
        options = category.get_by_role("option")
        assert options.count() > 70, options.count()
        options.nth(1).click()
        expect(page.locator("#llm_prompt_studio_wildcard_results input")).to_be_visible()
        assert not errors, errors
        page.set_viewport_size({"width": 720, "height": 900})
        page.get_by_role("tab", name="缓存", exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        print(json.dumps(  # noqa: T201 - diagnostic report
            {"button_height":button_height, "scroll_samples":samples, "page_errors":errors,
                          "verified":"pagination, filter, full-record load, single save, 120-row bulk edit, settings, categories"}, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
