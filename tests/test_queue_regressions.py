"""Focused regressions; all databases and Forge processing are isolated fakes."""
import ast
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import uuid
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prompt_studio_core import StudioDB


def ui_functions(*names, constants=(), **context):
    """Extract top-level functions (and any named module constants) for focused tests.

    ``constants`` is opt-in: it lets a test pull a real module-level table (recipe
    dimensions, stop-token sets, settings defaults) instead of copying it, so the
    test cannot drift away from the implementation. Collected nodes are executed in
    source order, which keeps cross-constant references valid.
    """
    tree = ast.parse((ROOT / "scripts/prompt_studio_ui.py").read_text(encoding="utf-8"))
    wanted_constants = set(constants)
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            selected.append(node)
        elif wanted_constants and isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(target, "id", None) in wanted_constants for target in targets):
                selected.append(node)
    selected.sort(key=lambda node: (node.lineno, node.col_offset))
    namespace = dict(Any=Any, StudioDB=StudioDB, threading=threading, time=time, json=json, re=re,
                     hashlib=hashlib, uuid=uuid, LOGGER=logging.getLogger("queue-test"), MAX_SERVER_QUEUE_REQUESTS=5000)
    namespace.update(context)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / "scripts/prompt_studio_ui.py"), "exec"), namespace)
    return SimpleNamespace(**namespace)


class WD14LifecycleTests(unittest.TestCase):
    def test_batch_cleanup_releases_cached_model_without_masking_cleanup_errors(self):
        unload = Mock(return_value="反推模型已卸载。")
        api = ui_functions("_wd14_release_after_batch", unload_local_model=unload)
        self.assertEqual(api._wd14_release_after_batch(), "反推模型已卸载。")
        unload.assert_called_once_with()

        unload.reset_mock(side_effect=True)
        unload.side_effect = RuntimeError("already closed")
        self.assertEqual(api._wd14_release_after_batch(), "")
        unload.assert_called_once_with()


class QueueDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = StudioDB(Path(self.tmp.name) / "queue.db")

    def enqueue(self, count=1):
        self.db.enqueue_server_queue("batch", [{"id": f"job-{i}", "request": f"prompt {i}"} for i in range(count)])

    def test_token_guards_completion(self):
        self.enqueue()
        job = self.db.claim_server_queue_job()
        self.assertTrue(job["claim_token"])
        self.assertFalse(self.db.update_server_queue_job(job["id"], "completed", expected_claim_token="wrong"))
        self.assertTrue(self.db.server_queue_job_is_active(job["id"], job["claim_token"]))
        self.assertTrue(self.db.update_server_queue_job(job["id"], "completed", prompt="done", expected_claim_token=job["claim_token"]))
        self.assertFalse(self.db.update_server_queue_job(job["id"], "error", expected_claim_token=job["claim_token"]))

    def test_cancel_pending_and_running_cannot_be_overwritten(self):
        self.enqueue(3)
        job = self.db.claim_server_queue_job()
        self.assertEqual(self.db.cancel_server_queue("batch"), 3)
        self.assertFalse(self.db.update_server_queue_job(job["id"], "completed", expected_claim_token=job["claim_token"]))
        self.assertFalse(self.db.server_queue_job_is_active(job["id"], job["claim_token"]))
        self.assertEqual({r["status"] for r in self.db.list_server_queue("batch")}, {"cancelled"})
        self.assertIsNone(self.db.claim_server_queue_job())

    def test_stale_running_is_unknown_not_requeued(self):
        self.enqueue()
        job = self.db.claim_server_queue_job()
        with self.db._connection() as conn:
            conn.execute("UPDATE server_queue_jobs SET updated_at=0")
        self.assertEqual(self.db.recover_server_queue(), 1)
        self.assertEqual(self.db.list_server_queue("batch")[0]["status"], "error")
        self.assertIsNone(self.db.claim_server_queue_job())
        self.assertFalse(self.db.update_server_queue_job(job["id"], "completed", expected_claim_token=job["claim_token"]))

    def test_live_claim_excluded_from_recovery(self):
        self.enqueue()
        job = self.db.claim_server_queue_job()
        with self.db._connection() as conn:
            conn.execute("UPDATE server_queue_jobs SET updated_at=0")
        self.assertEqual(self.db.recover_server_queue(active_claim_tokens=[job["claim_token"]]), 0)

    def test_5000_jobs_snapshot_is_complete(self):
        self.enqueue(5000)
        self.assertEqual(len(self.db.list_server_queue("batch")), 5000)
        api = ui_functions("_server_queue_snapshot", DB=self.db)
        snapshot = api._server_queue_snapshot("batch")
        self.assertEqual(snapshot["counts"], {"pending": 5000})
        self.assertEqual(len(snapshot["jobs"]), 5000)
        self.assertEqual(snapshot["jobs"][-1]["position"], 5000)
        self.assertIn("等待", snapshot["status"])

    def test_active_cancel_event_not_released_early(self):
        self.enqueue()
        event = threading.Event()
        event.set()
        active = {"batch"}
        events = {"batch": event}
        api = ui_functions("_release_server_queue_cancel_event", DB=self.db,
                           _SERVER_QUEUE_CANCEL_LOCK=threading.Lock(),
                           _SERVER_QUEUE_CANCEL_EVENTS=events, _SERVER_QUEUE_ACTIVE_BATCHES=active)
        self.db.cancel_server_queue("batch")
        api._release_server_queue_cancel_event("batch")
        self.assertIs(events["batch"], event)
        active.clear()
        api._release_server_queue_cancel_event("batch")
        self.assertNotIn("batch", events)


