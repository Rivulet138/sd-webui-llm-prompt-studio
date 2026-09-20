"""Isolated real-Gradio inline panel for browser verification without a GPU."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7863)
    args = parser.parse_args()
    import gradio as gr
    import prompt_studio_core as core

    with tempfile.TemporaryDirectory(prefix="studio-inline-buttons-") as folder:
        databases = [core.StudioDB(Path(folder) / name) for name in ("studio.db", "processed.db")]
        with patch.object(core, "StudioDB", side_effect=databases), patch.object(
            core, "CredentialStore", return_value=core.CredentialStore(Path(folder) / "credentials.json")
        ):
            import prompt_studio_ui as ui
        bootstrap = """
        window.gradioApp = () => document;
        window.onAfterUiUpdate = (fn) => window.addEventListener('load', fn);
        window.submit = (...args) => {
            const id = 'task-' + Date.now();
            localStorage.setItem('txt2img_task_id', id);
            document.getElementById('txt2img_interrupt').style.display = 'block';
            window.requestProgress(id, null, null, () => {
                localStorage.removeItem('txt2img_task_id');
                document.getElementById('txt2img_interrupt').style.display = 'none';
            });
            args[0] = id;
            return args;
        };
        """
        js = (ROOT / "javascript" / "llm_prompt_studio_auto_loop.js").read_text(encoding="utf-8")
        with gr.Blocks(head=f"<script>{bootstrap}\n{js}</script>", css=ui.UI_CSS) as app:
            prompt = gr.Textbox(label="Prompt", elem_id="txt2img_prompt")
            ui._create_inline_panel("txt2img", prompt)
            generate = gr.Button("Generate", elem_id="txt2img_generate")
            interrupt = gr.Button("Interrupt", elem_id="txt2img_interrupt", visible=False)
            task_id = gr.Textbox(visible=False)
            status = gr.HTML("", elem_id="txt2img_status")
            generate.click(lambda *_: "done", inputs=[task_id, prompt], outputs=status, js="submit")
            interrupt.click(lambda: None, outputs=[])
        app.queue().launch(server_name="127.0.0.1", server_port=args.port, inbrowser=False)


if __name__ == "__main__":
    main()
