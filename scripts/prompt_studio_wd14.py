"""Offline WD14 / CL Tagger inference and local model discovery."""

import csv
import importlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import threading
from contextlib import nullcontext


def _forge_paths():
    app = Path(__file__).resolve().parents[3]
    paths = sys.modules.get("modules.paths") or sys.modules.get("modules.paths_internal")
    return (Path(getattr(paths, "models_path", app / "models")),
            Path(getattr(paths, "extensions_dir", app / "extensions")),
            Path(getattr(paths, "extensions_builtin_dir", app / "extensions-builtin")))


def _model_roots():
    models, extensions, builtins = _forge_paths()
    default_cache = Path.home() / ".cache" / "huggingface" / "hub"
    hf_home = Path(os.environ.get("HF_HOME", str(Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "huggingface")))
    roots = [hf_home / "hub", default_cache, *[models / name for name in ("WD14", "wd14-tagger", "tagger", "interrogate")]]
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
        if os.environ.get(key):
            roots.insert(0, Path(os.environ[key]))
    # HF constants may have been initialized before another extension changed env.
    constants = sys.modules.get("huggingface_hub.constants")
    if constants is not None:
        for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
            if getattr(constants, name, None):
                roots.append(Path(getattr(constants, name)))
    for parent in (extensions, builtins):
        try:
            roots.extend(path for path in parent.glob("*wd14*") if path.is_dir())
        except OSError:
            continue
    return list(dict.fromkeys(roots))


def _paired_model(path):
    path = Path(path).expanduser().absolute()
    if path.is_dir():
        path = path / "model.onnx"
    if path.suffix.lower() != ".onnx" or not path.is_file():
        raise ValueError("请选择已下载的 ONNX 模型文件或模型目录。")
    labels_path = path.parent / "tag_mapping.json"
    if not labels_path.is_file():
        labels_path = path.parent / "selected_tags.csv"
    if not labels_path.is_file():
        raise ValueError("模型目录缺少 selected_tags.csv 或 CL Tagger 的 tag_mapping.json。")
    if path.stat().st_size == 0 or labels_path.stat().st_size == 0:
        raise ValueError("模型或标签文件为空，请重新下载。")
    return path, labels_path


def discover_local_models(extra_directory=""):
    """List complete local models only; never resolve or download remote weights."""
    roots = list(_model_roots())
    if extra_directory:
        roots.insert(0, Path(str(extra_directory)).expanduser())
    found = {}
    for root in roots:
        try:
            candidates = [root] if root.is_file() else root.rglob("*.onnx")
            for candidate in candidates:
                try:
                    path, labels_path = _paired_model(candidate)
                    key = os.path.normcase(str(path))
                    parts = path.parts
                    repo = next((part.removeprefix("models--").replace("--", "/") for part in parts if part.startswith("models--")), "")
                    label = f"{repo} · {path.parent.name[:8]}" if repo else f"{path.parent.name}/{path.name}"
                    if labels_path.suffix == ".json":
                        label = f"CL Tagger · {path.parent.name} · {path.name}"
                    found[key] = (label, str(path))
                except (ValueError, OSError):
                    continue
        except OSError:
            continue
    choices = sorted(found.values(), key=lambda item: item[0].lower())
    default = choices[0][1] if choices else None
    return choices, default


def _load_cl_tags(path):
    """Keep model output indices intact, including holes in sparse mappings."""
    with path.open("r", encoding="utf-8-sig") as handle:
        mapping = json.load(handle)
    try:
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("需要非空字典")
        if "idx_to_tag" in mapping:
            categories = mapping["tag_to_category"]
            entries = {int(index): (name, categories.get(name, "Unknown"))
                       for index, name in mapping["idx_to_tag"].items()}
        else:
            entries = {int(index): (entry["tag"], entry["category"])
                       for index, entry in mapping.items()}
        if not entries or min(entries) < 0:
            raise ValueError("标签索引必须为非负整数")
        tags = [(None, "unknown")] * (max(entries) + 1)
        for index, (name, category) in entries.items():
            if not isinstance(name, str) or not name or not isinstance(category, str):
                raise ValueError("标签名称或类别无效")
            tags[index] = (name, category.lower())
        return tags
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError(f"tag_mapping.json 格式无效：{exc}") from exc


