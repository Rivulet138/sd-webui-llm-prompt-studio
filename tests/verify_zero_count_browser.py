"""Use studio_browser_harness --inline --mock-llm --mock-delay .15 (worker disabled)."""
import argparse
import json
from pathlib import Path
import re

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7868")
    args = parser.parse_args()
    artifacts = ROOT / "user" / "zero-count"
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors, generated, queued = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(args.url) else route.abort())

        def inline_generate(route):
            generated.append(route.request.post_data_json)
            route.fulfill(json={"prompt": f"garden scene {len(generated)}, trees, soft daylight", "status": "ok"})

        def enqueue(route):
            queued.append(route.request.post_data_json)
            route.fulfill(json={"batch_id": f"inline-{len(queued)}", "status": "pending"})
            page.evaluate("count => window.__inlineQueued = count", len(queued))

        def snapshot(route):
            ident = route.request.url.rsplit("/", 1)[-1]
            state = "completed" if ident.startswith("inline-") else "pending"
            route.fulfill(json={"batch_id": ident, "counts": {state: 1}, "status": state,
                                "jobs": [{"position": 1, "status": state, "prompt": "garden scene"}]})

        page.route("**/llm-prompt-studio/v1/inline-generate", inline_generate)
        page.route("**/llm-prompt-studio/v1/queue", enqueue)
        page.route("**/llm-prompt-studio/v1/queue/*", snapshot)
        page.route("**/llm-prompt-studio/v1/queue/*/cancel", lambda route: route.fulfill(json={"status": "cancelled"}))
        page.goto(args.url + "/?__theme=dark", wait_until="domcontentloaded")
        expect(page.locator("#llm_prompt_studio_main_tabs")).to_be_visible(timeout=30000)
        page.add_script_tag(path=str(ROOT / "javascript/llm_prompt_studio_auto_loop.js"))
        count = page.locator("#llm_prompt_studio_batch_generation_count input")
        count.fill("0")
        expect(count).to_have_attribute("min", "0")
        status = page.locator("#llm_prompt_studio_batch_status")
        expect(status).to_have_text("就绪")
        page.locator("#llm_prompt_studio_request textarea").fill("a peaceful garden")
        page.locator("#llm_prompt_studio_generate_button").click()
        page.wait_for_function(r"""() => {
            const match = document.querySelector('#llm_prompt_studio_batch_status').textContent.match(/进度 (\d+)\/无限/);
            return match && Number(match[1]) >= 3;
        }""", timeout=30000)
        page.locator("#llm_prompt_studio_generation_stop").click()
        expect(status).to_contain_text("取消", timeout=30000)
        last = page.locator("#llm_prompt_studio_output textarea").input_value()
        page.wait_for_timeout(600)
        expect(page.locator("#llm_prompt_studio_output textarea")).to_have_value(last)

        # A positive count still finishes; a continuous queue waits for the render.
        count.fill("2")
        page.locator("#llm_prompt_studio_request textarea").fill("a forest garden")
        page.locator("#llm_prompt_studio_generate_button").click()
        expect(status).to_contain_text("批量任务完成", timeout=30000)
        expect(status).to_contain_text("新增 2")
        count.fill("0")
        page.locator("#llm_prompt_studio_generation_destination input[value=queue]").check()
        page.locator("#llm_prompt_studio_request textarea").fill("a mountain garden")
        page.locator("#llm_prompt_studio_generate_button").click()
        expect(page.locator("#llm_prompt_studio_server_queue_id textarea")).not_to_have_value("", timeout=30000)
        expect(status).to_contain_text("进度 1/无限")
        page.wait_for_timeout(800)
        expect(status).to_contain_text("进度 1/无限")
        page.locator("#llm_prompt_studio_generation_stop").click()
        expect(status).to_contain_text("取消")

        prefix = "#llm_prompt_studio_txt2img_inline"
        panel = page.locator(prefix)
        panel.get_by_text("Prompt 批量生成", exact=True).click()
        destination = page.locator(prefix + "_destination")
        destination.get_by_text("仅保存到缓存", exact=True).click()
        inline_count = page.locator(prefix + "_count input")
        expect(inline_count).to_have_attribute("min", "0")
        inline_count.fill("0")
        prompt = page.locator("#txt2img_prompt textarea")
        original = prompt.input_value()
        inline_status = page.locator(prefix + "_loop_status")
        page.locator(prefix + "_once").click()
        expect(inline_status).to_contain_text(re.compile(r"无限.*已完成 [3-9]\d* 条"), timeout=30000)
        page.locator(prefix + "_cancel").click()
        expect(inline_status).to_contain_text("取消")
        before = len(generated)
        page.wait_for_timeout(400)
        assert len(generated) == before
        expect(prompt).to_have_value(original)

        destination.get_by_text("加入生图队列", exact=True).click()
        page.evaluate("window.__inlineQueued = 0")
        page.locator(prefix + "_once").click()
        page.wait_for_function("window.__inlineQueued >= 3", timeout=15000)
        expect(inline_status).to_contain_text("无限")
        page.locator(prefix + "_cancel").click()
        expect(inline_status).to_contain_text("取消")
        assert len(queued) >= 2 and all(len(batch["requests"]) == 1 for batch in queued)
        expect(prompt).to_have_value(original)
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            panel.screenshot(path=str(artifacts / f"inline-{width}.png"))
        assert not errors, errors
        (artifacts / "result.json").write_text(json.dumps({
            "main": ["0 caches until stopped", "positive 2 finishes", "0 queue waits for current render", "stop prevents next round"],
            "inline": ["0 caches until stopped", "0 submits one prompt at a time", "fixed prompt unchanged"],
            "page_errors": errors, "mock_llm": True, "worker_disabled": True, "gpu_invoked": False,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
        print("Zero-count browser checks passed (mock LLM, no GPU).")  # noqa: T201


if __name__ == "__main__":
    main()
