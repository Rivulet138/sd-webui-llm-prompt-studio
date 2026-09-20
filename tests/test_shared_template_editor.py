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


class Component:
    def input(self, fn, **kwargs):
        self.callback = fn
        self.outputs = kwargs["outputs"]

    click = input


def panel():
    return dict(choice=Component(), fields=[Component() for _ in range(9)],
                save=Component(), delete=Component(), default=Component(), status=Component())


class SharedTemplateEditorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = core.StudioDB(Path(temporary.name) / "templates.db")
        for patch in (
            mock.patch.object(ui, "DB", self.db),
            mock.patch.object(ui.gr, "update", lambda **values: values, create=True),
            mock.patch.object(ui, "_INLINE_TEMPLATE_COMPONENTS", {}),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.studio, self.inline = panel(), panel()
        ui._bind_template_editors([self.studio, self.inline])

    def test_save_from_either_editor_updates_both_even_when_name_unchanged(self):
        for source, content in ((self.inline, "night scene"), (self.studio, "updated night scene")):
            result = source["save"].callback("夜景", content)
            self.assertEqual(len(result), len(source["save"].outputs))
            self.assertEqual(result[:11], result[11:])
            self.assertIsNot(result[1], result[12])
            self.assertEqual(result[1]["value"], "custom:夜景")
            self.assertEqual(result[2:4], ("夜景", content))
            self.assertEqual(self.db.get_setting(ui.CUSTOM_TEMPLATES_SETTING)["夜景"]["content"], content)

    def test_selection_synchronizes_structured_editor_values(self):
        self.inline["save"].callback("夜景", "night", False, False, "camera", "film", "text", "allow", "accept")
        result = self.studio["choice"].callback("custom:夜景")
        self.assertEqual(result[2:11], ("夜景", "night", False, False, "camera", "film", "text", "allow", "accept"))
        self.assertEqual(result[:11], result[11:])

    def test_delete_default_resets_both_editors_and_persisted_default(self):
        self.inline["save"].callback("夜景", "night")
        self.inline["default"].callback("custom:夜景")
        result = self.studio["delete"].callback("custom:夜景")
        self.assertEqual(result[1]["value"], "general")
        self.assertEqual(result[:11], result[11:])
        self.assertEqual(self.db.get_setting(ui.DEFAULT_TEMPLATE_SETTING), "general")
        self.assertNotIn("夜景", ui._custom_templates())

    def test_invalid_save_keeps_unsaved_editor_content(self):
        result = self.inline["save"].callback("", "unfinished")
        self.assertNotIn("value", result[1])
        self.assertEqual(result[2:11], ({},) * 9)
        self.assertEqual(ui._custom_templates(), {})

    def test_builtin_can_be_copied_and_saved_without_overwriting_builtin(self):
        result = self.inline["choice"].callback("general")
        self.assertEqual(result[2], "")
        self.assertEqual(result[3], ui.GENERAL_CREATIVE_REQUEST_TEMPLATE)
        self.inline["save"].callback("我的模板", result[3] + "\nnight")
        self.assertEqual(ui._template_request("general"), ui.GENERAL_CREATIVE_REQUEST_TEMPLATE)
        self.assertIn("night", ui._template_request("custom:我的模板"))

    def test_callbacks_keep_output_arity_after_registry_changes(self):
        ui._INLINE_TEMPLATE_COMPONENTS["new-panel"] = Component()
        result = self.inline["save"].callback("夜景", "night")
        self.assertEqual(len(result), 22)


if __name__ == "__main__":
    unittest.main()
