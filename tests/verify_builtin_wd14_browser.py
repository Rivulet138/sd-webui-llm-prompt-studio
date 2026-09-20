"""Exercise built-in WD14 single/folder workflows through actual Gradio events."""
import argparse
from pathlib import Path
import tempfile

from PIL import Image
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7866")
    parser.add_argument("--no-models", action="store_true")
    args = parser.parse_args()
    prefix = "#llm_prompt_studio_wd14_"
    artifacts = Path(__file__).resolve().parents[1] / "user" / "builtin-wd14-verification"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wd14-upload-") as folder, sync_playwright() as p:
        upload = Path(folder) / "single.png"
        Image.new("RGB", (16, 16), "red").save(upload)
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1360, "height": 1000})
        errors = []
        def record_error(error):
            errors.append(str(error))
            print("BROWSER ERROR:", str(error), flush=True)
        page.on("pageerror", record_error)
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(args.url) else route.abort())
        page.goto(args.url, wait_until="domcontentloaded")
        page.locator("#wd14_test_refresh_cache").click()
        page.wait_for_timeout(300)
        initial_cache_count = int(page.locator("#wd14_test_cache_count input").input_value())
        page.get_by_role("tab", name="更多", exact=True).click()
        page.get_by_role("tab", name="WD14", exact=False).click()
        expect(page.locator(prefix + "tab")).not_to_contain_text("推荐模型")
        if args.no_models:
            missing = page.locator(prefix + "download_panel")
            expect(missing).to_be_visible()
            expect(page.locator(prefix + "model_status")).to_contain_text("未检测到完整")
            expect(page.locator(prefix + "download")).to_be_disabled()
            page.locator(prefix + "download_choice input").click()
            page.get_by_role("option", name="CL Tagger 1.01", exact=True).click()
            for name in ("model.onnx", "tag_mapping.json"):
                expect(missing.get_by_role("link", name=name, exact=True)).to_have_attribute("href", f"https://huggingface.co/cella110n/cl_tagger/resolve/0b6e9b4e145b1423bfd1715119074a24a301b471/cl_tagger_1_01/{name}")
            page.locator("#wd14_test_read_downloads").click()
            expect(page.locator("#wd14_test_downloads input")).to_have_value("0")
            page.locator(prefix + "download").click()
            expect(page.locator(prefix + "model_status")).to_contain_text("下载失败", timeout=10000)
            expect(missing).to_be_visible()
            expect(page.locator(prefix + "download")).to_be_enabled()
            page.locator(prefix + "tab").screenshot(path=str(artifacts / "missing-model.png"))
            page.locator(prefix + "download").click()
            expect(page.locator(prefix + "download")).to_be_hidden(timeout=10000)
            expect(page.locator(prefix + "model input")).to_have_value("CL Tagger 1.01")
            expect(page.locator(prefix + "model_status")).to_contain_text("已检测到 1")
            page.locator("#wd14_test_read_downloads").click()
            expect(page.locator("#wd14_test_downloads input")).to_have_value("2")
            page.locator(prefix + "model_refresh").click()
            expect(page.locator(prefix + "model_status")).to_contain_text("已检测到 1")
        else:
            expect(page.locator(prefix + "download_panel")).to_be_visible()
            expect(page.locator(prefix + "download")).to_be_hidden()
        page.locator(prefix + "image input[type=file]").set_input_files(str(upload))
        page.locator(prefix + "interrogate").click()
        expect(page.locator(prefix + "tags textarea")).to_have_value("red subject", timeout=30000)
        page.locator(prefix + "send").click()
        expect(page.locator("#txt2img_prompt textarea")).to_have_value("fixed subject, red subject")
        page.locator(prefix + "save").click()
        expect(page.locator(prefix + "status")).to_contain_text("缓存", timeout=10000)
        page.locator("#wd14_test_refresh_cache").click()
        expect(page.locator("#wd14_test_cache_count input")).to_have_value(str(max(1, initial_cache_count)))
        path = page.locator("#wd14_test_folder textarea").input_value()
        page.get_by_role("tab", name="文件夹批量", exact=True).click()
        page.locator(prefix + "folder textarea").fill(path)
        recursive = page.locator(prefix + "recursive input")
        recursive.uncheck()
        page.locator(prefix + "batch_start").click()
        try:
            expect(page.locator(prefix + "batch_table")).to_contain_text("red subject", timeout=30000)
        except AssertionError:
            print("BATCH STATUS:", page.locator(prefix + "batch_status").inner_text(), flush=True)
            page.screenshot(path=str(artifacts / "failure.png"), full_page=True)
            raise
        expect(page.locator(prefix + "batch_table")).not_to_contain_text("blue subject")
        recursive.check()
        page.locator(prefix + "batch_start").click()
        try:
            expect(page.locator(prefix + "batch_table")).to_contain_text("blue subject", timeout=30000)
        except AssertionError:
            print("BATCH STATUS:", page.locator(prefix + "batch_status").inner_text(), flush=True)
            page.screenshot(path=str(artifacts / "failure.png"), full_page=True)
            raise
        expect(page.locator(prefix + "batch_table")).to_contain_text("broken.png")
        status = page.locator(prefix + "batch_status")
        expect(status).to_contain_text("已完成 2", timeout=10000)
        expect(status).to_contain_text("未处理 0")
        expect(status).to_contain_text("失败 1")
        baseline = page.locator("#txt2img_prompt textarea").input_value()
        page.locator(prefix + "batch_send").click()
        native = page.locator("#txt2img_script_prompt_txt textarea")
        expect(native).to_have_value("--prompt 'red subject'\n--prompt 'blue subject'", timeout=10000)
        expect(page.locator("#txt2img_prompt textarea")).to_have_value(baseline)
        expect(page.locator("#txt2img_script input")).to_have_value("Prompts from File or Textbox")
        page.locator(prefix + "batch_save").click()
        expect(page.locator(prefix + "batch_output_status")).to_contain_text("缓存", timeout=10000)
        page.locator("#wd14_test_refresh_cache").click()
        expect(page.locator("#wd14_test_cache_count input")).to_have_value("3")
        page.get_by_role("tab", name="单图", exact=True).click()
        page.locator(prefix + "send").click()
        expect(page.locator("#txt2img_script input")).to_have_value("None")
        expect(page.locator("#txt2img_prompt textarea")).to_have_value("fixed subject, red subject, red subject")
        page.locator("#txt2img_script input").click()
        page.get_by_role("option", name="Other script", exact=True).click()
        page.locator(prefix + "send").click()
        expect(page.locator("#txt2img_prompt textarea")).to_have_value("fixed subject, red subject, red subject, red subject")
        expect(page.locator("#txt2img_script input")).to_have_value("Other script")
        expect(page.locator("#wd14_test_generations input")).to_have_value("0")
        page.get_by_role("tab", name="文件夹批量", exact=True).click()
        page.locator(prefix + "tab").screenshot(path=str(artifacts / "desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator(prefix + "batch_send")).to_be_visible()
        dimensions = page.evaluate("({client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
        assert dimensions["scroll"] <= dimensions["client"] + 1, dimensions
        page.locator(prefix + "tab").screenshot(path=str(artifacts / "mobile.png"))
        assert not errors, errors
        browser.close()
        print("PASS: single tagging, cache/write destinations, recursive folders, corrupt image, native batch handoff; no generation")


if __name__ == "__main__":
    main()
