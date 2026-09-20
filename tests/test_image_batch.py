import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prompt_studio_image_batch as batch


class ImageBatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def image(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 3), "red").save(path)
        return str(path)

    def job(self, **kwargs):
        return batch.ImageBatchJob(self.root, "local.onnx", **kwargs)

    def test_recursive_and_flat_scan_are_sorted_and_filter_extensions(self):
        expected = [self.image(name) for name in ("z.PNG", "a.jpg", "nested/b.webp")]
        (self.root / "ignore.txt").write_text("not an image")
        self.assertEqual(batch.list_image_files(self.root), sorted(expected, key=lambda path: (path.casefold(), path)))
        self.assertEqual(batch.list_image_files(self.root, False), [expected[1], expected[0]])

    def test_directory_links_and_junctions_are_not_traversed(self):
        self.image("nested/image.png")
        # Exercise the Windows reparse-point filter even without symlink privileges.
        with mock.patch.object(batch, "_linked_directory", return_value=True):
            self.assertEqual(batch.list_image_files(self.root), [])
        with mock.patch.object(batch.os, "lstat", return_value=types.SimpleNamespace(st_mode=0o040755, st_file_attributes=0x400)):
            self.assertTrue(batch._linked_directory(self.root / "nested"))

    def test_symlink_cycle_is_not_traversed(self):
        expected = self.image("image.png")
        try:
            os.symlink(self.root, self.root / "cycle", target_is_directory=True)
        except OSError:
            self.skipTest("symlink permission unavailable")
        self.assertEqual(batch.list_image_files(self.root), [expected])

    def test_corrupt_image_fails_individually_and_success_is_preserved(self):
        (self.root / "a.png").write_bytes(b"invalid")
        self.image("b.png")
        job = self.job()
        infer = mock.Mock(return_value="blue eyes")
        snapshots = list(job.iter_run(infer))
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[-1]["counts"], dict(completed=1, failed=1, pending=0, skipped=0))
        self.assertTrue(job.records[0]["error"])
        infer.assert_called_once()
        self.assertEqual(infer.call_args.args[1:], ("local.onnx", 0.35, 0.85))
        snapshots[-1]["records"][1]["prompt"] = "modified"
        self.assertEqual(job.records[1]["prompt"], "blue eyes")

    def test_cancel_and_resume_only_process_pending(self):
        for name in ("a.png", "b.png", "c.png"):
            self.image(name)
        job = self.job()

        def cancel_after_first(*args):
            job.cancel.set()
            return "first"

        first = list(job.iter_run(cancel_after_first))
        self.assertEqual(first[-1]["counts"]["pending"], 2)
        self.assertTrue(first[-1]["cancelled"])
        job.cancel.clear()
        infer = mock.Mock(return_value="next")
        list(job.iter_run(infer))
        self.assertEqual(infer.call_count, 2)
        self.assertEqual([record["prompt"] for record in job.records], ["first", "next", "next"])

    def test_failed_requires_explicit_retry_completed_never_repeats(self):
        self.image("a.png")
        self.image("b.png")
        job = self.job()
        list(job.iter_run(mock.Mock(side_effect=[RuntimeError("device"), "success"])))
        infer = mock.Mock(return_value="retry")
        list(job.iter_run(infer))
        infer.assert_not_called()
        list(job.iter_run(infer, retry_failed=True))
        infer.assert_called_once()
        self.assertEqual([record["prompt"] for record in job.records], ["retry", "success"])

    def test_empty_tags_are_skipped_and_not_repeated(self):
        self.image("empty.png")
        job = self.job()
        list(job.iter_run(lambda *args: "  "))
        self.assertEqual(job.snapshot()["counts"]["skipped"], 1)
        infer = mock.Mock()
        list(job.iter_run(infer, retry_failed=True))
        infer.assert_not_called()

    def test_exif_rotation_applied_before_inference(self):
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (4, 3)).save(self.root / "rotated.jpg", exif=exif)
        sizes = []
        list(self.job().iter_run(lambda image, *args: sizes.append(image.size) or "tags"))
        self.assertEqual(sizes, [(3, 4)])

    def test_empty_cancelled_and_duplicate_runs(self):
        self.assertEqual(list(self.job().iter_run())[0]["total"], 0)
        self.image("a.png")
        job = self.job()
        job.cancel.set()
        infer = mock.Mock()
        self.assertEqual(list(job.iter_run(infer))[0]["counts"]["pending"], 1)
        infer.assert_not_called()
        job.cancel.clear()
        iterator = job.iter_run(lambda *args: "tags")
        next(iterator)
        with self.assertRaises(RuntimeError):
            list(job.iter_run())
        iterator.close()
        list(job.iter_run())

    def test_invalid_folder_and_threshold(self):
        with self.assertRaises(ValueError):
            batch.list_image_files("")
        with self.assertRaises(FileNotFoundError):
            batch.list_image_files(self.root / "missing")
        with self.assertRaises(ValueError):
            self.job(threshold=float("nan"))


if __name__ == "__main__":
    unittest.main()
