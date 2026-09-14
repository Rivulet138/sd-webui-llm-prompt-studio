"""Isolated real-Gradio Studio harness; never starts the server queue worker."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7863)
    args = parser.parse_args()
    import prompt_studio_core as core

    with tempfile.TemporaryDirectory(prefix="studio-gradio-audit-") as folder:
        root = Path(folder)
        db = core.StudioDB(root / "studio.db")
        credentials = core.CredentialStore(root / "credentials.json")
        with patch.object(core, "StudioDB", return_value=db), patch.object(
            core, "CredentialStore", return_value=credentials
        ):
            import prompt_studio_ui as ui
        app = ui.on_ui_tabs()[0][0]
        app.queue().launch(server_name="127.0.0.1", server_port=args.port,
                           inbrowser=False, show_error=True)


if __name__ == "__main__":
    main()
