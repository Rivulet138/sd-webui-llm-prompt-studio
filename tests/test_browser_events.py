"""Opt-in browser diagnostics: run directly against studio_browser_harness.py."""
import argparse
from collections import Counter
import json
import time


def main():
    from playwright.sync_api import sync_playwright
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7863")
    parser.add_argument("--records", type=int, default=1)
    args = parser.parse_args()
    payload = {"schema_version": "prompt_batch.v1", "records": [
        {"record_id": f"test-{index}", "index": index, "image": {"filename": f"test-{index}.png"},
         "prompt": {"positive": "a red house", "processed": "a red house in daylight"},
         "selected": False}
        for index in range(1, args.records + 1)
    ]}
    requests = Counter()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        def track(request):
            if "/queue/join" in request.url:
                requests[str((request.post_data_json or {}).get("fn_index"))] += 1
        page.on("request", track)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        page.get_by_role("tab", name="批处理", exact=True).click()
        page.get_by_role("tab", name="导入与插件批次", exact=True).click()
        page.wait_for_timeout(1500)
        baseline = dict(requests)
        page.locator('#llm_prompt_studio_png_batch_file input[type=file]').set_input_files(
            {"name": "batch.json", "mimeType": "application/json",
             "buffer": json.dumps(payload).encode()})
        samples = []
        for _ in range(5):
            page.wait_for_timeout(1000)
            samples.append(dict(requests))
        checkbox = page.locator('#llm_prompt_studio_png_batch_selected input[type=checkbox]')
        assert checkbox.count() == args.records, "Imported results did not render"
        checkbox.first.check(timeout=5000)
        assert checkbox.first.is_checked(), "Selection did not apply"
        selected_samples = []
        for _ in range(3):
            page.wait_for_timeout(1000)
            selected_samples.append(dict(requests))
        page.get_by_text("批次 JSON", exact=True).click()
        textbox = page.locator('#llm_prompt_studio_png_batch_payload textarea')
        textbox.fill("{}")
        page.wait_for_timeout(1000)
        before_typing = dict(requests)
        textbox.press("End")
        # Spaces keep JSON valid and distinguish input from blur semantics.
        textbox.press_sequentially("     ", delay=350)
        page.wait_for_timeout(1000)
        after_typing = dict(requests)
        assert textbox.evaluate("e => e === document.activeElement")
        start = time.monotonic()
        page.get_by_role("tab", name="设置", exact=True).click(timeout=5000)
        click_seconds = time.monotonic() - start
        print(json.dumps({"records": args.records, "baseline": baseline, "after_import": samples,
                          "after_selection": selected_samples,
                          "before_typing": before_typing, "after_typing_without_blur": after_typing,
                          "tab_click_seconds": click_seconds, "page_errors": errors}, indent=2))
        assert samples[-1] == samples[-2], "Import event chain did not settle"
        assert selected_samples[-1] == selected_samples[-2], "Selection event chain did not settle"
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    main()