def _forge_lock():
    try:
        return importlib.import_module("modules.call_queue").queue_lock
    except ImportError:
        return nullcontext()


def _providers(ort):
    force_cpu = os.environ.get("WD14_FORCE_CPU", "").lower() in {"1", "true", "yes"}
    try:
        opts = importlib.import_module("modules.shared").cmd_opts
        use_cpu = getattr(opts, "use_cpu", []) or []
        force_cpu = force_cpu or getattr(opts, "cpu", False) or any(name in use_cpu for name in ("all", "interrogate", "wd14"))
    except ImportError:
        pass
    available = ort.get_available_providers()
    wanted = ["CPUExecutionProvider"] if force_cpu else ["CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
    selected = [provider for provider in wanted if provider in available]
    if not selected:
        raise ValueError("onnxruntime 没有可用的推理设备。")
    return selected


class LocalWD14Tagger:
    """A single lazily loaded session, serialized with Forge GPU work."""

    def __init__(self):
        self._lock = threading.RLock()
        self._session = None
        self._model_key = None
        self._tags = []
        self._family = None

    def _load(self, model_path):
        path, labels_path = _paired_model(model_path)
        key = (str(path), str(labels_path), path.stat().st_mtime_ns, labels_path.stat().st_mtime_ns)
        if self._session is not None and self._model_key == key:
            return self._session
        self._session = None
        self._model_key = None
        self._tags = []
        self._family = None
        family = "cl" if labels_path.suffix == ".json" else "wd14"
        if family == "cl":
            tags = _load_cl_tags(labels_path)
        else:
            with labels_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not {"name", "category"}.issubset(reader.fieldnames or []):
                    raise ValueError("selected_tags.csv 缺少 name 或 category 列。")
                tags = [(row["name"], int(row["category"])) for row in reader]
            if not tags or any(not name for name, _ in tags):
                raise ValueError("selected_tags.csv 标签为空。")
        ort = importlib.import_module("onnxruntime")
        providers = _providers(ort)
        try:
            session = ort.InferenceSession(str(path), providers=providers)
        except Exception as error:
            if providers == ["CPUExecutionProvider"] or "CPUExecutionProvider" not in providers:
                raise
            # Installed CUDA providers can be unavailable at runtime (missing DLLs).
            logging.getLogger(__name__).warning("Tagger providers %s failed; retrying on CPU: %s", providers, error)
            session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        if len(session.get_inputs()) != 1 or len(session.get_outputs()) != 1:
            raise ValueError("请选择 WD14 / CL 单输入、单输出模型。")
        self._session, self._model_key, self._tags = session, key, tags
        self._family = family
        return session

    @staticmethod
    def _preprocess(image, shape, family="wd14"):
        import numpy as np
        from PIL import Image

        if len(shape) != 4 or shape[0] not in (1, None, "batch", "batch_size") and isinstance(shape[0], int):
            raise ValueError("反推模型需要四维、单张图像输入。")
        if family == "cl" and shape[1] == 3:
            nchw, height, width = True, shape[2], shape[3]
        elif shape[-1] == 3:
            nchw, height, width = False, shape[1], shape[2]
        elif shape[1] == 3:
            nchw, height, width = True, shape[2], shape[3]
        else:
            raise ValueError("无法识别反推模型的 RGB 输入维度。")
        if family == "cl":
            # CL exports symbolic spatial axes; 448 matches its trained/default
            # resolution and the installed tagger's preprocessing.
            height = 448 if height is None or isinstance(height, str) else height
            width = 448 if width is None or isinstance(width, str) else width
        if not isinstance(height, int) or not isinstance(width, int) or height != width or height <= 0:
            raise ValueError("反推模型输入必须为固定大小的正方形。")
        if not isinstance(image, Image.Image):
            image = Image.fromarray(np.asarray(image))
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        rgb = background.convert("RGB")
        side = max(rgb.width, rgb.height) if family == "cl" else max(rgb.width, rgb.height, height)
        square = Image.new("RGB", (side, side), "white")
        square.paste(rgb, ((side - rgb.width) // 2, (side - rgb.height) // 2))
        if family == "cl":
            array = np.asarray(square.resize((width, height), Image.Resampling.BICUBIC), dtype=np.float32)[:, :, ::-1]
            array = array / np.float32(127.5) - np.float32(1)
            if nchw:
                array = array.transpose(2, 0, 1)
            return np.ascontiguousarray(array[None, ...])
        array = np.asarray(square)[:, :, ::-1]
        if side != height:
            try:
                import cv2
                array = cv2.resize(array, (width, height), interpolation=cv2.INTER_AREA)
            except ImportError:
                array = np.asarray(square.resize((width, height), Image.Resampling.BOX))[:, :, ::-1]
        array = array.astype(np.float32)
        if nchw:
            array = array.transpose(2, 0, 1)
        return np.ascontiguousarray(array[None, ...])

    def interrogate(self, image, model_path, threshold=0.35, character_threshold=0.85):
        import numpy as np

        if image is None:
            raise ValueError("请先上传图片。")
        for value in (threshold, character_threshold):
            if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
                raise ValueError("反推阈值必须在 0 到 1 之间。")
        with _forge_lock(), self._lock:
            session = self._load(model_path)
            input_meta = session.get_inputs()[0]
            if getattr(input_meta, "type", "tensor(float)") != "tensor(float)":
                raise ValueError("反推模型输入必须为 float32。")
            array = self._preprocess(image, input_meta.shape, self._family)
            scores = np.asarray(session.run([session.get_outputs()[0].name], {input_meta.name: array})[0])
            if scores.shape != (1, len(self._tags)) or not np.isfinite(scores).all():
                raise ValueError("模型输出与标签数量不匹配或包含无效分数。")
            if self._family == "cl":
                scores = 1 / (1 + np.exp(-np.clip(scores.astype(np.float64), -30, 30)))
                quality = [(name, float(score)) for (name, category), score in zip(self._tags, scores[0])
                           if name and category == "quality"]
                selected = [(name, float(score)) for (name, category), score in zip(self._tags, scores[0])
                            if name and category in {"general", "character", "copyright", "artist", "meta", "model"}
                            and score >= float(character_threshold if category == "character" else threshold)]
                if quality:
                    best_quality = max(quality, key=lambda item: item[1])
                    if best_quality[1] >= float(threshold):
                        selected.append(best_quality)
            else:
                selected = [(name, float(score)) for (name, category), score in zip(self._tags, scores[0])
                            if category != 9 and score >= float(character_threshold if category == 4 else threshold)]
            selected.sort(key=lambda item: item[1], reverse=True)
            return ", ".join(re.sub(r"([\\()])", r"\\\1", name.replace("_", " ")) for name, _ in selected)

    def unload(self):
        with _forge_lock(), self._lock:
            self._session = None
            self._model_key = None
            self._tags = []
            self._family = None
        return "反推模型已卸载。"


_TAGGER = LocalWD14Tagger()


def interrogate_image(image, model_path, threshold=0.35, character_threshold=0.85):
    """Return tags or raise, for batch callers that record individual failures."""
    return _TAGGER.interrogate(image, model_path, threshold, character_threshold)


def interrogate_local(image, model_path, threshold=0.35, character_threshold=0.85):
    try:
        tags = interrogate_image(image, model_path, threshold, character_threshold)
        family = "CL Tagger" if _paired_model(model_path)[1].suffix == ".json" else "WD14"
        return tags, f"本地 {family} · {len(tags.split(', ')) if tags else 0} 个标签"
    except ImportError as exc:
        return "", f"内置反推缺少本地运行依赖：{exc}"
    except Exception as exc:
        return "", f"本地反推识别失败：{exc}"


def unload_local_model():
    return _TAGGER.unload()
