import hashlib
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prompt_studio_ui as ui
from prompt_studio_core import CredentialStore, StudioDB


class PromptStudioApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.root = root
        self.lexicon = root / "lexicon"
        self.lexicon.mkdir()
        self.originals = {
            "db": ui.DB,
            "credentials": ui.CREDENTIALS,
            "wildcards": ui.DEFAULT_WILDCARDS,
            "call_llm": ui.call_llm,
            "generate": ui._generate,
            "independent_sequence": ui._independent_batch_sequence,
            "modules": sys.modules.get("modules"),
            "modules.shared": sys.modules.get("modules.shared"),
        }
        ui.DB = StudioDB(root / "studio.db")
        ui.CREDENTIALS = CredentialStore(root / "credentials.json")
        ui.DEFAULT_WILDCARDS = self.lexicon
        ui.call_llm = lambda *args, **kwargs: "1girl, red_hair"
        ui.DB.set_setting("llm_connections_v2", {
            "version": 2,
            "active_provider": "OpenAI Compatible",
            "providers": {
                "OpenAI Compatible": {
                    "endpoint": "http://127.0.0.1:1234/v1",
                    "model": "test-model",
                    "temperature": 0.35,
                    "timeout": 90,
                    "max_tokens": 1024,
                    "send_temperature": True,
                }
            },
        })

    def tearDown(self):
        ui.DB = self.originals["db"]
        ui.CREDENTIALS = self.originals["credentials"]
        ui.DEFAULT_WILDCARDS = self.originals["wildcards"]
        ui.call_llm = self.originals["call_llm"]
        ui._generate = self.originals["generate"]
        with ui._INDEPENDENT_BATCH_LOCK:
            ui._independent_batch_sequence = self.originals["independent_sequence"]
        with ui._BATCH_CONTROL_LOCK:
            ui._BATCH_ACTIVE_TASK_ID = ""
            ui._BATCH_CANCEL.clear()
        for name in ["modules", "modules.shared"]:
            if self.originals[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = self.originals[name]
        self.temporary.cleanup()

    @staticmethod
    def _install_shared(api_auth: str):
        shared = types.ModuleType("modules.shared")
        shared.cmd_opts = SimpleNamespace(api_auth=api_auth)
        modules = types.ModuleType("modules")
        modules.shared = shared
        sys.modules["modules"] = modules
        sys.modules["modules.shared"] = shared

    @staticmethod
    async def _request(app, client_host, method, path, **kwargs):
        transport = httpx.ASGITransport(app=app, client=(client_host, 43210))
        async with httpx.AsyncClient(transport=transport, base_url="http://plugin.test") as client:
            return await client.request(method, path, **kwargs)

    @staticmethod
    def _batch_args(source_text, skip_existing=True, skip_failed=True):
        return (
            source_text, skip_existing, skip_failed,
            "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "test-model", "",
            0.35, 90, 1024, True, 0, 0,
            True, "", False, False, 0, "Plain Prompt", 1,
            "", 0, "全部", "全部",
        )

    async def test_local_api_uses_saved_endpoint_and_blocks_remote_clients(self):
        self._install_shared("")
        app = FastAPI()
        ui.on_app_started(None, app)

        generated = await self._request(app, "127.0.0.1", "POST", "/llm-prompt-studio/v1/generate", json={"request": "red-haired girl"})
        mismatch = await self._request(app, "127.0.0.1", "POST", "/llm-prompt-studio/v1/generate", json={"request": "test", "endpoint": "http://127.0.0.1:9999/v1"})
        remote = await self._request(app, "203.0.113.10", "GET", "/llm-prompt-studio/v1/cache")

        self.assertEqual(generated.status_code, 200)
        self.assertEqual(generated.json()["prompt"], "1girl, red_hair")
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(remote.status_code, 403)

    async def test_ranbooru_handoff_api_is_idempotent_and_preserves_metadata(self):
        self._install_shared("")
        app = FastAPI()
        ui.on_app_started(None, app)
        payload = {
            "ranbooru_id": 17,
            "database_key": "abcdef0123456789",
            "tags_prompt": "1girl, silver_hair, library",
            "natural_prompt": "A silver-haired girl reading in a library.",
            "rating": "g",
            "source_score": 42,
            "booru": "danbooru",
            "post_id": "9001",
        }

        first = await self._request(app, "127.0.0.1", "POST", "/llm-prompt-studio/v1/handoff", json=payload)
        second = await self._request(app, "127.0.0.1", "POST", "/llm-prompt-studio/v1/handoff", json=payload)
        listing = await self._request(app, "127.0.0.1", "GET", "/llm-prompt-studio/v1/handoffs")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["handoff_id"], second.json()["handoff_id"])
        self.assertEqual(len(listing.json()["records"]), 1)
        stored = listing.json()["records"][0]["payload"]
        self.assertEqual(stored["source_score"], 42)
        self.assertEqual(stored["natural_prompt"], payload["natural_prompt"])

    async def test_ranbooru_handoff_process_sends_once_and_keeps_failed_record(self):
        ui.DB.set_setting("workflow_settings_v1", {})
        calls = []

        def fail_llm(*_args, **_kwargs):
            calls.append(True)
            raise RuntimeError("temporary outage")

        ui.call_llm = fail_llm
        payload = {
            "ranbooru_id": 18,
            "database_key": "abcdef0123456789",
            "tags_prompt": "1girl, blue_hair",
            "rating": "e",
        }

        with self.assertRaisesRegex(ValueError, "手动重试"):
            ui.process_ranbooru_handoff(payload)
        record = ui.DB.list_handoffs()[0]

        self.assertEqual(len(calls), 1)
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["attempts"], 1)
        self.assertIn("temporary outage", record["error"])
        self.assertEqual(ui._handoff_safety("e"), "NSFW")

    async def test_ranbooru_handoff_unexpected_exception_is_not_retried_and_is_recorded(self):
        ui.DB.set_setting("workflow_settings_v1", {})
        calls = []

        def fail_generate(*_args, **_kwargs):
            calls.append(True)
            raise RuntimeError("cache write failed")

        ui._generate = fail_generate
        with self.assertRaisesRegex(ValueError, "cache write failed"):
            ui.process_ranbooru_handoff({
                "ranbooru_id": 180,
                "database_key": "abcdef0123456789",
                "tags_prompt": "1girl, blue_hair",
            })
        record = ui.DB.list_handoffs()[0]

        self.assertEqual(len(calls), 1)
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["attempts"], 1)
        self.assertIn("cache write failed", record["error"])

    async def test_ranbooru_handoff_process_caches_with_source_provenance(self):
        ui.DB.set_setting("workflow_settings_v1", {
            "preset": "NoobAI Tags", "base_model": "NoobAI",
        })
        calls = []

        def generate_once(*_args, **_kwargs):
            calls.append(True)
            return "1girl, red_hair"

        ui.call_llm = generate_once
        payload = {
            "ranbooru_id": 19,
            "database_key": "abcdef0123456789",
            "tags_prompt": "1girl, red_hair, portrait",
            "rating": "g",
        }

        result = ui.process_ranbooru_handoff(payload)
        handoff = ui.DB.get_handoff(result["handoff_id"])
        cached = ui.DB.list_prompts()[0]

        self.assertEqual(len(calls), 1)
        self.assertEqual(handoff["status"], "completed")
        self.assertEqual(handoff["attempts"], 1)
        self.assertEqual(cached["source_kind"], "ranbooru")
        self.assertEqual(cached["score_source"], "unrated")
        self.assertIn("ranbooru:abcdef0123456789:19:llm:", cached["source_ref"])
        self.assertEqual(cached["tags"], payload["tags_prompt"])

    async def test_completed_handoff_process_reuses_result_without_another_llm_call(self):
        calls = []
        ui.call_llm = lambda *_args, **_kwargs: calls.append(True) or "1girl, red_hair"
        payload = {
            "ranbooru_id": 29,
            "database_key": "abcdef0123456789",
            "tags_prompt": "1girl, red_hair, portrait",
            "rating": "g",
        }

        first = ui.process_ranbooru_handoff(payload)
        second = ui.process_ranbooru_handoff(payload)

        self.assertEqual(len(calls), 1)
        self.assertEqual(second["handoff_id"], first["handoff_id"])
        self.assertEqual(second["prompt"], first["prompt"])
        self.assertIn("复用", second["status"])

    async def test_superseded_handoff_discards_stale_result_and_cache_write(self):
        original_payload = {
            "ranbooru_id": 30,
            "database_key": "abcdef0123456789",
            "tags_prompt": "old tags",
        }
        updated_payload = {**original_payload, "tags_prompt": "new tags"}

        def superseding_generate(*_args, **_kwargs):
            ui.receive_ranbooru_handoff(updated_payload, "process_and_cache")
            return "stale result", "system", "生成完成"

        ui._generate = superseding_generate
        with self.assertRaisesRegex(ValueError, "新版本替代"):
            ui.process_ranbooru_handoff(original_payload)

        pending = ui.DB.list_handoffs()[0]
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["payload"]["tags_prompt"], "new tags")
        self.assertEqual(ui.DB.list_prompts(), [])

        ui._generate = lambda *_args, **_kwargs: ("current result", "system", "生成完成")
        result = ui.process_ranbooru_handoff(pending["id"])
        self.assertEqual(result["prompt"], "current result")
        self.assertEqual(ui.DB.get_handoff(pending["id"])["status"], "completed")

    async def test_ranbooru_handoff_process_uses_current_natural_variant(self):
        captured = {}

        def fake_generate(request, source_tags, *_args):
            captured.update(request=request, source_tags=source_tags)
            return "A polished natural prompt.", "system", "生成完成"

        ui._generate = fake_generate
        result = ui.process_ranbooru_handoff({
            "ranbooru_id": 20,
            "database_key": "abcdef0123456789",
            "tags_prompt": "1girl, green_hair, forest",
            "natural_prompt": "A green-haired girl standing in a forest.",
            "selected_prompt": "A green-haired girl standing in a forest.",
            "selected_is_natural": True,
            "rating": "g",
        })

        self.assertEqual(result["prompt"], "A polished natural prompt.")
        self.assertEqual(captured["request"], "A green-haired girl standing in a forest.")
        self.assertEqual(captured["source_tags"], "")

    async def test_ranbooru_handoff_rejects_non_boolean_variant_flag(self):
        with self.assertRaisesRegex(ValueError, "必须是布尔值"):
            ui.receive_ranbooru_handoff({
                "tags_prompt": "1girl",
                "selected_is_natural": "false",
            })

    async def test_ranbooru_handoff_rejects_lossy_source_identity(self):
        with self.assertRaisesRegex(ValueError, "16 位十六进制"):
            ui.receive_ranbooru_handoff({
                "database_key": "same-key!",
                "ranbooru_id": "1",
                "tags_prompt": "1girl",
            })

    async def test_corrupt_handoff_payload_keeps_evidence_and_becomes_error(self):
        handoff_id = ui.receive_ranbooru_handoff({
            "database_key": "abcdef0123456789",
            "ranbooru_id": "21",
            "tags_prompt": "1girl",
        })["handoff_id"]
        conn = sqlite3.connect(ui.DB.path)
        try:
            conn.execute("UPDATE handoffs SET payload_json=? WHERE id=?", ('{"broken":', handoff_id))
            conn.commit()
        finally:
            conn.close()

        damaged = ui.DB.get_handoff(handoff_id)
        with self.assertRaisesRegex(ValueError, "JSON 已损坏"):
            ui.process_ranbooru_handoff(handoff_id)
        persisted = ui.DB.get_handoff(handoff_id)

        self.assertEqual(damaged["payload_raw"], '{"broken":')
        self.assertIn("JSON 已损坏", damaged["payload_decode_error"])
        self.assertEqual(persisted["status"], "error")
        self.assertIn("JSON 已损坏", persisted["error"])
        with self.assertRaisesRegex(ValueError, "不支持的字符"):
            ui.receive_ranbooru_handoff({
                "database_key": "abcdef0123456789",
                "ranbooru_id": "1/../../2",
                "tags_prompt": "1girl",
            })

    async def test_remote_api_requires_valid_forge_basic_auth(self):
        self._install_shared("tester:secret")
        app = FastAPI()
        ui.on_app_started(None, app)

        missing = await self._request(app, "203.0.113.10", "GET", "/llm-prompt-studio/v1/cache")
        valid = await self._request(app, "203.0.113.10", "GET", "/llm-prompt-studio/v1/cache", auth=("tester", "secret"))

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(valid.status_code, 200)

    async def test_provider_settings_and_keys_are_restored_independently(self):
        message, _, saved_model = ui._save_llm_settings(
            "Anthropic", "https://api.anthropic.com/", "claude-test", "anthropic-key", 0.2, 120, 2048, False,
        )
        self.assertIn("Anthropic 设置已保存", message)
        self.assertIn("模型 ID：claude-test", message)
        self.assertEqual(saved_model["value"], "claude-test")
        message, _, saved_model = ui._save_llm_settings(
            "Google Gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini-test", "gemini-key", 0.6, 80, 4096, True,
        )
        self.assertIn("Google Gemini 设置已保存", message)
        self.assertEqual(saved_model["value"], "gemini-test")

        anthropic = ui._connection_settings("Anthropic")
        gemini = ui._connection_settings("Google Gemini")
        self.assertEqual(anthropic["endpoint"], "https://api.anthropic.com")
        self.assertEqual(anthropic["model"], "claude-test")
        self.assertFalse(anthropic["send_temperature"])
        self.assertEqual(gemini["max_tokens"], 4096)
        self.assertEqual(ui.CREDENTIALS.resolve("", "Anthropic", anthropic["endpoint"]), "anthropic-key")
        self.assertEqual(ui.CREDENTIALS.resolve("", "Google Gemini", gemini["endpoint"]), "gemini-key")

        ui._clear_llm_credentials("Anthropic", anthropic["endpoint"])
        self.assertEqual(ui.CREDENTIALS.resolve("", "Anthropic", anthropic["endpoint"]), "")
        self.assertEqual(ui.CREDENTIALS.resolve("", "Google Gemini", gemini["endpoint"]), "gemini-key")

    async def test_saved_model_is_restored_after_database_reopen_and_page_load(self):
        message, _, _ = ui._save_llm_settings(
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "local-model", "", 0.35, 90, 1024, True,
        )
        self.assertIn("模型 ID：local-model", message)

        ui.DB = StudioDB(ui.DB.path)
        loaded = ui._load_active_connection_settings()

        self.assertEqual(loaded[0], "OpenAI Compatible")
        self.assertEqual(loaded[1], "http://127.0.0.1:1234/v1")
        self.assertEqual(loaded[2], "local-model")

    async def test_empty_model_cannot_overwrite_saved_connection(self):
        before = ui._connection_settings("OpenAI Compatible")

        message, endpoint_update, model_update = ui._save_llm_settings(
            "OpenAI Compatible", "http://127.0.0.1:9999/v1", "", "", 0.5, 120, 2048, True,
        )

        self.assertIn("保存失败", message)
        self.assertIn("LLM model ID is required", message)
        self.assertEqual(endpoint_update, ui.gr.update())
        self.assertEqual(model_update, ui.gr.update())
        self.assertEqual(ui._connection_settings("OpenAI Compatible"), before)

    async def test_provider_ui_choices_are_derived_from_registry(self):
        self.assertEqual(
            [provider for _, provider in ui.PROVIDER_UI_CHOICES],
            list(ui.PROVIDER_PROFILES),
        )
        self.assertTrue(all(label == ui.PROVIDER_PROFILES[provider]["ui_label"] for label, provider in ui.PROVIDER_UI_CHOICES))

    async def test_legacy_connection_setting_is_deleted_and_ignored(self):
        ui.DB.set_setting("llm_connection", {
            "backend": "OpenAI Compatible",
            "endpoint": "http://127.0.0.1:9999/v1",
            "model": "stale-legacy-model",
        })
        ui.DB.delete_setting("llm_connections_v2")

        settings = ui._connection_settings()
        self.assertEqual(settings["provider"], "OpenAI Compatible")
        self.assertEqual(settings["model"], "")
        self.assertEqual(settings["endpoint"], "http://127.0.0.1:1234/v1")
        self.assertIsNone(ui.DB.get_setting("llm_connection"))

    async def test_generate_api_rejects_removed_backend_alias(self):
        self._install_shared("")
        app = FastAPI()
        ui.on_app_started(None, app)

        response = await self._request(
            app,
            "127.0.0.1",
            "POST",
            "/llm-prompt-studio/v1/generate",
            json={"request": "red-haired girl", "backend": "OpenAI Compatible"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported fields", response.json()["detail"])

    async def test_generate_api_rejects_unknown_fields(self):
        self._install_shared("")
        app = FastAPI()
        ui.on_app_started(None, app)

        response = await self._request(
            app,
            "127.0.0.1",
            "POST",
            "/llm-prompt-studio/v1/generate",
            json={"request": "red-haired girl", "future_internal_secret": "blocked"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported fields", response.json()["detail"])

    async def test_generate_api_rejects_invalid_policy_enums(self):
        self._install_shared("")
        app = FastAPI()
        ui.on_app_started(None, app)

        for payload in [
            {"request": "test", "safety": "sfw"},
            {"request": "test", "safety": ["SFW"]},
            {"request": "test", "preset": "Unknown"},
            {"request": "test", "base_model": "Unknown"},
            {"request": "test", "structured_mode": "XML"},
        ]:
            response = await self._request(app, "127.0.0.1", "POST", "/llm-prompt-studio/v1/generate", json=payload)
            self.assertEqual(response.status_code, 400, payload)

    async def test_generate_api_rejects_non_boolean_switches(self):
        self._install_shared("")
        app = FastAPI()
        ui.on_app_started(None, app)

        for field in ("send_temperature", "remove_bad", "shuffle", "spaces", "cache_result"):
            for value in ("false", 0, None):
                payload = {"request": "test", field: value}
                with self.subTest(field=field, value=value):
                    response = await self._request(
                        app, "127.0.0.1", "POST", "/llm-prompt-studio/v1/generate", json=payload,
                    )
                    self.assertEqual(response.status_code, 400, payload)
                    self.assertIn("must be a boolean", response.json()["detail"])

    async def test_inline_generation_failure_preserves_existing_prompt(self):
        original_generate = ui._generate
        ui._generate = lambda *args: ("", "system", "生成失败")
        try:
            generated, system, status, prompt_update = ui._inline_generate(
                "request", "", "NoobAI Tags", "", "NoobAI", "SFW", "", "",
                0, 0, True, "", False, False, 0, "Plain Prompt", 1, 0, False,
            )
        finally:
            ui._generate = original_generate

        self.assertEqual(generated, "")
        self.assertEqual(system, "system")
        self.assertEqual(status, "生成失败")
        self.assertEqual(prompt_update.get("__type__"), "update")
        self.assertNotIn("value", prompt_update)
        shared_workflow = ui._workflow_settings()
        self.assertEqual(shared_workflow["preset"], "NoobAI Tags")
        self.assertEqual(shared_workflow["base_model"], "NoobAI")
        self.assertEqual(shared_workflow["safety"], "SFW")

    async def test_app_start_indexes_saved_custom_wildcard_path(self):
        (self.lexicon / "custom.txt").write_text("custom_saved_tag\n", encoding="utf-8")
        ui.DB.set_setting("workflow_settings_v1", {"wildcard_path": str(self.lexicon)})
        self._install_shared("")
        app = FastAPI()

        ui.on_app_started(None, app)

        self.assertEqual(ui.DB.wildcard_matches("custom_saved"), ["custom_saved_tag"])

    async def test_connection_probe_uses_current_temperature_settings(self):
        captured = []
        ui.call_llm = lambda *args, **kwargs: captured.append((args, kwargs)) or "READY"

        result = ui._test_connection(
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "test-model", "",
            0.7, 45, 512, True,
        )

        self.assertIn("连接成功", result)
        args, kwargs = captured[0]
        self.assertFalse(kwargs)
        self.assertEqual(args[6], 0.7)
        self.assertEqual(args[7], 45)
        self.assertEqual(args[8], 64)
        self.assertTrue(args[9])

    async def test_workflow_parameters_are_saved_and_restored(self):
        message = ui._save_workflow_settings(
            "NoobAI Tags", "custom system", "NoobAI", "NSFW", "local policy", "最多 40 个标签",
            "Regional JSON", 4, True, "watermark", True, True, 40,
            5, 8.5, 9, True,
            False, True,
            "http://127.0.0.1:7861", "wd-test", 0.42, str(self.lexicon),
        )

        restored = ui._workflow_settings()
        self.assertIn("下次打开", message)
        self.assertEqual(restored["preset"], "NoobAI Tags")
        self.assertEqual(restored["base_model"], "NoobAI")
        self.assertEqual(restored["structured_mode"], "Regional JSON")
        self.assertTrue(restored["batch_skip_failed"])
        self.assertFalse(restored["batch_skip_existing"])
        self.assertEqual(ui.DB.get_setting("workflow_settings_v1")["version"], 2)
        self.assertEqual(restored["wd_model"], "wd-test")
        self.assertEqual(restored["wildcard_path"], str(self.lexicon))

        reset_values = ui._reset_workflow_settings()
        self.assertEqual(len(reset_values), 24)
        self.assertEqual(reset_values[0], ui.WORKFLOW_DEFAULTS["preset"])
        self.assertIn("已恢复默认", reset_values[-1])
        self.assertIsNone(ui.DB.get_setting("workflow_settings_v1"))

    async def test_legacy_workflow_migrates_batch_cache_skipping_to_opt_in(self):
        ui.DB.set_setting("workflow_settings_v1", {"version": 1, "batch_skip_existing": True})

        restored = ui._workflow_settings()

        self.assertFalse(restored["batch_skip_existing"])

    async def test_generated_cache_uses_manual_score_without_extra_llm_request(self):
        generated, _system, status = ui._generate(
            "red-haired mage", "", "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "judge-model", "",
            0.35, 90, 1024, True, 0, 0,
            True, "", False, False, 0, "Plain Prompt", 1,
            4, True,
        )
        record = ui.DB.list_prompts()[0]

        self.assertTrue(generated)
        self.assertEqual(record["score"], 4)
        self.assertEqual(record["score_source"], "manual")
        self.assertEqual(record["score_model"], "")
        self.assertEqual(record["score_reason"], "生成时手动评分")
        self.assertIn("生成完成", status)

    async def test_generation_rejects_prompt_emptied_by_postprocessing(self):
        ui.call_llm = lambda *_args, **_kwargs: "watermark, signature"

        generated, _system, status = ui._generate(
            "test", "", "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "test-model", "",
            0.35, 90, 1024, True, 0, 0,
            True, "", False, False, 0, "Plain Prompt", 1,
            0, True,
        )

        self.assertEqual(generated, "")
        self.assertIn("未返回可用", status)
        self.assertEqual(ui.DB.list_prompts(), [])

    async def test_repeated_generated_cache_dedupes_identical_content(self):
        args = (
            "same request", "", "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "test-model", "",
            0.35, 90, 1024, True, 0, 0,
            True, "", False, False, 0, "Plain Prompt", 1,
            0, True,
        )

        ui._generate(*args)
        ui._generate(*args)

        self.assertEqual(len(ui.DB.list_prompts()), 1)

    async def test_cache_score_does_not_call_llm_evaluator(self):
        _generated, _system, status = ui._generate(
            "red-haired mage", "", "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "judge-model", "",
            0.35, 90, 1024, True, 0, 0,
            True, "", False, False, 0, "Plain Prompt", 1,
            9, True,
        )
        record = ui.DB.list_prompts()[0]

        self.assertEqual(record["score"], 9)
        self.assertEqual(record["score_source"], "manual")
        self.assertIn("生成完成", status)

    async def test_manual_cache_edit_invalidates_previous_llm_score(self):
        record_id = ui.DB.save_prompt(
            "evaluated prompt", output_mode="NoobAI Tags", base_model="NoobAI", score=9, tags="source",
            score_source="llm", score_reason="Original evaluation", score_model="judge-model",
            source_kind="ranbooru", source_ref="ranbooru:test:1:tags",
        )

        ui._save_record(
            str(record_id), "edited prompt", "", "NoobAI Tags", "NoobAI", 9.5, "source",
            "", 0, "全部", "全部",
        )
        record = ui.DB.get_prompt(record_id)

        self.assertEqual(record["score_source"], "manual")
        self.assertEqual(record["score_reason"], "")
        self.assertEqual(record["score_model"], "")
        self.assertEqual(record["source_kind"], "")
        self.assertEqual(record["source_ref"], "")

    async def test_ranbooru_link_preview_sync_and_settings_are_idempotent(self):
        path = self.root / "ranbooru_tag_cache.db"
        tags = "1girl, silver_hair, library"
        conn = sqlite3.connect(path)
        try:
            conn.execute("""
                CREATE TABLE tags (
                    id INTEGER PRIMARY KEY, tags TEXT NOT NULL, tags_prompt TEXT,
                    natural_prompt TEXT, natural_source_hash TEXT, score INTEGER, rating TEXT
                )
            """)
            conn.execute(
                "INSERT INTO tags VALUES(?,?,?,?,?,?,?)",
                (1, tags, tags, "A silver-haired girl in a library.", hashlib.sha256(tags.encode()).hexdigest(), 25, "g"),
            )
            conn.commit()
        finally:
            conn.close()

        link_args = (
            str(path), "both", "sfw", 10, 0,
            "NoobAI Tags", "NoobAI", "Krea 2 Natural", "Krea 2",
        )
        preview, preview_status = ui._preview_ranbooru_link(*link_args)
        first_status, _table, _choices = ui._sync_ranbooru_link(*link_args)
        second_status, _table, _choices = ui._sync_ranbooru_link(*link_args)
        records = ui.DB.list_prompts()
        natural = next(record for record in records if record["output_mode"] == "Krea 2 Natural")
        ui.DB.save_prompt(
            natural["prompt"], output_mode=natural["output_mode"], base_model=natural["base_model"],
            score=9, tags=natural["tags"], record_id=natural["id"], score_source="llm",
            score_reason="Verified", score_model="judge",
        )
        conn = sqlite3.connect(path)
        try:
            conn.execute("UPDATE tags SET tags='1girl, blue_hair', tags_prompt='1girl, blue_hair' WHERE id=1")
            conn.commit()
        finally:
            conn.close()
        stale_status, _table, _choices = ui._sync_ranbooru_link(*link_args)
        stale_again_status, _table, _choices = ui._sync_ranbooru_link(*link_args)
        invalidated = ui.DB.get_prompt(natural["id"])
        saved = ui._ranbooru_link_settings()

        self.assertEqual(len(preview["value"]), 2)
        self.assertIn("可同步 Prompt 2", preview_status)
        self.assertIn("新增 2", first_status)
        self.assertIn("未变化 2", second_status)
        self.assertIn("失效评分 1", stale_status)
        self.assertIn("失效评分 0", stale_again_status)
        self.assertEqual(invalidated["score_source"], "unrated")
        self.assertEqual({record["source_kind"] for record in records}, {"ranbooru"})
        self.assertEqual({record["score_source"] for record in records}, {"unrated"})
        self.assertEqual(saved["database_path"], str(path))
        self.assertEqual(saved["natural_base_model"], "Krea 2")

    async def test_invalid_png_payload_refresh_preserves_error_evidence(self):
        table, selection, current, status = ui._png_batch_refresh("not-json", 1)

        self.assertEqual(table, [])
        self.assertEqual(selection, 1)
        self.assertEqual(current, "")
        self.assertIn("无效", status)

    async def test_batch_and_direct_import_previews_report_queue_shape(self):
        queue, status = ui._preview_batch_sources(
            "first request\nfirst request\n# note\nsecond request", True, "NoobAI Tags", "NoobAI",
        )
        direct, direct_status = ui._preview_bulk_cache(
            "8\tfirst prompt\nsecond prompt\n# note", "NoobAI Tags", "NoobAI", 6,
        )

        self.assertEqual(len(queue["value"]), 3)
        self.assertEqual(len(queue["value"][0]), 4)
        self.assertEqual(queue["value"][0][2], "")
        self.assertIn("重复输入 1 条", status)
        self.assertIn("均保留为独立任务", status)
        self.assertEqual(direct["value"][0][1], 8)
        self.assertEqual(direct["value"][1][1], 6)
        self.assertIn("2 条可导入", direct_status)

    async def test_batch_sends_each_input_line_and_collects_errors_and_cached_skips(self):
        ui.DB.save_prompt("already cached", "", "NoobAI Tags", "NoobAI", 7, "cached")
        attempts = {}

        def fake_generate(source, *_args):
            attempts[source] = attempts.get(source, 0) + 1
            if source == "once":
                return "1girl, generated once", "", "生成完成"
            return "", "", f"模拟错误 {attempts[source]}"

        ui._generate = fake_generate
        results = list(ui._batch_generate(*self._batch_args("once\nonce\ncached\nbad")))
        status, _table, _choices, issue_table, issue_choices, issue_state, result_rows = results[-1]

        self.assertEqual(attempts, {"once": 2, "bad": 1})
        self.assertIn("LLM 请求 3", status)
        self.assertIn("问题汇总 2 条", status)
        self.assertEqual([item["status"] for item in issue_state], ["已跳过", "生成错误"])
        self.assertEqual(issue_state[1]["attempts"], 1)
        self.assertEqual(len(issue_table["value"]), 2)
        self.assertEqual(len(issue_choices["choices"]), 2)
        self.assertEqual(result_rows["value"][0][2], "1girl, generated once")
        self.assertEqual(result_rows["value"][0][3], "生成成功")
        self.assertTrue(ui.DB.has_source_prompt("once", "NoobAI Tags", "NoobAI"))

    async def test_batch_repeats_same_request_and_keeps_distinct_results(self):
        attempt = 0
        directives = []

        def fake_generate(source, *_args):
            nonlocal attempt
            attempt += 1
            directives.append(_args[-1])
            return f"1girl, scene_{attempt}", "", "生成完成"

        ui._generate = fake_generate
        results = list(ui._batch_generate(*self._batch_args("same request\nsame request", False, True)))
        status = results[-1][0]
        records = ui.DB.list_prompts()

        self.assertEqual(attempt, 2)
        self.assertIn("LLM 请求 2", status)
        self.assertEqual({record["prompt"] for record in records}, {"1girl, scene_1", "1girl, scene_2"})
        self.assertIn("独立任务第 1/2 条", directives[0])
        self.assertIn("独立任务第 2/2 条", directives[1])
        self.assertNotEqual(directives[0], directives[1])

    async def test_batch_calls_llm_with_fresh_stateless_diversity_directives(self):
        calls = []

        def capture_call(*args, **kwargs):
            calls.append((args, kwargs))
            return f"1girl, independent_scene_{len(calls)}"

        ui.call_llm = capture_call
        results = list(ui._batch_generate(*self._batch_args("alpha\nbeta\ngamma", False, True)))

        self.assertEqual(len(calls), 3)
        self.assertIn("LLM 请求 3", results[-1][0])
        self.assertEqual([call[0][6] for call in calls], [0.9, 0.9, 0.9])
        self.assertEqual(
            [call[0][5] for call in calls],
            [
                ui.build_user_message("alpha"),
                ui.build_user_message("beta"),
                ui.build_user_message("gamma"),
            ],
        )
        systems = [call[0][4] for call in calls]
        self.assertEqual(len(set(systems)), 3)
        for index, system in enumerate(systems, start=1):
            self.assertIn("independent one-shot batch request", system)
            self.assertIn(ui._INDEPENDENT_BATCH_BLUEPRINTS[index - 1], system)
            self.assertNotIn("independent_scene_", system)
        for args, kwargs in calls:
            self.assertEqual(len(args), 10)
            self.assertNotIn("history", kwargs)
            self.assertNotIn("messages", kwargs)

        high_temperature_args = list(self._batch_args("delta", False, True))
        high_temperature_args[13] = 1.1
        list(ui._batch_generate(*high_temperature_args))
        self.assertEqual(calls[-1][0][6], 1.1)

    async def test_batch_diversity_plan_does_not_repeat_across_supported_queue_size(self):
        semantic_directives = {
            ui._independent_batch_directive(index).split("独立请求标识:", 1)[0]
            for index in range(1, 10002)
        }

        self.assertEqual(len(semantic_directives), 10001)

    async def test_inline_generation_uses_fresh_stateless_request_each_time(self):
        calls = []

        def capture_call(*args, **kwargs):
            calls.append((args, kwargs))
            return f"1girl, inline_scene_{len(calls)}"

        ui.call_llm = capture_call
        with ui._INDEPENDENT_BATCH_LOCK:
            ui._independent_batch_sequence = 0
        inline_args = (
            "same request", "", "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            0, 0, True, "", False, False, 0, "Plain Prompt", 1, 0, False, "txt2img",
        )

        first = ui._inline_generate(*inline_args)
        second = ui._inline_generate(*inline_args)

        self.assertEqual([first[0], second[0]], ["1girl, inline_scene_1", "1girl, inline_scene_2"])
        self.assertEqual(len(calls), 2)
        self.assertEqual([call[0][6] for call in calls], [0.9, 0.9])
        self.assertEqual(calls[0][0][5], calls[1][0][5])
        self.assertNotEqual(calls[0][0][4], calls[1][0][4])
        self.assertIn(ui._INDEPENDENT_BATCH_BLUEPRINTS[0], calls[0][0][4])
        self.assertIn(ui._INDEPENDENT_BATCH_BLUEPRINTS[1], calls[1][0][4])
        self.assertNotIn("inline_scene_1", calls[1][0][4])

    async def test_batch_cache_skip_only_considers_records_existing_at_batch_start(self):
        attempt = 0

        def fake_generate(source, *_args):
            nonlocal attempt
            attempt += 1
            return f"1girl, variation_{attempt}", "", "生成完成"

        ui._generate = fake_generate
        repeated = "\n".join(["same request"] * 12)
        results = list(ui._batch_generate(*self._batch_args(repeated, True, True)))

        self.assertEqual(attempt, 12)
        self.assertIn("LLM 请求 12", results[-1][0])

    async def test_batch_parser_does_not_silently_truncate_large_queues(self):
        sources, stats = ui._parse_batch_sources("\n".join(f"prompt {index}" for index in range(10001)))

        self.assertEqual(len(sources), 10001)
        self.assertEqual(sources[-1], "prompt 10000")
        self.assertEqual(stats["duplicates"], 0)

    async def test_auto_loop_dispatch_can_cache_without_llm_scoring(self):
        captured = {}

        def fake_generate(*args):
            captured["args"] = args
            return "generated", "system", "ok"

        ui._generate = fake_generate
        result = ui._generate_auto_loop(
            "request", "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "model", "",
            0.3, 90, 1024, True, 0, 0, True, "", False, False, 0,
            "Plain Prompt", 1,
        )

        self.assertEqual(result[0], "generated")
        self.assertEqual(captured["args"][-8:-5], (1, 0, False))
        self.assertEqual(captured["args"][-5:-2], ("", "", False))
        self.assertIs(captured["args"][-2], ui._AUTO_LOOP_CANCEL)
        self.assertIn("independent one-shot batch request", captured["args"][-1])

        result = ui._generate_auto_loop(
            "request", "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "model", "",
            0.3, 90, 1024, True, 0, 0, True, "", False, False, 0,
            "Plain Prompt", 1, True,
        )
        self.assertEqual(result[0], "generated")
        self.assertEqual(captured["args"][-8:-5], (1, 0, True))
        self.assertEqual(captured["args"][-5], "auto_loop")
        self.assertTrue(captured["args"][-4].startswith("auto_loop:"))
        self.assertTrue(captured["args"][-3])
        self.assertIs(captured["args"][-2], ui._AUTO_LOOP_CANCEL)
        self.assertIn("independent one-shot batch request", captured["args"][-1])

    async def test_auto_loop_cache_is_unrated_and_model_configuration_is_part_of_identity(self):
        common = (
            "request", "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1",
        )
        tail = ("", 0.3, 90, 1024, True, 0, 0, True, "", False, False, 0, "Plain Prompt", 1, True)

        first = ui._generate_auto_loop(*common, "model-a", *tail)
        second = ui._generate_auto_loop(*common, "model-b", *tail)
        records = ui.DB.list_prompts()

        self.assertEqual(first[0], "1girl, red_hair")
        self.assertEqual(second[0], "1girl, red_hair")
        self.assertEqual(len(records), 2)
        self.assertEqual({record["score_source"] for record in records}, {"unrated"})
        self.assertEqual({record["source_kind"] for record in records}, {"auto_loop"})
        self.assertEqual(len({record["source_ref"] for record in records}), 2)

    async def test_auto_loop_cache_keeps_each_unique_result_from_the_same_configuration(self):
        outputs = iter(["1girl, red_hair", "1girl, blue_hair"])
        ui.call_llm = lambda *_args, **_kwargs: next(outputs)
        args = (
            "request", "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "model", "",
            0.3, 90, 1024, True, 0, 0, True, "", False, False, 0,
            "Plain Prompt", 1, True,
        )

        ui._generate_auto_loop(*args)
        ui._generate_auto_loop(*args)
        records = ui.DB.list_prompts()

        self.assertEqual({record["prompt"] for record in records}, {"1girl, red_hair", "1girl, blue_hair"})
        self.assertEqual(len({record["source_ref"] for record in records}), 2)
        self.assertEqual({record["score_source"] for record in records}, {"unrated"})

    async def test_batch_cache_is_unrated_without_llm_evaluation_request(self):
        ui._generate = lambda source, *_args: (f"generated {source}", "", "生成完成")

        results = list(ui._batch_generate(*self._batch_args("alpha", False, True)))
        status = results[-1][0]
        record = ui.DB.list_prompts()[0]

        self.assertIn("LLM 请求 1", status)
        self.assertEqual(record["score"], 0)
        self.assertEqual(record["score_source"], "unrated")
        self.assertIn("批量生成未评分", record["score_reason"])

    async def test_batch_can_stop_after_single_failure_and_collect_unprocessed(self):
        calls = []

        def fake_generate(source, *_args):
            calls.append(source)
            return "", "", "永久错误"

        ui._generate = fake_generate
        results = list(ui._batch_generate(*self._batch_args("bad\nlater", False, False)))
        status, _table, _choices, _issue_table, _issue_choices, issue_state, _result_rows = results[-1]

        self.assertEqual(calls, ["bad"])
        self.assertIn("因错误停止", status)
        self.assertEqual([item["status"] for item in issue_state], ["生成错误", "未处理"])
        self.assertEqual(issue_state[0]["attempts"], 1)

    async def test_manual_retry_runs_only_selected_issue_and_preserves_the_rest(self):
        ui.DB.save_prompt("old alpha", "", "NoobAI Tags", "NoobAI", 7, "alpha")
        calls = []

        def fake_generate(source, *_args):
            calls.append(source)
            return f"generated {source}", "", "生成完成"

        ui._generate = fake_generate
        issues = [
            {"index": 2, "source": "alpha", "status": "已跳过", "reason": "已有缓存", "attempts": 0},
            {"index": 5, "source": "beta", "status": "生成错误", "reason": "超时", "attempts": 3},
        ]
        retry_args = (
            ["2:alpha"], issues, True,
            "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "test-model", "",
            0.35, 90, 1024, True, 0, 0,
            True, "", False, False, 0, "Plain Prompt", 1,
            "", 0, "全部", "全部",
        )
        results = list(ui._retry_batch_issues(*retry_args))
        status, _table, _choices, issue_table, issue_choices, issue_state, _result_rows = results[-1]

        self.assertEqual(calls, ["alpha"])
        self.assertIn("手动重试", status)
        self.assertEqual([item["source"] for item in issue_state], ["beta"])
        self.assertEqual(issue_table["value"][0][0], 5)
        self.assertEqual(issue_choices["choices"][0][1], "5:beta")
        self.assertTrue(ui.DB.has_source_prompt("alpha", "NoobAI Tags", "NoobAI"))

    async def test_manual_retry_selects_only_one_duplicate_issue(self):
        calls = []
        ui._generate = lambda source, *_args: (calls.append(source) or f"generated {source}", "", "生成完成")
        issues = [
            {"index": 1, "source": "same", "status": "生成错误", "reason": "超时", "attempts": 1},
            {"index": 2, "source": "same", "status": "生成错误", "reason": "超时", "attempts": 1},
        ]
        retry_args = (
            ["1:same"], issues, True,
            "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "test-model", "",
            0.35, 90, 1024, True, 0, 0,
            True, "", False, False, 0, "Plain Prompt", 1,
            "", 0, "全部", "全部",
        )

        results = list(ui._retry_batch_issues(*retry_args))
        _status, _table, _choices, issue_table, issue_choices, issue_state, _result_rows = results[-1]

        self.assertEqual(calls, ["same"])
        self.assertEqual([item["index"] for item in issue_state], [2])
        self.assertEqual(issue_table["value"][0][0], 2)
        self.assertEqual([choice[1] for choice in issue_choices["choices"]], ["2:same"])

    async def test_batch_cancel_collects_every_unprocessed_source(self):
        calls = []

        def fake_generate(source, *_args):
            calls.append(source)
            ui._BATCH_CANCEL.set()
            return f"generated {source}", "", "生成完成"

        ui._generate = fake_generate
        results = list(ui._batch_generate(*self._batch_args("first\nsecond\nthird", False, True)))
        status, _table, _choices, _issue_table, _issue_choices, issue_state, _result_rows = results[-1]

        self.assertEqual(calls, ["first"])
        self.assertIn("任务已取消：处理 1/3", status)
        self.assertEqual([item["source"] for item in issue_state], ["second", "third"])
        self.assertEqual({item["status"] for item in issue_state}, {"已取消"})
        self.assertTrue(ui.DB.has_source_prompt("first", "NoobAI Tags", "NoobAI"))

        selected = ui._select_all_batch_issues(issue_state)
        self.assertEqual(selected["value"], ["2:second", "3:third"])

    async def test_batch_cancel_during_retry_marks_current_item_cancelled(self):
        def fake_generate(source, *_args):
            ui._BATCH_CANCEL.set()
            return "", "", "生成失败：LLM request cancelled"

        ui._generate = fake_generate
        results = list(ui._batch_generate(*self._batch_args("first\nsecond", False, True)))
        status, _table, _choices, _issue_table, _issue_choices, issues, rows = results[-1]

        self.assertIn("任务已取消", status)
        self.assertEqual([item["status"] for item in issues], ["已取消", "已取消"])
        self.assertEqual([row[3] for row in rows["value"]], ["已取消", "已取消"])
        self.assertFalse(ui.DB.has_source_prompt("first", "NoobAI Tags", "NoobAI"))

    async def test_manual_retry_lock_contention_does_not_duplicate_issues(self):
        issues = [
            {"index": 2, "source": "alpha", "status": "已跳过", "reason": "已有缓存", "attempts": 0},
            {"index": 5, "source": "beta", "status": "生成错误", "reason": "超时", "attempts": 3},
        ]
        retry_args = (
            ["2:alpha"], issues, True,
            "NoobAI Tags", "", "NoobAI", "SFW", "", "",
            "OpenAI Compatible", "http://127.0.0.1:1234/v1", "test-model", "",
            0.35, 90, 1024, True, 0, 0,
            True, "", False, False, 0, "Plain Prompt", 1,
            "", 0, "全部", "全部",
        )

        ui._BATCH_LOCK.acquire()
        try:
            results = list(ui._retry_batch_issues(*retry_args))
        finally:
            ui._BATCH_LOCK.release()

        status, _table, _choices, _issue_table, issue_choices, issue_state, _result_rows = results[-1]
        self.assertIn("已有批量任务正在运行", status)
        self.assertEqual([item["source"] for item in issue_state], ["alpha", "beta"])
        self.assertEqual([choice[1] for choice in issue_choices["choices"]], ["2:alpha", "5:beta"])
        self.assertEqual(issue_choices["value"], ["2:alpha"])

    async def test_only_the_owning_session_can_cancel_a_batch(self):
        with ui._BATCH_CONTROL_LOCK:
            ui._BATCH_ACTIVE_TASK_ID = "owner-session"
            ui._BATCH_CANCEL.clear()

        rejected = ui._cancel_batch_generation("other-session")
        self.assertIn("未发送取消请求", rejected)
        self.assertFalse(ui._BATCH_CANCEL.is_set())

        accepted = ui._cancel_batch_generation("owner-session")
        self.assertIn("已请求取消", accepted)
        self.assertTrue(ui._BATCH_CANCEL.is_set())

    async def test_cache_table_selection_loads_editor_and_multiselect_preview(self):
        record_id = ui.DB.save_prompt(
            "selected prompt", "negative", "NoobAI Tags", "NoobAI", 9, "source tags",
        )
        rows = ui._as_rows(ui.DB.list_prompts())
        event = SimpleNamespace(index=(0, 5))

        selected_update, loaded_id, prompt, negative, output_mode, base_model, score, tags, status = ui._select_cache_row(rows, event)

        self.assertEqual(selected_update["value"], [str(record_id)])
        self.assertEqual(loaded_id, str(record_id))
        self.assertEqual(prompt, "selected prompt")
        self.assertEqual(negative, "negative")
        self.assertEqual(output_mode, "NoobAI Tags")
        self.assertEqual(base_model, "NoobAI")
        self.assertEqual(score, 9)
        self.assertEqual(tags, "source tags")
        self.assertIn("已载入", status)
        preview, previewed_ids = ui._preview_selected([str(record_id)])
        self.assertIn("将操作 1 条记录", preview)
        self.assertEqual(previewed_ids, [str(record_id)])

    async def test_cache_mutations_preserve_active_filters(self):
        kept_id = ui.DB.save_prompt("keep this prompt", output_mode="NoobAI Tags", base_model="NoobAI", score=9)
        ui.DB.save_prompt("hide this prompt", output_mode="Natural Language", base_model="Krea 2", score=2)

        status, table, selected = ui._save_record_as_new(
            "another hidden prompt", "", "Natural Language", "Krea 2", 1, "",
            "keep", 8, "NoobAI Tags", "NoobAI",
        )
        self.assertIn("已保存", status)
        self.assertEqual([row[1] for row in table["value"]], [kept_id])
        self.assertEqual(selected["value"], [])

        status, table, selected = ui._bulk_cache(
            "bulk hidden prompt", "Natural Language", "Krea 2", 1,
            "keep", 8, "NoobAI Tags", "NoobAI",
        )
        self.assertIn("批量导入完成", status)
        self.assertEqual([row[1] for row in table["value"]], [kept_id])
        self.assertEqual(selected["value"], [])

        status, table, selected = ui._delete_previewed_records(
            [str(kept_id)], [str(kept_id)], "keep", 8, "NoobAI Tags", "NoobAI",
        )
        self.assertIn("已删除 1 条", status)
        self.assertEqual(table["value"], [])
        self.assertEqual(selected["value"], [])

    async def test_selected_delete_requires_preview_of_current_selection(self):
        first = ui.DB.save_prompt("first prompt")
        second = ui.DB.save_prompt("second prompt")

        status, table, selected = ui._delete_previewed_records([str(second)], [str(first)])

        self.assertIn("选择已变化", status)
        self.assertEqual(table.get("__type__"), "update")
        self.assertNotIn("value", table)
        self.assertEqual(selected.get("__type__"), "update")
        self.assertNotIn("value", selected)
        self.assertIsNotNone(ui.DB.get_prompt(first))
        self.assertIsNotNone(ui.DB.get_prompt(second))

    async def test_selected_export_reports_only_existing_records(self):
        existing = ui.DB.save_prompt("export me")

        status, path = ui._export_selected([str(existing), "999999"], "JSON")

        self.assertIn("已导出选中的 1 条记录", status)
        self.assertIn("忽略已不存在的 1 条选择", status)
        self.assertTrue(Path(path).is_file())


if __name__ == "__main__":
    unittest.main()