class InputBoundaryTests(unittest.TestCase):
    def test_boolean_spellings(self):
        parse = ui_functions("_coerce_bool")._coerce_bool
        for value in (False, "false", "False", "0", "off", "no", "关闭", "", 0):
            with self.subTest(value=value):
                self.assertFalse(parse(value, True))
        for value in (True, "true", "1", "on", "yes", "开启", 1):
            with self.subTest(value=value):
                self.assertTrue(parse(value))
        self.assertTrue(parse(None, True))
        self.assertFalse(parse("unknown"))

    def test_direct_false_does_not_reject_nongeneration_queue(self):
        workflow = dict(preset="Danbooru Tags", base_model="Auto", safety="SFW", system_override="", nsfw_injection="", user_instruction="",
                        remove_bad=True, remove_terms="", shuffle=False, spaces=False, max_tags=0, structured_mode="Plain Prompt", region_count=1, save_score=0)
        connection = dict(temperature=1, timeout=90, max_tokens=8096, send_temperature=True, provider="OpenAI Compatible", endpoint="", model="")
        db = Mock()
        db.enqueue_server_queue.return_value = 1
        api = ui_functions("_coerce_bool", "_enqueue_server_queue", DB=db,
            _workflow_settings=lambda: workflow, _connection_settings=lambda: connection,
            _aligned_preset=lambda p, b: (p, False), uuid=SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="batch")),
            _ensure_server_queue_worker=Mock(), _SERVER_QUEUE_WAKE=threading.Event(), _server_queue_snapshot=lambda b: {})
        result = api._enqueue_server_queue({"requests": ["prompt"], "target": "none", "config": {"direct_prompt": "false", "cache_result": "false"}})
        self.assertEqual(result["queued"], 1)
        config = db.enqueue_server_queue.call_args.args[1][0]["config"]
        self.assertIs(config["direct_prompt"], False)
        self.assertIs(config["cache_result"], False)

    def handoff_api(self):
        db = Mock()
        db.save_handoff.return_value = 17
        legacy = Mock(return_value={"legacy": True})
        return ui_functions("_normalize_generic_handoff", "receive_handoff", DB=db, receive_ranbooru_handoff=legacy), db, legacy

    def test_explicit_generic_schema(self):
        api, db, legacy = self.handoff_api()
        result = api.receive_handoff({"schema": "prompt_studio.handoff.v1", "source_kind": "collector", "prompt": "one cat"})
        self.assertEqual(result["handoff_id"], 17)
        legacy.assert_not_called()
        self.assertEqual(db.save_handoff.call_args.args[0]["raw_prompt"], "one cat")

    def test_invalid_explicit_schema_cannot_fall_back_to_legacy(self):
        api, db, legacy = self.handoff_api()
        with self.assertRaises(ValueError):
            api.receive_handoff({"schema": "unknown.v2", "source_kind": "collector", "selected_prompt": "one cat"})
        legacy.assert_not_called()
        db.save_handoff.assert_not_called()

    def test_legacy_handoff_remains_compatible(self):
        api, db, legacy = self.handoff_api()
        payload = {"ranbooru_id": "7", "tags_prompt": "one cat"}
        self.assertEqual(api.receive_handoff(payload), {"legacy": True})
        legacy.assert_called_once_with(payload, "send")
        db.save_handoff.assert_not_called()


