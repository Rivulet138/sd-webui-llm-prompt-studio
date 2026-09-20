import ast
import json
import random
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from scripts.prompt_studio_core import StudioDB


class WildcardCategoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "lexicon"
        self.source.mkdir()
        self.db = StudioDB(self.root / "test.db")

    def write(self, name, text):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_preserves_folders_and_stems_and_filters_folder(self):
        self.write("people/hair/colors.txt", "blue hair\nblack hair\n")
        self.write("people/eyes/colors.TXT", "blue eyes\n")
        self.write("setting.txt", "blue sky\n")
        self.assertEqual(self.db.index_wildcards(self.source), (3, 4))
        self.assertEqual(self.db.wildcard_categories(), ["people/eyes/colors", "people/hair/colors", "setting"])
        self.assertEqual(set(self.db.wildcard_matches("blue", category="people")), {"blue hair", "blue eyes"})
        self.assertEqual(self.db.wildcard_matches("", category="people/hair/colors"), ["blue hair", "black hair"])
        self.assertEqual(self.db.index_wildcards(self.source), (0, 4))

    def test_structured_formats_parse_values_instead_of_syntax(self):
        self.write("poses.csv", 'tag,weight,translation\n"looking back, waving",2,hello\nstanding,1,world\n')
        self.write("scenes.json", json.dumps({"indoor": ["library", "studio"], "outdoor": ["forest"]}))
        self.db.index_wildcards(self.source)
        self.assertEqual(set(self.db.wildcard_matches("", category="poses")), {"looking back, waving", "standing"})
        self.assertEqual(self.db.wildcard_matches("", category="scenes/outdoor"), ["forest"])
        self.assertIn("scenes/indoor", self.db.wildcard_categories())

    def test_yaml_when_available(self):
        try:
            import yaml  # noqa: F401 - check optional runtime support
        except ImportError:
            self.skipTest("PyYAML is not installed")
        self.write("clothes.yaml", "casual:\n  - shirt\n  - jeans\n")
        self.db.index_wildcards(self.source)
        self.assertEqual(set(self.db.wildcard_matches("", category="clothes/casual")), {"shirt", "jeans"})

    def test_sampling_is_bounded_and_supports_category_filter(self):
        for index in range(70):
            self.write(f"group/{index}.txt", f"term {index}\nextra {index}\n")
        self.db.index_wildcards(self.source)
        sample = self.db.wildcard_samples_by_category(12)
        self.assertLessEqual(len(sample), 12)
        self.assertLessEqual(sum(map(len, sample.values())), 30)
        self.assertEqual(self.db.wildcard_samples_by_category(2, exclude=["term 1"], categories=["group/1"]), {"group/1": ["extra 1"]})

    def test_failed_reindex_preserves_old_index_transactionally(self):
        self.write("terms.json", '["valid"]')
        self.write("removed.txt", "removed")
        self.db.index_wildcards(self.source)
        self.write("terms.json", '{ invalid json contents')
        (self.source / "removed.txt").unlink()
        with self.assertRaisesRegex(ValueError, "terms.json"):
            self.db.index_wildcards(self.source)
        self.assertEqual(set(self.db.wildcard_matches("")), {"valid", "removed"})
        (self.source / "terms.json").unlink()
        self.db.index_wildcards(self.source)
        self.assertEqual(self.db.wildcard_categories(), [])

    def test_oversized_file_and_missing_yaml_report_filename(self):
        self.write("large.txt", "too large")
        with mock.patch.object(self.db, "MAX_WILDCARD_FILE_BYTES", 2):
            with self.assertRaisesRegex(ValueError, "large.txt.*exceeds"):
                self.db.index_wildcards(self.source)
        (self.source / "large.txt").unlink()
        self.write("nested/terms.yaml", "- blue hair")
        with mock.patch.dict("sys.modules", {"yaml": None}):
            with self.assertRaisesRegex(ValueError, "nested/terms.yaml.*yaml"):
                self.db.index_wildcards(self.source)

    def ui_functions(self):
        source = Path(__file__).resolve().parents[1] / "scripts" / "prompt_studio_ui.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names = {"_static_prompt_reference", "_build_inspiration_sources", "_parse_batch_sources", "_normalize_generation_count"}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {"DB": self.db, "random": random, "_INSPIRATION_TOPIC_POOL": ["test topic"], "_INSPIRATION_VARIATION_HINTS": ["test variation"]}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102 - execute only local AST-selected test subjects
        return namespace

    def test_generation_reference_uses_real_category_labels_and_terms(self):
        self.write("人物/头发/颜色.txt", "blue hair")
        self.db.index_wildcards(self.source)
        functions = self.ui_functions()
        reference = functions["_static_prompt_reference"]("unrelated", related_limit=0)
        self.assertEqual(len(reference), 1)
        self.assertIn("人物/头发/颜色", reference[0])
        self.assertIn("blue hair", reference[0])
        sources, _ = functions["_build_inspiration_sources"]("", generation_count=1)
        self.assertIn("人物/头发/颜色", sources[0])
        self.assertIn("blue hair", sources[0])
        self.assertEqual(functions["_static_prompt_reference"]("unrelated", exclude_terms=["blue hair"]), [])

    def test_generation_reference_total_is_bounded(self):
        for index in range(70):
            self.write(f"custom/{index}.txt", f"term {index}\nextra {index}")
        self.db.index_wildcards(self.source)
        functions = self.ui_functions()
        reference = functions["_static_prompt_reference"]("unrelated", related_limit=200, sample_limit=200)
        self.assertGreater(len(reference), 0)
        self.assertLessEqual(len(reference), 30)
        self.assertTrue(all("[custom/" in item for item in reference))

    def test_legacy_index_is_upgraded_even_when_mtime_is_unchanged(self):
        self.write("folder/terms.txt", "original")
        path = self.source / "folder/terms.txt"
        with sqlite3.connect(self.db.path) as connection:
            connection.execute("INSERT INTO wildcard_files(path, modified_at, terms_json) VALUES(?,?,?)", (str(path.resolve()), path.stat().st_mtime, '["original"]'))
        connection.close()
        self.db.index_wildcards(self.source)
        self.assertEqual(self.db.wildcard_categories(), ["folder/terms"])

    def test_changing_root_recomputes_relative_categories(self):
        self.write("folder/terms.txt", "original")
        self.db.index_wildcards(self.source)
        self.db.index_wildcards(self.source / "folder")
        self.assertEqual(self.db.wildcard_categories(), ["terms"])

    def test_existing_database_schema_migrates_without_losing_terms(self):
        legacy_path = self.root / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute("CREATE TABLE wildcard_files (path TEXT PRIMARY KEY, modified_at REAL NOT NULL, terms_json TEXT NOT NULL)")
            connection.execute("INSERT INTO wildcard_files VALUES ('terms.txt', 1, '[\"original\"]')")
            connection.commit()
        finally:
            connection.close()
        legacy = StudioDB(legacy_path)
        self.assertEqual(legacy.wildcard_matches(""), ["original"])


if __name__ == "__main__":
    unittest.main()
