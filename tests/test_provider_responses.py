import unittest
from pathlib import Path
from unittest import mock

from scripts import prompt_studio_core as core
from scripts.prompt_studio_core import LLMRequestError, extract_provider_text

ROOT = Path(__file__).resolve().parents[1]


class ProviderResponseTests(unittest.TestCase):
    def test_localized_choices_are_normalized_to_api_values(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn("def _canonical_preset", source)
        self.assertIn("PRESET_VALUE_ALIASES", source)
        self.assertIn("BASE_MODEL_VALUE_ALIASES", source)
        self.assertIn("return PRESET_VALUE_ALIASES.get(text, text)", source)

    def test_responses_api_variants_return_assistant_text(self):
        cases = [
            ({"output": [{"type": "message", "content": [{"type": "output_text", "text": "alpha"}]}]}, "alpha"),
            ({"output": [{"type": "message", "content": [{"type": "text", "text": "beta"}]}]}, "beta"),
            ({"output": [{"type": "output_text", "text": "gamma"}]}, "gamma"),
            ({"output_text": "delta"}, "delta"),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                self.assertEqual(extract_provider_text("OpenAI", payload), expected)

    def test_batch_count_normalizes_non_finite_gradio_values(self):
        source = (ROOT / "scripts" / "prompt_studio_ui.py").read_text(encoding="utf-8")
        self.assertIn("requested_count = int(float(generation_count))", source)
        self.assertIn("except (TypeError, ValueError, OverflowError):", source)
        self.assertIn("count = max(1, min(200, requested_count))", source)

    def test_https_requests_never_downgrade_to_cleartext(self):
        requested_urls = []

        def fail(url, *args, **kwargs):
            requested_urls.append(url)
            raise LLMRequestError("temporary failure", retryable=True)

        with mock.patch.object(core, "_request_json", side_effect=fail), mock.patch.object(core.time, "sleep"):
            with self.assertRaises(LLMRequestError):
                core.call_llm(
                    "OpenAI", "https://api.openai.com/v1", "test-model", "secret",
                    "system", "user", max_retries=1,
                )
        self.assertEqual(len(requested_urls), 2)
        self.assertTrue(all(url.startswith("https://") for url in requested_urls))


if __name__ == "__main__":
    unittest.main()
