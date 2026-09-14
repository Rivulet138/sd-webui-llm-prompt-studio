"""Behavioral browser-loop tests using Node's built-in test runner."""
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InlineLoopRuntimeTests(unittest.TestCase):
    def test_button_loop_runtime(self):
        result = subprocess.run(
            ["node", "--test", str(ROOT / "tests" / "inline_loop_runtime.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
