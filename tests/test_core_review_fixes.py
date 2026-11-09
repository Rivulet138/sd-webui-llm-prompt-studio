"""Regression coverage for core import, backup, ID batching and tag review fixes."""
import csv
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prompt_studio_core import StudioDB, build_provider_request, process_tags


class CoreReviewFixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = StudioDB(self.root / "prompt_studio.db")

    def write_import(self, records, suffix):
        path = self.root / f"import{suffix}"
        if suffix == ".json":
            path.write_text(json.dumps(records), encoding="utf-8")
        else:
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(records[0]))
                writer.writeheader()
                writer.writerows(records)
        return path

    def sourced(self, **changes):
        return {"prompt": "cat, red_hair", "source_kind": "ranbooru", "source_ref": "danbooru:17:tags", **changes}

    def seed_ids(self, count=130):
        return self.db.save_prompts_batch([{"prompt": f"prompt {index}"} for index in range(count)])["ids"]

    @contextmanager
    def limited_variables(self, db=None, limit=64):
        db = db or self.db
        original = db._connection

        @contextmanager
        def connection():
            with original() as conn:
                conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, limit)
                yield conn

        with patch.object(db, "_connection", connection):
            yield

    def test_json_csv_roundtrip_preserves_source_and_resets_untrusted_score_metadata(self):
        source_id = self.db.save_prompt("cat", score=99, score_source="llm", score_reason="verified",
                                        score_model="test", source_kind="ranbooru", source_ref="17:tags")
        for fmt in ("json", "csv"):
            with self.subTest(fmt=fmt):
                target = StudioDB(self.root / f"target_{fmt}.db")
                path = self.db.export_records(fmt, ids=[source_id])
                result = target.import_records(path)
                record = target.get_prompt(result["ids"][0])
                self.assertEqual((record["source_kind"], record["source_ref"]), ("ranbooru", "17:tags"))
                self.assertEqual((record["score_source"], record["score_reason"], record["score_model"]), ("manual", "", ""))
                self.assertEqual(target.import_records(path)["duplicates"], 1)

    def test_distinct_sources_with_identical_content_are_preserved(self):
        self.db.save_prompts_batch([{"prompt": "cat, red_hair"}])
        result = self.db.save_prompts_batch([self.sourced(), self.sourced(source_ref="18:tags")])
        self.assertEqual(result["inserted"], 2)
        self.assertEqual({self.db.get_prompt(ident)["source_ref"] for ident in result["ids"]}, {"danbooru:17:tags", "18:tags"})

    def test_source_identity_deduplicates_even_when_content_dedupe_disabled(self):
        result = self.db.save_prompts_batch([self.sourced(), self.sourced()], dedupe=False)
        self.assertEqual((result["inserted"], result["duplicates"]), (1, 1))
        again = self.db.save_prompts_batch([self.sourced()], dedupe=False)
        self.assertEqual((again["inserted"], again["duplicates"]), (0, 1))

    def test_invalid_source_metadata_is_rejected_without_partial_import(self):
        invalid = [
            {"source_kind": "ranbooru"}, {"source_ref": "17"},
            {"source_kind": "bad kind", "source_ref": "17"},
            {"source_kind": "x" * 81, "source_ref": "17"},
            {"source_kind": "ranbooru", "source_ref": "x" * 501},
            {"source_kind": ["ranbooru"], "source_ref": "17"},
            {"source_kind": "ranbooru", "source_ref": {"id": 17}},
        ]
        for suffix in (".json", ".csv"):
            for fields in invalid:
                # CSV cells are always strings; structured-type validation is JSON-only.
                if suffix == ".csv" and any(not isinstance(value, str) for value in fields.values()):
                    continue
                with self.subTest(suffix=suffix, fields=fields):
                    records = [{"prompt": "valid", "source_kind": "", "source_ref": ""},
                               {"prompt": "invalid", "source_kind": "", "source_ref": "", **fields}]
                    with self.assertRaisesRegex(ValueError, "source"):
                        self.db.import_records(self.write_import(records, suffix))
                    self.assertEqual(self.db.list_prompts(limit=None), [])

    def test_conflicting_source_is_explicit_and_whole_batch_rolls_back(self):
        self.db.save_prompts_batch([self.sourced()])
        for suffix in (".json", ".csv"):
            with self.subTest(suffix=suffix):
                records = [self.sourced(prompt="another", source_ref="other"), self.sourced(prompt="changed")]
                with self.assertRaisesRegex(ValueError, "[Cc]onflict.*ranbooru.*danbooru:17:tags"):
                    self.db.import_records(self.write_import(records, suffix))
                self.assertEqual([record["prompt"] for record in self.db.list_prompts(limit=None)], ["cat, red_hair"])

    def test_conflicting_sources_within_one_batch_roll_back(self):
        with self.assertRaisesRegex(ValueError, "[Cc]onflict.*ranbooru"):
            self.db.save_prompts_batch([self.sourced(), self.sourced(prompt="changed")])
        self.assertEqual(self.db.list_prompts(limit=None), [])

    def test_imported_sources_can_be_updated_by_existing_sync(self):
        path = self.write_import([self.sourced()], ".json")
        ident = self.db.import_records(path)["ids"][0]
        result = self.db.sync_external_prompts([self.sourced(prompt="changed")])
        self.assertEqual((result["inserted"], result["updated"], result["ids"]), (0, 1, [ident]))
        self.assertEqual(self.db.get_prompt(ident)["prompt"], "changed")

    def test_backup_retention_is_per_database_and_leaves_legacy_files(self):
        processed = StudioDB(self.root / "processed_prompt_cache.db")
        backups_dir = self.root / "backups"
        backups_dir.mkdir()
        legacy = backups_dir / "prompt_studio_20200101_000000_1_manual.db"
        legacy.write_bytes(b"unattributed legacy backup")
        for db in (self.db, processed):
            db.BACKUP_MAX_COUNT = 2
        raw_paths, processed_paths = [], []
        for _ in range(4):
            raw_paths.append(Path(self.db.backup_db()))
            processed_paths.append(Path(processed.backup_db()))
        self.assertTrue(legacy.exists())
        self.assertEqual([p.exists() for p in raw_paths], [False, False, True, True])
        self.assertEqual([p.exists() for p in processed_paths], [False, False, True, True])
        with closing(sqlite3.connect(raw_paths[-1])) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_backup_age_and_byte_limits_do_not_prune_other_database(self):
        other = StudioDB(self.root / "processed_prompt_cache.db")
        foreign = Path(other.backup_db())
        old = Path(self.db.backup_db())
        os.utime(old, (time.time() - 40 * 86400,) * 2)
        recent = Path(self.db.backup_db())
        self.assertFalse(old.exists())
        self.db.BACKUP_MAX_TOTAL_BYTES = recent.stat().st_size
        newest = Path(self.db.backup_db())
        self.assertFalse(recent.exists())
        self.assertTrue(newest.exists())
        self.assertTrue(foreign.exists())

    def test_backup_retains_latest_even_if_it_exceeds_byte_budget(self):
        self.db.BACKUP_MAX_TOTAL_BYTES = 1
        latest = Path(self.db.backup_db())
        self.assertTrue(latest.exists())

    def test_backups_distinguish_database_names_that_sanitize_identically(self):
        first = StudioDB(self.root / "cache-a.db")
        second = StudioDB(self.root / "cache_a.db")
        first.BACKUP_MAX_COUNT = second.BACKUP_MAX_COUNT = 1
        keep = Path(first.backup_db())
        second.backup_db()
        second.backup_db()
        self.assertTrue(keep.exists())

    def test_delete_chunks_ids_and_keeps_one_sorted_undo_journal(self):
        ids = self.seed_ids()
        with self.limited_variables():
            self.assertEqual(self.db.delete_prompts([*reversed(ids), ids[0], 999999]), len(ids))
            with self.db._connection() as conn:
                journals = conn.execute("SELECT * FROM deletion_journal").fetchall()
            self.assertEqual(len(journals), 1)
            payload = json.loads(journals[0]["records_json"])
            self.assertEqual([record["id"] for record in payload["records"]], ids)
            self.assertTrue(Path(payload["backup_path"]).exists())
            self.assertEqual(self.db.undo_last_delete(), len(ids))
        self.assertEqual(sorted(record["id"] for record in self.db.list_prompts(limit=None)), ids)

    def test_delete_later_chunk_failure_rolls_back_records_and_journal(self):
        ids = self.seed_ids()
        with self.db._connection() as conn:
            conn.execute(f"CREATE TRIGGER fail_delete BEFORE DELETE ON prompts WHEN OLD.id={ids[-1]} "
                         "BEGIN SELECT RAISE(ABORT, 'stop deletion'); END")
        with self.limited_variables(), self.assertRaisesRegex(sqlite3.IntegrityError, "stop deletion"):
            self.db.delete_prompts(ids)
        with self.db._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0], len(ids))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM deletion_journal").fetchone()[0], 0)

    def test_export_chunks_ids_and_preserves_global_order_in_json_csv(self):
        ids = self.seed_ids()
        for fmt in ("json", "csv"):
            with self.subTest(fmt=fmt), self.limited_variables():
                path = Path(self.db.export_records(fmt, ids=[*reversed(ids[1:]), ids[1], 999999]))
                if fmt == "json":
                    records = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual([record["id"] for record in records], ids[1:])
                else:
                    with path.open(encoding="utf-8-sig", newline="") as handle:
                        records = list(csv.DictReader(handle))
                self.assertEqual([record["prompt"] for record in records], [f"prompt {i}" for i in range(1, len(ids))])

    def test_tag_removal_normalizes_underscore_patterns_and_wildcards(self):
        for spaces in (False, True):
            with self.subTest(spaces=spaces):
                self.assertEqual(process_tags("red_hair, blue eyes, artist_name, cat", remove_bad=False,
                                              remove_terms="RED_HAIR, blue_*, artist name", underscores_to_spaces=spaces), "cat")

    def test_tag_removal_remains_literal_except_for_star(self):
        self.assertEqual(process_tags("tag_(test), tag_other, tag_test, blue_eyes", remove_bad=False,
                                      remove_terms="tag_(test), blue *"), "tag_other, tag_test")


