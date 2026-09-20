"""Resumable, read-only folder interrogation using the built-in tagger backend."""

import math
import os
from pathlib import Path
import stat
import threading

import prompt_studio_wd14


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


def _linked_directory(path):
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def list_image_files(folder, recursive=True):
    """Return stable absolute paths without following directory links or junctions."""
    if not str(folder or "").strip():
        raise ValueError("请输入图片文件夹路径。")
    root = Path(folder).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("图片路径必须是文件夹。")
    paths = []

    def fail(error):
        raise error

    for directory, children, files in os.walk(root, followlinks=False, onerror=fail):
        children[:] = sorted(
            (name for name in children if recursive and not _linked_directory(Path(directory) / name)),
            key=lambda name: (name.casefold(), name),
        )
        paths.extend(str(Path(directory) / name) for name in files if Path(name).suffix.lower() in IMAGE_EXTENSIONS)
    return sorted(paths, key=lambda path: (path.casefold(), path))


class ImageBatchJob:
    """One settings snapshot; completed items are never inferred again on resume.

    Call ``cancel.set()`` to stop after the current inference. To resume, explicitly
    clear the event before calling ``iter_run``. Injected interrogators take
    (PIL image, model, general threshold, character threshold) and return a string.
    """

    def __init__(self, folder, model, threshold=0.35, character_threshold=0.85, recursive=True):
        self.folder = str(Path(folder).expanduser().resolve()) if str(folder or "").strip() else ""
        self.model = str(model or "")
        self.threshold = float(threshold)
        self.character_threshold = float(character_threshold)
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in (self.threshold, self.character_threshold)):
            raise ValueError("反推阈值必须在 0 到 1 之间。")
        self.recursive = bool(recursive)
        self.signature = (self.folder, self.model, self.threshold, self.character_threshold, self.recursive)
        self.records = [dict(path=path, prompt="", status="pending", error="") for path in list_image_files(self.folder, self.recursive)]
        self.cancel = threading.Event()
        self._run_lock = threading.Lock()

    def snapshot(self):
        records = [dict(record) for record in self.records]
        counts = {status: sum(record["status"] == status for record in records)
                  for status in ("completed", "failed", "pending", "skipped")}
        return dict(records=records, total=len(records), counts=counts, cancelled=self.cancel.is_set())

    def iter_run(self, interrogate_fn=None, retry_failed=False):
        """Yield a detached snapshot after every attempted image, including failures."""
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("此批次正在处理，请勿重复启动。")
        try:
            from PIL import Image, ImageOps

            infer = interrogate_fn or prompt_studio_wd14.interrogate_image
            attempted = False
            for record in self.records:
                if self.cancel.is_set():
                    break
                if record["status"] != "pending" and not (retry_failed and record["status"] == "failed"):
                    continue
                attempted = True
                try:
                    with Image.open(record["path"]) as source:
                        image = ImageOps.exif_transpose(source).copy()
                    try:
                        prompt = infer(image, self.model, self.threshold, self.character_threshold)
                    finally:
                        image.close()
                    if not isinstance(prompt, str):
                        raise TypeError("反推结果必须是提示词文本。")
                    record.update(prompt=prompt.strip(), status="completed" if prompt.strip() else "skipped", error="")
                except Exception as exc:
                    record.update(prompt="", status="failed", error=str(exc))
                yield self.snapshot()
            if not attempted:
                yield self.snapshot()
        finally:
            self._run_lock.release()
