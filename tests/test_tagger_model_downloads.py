import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prompt_studio_tagger_models as models
import prompt_studio_wd14 as engine


class ModelDownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        paths = (self.root / "models", self.root / "extensions", self.root / "builtins")
        for patch in (mock.patch.object(models, "_forge_paths", return_value=paths),
                      mock.patch.object(engine, "_model_roots", return_value=[self.root])):
            patch.start()
            self.addCleanup(patch.stop)

    def write_pair(self, directory, family="cl"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "model.onnx").write_bytes(b"weights")
        if family == "cl":
            (directory / "tag_mapping.json").write_text(json.dumps({"0": {"tag": "blue_eyes", "category": "General"}}))
        else:
            (directory / "selected_tags.csv").write_text("name,category\nblue_eyes,0\n")
        return str(directory / "model.onnx")

    def test_selected_cl_reuses_hf_cache_with_no_download(self):
        expected = self.write_pair(self.root / "models--cella110n--cl_tagger" / "snapshots" / "revision" / "cl_tagger_1_01")
        with mock.patch.dict(sys.modules, {"huggingface_hub": None}):
            result = list(models.download_model("cl_tagger_1_01"))[-1]
        self.assertEqual(result[1], expected)

    def test_selected_model_downloads_even_when_another_family_exists(self):
        self.write_pair(self.root / "wd-vit-tagger-v3", "wd14")
        def fetch(**kwargs):
            path = Path(kwargs["local_dir"]) / kwargs["filename"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"weights" if path.suffix == ".onnx" else b'{"0":{"tag":"blue_eyes","category":"General"}}')
            return str(path)
        download = mock.Mock(side_effect=fetch)
        with mock.patch.dict(sys.modules, {"huggingface_hub": types.SimpleNamespace(hf_hub_download=download)}):
            result = list(models.download_model("cl_tagger_1_01"))[-1]
        self.assertEqual(result[1], str(models.model_directory("cl_tagger_1_01") / "model.onnx"))
        calls = [call.kwargs for call in download.call_args_list]
        self.assertEqual([call["filename"] for call in calls], list(models.MODEL_CATALOG["cl_tagger_1_01"]["files"]))
        self.assertEqual({call["revision"] for call in calls}, {models.MODEL_CATALOG["cl_tagger_1_01"]["revision"]})
        self.assertTrue(all(call["token"] is False for call in calls))

    def test_other_cl_version_is_not_treated_as_selected_version(self):
        self.write_pair(self.root / "models--cella110n--cl_tagger" / "snapshots" / "revision" / "cl_tagger_1_02")
        self.assertIsNone(models.find_local_model("cl_tagger_1_01"))

    def test_failed_download_can_retry_without_listing_incomplete_pair(self):
        folder = models.model_directory("wd-vit-tagger-v3")
        folder.mkdir(parents=True)
        (folder / "model.onnx").write_bytes(b"weights")
        fetch = mock.Mock(side_effect=RuntimeError("offline"))
        with mock.patch.dict(sys.modules, {"huggingface_hub": types.SimpleNamespace(hf_hub_download=fetch)}):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                list(models.download_model("wd-vit-tagger-v3"))
        self.assertFalse(models._DOWNLOAD_LOCK.locked())
        self.assertIsNone(models.find_local_model("wd-vit-tagger-v3"))

    def test_invalid_choice_does_not_touch_network(self):
        with mock.patch.dict(sys.modules, {"huggingface_hub": None}):
            with self.assertRaisesRegex(ValueError, "选择"):
                list(models.download_model(None))


if __name__ == "__main__":
    unittest.main()
