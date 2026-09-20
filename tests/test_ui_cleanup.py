import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.modules.setdefault("gradio", types.ModuleType("gradio"))
import prompt_studio_core as core
import prompt_studio_ui as ui


class UICleanupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = core.StudioDB(Path(folder.name) / "cache.db")
        for patch in (mock.patch.object(ui, "DB", self.db),
                      mock.patch.object(ui.gr, "update", lambda **kw: kw, create=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_system_editor_shows_saved_custom_content_or_effective_preset(self):
        self.assertEqual(ui._system_prompt_editor_value("Danbooru Tags", "custom instructions"), "custom instructions")
        self.assertEqual(ui._system_prompt_editor_value("Danbooru Tags", ""), core.PRESETS["Danbooru Tags"])
        self.assertEqual(ui._system_prompt_editor_value("Krea 2 Natural"), core.PRESETS["Krea 2 Natural"])

    def test_builtin_text_is_not_a_custom_override_and_aliases_show_real_content(self):
        for preset in ("Danbooru Tags", "Pony / Illustrious Tags", "Flux Natural"):
            text = ui._system_prompt_editor_value(preset)
            self.assertTrue(text)
            self.assertEqual(ui._system_prompt_override_value(preset, text), "")
        self.assertEqual(ui._system_prompt_override_value("Flux Natural", "custom directions"), "custom directions")

    def test_builtin_editor_tracks_model_alignment_without_overwriting_custom_text(self):
        text = ui._system_prompt_editor_value("Danbooru Tags", "", "Flux")
        self.assertEqual(text, core.PRESETS["Natural Language"])
        self.assertEqual(ui._system_prompt_override_value("Danbooru Tags", text, "Flux"), "")
        self.assertEqual(ui._system_prompt_editor_value("Danbooru Tags", "custom directions", "Flux"), "custom directions")

    def test_reload_restores_saved_system_text_and_reset_restores_defaults(self):
        ui._save_workflow_values({"preset": "Danbooru Tags", "base_model": "Flux", "system_override": "saved directions"})
        self.assertEqual(ui._load_system_prompt_editor(), ("Danbooru Tags", "Flux", "saved directions", "saved directions"))
        ui._reset_workflow_settings()
        self.assertEqual(ui._load_system_prompt_editor()[2], "")
        self.assertTrue(ui._load_system_prompt_editor()[3])

    def test_compact_cache_picker_pages_and_loads_stable_ids(self):
        self.db.save_prompts_batch([{"prompt": f"cat {i}"} for i in range(57)])
        self.db.save_prompt("dog")
        choice, message, page, record, text = ui._cache_picker_view("cat", "全部", "全部", 2)
        self.assertEqual(page, 2)
        self.assertEqual(len(choice["choices"]), 7)
        self.assertEqual(record, {})
        self.assertEqual(text, "")
        self.assertIn("57", message)
        ident = choice["choices"][0][1]
        selected, prompt = ui._cache_picker_select(ident)
        self.assertEqual(str(selected["id"]), ident)
        self.assertEqual(prompt, self.db.get_prompt(int(ident))["prompt"])

    def test_picker_empty_and_deleted_selection_are_safe(self):
        choice, _, page, record, text = ui._cache_picker_view("missing", "全部", "全部", 99)
        self.assertEqual(choice["choices"], [])
        self.assertEqual(page, 1)
        self.assertEqual((record, text), ({}, ""))
        self.assertEqual(ui._cache_picker_select("999"), ({}, ""))
        self.assertEqual(ui._cache_picker_select(None), ({}, ""))


if __name__ == "__main__":
    unittest.main()
