import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prompt_studio_core import (  # noqa: E402
    PROMPT_POLICY_V3,
    build_user_message,
    extract_deterministic_constraints,
)


class DeterministicConstraintTests(unittest.TestCase):
    def test_extracts_explicit_constraints_verbatim_and_deduplicates(self):
        request = (
            "Exactly 2 cats beside the kettle, no extra people. "
            "Keep the red background. Exactly 2 cats beside the kettle."
        )
        self.assertEqual(
            extract_deterministic_constraints(request),
            ["Exactly 2 cats beside the kettle", "no extra people", "Keep the red background"],
        )

    def test_does_not_invent_constraints_for_creative_prose(self):
        self.assertEqual(extract_deterministic_constraints("A quiet blue room with warm light."), [])

    def test_user_message_keeps_full_request_and_structured_constraints(self):
        request = "Include one bicycle beside the wall; do not add text."
        message = build_user_message(request)
        payload = json.loads(message.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertEqual(payload["request"], request)
        self.assertIn("Include one bicycle beside the wall", payload["deterministic_constraints"])
        self.assertIn("do not add text", payload["deterministic_constraints"])
        self.assertEqual(message.count("</user_image_request>"), 1)

    def test_constraint_block_is_required_by_policy(self):
        self.assertIn('deterministic_constraints', PROMPT_POLICY_V3)
        self.assertIn("hard constraint", PROMPT_POLICY_V3)

    def test_decimal_weight_is_not_cut_at_decimal_point(self):
        clauses = extract_deterministic_constraints("Keep (red_background:1.2), no extra people.")
        self.assertIn("Keep (red_background:1.2)", clauses)

    def test_long_constraint_is_omitted_not_truncated(self):
        request = "Keep " + "a" * 501 + "."
        self.assertEqual(extract_deterministic_constraints(request), [])
        self.assertEqual(extract_deterministic_constraints("Keep red", limit=0), [])

    def test_oversized_request_fails_instead_of_losing_tail(self):
        with self.assertRaisesRegex(ValueError, "16000"):
            build_user_message("x" * 16000 + " no extra people")


if __name__ == "__main__":
    unittest.main()