class NativeCacheContextTests(unittest.TestCase):
    class StableDiffusionProcessingTxt2Img:
        pass

    class StableDiffusionProcessingImg2Img:
        pass

    def setUp(self):
        records = [
            {"id": 1, "prompt": "cached one"},
            {"id": 2, "prompt": "cached two"},
        ]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = StudioDB(Path(self.tmp.name) / "native.db")
        for record in records:
            self.db.save_prompt(record["prompt"])
        self.api = ui_functions(
            "_coerce_bool", "_native_cache_database", "_native_cache_merge_prompt",
            "_native_cache_cleanup_locked", "_native_cache_extract_claim", "_native_cache_prepare",
            "apply_native_cache_context", "_native_cache_release", "_native_cache_get_context",
            "prepare_native_cache_processing", "complete_native_cache_context", "_native_cache_fail",
            "track_native_cache_image", "_native_cache_state", "_native_cache_slot",
            constants=(
                "_NATIVE_CACHE_CONTEXTS", "_NATIVE_CACHE_CONTEXT_LOCK", "_NATIVE_CACHE_CONTEXT_TTL",
                "_NATIVE_CACHE_MARKER_START", "_NATIVE_CACHE_MARKER_END",
                "_NATIVE_CACHE_MARKER_ZERO", "_NATIVE_CACHE_MARKER_ONE",
            ),
            DB=self.db, RESULT_DB=self.db,
        )

    def marker(self, token):
        bits = "".join(f"{int(char, 16):04b}" for char in token)
        return (
            self.api._NATIVE_CACHE_MARKER_START
            + "".join(
                self.api._NATIVE_CACHE_MARKER_ONE if bit == "1" else self.api._NATIVE_CACHE_MARKER_ZERO
                for bit in bits
            )
            + self.api._NATIVE_CACHE_MARKER_END
        )

    def prepare(self, **overrides):
        payload = {
            "slot": "txt2img", "source": "cache", "after_id": 0, "total_images": 1,
            "base_prompt": "base", "write_mode": "append_end", "marker": "{{LLM}}",
            **overrides,
        }
        return self.api._native_cache_prepare(payload)

    def native_script(self, shared):
        tree = ast.parse((ROOT / "scripts/prompt_studio_native.py").read_text(encoding="utf-8"))
        namespace = {**vars(self.api), "shared": shared,
                     "scripts": SimpleNamespace(Script=object, AlwaysVisible=object())}
        exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.ClassDef)],
                                type_ignores=[]), "prompt_studio_native.py", "exec"), namespace)
        return namespace["Script"]()

    def test_native_hook_rejects_cancelled_or_missing_claim_and_interrupts_forge(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                token = self.prepare()["token"] if cancelled else "f" * 32
                if cancelled:
                    self.api._native_cache_release(token, False)
                p = self.StableDiffusionProcessingTxt2Img()
                p.prompt = "base" + self.marker(token)
                shared = SimpleNamespace(state=SimpleNamespace(interrupt=Mock()))
                bridge = self.native_script(shared)
                with self.assertRaises(ValueError):
                    bridge.process(p)
                shared.state.interrupt.assert_called_once_with()
                self.assertEqual(p.prompt, "base")
                self.assertIsNone(self.db.get_native_cache_cursor("txt2img", "cache"))

    def test_before_hook_strips_claim_before_inherited_hires_setup(self):
        token = self.prepare()["token"]
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt, p.hr_prompt = "base" + self.marker(token), ""
        self.native_script(SimpleNamespace()).before_process(p)
        self.assertEqual(p.prompt, "base")
        self.assertTrue(p._llm_prompt_studio_inherit_hr)

    def test_style_failure_interrupts_without_changing_negative_fields(self):
        prepared = self.prepare()
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt, p.styles = "base" + self.marker(prepared["token"]), ["style"]
        p.negative_prompt = negative = ["native negative"]
        shared = SimpleNamespace(state=SimpleNamespace(interrupt=Mock()),
                                 prompt_styles=SimpleNamespace(apply_styles_to_prompt=Mock(side_effect=RuntimeError("bad style"))))
        modules = ModuleType("modules")
        modules.shared = shared
        bridge = self.native_script(shared)
        bridge.before_process(p)
        with patch.dict(sys.modules, {"modules": modules}):
            with self.assertRaisesRegex(RuntimeError, "bad style"):
                bridge.process(p)
        shared.state.interrupt.assert_called_once_with()
        self.assertIs(p.negative_prompt, negative)
        self.assertEqual(self.db.get_native_cache_job(prepared["token"])["state"], "failed")
        self.assertIsNone(self.db.get_native_cache_cursor("txt2img", "cache"))

    def test_styles_applied_once_and_active_release_does_not_commit_or_cancel(self):
        prepared = self.prepare()
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt, p.styles = "base" + self.marker(prepared["token"]), ["style"]
        style = Mock(side_effect=lambda prompt, styles: "styled " + prompt)
        modules = ModuleType("modules")
        modules.shared = SimpleNamespace(prompt_styles=SimpleNamespace(apply_styles_to_prompt=style))
        with patch.dict(sys.modules, {"modules": modules}):
            self.api.apply_native_cache_context(p)
            self.api.apply_native_cache_context(p)
        style.assert_called_once_with("base, cached one", ["style"])
        for commit in (True, False):
            result = self.api._native_cache_release(prepared["token"], commit)
            self.assertFalse(result["committed"])
            self.assertFalse(result["released"])
            self.assertEqual(result["state"], "active")

    def test_native_comments_filter_positive_prompts_and_keep_raw_infotext(self):
        for comments_after in (False, True):
            with self.subTest(comments_after=comments_after):
                prepared = self.prepare(base_prompt="base # comment")
                p = self.StableDiffusionProcessingTxt2Img()
                p.prompt = "base # comment" + self.marker(prepared["token"])
                p.negative_prompt = negative = "negative # native"
                p._all_negative_prompts_c = raw_negative = [negative]
                shared = SimpleNamespace(opts=SimpleNamespace(enable_prompt_comments=True, save_prompt_comments=True))
                modules = ModuleType("modules")
                modules.shared = shared
                comments = ModuleType("modules.processing_scripts.comments")
                comments.strip_comments = Mock(side_effect=lambda text: text.split("#", 1)[0].rstrip())
                bridge = self.native_script(shared)
                with patch.dict(sys.modules, {"modules": modules, "modules.processing_scripts.comments": comments}):
                    bridge.before_process(p)
                    bridge.process(p)
                if comments_after:
                    p._all_prompts_c = p.all_prompts.copy()
                bridge.before_process_batch(p, batch_number=0)
                self.assertEqual(p.all_prompts, ["base"])
                self.assertEqual(p.main_prompt, "base")
                self.assertEqual(p._all_prompts_c, ["base # comment, cached one"])
                self.assertIs(p.negative_prompt, negative)
                self.assertIs(p._all_negative_prompts_c, raw_negative)

    def test_native_image_indices_ignore_extra_gallery_images(self):
        prepared = self.prepare(total_images=2)
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt = "base" + self.marker(prepared["token"])
        p.batch_size, p.n_iter = 1, 2
        bridge = self.native_script(SimpleNamespace())
        bridge.before_process(p)
        bridge.process(p)
        bridge.postprocess_image(p, SimpleNamespace(index=0))
        bridge.postprocess(p, SimpleNamespace(images=["grid", "one", "extension output"], index_of_first_image=1))
        self.assertEqual(self.db.get_native_cache_cursor("txt2img", "cache"), 1)

    def test_missing_marker_does_not_consume_pending_context(self):
        prepared = self.prepare()
        processing = self.StableDiffusionProcessingTxt2Img()
        processing.prompt = "base"

        self.assertIsNone(self.api.apply_native_cache_context(processing))
        self.assertEqual(self.api._NATIVE_CACHE_CONTEXTS[prepared["token"]]["state"], "pending")

    def test_marker_binds_exact_context_and_restores_prompt(self):
        prepared = self.prepare()
        processing = self.StableDiffusionProcessingTxt2Img()
        processing.prompt = "base" + self.marker(prepared["token"])

        self.assertEqual(self.api.apply_native_cache_context(processing), prepared["token"])
        self.assertEqual(processing.prompt, "base")
        self.assertEqual(processing.all_prompts, ["base, cached one"])
        self.assertEqual(processing.main_prompt, "base, cached one")
        self.assertEqual(self.api._NATIVE_CACHE_CONTEXTS[prepared["token"]]["state"], "active")

    def test_reverse_token_order_consumes_the_matching_context(self):
        first = self.prepare(base_prompt="first")
        second = self.prepare(base_prompt="second")

        second_processing = self.StableDiffusionProcessingTxt2Img()
        second_processing.prompt = "second" + self.marker(second["token"])
        self.api.apply_native_cache_context(second_processing)
        self.assertEqual(second_processing.all_prompts, ["second, cached one"])

        first_processing = self.StableDiffusionProcessingTxt2Img()
        first_processing.prompt = "first" + self.marker(first["token"])
        self.api.apply_native_cache_context(first_processing)
        self.assertEqual(first_processing.all_prompts, ["first, cached one"])

    def test_slot_mismatch_does_not_consume_context(self):
        prepared = self.prepare(slot="txt2img")
        processing = self.StableDiffusionProcessingImg2Img()
        processing.prompt = "base" + self.marker(prepared["token"])

        self.assertIsNone(self.api.apply_native_cache_context(processing))
        self.assertEqual(self.api._NATIVE_CACHE_CONTEXTS[prepared["token"]]["state"], "pending")

    def test_release_supports_rollback_and_commit(self):
        rollback = self.prepare()
        released = self.api._native_cache_release(rollback["token"], False)
        self.assertEqual(released["released"], True)
        self.assertEqual(released["committed"], False)
        self.assertEqual(self.db.get_native_cache_job(rollback["token"])["state"], "cancelled")

        commit = self.prepare()
        processing = self.StableDiffusionProcessingTxt2Img()
        processing.prompt = "base" + self.marker(commit["token"])
        self.api.apply_native_cache_context(processing)
        self.assertFalse(self.api._native_cache_release(commit["token"], True)["committed"])
        self.api.complete_native_cache_context(processing, SimpleNamespace(images=[object()], index_of_first_image=0))
        committed = self.api._native_cache_release(commit["token"], True)
        self.assertEqual(committed["released"], True)
        self.assertEqual(committed["committed"], True)


    def test_invalid_merge_is_rejected_before_job_creation(self):
        with self.assertRaisesRegex(ValueError, "标记"):
            self.prepare(write_mode="marker", base_prompt="no marker")
        with self.assertRaises(ValueError):
            self.prepare(write_mode="unknown")

    def test_only_positive_fields_change_and_explicit_hires_is_preserved(self):
        prepared = self.prepare(total_images=2)
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt = "base" + self.marker(prepared["token"])
        p.batch_size, p.n_iter = 1, 2
        original = {
            "negative_prompt": ["negative A", "negative B"],
            "all_negative_prompts": ["styled negative A", "styled negative B"],
            "main_negative_prompt": "main negative", "hr_prompt": "explicit hires",
            "all_hr_prompts": ["explicit hires A", "explicit hires B"],
            "hr_negative_prompt": "hires negative", "all_hr_negative_prompts": ["hires negatives"],
        }
        for key, value in original.items():
            setattr(p, key, value)
        self.api.prepare_native_cache_processing(p)
        self.api.apply_native_cache_context(p)
        for key, value in original.items():
            self.assertIs(getattr(p, key), value, key)
        self.assertEqual(p.all_prompts, ["base, cached one", "base, cached two"])

    def test_inherited_hires_uses_positive_per_image_prompts(self):
        prepared = self.prepare(total_images=2)
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt = "base" + self.marker(prepared["token"])
        p.hr_prompt, p.batch_size, p.n_iter = "", 2, 1
        self.api.prepare_native_cache_processing(p)
        p.hr_prompt = p.prompt
        self.api.apply_native_cache_context(p)
        self.assertEqual(p.all_hr_prompts, p.all_prompts)
        self.assertIsInstance(p.hr_prompt, str)

    def test_completion_persists_without_browser_and_release_is_idempotent(self):
        prepared = self.prepare(total_images=2)
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt = "base" + self.marker(prepared["token"])
        p.batch_size, p.n_iter = 1, 2
        self.api.apply_native_cache_context(p)
        self.api._NATIVE_CACHE_CONTEXTS.clear()
        self.api.complete_native_cache_context(p, SimpleNamespace(images=["grid", "one", "two"], index_of_first_image=1))
        fresh = StudioDB(self.db.path)
        self.assertEqual(fresh.get_native_cache_cursor("txt2img", "cache"), 2)
        first = self.api._native_cache_release(prepared["token"], True)
        self.assertEqual(first, self.api._native_cache_release(prepared["token"], True))
        self.assertEqual(first, self.api._native_cache_release(prepared["token"], False))

    def test_partial_completion_commits_only_produced_prefix(self):
        prepared = self.prepare(total_images=2)
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt = "base" + self.marker(prepared["token"])
        p.batch_size, p.n_iter = 1, 2
        self.api.apply_native_cache_context(p)
        self.api.complete_native_cache_context(p, SimpleNamespace(images=["one"], index_of_first_image=0))
        result = self.api._native_cache_release(prepared["token"], True)
        self.assertEqual(result["next_cursor"], 1)
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["state"], "partial")

    def test_skipped_first_batch_does_not_consume_later_records(self):
        prepared = self.prepare(total_images=2)
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt = "base" + self.marker(prepared["token"])
        p.batch_size, p.n_iter = 1, 2
        self.api.apply_native_cache_context(p)
        self.api.track_native_cache_image(p, SimpleNamespace(index=1))
        self.api.complete_native_cache_context(p, SimpleNamespace(images=["second"], index_of_first_image=0))
        self.assertIsNone(self.db.get_native_cache_cursor("txt2img", "cache"))

    def test_server_cursor_is_used_when_client_does_not_override(self):
        prepared = self.prepare()
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt = "base" + self.marker(prepared["token"])
        self.api.apply_native_cache_context(p)
        self.api.complete_native_cache_context(p, SimpleNamespace(images=["one"], index_of_first_image=0))
        next_job = self.prepare(after_id=None)
        self.assertEqual(next_job["after_id"], 1)
        self.assertEqual(next_job["records"][0]["id"], 2)

    def test_configuration_survives_database_reopen(self):
        config = {"source": "processed_cache", "cache_enabled": True, "write_mode": "marker", "marker": "<cache>"}
        self.api._native_cache_state("img2img", config)
        reopened = StudioDB(Path(self.tmp.name) / "native.db")
        with patch.dict(self.api._native_cache_state.__globals__, DB=reopened):
            self.assertEqual(self.api._native_cache_state("img2img")["config"], config)
            self.assertEqual(self.api._native_cache_state("txt2img")["config"]["source"], "llm")
            self.assertFalse(self.api._native_cache_state("txt2img")["config"]["cache_enabled"])
        with self.assertRaises(ValueError):
            self.api._native_cache_state("other", config)

    def test_selectable_script_and_changed_batch_shape_are_rejected(self):
        with self.assertRaises(ValueError):
            self.prepare(selected_script="Prompts from file or textbox")
        prepared = self.prepare(total_images=2)
        p = self.StableDiffusionProcessingTxt2Img()
        p.prompt = "base" + self.marker(prepared["token"])
        p.batch_size, p.n_iter = 1, 1
        with self.assertRaises(ValueError):
            self.api.apply_native_cache_context(p)
        self.assertNotEqual(self.db.get_native_cache_job(prepared["token"])["state"], "active")


