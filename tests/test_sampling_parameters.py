import unittest
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

from scripts import prompt_studio_core as core


class SamplingParameterTests(unittest.TestCase):
    def payload(self, provider, **kwargs):
        return core.build_provider_request(
            provider, core.get_provider_profile(provider)["default_endpoint"],
            "test-model", "test-key", "system", "user", **kwargs,
        )[1]

    def test_optional_sampling_defaults_do_not_change_requests(self):
        for provider in core.PROVIDER_PROFILES:
            payload = self.payload(provider)
            options = payload.get("options", payload.get("generationConfig", payload))
            self.assertNotIn("top_p", options)
            self.assertNotIn("top_k", options)
            self.assertNotIn("topP", options)

    def test_sampling_parameters_use_protocol_field_names(self):
        for provider, container, p_key, k_key in (
            ("Ollama", "options", "top_p", "top_k"),
            ("Google Gemini", "generationConfig", "topP", "topK"),
            ("Anthropic", None, "top_p", "top_k"),
            ("OpenRouter", None, "top_p", "top_k"),
        ):
            with self.subTest(provider=provider):
                payload = self.payload(provider, top_p=0.8, top_k=40, send_temperature=False)
                options = payload[container] if container else payload
                self.assertEqual(options[p_key], 0.8)
                self.assertEqual(options[k_key], 40)

    def test_openai_and_deepseek_do_not_receive_top_k(self):
        for provider in ("OpenAI", "OpenAI Chat Completions", "DeepSeek"):
            payload = self.payload(provider, top_p=0.9, top_k=40)
            self.assertEqual(payload["top_p"], 0.9)
            self.assertNotIn("top_k", payload)

    def test_invalid_sampling_values_are_rejected(self):
        for kwargs in ({"top_p": -0.1}, {"top_p": 1.1}, {"top_p": float("nan")},
                       {"top_k": -1}, {"top_k": 1.5}, {"top_k": float("inf")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.payload("Ollama", **kwargs)

    def test_call_passes_sampling_to_request(self):
        with mock.patch.object(core, "_request_json", return_value={"message": {"content": "ok"}}) as request:
            self.assertEqual(core.call_llm("Ollama", "http://localhost:11434", "test", "",
                                          "system", "user", top_p=0.75, top_k=25), "ok")
        self.assertEqual(request.call_args.args[1]["options"]["top_k"], 25)


class SamplingSettingsTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        sys.modules.setdefault("gradio", types.ModuleType("gradio"))
        import prompt_studio_ui as ui
        self.ui = ui
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name, value in (
            ("DB", core.StudioDB(Path(directory.name) / "studio.db")),
            ("CREDENTIALS", core.CredentialStore(Path(directory.name) / "keys.json")),
        ):
            patch = mock.patch.object(ui, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(ui.gr, "update", lambda **kwargs: kwargs, create=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_sampling_survives_save_reload_and_can_be_cleared(self):
        args = ["Ollama", "http://localhost:11434", "test-model", "", "", "", "", 0.5, 90, 1000, True, 2]
        message = self.ui._save_llm_settings(*args, top_p=0.8, top_k=40)[0]
        self.assertIn("设置已保存", message)
        saved = self.ui._connection_settings("Ollama")
        self.assertEqual((saved["top_p"], saved["top_k"]), (0.8, 40))
        self.assertEqual(self.ui._load_provider_settings("Ollama")[-2:], (0.8, {"value": 40, "visible": True}))
        self.ui._save_llm_settings(*args)
        self.assertIsNone(self.ui._connection_settings("Ollama")["top_p"])
        self.assertIsNone(self.ui._connection_settings("Ollama")["top_k"])

    def test_connection_test_uses_unsaved_sampling_values(self):
        with mock.patch.object(self.ui, "call_llm", return_value="READY") as call:
            self.ui._test_connection("Ollama", "http://localhost:11434", "test", "", "", 0.4, 30, 1000,
                                     True, 0, top_p=0.7, top_k=12)
        self.assertEqual(call.call_args.kwargs["top_p"], 0.7)
        self.assertEqual(call.call_args.kwargs["top_k"], 12)


if __name__ == "__main__":
    unittest.main()
