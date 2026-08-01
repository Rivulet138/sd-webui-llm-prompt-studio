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
        ui.DB.set_setting("llm_connection", {
            "backend": "OpenAI Compatible",
            "endpoint": "http://127.0.0.1:1234/v1",
            "model": "test-model",
            "temperature": 0.35,
            "timeout": 90,
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


if __name__ == "__main__":
    unittest.main()
