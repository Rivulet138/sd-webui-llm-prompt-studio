import ast
import unittest
from pathlib import Path


class WildcardSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "scripts" / "prompt_studio_ui.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_apply_wildcard_selection"]
        namespace = {}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102
        cls.apply = staticmethod(namespace["_apply_wildcard_selection"])

    def test_appends_selected_terms_to_fixed_prompt(self):
        prompt, status = self.apply("fixed subject", ["blue hair", "forest"])
        self.assertEqual(prompt, "fixed subject, blue hair, forest")
        self.assertIn("2", status)

    def test_empty_selection_keeps_prompt_unchanged(self):
        result = self.apply("fixed subject", [])
        self.assertEqual(result[0], "fixed subject")
        self.assertIn("未选择", result[1])

    def test_duplicate_and_blank_terms_are_ignored(self):
        prompt, _status = self.apply("", ["blue hair", "", "blue hair"])
        self.assertEqual(prompt, "blue hair")

    def test_preserves_fixed_prompt_whitespace(self):
        prompt, _status = self.apply("  fixed subject  ", ["forest"])
        self.assertEqual(prompt, "  fixed subject  , forest")


if __name__ == "__main__":
    unittest.main()
