"""Exercise Forge's actual keepAlive bridge with suspended background frames, no GPU/LLM calls."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def scenario(browser, keep_alive, start_hidden):
    context = browser.new_context()
    page = context.new_page()
    page.route("http://background.test/", lambda route: route.fulfill(body="<html><body></body></html>", content_type="text/html"))
    page.goto("http://background.test/")
    page.set_content('''<div id="txt2img_prompt"><textarea>fixed subject</textarea></div>
        <button id="txt2img_generate">Generate</button><button id="txt2img_interrupt" style="display:none">Stop</button>
        <div id="txt2img_status"></div><div id="llm_prompt_studio_txt2img_inline_loop_status"></div>''')
    page.evaluate("""({keepAlive, hidden}) => {
        window.opts = {keep_alive: keepAlive};
        window.onOptionsAvailable = callback => callback();
        window.gradioApp = () => document;
        window.backgroundHidden = hidden;
        Object.defineProperty(document, 'hidden', {get: () => window.backgroundHidden});
        const nativeFrame = window.requestAnimationFrame.bind(window);
        window.requestAnimationFrame = callback => document.hidden ? 0 : nativeFrame(callback);
        window.submissions = [];
        window.requests = 0;
        window.fetch = async (url) => {
            if (url.endsWith('inline-cancel')) return {ok:true, status:200, json:async()=>({})};
            if (!url.endsWith('inline-generate')) throw new Error('Unexpected request ' + url);
            const id = ++window.requests;
            return {ok:true, status:200, json:async()=>({prompt:'garden scenery ' + id})};
        };
        const generate = document.querySelector('#txt2img_generate');
        const interrupt = document.querySelector('#txt2img_interrupt');
        window.finishGeneration = () => {
            window.backgroundHidden = true; // Switch to desktop after the first foreground round.
            localStorage.removeItem('txt2img_task_id');
            generate.disabled = false;
            interrupt.style.display = 'none';
            document.querySelector('#txt2img_status').textContent = 'completed ' + window.submissions.length;
        };
        generate.addEventListener('click', () => requestAnimationFrame(() => {
            window.submissions.push(document.querySelector('#txt2img_prompt textarea').value);
            localStorage.setItem('txt2img_task_id', 'task-' + window.submissions.length);
            generate.disabled = true;
            interrupt.style.display = 'block';
            setTimeout(window.finishGeneration, 80);
        }));
        interrupt.addEventListener('click', window.finishGeneration);
    }""", {"keepAlive": keep_alive, "hidden": start_hidden})
    page.add_script_tag(path=str(ROOT.parent.parent / "javascript/keepAlive.js"))
    page.add_script_tag(path=str(ROOT / "javascript/llm_prompt_studio_auto_loop.js"))
    page.evaluate("window.llmPromptStudioAutoLoop.startInlineLoop({slot:'txt2img', request:'change scenery'})")
    if keep_alive:
        page.wait_for_function("window.submissions.length >= 3", timeout=10000)
        page.evaluate("window.llmPromptStudioAutoLoop.cancelInline('txt2img')")
        count = page.evaluate("window.submissions.length")
        page.wait_for_timeout(400)
        assert page.evaluate("window.submissions.length") == count, "Stop must prevent later submissions"
        assert page.locator("#txt2img_prompt textarea").input_value() == "fixed subject"
    else:
        page.wait_for_function("window.requests >= 1")
        page.wait_for_timeout(600)
        count = page.evaluate("window.submissions.length")
        assert count == 0, "Suspended animation frames should reproduce the pre-fix stall"
    result = {"keep_alive": keep_alive, "started_hidden": start_hidden, "submitted_rounds": count}
    context.close()
    return result


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        results = [scenario(browser, False, True), scenario(browser, True, True), scenario(browser, True, False)]
        browser.close()
    (ROOT / "user/background-verification.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
