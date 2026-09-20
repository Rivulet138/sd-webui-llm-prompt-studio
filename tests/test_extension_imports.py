"""Exercise deferred callbacks after Forge restores its module search path."""
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = textwrap.dedent(r'''
    import importlib.util
    import os
    import sys
    import tempfile
    import types
    from pathlib import Path
    from unittest.mock import patch
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

    scripts = Path(sys.argv[1])
    real_gradio = sys.argv[2] == "real"
    if real_gradio:
        import gradio as gr
    else:
        gr = types.ModuleType("gradio")
        gr.update = lambda **values: values
        sys.modules["gradio"] = gr
    registered = {}
    callbacks = types.ModuleType("modules.script_callbacks")
    for name in ("on_ui_tabs", "on_app_started", "on_after_component", "on_before_component"):
        setattr(callbacks, name, lambda fn, name=name: registered.__setitem__(name, fn))
    modules = types.ModuleType("modules")
    modules.script_callbacks = callbacks
    sys.modules["modules"] = modules
    sys.modules["modules.script_callbacks"] = callbacks

    baseline = [p for p in sys.path if Path(p or ".").resolve() != scripts.resolve()]
    sys.path = [str(scripts), *baseline]
    import prompt_studio_core as core
    with tempfile.TemporaryDirectory(prefix="studio-import-test-") as folder:
        db = core.StudioDB(Path(folder) / "original.db")
        results = core.StudioDB(Path(folder) / "processed.db")
        credentials = core.CredentialStore(Path(folder) / "credentials.json")
        sys.path = list(baseline)
        # Forge temporarily installs a new path list per script, then restores it.
        sys.path = [str(scripts.parent), *sys.path]
        try:
            with patch.object(core, "StudioDB", side_effect=[db, results]), patch.object(core, "CredentialStore", return_value=credentials):
                spec = importlib.util.spec_from_file_location("llm_prompt_studio.py", scripts / "llm_prompt_studio.py")
                entry = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(entry)
        finally:
            sys.path = baseline
        ui = sys.modules["prompt_studio_ui"]
        assert all(Path(p or ".").resolve() != scripts.resolve() for p in sys.path)
        assert "tagger.utils" not in sys.modules, "Optional WD14 plugin must not load at entry import"
        with patch.object(sys.modules["prompt_studio_wd14"], "_model_roots", return_value=[Path(folder)]):
            assert ui._wd14_model_choices()[0].get("choices") == []
        assert ui._wd14_interrogate(None, "wd-vit-v3", 0.35)[0] == ""
        if real_gradio:
            tabs = registered["on_ui_tabs"]()
            assert len(tabs) == 1 and tabs[0][2] == "llm_prompt_studio"
            assert any(c.get("props", {}).get("elem_id") == "llm_prompt_studio_wd14_model"
                       for c in tabs[0][0].config["components"])
        assert ui._SERVER_QUEUE_THREAD is None
        print("PASS: callbacks after Forge path reset; optional WD14 absent; no queue startup")
''')


def run_probe(real_gradio=False):
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(ROOT / "scripts"), "real" if real_gradio else "stub"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout.strip()


class ExtensionImportTests(unittest.TestCase):
    def test_callbacks_work_after_forge_restores_sys_path(self):
        self.assertIn("PASS", run_probe())


if __name__ == "__main__":
    unittest.main()
