import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.modules.setdefault("gradio", types.ModuleType("gradio"))

import prompt_studio_core as core
import prompt_studio_ui as ui


class InlineInferenceSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        database = core.StudioDB(Path(self.temp.name) / "studio.db")
        credentials = core.CredentialStore(Path(self.temp.name) / "credentials.json")
        for name, value in (("DB", database), ("CREDENTIALS", credentials)):
            patch = mock.patch.object(ui, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_inline_generation_uses_the_saved_inference_snapshot_without_temperature_override(self):
        connection = {
            "provider": "Ollama", "endpoint": "http://localhost:11434", "model": "primary",
            "temperature": 0.35, "timeout": 47, "max_tokens": 3210, "send_temperature": True,
            "retry_count": 4, "thinking_enabled": True, "thinking_budget": 2048,
            "reasoning_effort": "high", "top_p": 0.72, "top_k": 33,
            "fallback_provider": "OpenAI Compatible", "fallback_endpoint": "http://localhost:1234/v1",
            "fallback_model": "backup",
        }
        with mock.patch.object(ui, "call_llm", return_value="one detailed garden prompt") as call:
            generated, _, status = ui._generate(
                "garden", "", "Danbooru Tags", "", "Auto / checkpoint default", "SFW", "", "",
                connection["provider"], connection["endpoint"], connection["model"], "",
                connection["temperature"], connection["timeout"], connection["max_tokens"], connection["send_temperature"],
                True, "", False, False, 0, "Plain Prompt", 1, 0, False,
                batch_directive="inline batch diversity",
                connection_settings=connection,
                preserve_inference_settings=True,
            )
        self.assertEqual(status, "生成完成")
        self.assertEqual(generated, "one detailed garden prompt")
        self.assertEqual(call.call_args.args[6], 0.35)
        self.assertEqual(call.call_args.kwargs["max_retries"], 4)
        self.assertTrue(call.call_args.kwargs["thinking_enabled"])
        self.assertEqual(call.call_args.kwargs["thinking_budget"], 2048)
        self.assertEqual(call.call_args.kwargs["reasoning_effort"], "high")
        self.assertEqual(call.call_args.kwargs["top_p"], 0.72)
        self.assertEqual(call.call_args.kwargs["top_k"], 33)
        self.assertEqual(call.call_args.kwargs["fallback_provider"], "OpenAI Compatible")
        self.assertEqual(call.call_args.kwargs["fallback_endpoint"], "http://localhost:1234/v1")
        self.assertEqual(call.call_args.kwargs["fallback_model"], "backup")


if __name__ == "__main__":
    unittest.main()
