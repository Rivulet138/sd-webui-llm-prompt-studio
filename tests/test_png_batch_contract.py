from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prompt_studio_ui as ui


class PngBatchContractTests(unittest.TestCase):
    def test_schema_and_per_prompt_limit_are_declared(self):
        source = (Path(__file__).parents[1] / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn('PNG_BATCH_SCHEMA = "prompt_batch.v1"', source)
        self.assertIn("PNG_BATCH_MAX_PROMPT_LENGTH = 12000", source)
        self.assertNotIn("PNG_BATCH_MAX_RECORDS", source)
        self.assertNotIn("PNG_BATCH_MAX_TOTAL_LENGTH", source)
        self.assertNotIn("PNG_BATCH_MAX_BYTES", source)
        self.assertIn("Path(str(image.get(\"filename\") or \"\")).name", source)
        self.assertIn("png_batch_payload = gr.Textbox", source)
        self.assertIn("_generic_prompt_records", source)

    def test_png_batch_ui_ids_are_stable(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        for suffix in ("tab", "file", "run", "cancel", "table", "payload", "results", "selection", "current", "target", "append", "append_all", "status"):
            self.assertIn(f"llm_prompt_studio_png_batch_{suffix}", source)

    def test_schema_round_trip_preserves_one_prompt_per_image(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "collector"},
            "records": [
                {"record_id": "a", "image": {"filename": "one.png"}, "prompt": {"positive": "cat"}},
                {"record_id": "b", "image": {"filename": "two.png"}, "prompt": {"positive": "cat"}},
            ],
        }
        normalized = ui._normalize_png_batch_payload(payload)
        self.assertEqual(len(normalized["records"]), 2)
        self.assertEqual([row["image"]["filename"] for row in normalized["records"]], ["one.png", "two.png"])

    def test_generic_json_arrays_and_prompt_objects_are_importable(self):
        normalized = ui._normalize_png_batch_payload([
            "a simple prompt",
            {"id": "second", "text": "a second prompt", "filename": "second.jpg"},
            {"prompt": {"positive": "a nested prompt"}},
        ])
        self.assertEqual([row["prompt"]["positive"] for row in normalized["records"]], [
            "a simple prompt", "a second prompt", "a nested prompt",
        ])
        self.assertEqual(normalized["records"][1]["image"]["filename"], "second.jpg")
        mapped = ui._normalize_png_batch_payload({"a": {"prompt": "mapped prompt"}})
        self.assertEqual(mapped["records"][0]["prompt"]["positive"], "mapped prompt")

    def test_schema_round_trip_preserves_processed_output_kind(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "collector"},
            "records": [{
                "record_id": "typed",
                "source_identity": "tags:cat",
                "image": {"filename": "typed.png"},
                "prompt": {
                    "positive": "cat",
                    "processed": "a cat in sunlight",
                    "processed_kind": "natural",
                    "output_kind": "natural",
                },
            }],
        }

        normalized = ui._normalize_png_batch_payload(payload)

        self.assertEqual(normalized["records"][0]["prompt"]["processed_kind"], "natural")
        self.assertEqual(normalized["records"][0]["prompt"]["output_kind"], "natural")
        self.assertEqual(normalized["records"][0]["source_identity"], "tags:cat")

    def test_schema_accepts_more_than_five_thousand_records(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "collector"},
            "records": [
                {"record_id": str(index), "image": {"filename": f"{index}.png"}, "prompt": {"positive": f"prompt {index} " + "x" * 1024}}
                for index in range(5001)
            ],
        }
        normalized = ui._normalize_png_batch_payload(payload)
        self.assertEqual(len(normalized["records"]), 5001)
        self.assertGreater(len(ui._png_batch_json(normalized).encode("utf-8")), 4 * 1024 * 1024)
        self.assertTrue(normalized["records"][-1]["prompt"]["positive"].startswith("prompt 5000 "))

    def test_schema_rejects_oversized_and_malformed_records(self):
        with self.assertRaises(ValueError):
            ui._normalize_png_batch_payload({
                "schema_version": "prompt_batch.v1",
                "records": [{"image": {}, "prompt": {"positive": "x" * 12001}}],
            })
        with self.assertRaises(ValueError):
            ui._normalize_png_batch_payload({
                "schema_version": "prompt_batch.v1",
                "records": [{"image": "bad", "prompt": {"positive": "x"}}],
            })

    def test_schema_uses_stable_generated_ids_and_rejects_identity_conflicts(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "collector"},
            "records": [{"image": {"filename": "one.png", "sha256": "a" * 64}, "prompt": {"positive": "cat"}}],
        }
        first = ui._normalize_png_batch_payload(payload)
        second = ui._normalize_png_batch_payload(payload)
        self.assertEqual(first["records"][0]["record_id"], second["records"][0]["record_id"])

        duplicate = {**payload, "records": [
            {**payload["records"][0], "record_id": "same"},
            {**payload["records"][0], "record_id": "same"},
        ]}
        with self.assertRaisesRegex(ValueError, "record_id 重复"):
            ui._normalize_png_batch_payload(duplicate)
        with self.assertRaisesRegex(ValueError, "sha256"):
            ui._normalize_png_batch_payload({
                "schema_version": "prompt_batch.v1",
                "records": [{"image": {"filename": "one.png", "sha256": "bad"}, "prompt": {"positive": "cat"}}],
            })

    def test_partial_failure_does_not_drop_later_records(self):
        records = [
            {"record_id": "a", "index": 1, "image": {"filename": "one.png", "sha256": ""}, "prompt": {"positive": "one"}},
            {"record_id": "b", "index": 2, "image": {"filename": "two.png", "sha256": ""}, "prompt": {"positive": "two"}},
        ]

        def transform(source, _action):
            if source == "one":
                raise RuntimeError("failed")
            return "processed two"

        results = ui._png_batch_process_records(records, "Expand", transform)
        self.assertEqual([item["status"] for item in results], ["failed", "completed"])
        self.assertEqual(results[1]["prompt"]["processed"], "processed two")

    def test_large_batch_progress_does_not_resend_full_payload(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "collector"},
            "records": [
                {"record_id": str(index), "image": {"filename": f"{index}.png"}, "prompt": {"positive": f"prompt {index}"}}
                for index in range(5001)
            ],
        }
        original = ui._expand_or_polish
        ui._expand_or_polish = lambda *_args: ("processed", "ok")
        try:
            updates = list(ui._png_batch_run(
                payload, "Expand", "Danbooru Tags", "", "Auto / checkpoint default",
                "SFW", "", "", "OpenAI-compatible", "http://127.0.0.1:1234",
                "model", "", 0.3, 30, 1000, True,
            ))
        finally:
            ui._expand_or_polish = original
            ui._PNG_BATCH_CANCEL.clear()

        self.assertLessEqual(len(updates), 102)
        self.assertTrue(all(update[0].get("__type__") == "update" for update in updates[:-1]))
        self.assertTrue(all(update[1].get("__type__") == "update" for update in updates[:-1]))
        self.assertEqual(len(ui._normalize_png_batch_payload(updates[-1][0])["records"]), 5001)

    def test_completed_records_are_not_sent_to_llm_again(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "studio"},
            "records": [
                {"record_id": "done", "image": {"filename": "done.png"}, "prompt": {"positive": "same", "processed": "already done"}, "status": "completed"},
                {"record_id": "new", "image": {"filename": "new.png"}, "prompt": {"positive": "same"}},
            ],
        }
        calls = []
        original = ui._expand_or_polish

        def fake_expand(source, *_args):
            calls.append(source)
            return "new result", "ok"

        ui._expand_or_polish = fake_expand
        try:
            updates = list(ui._png_batch_run(
                payload, "Expand", "Danbooru Tags", "", "Auto / checkpoint default",
                "SFW", "", "", "OpenAI-compatible", "http://127.0.0.1:1234",
                "model", "", 0.3, 30, 1000, True,
            ))
        finally:
            ui._expand_or_polish = original
            ui._PNG_BATCH_CANCEL.clear()

        records = ui._normalize_png_batch_payload(updates[-1][0])["records"]
        self.assertEqual(calls, ["same"])
        self.assertEqual(records[0]["prompt"]["processed"], "already done")
        self.assertEqual(records[1]["prompt"]["processed"], "new result")
        self.assertIn("已有结果跳过 1", updates[-1][-1])

    def test_convert_reprocesses_when_system_prompt_target_changes(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "studio"},
            "records": [{
                "record_id": "switch-target",
                "image": {"filename": "switch.png"},
                "prompt": {
                    "positive": "1girl, silver_hair, greenhouse, holding_flowers",
                    "processed": "A silver-haired girl holding flowers in a greenhouse.",
                    "processed_kind": "natural",
                    "processed_preset": "Krea 2 Natural",
                    "processed_base_model": "Krea 2",
                },
                "status": "completed",
            }],
        }
        calls = []
        original = ui._expand_or_polish
        ui._expand_or_polish = lambda source, *_args: (calls.append(source) or ("1girl, silver_hair, greenhouse, holding_flowers", "ok"))
        try:
            first = list(ui._png_batch_run(
                payload, "Convert", "Anima Tags", "", "Anima", "SFW", "", "",
                "OpenAI-compatible", "http://127.0.0.1:1234", "model", "", 1.25, 30, 1000, True,
            ))[-1][0]
            second = list(ui._png_batch_run(
                first, "Convert", "Anima Tags", "", "Anima", "SFW", "", "",
                "OpenAI-compatible", "http://127.0.0.1:1234", "model", "", 1.25, 30, 1000, True,
            ))[-1][0]
        finally:
            ui._expand_or_polish = original
            ui._PNG_BATCH_CANCEL.clear()

        record = ui._normalize_png_batch_payload(second)["records"][0]
        self.assertEqual(calls, ["1girl, silver_hair, greenhouse, holding_flowers"])
        self.assertEqual(record["prompt"]["processed_kind"], "tags")
        self.assertEqual(record["prompt"]["processed_preset"], "Anima Tags")
        self.assertEqual(record["prompt"]["processed_base_model"], "Anima")

    def test_duplicate_pending_prompts_reuse_success_or_failure_outcome(self):
        def run_with(result):
            payload = {
                "schema_version": "prompt_batch.v1",
                "records": [
                    {"record_id": "one", "image": {"filename": "one.png"}, "prompt": {"positive": "same"}},
                    {"record_id": "two", "image": {"filename": "two.png"}, "prompt": {"positive": "same"}},
                ],
            }
            calls = []
            original = ui._expand_or_polish
            ui._expand_or_polish = lambda source, *_args: (calls.append(source) or result)
            try:
                updates = list(ui._png_batch_run(
                    payload, "Expand", "Danbooru Tags", "", "Auto / checkpoint default",
                    "SFW", "", "", "OpenAI-compatible", "http://127.0.0.1:1234",
                    "model", "", 0.3, 30, 1000, True,
                ))
            finally:
                ui._expand_or_polish = original
                ui._PNG_BATCH_CANCEL.clear()
            return calls, ui._normalize_png_batch_payload(updates[-1][0])["records"], updates[-1][-1]

        success_calls, success_records, success_status = run_with(("processed", "ok"))
        failed_calls, failed_records, failed_status = run_with(("", "timeout"))

        self.assertEqual(success_calls, ["same"])
        self.assertEqual([record["prompt"]["processed"] for record in success_records], ["processed", "processed"])
        self.assertIn("相同 Prompt 复用 1", success_status)
        self.assertEqual(failed_calls, ["same"])
        self.assertEqual([record["status"] for record in failed_records], ["失败", "失败"])
        self.assertIn("相同 Prompt 复用 1", failed_status)

    def test_independent_mode_calls_llm_for_each_duplicate_source(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "studio"},
            "records": [
                {"record_id": "one", "image": {"filename": "one.png"}, "prompt": {"positive": "same"}},
                {"record_id": "two", "image": {"filename": "two.png"}, "prompt": {"positive": "same"}},
            ],
        }
        calls = []
        original = ui._expand_or_polish

        def fake_expand(source, *args):
            calls.append((source, args))
            return f"processed {len(calls)}", "ok"

        ui._expand_or_polish = fake_expand
        try:
            updates = list(ui._png_batch_run(
                payload, "Convert", "Krea 2 Natural", "", "Krea 2",
                "SFW", "", "", "OpenAI-compatible", "http://127.0.0.1:1234",
                "model", "", 0.3, 30, 1000, True, variation_mode="independent",
            ))
        finally:
            ui._expand_or_polish = original
            ui._PNG_BATCH_CANCEL.clear()

        self.assertEqual([item[0] for item in calls], ["same", "same"])
        self.assertTrue(all("Independent single-image request" in str(item[1][-2]) for item in calls))
        records = ui._normalize_png_batch_payload(updates[-1][0])["records"]
        self.assertEqual([record["prompt"]["processed"] for record in records], ["processed 1", "processed 2"])

    def test_independent_mode_enforces_high_temperature(self):
        captured = []
        original_call = ui.call_llm
        original_finalize = ui._finalize_generated_prompt
        original_resolve = ui.CREDENTIALS.resolve
        ui.CREDENTIALS.resolve = lambda *_args: ""
        ui.call_llm = lambda *args, **kwargs: captured.append(args) or "a distinct prompt"
        ui._finalize_generated_prompt = lambda result, *_args: result
        try:
            result, _status = ui._expand_or_polish(
                "same", "Convert", "Krea 2 Natural", "", "Krea 2", "SFW", "", "",
                "OpenAI-compatible", "http://127.0.0.1:1234", "model", "", 0.1, 30, 1000, True,
                batch_directive="independent directive", previous_outputs=[],
            )
        finally:
            ui.call_llm = original_call
            ui._finalize_generated_prompt = original_finalize
            ui.CREDENTIALS.resolve = original_resolve
        self.assertEqual(result, "a distinct prompt")
        self.assertGreaterEqual(captured[0][6], 1.25)

    def test_polish_injects_krea_anima_detail_role(self):
        captured_systems = []
        finalized_presets = []
        original_call = ui.call_llm
        original_finalize = ui._finalize_generated_prompt
        original_resolve = ui.CREDENTIALS.resolve
        ui.CREDENTIALS.resolve = lambda *_args: ""
        ui.call_llm = lambda *args, **kwargs: captured_systems.append(args[4]) or "detailed prompt"
        ui._finalize_generated_prompt = lambda result, preset, *_args: finalized_presets.append(preset) or result
        try:
            ui._expand_or_polish(
                "1girl, silver hair", "Polish", "Anima Tags", "", "Anima", "SFW", "", "",
                "OpenAI-compatible", "http://127.0.0.1:1234", "model", "", 0.3, 30, 1000, True,
            )
        finally:
            ui.call_llm = original_call
            ui._finalize_generated_prompt = original_finalize
            ui.CREDENTIALS.resolve = original_resolve
        output_profile = str(captured_systems[0])
        self.assertIn("Krea2 & Anima extreme-detail expansion prompt engineer", output_profile)
        self.assertIn("score_7, score_8_up", output_profile)
        self.assertIn("STATIC VOCABULARY REFERENCE", output_profile)
        self.assertIn("Composition & Pose", output_profile)
        self.assertIn("MASTER DESCRIPTION", output_profile)
        self.assertNotIn("PROMPT POLICY V2", output_profile)
        self.assertEqual(finalized_presets, ["Krea 2 Natural"])

    def test_recent_outputs_are_available_as_generation_exclusions(self):
        source = "unique exclusion source"
        key = ui.hashlib.sha256(source.encode("utf-8")).hexdigest()
        with ui._RECENT_BATCH_OUTPUTS_LOCK:
            ui._RECENT_BATCH_OUTPUTS.pop(key, None)
        try:
            self.assertTrue(ui._remember_diverse_output(source, "old scene with a red umbrella"))
            self.assertEqual(ui._recent_diverse_outputs(source), ["old scene with a red umbrella"])
        finally:
            with ui._RECENT_BATCH_OUTPUTS_LOCK:
                ui._RECENT_BATCH_OUTPUTS.pop(key, None)

    def test_diversity_ledger_surfaces_repeated_content_concepts(self):
        source = "one fox girl"
        outputs = [
            "one fox girl holding a red umbrella beside a stone bridge",
            "one fox girl holding a red umbrella beneath a paper lantern",
            "one fox girl holding a red umbrella near a quiet canal",
        ]
        terms = ui._diversity_exclusion_terms_from_outputs(outputs, source)
        self.assertIn("holding", terms)
        self.assertIn("umbrella", terms)
        self.assertNotIn("girl", terms)

    def test_diversity_duplicate_check_catches_repeated_item_and_action(self):
        source = "one fox girl"
        first = "one fox girl holding a red umbrella beside a stone bridge"
        near_copy = "one fox girl holding a blue umbrella beside a stone bridge"
        fresh = "one fox girl repairing a clock inside a sunlit workshop"
        self.assertTrue(ui._is_diversity_duplicate(near_copy, first, source))
        self.assertFalse(ui._is_diversity_duplicate(fresh, first, source))

    def test_native_append_javascript_dispatches_prompt_events(self):
        script = (ROOT / "javascript" / "llm_prompt_studio_png_batch.js").read_text(encoding="utf-8")
        self.assertIn('dispatchEvent(new Event("input"', script)
        self.assertIn('dispatchEvent(new Event("change"', script)
        self.assertIn("appendToPrompt", script)
        self.assertIn("appendAllToPrompt", script)

    def test_batch_cancel_preserves_completed_result(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "collector"},
            "records": [
                {"image": {"filename": "one.png"}, "prompt": {"positive": "one"}},
                {"image": {"filename": "two.png"}, "prompt": {"positive": "two"}},
                {"image": {"filename": "done.png"}, "prompt": {"positive": "done", "processed": "existing"}, "status": "completed"},
            ],
        }
        original = ui._expand_or_polish

        def fake_expand(*_args):
            ui._PNG_BATCH_CANCEL.set()
            return "processed one", "ok"

        ui._expand_or_polish = fake_expand
        try:
            updates = list(ui._png_batch_run(
                payload, "Expand", "Danbooru Tags", "", "Auto / checkpoint default",
                "SFW", "", "", "OpenAI-compatible", "http://127.0.0.1:1234",
                "model", "", 0.3, 30, 1000, True,
            ))
        finally:
            ui._expand_or_polish = original
            ui._PNG_BATCH_CANCEL.clear()

        records = ui._normalize_png_batch_payload(updates[-1][0])["records"]
        self.assertEqual(records[0]["prompt"]["processed"], "processed one")
        self.assertEqual(records[1]["status"], "已取消")
        self.assertEqual(records[2]["status"], "completed")
        self.assertNotIn("error", records[2])

    def test_last_append_enters_completed_state(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "studio"},
            "records": [
                {"image": {"filename": "one.png"}, "prompt": {"positive": "one", "processed": "done"}},
            ],
        }
        updated, selection, current, status = ui._png_batch_advance_after_append(payload, 1, True)
        self.assertEqual(selection, 0)
        self.assertEqual(current, "")
        self.assertIn("全部", status)
        self.assertTrue(ui._normalize_png_batch_payload(updated)["records"][0]["appended"])


if __name__ == "__main__":
    unittest.main()
