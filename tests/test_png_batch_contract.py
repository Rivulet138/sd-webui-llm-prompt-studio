from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prompt_studio_ui as ui


class PngBatchContractTests(unittest.TestCase):
    def test_schema_and_limits_are_declared(self):
        source = (Path(__file__).parents[1] / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn('PNG_BATCH_SCHEMA = "prompt_batch.v1"', source)
        self.assertIn("PNG_BATCH_MAX_RECORDS = 200", source)
        self.assertIn("PNG_BATCH_MAX_PROMPT_LENGTH = 12000", source)
        self.assertIn("PNG_BATCH_MAX_BYTES = 4 * 1024 * 1024", source)
        self.assertIn("Path(str(image.get(\"filename\") or \"\")).name", source)
        self.assertIn("png_batch_payload = gr.Textbox", source)

    def test_png_batch_ui_ids_are_stable(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        for suffix in ("tab", "file", "run", "cancel", "table", "payload", "results", "selection", "current", "target", "append", "status"):
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

    def test_native_append_javascript_dispatches_prompt_events(self):
        script = (ROOT / "javascript" / "llm_prompt_studio_png_batch.js").read_text(encoding="utf-8")
        self.assertIn('dispatchEvent(new Event("input"', script)
        self.assertIn('dispatchEvent(new Event("change"', script)
        self.assertIn("appendToPrompt", script)

    def test_batch_cancel_preserves_completed_result(self):
        payload = {
            "schema_version": "prompt_batch.v1",
            "producer": {"name": "collector"},
            "records": [
                {"image": {"filename": "one.png"}, "prompt": {"positive": "one"}},
                {"image": {"filename": "two.png"}, "prompt": {"positive": "two"}},
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
                "model", "", 0.3, 30, 1000, True, "test-task",
            ))
        finally:
            ui._expand_or_polish = original
            ui._PNG_BATCH_CANCEL.clear()

        records = ui._normalize_png_batch_payload(updates[-1][0])["records"]
        self.assertEqual(records[0]["prompt"]["processed"], "processed one")
        self.assertEqual(records[1]["status"], "已取消")

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
