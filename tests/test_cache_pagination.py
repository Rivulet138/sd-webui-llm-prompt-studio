import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prompt_studio_core import StudioDB


class CachePaginationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = StudioDB(Path(temp.name) / "cache.db")

    def test_traverses_all_records_beyond_one_thousand(self):
        ids = self.db.save_prompts_batch(
            [{"prompt": f"scene {index}"} for index in range(2105)]
        )["ids"]
        seen, cursor = [], 0
        while True:
            page = self.db.list_prompts_after(limit=127, after_id=cursor)
            if not page:
                break
            seen.extend(row["id"] for row in page)
            cursor = page[-1]["id"]
        self.assertEqual(seen, ids)
        self.assertEqual(len(self.db.list_prompts_after(limit=10000)), 1000)
        self.assertEqual(len(self.db.list_prompts_after(limit=0)), 1)

    def test_deleted_and_appended_records_do_not_shift_cursor(self):
        ids = self.db.save_prompts_batch([{"prompt": f"scene {i}"} for i in range(5)])["ids"]
        first = self.db.list_prompts_after(limit=2)
        with self.db._connection() as conn:
            conn.execute("DELETE FROM prompts WHERE id IN (?, ?)", (ids[0], ids[2]))
        appended = self.db.save_prompt("appended")
        remaining = self.db.list_prompts_after(after_id=first[-1]["id"])
        self.assertEqual([row["id"] for row in remaining], [ids[3], ids[4], appended])
        self.assertEqual(self.db.list_prompts_after(after_id=appended), [])
        newest = self.db.save_prompt("newly appended")
        self.assertEqual(self.db.list_prompts_after(after_id=appended)[0]["id"], newest)

    def test_skips_blank_rows_across_multiple_pages(self):
        ids = self.db.save_prompts_batch([{"prompt": f"blank {i}"} for i in range(205)])["ids"]
        with self.db._connection() as conn:
            conn.execute("UPDATE prompts SET prompt=?", (" \t\n\r\v\f\u2003\u3000",))
            conn.execute("UPDATE prompts SET prompt='' WHERE id=?", (ids[0],))
        good = self.db.save_prompt("usable")
        self.assertEqual([row["id"] for row in self.db.list_prompts_after(limit=1)], [good])
        self.assertEqual(self.db.list_prompts_after(after_id=good), [])

    def test_keyword_matches_existing_search_fields(self):
        self.db.save_prompt("unrelated")
        expected = [
            self.db.save_prompt("needle scene"),
            self.db.save_prompt("negative match", negative="needle"),
            self.db.save_prompt("tag match", tags="needle"),
            self.db.save_prompt("source match", source_kind="needle"),
            self.db.save_prompt("reference match", source_ref="needle"),
        ]
        self.assertEqual(
            [row["id"] for row in self.db.list_prompts_after(" needle ", after_id=expected[0])],
            expected[1:],
        )

    def test_legacy_listing_retains_visible_positions_and_offset(self):
        ids = self.db.save_prompts_batch([{"prompt": f"scene {i}"} for i in range(3)])["ids"]
        with self.db._connection() as conn:
            conn.execute("DELETE FROM prompts WHERE id=?", (ids[0],))
        page = self.db.list_prompts(limit=1, offset=1)
        self.assertEqual(page[0]["id"], ids[2])
        self.assertEqual(page[0]["visible_position"], 2)


class CachePaginationEndpointTests(unittest.TestCase):
    def test_both_routes_support_cursor_and_legacy_calls(self):
        from fastapi import FastAPI

        sys.modules.setdefault("gradio", types.ModuleType("gradio"))
        import prompt_studio_ui as ui

        with tempfile.TemporaryDirectory() as directory:
            original = StudioDB(Path(directory) / "original.db")
            processed = StudioDB(Path(directory) / "processed.db")
            modules = types.ModuleType("modules")
            modules.shared = types.SimpleNamespace(cmd_opts=types.SimpleNamespace(api_auth=""))
            app = FastAPI()
            with mock.patch.object(ui, "DB", original), mock.patch.object(ui, "RESULT_DB", processed), \
                    mock.patch.dict(sys.modules, {"modules": modules}), \
                    mock.patch.object(ui, "_ensure_server_queue_worker") as worker:
                ui.on_app_started(None, app)
                worker.assert_not_called()
                routes = {route.path: route.endpoint for route in app.routes}
                for path, db in (("cache", original), ("processed-cache", processed)):
                    with self.subTest(path=path):
                        ids = db.save_prompts_batch(
                            [{"prompt": f"scene {i}"} for i in range(1005)]
                        )["ids"]
                        endpoint = routes[f"/llm-prompt-studio/v1/{path}"]
                        self.assertEqual(endpoint(), {"records": db.list_prompts(limit=100)})
                        tail = endpoint(query="scene", limit=100, after_id=ids[999])["records"]
                        self.assertEqual([row["id"] for row in tail], ids[1000:])
                        self.assertEqual(endpoint(after_id=ids[-1]), {"records": []})
                        self.assertEqual(endpoint(query="missing", after_id=0), {"records": []})
                        self.assertEqual(endpoint(after_id=0, limit=1)["records"][0]["id"], ids[0])


if __name__ == "__main__":
    unittest.main()
