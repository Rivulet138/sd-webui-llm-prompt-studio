"""Tests for the standalone diversity metrics module (R3).

The metrics must stay dependency-light and, more importantly, must measure the
same thing the runtime duplicate gate does. The last test guards that invariant.
"""
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from test_queue_regressions import ROOT, StudioDB

import prompt_studio_diversity as metrics


class MetricBasicsTests(unittest.TestCase):
    def test_distinct_n_falls_when_items_repeat(self):
        repeated = [["a", "b", "c", "d"], ["a", "b", "c", "d"]]
        varied = [["a", "b", "c", "d"], ["e", "f", "g", "h"]]
        self.assertAlmostEqual(metrics.distinct_n(repeated, 2), 0.5)
        self.assertAlmostEqual(metrics.distinct_n(varied, 2), 1.0)

    def test_self_bleu_separates_copies_from_distinct_items(self):
        copied = [["a", "b", "c", "d"], ["a", "b", "c", "d"]]
        distinct = [["a", "b", "c", "d"], ["e", "f", "g", "h"]]
        self.assertGreater(metrics.self_bleu(copied, 2), 0.9)
        self.assertLess(metrics.self_bleu(distinct, 2), 0.1)

    def test_vendi_is_one_for_copies_and_n_for_orthogonal_items(self):
        identical = ["kitchen wooden spoon cream"] * 4
        self.assertAlmostEqual(metrics.vendi_score(identical), 1.0, places=6)
        orthogonal = [["kitchen"], ["forest"], ["harbour"], ["attic"]]
        self.assertAlmostEqual(metrics.vendi_score(orthogonal), 4.0, delta=0.05)

    def test_content_cosine_ignores_the_shared_skeleton(self):
        left = "A young girl with soft animal ears and a fluffy tail stands in a wooden kitchen."
        restyled = "A single girl with fluffy ears and a soft tail is standing in a wooden kitchen."
        elsewhere = "A girl with animal ears crouches on a stone street at midnight."
        self.assertGreater(
            metrics.content_cosine(left, restyled), metrics.content_cosine(left, elsewhere))

    def test_duplicate_clusters_groups_near_copies_only(self):
        texts = ["warm wooden kitchen with a spoon", "warm wooden kitchen and a spoon",
                 "rain-soaked street at midnight"]
        clusters = metrics.duplicate_clusters(texts, 0.5)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(sorted(clusters[0]), [0, 1])


class ReportTests(unittest.TestCase):
    def test_report_contains_every_metric_and_reads_sensibly(self):
        texts = [
            "a young girl stands in a wooden kitchen holding a spoon",
            "a young girl stands in a wooden kitchen with a spoon in hand",
            "a knight crouches on a stone bridge under heavy rain",
        ]
        report = metrics.diversity_report(texts)
        for key in ("count", "scored", "distinct_1", "distinct_2", "distinct_3",
                    "self_bleu_3", "self_bleu_4", "vendi", "vendi_ratio",
                    "mean_content_cosine", "duplicate_clusters", "duplicate_items"):
            self.assertIn(key, report)
        self.assertEqual(report["count"], 3)
        self.assertGreaterEqual(report["vendi"], 1.0)
        self.assertLessEqual(report["vendi"], 3.0)
        self.assertEqual(report["duplicate_items"], 1)
        self.assertIn("Vendi", metrics.format_report(report))

    def test_cli_reads_a_file_and_emits_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prompts.txt"
            path.write_text(
                "a young girl stands in a wooden kitchen\n"
                "# comment lines are ignored\n"
                "a knight crouches on a stone bridge\n",
                encoding="utf-8",
            )
            buffer = StringIO()
            with patch("sys.stdout", buffer):
                code = metrics.main(["--file", str(path), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(buffer.getvalue())
            self.assertEqual(payload["count"], 2)

    def test_cli_reads_a_studio_database_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = StudioDB(Path(tmp) / "studio.db")
            database.save_prompt("a young girl stands in a wooden kitchen")
            database.save_prompt("a knight crouches on a stone bridge")
            before = (Path(tmp) / "studio.db").read_bytes()
            texts = metrics.load_prompts_from_db(Path(tmp) / "studio.db")
            self.assertEqual(len(texts), 2)
            self.assertEqual((Path(tmp) / "studio.db").read_bytes(), before)


class SingleSourceOfTruthTests(unittest.TestCase):
    def test_stop_tokens_include_the_skeleton(self):
        self.assertTrue(metrics.SKELETON_TOKENS)
        self.assertTrue(metrics.SKELETON_TOKENS <= metrics.STOP_TOKENS)

    def test_ui_module_does_not_redefine_the_vocabulary(self):
        """The gate and the report must import one copy, not keep two."""
        source = (ROOT / "scripts/prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("_DIVERSITY_STOP_TOKENS = frozenset(", source)
        self.assertNotIn("_DIVERSITY_CONCEPT_ALIASES = {", source)
        self.assertIn("from prompt_studio_diversity import", source)


if __name__ == "__main__":
    unittest.main()
