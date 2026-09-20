"""Opt-in live Gradio check with isolated LLM and GPU responses."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--screenshots", type=Path, required=True)
    parser.add_argument("--native-only", action="store_true")
    parser.add_argument("--validation-failures", action="store_true")
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    llm_requests, cancellations, submissions, events, errors = [], [], [], [], []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        def isolate_scripts(route):
            url = route.request.url
            if "extensions" in url and ".js" in url and "llm-prompt-studio" not in url:
                route.fulfill(content_type="application/javascript", body="")
            else:
                route.continue_()

        page.route("**/*.js*", isolate_scripts)
        page.route("**/llm-prompt-studio/v1/inline-generate", lambda route: llm_requests.append(route))

        def cancel_request(route):
            cancellations.append(route.request.post_data_json)
            route.fulfill(json={"cancelled": True})

        page.route("**/llm-prompt-studio/v1/inline-cancel", cancel_request)
        config = page.request.get(args.url + "/config").json()
        dependencies = {dep["id"]: dep for dep in config["dependencies"]}
        generate_id = next(c["id"] for c in config["components"] if c["props"].get("elem_id") == "txt2img_generate")
        generation_ids = {dep["id"] for dep in config["dependencies"]
                          if [generate_id, "click"] in dep["targets"] and dep.get("js") == "submit"}

        # Exercise the real Gradio input snapshot and submit() without starting GPU work.
        event_sequence = 0

        def queue_join(route):
            nonlocal event_sequence
            payload = route.request.post_data_json
            dependency = dependencies[payload["fn_index"]]
            if payload["fn_index"] in generation_ids:
                submissions.append(payload)
            event_sequence += 1
            event_id = f"studio-browser-{event_sequence}"
            events.append({"msg": "process_completed", "event_id": event_id, "success": True,
                           "output": {"data": [{"__type__": "update"} for _ in dependency["outputs"]],
                                      "is_generating": False, "duration": 0.01}})
            route.fulfill(json={"event_id": event_id})

        def queue_data(route):
            messages = events[:]
            events.clear()
            messages.append({"msg": "close_stream"})
            route.fulfill(content_type="text/event-stream", body="".join(
                "data: " + json.dumps(event) + "\n\n" for event in messages))

        def predict(route):
            payload = route.request.post_data_json
            dependency = dependencies[payload["fn_index"]]
            if payload["fn_index"] in generation_ids:
                submissions.append(payload)
            route.fulfill(json={"data": [{"__type__": "update"} for _ in dependency["outputs"]],
                                "is_generating": False, "duration": 0.01})

        page.route("**/queue/join*", queue_join)
        page.route("**/queue/data?*", queue_data)
        page.route("**/run/predict*", predict)
        page.goto(args.url + "/?__theme=dark", wait_until="domcontentloaded", timeout=120000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass  # Forge can keep background requests open.
        page.wait_for_function("typeof window.llmPromptStudioAutoLoop?.startInlineLoop === 'function'")
        assert page.locator("#llm_prompt_studio_txt2img_inline_infinite").count() == 0
        page.locator("#llm_prompt_studio_txt2img_inline_batch > .label-wrap").click()
        start = page.locator("#llm_prompt_studio_txt2img_inline_start")
        stop = page.locator("#llm_prompt_studio_txt2img_inline_cancel")
        start.wait_for(state="visible")
        fixed = "red house,"
        prompt = page.locator("#txt2img_prompt textarea")
        prompt.fill(fixed)
        page.locator("#llm_prompt_studio_txt2img_inline_request textarea").fill("Vary the scene")
        page.wait_for_timeout(1000)
        page.evaluate("""() => {
            window.__studioCompletions = [];
            window.__studioTrace = [];
            const submit = window.submit;
            window.submit = function(...args) {
                window.__studioTrace.push({event: 'submit', prompt: args[1]});
                return submit(...args);
            };
            document.querySelector('#txt2img_generate').addEventListener('click', () => {
                window.__studioTrace.push({event: 'click', prompt: document.querySelector('#txt2img_prompt textarea').value});
            }, true);
            window.requestProgress = (id, container, gallery, done) => {
                window.__studioCompletions.push(done);
            };
        }""")

        def wait_count(items, count):
            for _ in range(200):
                if len(items) >= count:
                    return
                page.wait_for_timeout(50)
            status = page.evaluate("document.querySelector('#llm_prompt_studio_txt2img_inline_loop_status')?.innerText || document.body.innerText.slice(0, 1000)")
            page.screenshot(path=str(args.screenshots / "inline-buttons-failure.png"))
            trace = page.evaluate("window.__studioTrace")
            raise AssertionError(f"Expected {count} events, received {len(items)}; status={status}; errors={errors}; trace={trace}")

        def release_llm(index, text):
            llm_requests[index].fulfill(json={"prompt": text, "status": "ok"})

        if args.native_only:
            page.locator("#txt2img_generate").click()
            wait_count(submissions, 1)
            print("Native Forge Generate submitted successfully", flush=True)
            browser.close()
            return

        start.click()
        wait_count(llm_requests, 1)
        print("Start button dispatched the first LLM request", flush=True)
        assert start.is_disabled()
        assert not submissions
        assert prompt.input_value() == fixed, repr(prompt.input_value())
        first = 0
        if args.validation_failures:
            llm_requests[first].fulfill(status=400, json={"detail": "生成失败：LLM 输出不是英文。请检查模型的语言指令或更换模型后重试。"})
            first += 1
            wait_count(llm_requests, first + 1)
            assert start.is_disabled()
            assert not submissions
        release_llm(first, "red house, morning sunlight")
        wait_count(submissions, 1)
        print("Captured the first Gradio generation submission", flush=True)
        next_request = first + 1
        wait_count(llm_requests, next_request + 1)
        assert submissions[0]["data"][1] == "red house, morning sunlight", submissions[0]["data"][1]
        assert prompt.input_value() == fixed
        if args.validation_failures:
            for detail in ("生成失败：LLM 输出不是英文。", "生成失败：SFW 校验拦截了成人内容。"):
                llm_requests[next_request].fulfill(status=400, json={"detail": detail})
                next_request += 1
                wait_count(llm_requests, next_request + 1)
                assert start.is_disabled()
                assert len(submissions) == 1
                assert prompt.input_value() == fixed
        release_llm(next_request, "red house, evening rain")
        page.wait_for_timeout(500)
        assert len(submissions) == 1, "Second generation overlapped the first"
        page.evaluate("window.__studioCompletions.shift()()")
        wait_count(submissions, 2)
        assert submissions[1]["data"][1] == "red house, evening rain", submissions[1]["data"][1]
        next_request += 1
        wait_count(llm_requests, next_request + 1)
        stop.click()
        wait_count(cancellations, 1)
        assert cancellations[-1]["request_id"] == llm_requests[next_request].request.post_data_json["request_id"]
        assert start.is_enabled()
        assert prompt.input_value() == fixed
        page.evaluate("window.__studioCompletions.shift()()")
        release_llm(next_request, "late result must not submit")
        page.wait_for_timeout(500)
        assert len(submissions) == 2
        start.click()
        next_request += 1
        wait_count(llm_requests, next_request + 1)
        stop.click()
        wait_count(cancellations, 2)
        release_llm(next_request, "cancelled restart result")
        page.wait_for_timeout(300)
        assert start.is_enabled()
        start.scroll_into_view_if_needed()
        page.screenshot(path=str(args.screenshots / "inline-buttons-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        start.scroll_into_view_if_needed()
        page.screenshot(path=str(args.screenshots / "inline-buttons-mobile.png"))
        for button in (start, stop):
            bounds = button.bounding_box()
            assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 391
        assert not errors, errors
        print(json.dumps({"forge_submissions": len(submissions), "llm_requests": len(llm_requests),
                          "cancellations": len(cancellations), "prompt_restored": prompt.input_value() == fixed,
                          "restart_passed": True, "page_errors": errors,
                          "validation_failure_recovery": args.validation_failures,
                          "llm_and_gpu_mocked": True, "other_extension_scripts_disabled": True}, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