class ProviderThinkingReviewTests(unittest.TestCase):
    def request(self, provider, model, **kwargs):
        arguments = dict(provider=provider, endpoint="https://example.invalid", model=model, api_key="test-key",
                         system="system", user="prompt", max_tokens=8096, temperature=0.7, top_p=0.8, top_k=40)
        arguments.update(kwargs)
        return build_provider_request(**arguments)[1]

    def test_none_omits_thinking_controls_for_all_three_providers(self):
        for provider, model in (("Anthropic", "claude-sonnet-4-5"),
                                ("Anthropic", "claude-sonnet-4-6"),
                                ("Google Gemini", "gemini-2.5-pro"),
                                ("Google Gemini", "gemini-3-flash-preview"),
                                ("Ollama", "qwen3"), ("Ollama", "gpt-oss:20b")):
            with self.subTest(provider=provider, model=model):
                payload = self.request(provider, model, thinking_enabled=None, thinking_budget=0)
                self.assertNotIn("thinking", payload)
                self.assertNotIn("think", payload)
                self.assertNotIn("thinkingConfig", payload.get("generationConfig", {}))

    def test_anthropic_manual_budget_default_explicit_and_sampling_compatibility(self):
        for model in ("claude-3-7-sonnet-20250219", "claude-sonnet-4-20250514", "claude-opus-4-1-20250805",
                      "claude-haiku-4-5-20251001", "claude-sonnet-4-5"):
            for budget in (0, 1024, 4096, 8095):
                with self.subTest(model=model, budget=budget):
                    payload = self.request("Anthropic", model, thinking_enabled=True, thinking_budget=budget)
                    self.assertEqual(payload["thinking"], {"type": "enabled", "budget_tokens": budget or 1024})
                    for key in ("temperature", "top_p", "top_k"):
                        self.assertNotIn(key, payload)
        payload = self.request("Anthropic", "claude-sonnet-4-5", thinking_enabled=True, max_tokens=0)
        self.assertEqual(payload["max_tokens"], 8096)
        self.assertEqual(payload["thinking"]["budget_tokens"], 1024)

    def test_anthropic_rejects_invalid_manual_budgets_before_request(self):
        for budget, limit in ((1, 8096), (1023, 8096), (8096, 8096), (9000, 8096), (0, 1024), (-1, 8096)):
            with self.subTest(budget=budget, limit=limit), self.assertRaisesRegex(ValueError, "budget"):
                self.request("Anthropic", "claude-sonnet-4-5", thinking_enabled=True,
                             thinking_budget=budget, max_tokens=limit)

    def test_anthropic_disabled_preserves_normal_sampling(self):
        for model in ("claude-sonnet-4-5", "claude-sonnet-4-6"):
            with self.subTest(model=model):
                payload = self.request("Anthropic", model, thinking_enabled=False)
                self.assertEqual(payload["thinking"], {"type": "disabled"})
                self.assertEqual(payload["temperature"], 0.7)
                self.assertEqual(payload["top_k"], 40)

    def test_anthropic_opus_47_omits_sampling_even_with_thinking_off_or_default(self):
        for model in ("claude-opus-4-7", "claude-opus-4-7-latest", "claude-opus-4-7-20260416"):
            for enabled in (False, None, True):
                with self.subTest(model=model, enabled=enabled):
                    payload = self.request("Anthropic", model, thinking_enabled=enabled)
                    for key in ("temperature", "top_p", "top_k"):
                        self.assertNotIn(key, payload)
                    if enabled is False:
                        self.assertEqual(payload["thinking"], {"type": "disabled"})
                    elif enabled is None:
                        self.assertNotIn("thinking", payload)
                    else:
                        self.assertEqual(payload["thinking"], {"type": "adaptive"})

    def test_anthropic_adaptive_has_no_fake_numeric_budget_and_maps_effort(self):
        payload = self.request("Anthropic", "claude-sonnet-4-6", thinking_enabled=True,
                               thinking_budget=0, reasoning_effort="high")
        self.assertEqual(payload["thinking"], {"type": "adaptive"})
        self.assertEqual(payload["output_config"], {"effort": "high"})
        for key in ("temperature", "top_p", "top_k"):
            self.assertNotIn(key, payload)
        with self.assertRaisesRegex(ValueError, "budget"):
            self.request("Anthropic", "claude-opus-4-6", thinking_enabled=True, thinking_budget=1024)
        with self.assertRaisesRegex(ValueError, "effort"):
            self.request("Anthropic", "claude-sonnet-4-6", thinking_enabled=True, reasoning_effort="minimal")

    def test_gemini_25_maps_dynamic_and_valid_budget_boundaries(self):
        for model, low, high in (("gemini-2.5-pro", 128, 32768), ("gemini-2.5-flash", 1, 24576),
                                 ("gemini-2.5-flash-lite", 512, 24576)):
            for budget in (0, low, high):
                with self.subTest(model=model, budget=budget):
                    payload = self.request("Google Gemini", f"models/{model}", thinking_enabled=True, thinking_budget=budget)
                    self.assertEqual(payload["generationConfig"]["thinkingConfig"], {"thinkingBudget": budget or -1})
                    self.assertEqual(payload["generationConfig"]["topK"], 40)
            for budget in (low - 1, high + 1):
                if budget == 0:
                    continue
                with self.subTest(model=model, invalid_budget=budget), self.assertRaisesRegex(ValueError, "budget"):
                    self.request("Google Gemini", model, thinking_enabled=True, thinking_budget=budget)

    def test_gemini_25_disable_supported_models_and_preserve_mandatory_defaults(self):
        for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
            payload = self.request("Google Gemini", model, thinking_enabled=False)
            self.assertEqual(payload["generationConfig"]["thinkingConfig"], {"thinkingBudget": 0})
        for enabled in (False, None):
            with self.subTest(enabled=enabled):
                payload = self.request("Google Gemini", "gemini-2.5-pro", thinking_enabled=enabled)
                self.assertNotIn("thinkingConfig", payload["generationConfig"])

    def test_gemini_3_uses_model_specific_levels(self):
        for model, levels in (("gemini-3-pro-preview", ("low", "high")),
                              ("gemini-3-flash-preview", ("low", "medium", "high")),
                              ("gemini-3.1-pro-preview", ("low", "medium", "high"))):
            for level in levels:
                with self.subTest(model=model, level=level):
                    payload = self.request("Google Gemini", model, thinking_enabled=True, reasoning_effort=level)
                    self.assertEqual(payload["generationConfig"]["thinkingConfig"], {"thinkingLevel": level})
            payload = self.request("Google Gemini", model, thinking_enabled=True)
            self.assertEqual(payload["generationConfig"]["thinkingConfig"], {"thinkingLevel": "high"})
        with self.assertRaisesRegex(ValueError, "effort"):
            self.request("Google Gemini", "gemini-3-pro-preview", thinking_enabled=True, reasoning_effort="medium")

    def test_gemini_3_does_not_fake_disable_or_budget_control(self):
        for model in ("gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-3.1-pro-preview"):
            with self.subTest(model=model):
                for enabled in (False, None):
                    payload = self.request("Google Gemini", model, thinking_enabled=enabled)
                    self.assertNotIn("thinkingConfig", payload["generationConfig"])
                with self.assertRaisesRegex(ValueError, "budget"):
                    self.request("Google Gemini", model, thinking_enabled=True, thinking_budget=1024)

    def test_ollama_boolean_think_is_top_level_and_preserves_total_token_limit(self):
        for enabled in (False, True):
            payload = self.request("Ollama", "qwen3:8b", thinking_enabled=enabled, max_tokens=2048)
            self.assertIs(payload["think"], enabled)
            self.assertNotIn("think", payload["options"])
            self.assertEqual(payload["options"]["num_predict"], 2048)

    def test_ollama_gpt_oss_uses_levels_and_preserves_mandatory_defaults(self):
        for effort in ("", "low", "medium", "high"):
            payload = self.request("Ollama", "gpt-oss:20b", thinking_enabled=True, reasoning_effort=effort)
            self.assertEqual(payload["think"], effort or "medium")
        for enabled in (False, None):
            with self.subTest(enabled=enabled):
                payload = self.request("Ollama", "gpt-oss:120b", thinking_enabled=enabled)
                self.assertNotIn("think", payload)
                self.assertNotIn("think", payload["options"])
        with self.assertRaisesRegex(ValueError, "effort"):
            self.request("Ollama", "gpt-oss", thinking_enabled=True, reasoning_effort="minimal")

    def test_ollama_rejects_numeric_budget_instead_of_ignoring_it(self):
        for model in ("qwen3:8b", "gpt-oss:20b", "custom-model"):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, "budget.*max_tokens"):
                self.request("Ollama", model, thinking_enabled=True, thinking_budget=1024)

    def test_unknown_models_keep_defaults_or_reject_explicit_thinking(self):
        for provider in ("Anthropic", "Google Gemini", "Ollama"):
            for model in ("unknown-alias", "qwen3-custom", "gemini-2.5-pro-custom", "claude-sonnet-4-5-custom"):
                for enabled in (None, False):
                    with self.subTest(provider=provider, model=model, enabled=enabled):
                        payload = self.request(provider, model, thinking_enabled=enabled)
                        self.assertNotIn("think", payload)
                        self.assertNotIn("thinking", payload)
                        self.assertNotIn("thinkingConfig", payload.get("generationConfig", {}))
                with self.subTest(provider=provider, model=model), self.assertRaisesRegex(ValueError, "thinking.*model"):
                    self.request(provider, model, thinking_enabled=True)


if __name__ == "__main__":
    unittest.main()
