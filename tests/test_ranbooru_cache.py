"""Regression tests for Ranbooru source selection and metadata preservation."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from prompt_studio_core import load_ranbooru_cache


class RanbooruCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "tag_cache.db"
        connection = sqlite3.connect(self.path)
        connection.execute(
            """
            CREATE TABLE tags (
                id INTEGER PRIMARY KEY,
                tags_prompt TEXT,
                tags_raw TEXT,
                natural_prompt TEXT,
                natural_source_hash TEXT,
                score TEXT,
                rating TEXT,
                booru TEXT,
                post_id TEXT,
                search_query TEXT
            )
            """
        )
        rows = [
            (1, "old, low_score", "", "", "", "10", "g", "danbooru", "101", ""),
            (2, "high, explicit", "", "", "", "900", "e", "danbooru", "102", ""),
            (3, "high, safe", "", "", "", "900", "g", "konachan", "103", ""),
            (4, "middle, safe", "", "", "", "500", "g", "danbooru", "104", ""),
        ]
        connection.executemany(
            "INSERT INTO tags VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        connection.commit()
        connection.close()

    def test_source_limit_prefers_high_score_and_stable_id_order(self):
        result = load_ranbooru_cache(
            self.path,
            content_mode="tags",
            source_limit=2,
        )

        self.assertEqual(result["loaded_sources"], 2)
        self.assertEqual([record["_ranbooru_id"] for record in result["records"]], ["2", "3"])
        self.assertEqual([record["_ranbooru_score"] for record in result["records"]], [900, 900])
        self.assertEqual(result["records"][0]["_ranbooru_rating"], "e")
        self.assertEqual(result["records"][1]["source_ref"].split(":")[-1], "tags")

    def test_score_and_rating_filters_apply_before_high_score_limit(self):
        result = load_ranbooru_cache(
            self.path,
            content_mode="tags",
            rating_filter="sfw",
            min_source_score=400,
            source_limit=1,
        )

        self.assertEqual(result["total_sources"], 2)
        self.assertEqual(result["loaded_sources"], 1)
        self.assertEqual(result["records"][0]["_ranbooru_id"], "3")
        self.assertEqual(result["records"][0]["_ranbooru_rating"], "g")


if __name__ == "__main__":
    unittest.main()