class RenderCancellationTests(unittest.TestCase):
    def setUp(self):
        self.event = threading.Event()
        self.queue_lock = threading.Lock()
        self.state = SimpleNamespace(job="", interrupted=False)
        self.state.begin = lambda job: setattr(self.state, "job", job)
        self.state.end = lambda: setattr(self.state, "job", "")
        self.state.interrupt = Mock(side_effect=lambda: setattr(self.state, "interrupted", True))
        self.p = SimpleNamespace(close=Mock())
        self.progress = ModuleType("modules.progress")
        self.progress.pending_tasks = {}
        self.progress.current_task = None
        self.progress.add_task_to_queue = lambda task: self.progress.pending_tasks.update({task: True})
        def start(task):
            self.progress.current_task = task
            self.progress.pending_tasks.pop(task, None)
        self.progress.start_task = start
        self.progress.finish_task = lambda task: setattr(self.progress, "current_task", None)
        self.processing = SimpleNamespace(StableDiffusionProcessingTxt2Img=Mock(return_value=self.p), process_images=Mock(return_value=SimpleNamespace(images=[], js=lambda: "{}")))
        self.shared = SimpleNamespace(state=self.state, opts=SimpleNamespace(), total_tqdm=SimpleNamespace(clear=Mock()))
        modules = ModuleType("modules")
        modules.processing, modules.shared, modules.progress = self.processing, self.shared, self.progress
        call_queue = ModuleType("modules.call_queue")
        call_queue.queue_lock = self.queue_lock
        self.patcher = patch.dict(sys.modules, {"modules": modules, "modules.call_queue": call_queue, "modules.progress": self.progress})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.api = ui_functions("_coerce_bool", "_parse_generation_settings", "_server_render_prompt")

    def test_render_saves_locally_without_inspecting_or_encoding_output(self):
        class Output:
            @property
            def images(self):
                raise AssertionError("Queue must not read images for return")

            def js(self):
                raise AssertionError("Queue must not serialize unused output")

        self.processing.process_images.return_value = Output()
        self.assertIsNone(self.api._server_render_prompt("cat", "txt2img", "job", cancel_event=self.event))
        self.processing.process_images.assert_called_once_with(self.p)
        self.assertFalse(self.processing.StableDiffusionProcessingTxt2Img.call_args.kwargs["do_not_save_samples"])
        self.p.close.assert_called_once()
        self.assertFalse(self.queue_lock.locked())

    def test_cancel_after_forge_rewrites_job_label(self):
        def process(p):
            self.state.job = "Batch 1 out of 2"
            self.event.set()
            deadline = time.monotonic() + 0.7
            while not self.state.interrupted and time.monotonic() < deadline:
                time.sleep(0.01)
            return SimpleNamespace(images=[], js=lambda: "{}")
        self.processing.process_images.side_effect = process
        self.api._server_render_prompt("cat", "txt2img", "job", cancel_event=self.event)
        self.state.interrupt.assert_called_once()
        self.assertFalse(self.queue_lock.locked())
        self.p.close.assert_called_once()

    def test_cancel_waiting_for_lock_never_interrupts_other_job(self):
        self.queue_lock.acquire()
        self.state.job = "another-task"
        self.event.set()
        try:
            with self.assertRaisesRegex(RuntimeError, "取消"):
                self.api._server_render_prompt("cat", "txt2img", "job", cancel_event=self.event)
            self.state.interrupt.assert_not_called()
            self.processing.process_images.assert_not_called()
            self.p.close.assert_called_once()
            self.assertNotIn("server-queue-job", self.progress.pending_tasks)
        finally:
            self.queue_lock.release()

    def test_finished_render_does_not_interrupt_later_task(self):
        self.api._server_render_prompt("cat", "txt2img", "job", {"generation_settings": {"enable_hr": "false"}}, self.event)
        self.assertIs(self.processing.StableDiffusionProcessingTxt2Img.call_args.kwargs["enable_hr"], False)
        self.state.job = "next-task"
        self.event.set()
        time.sleep(0.15)
        self.state.interrupt.assert_not_called()
        self.assertEqual(self.state.job, "next-task")


if __name__ == "__main__":
    unittest.main()
