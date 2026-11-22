"""Prompt-page controls and backend callback wiring."""
import ast
from collections import Counter
from pathlib import Path
import tempfile
import unittest

from test_queue_regressions import ROOT, StudioDB, ui_functions


SOURCE = (ROOT / "scripts/prompt_studio_ui.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
UI = next(node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name == "on_ui_tabs")
KEYS = (
    "recipe_enabled", "recipe_dimensions", "retry_limit", "similarity_threshold",
    "memory_enabled", "batch_temperature_floor", "source_fold_duplicates",
)
CONTROLS = ["diversity_" + key for key in KEYS]


class DiversityUiContractTests(unittest.TestCase):
    def test_prompt_tab_constructs_controls_from_persisted_settings(self):
        prompt_tab = next(
            node for node in ast.walk(UI) if isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and any(keyword.arg == "elem_id" and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value == "llm_prompt_studio_prompt_tab"
                        for keyword in item.context_expr.keywords)
                for item in node.items
            )
        )
        assignments = {
            target.id: node.value for node in ast.walk(prompt_tab) if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        }
        self.assertEqual(ast.unparse(assignments["diversity_initial"]), "_diversity_settings()")
        for key, name in zip(KEYS, CONTROLS):
            with self.subTest(control=name):
                component = assignments[name]
                self.assertIn(ast.unparse(component.func), ("gr.Number", "gr.Checkbox"))
                value = next(keyword.value for keyword in component.keywords if keyword.arg == "value")
                self.assertEqual(ast.unparse(value), f"diversity_initial['{key}']")

    def test_save_reset_report_and_reload_bind_the_correct_controls(self):
        assignments = {
            target.id: node.value for node in ast.walk(UI) if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        }
        self.assertEqual([node.id for node in assignments["diversity_controls"].elts], CONTROLS)
        calls = {ast.unparse(node.func): node for node in ast.walk(UI) if isinstance(node, ast.Call)}
        for event, function, inputs, outputs in (
            ("diversity_save.click", "_save_diversity_settings", "diversity_controls", "diversity_status"),
            ("diversity_reset.click", "_reset_diversity_settings", None, "[*diversity_controls, diversity_status]"),
            ("diversity_report_button.click", "_diversity_report_markdown", None, "diversity_report"),
        ):
            with self.subTest(event=event):
                call = calls[event]
                self.assertEqual(ast.unparse(call.args[0]), function)
                arguments = {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords}
                self.assertEqual(arguments.get("inputs"), inputs)
                self.assertEqual(arguments["outputs"], outputs)
        reload_call = next(
            node for node in ast.walk(UI) if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "ui.load" and node.args
            and ast.unparse(node.args[0]) == "_load_diversity_controls"
        )
        self.assertEqual(next(ast.unparse(keyword.value) for keyword in reload_call.keywords
                              if keyword.arg == "outputs"), "diversity_controls")

    def test_saved_values_reload_and_reset_in_ui_order(self):
        with tempfile.TemporaryDirectory() as directory:
            api = ui_functions(
                "_coerce_bool", "_diversity_int", "_diversity_float", "_diversity_settings",
                "_save_diversity_settings", "_load_diversity_controls", "_reset_diversity_settings",
                constants=("_DIVERSITY_SETTINGS_KEY", "_DIVERSITY_SETTINGS_DEFAULTS",
                           "_DIVERSITY_CONTROL_KEYS", "_MAX_CONTENT_SIMILARITY",
                           "_MAX_DIVERSITY_RETRIES", "_DIVERSITY_MEMORY_SIZE"),
                DB=StudioDB(Path(directory) / "settings.db"), _safe_error=str,
            )
            self.assertEqual(api._DIVERSITY_CONTROL_KEYS, KEYS)
            values = (True, 4, 2, 0.6, False, True, True)
            self.assertIn("已保存", api._save_diversity_settings(*values))
            self.assertEqual(api._load_diversity_controls(), values)
            *defaults, message = api._reset_diversity_settings()
            self.assertIn("已恢复默认值", message)
            self.assertEqual(api._load_diversity_controls(), tuple(defaults))

    def test_main_page_component_ids_are_unique(self):
        ids = Counter(
            keyword.value.value for node in ast.walk(UI) if isinstance(node, ast.Call)
            for keyword in node.keywords if keyword.arg == "elem_id"
            and isinstance(keyword.value, ast.Constant)
        )
        self.assertEqual({name: count for name, count in ids.items() if count > 1}, {})

    def test_readme_points_to_the_actual_control(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn('「提示词」页的“批量多样性”中启用词库配方', readme)


if __name__ == "__main__":
    unittest.main()
