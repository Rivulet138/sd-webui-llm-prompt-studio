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
        self.lexicon = root / "lexicon"
        self.lexicon.mkdir()
        self.originals = {
            "db": ui.DB,
            "credentials": ui.CREDENTIALS,
            "wildcards": ui.DEFAULT_WILDCARDS,
            "call_llm": ui.call_llm,
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

    async def test_remote_api_requires_valid_forge_basic_auth(self):
        self._install_shared("tester:secret")
        app = FastAPI()
        ui.on_app_started(None, app)

        missing = await self._request(app, "203.0.113.10", "GET", "/llm-prompt-studio/v1/cache")
        valid = await self._request(app, "203.0.113.10", "GET", "/llm-prompt-studio/v1/cache", auth=("tester", "secret"))

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(valid.status_code, 200)

    async def test_provider_settings_and_keys_are_restored_independently(self):
        message, _ = ui._save_llm_settings(
            "Anthropic", "https://api.anthropic.com/", "claude-test", "anthropic-key", 0.2, 120, 2048, False,
        )
        self.assertIn("Anthropic 设置已保存", message)
        message, _ = ui._save_llm_settings(
            "Google Gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini-test", "gemini-key", 0.6, 80, 4096, True,
        )
        self.assertIn("Google Gemini 设置已保存", message)

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


if __name__ == "__main__":
    unittest.main()
