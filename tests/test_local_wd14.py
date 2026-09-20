import csv
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prompt_studio_wd14 as wd14


class LocalWD14Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.model("first")
        self.lock = threading.Lock()
        self.sessions = []
        self.ort = types.SimpleNamespace(get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"], InferenceSession=mock.Mock(side_effect=self.session))
        patcher = mock.patch.dict(sys.modules, {"onnxruntime": self.ort, "modules.call_queue": types.SimpleNamespace(queue_lock=self.lock), "modules.shared": types.SimpleNamespace(cmd_opts=types.SimpleNamespace(use_cpu=["interrogate"]))})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tagger = wd14.LocalWD14Tagger()

    def model(self, name):
        folder = self.root / name
        folder.mkdir(parents=True)
        (folder / "model.onnx").write_bytes(b"fake weights")
        with (folder / "selected_tags.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerows([["name", "category"], ["general", 9], ["blue_eyes", 0], ["char_(series)", 4], ["low_tag", 0]])
        return folder / "model.onnx"

    def session(self, path, providers):
        self.assertTrue(self.lock.locked())
        self.assertEqual(providers, ["CPUExecutionProvider"])
        session = mock.Mock()
        session.get_inputs.return_value = [types.SimpleNamespace(name="image", shape=[1, 4, 4, 3], type="tensor(float)")]
        session.get_outputs.return_value = [types.SimpleNamespace(name="tags")]
        def run(outputs, inputs):
            self.assertTrue(self.lock.locked())
            self.last_input = inputs["image"]
            return [np.asarray([[0.99, 0.7, 0.8, 0.1]], dtype=np.float32)]
        session.run.side_effect = run
        self.sessions.append(session)
        return session

    def test_categories_threshold_sort_escape_and_white_alpha(self):
        image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
        tags = self.tagger.interrogate(image, self.path)
        self.assertEqual(tags, "blue eyes")
        self.assertTrue(np.all(self.last_input == 255))
        self.assertEqual(self.last_input.dtype, np.float32)
        tags = self.tagger.interrogate(image, self.path, character_threshold=0.7)
        self.assertEqual(tags, r"char \(series\), blue eyes")
        self.assertFalse(self.lock.locked())

    def test_preprocessing_bgr_and_center_padding(self):
        image = Image.new("RGB", (2, 2), (10, 20, 30))
        array = self.tagger._preprocess(image, [1, 4, 4, 3])
        np.testing.assert_array_equal(array[0, 1, 1], [30, 20, 10])
        np.testing.assert_array_equal(array[0, 0, 0], [255, 255, 255])
        nchw = self.tagger._preprocess(image, [1, 3, 4, 4])
        np.testing.assert_array_equal(nchw, array.transpose(0, 3, 1, 2))
        self.assertTrue(nchw.flags.c_contiguous)

    def test_downscale_numpy_and_grayscale(self):
        image = np.full((8, 8, 3), [10, 20, 30], dtype=np.uint8)
        array = self.tagger._preprocess(image, [1, 4, 4, 3])
        np.testing.assert_array_equal(array[0, 0, 0], [30, 20, 10])
        self.assertEqual(self.tagger._preprocess(Image.new("L", (4, 4), 80), [1, 4, 4, 3]).shape, (1, 4, 4, 3))

    def test_single_session_reuse_switch_and_unload(self):
        image = Image.new("RGB", (4, 4))
        self.tagger.interrogate(image, self.path)
        first_session = self.tagger._session
        self.tagger.interrogate(image, self.path)
        self.assertEqual(len(self.sessions), 1)
        self.tagger.interrogate(image, self.model("second"))
        self.assertEqual(len(self.sessions), 2)
        self.assertIsNot(self.tagger._session, first_session)
        self.assertIn("卸载", self.tagger.unload())
        self.assertIsNone(self.tagger._session)
        self.assertEqual(self.tagger._tags, [])
        self.assertFalse(self.lock.locked())

    def test_incomplete_models_and_snapshots(self):
        self.model("models--SmilingWolf--wd-vit-v3/snapshots/revision")
        incomplete = self.root / "incomplete"
        incomplete.mkdir()
        (incomplete / "model.onnx").write_bytes(b"x")
        with mock.patch.object(wd14, "_model_roots", return_value=[self.root]):
            choices, default = wd14.discover_local_models()
            self.assertEqual(len(choices), 2)
            self.assertEqual(default, choices[0][1])
            self.assertTrue(all(Path(path).is_absolute() for _, path in choices))
        with mock.patch.object(wd14, "_model_roots", return_value=[]):
            self.assertEqual(wd14.discover_local_models(str(self.path))[1], str(self.path))
            self.assertEqual(wd14.discover_local_models(str(incomplete)), ([], None))
        self.ort.InferenceSession.assert_not_called()

    def test_validation_and_errors_release_lock(self):
        image = Image.new("RGB", (4, 4))
        for threshold in (-1, 2, float("nan")):
            with self.assertRaisesRegex(ValueError, "阈值"):
                self.tagger.interrogate(image, self.path, threshold)
        with self.assertRaisesRegex(ValueError, "上传图片"):
            self.tagger.interrogate(None, self.path)
        self.ort.InferenceSession.assert_not_called()
        self.tagger.interrogate(image, self.path)
        self.sessions[0].run.side_effect = lambda *args: [np.zeros((1, 3))]
        with self.assertRaisesRegex(ValueError, "标签数量"):
            self.tagger.interrogate(image, self.path)
        self.assertFalse(self.lock.locked())
        for shape in ([1, 4, 4], [1, 4, 4, 1], [1, 4, 8, 3]):
            with self.assertRaises(ValueError):
                self.tagger._preprocess(image, shape)

    def test_bad_csv_rejects_before_session_load(self):
        self.path.with_name("selected_tags.csv").write_text("bad,columns\nx,y\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "category"):
            self.tagger.interrogate(Image.new("RGB", (4, 4)), self.path)
        self.ort.InferenceSession.assert_not_called()

    def test_failed_gpu_load_retries_cpu(self):
        def load(path, providers):
            if providers != ["CPUExecutionProvider"]:
                raise RuntimeError("CUDA DLL missing")
            return self.session(path, providers)
        self.ort.InferenceSession.side_effect = load
        with mock.patch.object(wd14, "_providers", return_value=["CUDAExecutionProvider", "CPUExecutionProvider"]), \
             self.assertLogs(wd14.__name__, level="WARNING") as messages:
            self.assertEqual(self.tagger.interrogate(Image.new("RGB", (4, 4)), self.path), "blue eyes")
        self.assertEqual(self.ort.InferenceSession.call_count, 2)
        self.assertIn("CUDA DLL missing", messages.output[0])
        self.assertIn("retrying on CPU", messages.output[0])

    def test_discovery_uses_sorted_first_without_model_recommendation(self):
        self.model("models--SmilingWolf--wd-vit-v3/snapshots/old")
        self.model("models--SmilingWolf--wd-vit-tagger-v3/snapshots/current")
        with mock.patch.object(wd14, "_model_roots", return_value=[self.root]):
            choices, default = wd14.discover_local_models()
            self.assertEqual(default, choices[0][1])
            self.assertEqual(default, str(self.path))

    def test_public_wrappers_report_errors(self):
        with mock.patch.object(wd14, "_TAGGER", self.tagger):
            with self.assertRaisesRegex(ValueError, "上传图片"):
                wd14.interrogate_image(None, self.path)
            tags, status = wd14.interrogate_local(None, self.path)
            self.assertEqual(tags, "")
            self.assertIn("上传图片", status)
            self.assertIn("卸载", wd14.unload_local_model())
            tags, status = wd14.interrogate_local(Image.new("RGB", (4, 4)), self.path)
            self.assertEqual(tags, "blue eyes")
            self.assertIn("1 个标签", status)

    def test_auto_detection_uses_forge_paths_plugin_cache_and_hf_locations(self):
        forge_models = self.root / "custom-models"
        extensions = self.root / "extensions"
        builtins = self.root / "builtins"
        plugin_model = self.model("extensions/renamed-wd14-tagger/models/vit")
        hf_model = self.model("hf-home/hub/models--SmilingWolf--wd-vit-tagger-v3/snapshots/revision")
        environment_model = self.model("hf-cache/convnext")
        models_model = self.model("custom-models/WD14/eva")
        cached_model = self.model("loaded-hf-cache/large")
        modules = {"modules.paths": types.SimpleNamespace(models_path=forge_models, extensions_dir=extensions, extensions_builtin_dir=builtins),
                   "huggingface_hub.constants": types.SimpleNamespace(HF_HUB_CACHE=cached_model.parent)}
        with mock.patch.dict(sys.modules, modules), \
             mock.patch.dict(os.environ, {"HF_HOME": str(self.root / "hf-home"), "HF_HUB_CACHE": str(environment_model.parent)}, clear=True), \
             mock.patch.object(wd14.Path, "home", return_value=self.root / "home"):
            choices, default = wd14.discover_local_models()
            self.assertEqual({path for _, path in choices}, {str(p) for p in (plugin_model, hf_model, environment_model, models_model, cached_model)})
            self.assertEqual(default, choices[0][1])

    def test_empty_model_files_are_not_detected(self):
        self.path.write_bytes(b"")
        with mock.patch.object(wd14, "_model_roots", return_value=[self.root]):
            self.assertEqual(wd14.discover_local_models(), ([], None))

if __name__ == "__main__":
    unittest.main()
