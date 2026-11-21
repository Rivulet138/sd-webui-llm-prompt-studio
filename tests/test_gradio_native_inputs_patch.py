"""Exercise the host patch only on temporary asset files."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("native_inputs_patch", Path(__file__).resolve().parents[1] / "tools/patch_gradio_native_inputs.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class NativeInputPatchTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.asset = Path(folder.name) / "Blocks-B7cNA3Pj.js"
        self.original = ("const inputs=" + patch.ORIGINAL + "; // fixture").encode()
        self.asset.write_bytes(self.original)

    def test_apply_repeat_and_restore_preserve_original_bytes(self):
        self.assertEqual(patch.patch_asset(self.asset), "patched")
        self.assertIn(patch.PATCHED.encode(), self.asset.read_bytes())
        self.assertEqual(patch.patch_asset(self.asset), "already patched")
        self.assertEqual(patch.patch_asset(self.asset, restore=True), "restored")
        self.assertEqual(self.asset.read_bytes(), self.original)
        self.assertFalse(self.asset.with_name(self.asset.name + patch.BACKUP_SUFFIX).exists())

    def test_unsupported_assets_are_unchanged(self):
        for data in [b"unsupported", self.original + self.original]:
            self.asset.write_bytes(data)
            with self.assertRaises(ValueError):
                patch.patch_asset(self.asset)
            self.assertEqual(self.asset.read_bytes(), data)

    def test_restore_retains_subsequent_host_changes(self):
        patch.patch_asset(self.asset)
        changed = self.asset.read_bytes() + b"// another patch"
        self.asset.write_bytes(changed)
        with self.assertRaises(ValueError):
            patch.patch_asset(self.asset, restore=True)
        self.assertEqual(self.asset.read_bytes(), changed)

    def test_existing_different_backup_is_not_overwritten(self):
        backup = self.asset.with_name(self.asset.name + patch.BACKUP_SUFFIX)
        backup.write_bytes(b"old environment")
        with self.assertRaises(ValueError):
            patch.patch_asset(self.asset)
        self.assertEqual(self.asset.read_bytes(), self.original)
        self.assertEqual(backup.read_bytes(), b"old environment")
