"""Against the isolated harness --inline --mock-llm --seed-cache 120."""
import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if "127.0.0.1" in route.request.url else route.abort())
        page.goto("http://127.0.0.1:7863/?__theme=dark")
        prefix = "#llm_prompt_studio_txt2img_cache"
        cache_panel = page.locator(prefix)
        expect(cache_panel.locator("textarea").first).to_be_visible(timeout=30000)
        assert cache_panel.evaluate("e => Boolean(e.closest('#llm_prompt_studio_cache_processing'))")
        expect(page.locator(prefix + "_result textarea")).to_be_hidden()
        page.locator(prefix + "_next").click()
        expect(page.locator(prefix + "_page input")).to_have_value("2")
        query = page.locator(prefix + "_query textarea")
        query.fill("scene 120,")
        query.press("Enter")
        expect(page.locator(prefix + "_page input")).to_have_value("1")
        row = page.locator(prefix + "_table table.table td").filter(has_text="scene 120,")
        expect(row).to_have_count(1)
        row.click()
        source = page.locator(prefix + "_source textarea")
        expect(source).to_have_value(re.compile("^scene 120,"))
        source.fill("edited cache prompt")
        page.locator(prefix + "_run").click()
        result = page.locator(prefix + "_result textarea")
        expect(result).to_have_value("edited cache prompt, soft daylight", timeout=30000)
        result.fill("reviewed positive prompt")
        page.locator(prefix + "_write_mode").get_by_text("替换", exact=True).click()
        page.locator(prefix + "_write").click()
        expect(page.locator("#llm_prompt_studio_output textarea")).to_have_value("reviewed positive prompt")
        page.locator(prefix + "_save_one").click()
        expect(page.locator(prefix + "_status")).to_contain_text("已保存 1 条")
        expect(source).to_have_value("reviewed positive prompt")
        expect(result).to_be_hidden()
        # Save refreshed the selected snapshot, so the same record can be processed again.
        page.locator(prefix + "_run").click()
        expect(result).to_have_value("reviewed positive prompt, soft daylight")
        page.locator(prefix + "_save_one").click()
        expect(source).to_have_value("reviewed positive prompt, soft daylight")
        query.fill("")
        query.press("Enter")
        expect(cache_panel).to_contain_text(re.compile(r"匹配 \d+ 条"))
        cache_panel.screenshot(path="user/revised-inline.png")
        page.locator(prefix + "_scope").get_by_text("全部筛选结果", exact=True).click()
        page.locator(prefix + "_run").click()
        expect(page.locator(prefix + "_status")).to_contain_text("已处理 120", timeout=30000)
        result_page = page.locator(prefix + "_result_page input")
        result_page.fill("2")
        result_page.press("Enter")
        expect(result).to_have_value(re.compile("^scene 51,"))
        page.locator(prefix + "_save_all").click()
        expect(page.locator(prefix + "_status")).to_contain_text("已保存 120 条")

        main_panel = page.locator("#llm_prompt_studio_main_tabs")
        request = main_panel.locator("#llm_prompt_studio_request textarea")
        request.fill("short request")
        page.wait_for_timeout(250)
        short = request.bounding_box()["height"]
        request.fill("long request\n" * 16)
        expect(request).to_have_value("long request\n" * 16)
        page.wait_for_timeout(250)
        tall = request.bounding_box()["height"]
        assert tall > short + 100, (short, tall)
        request.fill("Describe one complete scene with clear lighting and composition.")
        page.wait_for_timeout(250)
        assert request.bounding_box()["height"] < tall
        columns = main_panel.locator(".lps-main-workbench > :is(.lps-source-column, .lps-rules-column)")
        assert abs(columns.nth(0).bounding_box()["y"] - columns.nth(1).bounding_box()["y"]) < 2
        assert float(request.evaluate("e => getComputedStyle(e).fontSize").removesuffix("px")) >= 16
        expect(page.get_by_text("另一条路径：浏览器生图队列", exact=True)).to_have_count(0)
        main_panel.screenshot(path="user/revised-main.png")
        page.get_by_role("tab", name="缓存", exact=True).click()
        table = page.locator("#llm_prompt_studio_cache_table")
        headers = table.locator("table.table th").all_text_contents()
        assert len(headers) == 6, headers
        assert not any("负面" in text or "标签摘要" in text for text in headers)
        positive_width = table.locator("table.table th").nth(4).bounding_box()["width"]
        assert positive_width > table.bounding_box()["width"] * .5
        main_panel.screenshot(path="user/revised-cache.png")
        for width in [720, 390]:
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(200)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), width
        assert not errors, errors
        Path("user/prompt-panel-verification.json").write_text(json.dumps({
            "request_height_short": short, "request_height_long": tall,
            "positive_column_width": positive_width, "headers": headers,
            "page_errors": errors, "verified": "single edit/process/write/save/reprocess; 120-row batch and pagination; responsive layout",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()


if __name__ == "__main__":
    main()
