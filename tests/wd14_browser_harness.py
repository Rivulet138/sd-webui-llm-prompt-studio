"""Real Gradio WD14 workflow harness with deterministic inference and isolated DBs."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7866)
    parser.add_argument("--no-models", action="store_true")
    args = parser.parse_args()
    import gradio as gr
    from PIL import Image
    import prompt_studio_core as core

    with tempfile.TemporaryDirectory(prefix="studio-wd14-browser-") as folder:
        root = Path(folder)
        images = root / "images"
        (images / "sub").mkdir(parents=True)
        Image.new("RGB", (16, 16), "red").save(images / "a.PNG")
        Image.new("RGB", (16, 16), "blue").save(images / "sub" / "b.jpg")
        (images / "sub" / "broken.png").write_bytes(b"not an image")
        databases = [core.StudioDB(root / name) for name in ("studio.db", "processed.db")]
        with patch.object(core, "StudioDB", side_effect=databases), patch.object(
            core, "CredentialStore", return_value=core.CredentialStore(root / "credentials.json")
        ):
            import prompt_studio_ui as ui

        model_path = str(root / "fixture.onnx")
        model_ready = not args.no_models
        model_label = "fixture"
        download_attempts = 0
        ui.discover_local_models = lambda *a, **kw: ([(model_label, model_path)], model_path) if model_ready else ([], None)

        def download_model(model_id, directory=""):
            nonlocal model_ready, model_label, download_attempts
            download_attempts += 1
            if download_attempts == 1:
                raise RuntimeError("fixture network failure")
            yield "正在下载模型…", None
            model_ready = True
            model_label = ui.MODEL_CATALOG[model_id]["label"]
            yield "模型下载完成。", model_path

        ui.download_model = download_model

        def interrogate(image, *args, **kwargs):
            if not isinstance(image, Image.Image):
                image = Image.fromarray(image)
            red, _, blue = image.convert("RGB").getpixel((0, 0))
            return ("red subject" if red > blue else "blue subject"), "识别完成"

        ui.interrogate_local = interrogate
        ui._ensure_server_queue_worker = lambda: (_ for _ in ()).throw(AssertionError("Unexpected render worker"))
        modules = ModuleType("modules")
        scripts = ModuleType("modules.scripts")
        modules.scripts = scripts
        with patch.dict(sys.modules, {"modules": modules, "modules.scripts": scripts}):
            with gr.Blocks(analytics_enabled=False, css=ui.UI_CSS) as app:
                prompt = gr.Textbox(value="fixed subject", elem_id="txt2img_prompt", label="Native Prompt")
                dropdown = gr.Dropdown(choices=["None", "Prompts from File or Textbox", "Other script"], value="None", elem_id="txt2img_script", label="Script")
                batch_text = gr.Textbox(elem_id="txt2img_script_prompt_txt", label="Native batch prompts")
                generate = gr.Button("Generate", elem_id="txt2img_generate")
                count = gr.Number(value=0, elem_id="wd14_test_generations")
                generate.click(lambda n: n + 1, count, count)
                script = SimpleNamespace(controls=[batch_text], title=lambda: "Prompts from File or Textbox")
                scripts.scripts_txt2img = SimpleNamespace(inputs=[dropdown], selectable_scripts=[script], title_map={"prompts from file or textbox": script})
                ui._PROMPT_TARGETS["txt2img"] = prompt
                gr.Textbox(value=str(images), elem_id="wd14_test_folder", label="Fixture folder")
                refresh_count = gr.Button("Read cache count", elem_id="wd14_test_refresh_cache")
                cache_count = gr.Number(value=0, elem_id="wd14_test_cache_count")
                refresh_count.click(lambda: len(databases[0].list_prompts(limit=None)), outputs=cache_count)
                read_downloads = gr.Button("Read download count", elem_id="wd14_test_read_downloads")
                download_count = gr.Number(value=0, elem_id="wd14_test_downloads")
                read_downloads.click(lambda: download_attempts, outputs=download_count)
                ui.on_ui_tabs()
            app.queue().launch(server_name="127.0.0.1", server_port=args.port, inbrowser=False, show_error=True)


if __name__ == "__main__":
    main()
