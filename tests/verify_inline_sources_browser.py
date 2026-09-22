"""Exercise source selection and writing against inline_buttons_gradio_harness."""
import argparse
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7865")
    args = parser.parse_args()
    artifacts = Path(__file__).resolve().parents[1] / "user" / "inline-sources"
    artifacts.mkdir(parents=True, exist_ok=True)
    prefix = "#llm_prompt_studio_txt2img_inline"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors, calls = [], []
        records = {"cache": ["raw garden", "raw beach"], "processed-cache": ["polished forest"]}
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(args.url) else route.abort())

        def cached(route):
            name = route.request.url.split("/v1/")[1].split("?")[0]
            calls.append(name)
            params = parse_qs(urlsplit(route.request.url).query)
            after_id = int(params.get("after_id", [0])[0])
            limit = int(params.get("limit", [100])[0])
            page_records = [{"id": i + 1, "prompt": value} for i, value in enumerate(records[name])
                            if i + 1 > after_id and value.strip()][:limit]
            route.fulfill(json={"records": page_records})

        def generated(route):
            calls.append("llm")
            route.fulfill(json={"prompt": "new sunrise", "status": "ok"})

        page.route("**/llm-prompt-studio/v1/cache?*", cached)
        page.route("**/llm-prompt-studio/v1/processed-cache?*", cached)
        page.route("**/llm-prompt-studio/v1/inline-generate", generated)
        page.goto(args.url + "/?__theme=dark")
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except PlaywrightTimeoutError:
            pass  # Gradio's heartbeat connection stays open.
        page.wait_for_function("typeof window.llmPromptStudioAutoLoop?.inlineOnce === 'function'")
        page.locator("#txt2img_generate").wait_for(state="visible")
        page.evaluate("""() => {
            window.__generations = 0;
            window.__submittedPrompts = [];
            document.querySelector('#txt2img_generate').addEventListener('click', () => {
                window.__generations++;
                window.__submittedPrompts.push(document.querySelector('#txt2img_prompt textarea').value);
            });
            window.requestProgress = (id, container, gallery, done) => setTimeout(done, 250);
        }""")
        page.locator(prefix + "_batch > .label-wrap").click()
        panel = page.locator(prefix)
        source = page.locator(prefix + "_source")
        once = page.locator(prefix + "_once")
        llm_fields = page.locator(prefix + "_llm")
        prompt = page.locator("#txt2img_prompt textarea")
        expect(once).to_have_text("生成一条并写入 Prompt")
        expect(llm_fields).to_be_visible()
        expect(page.locator(prefix + "_marker").locator("textarea, input")).to_be_hidden()
        assert source.bounding_box()["y"] < llm_fields.bounding_box()["y"]
        assert calls == [], calls
        panel.screenshot(path=str(artifacts / "llm-desktop.png"))

        source.get_by_text("原始缓存库", exact=True).click()
        expect(once).to_have_text("读取原始缓存并写入 Prompt")
        expect(llm_fields).to_be_hidden()
        assert calls == [], "Selecting a source must not read a prompt or generate an image"
        prompt.fill("fixed subject")
        once.click()
        expect(prompt).to_have_value("fixed subject, raw garden")
        prompt.fill("fixed subject")
        once.click()
        expect(prompt).to_have_value("fixed subject, raw beach")
        source.get_by_text("处理结果库", exact=True).click()
        expect(once).to_have_text("读取处理结果并写入 Prompt")
        once.click()
        expect(prompt).to_have_value("fixed subject, raw beach, polished forest")
        expect(llm_fields).to_be_hidden()
        assert calls == ["cache", "cache", "processed-cache"], calls
        panel.screenshot(path=str(artifacts / "cache-desktop.png"))

        mode = page.locator(prefix + "_write_mode")
        mode.locator("input").click()
        page.get_by_role("option", name="插入到标记位置", exact=True).click()
        marker = page.locator(prefix + "_marker").locator("textarea, input")
        expect(marker).to_be_visible()
        prompt.fill("fixed subject, {{LLM}}, cinematic light")
        once.click()
        expect(prompt).to_have_value("fixed subject, polished forest, cinematic light")
        mode.locator("input").click()
        page.get_by_role("option", name="替换当前 Prompt", exact=True).click()
        expect(marker).to_be_hidden()
        once.click()
        expect(prompt).to_have_value("polished forest")

        records["processed-cache"] = []
        once.click()
        expect(page.locator(prefix + "_loop_status")).to_contain_text("处理结果库为空")
        expect(prompt).to_have_value("polished forest")
        source.get_by_text("LLM 自动生成", exact=True).click()
        expect(llm_fields).to_be_visible()
        expect(once).to_have_text("生成一条并写入 Prompt")
        once.click()
        expect(prompt).to_have_value("new sunrise")
        assert calls[-1] == "llm"
        assert page.evaluate("window.__generations") == 0

        destination = page.locator(prefix + "_destination")
        destination.get_by_text("仅保存到缓存", exact=True).click()
        expect(once).to_have_text("生成到缓存")
        expect(page.locator(prefix + "_start")).to_be_hidden()
        page.locator(prefix + "_count input").fill("3")
        prompt.fill("fixed subject")
        before = calls.count("llm")
        once.click()
        expect(page.locator(prefix + "_loop_status")).to_contain_text("3 条")
        expect(prompt).to_have_value("fixed subject")
        assert calls.count("llm") == before + 3
        assert page.evaluate("window.__generations") == 0

        queued = []
        def queue_request(route):
            queued.append(route.request.post_data_json)
            route.fulfill(status=500, json={"detail": "Unexpected Studio server queue call"})
        page.route("**/llm-prompt-studio/v1/queue", queue_request)
        destination.get_by_text("前端连续生图（使用当前 txt2img 参数）", exact=True).click()
        expect(once).to_have_text("开始前端生图")
        expect(page.locator(prefix + "_start")).to_be_hidden()
        page.locator(prefix + "_count input").fill("2")
        once.click()
        expect(page.locator(prefix + "_loop_status")).to_contain_text("前端生图完成，共 2 轮", timeout=15000)
        expect(prompt).to_have_value("fixed subject")
        assert page.evaluate("window.__generations") == 2
        assert page.evaluate("window.__submittedPrompts") == ["new sunrise", "new sunrise"]
        assert queued == [], queued
        source.get_by_text("原始缓存库", exact=True).click()
        expect(once).to_have_text("开始前端生图")
        expect(destination.get_by_text("仅保存到缓存", exact=True)).to_have_count(0)
        once.click()
        page.wait_for_function("window.__generations === 4")
        expect(page.locator(prefix + "_loop_status")).to_contain_text("前端生图完成，共 2 轮", timeout=15000)
        assert page.evaluate("window.__submittedPrompts.slice(2)") == ["raw garden", "raw beach"]
        records["processed-cache"] = ["polished forest"]
        source.get_by_text("处理结果库", exact=True).click()
        once.click()
        page.wait_for_function("window.__generations === 6")
        expect(page.locator(prefix + "_loop_status")).to_contain_text("前端生图完成，共 2 轮", timeout=15000)
        assert page.evaluate("window.__submittedPrompts.slice(4)") == ["polished forest", "polished forest"]
        expect(prompt).to_have_value("fixed subject")
        assert queued == [], queued
        panel.screenshot(path=str(artifacts / "queue-desktop.png"))

        for width in (720, 390):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
            for button in (once, page.locator(prefix + "_start"), page.locator(prefix + "_cancel")):
                if not button.is_visible():
                    continue
                box = button.bounding_box()
                assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
            panel.screenshot(path=str(artifacts / f"llm-{width}.png"))
        source.get_by_text("处理结果库", exact=True).click()
        expect(llm_fields).to_be_hidden()
        panel.screenshot(path=str(artifacts / "cache-mobile.png"))
        assert not errors, errors
        print("PASS: source visibility, cache reads, writes, merge controls, cache-only/native frontend destinations, fixed prompt, no automatic generation, responsive layout")
        browser.close()


if __name__ == "__main__":
    main()
