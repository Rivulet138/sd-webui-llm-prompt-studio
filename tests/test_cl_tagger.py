import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prompt_studio_wd14 as wd14


class CLTaggerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model = self.root / "model.onnx"
        self.model.write_bytes(b"model")
        self.mapping = self.root / "tag_mapping.json"
        self.mapping.write_text(json.dumps({
            "0": {"tag": "safe", "category": "Rating"},
            "1": {"tag": "blue_eyes", "category": "General"},
            "3": {"tag": "character_(name)", "category": "Character"},
            "4": {"tag": "best_quality", "category": "Quality"},
            "5": {"tag": "normal_quality", "category": "Quality"},
            "6": {"tag": "artist_name", "category": "Artist"},
        }), encoding="utf-8")
        self.session = mock.Mock()
        self.session.get_inputs.return_value = [types.SimpleNamespace(name="image", shape=[1, 3, 6, 6], type="tensor(float)")]
        self.session.get_outputs.return_value = [types.SimpleNamespace(name="logits")]
        self.session.run.return_value = [np.array([[1000, 0, 1000, 1, 3, 2, -1000]], dtype=np.float32)]
        self.ort = types.SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"], InferenceSession=mock.Mock(return_value=self.session))
        patcher = mock.patch.dict(sys.modules, {"onnxruntime": self.ort})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tagger = wd14.LocalWD14Tagger()

    def test_mapping_formats_keep_sparse_indices(self):
        tags = wd14._load_cl_tags(self.mapping)
        self.assertEqual(tags[2], (None, "unknown"))
        self.assertEqual(tags[3], ("character_(name)", "character"))
        self.mapping.write_text(json.dumps({"idx_to_tag": {"0": "safe", "3": "hero"},
                                            "tag_to_category": {"safe": "Rating", "hero": "Character"}}), encoding="utf-8")
        self.assertEqual(wd14._load_cl_tags(self.mapping), [("safe", "rating"), (None, "unknown"), (None, "unknown"), ("hero", "character")])

    def test_invalid_mapping_fails_before_loading_model(self):
        for mapping in ({}, [], {"-1": {"tag": "bad", "category": "General"}}, {"0": {"tag": "bad"}}, {"idx_to_tag": {"0": "bad"}}):
            self.mapping.write_text(json.dumps(mapping), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "tag_mapping.json"):
                self.tagger.interrogate(Image.new("RGB", (2, 2)), self.model)
        self.ort.InferenceSession.assert_not_called()

    def test_normalized_bgr_bicubic_actual_shape_and_white_alpha(self):
        transparent = Image.new("RGBA", (2, 4), (20, 30, 40, 0))
        array = self.tagger._preprocess(transparent, [1, 3, 6, 6], "cl")
        self.assertEqual(array.shape, (1, 3, 6, 6))
        self.assertEqual(array.dtype, np.float32)
        self.assertTrue(array.flags.c_contiguous)
        np.testing.assert_array_equal(array, np.ones_like(array))
        image = Image.new("RGB", (2, 4), (255, 64, 0))
        expected = Image.new("RGB", (4, 4), "white")
        expected.paste(image, (1, 0))
        expected = np.asarray(expected.resize((6, 6), Image.Resampling.BICUBIC), dtype=np.float32)[:, :, ::-1] / 127.5 - 1
        actual = self.tagger._preprocess(image, [1, 3, 6, 6], "cl")
        np.testing.assert_allclose(actual[0], expected.transpose(2, 0, 1))
        nhwc = self.tagger._preprocess(image, [1, 6, 6, 3], "cl")
        np.testing.assert_allclose(nhwc[0], expected)

    def test_logits_categories_quality_threshold_and_sparse_holes(self):
        image = Image.new("RGB", (2, 2))
        with np.errstate(over="raise"):
            self.assertEqual(self.tagger.interrogate(image, self.model), "best quality, blue eyes")
            self.assertEqual(self.tagger.interrogate(image, self.model, character_threshold=.7), r"best quality, character \(name\), blue eyes")
        self.assertEqual(self.session.run.call_args.args[1]["image"].shape, (1, 3, 6, 6))
        self.assertEqual(self.tagger.interrogate(image, self.model, threshold=.99), "")

    def test_dynamic_cl_spatial_axes_use_trained_448_resolution(self):
        image = Image.new("RGB", (2, 4), "white")
        for shape in (["batch", 3, "height", "width"], [None, 3, None, None]):
            array = self.tagger._preprocess(image, shape, "cl")
            self.assertEqual(array.shape, (1, 3, 448, 448))
            np.testing.assert_array_equal(array, 1)
        self.session.get_inputs.return_value[0].shape = ["batch", 3, "height", "width"]
        self.assertEqual(self.tagger.interrogate(image, self.model), "best quality, blue eyes")
        self.assertEqual(self.session.run.call_args.args[1]["image"].shape, (1, 3, 448, 448))
        for shape in ([1, 3, 0, 0], [1, 3, -1, -1], [1, 3, 224, 448]):
            with self.assertRaises(ValueError):
                self.tagger._preprocess(image, shape, "cl")
        with self.assertRaises(ValueError):
            self.tagger._preprocess(image, ["batch", 3, "height", "width"], "wd14")

    def test_discovery_recognizes_complete_cl_and_excludes_incomplete(self):
        incomplete = self.root / "incomplete"
        incomplete.mkdir()
        (incomplete / "model.onnx").write_bytes(b"x")
        with mock.patch.object(wd14, "_model_roots", return_value=[self.root]):
            choices, default = wd14.discover_local_models()
        self.assertEqual(len(choices), 1)
        self.assertIn("CL Tagger", choices[0][0])
        self.assertEqual(default, str(self.model))
        self.ort.InferenceSession.assert_not_called()

    def test_model_choices_distinguish_cl_versions_and_optimized_weights(self):
        for version in ("cl_tagger_1_01", "cl_tagger_1_02"):
            folder = self.root / "models--cella110n--cl_tagger" / "snapshots" / "revision" / version
            folder.mkdir(parents=True)
            (folder / "tag_mapping.json").write_bytes(self.mapping.read_bytes())
            for filename in ("model.onnx", "model_optimized.onnx"):
                (folder / filename).write_bytes(b"weights")
        with mock.patch.object(wd14, "_model_roots", return_value=[self.root / "models--cella110n--cl_tagger"]):
            choices, _ = wd14.discover_local_models()
        self.assertEqual(len({label for label, _ in choices}), 4)
        self.assertTrue(all("cl_tagger_1_0" in label and ".onnx" in label for label, _ in choices))

    def test_switch_family_and_session_reuse_does_not_reuse_preprocessing(self):
        image = Image.new("RGB", (6, 6), (255, 255, 255))
        self.tagger.interrogate(image, self.model)
        self.tagger.interrogate(image, self.model)
        self.assertEqual(self.ort.InferenceSession.call_count, 1)
        np.testing.assert_array_equal(self.session.run.call_args.args[1]["image"], 1)
        self.mapping.unlink()
        self.model.with_name("selected_tags.csv").write_text("name,category\nblue_eyes,0\n", encoding="utf-8")
        self.session.run.return_value = [np.array([[.7]], dtype=np.float32)]
        self.assertEqual(self.tagger.interrogate(image, self.model), "blue eyes")
        self.assertEqual(self.ort.InferenceSession.call_count, 2)
        self.assertEqual(self.tagger._family, "wd14")
        np.testing.assert_array_equal(self.session.run.call_args.args[1]["image"], 255)
        self.tagger.unload()
        self.assertIsNone(self.tagger._family)

    def test_invalid_output_is_rejected(self):
        for output in (np.zeros((1, 6)), np.full((1, 7), np.nan), np.full((1, 7), np.inf)):
            self.session.run.return_value = [output]
            with self.assertRaisesRegex(ValueError, "标签数量"):
                self.tagger.interrogate(Image.new("RGB", (2, 2)), self.model)


if __name__ == "__main__":
    unittest.main()
