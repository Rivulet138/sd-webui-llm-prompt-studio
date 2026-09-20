"""Isolated real-Gradio Studio harness; never starts the server queue worker."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7863)
    parser.add_argument("--seed-cache", type=int, default=0)
    parser.add_argument("--inline", action="store_true", help="Render the txt2img entry above Studio")
    parser.add_argument("--mock-llm", action="store_true", help="Use a deterministic local transform for UI verification")
    parser.add_argument("--mock-delay", type=float, default=0, help="Delay each mock LLM response for stop-button checks")
    args = parser.parse_args()
    import prompt_studio_core as core

    with tempfile.TemporaryDirectory(prefix="studio-gradio-audit-") as folder:
        root = Path(folder)
        db = core.StudioDB(root / "studio.db")
        processed_db = core.StudioDB(root / "processed_prompt_cache.db")
        for index in range(args.seed_cache):
            db.save_prompt(
                f"scene {index + 1}, " + "long descriptive prompt, " * (10 + index % 40),
                "blur, low quality", "Danbooru Tags", "SDXL", 0, "scene, landscape",
                source_kind="browser-test", source_ref=str(index + 1),
            )
        if args.seed_cache:
            db.index_wildcards(core.DEFAULT_WILDCARDS)
        credentials = core.CredentialStore(root / "credentials.json")
        with patch.object(core, "StudioDB", side_effect=[db, processed_db]), patch.object(
            core, "CredentialStore", return_value=credentials
        ):
            import prompt_studio_ui as ui
        ui._ensure_server_queue_worker = lambda: None
        if args.mock_llm:
            generated = iter(range(1, 10001))
            def mock_llm(*call_args, **kwargs):
                if args.mock_delay:
                    time.sleep(max(0, min(args.mock_delay, 2)))
                return f"garden scene {next(generated)}, trees, soft daylight, detailed landscape"
            ui.call_llm = mock_llm
            ui._inline_cache_transform = lambda source, *args: (source + ", soft daylight", "ok")
            ui._expand_or_polish = lambda source, *args, **kwargs: (source + ", soft daylight", "ok")
            ui._ranbooru_load = lambda *args: {"records": [
                {"prompt": "a green garden", "_ranbooru_id": "42", "_ranbooru_variant": "natural"},
                {"prompt": "a sandy beach", "_ranbooru_id": "43", "_ranbooru_variant": "natural"},
            ]}
        if args.inline:
            import gradio as gr
            with gr.Blocks(analytics_enabled=False, css=ui.UI_CSS) as app:
                prompt = gr.Textbox(label="文生图正面 Prompt", elem_id="txt2img_prompt", value="fixed subject")
                ui.capture_prompt_component(prompt, elem_id="txt2img_prompt")
                gr.Textbox(
                    value=json.dumps({"schema_version": "prompt_batch.v1", "records": [
                        {"record_id": "png-1", "image": {"filename": "garden.png"}, "prompt": {"positive": "a quiet garden"}},
                        {"record_id": "png-2", "image": {"filename": "beach.png"}, "prompt": {"positive": "a sunny beach"}},
                    ]}),
                    elem_id="ppc_prompt_batch_cache",
                )
                ui._create_inline_panel("txt2img", prompt)
                ui.on_ui_tabs()
        else:
            app = ui.on_ui_tabs()[0][0]
        app.queue().launch(server_name="127.0.0.1", server_port=args.port,
                           inbrowser=False, show_error=True)


if __name__ == "__main__":
    main()
