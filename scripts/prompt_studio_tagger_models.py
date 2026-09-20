"""Selectable WD14 / CL model downloads; discovery itself stays offline."""

import os
from pathlib import Path, PurePosixPath
import threading

from prompt_studio_wd14 import _forge_paths, _paired_model, discover_local_models


MODEL_CATALOG = {
    "cl_tagger_1_01": {
        "label": "CL Tagger 1.01", "repo_id": "cella110n/cl_tagger",
        "revision": "0b6e9b4e145b1423bfd1715119074a24a301b471",
        "files": ("cl_tagger_1_01/model.onnx", "cl_tagger_1_01/tag_mapping.json"),
    },
    "wd-convnext-tagger-v3": {
        "label": "WD ConvNeXt v3", "repo_id": "SmilingWolf/wd-convnext-tagger-v3",
        "revision": "d39e46de298d27340111b64965e20b8185c407e6",
        "files": ("model.onnx", "selected_tags.csv"),
    },
    "wd-eva02-large-tagger-v3": {
        "label": "WD EVA02 Large v3", "repo_id": "SmilingWolf/wd-eva02-large-tagger-v3",
        "revision": "b25b82a03f7282e41aa2f257a52c7583b710bd1c",
        "files": ("model.onnx", "selected_tags.csv"),
    },
    "wd-vit-large-tagger-v3": {
        "label": "WD ViT Large v3", "repo_id": "SmilingWolf/wd-vit-large-tagger-v3",
        "revision": "ae469aa2e4706a3af08d3673cf73a11d1add314c",
        "files": ("model.onnx", "selected_tags.csv"),
    },
    "wd-vit-tagger-v3": {
        "label": "WD ViT v3", "repo_id": "SmilingWolf/wd-vit-tagger-v3",
        "revision": "7f6b584d0bd3f55c4531f14ba3d4761b2bccdc0f",
        "files": ("model.onnx", "selected_tags.csv"),
    },
    "wd-swinv2-tagger-v3": {
        "label": "WD SwinV2 v3", "repo_id": "SmilingWolf/wd-swinv2-tagger-v3",
        "revision": "627aef95638667ddcaa3ac8ae625e88ea5b02f51",
        "files": ("model.onnx", "selected_tags.csv"),
    },
    "wd-v1-4-convnextv2-tagger-v2": {
        "label": "WD ConvNeXtV2 v2", "repo_id": "SmilingWolf/wd-v1-4-convnextv2-tagger-v2",
        "revision": "bf364499ea843a403cc2770072f31f5cfb2ffa58",
        "files": ("model.onnx", "selected_tags.csv"),
    },
}
_DOWNLOAD_LOCK = threading.Lock()


def _model_spec(model_id):
    if model_id not in MODEL_CATALOG:
        raise ValueError("请先选择要下载的模型。")
    return MODEL_CATALOG[model_id]


def model_directory(model_id):
    spec = _model_spec(model_id)
    return _forge_paths()[0] / "WD14" / spec["repo_id"].split("/")[-1] / PurePosixPath(spec["files"][0]).parent


def download_links(model_id):
    spec = _model_spec(model_id)
    return {PurePosixPath(name).name: f"https://huggingface.co/{spec['repo_id']}/resolve/{spec['revision']}/{name}"
            for name in spec["files"]}


def find_local_model(model_id, extra_directory=""):
    spec = _model_spec(model_id)
    repository = "models--" + spec["repo_id"].replace("/", "--")
    suffix = PurePosixPath(spec["files"][0]).parts
    for _, candidate in discover_local_models(extra_directory)[0]:
        path = Path(candidate)
        in_repository = repository in path.parts and path.parts[-len(suffix):] == suffix
        in_named_directory = path.parent.name == model_id
        in_target = os.path.normcase(str(path.parent)) == os.path.normcase(str(model_directory(model_id)))
        if in_repository or in_named_directory or in_target:
            return candidate
    return None


def download_model(model_id, extra_directory=""):
    """Reuse the selected local model or explicitly fetch its matching file pair."""
    spec = _model_spec(model_id)
    if not _DOWNLOAD_LOCK.acquire(blocking=False):
        raise RuntimeError("模型正在下载，请稍后重新检测。")
    try:
        existing = find_local_model(model_id, extra_directory)
        if existing:
            yield "已使用本地模型。", existing
            return
        from huggingface_hub import hf_hub_download
        destination = _forge_paths()[0] / "WD14" / spec["repo_id"].split("/")[-1]
        for index, filename in enumerate(spec["files"], 1):
            yield f"正在下载 {spec['label']} · {PurePosixPath(filename).name}（{index}/{len(spec['files'])}）…", None
            hf_hub_download(repo_id=spec["repo_id"], filename=filename, revision=spec["revision"],
                            local_dir=str(destination), token=False)
        model, _ = _paired_model(model_directory(model_id) / "model.onnx")
        yield f"{spec['label']} 下载完成。", str(model)
    finally:
        _DOWNLOAD_LOCK.release()
