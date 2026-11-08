"""Tests for canonicalizing Danbooru tags using tagcomplete CSV data."""
import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from prompt_studio_core import (
    booru_alias_to_canonical,
    canonicalize_booru_tags,
    discover_booru_vocabulary_path,
    load_booru_vocabulary,
    process_tags,
)


class BooruVocabularyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dictionary = Path(self.tmp.name) / "danbooru.csv"
        self.dictionary.write_text(
            '1girl,0,10,"1girls,sole_female"\n'
            'red_hair,0,10,"red hair"\n'
            'long_hair,0,10,longhair\n',
            encoding="utf-8",
        )
        self.vocabulary = load_booru_vocabulary(self.dictionary)

    def test_loads_canonical_tags_and_aliases(self):
        self.assertEqual(booru_alias_to_canonical("1girl", self.vocabulary), "1girl")
        self.assertEqual(booru_alias_to_canonical("sole_female", self.vocabulary), "1girl")
        self.assertEqual(booru_alias_to_canonical("longhair", self.vocabulary), "long_hair")

    def test_lookup_is_case_insensitive_and_normalizes_spaces_to_underscores(self):
        self.assertEqual(booru_alias_to_canonical("SOLE_FEMALE", self.vocabulary), "1girl")
        self.assertEqual(booru_alias_to_canonical("RED_HAIR", self.vocabulary), "red_hair")
        self.assertEqual(booru_alias_to_canonical("red hair", self.vocabulary), "red_hair")

    def test_canonicalization_preserves_unknowns_and_reports_diagnostics(self):
        result = canonicalize_booru_tags(
            "1girl, sole_female, mystery tag", vocabulary=self.vocabulary
        )
        self.assertEqual(result["tags"], ["1girl", "1girl", "mystery tag"])
        self.assertEqual(result["text"], "1girl, 1girl, mystery tag")
        self.assertEqual(
            result["unknown"],
            [{"index": 2, "tag": "mystery tag", "normalized": "mystery_tag"}],
        )

    def test_list_input_is_supported_and_implications_are_not_expanded(self):
        result = canonicalize_booru_tags(["1girl", "red hair"], vocabulary=self.vocabulary)
        self.assertEqual(result["tags"], ["1girl", "red_hair"])
        self.assertEqual(result["unknown"], [])

    def test_missing_dictionary_keeps_tags_and_reports_all_as_unknown(self):
        missing = Path(self.tmp.name) / "missing.csv"
        self.assertIsNone(discover_booru_vocabulary_path(missing))
        self.assertEqual(load_booru_vocabulary(missing), {})
        result = canonicalize_booru_tags(["Known", "mystery_tag"], dictionary_path=missing)
        self.assertEqual(result["tags"], ["Known", "mystery_tag"])
        self.assertFalse(result["dictionary_found"])
        self.assertEqual([item["tag"] for item in result["unknown"]], ["Known", "mystery_tag"])

    def test_existing_process_tags_behavior_is_unchanged(self):
        self.assertEqual(process_tags("RED_HAIR, red_hair"), "RED_HAIR")


if __name__ == "__main__":
    unittest.main()
