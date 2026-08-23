from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import json
import logging
import re
import threading
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import gradio as gr
from starlette.requests import Request

from prompt_studio_core import (
    BASE_MODEL_GUIDANCE, DEFAULT_WILDCARDS, PRESETS, PROVIDER_PROFILES, CredentialStore, StudioDB,
    build_system_prompt, build_user_message, call_llm, get_provider_profile, is_sfw_output,
    LLMRequestError,
    discover_ranbooru_cache, load_ranbooru_cache, process_tags,
    regional_format, validate_endpoint, _cosine, _tokens,
)


DB = StudioDB()
CREDENTIALS = CredentialStore()
LOGGER = logging.getLogger(__name__)
_STYLE_PATH = Path(__file__).resolve().parents[1] / "style.css"
UI_CSS = _STYLE_PATH.read_text(encoding="utf-8") if _STYLE_PATH.is_file() else ""
DEFAULT_LLM_SETTINGS = {
    "provider": "OpenAI Compatible",
    "endpoint": "http://127.0.0.1:1234/v1",
    "model": "",
    "temperature": 0.35,
    "timeout": 90,
    "max_tokens": 1024,
    "send_temperature": True,
}
GENERAL_CREATIVE_REQUEST_TEMPLATE = """围绕原始 Prompt 的核心主体，创作一条全新、独立成图的日系插画方向。

保留主体身份、用户明确固定特征、LoRA/权重和内容限制；场景、动作、构图、服装、道具、时间、天气和光线由模型自由选择。批量结果应自然地彼此不同，避免只改同义词、颜色、质量词或标签顺序；不要套用固定场景清单，也不要强行改变用户明确指定的元素。静态词库只作参考，用于补充兼容且可见的词汇，不要堆砌无关元素。

本要求只定义内容方向，不定义输出语言、标签/自然语言格式、字段顺序或结构化协议。以上格式与目标模型适配完全遵循当前选中的 System Prompt 预设。"""
KEMONOMIMI_LOLI_BATCH_TEMPLATE = """围绕原始 Prompt 的核心主体，批量创作彼此不同的二次元可爱兽耳小萝莉日系插画方向。

保留原始 Prompt 中明确指定的主体身份、外观特征、LoRA/权重和内容限制；突出角色作为画面主体的可爱、萌感、兽耳特征、表情与自然姿态。发色、瞳色、服装、动作、场景、道具、构图、时间、天气和光线都交由模型结合当前预设自由选择，不固定清单，不套用模板，不用重复元素凑差异。批量结果应自然地彼此不同，优先改变有视觉意义的动作、空间关系、物品关系、构图或光线，而不是只改同义词、颜色、质量词或标签顺序。静态词库只作参考，选择兼容且有用的词汇，不要堆词。

本要求只定义内容方向，不定义输出语言、标签/自然语言格式、字段顺序或结构化协议。以上格式与目标模型适配完全遵循当前选中的 System Prompt 预设。"""
KREA_ANIMA_POLISH_ROLE = """Role: Krea2 & Anima extreme-detail expansion prompt engineer for Japanese light-novel illustrations.

Task: From the user's text, tags, or reference image description, produce one complete full-English image prompt. Detail is the highest priority. Actively decompose every useful visible element instead of giving a short summary.

Core requirements:
- Describe hair movement, hair strands, head accessories, facial details, clothing layers, ornaments, handheld objects, floating objects, material surfaces, highlights, shadows, and spatial relationships.
- Distinguish tactile materials such as smooth satin, soft lace, plush fur, brushed fabric, polished metal, glass, paper, wood, leather, and translucent surfaces. State how light reacts to each material when it matters.
- Strengthen motion and cinematic storytelling through body weight, balance, hand placement, gaze direction, wind-driven hair and fabric, object motion, depth, and the small event happening in this exact frame.
- Allow rich detail density and tasteful decorative complexity, but omit unrelated clutter and contradictory elements.
- When the source is sparse, add reasonable visible clothing, props, environment, lighting, and material detail that supports the subject and scene. Never invent a separate subject or unrelated setting.
- Always consult the provided static vocabulary lexicon for compatible hair, clothing, prop, environment, camera, and material terms. Treat it as reference vocabulary: select and combine relevant entries, do not mechanically dump the list or copy unrelated entries.
- Aim for a polished Japanese light-novel illustration: refined character design, vivid but coherent color, clear material response, commercial finish, camera awareness, and emotional tension.

Anti-lazy rules:
- Never return a short, generic, high-level, or evasive description.
- Fully expand every section in the second part. Each section must contain concrete visible information plus relevant motion, texture, lighting, or position relationships.
- If one section has little source information, infer useful detail from movement, material, light, depth, and object relationships rather than leaving it as one short sentence.
- The Master Description must be a dense complete paragraph, not a short conclusion or a list of disconnected adjectives.

Output rules:
- Output English only. Output the result directly with no explanation and no Markdown code fence.
- Do not use weight syntax such as (text:1.2) or any other weighting syntax.
- Tags must be lowercase. Keep the requested anchor tags and separate tag items clearly.
- Detail is preferred over vague quality words, but every added detail must serve the visible image.

Output exactly this structure:

PART ONE: TAG ANCHORS
masterpiece, best quality, score_7, score_8_up, anime illustration, detailed, [subject count], [hair, hair color, head accessories], [face], [clothing and ornament details], [handheld and floating props], [pose and camera]

BREAK
One concise sentence stating the image density, dynamic direction, dominant color relationship, and emotional atmosphere.

PART TWO: EXTREME LAYERED DETAIL
Composition & Pose:
Expand the camera distance, viewing angle, body tilt, hand positions, weight shift, movement direction, subject placement, foreground framing, depth layers, and cinematic lens impression. Explain how the pose reads as one moment in progress.

Hair & Head Accessories:
Expand color, length, haircut, strand grouping, flyaway strands, wind or motion, texture, gloss, translucency at the edges, and every head accessory's shape, material, location, attachment, and light response.

Face & Expression:
Expand iris color, pupil shape, eye highlights, eyelid angle, eyebrows, gaze direction, mouth and lips, skin texture, blush or reflected light, expression, and the emotional event implied by the face.

Clothing & Details:
Expand the main garment colors and construction, layered order, collars, sleeves, hems, fasteners, embroidery, ribbons, jewelry, armor or trim, fabric thickness, folds, tension points, satin sheen, lace softness, plushness, metal reflections, translucency, and how clothing reacts to the pose and air.

Props & Floating Elements:
Expand the exact shape, scale, material, wear, grip, orientation, and light response of handheld props. Describe nearby, suspended, falling, glowing, or wind-carried elements and their distribution, depth, motion, and relationship to the subject.

Lighting & Color:
Expand the key light direction and softness, fill light, rim light, cast shadows, reflected light, specular highlights, material-specific reactions, color contrast, palette hierarchy, atmosphere, and emotional effect.

Background:
Expand only useful location elements, architecture, terrain, furniture, distant objects, color masses, foreground obstruction, middle-ground relationship, background depth, atmospheric perspective, and controlled blur. The background must support the subject instead of becoming an unrelated catalog of objects.

PART THREE: REFINED + MASTER
SUBJECT
Use long but clear English sentences to confirm the subject's appearance, clothing, key accessories, props, pose, and main material qualities.

MASTER DESCRIPTION
Write one high-density natural-language paragraph that recombines all important details. Emphasize layered decoration, movement, camera, light, material texture, color relationships, depth, and the small story implied by the frame. Do not make it a short summary."""
PRESET_UI_CHOICES = [
    ("Danbooru 标签", "Danbooru Tags"),
    ("Danbooru 标签 + 自然语言", "Danbooru + Natural"),
    ("自然语言", "Natural Language"),
    ("NoobAI 标签", "NoobAI Tags"),
    ("Anima 标签", "Anima Tags"),
    ("Krea 2 自然语言", "Krea 2 Natural"),
]
MODEL_UI_CHOICES = [
    ("自动 / 使用底模默认规则", "Auto / checkpoint default"),
    ("Pony / Illustrious", "Pony / Illustrious"),
    ("NoobAI", "NoobAI"),
    ("Flux", "Flux"),
    ("Anima", "Anima"),
    ("Krea 2", "Krea 2"),
]
OUTPUT_UI_CHOICES = [
    ("普通提示词", "Plain Prompt"),
    ("Regional JSON", "Regional JSON"),
    ("Regional Markdown", "Regional Markdown"),
]
PROVIDER_UI_CHOICES = [(profile["ui_label"], provider) for provider, profile in PROVIDER_PROFILES.items()]
ACTION_UI_CHOICES = [("格式转换", "Convert"), ("扩写", "Expand"), ("润色", "Polish")]
JSON_VARIATION_MODE_CHOICES = [("独立构图转换", "independent"), ("忠实格式转换", "faithful")]
PRESET_BASE_MODEL_DEFAULTS = {
    "NoobAI Tags": "NoobAI",
    "Anima Tags": "Anima",
    "Krea 2 Natural": "Krea 2",
    "Natural Language": "Auto / checkpoint default",
    "Danbooru Tags": "Auto / checkpoint default",
    "Danbooru + Natural": "Auto / checkpoint default",
}
RANBOORU_CONTENT_CHOICES = [
    ("Tag Prompt", "tags"),
    ("自然语言 Prompt", "natural"),
    ("Tag 与自然语言分别导入", "both"),
]
RANBOORU_RATING_CHOICES = [
    ("全部分级", "all"),
    ("仅 SFW（g / general / safe / sensitive）", "sfw"),
    ("仅 NSFW（q / questionable / e / explicit）", "nsfw"),
]
RANBOORU_LINK_DEFAULTS = {
    "database_path": str(discover_ranbooru_cache()),
    "content_mode": "both",
    "rating_filter": "all",
    "min_source_score": 0,
    "source_limit": 0,
    "tag_output_mode": "NoobAI Tags",
    "tag_base_model": "NoobAI",
    "natural_output_mode": "Krea 2 Natural",
    "natural_base_model": "Krea 2",
}
WORKFLOW_DEFAULTS = {
    "preset": "Danbooru Tags",
    "system_override": "",
    "base_model": "Auto / checkpoint default",
    "safety": "SFW",
    "nsfw_injection": "",
    "user_instruction": "",
    "structured_mode": "Plain Prompt",
    "region_count": 2,
    "remove_bad": True,
    "remove_terms": "",
    "shuffle": False,
    "spaces": False,
    "max_tags": 0,
    "few_shot_count": 3,
    "rag_min_score": 7,
    "save_score": 0,
    "cache_result": True,
    "batch_skip_existing": False,
    "batch_skip_failed": True,
    "wd_endpoint": "http://127.0.0.1:7860",
    "wd_model": "wd14-moat-v2",
    "wd_threshold": 0.35,
    "wildcard_path": str(DEFAULT_WILDCARDS),
}
_PROMPT_TARGETS: dict[str, Any] = {}
_INLINE_SLOTS: set[str] = set()
_INLINE_WORKFLOW_COMPONENTS: dict[str, dict[str, Any]] = {}
_INLINE_LOCK = threading.RLock()
_BATCH_CANCEL = threading.Event()
_BATCH_LOCK = threading.Lock()
_BATCH_CONTROL_LOCK = threading.Lock()
_BATCH_ACTIVE_TASK_ID = ""
_AUTO_LOOP_CANCEL = threading.Event()
_INLINE_CANCEL_EVENTS = {"txt2img": threading.Event(), "img2img": threading.Event()}
_BATCH_VARIATION_FOCI = (
    "subject action or gesture",
    "facial expression or emotional beat",
    "clothing and accessory details",
    "handheld object or nearby prop",
    "setting and environmental identity",
    "spatial layout and depth",
    "camera distance and framing",
    "time, weather, and light",
    "color relationship and material contrast",
    "interaction with a companion or object",
    "small event happening in this frame",
    "silhouette, balance, and movement direction",
)
_COMPLETE_PROMPT_CONTRACT = (
    "Complete prompt contract: preserve every source-fixed subject, identity, tag, and restriction; then cover the visible "
    "subject/appearance, clothing, action/expression, environment/objects, composition/camera, and time/weather/lighting. "
    "Use the selected output profile, keep one coherent single image, and return the complete prompt without headings, alternatives, or explanation; "
    "not a storyboard or collage."
)
_INDEPENDENT_BATCH_LOCK = threading.Lock()
_independent_batch_sequence = 0
_MIN_INDEPENDENT_BATCH_TEMPERATURE = 1.25
_MAX_BATCH_SIMILARITY = 0.62
_MIN_DISTINCTIVE_OVERLAP = 0.40
_MIN_DISTINCTIVE_COMMON = 4
_MAX_DIVERSITY_RETRIES = 3
_DIVERSITY_MEMORY_SIZE = 64
_DIVERSITY_REFERENCE_LIMIT = 10
_DIVERSITY_LEDGER_LIMIT = 18
_DIVERSITY_STOP_TOKENS = frozenset({
    "a", "an", "and", "anime", "art", "best", "background", "character", "clothes", "clothing",
    "color", "composition", "cute", "detailed", "expression", "face", "girl", "hair", "image",
    "illustration", "japanese", "light", "lighting", "masterpiece", "outfit", "pose", "quality",
    "scene", "setting", "subject", "style", "the", "very", "visual", "weather", "with",
    "anchors", "backgrounds", "break", "description", "detail", "details", "extreme", "layered",
    "master", "part", "paragraph", "props", "reference", "section", "tags", "three", "two", "use",
})
_RECENT_BATCH_OUTPUTS: dict[str, list[str]] = {}
_RECENT_BATCH_OUTPUTS_LOCK = threading.Lock()
_INDEPENDENT_CREATIVE_LOCK = threading.Lock()
_independent_creative_sequence = 0
_INDEPENDENT_CREATIVE_FOCI = (
    "change the character's action and expression while keeping the same core subject",
    "change the clothing details, accessories, and interaction with one meaningful prop",
    "change the setting and spatial depth with distinct foreground, middle ground, and background objects",
    "change the camera distance, viewing angle, and subject placement in the frame",
    "change the time, weather, and practical lighting so they affect the scene",
    "change the relationship between the character and nearby objects or companions",
    "change the small narrative event that is happening around the character",
    "change the environment from interior to exterior or vice versa without changing the core subject",
    "change the pose and movement direction while keeping the outfit fully described",
    "change the location layout and readable environmental landmarks",
    "change the foreground framing and depth cues while keeping the subject clearly visible",
    "combine a different action, prop, and environmental condition into one coherent moment",
)


def _independent_batch_directive(sequence: int = 0) -> str:
    """Create a compact variation directive; the ledger, not a scene blueprint, drives diversity."""
    global _independent_batch_sequence
    requested_sequence = int(sequence or 0)
    if requested_sequence > 0:
        focus_index = requested_sequence - 1
    else:
        with _INDEPENDENT_BATCH_LOCK:
            focus_index = _independent_batch_sequence
            _independent_batch_sequence += 1
    focus = _BATCH_VARIATION_FOCI[focus_index % len(_BATCH_VARIATION_FOCI)]
    nonce = uuid.uuid4().hex[:12]
    return (
        "Independent single-image batch item. " + _COMPLETE_PROMPT_CONTRACT + " "
        f"Optional variation axis for this item: {focus}. Use it as inspiration, not a fixed template; choose fresh compatible details "
        "and avoid repeated concepts, actions, and props from the diversity ledger. Do not vary only style, quality words, synonyms, or tag order. "
        "Use static vocabulary only as a reference. "
        f"独立请求标识: {nonce}."
    )


def _independent_creative_directive(sequence: int = 0) -> str:
    """Create the compact inline variation and completeness contract."""
    global _independent_creative_sequence
    requested_sequence = int(sequence or 0)
    if requested_sequence > 0:
        focus_index = requested_sequence - 1
    else:
        with _INDEPENDENT_CREATIVE_LOCK:
            focus_index = _independent_creative_sequence
            _independent_creative_sequence += 1
    focus = _INDEPENDENT_CREATIVE_FOCI[focus_index % len(_INDEPENDENT_CREATIVE_FOCI)]
    nonce = uuid.uuid4().hex[:12]
    return (
        "Independent single-image inline request. " + _COMPLETE_PROMPT_CONTRACT + " "
        f"Optional variation axis: {focus}. Reinterpret it freely and avoid repeated concepts from the diversity ledger. "
        "Use static vocabulary only as a reference. "
        f"Independent request id: {nonce}."
    )


def _distinctive_tokens(text: str, stable_source: str = "") -> set[str]:
    """Return content-bearing tokens, excluding the fixed subject and boilerplate."""
    stable = _tokens(stable_source)
    tokens = _tokens(text) - stable
    return {
        token for token in tokens
        if token not in _DIVERSITY_STOP_TOKENS and (len(token) >= 3 or "_" in token)
    }


def _prompt_similarity(left: str, right: str, stable_source: str = "") -> float:
    """Measure content overlap without requiring an embedding service."""
    stable = _tokens(stable_source)
    return _cosine(_tokens(left) - stable, _tokens(right) - stable)


def _distinctive_overlap(left: str, right: str, stable_source: str = "") -> float:
    left_tokens = _distinctive_tokens(left, stable_source)
    right_tokens = _distinctive_tokens(right, stable_source)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def _is_diversity_duplicate(left: str, right: str, stable_source: str = "") -> bool:
    """Catch both near-copy prompts and long prompts sharing the same key concepts."""
    if _prompt_similarity(left, right, stable_source) >= _MAX_BATCH_SIMILARITY:
        return True
    left_tokens = _distinctive_tokens(left, stable_source)
    right_tokens = _distinctive_tokens(right, stable_source)
    common = len(left_tokens & right_tokens)
    return common >= _MIN_DISTINCTIVE_COMMON and _distinctive_overlap(left, right, stable_source) >= _MIN_DISTINCTIVE_OVERLAP


def _diversity_exclusion_terms_from_outputs(
    outputs: list[str], source: str = "", limit: int = _DIVERSITY_LEDGER_LIMIT,
) -> list[str]:
    """Build a compact ledger of concepts repeatedly used by earlier outputs."""
    counts: Counter[str] = Counter()
    for output in outputs:
        counts.update(_distinctive_tokens(str(output or ""), source))
    return [
        token for token, count in counts.most_common()
        if count >= 2
    ][:max(1, int(limit or 1))]


def _remember_diverse_output(source: str, prompt: str, force: bool = False) -> bool:
    """Reject near-identical outputs for the same repeated batch request."""
    key = hashlib.sha256(str(source or "").strip().encode("utf-8")).hexdigest()
    with _RECENT_BATCH_OUTPUTS_LOCK:
        previous = _RECENT_BATCH_OUTPUTS.setdefault(key, [])
        duplicate = any(_is_diversity_duplicate(prompt, item, source) for item in previous)
        if not duplicate or force:
            previous.append(prompt)
            del previous[:-_DIVERSITY_MEMORY_SIZE]
        return not duplicate


def _recent_diverse_outputs(source: str, limit: int = _DIVERSITY_REFERENCE_LIMIT) -> list[str]:
    key = hashlib.sha256(str(source or "").strip().encode("utf-8")).hexdigest()
    with _RECENT_BATCH_OUTPUTS_LOCK:
        return list(_RECENT_BATCH_OUTPUTS.get(key, [])[-max(1, int(limit or 1)):])


def _diversity_exclusion_terms(source: str, limit: int = _DIVERSITY_LEDGER_LIMIT) -> list[str]:
    key = hashlib.sha256(str(source or "").strip().encode("utf-8")).hexdigest()
    with _RECENT_BATCH_OUTPUTS_LOCK:
        previous = list(_RECENT_BATCH_OUTPUTS.get(key, []))
    return _diversity_exclusion_terms_from_outputs(previous, source, limit)


_PNG_BATCH_CANCEL = threading.Event()
_SERVER_QUEUE_WAKE = threading.Event()
_SERVER_QUEUE_CANCEL = threading.Event()
_SERVER_QUEUE_CANCEL_BATCHES: set[str] = set()
_SERVER_QUEUE_CANCEL_LOCK = threading.Lock()
_SERVER_QUEUE_THREAD: threading.Thread | None = None
_SERVER_QUEUE_START_LOCK = threading.Lock()


def _server_queue_snapshot(batch_id: str, limit: int = 500) -> dict[str, Any]:
    records = DB.list_server_queue(str(batch_id or ""), limit)
    counts: dict[str, int] = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    total = len(records)
    done = counts.get("completed", 0)
    failed = counts.get("error", 0)
    cancelled = counts.get("cancelled", 0)
    active = counts.get("running", 0)
    if not total:
        status = "未找到服务端队列任务"
    elif active:
        status = f"服务端队列运行中：完成 {done}/{total}，失败 {failed}，取消 {cancelled}"
    elif done + failed + cancelled >= total:
        status = f"服务端队列已结束：完成 {done}，失败 {failed}，取消 {cancelled}"
    else:
        status = f"服务端队列等待中：{done}/{total}"
    return {"batch_id": str(batch_id or ""), "status": status, "counts": counts, "jobs": records}


def _server_render_prompt(prompt: str, target: str, job_id: str) -> None:
    """Submit txt2img through Forge's own API from the server worker."""
    target = str(target or "none")
    if target == "none":
        return
    if target != "txt2img":
        raise RuntimeError("服务端队列目前只支持 txt2img；img2img 需要由队列任务提供 init image")
    try:
        from modules import shared
        port = int(getattr(shared.cmd_opts, "port", 7860) or 7860)
        auth = str(getattr(shared.cmd_opts, "api_auth", "") or "").split(",", 1)[0]
    except Exception:
        port, auth = 7860, ""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/sdapi/v1/txt2img",
        data=json.dumps({
            "prompt": str(prompt), "negative_prompt": "", "steps": 20,
            "send_images": False, "save_images": True,
            "force_task_id": f"server-queue-{job_id}",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    if auth and ":" in auth:
        request.add_header("Authorization", "Basic " + base64.b64encode(auth.encode("utf-8")).decode("ascii"))
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            response.read(1024)
    except urllib.error.HTTPError as error:
        body = error.read(2048).decode("utf-8", "replace")
        raise RuntimeError(f"Forge API HTTP {error.code}: {body[:500]}") from error


def _server_queue_worker() -> None:
    while True:
        job = DB.claim_server_queue_job()
        if not job:
            _SERVER_QUEUE_WAKE.wait(1.0)
            _SERVER_QUEUE_WAKE.clear()
            continue
        with _SERVER_QUEUE_CANCEL_LOCK:
            batch_cancelled = job["batch_id"] in _SERVER_QUEUE_CANCEL_BATCHES
        if _SERVER_QUEUE_CANCEL.is_set() or batch_cancelled:
            DB.update_server_queue_job(job["id"], "cancelled", error="服务端队列已取消")
            _SERVER_QUEUE_CANCEL.clear()
            continue
        config = dict(job.get("config") or {})
        cancel_event = _SERVER_QUEUE_CANCEL
        try:
            generated, _system, status = _generate(
                job["request"], "", config.get("preset", "Danbooru Tags"), config.get("system_override", ""),
                config.get("base_model", "Auto / checkpoint default"), config.get("safety", "SFW"),
                config.get("nsfw_injection", ""), config.get("user_instruction", ""),
                config.get("provider", "OpenAI Compatible"), config.get("endpoint", ""), config.get("model", ""), "",
                float(config.get("temperature", 1.25) or 1.25), int(config.get("timeout", 90) or 90), int(config.get("max_tokens", 1024) or 1024),
                bool(config.get("send_temperature", True)), 0, 0,
                bool(config.get("remove_bad", True)), config.get("remove_terms", ""), bool(config.get("shuffle", False)),
                bool(config.get("spaces", False)), int(config.get("max_tags", 0) or 0), config.get("structured_mode", "Plain Prompt"),
                int(config.get("region_count", 1) or 1), float(config.get("save_score", 0) or 0), bool(config.get("cache_result", True)),
                "server_queue", f"server_queue:{job['batch_id']}:{job['position']}", True, cancel_event,
                _independent_batch_directive(job["position"]),
            )
            if cancel_event.is_set():
                DB.update_server_queue_job(job["id"], "cancelled", error="服务端队列已取消")
                cancel_event.clear()
                continue
            if not generated:
                raise RuntimeError(status or "LLM 未返回 Prompt")
            if bool(config.get("cache_result", True)):
                DB.save_prompt(generated, "", config.get("preset", "Danbooru Tags"), config.get("base_model", ""), 0, job["request"], score_source="unrated", source_kind="server_queue", source_ref=f"server_queue:{job['id']}", dedupe=True)
            _server_render_prompt(generated, job.get("target", "none"), job["id"])
            DB.update_server_queue_job(job["id"], "completed", prompt=generated)
        except Exception as error:
            LOGGER.exception("server queue job failed: %s", job["id"])
            with _SERVER_QUEUE_CANCEL_LOCK:
                batch_cancelled = job["batch_id"] in _SERVER_QUEUE_CANCEL_BATCHES
            DB.update_server_queue_job(job["id"], "cancelled" if cancel_event.is_set() or batch_cancelled else "error", error=str(error))
            if cancel_event.is_set():
                cancel_event.clear()


def _ensure_server_queue_worker() -> None:
    global _SERVER_QUEUE_THREAD
    with _SERVER_QUEUE_START_LOCK:
        if _SERVER_QUEUE_THREAD and _SERVER_QUEUE_THREAD.is_alive():
            return
        DB.recover_server_queue()
        _SERVER_QUEUE_CANCEL.clear()
        _SERVER_QUEUE_THREAD = threading.Thread(target=_server_queue_worker, name="llm-prompt-studio-server-queue", daemon=True)
        _SERVER_QUEUE_THREAD.start()


def _enqueue_server_queue(payload: dict[str, Any]) -> dict[str, Any]:
    requests = payload.get("requests") if isinstance(payload, dict) else None
    if not isinstance(requests, list):
        requests = str(payload.get("source_text") or "").splitlines() if isinstance(payload, dict) else []
    requests = [str(item).strip() for item in requests if str(item).strip() and not str(item).strip().startswith("#")]
    if not requests:
        raise ValueError("服务端队列至少需要一条请求")
    if len(requests) > 1000:
        raise ValueError("单次服务端队列最多 1000 条请求")
    if any(len(request) > 12000 for request in requests):
        raise ValueError("单条服务端队列请求最多 12000 个字符")
    target = str(payload.get("target") or "none")
    if target not in {"none", "txt2img"}:
        raise ValueError("服务端队列目标只支持 none 或 txt2img")
    config = dict(payload.get("config") or {}) if isinstance(payload, dict) else {}
    workflow = _workflow_settings()
    connection = _connection_settings()
    merged = {
        "preset": workflow["preset"], "base_model": workflow["base_model"], "safety": workflow["safety"],
        "temperature": max(float(connection["temperature"] or 1.25), _MIN_INDEPENDENT_BATCH_TEMPERATURE),
        "timeout": connection["timeout"], "max_tokens": connection["max_tokens"], "send_temperature": connection["send_temperature"],
        "provider": connection["provider"], "endpoint": connection["endpoint"], "model": connection["model"],
        "system_override": workflow["system_override"], "nsfw_injection": workflow["nsfw_injection"], "user_instruction": workflow["user_instruction"],
        "cache_result": True, "target": target,
    }
    merged.update({key: value for key, value in config.items() if key in merged and key not in {"provider", "endpoint", "model"}})
    batch_id = uuid.uuid4().hex
    with _SERVER_QUEUE_CANCEL_LOCK:
        _SERVER_QUEUE_CANCEL_BATCHES.discard(batch_id)
    count = DB.enqueue_server_queue(batch_id, [
        {"request": request, "position": index, "target": merged["target"], "config": merged}
        for index, request in enumerate(requests, start=1)
    ])
    _ensure_server_queue_worker()
    _SERVER_QUEUE_WAKE.set()
    return _server_queue_snapshot(batch_id) | {"queued": count}


def _server_queue_html(snapshot: dict[str, Any]) -> str:
    rows = []
    for item in snapshot.get("jobs", []):
        rows.append(
            "<div class='lps-server-queue-row'>"
            f"<span>{int(item.get('position', 0))}</span>"
            f"<b>{html.escape(str(item.get('status', '')))}</b>"
            f"<code>{html.escape(str(item.get('prompt') or item.get('request') or ''))}</code>"
            f"<small>{html.escape(str(item.get('error') or ''))}</small>"
            "</div>"
        )
    return "".join(rows) or "<div class='lps-auto-loop-empty'>暂无服务端队列记录。</div>"


def _server_queue_start_ui(source_text: str, target: str):
    try:
        snapshot = _enqueue_server_queue({"source_text": source_text, "target": target})
        return snapshot["batch_id"], snapshot["status"], _server_queue_html(snapshot)
    except Exception as error:
        return "", f"服务端队列提交失败：{_safe_error(error)}", ""


def _server_queue_refresh_ui(batch_id: str):
    snapshot = _server_queue_snapshot(batch_id)
    return snapshot["status"], _server_queue_html(snapshot)


def _server_queue_cancel_ui(batch_id: str):
    with _SERVER_QUEUE_CANCEL_LOCK:
        _SERVER_QUEUE_CANCEL_BATCHES.add(str(batch_id or ""))
    _SERVER_QUEUE_CANCEL.set()
    DB.cancel_server_queue(batch_id)
    snapshot = _server_queue_snapshot(batch_id)
    return snapshot["status"] + "；已请求取消", _server_queue_html(snapshot)


def _as_rows(records: list[dict[str, Any]]) -> list[list[Any]]:
    labels = {"llm": "LLM", "manual": "手动", "unrated": "未评分"}
    source_labels = {"ranbooru": "Ranbooru", "auto_loop": "自动生图循环"}
    return [[
        row.get("visible_position", ""), row["id"], row["score"], labels.get(row.get("score_source"), "手动"),
        row.get("score_model", ""), row["output_mode"], row["base_model"], row["prompt"],
        row["negative_prompt"], row["tags"], row.get("score_reason", ""),
        source_labels.get(row.get("source_kind"), row.get("source_kind", "")), row.get("source_ref", ""),
    ] for row in records]


def _cache_choices(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    choices = []
    for row in records:
        preview = " ".join(str(row.get("prompt") or "").split())
        if len(preview) > 72:
            preview = preview[:69] + "..."
        choices.append((f"#{row['visible_position']} · ID {row['id']} · {preview}", str(row["id"])))
    return choices


def _cache_records(query: str = "", min_score: float = 0, output_mode: str = "全部", base_model: str = "全部") -> list[dict[str, Any]]:
    return DB.list_prompts(
        str(query or ""),
        min_score=float(min_score or 0),
        output_mode="" if output_mode == "全部" else str(output_mode or ""),
        base_model="" if base_model == "全部" else str(base_model or ""),
    )


def _refresh_cache(query: str = "", min_score: float = 0, output_mode: str = "全部", base_model: str = "全部"):
    records = _cache_records(query, min_score, output_mode, base_model)
    if records:
        message = f"当前筛选显示 {len(records)} 条缓存。点击表格任意单元格可载入该行，或在选择框中多选。"
    else:
        message = "当前筛选没有记录。可清除筛选，或在批量缓存页导入和生成 Prompt。"
    return gr.update(value=_as_rows(records)), gr.update(choices=_cache_choices(records), value=[]), message


def _filtered_cache_updates(query: str = "", min_score: float = 0, output_mode: str = "全部", base_model: str = "全部", selected=None):
    records = _cache_records(query, min_score, output_mode, base_model)
    available_ids = {str(record["id"]) for record in records}
    retained_selection = [value for value in _selected_values(selected) if value in available_ids]
    return (
        gr.update(value=_as_rows(records)),
        gr.update(choices=_cache_choices(records), value=retained_selection),
    )


def _clear_cache_filters():
    table, choices, status = _refresh_cache()
    return "", 0, "全部", "全部", table, choices, status


def _safe_error(error: Exception) -> str:
    return html.escape(str(error), quote=False)


def _ranbooru_link_settings() -> dict[str, Any]:
    stored = DB.get_setting("ranbooru_link_v1", {}) or {}
    values = {**RANBOORU_LINK_DEFAULTS, **(stored if isinstance(stored, dict) else {})}
    if values["content_mode"] not in {value for _, value in RANBOORU_CONTENT_CHOICES}:
        values["content_mode"] = RANBOORU_LINK_DEFAULTS["content_mode"]
    if values["rating_filter"] not in {value for _, value in RANBOORU_RATING_CHOICES}:
        values["rating_filter"] = RANBOORU_LINK_DEFAULTS["rating_filter"]
    for key in ("tag_output_mode", "natural_output_mode"):
        if values[key] not in PRESETS:
            values[key] = RANBOORU_LINK_DEFAULTS[key]
    for key in ("tag_base_model", "natural_base_model"):
        if values[key] not in BASE_MODEL_GUIDANCE:
            values[key] = RANBOORU_LINK_DEFAULTS[key]
    try:
        values["min_source_score"] = max(0, int(values["min_source_score"] or 0))
    except (TypeError, ValueError):
        values["min_source_score"] = 0
    try:
        values["source_limit"] = max(0, min(int(values["source_limit"] or 0), DB.MAX_IMPORT_RECORDS))
    except (TypeError, ValueError):
        values["source_limit"] = 0
    values["database_path"] = str(values.get("database_path") or discover_ranbooru_cache())
    return values


def _save_ranbooru_link_settings(
    database_path, content_mode, rating_filter, min_source_score, source_limit,
    tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
):
    values = {
        "version": 1,
        "database_path": str(database_path or discover_ranbooru_cache()),
        "content_mode": content_mode,
        "rating_filter": rating_filter,
        "min_source_score": max(0, int(min_source_score or 0)),
        "source_limit": max(0, min(int(source_limit or 0), DB.MAX_IMPORT_RECORDS)),
        "tag_output_mode": tag_output_mode,
        "tag_base_model": tag_base_model,
        "natural_output_mode": natural_output_mode,
        "natural_base_model": natural_base_model,
    }
    DB.set_setting("ranbooru_link_v1", values)
    return "Ranbooru 联动参数已保存，下次打开界面会自动填入。"


def _detect_ranbooru_cache():
    path = discover_ranbooru_cache()
    if path.is_file():
        return str(path), f"已检测到 Ranbooru 缓存：{path}"
    return str(path), f"未检测到 Ranbooru 缓存，请检查路径：{path}"


def _workflow_settings() -> dict[str, Any]:
    stored = DB.get_setting("workflow_settings_v1", {}) or {}
    try:
        stored_version = int(stored.get("version") or 0) if isinstance(stored, dict) else 0
    except (TypeError, ValueError):
        stored_version = 0
    stored_values = stored if isinstance(stored, dict) else {}
    values = {key: stored_values.get(key, default) for key, default in WORKFLOW_DEFAULTS.items()}
    if stored_version < 2:
        values["batch_skip_existing"] = False
    preset_values = {value for _, value in PRESET_UI_CHOICES}
    model_values = {value for _, value in MODEL_UI_CHOICES}
    output_values = {value for _, value in OUTPUT_UI_CHOICES}
    if values["preset"] not in preset_values:
        values["preset"] = WORKFLOW_DEFAULTS["preset"]
    if values["base_model"] not in model_values:
        values["base_model"] = WORKFLOW_DEFAULTS["base_model"]
    if values["structured_mode"] not in output_values:
        values["structured_mode"] = WORKFLOW_DEFAULTS["structured_mode"]
    if values["safety"] not in {"SFW", "NSFW"}:
        values["safety"] = WORKFLOW_DEFAULTS["safety"]
    integer_limits = {
        "region_count": (1, 8), "max_tags": (0, 200), "few_shot_count": (0, 8),
    }
    float_limits = {
        "rag_min_score": (0, 10), "save_score": (0, 10),
        "wd_threshold": (0, 1),
    }
    for key, (minimum, maximum) in integer_limits.items():
        try:
            values[key] = max(minimum, min(int(values[key]), maximum))
        except (TypeError, ValueError):
            values[key] = WORKFLOW_DEFAULTS[key]
    for key, (minimum, maximum) in float_limits.items():
        try:
            values[key] = max(minimum, min(float(values[key]), maximum))
        except (TypeError, ValueError):
            values[key] = WORKFLOW_DEFAULTS[key]
    for key in ["remove_bad", "shuffle", "spaces", "cache_result", "batch_skip_existing", "batch_skip_failed"]:
        values[key] = bool(values[key])
    for key in [
        "system_override", "nsfw_injection", "user_instruction", "remove_terms",
        "wd_endpoint", "wd_model", "wildcard_path",
    ]:
        values[key] = str(values.get(key) or "")
    return values


def _save_workflow_values(updates: dict[str, Any]) -> str:
    values = _workflow_settings()
    values.update(updates)
    DB.set_setting("workflow_settings_v1", {**values, "version": 2})
    return "工作参数已保存。下次打开完整页和内嵌面板时会自动填入。"


def _sync_value(value):
    return value


def _sync_value_pair(value):
    return value, value


def _sync_value_triplet(value):
    return value, value, value


def _bind_workflow_sync(source, outputs, event="input"):
    if not outputs:
        return
    callbacks = {1: _sync_value, 2: _sync_value_pair, 3: _sync_value_triplet}
    callback = callbacks.get(len(outputs))
    if callback is None:
        raise ValueError(f"Unsupported workflow synchronization target count: {len(outputs)}")
    getattr(source, event)(callback, inputs=source, outputs=outputs, queue=False)


def _save_workflow_settings(
    preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    structured_mode, region_count, remove_bad, remove_terms, shuffle, spaces, max_tags,
    few_shot_count, rag_min_score, save_score, cache_result,
    batch_skip_existing, batch_skip_failed,
    wd_endpoint, wd_model, wd_threshold, wildcard_path,
):
    return _save_workflow_values({
        "preset": preset, "system_override": system_override, "base_model": base_model,
        "safety": safety, "nsfw_injection": nsfw_injection, "user_instruction": user_instruction,
        "structured_mode": structured_mode, "region_count": int(region_count or 1),
        "remove_bad": bool(remove_bad), "remove_terms": remove_terms, "shuffle": bool(shuffle),
        "spaces": bool(spaces), "max_tags": int(max_tags or 0),
        "few_shot_count": int(few_shot_count or 0), "rag_min_score": float(rag_min_score or 0),
        "save_score": float(save_score or 0), "cache_result": bool(cache_result),
        "batch_skip_existing": bool(batch_skip_existing), "batch_skip_failed": bool(batch_skip_failed),
        "wd_endpoint": wd_endpoint, "wd_model": wd_model,
        "wd_threshold": float(wd_threshold or 0), "wildcard_path": wildcard_path,
    })


def _workflow_component_values(values: dict[str, Any]) -> list[Any]:
    return [values[key] for key in [
        "preset", "system_override", "base_model", "safety", "nsfw_injection", "user_instruction",
        "structured_mode", "region_count", "remove_bad", "remove_terms", "shuffle", "spaces", "max_tags",
        "few_shot_count", "rag_min_score", "save_score", "cache_result",
        "batch_skip_existing", "batch_skip_failed",
        "wd_endpoint", "wd_model", "wd_threshold", "wildcard_path",
    ]]


def _reset_workflow_settings():
    DB.delete_setting("workflow_settings_v1")
    return (*_workflow_component_values(WORKFLOW_DEFAULTS), "已恢复默认工作参数。下次打开界面也会使用默认值。")


def _connection_store() -> dict[str, Any]:
    DB.delete_setting("llm_connection")
    stored = DB.get_setting("llm_connections_v2", {}) or {}
    if isinstance(stored, dict) and isinstance(stored.get("providers"), dict):
        return stored
    provider = DEFAULT_LLM_SETTINGS["provider"]
    return {"version": 2, "active_provider": provider, "providers": {}}


def _connection_settings(provider: str | None = None) -> dict[str, Any]:
    store = _connection_store()
    provider = str(provider or store.get("active_provider") or DEFAULT_LLM_SETTINGS["provider"])
    if provider not in PROVIDER_PROFILES:
        provider = DEFAULT_LLM_SETTINGS["provider"]
    profile = get_provider_profile(provider)
    saved = store.get("providers", {}).get(provider, {})
    if not isinstance(saved, dict):
        saved = {}
    try:
        temperature = max(0.0, min(float(saved.get("temperature", DEFAULT_LLM_SETTINGS["temperature"])), 2.0))
    except (TypeError, ValueError):
        temperature = DEFAULT_LLM_SETTINGS["temperature"]
    try:
        timeout = max(5, min(int(saved.get("timeout", DEFAULT_LLM_SETTINGS["timeout"])), 600))
    except (TypeError, ValueError):
        timeout = DEFAULT_LLM_SETTINGS["timeout"]
    try:
        max_tokens = max(0, min(int(saved.get("max_tokens", DEFAULT_LLM_SETTINGS["max_tokens"])), 262144))
    except (TypeError, ValueError):
        max_tokens = DEFAULT_LLM_SETTINGS["max_tokens"]
    return {
        "provider": provider,
        "endpoint": str(saved.get("endpoint") or profile["default_endpoint"]),
        "model": str(saved.get("model") or ""),
        "temperature": temperature,
        "timeout": timeout,
        "max_tokens": max_tokens,
        "send_temperature": bool(saved.get("send_temperature", profile["send_temperature"])),
    }


def _credential_status(provider: str, endpoint: str) -> str:
    if CREDENTIALS.has_matching(provider, endpoint):
        return "已找到该 Provider 与 URL 对应的服务端 API Key。"
    if get_provider_profile(provider).get("requires_api_key"):
        return "该 Provider 需要 API Key；保存后输入框可留空。"
    return "该 Provider 默认不要求 API Key；如代理服务要求认证仍可填写。"


def _load_provider_settings(provider):
    settings = _connection_settings(provider)
    return (
        settings["endpoint"], settings["model"], settings["temperature"], settings["timeout"],
        settings["max_tokens"], settings["send_temperature"],
        _credential_status(settings["provider"], settings["endpoint"]),
    )


def _load_active_connection_settings():
    settings = _connection_settings()
    return (
        settings["provider"], settings["endpoint"], settings["model"], settings["temperature"],
        settings["timeout"], settings["max_tokens"], settings["send_temperature"],
        _credential_status(settings["provider"], settings["endpoint"]),
    )


def _load_record(record_id):
    try:
        record = DB.get_prompt(int(record_id))
    except (TypeError, ValueError):
        record = None
    if not record:
        return "", "", "", "Danbooru Tags", "Auto / checkpoint default", 0, "", "未找到该记录"
    return (
        str(record["id"]), record["prompt"], record["negative_prompt"],
        record["output_mode"] or "Danbooru Tags", record["base_model"] or "Auto / checkpoint default",
        record["score"], record["tags"],
        f"已载入记录 #{record['id']}。评分来源：{record.get('score_source') or 'manual'}"
        + (f"；评分理由：{record.get('score_reason')}" if record.get("score_reason") else "")
        + "。手动保存会把评分来源改为 manual。",
    )


def _table_row_id(rows, row_index: int) -> str:
    if hasattr(rows, "values"):
        rows = rows.values.tolist()
    if isinstance(rows, dict):
        rows = rows.get("data", [])
    if not isinstance(rows, list) or row_index < 0 or row_index >= len(rows):
        return ""
    row = rows[row_index]
    if not isinstance(row, (list, tuple)) or len(row) < 2:
        return ""
    return str(row[1])


def _selected_values(selected_ids) -> list[str]:
    if isinstance(selected_ids, (list, tuple, set)):
        values = selected_ids
    elif selected_ids in {None, ""}:
        values = []
    else:
        values = [selected_ids]
    return [str(value).strip() for value in values if str(value).strip()]


def _select_cache_row(rows, evt: gr.SelectData):
    index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    record_id = _table_row_id(rows, int(index))
    loaded = _load_record(record_id)
    return gr.update(value=[record_id] if record_id else []), *loaded


def _load_selected_record(selected_ids):
    values = _selected_values(selected_ids)
    if len(values) != 1:
        return "", "", "", "Danbooru Tags", "Auto / checkpoint default", 0, "", f"已选择 {len(values)} 条。编辑前请选择一条记录。"
    return _load_record(values[0])


def _save_record(record_id, prompt, negative, output_mode, base_model, score, tags, query="", min_score=0, filter_output_mode="全部", filter_base_model="全部"):
    try:
        parsed_id = int(record_id) if str(record_id).strip() else None
    except ValueError:
        parsed_id = None
    if not str(prompt).strip():
        return "提示词不能为空", gr.update(), gr.update()
    saved_id = DB.save_prompt(
        str(prompt).strip(), str(negative or ""), output_mode, base_model, float(score or 0),
        str(tags or ""), parsed_id, source_kind="", source_ref="",
    )
    table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model, [str(saved_id)])
    return f"已保存缓存记录 #{saved_id}", table, choices


def _save_record_as_new(prompt, negative, output_mode, base_model, score, tags, query="", min_score=0, filter_output_mode="全部", filter_base_model="全部"):
    return _save_record("", prompt, negative, output_mode, base_model, score, tags, query, min_score, filter_output_mode, filter_base_model)


def _delete_records(ids, query="", min_score=0, output_mode="全部", base_model="全部"):
    if isinstance(ids, (list, tuple, set)):
        pieces = [str(piece).strip() for piece in ids if str(piece).strip()]
    else:
        pieces = [piece.strip() for piece in str(ids or "").split(",") if piece.strip()]
    try:
        count = DB.delete_prompts([int(piece) for piece in pieces])
    except ValueError:
        return "选择中包含无效记录 ID", gr.update(), gr.update()
    table, choices = _filtered_cache_updates(query, min_score, output_mode, base_model)
    return f"已删除 {count} 条记录，并创建自动备份。可使用撤销按钮恢复。", table, choices


def _preview_selected(ids):
    values = [int(value) for value in _selected_values(ids) if value.isdigit()]
    records = [DB.get_prompt(value) for value in values]
    records = [record for record in records if record]
    if not records:
        return "尚未选择缓存记录。", []
    lines = [f"将操作 {len(records)} 条记录："]
    for record in records[:12]:
        prompt = " ".join(str(record["prompt"] or "").split())
        lines.append(f"ID {record['id']} · {prompt[:120]}{'...' if len(prompt) > 120 else ''}")
    if len(records) > 12:
        lines.append(f"另有 {len(records) - 12} 条未展开。")
    return "\n".join(lines), [str(record["id"]) for record in records]


def _delete_previewed_records(ids, previewed_ids, query="", min_score=0, output_mode="全部", base_model="全部"):
    selected = sorted(_selected_values(ids))
    previewed = sorted(_selected_values(previewed_ids))
    if not selected or selected != previewed:
        return "删除未执行：选择已变化，请先点击“预览所选”核对当前记录。", gr.update(), gr.update()
    return _delete_records(selected, query, min_score, output_mode, base_model)


def _export_selected(ids, file_format):
    values = [int(value) for value in _selected_values(ids) if value.isdigit()]
    existing = [value for value in values if DB.get_prompt(value)]
    if not existing:
        return "请先选择要导出的缓存记录", None
    try:
        path = DB.export_records(str(file_format or "JSON").lower(), ids=existing)
        missing = len(values) - len(existing)
        suffix = f"；忽略已不存在的 {missing} 条选择" if missing else ""
        return f"已导出选中的 {len(existing)} 条记录{suffix}：{path}", path
    except Exception as error:
        return f"导出失败：{_safe_error(error)}", None


def _bulk_cache(import_text, output_mode, base_model, default_score, query="", min_score=0, filter_output_mode="全部", filter_base_model="全部"):
    """Import one prompt per line, optionally prefixed by `score<TAB>`."""
    records, _summary = _parse_bulk_cache(import_text, output_mode, base_model, default_score)
    stats = DB.save_prompts_batch(records, dedupe=True)
    table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
    return (
        f"批量导入完成：新增 {stats['inserted']} 条，跳过重复 {stats['duplicates']} 条",
        table, choices,
    )


def _parse_bulk_cache(import_text, output_mode, base_model, default_score):
    records = []
    ignored = 0
    for line in str(import_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            ignored += 1
            continue
        score, prompt = float(default_score or 0), line
        if "\t" in line:
            possible_score, prompt = line.split("\t", 1)
            try:
                score = float(possible_score)
            except ValueError:
                prompt = line
        if prompt.strip():
            records.append({"prompt": prompt.strip(), "output_mode": output_mode, "base_model": base_model, "score": score})
    return records, {"records": len(records), "ignored": ignored}


def _preview_bulk_cache(import_text, output_mode, base_model, default_score):
    records, summary = _parse_bulk_cache(import_text, output_mode, base_model, default_score)
    rows = [[index, item["score"], item["prompt"]] for index, item in enumerate(records[:200], start=1)]
    message = f"解析到 {summary['records']} 条可导入 Prompt，忽略 {summary['ignored']} 个空行或注释行。"
    if len(records) > 200:
        message += " 预览仅显示前 200 条。"
    return gr.update(value=rows), message


def _preview_positions(position_spec):
    try:
        records = DB.get_by_positions(position_spec)
        return (
            f"范围预览命中 {len(records)} 条记录。删除前请核对下表。",
            gr.update(value=_as_rows(records)), gr.update(choices=_cache_choices(records), value=[]),
        )
    except ValueError as error:
        return f"序号范围格式错误：{error}", gr.update(), gr.update()


def _delete_positions(position_spec, query="", min_score=0, output_mode="全部", base_model="全部"):
    try:
        count = DB.delete_by_positions(position_spec)
        table, choices = _filtered_cache_updates(query, min_score, output_mode, base_model)
        return (
            f"已删除 {count} 条记录，并创建自动备份；可使用撤销按钮恢复。",
            table, choices,
        )
    except ValueError as error:
        return f"序号范围格式错误：{error}", gr.update(), gr.update()


def _undo_last_delete(query="", min_score=0, output_mode="全部", base_model="全部"):
    count = DB.undo_last_delete()
    message = f"已恢复上次删除的 {count} 条记录" if count else "没有可撤销的删除操作"
    table, choices = _filtered_cache_updates(query, min_score, output_mode, base_model)
    return message, table, choices


def _export_cache(file_format):
    try:
        path = DB.export_records(str(file_format or "JSON").lower())
        return f"导出完成：{path}", path
    except Exception as error:
        return f"导出失败：{_safe_error(error)}", None


def _import_cache(file_value, dedupe, query="", min_score=0, output_mode="全部", base_model="全部"):
    path = getattr(file_value, "name", file_value)
    if not path:
        return "请选择 JSON 或 CSV 文件", gr.update(), gr.update()
    try:
        stats = DB.import_records(path, bool(dedupe))
        table, choices = _filtered_cache_updates(query, min_score, output_mode, base_model)
        return (
            f"导入完成：新增 {stats['inserted']} 条，跳过重复 {stats['duplicates']} 条",
            table, choices,
        )
    except Exception as error:
        return f"导入失败：{_safe_error(error)}", gr.update(), gr.update()


def _ranbooru_load(
    database_path, content_mode, rating_filter, min_source_score, source_limit,
    tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
):
    return load_ranbooru_cache(
        database_path, content_mode, rating_filter, int(min_source_score or 0), int(source_limit or 0),
        tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
    )


def _preview_ranbooru_link(
    database_path, content_mode, rating_filter, min_source_score, source_limit,
    tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
):
    try:
        _save_ranbooru_link_settings(
            database_path, content_mode, rating_filter, min_source_score, source_limit,
            tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
        )
        result = _ranbooru_load(
            database_path, content_mode, rating_filter, min_source_score, source_limit,
            tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
        )
        rows = [[
            index,
            record.get("_ranbooru_id", ""),
            "Tag" if record.get("_ranbooru_variant") == "tags" else "自然语言",
            record.get("_ranbooru_score", 0),
            record.get("_ranbooru_rating", ""),
            record["output_mode"],
            record["base_model"],
            record["prompt"],
        ] for index, record in enumerate(result["records"][:200], start=1)]
        message = (
            f"Ranbooru 缓存预览：源记录 {result['loaded_sources']}/{result['total_sources']}，"
            f"可同步 Prompt {result['mapped_records']}，有效自然语言 {result['natural_available']}。"
        )
        if result["stale_natural"]:
            message += f" 已跳过源 Tag 已变化的自然语言缓存 {result['stale_natural']} 条。"
        if result["truncated"]:
            message += " 当前受“最多读取源记录”限制，仅预览和同步前一部分。"
        if len(result["records"]) > 200:
            message += " 表格仅显示前 200 条。"
        return gr.update(value=rows), message
    except Exception as error:
        return gr.update(value=[]), f"Ranbooru 缓存预览失败：{_safe_error(error)}"


def _sync_ranbooru_link(
    database_path, content_mode, rating_filter, min_source_score, source_limit,
    tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
    query="", min_score=0, filter_output_mode="全部", filter_base_model="全部",
):
    try:
        _save_ranbooru_link_settings(
            database_path, content_mode, rating_filter, min_source_score, source_limit,
            tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
        )
        result = _ranbooru_load(
            database_path, content_mode, rating_filter, min_source_score, source_limit,
            tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
        )
        stats = DB.sync_external_prompts(result["records"])
        invalidated = DB.invalidate_external_prompts(
            "ranbooru", result["invalid_source_refs"],
            "Ranbooru 自然语言缓存已失效：源 Tag 已变化，需要重新转换；相关缓存已重置为未评分",
        )
        table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
        message = (
            f"Ranbooru 同步完成：新增 {stats['inserted']}，源内容更新 {stats['updated']}，"
            f"未变化 {stats['unchanged']}，失效评分 {invalidated}。新记录和发生变化的记录均为未评分，"
            "可在缓存库手动调整评分。"
        )
        if result["stale_natural"]:
            message += f" 跳过失效自然语言缓存 {result['stale_natural']} 条。"
        return message, table, choices
    except Exception as error:
        return f"Ranbooru 同步失败：{_safe_error(error)}", gr.update(), gr.update()


def _cancel_batch_generation(task_id=""):
    with _BATCH_CONTROL_LOCK:
        if not task_id or str(task_id) != _BATCH_ACTIVE_TASK_ID:
            return "当前会话没有正在运行的批量任务，未发送取消请求。"
        _BATCH_CANCEL.set()
    return "已请求取消。当前 HTTP 请求返回后会停止，并保存已完成结果。"


def _parse_batch_sources(source_text: str) -> tuple[list[str], dict[str, int]]:
    sources, seen = [], set()
    ignored, duplicates = 0, 0
    for line in str(source_text or "").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            ignored += 1
            continue
        if value in seen:
            duplicates += 1
        seen.add(value)
        sources.append(value)
    return sources, {"ignored": ignored, "duplicates": duplicates}


def _preview_batch_sources(source_text, skip_existing, preset, base_model):
    sources, stats = _parse_batch_sources(source_text)
    cached_sources = DB.existing_source_prompts(sources, preset, base_model) if skip_existing else set()
    cached = sum(1 for source in sources if source in cached_sources)
    rows = []
    for index, source in enumerate(sources[:200], start=1):
        state = "将跳过：已有缓存" if source in cached_sources else "等待生成"
        rows.append([index, source, "", state])
    message = (
        f"队列共 {len(sources)} 条；重复输入 {stats['duplicates']} 条（均保留为独立任务）；空行或注释 {stats['ignored']} 条；"
        f"按当前规则将跳过已有缓存 {cached} 条。"
    )
    if len(sources) > 200:
        message += " 预览仅显示前 200 条。"
    return gr.update(value=rows), message


def _batch_issue_key(item: dict[str, Any]) -> str:
    return str(item.get("key") or f"{item.get('index', '')}:{item.get('source', '')}")


def _batch_issue_views(issues, selected=None):
    records = [dict(item) for item in (issues or []) if isinstance(item, dict) and str(item.get("source") or "").strip()]
    rows = [
        [item.get("index", ""), item["source"], item.get("status", ""), item.get("reason", ""), item.get("attempts", 0)]
        for item in records
    ]
    choices = []
    for item in records:
        preview = " ".join(str(item["source"]).split())
        if len(preview) > 72:
            preview = preview[:69] + "..."
        choices.append((f"#{item.get('index', '')} · {item.get('status', '')} · {preview}", _batch_issue_key(item)))
    available = {value for _, value in choices}
    retained = [value for value in _selected_values(selected) if value in available]
    return gr.update(value=rows), gr.update(choices=choices, value=retained), records


def _select_all_batch_issues(issues):
    values = [_batch_issue_key(item) for item in (issues or []) if isinstance(item, dict) and item.get("source")]
    return gr.update(value=values)


def _clear_batch_issue_selection():
    return gr.update(value=[])


def _batch_output(status, table, cache_choices, issues, selected=None, result_rows=None):
    issue_table, issue_choices, issue_state = _batch_issue_views(issues, selected)
    return status, table, cache_choices, issue_table, issue_choices, issue_state, gr.update(value=result_rows) if result_rows is not None else gr.update()


def _batch_generate(
    source_text, skip_existing, skip_failed,
    preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
    query="", min_score=0, filter_output_mode="全部", filter_base_model="全部", existing_issues=None, task_id="",
):
    global _BATCH_ACTIVE_TASK_ID
    sources, _parse_stats = _parse_batch_sources(source_text)
    if not sources:
        yield _batch_output("请输入批量请求，每行一条。", gr.update(), gr.update(), existing_issues or [])
        return
    if not _BATCH_LOCK.acquire(blocking=False):
        yield _batch_output("已有批量任务正在运行。", gr.update(), gr.update(), existing_issues or [])
        return
    task_id = str(task_id or "")
    with _BATCH_CONTROL_LOCK:
        _BATCH_ACTIVE_TASK_ID = task_id
        _BATCH_CANCEL.clear()
    cached_sources = {
        source for source in set(sources)
        if skip_existing and DB.has_source_prompt(source, preset, base_model)
    }
    pending, inserted, duplicates, score_updates, skipped, failed, request_count = [], 0, 0, 0, 0, 0, 0
    issues = []
    result_rows = [[index, source, "", "等待处理"] for index, source in enumerate(sources[:200], start=1)]

    def set_result(index, prompt, state):
        if 1 <= index <= len(result_rows):
            result_rows[index - 1] = [index, sources[index - 1], str(prompt or ""), state]

    def flush_pending():
        nonlocal inserted, duplicates, score_updates
        if not pending:
            return
        stats = DB.save_prompts_batch(pending, dedupe=True, trust_score_metadata=True)
        inserted += stats["inserted"]
        duplicates += stats["duplicates"]
        score_updates += stats.get("updated", 0)
        pending.clear()

    try:
        for index, source in enumerate(sources, start=1):
            if _BATCH_CANCEL.is_set():
                for remaining_index, remaining_source in enumerate(sources[index - 1:], start=index):
                    set_result(remaining_index, "", "已取消")
                    issues.append({
                        "index": remaining_index, "source": remaining_source, "status": "已取消",
                        "reason": "批量任务已取消，尚未处理", "attempts": 0,
                    })
                flush_pending()
                table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
                yield _batch_output(
                    f"任务已取消：处理 {index - 1}/{len(sources)}，新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}",
                    table, choices, issues, result_rows=result_rows,
                )
                return
            if source in cached_sources:
                skipped += 1
                set_result(index, "", "已跳过：批次前已有缓存")
                issues.append({
                    "index": index, "source": source, "status": "已跳过",
                    "reason": "批次开始前已有相同输入、输出预设和目标底模的缓存", "attempts": 0,
                })
                continue
            generated, last_status = "", ""
            request_count += 1
            try:
                generated, _system, last_status = _generate(
                    source, "", preset, system_override, base_model, safety, nsfw_injection, user_instruction,
                    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
                    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
                    0, False, "", "", False, _BATCH_CANCEL,
                    _independent_batch_directive(index) + "\n" + (
                        f"这是同一批次中的独立任务第 {index}/{len(sources)} 条。"
                        f"可以优先参考{_BATCH_VARIATION_FOCI[(index - 1) % len(_BATCH_VARIATION_FOCI)]}来形成自然差异，但不要把它当作硬性模板；"
                        "避免复用同批其他结果，也不要只替换风格词或同义词来制造差异；"
                        "请根据原要求自由选择合适的变化，只返回一条 Prompt。"
                    ),
                )
            except Exception as error:
                generated, last_status = "", f"生成失败：{_safe_error(error)}"
            if not generated and _BATCH_CANCEL.is_set() and "request cancelled" in last_status.lower():
                set_result(index, "", "已取消")
                issues.append({
                    "index": index, "source": source, "status": "已取消",
                    "reason": "当前 LLM 请求已取消，结果未写入缓存", "attempts": 1,
                })
                for remaining_index, remaining_source in enumerate(sources[index:], start=index + 1):
                    set_result(remaining_index, "", "已取消")
                    issues.append({
                        "index": remaining_index, "source": remaining_source, "status": "已取消",
                        "reason": "批量任务已取消，尚未处理", "attempts": 0,
                    })
                flush_pending()
                table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
                yield _batch_output(
                    f"任务已取消：完成 {index - 1}/{len(sources)}，新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}",
                    table, choices, issues, result_rows=result_rows,
                )
                return
            if generated:
                set_result(index, generated, "生成成功")
                pending.append({
                    "prompt": generated, "output_mode": preset, "base_model": base_model, "score": 0,
                    "score_source": "unrated", "score_reason": "批量生成未评分",
                    "score_model": "", "tags": source,
                })
            else:
                failed += 1
                set_result(index, "", last_status or "生成错误")
                issues.append({
                    "index": index, "source": source, "status": "生成错误",
                    "reason": last_status or "未返回结果", "attempts": 1,
                })
                if not skip_failed and not _BATCH_CANCEL.is_set():
                    for remaining_index, remaining_source in enumerate(sources[index:], start=index + 1):
                        set_result(remaining_index, "", "未处理")
                        issues.append({
                            "index": remaining_index, "source": remaining_source, "status": "未处理",
                            "reason": "前一项单次请求失败，批量任务已停止", "attempts": 0,
                        })
                    flush_pending()
                    table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
                    yield _batch_output(
                        f"批量任务因错误停止：处理 {index}/{len(sources)}，新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}",
                        table, choices, issues, result_rows=result_rows,
                    )
                    return
            if len(pending) >= 10 or index == len(sources):
                flush_pending()
                table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
                yield _batch_output(
                    f"进度 {index}/{len(sources)}：LLM 请求 {request_count}，新增 {inserted}，重复 {duplicates}，评分更新 {score_updates}，跳过 {skipped}，失败 {failed}" + (f"；最近状态：{last_status}" if last_status and not generated else ""),
                    table, choices, issues, result_rows=result_rows,
                )
        table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
        yield _batch_output(
            f"批量任务完成：LLM 请求 {request_count}，新增 {inserted}，重复 {duplicates}，评分更新 {score_updates}，跳过 {skipped}，失败 {failed}；问题汇总 {len(issues)} 条",
            table, choices, issues, result_rows=result_rows,
        )
    finally:
        try:
            flush_pending()
        finally:
            with _BATCH_CONTROL_LOCK:
                if _BATCH_ACTIVE_TASK_ID == task_id:
                    _BATCH_ACTIVE_TASK_ID = ""
                    _BATCH_CANCEL.clear()
            _BATCH_LOCK.release()


def _retry_batch_issues(
    selected_sources, issue_records, skip_failed,
    preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
    query="", min_score=0, filter_output_mode="全部", filter_base_model="全部", task_id="",
):
    issues = [dict(item) for item in (issue_records or []) if isinstance(item, dict) and item.get("source")]
    requested = set(_selected_values(selected_sources))
    selected = [
        item for item in issues
        if _batch_issue_key(item) in requested or str(item["source"]) in requested
    ]
    if not selected:
        yield _batch_output("请先勾选需要手动重试的错误或跳过项。", gr.update(), gr.update(), issues, selected_sources)
        return

    selected_keys = {_batch_issue_key(item) for item in selected}
    selected_indices = [item.get("index", "") for item in selected]
    remaining = [item for item in issues if _batch_issue_key(item) not in selected_keys]
    source_text = "\n".join(str(item["source"]) for item in selected)
    generator = _batch_generate(
        source_text, False, skip_failed,
        preset, system_override, base_model, safety, nsfw_injection, user_instruction,
        provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
        remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
        query, min_score, filter_output_mode, filter_base_model, selected, task_id,
    )
    for status, table, cache_choices, _issue_table, _issue_choices, retry_issues, result_rows in generator:
        remapped = []
        for item in retry_issues:
            updated = dict(item)
            try:
                retry_position = int(updated.get("index") or 0) - 1
            except (TypeError, ValueError):
                retry_position = -1
            if 0 <= retry_position < len(selected_indices):
                updated["index"] = selected_indices[retry_position]
            remapped.append(updated)
        combined = sorted([*remaining, *remapped], key=lambda item: int(item.get("index") or 0))
        retained_selection = selected_sources if status == "已有批量任务正在运行。" else None
        remapped_rows = []
        for row in result_rows.get("value", []) if isinstance(result_rows, dict) else []:
            updated_row = list(row)
            try:
                retry_position = int(updated_row[0]) - 1
            except (TypeError, ValueError, IndexError):
                retry_position = -1
            if 0 <= retry_position < len(selected_indices):
                updated_row[0] = selected_indices[retry_position]
            remapped_rows.append(updated_row)
        yield _batch_output(
            f"手动重试：{status}", table, cache_choices, combined, retained_selection,
            result_rows=remapped_rows,
        )


def _index_wildcards(path):
    try:
        files, terms = DB.index_wildcards(path or DEFAULT_WILDCARDS)
        return f"索引完成：更新 {files} 个文件，共 {terms} 个词条", gr.update(value=DB.wildcard_matches(""))
    except ValueError as error:
        return str(error), gr.update()


def _search_wildcards(query):
    return gr.update(value=DB.wildcard_matches(query))


def _save_llm_settings(provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature):
    provider = str(provider or DEFAULT_LLM_SETTINGS["provider"])
    if provider not in PROVIDER_PROFILES:
        return "保存失败：不支持的 Provider。", gr.update(), gr.update()
    try:
        endpoint = validate_endpoint(endpoint)
        model = str(model or "").strip()
        if not model:
            raise ValueError("LLM model ID is required")
        settings = {
            "endpoint": endpoint,
            "model": model,
            "temperature": max(0.0, min(float(temperature), 2.0)),
            "timeout": max(5, min(int(timeout), 600)),
            "max_tokens": max(0, min(int(max_tokens), 262144)),
            "send_temperature": bool(send_temperature),
        }
    except (TypeError, ValueError) as error:
        return f"保存失败：{_safe_error(error)}", gr.update(), gr.update()
    store = _connection_store()
    providers = dict(store.get("providers", {}))
    providers[provider] = settings
    DB.set_setting("llm_connections_v2", {"version": 2, "active_provider": provider, "providers": providers})
    key_saved = CREDENTIALS.save(provider, endpoint, api_key)
    key_available = key_saved or CREDENTIALS.has_matching(provider, endpoint)
    message = f"{provider} 设置已保存。模型 ID：{model}。URL、模型 ID 和生成参数下次会自动恢复。"
    if key_available:
        message += " API Key 已按 Provider 与 URL 保存在服务端，下次可留空。"
    elif get_provider_profile(provider).get("requires_api_key"):
        message += " 尚未保存 API Key，调用前必须填写。"
    else:
        message += " 当前未保存 API Key。"
    return message, gr.update(value=endpoint), gr.update(value=model)


def _clear_llm_credentials(provider, endpoint):
    try:
        cleared = CREDENTIALS.clear(provider, validate_endpoint(endpoint))
    except ValueError as error:
        return f"清除失败：{_safe_error(error)}"
    return "已清除当前 Provider 与 URL 对应的 API Key。" if cleared else "当前连接没有已保存的 API Key。"


def _finalize_generated_prompt(
    result, preset, safety, remove_bad=True, remove_terms="", shuffle=False, spaces=False,
    max_tags=0, structured_mode="Plain Prompt", region_count=1,
):
    result = str(result or "").strip()
    if safety == "SFW" and not is_sfw_output(result):
        raise ValueError("SFW 校验拦截了成人内容。请修改要求，或明确切换为 NSFW 模式。")
    if preset in {"Danbooru Tags", "NoobAI Tags", "Anima Tags"}:
        result = process_tags(result, bool(remove_bad), remove_terms, bool(shuffle), bool(spaces), int(max_tags or 0))
    if structured_mode != "Plain Prompt":
        result = regional_format(result, structured_mode, int(region_count or 1))
    result = str(result or "").strip()
    if not result:
        raise ValueError("LLM 输出在标签清理或格式化后未返回可用 Prompt。")
    return result


def _processed_kind_for_preset(preset: str) -> str:
    if preset in {"Natural Language", "Krea 2 Natural"}:
        return "natural"
    if preset == "Danbooru + Natural":
        return "mixed"
    return "tags"


def _recommended_base_model_for_preset(preset: str):
    return PRESET_BASE_MODEL_DEFAULTS.get(str(preset), "Auto / checkpoint default")


def _static_prompt_reference(source: str, related_limit: int = 20, sample_limit: int = 20) -> list[str]:
    """Provide related plus fresh random lexicon terms for every creative request."""
    related = DB.wildcard_matches(str(source or ""), related_limit)
    samples = DB.wildcard_samples(sample_limit, exclude=related)
    result = []
    seen = set()
    for term in [*related, *samples]:
        normalized = str(term or "").strip()
        if normalized and normalized.casefold() not in seen:
            seen.add(normalized.casefold())
            result.append(normalized)
    return result


def _generate(
    request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
    save_score, cache_result, source_kind="", source_ref="", cache_unrated=False,
    cancel_event=None,
    batch_directive="",
):
    source = str(source_tags or request or "").strip()
    if not source:
        return "", "", "请输入创作要求或源 Danbooru 标签。"
    examples = []
    static_tags = _static_prompt_reference(source)
    effective_batch_directive = str(batch_directive or "").strip()
    if effective_batch_directive:
        recent_outputs = _recent_diverse_outputs(source)
        exclusion_terms = _diversity_exclusion_terms(source)
        if exclusion_terms:
            effective_batch_directive += (
                "\nDIVERSITY LEDGER: these concepts have appeared repeatedly in earlier outputs. "
                "Prefer fresh alternatives and do not reuse them unless the source explicitly fixes them: "
                + ", ".join(exclusion_terms)
                + "."
            )
        if recent_outputs:
            exclusions = "\n".join(f"- {item[:520]}" for item in recent_outputs)
            effective_batch_directive += (
                "\nRecent outputs are exclusion references only. Do not reuse their scene structure, action, "
                "camera arrangement, prop combination, or distinctive decorative elements:\n" + exclusions
            )
    system = build_system_prompt(
        preset, base_model, safety, nsfw_injection, user_instruction, examples,
        static_tags, system_override, effective_batch_directive,
    )
    try:
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
        request_temperature = float(temperature or 0.35)
        if effective_batch_directive:
            request_temperature = max(request_temperature, _MIN_INDEPENDENT_BATCH_TEMPERATURE)
        diversity_retry = False
        for attempt in range(_MAX_DIVERSITY_RETRIES + 1):
            attempt_system = system
            if attempt:
                diversity_retry = True
                attempt_system = build_system_prompt(
                    preset, base_model, safety, nsfw_injection, user_instruction, examples,
                    static_tags, system_override,
                    effective_batch_directive + "\nDIVERSITY RETRY: favor a fresh compatible interpretation of the scene, action, environment, camera, time/weather, or prop relationship; keep the model free to choose and return one complete single-image prompt.",
                )
            result = call_llm(
                provider, endpoint, model, resolved_key, attempt_system, build_user_message(source),
                min(2.0, request_temperature + 0.15 * attempt), int(timeout or 90), int(max_tokens or 0),
                bool(send_temperature), cancel_event=cancel_event,
            )
            candidate = _finalize_generated_prompt(
                result, preset, safety, remove_bad, remove_terms, shuffle, spaces,
                max_tags, structured_mode, region_count,
            )
            accepted = not effective_batch_directive or _remember_diverse_output(source, candidate)
            if accepted or attempt == _MAX_DIVERSITY_RETRIES:
                if effective_batch_directive and not accepted:
                    _remember_diverse_output(source, candidate, force=True)
                result = candidate
                system = attempt_system
                break
    except LLMRequestError as error:
        if str(error) == "LLM request cancelled":
            return "", system, "已取消"
        return "", system, f"生成失败：{_safe_error(error)}"
    except Exception as error:
        return "", system, f"生成失败：{_safe_error(error)}"
    try:
        result = str(result or "").strip()
    except ValueError as error:
        return "", system, f"生成失败：{error}"
    if cache_result:
        if cache_unrated:
            score, score_source = 0.0, "unrated"
            score_reason = "自动工作流未评分"
        else:
            score, score_source = float(save_score or 0), "manual"
            score_reason = "生成时手动评分"
        effective_source_ref = source_ref
        if source_kind == "auto_loop" and source_ref:
            result_hash = hashlib.sha256(result.encode("utf-8")).hexdigest()[:20]
            effective_source_ref = f"{source_ref}:{result_hash}"
        DB.save_prompt(
            result, "", preset, base_model, score, source,
            score_source=score_source, score_reason=score_reason, score_model="",
            source_kind=source_kind or None, source_ref=effective_source_ref or None,
            dedupe=True,
        )
    status = "生成完成" + ("，结果已缓存" if cache_result else "")
    return result, system, status


def _generate_auto_loop(
    request, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
    few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags,
    structured_mode, region_count, cache_result=False,
):
    _AUTO_LOOP_CANCEL.clear()
    if cache_result:
        identity = {
            "request": str(request or "").strip(), "preset": preset, "system_override": system_override,
            "base_model": base_model, "safety": safety, "nsfw_injection": nsfw_injection,
            "user_instruction": user_instruction, "provider": provider, "endpoint": endpoint, "model": model,
            "temperature": temperature, "max_tokens": max_tokens, "send_temperature": bool(send_temperature),
            "few_shot_count": few_shot_count, "rag_min_score": rag_min_score, "remove_bad": bool(remove_bad),
            "remove_terms": remove_terms, "shuffle": bool(shuffle), "spaces": bool(spaces),
            "max_tags": max_tags, "structured_mode": structured_mode, "region_count": region_count,
        }
        fingerprint = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
        source_ref = f"auto_loop:{fingerprint}"
        return _generate(
            request, "", preset, system_override, base_model, safety, nsfw_injection, user_instruction,
            provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
            few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags,
            structured_mode, region_count, 0, True, "auto_loop", source_ref, True, _AUTO_LOOP_CANCEL,
            _independent_creative_directive(),
        )
    return _generate(
        request, "", preset, system_override, base_model, safety, nsfw_injection, user_instruction,
        provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
            few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags,
            structured_mode, region_count, 0, False, "", "", False, _AUTO_LOOP_CANCEL,
            _independent_creative_directive(),
    )


def _expand_or_polish(
    source, action, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
    remove_bad=True, remove_terms="", shuffle=False, spaces=False, max_tags=0,
    structured_mode="Plain Prompt", region_count=1,
    cancel_event=None,
    batch_directive="", previous_outputs=None,
):
    instructions = {
        "Convert": (
            "Convert the source prompt into exactly the selected output profile and target-model format. "
            "Preserve every explicit visual fact that is compatible with the target format, translate Danbooru tags "
            "into fluent prose when the selected profile is natural language, and never return the original tag dump "
            "when the selected profile forbids tags."
        ),
        "Expand": "Expand this while keeping all explicit facts and the requested output format.",
        "Polish": "Polish this for clarity, visual specificity, and model compatibility without adding unsupported facts.",
    }
    action_name = str(action)
    instruction = instructions.get(action_name, instructions["Convert"])
    static_tags = _static_prompt_reference(source)
    if action_name == "Polish":
        instruction = "Enhance the source prompt with concrete visible detail while preserving its subject and constraints."
    directive = str(batch_directive or "").strip()
    previous = [str(item).strip() for item in (previous_outputs or []) if str(item).strip()]
    if directive:
        exclusion_terms = _diversity_exclusion_terms_from_outputs(previous, source)
        if exclusion_terms:
            directive += (
                "\nDIVERSITY LEDGER: concepts already repeated in this batch; prefer fresh alternatives and avoid reusing them "
                "unless the source explicitly fixes them: " + ", ".join(exclusion_terms) + "."
            )
        if previous:
            previous_summary = "\n".join(f"- {item[:520]}" for item in previous[-_DIVERSITY_REFERENCE_LIMIT:])
            directive += (
                "\nPrevious outputs are supplied only as exclusion constraints; do not copy their wording, scene structure, action, "
                "or prop combination:\n" + previous_summary
            )
    if directive:
        instruction += (
            " Preserve the core subject identity and explicit user constraints. You may freely reinterpret the scene, action, props, "
            "spatial layout, camera, time, weather, and lighting to create natural variation; do not force any particular combination."
        )
    def build_transform_system(active_directive: str) -> str:
        if action_name != "Polish":
            return build_system_prompt(
                preset, base_model, safety, nsfw_injection, f"{user_instruction}\n{instruction}", static_tags,
                system_override=system_override, batch_directive=active_directive,
            )
        sections = [KREA_ANIMA_POLISH_ROLE]
        if str(system_override or "").strip():
            sections.append("Additional system requirements:\n" + str(system_override).strip())
        if str(user_instruction or "").strip():
            sections.append("Additional user requirements:\n" + str(user_instruction).strip())
        sections.append(instruction)
        if static_tags:
            sections.append(
                "STATIC VOCABULARY REFERENCE (use as vocabulary inspiration; select only compatible visible terms, "
                "never dump the list):\n" + ", ".join(static_tags[:60])
            )
        if active_directive:
            sections.append(active_directive)
        return "\n\n".join(sections)

    system = build_transform_system(directive)
    try:
        request_temperature = float(temperature or 0.35)
    except (TypeError, ValueError):
        request_temperature = 0.35
    if directive:
        request_temperature = max(request_temperature, _MIN_INDEPENDENT_BATCH_TEMPERATURE)
    try:
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
        result = ""
        for attempt in range(_MAX_DIVERSITY_RETRIES + 1):
            attempt_system = system
            if attempt and directive:
                retry_directive = (
                    f"{directive}\nDIVERSITY RETRY {attempt}: the earlier candidate was too similar to an existing item. "
                    "Favor a different compatible combination of setting, action, prop relationship, spatial layout, camera angle, "
                    "time/weather, or narrative event, while leaving the model free to choose; return exactly one complete single-image prompt."
                )
                attempt_system = build_transform_system(retry_directive)
            result = call_llm(
                provider, endpoint, model, resolved_key, attempt_system,
                build_user_message(source), min(2.0, request_temperature + 0.15 * attempt),
                int(timeout or 90), int(max_tokens or 0), bool(send_temperature), cancel_event=cancel_event,
            )
            finalize_preset = "Krea 2 Natural" if action_name == "Polish" else preset
            result = _finalize_generated_prompt(
                result, finalize_preset, safety, remove_bad, remove_terms, shuffle, spaces,
                max_tags, structured_mode, region_count,
            )
            if not directive or not any(_is_diversity_duplicate(result, item, source) for item in previous):
                break
        return result, "LLM 提示词处理完成"
    except Exception as error:
        return "", f"处理失败：{_safe_error(error)}"


PNG_BATCH_SCHEMA = "prompt_batch.v1"
PNG_BATCH_MAX_PROMPT_LENGTH = 12000


def _png_batch_json(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _generic_prompt_records(payload):
    """Convert common prompt-export JSON shapes into the internal batch schema."""
    if isinstance(payload, list):
        source_records = payload
    elif isinstance(payload, dict):
        source_records = None
        for key in ("prompts", "items", "records", "results", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                source_records = candidate
                break
        if source_records is None:
            string_values = [(key, value) for key, value in payload.items() if isinstance(value, str) and value.strip()]
            object_values = [(key, value) for key, value in payload.items() if isinstance(value, dict)]
            if object_values:
                source_records = [
                    {**value, "record_id": str(value.get("record_id") or value.get("id") or key)}
                    for key, value in object_values
                ]
            else:
                source_records = [{"record_id": str(key), "prompt": value} for key, value in string_values]
            if not source_records:
                source_records = [payload]
    else:
        raise ValueError("JSON 顶层必须是数组或对象")

    records = []
    for position, source in enumerate(source_records, 1):
        if isinstance(source, str):
            records.append({"record_id": str(position), "prompt": {"positive": source}})
            continue
        if not isinstance(source, dict):
            raise ValueError(f"第 {position} 条 Prompt 必须是字符串或对象")
        nested = source.get("prompt")
        prompt = nested if isinstance(nested, dict) else {}
        positive = ""
        for key in ("positive", "text", "content", "prompt", "input", "description"):
            value = prompt.get(key) if key in prompt else source.get(key)
            if isinstance(value, str) and value.strip():
                positive = value.strip()
                break
        if not positive:
            raise ValueError(f"第 {position} 条记录没有可用的 Prompt 字段")
        image = source.get("image") if isinstance(source.get("image"), dict) else {}
        record = {
            "record_id": str(source.get("record_id") or source.get("id") or position),
            "image": {
                "filename": str(image.get("filename") or source.get("filename") or f"prompt-{position}.png"),
            },
            "prompt": {"positive": positive},
        }
        processed = source.get("processed")
        if processed is None and isinstance(nested, dict):
            processed = nested.get("processed")
        if isinstance(processed, str) and processed.strip():
            record["prompt"]["processed"] = processed.strip()
        records.append(record)
    return {
        "schema_version": PNG_BATCH_SCHEMA,
        "producer": {"name": "Generic JSON import"},
        "records": records,
    }


def _normalize_png_batch_payload(payload):
    if isinstance(payload, str):
        payload = json.loads(payload or "{}")
    if not (isinstance(payload, dict) and payload.get("schema_version") == PNG_BATCH_SCHEMA):
        payload = _generic_prompt_records(payload)
    if not isinstance(payload, dict) or payload.get("schema_version") != PNG_BATCH_SCHEMA:
        raise ValueError("不支持的 Prompt JSON：需要数组、Prompt 对象或 prompt_batch.v1 records")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("PNG batch records must be a list")
    producer = payload.get("producer") or {}
    if not isinstance(producer, dict):
        raise ValueError("producer 必须是对象")
    producer_name = str(producer.get("name") or "LLM Prompt Studio")
    normalized = []
    record_ids = set()
    for position, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise ValueError(f"record {position} must be an object")
        image, prompt = record.get("image") or {}, record.get("prompt") or {}
        if not isinstance(image, dict) or not isinstance(prompt, dict):
            raise ValueError(f"第 {position} 条记录缺少 image 或 prompt")
        positive = str(prompt.get("positive") or "").strip()
        if not positive:
            raise ValueError(f"第 {position} 条记录缺少正向 Prompt")
        if len(positive) > PNG_BATCH_MAX_PROMPT_LENGTH:
            raise ValueError(f"record {position} prompt is too long")
        filename = Path(str(image.get("filename") or "")).name or f"record-{position}.png"
        if len(filename) > 255:
            raise ValueError(f"第 {position} 条图片名过长")
        record_id = str(record.get("record_id") or "").strip()
        sha256 = str(image.get("sha256") or "")
        if len(record_id) > 256 or len(sha256) > 128:
            raise ValueError(f"第 {position} 条图片标识过长")
        if sha256 and (len(sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in sha256)):
            raise ValueError(f"第 {position} 条 sha256 必须是 64 位十六进制")
        if not record_id:
            identity = f"{producer_name}\x1f{sha256}\x1f{filename}\x1f{positive}"
            record_id = f"generated-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
        if record_id in record_ids:
            raise ValueError(f"第 {position} 条 record_id 重复: {record_id}")
        record_ids.add(record_id)
        item = {"record_id": record_id, "index": position,
                "image": {"filename": filename, "sha256": sha256},
                "prompt": {"positive": positive}}
        source_identity = str(record.get("source_identity") or "").strip()
        if source_identity:
            if len(source_identity) > 512:
                raise ValueError(f"第 {position} 条 source_identity 过长")
            item["source_identity"] = source_identity
        for field in ("source_url", "preview_url"):
            if image.get(field):
                item["image"][field] = str(image[field])
        if "natural" in prompt:
            natural = str(prompt["natural"] or "")
            if len(natural) > PNG_BATCH_MAX_PROMPT_LENGTH:
                raise ValueError(f"第 {position} 条自然语言 Prompt 过长")
            item["prompt"]["natural"] = natural
        if "processed" in prompt:
            processed = str(prompt["processed"] or "")
            if len(processed) > PNG_BATCH_MAX_PROMPT_LENGTH:
                raise ValueError(f"第 {position} 条处理结果过长")
            item["prompt"]["processed"] = processed
        for field in ("processed_kind", "output_kind", "processed_preset", "processed_base_model"):
            if field in prompt:
                kind = str(prompt[field] or "").strip()
                if len(kind) > 64:
                    raise ValueError(f"第 {position} 条 {field} 过长")
                if kind:
                    item["prompt"][field] = kind
        if record.get("status"):
            item["status"] = str(record["status"])
        if record.get("error"):
            item["error"] = str(record["error"])
        if record.get("appended") is True:
            item["appended"] = True
        if "booru" in record:
            item["booru"] = record["booru"]
        for field in ("rating", "ranbooru_id", "database_key", "post_id"):
            if record.get(field) is not None and str(record.get(field)).strip():
                item[field] = str(record[field]).strip()[:256]
        if record.get("source_score") is not None:
            try:
                item["source_score"] = int(float(record["source_score"]))
            except (TypeError, ValueError, OverflowError):
                item["source_score"] = 0
        normalized.append(item)
    return {"schema_version": PNG_BATCH_SCHEMA, "producer": {"name": producer_name}, "records": normalized}


def _png_batch_export_payload(records, producer="LLM Prompt Studio"):
    return _normalize_png_batch_payload({"schema_version": PNG_BATCH_SCHEMA, "producer": {"name": producer}, "records": records})


def _png_batch_process_records(records, action, transform):
    results = []
    for record in records:
        row = dict(record)
        if str(row.get("prompt", {}).get("processed") or "").strip():
            results.append(row)
            continue
        try:
            source = row.get("prompt", {}).get("positive", "")
            processed = str(transform(source, action) or "")
            if not processed:
                raise ValueError("empty LLM result")
            row["prompt"] = {**row.get("prompt", {}), "processed": processed}
            row["status"], row["error"] = "completed", ""
        except Exception as error:
            row["status"], row["error"] = "failed", str(error)
        results.append(row)
    return results


def _png_batch_load(file_path):
    try:
        path = Path(file_path)
        data = _normalize_png_batch_payload(path.read_text(encoding="utf-8"))
        selected, current = _png_batch_current(data, 1)
        return _png_batch_json(data), _png_batch_table(data), selected, current, f"已导入 {len(data['records'])} 条逐图 Prompt。"
    except Exception as error:
        return gr.update(), [], 1, "", f"导入失败：{_safe_error(error)}"


def _png_batch_table(payload):
    try:
        data = _normalize_png_batch_payload(payload or {})
    except Exception:
        return []
    return [
        [
            record["index"],
            record["image"]["filename"],
            record["prompt"]["positive"],
            "已追加" if record.get("appended") else record.get("status", "已完成" if record["prompt"].get("processed") else "等待处理"),
            record["prompt"].get("processed", ""),
            record.get("error", ""),
        ]
        for record in data["records"]
    ]


def _png_batch_current(payload, selection):
    try:
        data = _normalize_png_batch_payload(payload or {})
    except Exception:
        return 1, ""
    records = data["records"]
    if not records:
        return 1, ""
    if int(selection or 0) < 1:
        return 0, ""
    selected = max(1, min(int(selection or 1), len(records)))
    prompt = records[selected - 1]["prompt"]
    return selected, prompt.get("processed", "")


def _png_batch_refresh(payload, selection=1):
    try:
        data = _normalize_png_batch_payload(payload or {})
    except Exception as error:
        return [], 1, "", f"批次 JSON 无效：{_safe_error(error)}"
    selected, current = _png_batch_current(data, selection)
    table = _png_batch_table(data)
    return table, selected, current, f"已载入 {len(table)} 条逐图 Prompt。"


def _png_batch_move(payload, selection, offset):
    selected, current = _png_batch_current(payload, int(selection or 1) + int(offset))
    return selected, current


def _cancel_png_batch():
    _PNG_BATCH_CANCEL.set()
    return "已请求取消；当前 LLM 请求返回后停止，已完成结果会保留。"


def _inline_json_batch_run(payload, action, preset, base_model, variation_mode="independent"):
    """Run JSON Prompt processing from the Forge txt2img inline panel."""
    workflow = _workflow_settings()
    connection = _connection_settings()
    selected_preset = preset if preset in PRESETS else workflow["preset"]
    selected_base_model = base_model if base_model in BASE_MODEL_GUIDANCE else workflow["base_model"]
    yield from _png_batch_run(
        payload, action,
        selected_preset, workflow["system_override"], selected_base_model, workflow["safety"],
        workflow["nsfw_injection"], workflow["user_instruction"],
        connection["provider"], connection["endpoint"], connection["model"], "",
        connection["temperature"], connection["timeout"], connection["max_tokens"], connection["send_temperature"],
        workflow["remove_bad"], workflow["remove_terms"], workflow["shuffle"], workflow["spaces"],
        workflow["max_tags"], workflow["structured_mode"], workflow["region_count"],
        variation_mode,
    )


def _cancel_auto_loop_generation():
    _AUTO_LOOP_CANCEL.set()
    return "已请求取消当前 LLM 请求；已返回的结果不会写入队列。"


def _cancel_inline_generation(slot):
    event = _INLINE_CANCEL_EVENTS.get(str(slot or ""))
    if event is not None:
        event.set()
    return "已请求停止；LLM 等待已中断，迟到响应不会写入 Prompt。"


def _png_batch_run(
    payload, action, preset, system_override, base_model, safety, nsfw_injection,
    user_instruction, provider, endpoint, model, api_key, temperature, timeout,
    max_tokens, send_temperature, remove_bad=True, remove_terms="", shuffle=False,
    spaces=False, max_tags=0, structured_mode="Plain Prompt", region_count=1,
    variation_mode="faithful",
):
    try:
        data = _normalize_png_batch_payload(payload or {})
    except Exception as error:
        yield payload, [], 1, "", f"处理失败：{_safe_error(error)}"
        return
    if not data["records"]:
        yield _png_batch_json(data), [], 1, "", "批次为空，请先导入逐图 Prompt。"
        return
    _PNG_BATCH_CANCEL.clear()
    records = [dict(record) for record in data["records"]]
    independent = str(variation_mode or "faithful") == "independent"
    progress_interval = max(1, (len(records) + 99) // 100)
    skipped_existing = reused = 0
    outcomes = {}
    previous_outputs = []
    try:
        for position, record in enumerate(records, 1):
            if _PNG_BATCH_CANCEL.is_set():
                for pending in records[position - 1:]:
                    if not str(pending.get("prompt", {}).get("processed") or "").strip():
                        pending["status"] = "已取消"
                        pending["error"] = "尚未处理"
                break
            record_prompt = record.get("prompt", {})
            has_processed = bool(str(record_prompt.get("processed") or "").strip())
            same_conversion_target = (
                record_prompt.get("processed_preset") == preset
                and record_prompt.get("processed_base_model") == base_model
            )
            if not independent and has_processed and (action != "Convert" or same_conversion_target):
                skipped_existing += 1
                if not record.get("status"):
                    record["status"] = "已完成"
                if position % progress_interval == 0 or position == len(records):
                    yield gr.update(), gr.update(), gr.update(), gr.update(), f"处理中 {position}/{len(records)}"
                continue
            source = record["prompt"]["positive"]
            outcome_key = source.strip()
            if not independent and outcome_key in outcomes:
                processed, llm_status = outcomes[outcome_key]
                reused += 1
            else:
                batch_directive = _independent_batch_directive(position) if independent else ""
                processed, llm_status = _expand_or_polish(
                    source, action, preset, system_override, base_model, safety,
                    nsfw_injection, user_instruction, provider, endpoint, model,
                    api_key, temperature, timeout, max_tokens, send_temperature,
                    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
                    _PNG_BATCH_CANCEL, batch_directive, previous_outputs,
                )
                if not independent and not _PNG_BATCH_CANCEL.is_set():
                    outcomes[outcome_key] = (processed, llm_status)
            if not processed and _PNG_BATCH_CANCEL.is_set() and "request cancelled" in str(llm_status or "").lower():
                record["status"], record["error"] = "已取消", "当前 LLM 请求已取消"
                for pending in records[position:]:
                    if not str(pending.get("prompt", {}).get("processed") or "").strip():
                        pending["status"], pending["error"] = "已取消", "尚未处理"
                break
            if processed:
                if independent:
                    previous_outputs.append(processed)
                processed_kind = "mixed" if action == "Polish" else _processed_kind_for_preset(preset)
                record["prompt"] = {
                    **record["prompt"], "processed": processed,
                    "processed_kind": processed_kind, "output_kind": processed_kind,
                    "processed_preset": preset, "processed_base_model": base_model,
                }
                record["status"], record["error"] = "已完成", ""
            else:
                record["status"], record["error"] = "失败", llm_status or "LLM 未返回结果"
            if position % progress_interval == 0 or position == len(records):
                yield gr.update(), gr.update(), gr.update(), gr.update(), f"处理中 {position}/{len(records)}"

        result = {"schema_version": PNG_BATCH_SCHEMA, "producer": {"name": "LLM Prompt Studio"}, "records": records}
        selected, current = _png_batch_current(result, 1)
        completed = sum(1 for record in records if record.get("status") == "已完成")
        failed = sum(1 for record in records if record.get("status") == "失败")
        cancelled = sum(1 for record in records if record.get("status") == "已取消")
        yield _png_batch_json(result), _png_batch_table(result), selected, current, (
            f"批处理结束：目标 {preset} / {base_model}；完成 {completed}，相同 Prompt 复用 {reused}，"
            f"已有结果跳过 {skipped_existing}（同目标），失败 {failed}，取消 {cancelled}。"
        )
    finally:
        _PNG_BATCH_CANCEL.clear()


def _png_batch_advance_after_append(payload, selection, succeeded):
    if not succeeded:
        selected, current = _png_batch_current(payload, selection)
        return payload, selected, current, "当前结果未写入。"
    data = _normalize_png_batch_payload(payload or {})
    selected = int(selection or 0)
    if selected < 1 or selected > len(data["records"]):
        return _png_batch_json(data), 0, "", "没有待追加的结果。"
    data["records"][selected - 1]["appended"] = True
    if selected == len(data["records"]):
        return _png_batch_json(data), 0, "", "全部逐图结果已追加完成。"
    next_selection, current = _png_batch_current(data, selected + 1)
    return _png_batch_json(data), next_selection, current, f"已追加第 {selected} 条，当前为第 {next_selection} 条。"


def _png_batch_export_file(payload):
    data = _normalize_png_batch_payload(payload or {})
    export_dir = Path(__file__).resolve().parents[1] / "user" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"prompt_batch_{uuid.uuid4().hex}.json"
    content = _png_batch_json(data)
    path.write_text(content, encoding="utf-8")
    return str(path)


def _ranbooru_handoff_to_png_batch(handoff_id):
    """Convert one Ranbooru handoff into the shared prompt_batch.v1 shape."""
    try:
        handoff_key = int(str(handoff_id or "").strip())
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("请选择有效的 Ranbooru 交接记录") from error
    record = DB.get_handoff(handoff_key)
    if not record or record.get("source_kind") != "ranbooru":
        raise ValueError("Ranbooru 交接记录不存在，或已被清理")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Ranbooru 交接记录的 JSON 已损坏")
    normalized = _normalize_ranbooru_handoff(payload)
    selected = str(normalized.get("selected_prompt") or "").strip()
    if not selected:
        selected = str(normalized.get("natural_prompt") or normalized.get("tags_prompt") or "").strip()
    if not selected:
        raise ValueError("Ranbooru 交接记录没有可用 Prompt")
    source_identity = _handoff_source_ref(normalized)
    record_id = f"ranbooru-{normalized['database_key']}-{normalized['ranbooru_id']}"
    prompt = {"positive": selected}
    natural = str(normalized.get("natural_prompt") or "").strip()
    if natural:
        prompt["natural"] = natural
    return _normalize_png_batch_payload({
        "schema_version": PNG_BATCH_SCHEMA,
        "producer": {"name": "Ranbooru"},
        "records": [{
            "record_id": record_id,
            "image": {"filename": f"{record_id}.png"},
            "prompt": prompt,
            "source_identity": source_identity,
            "booru": normalized.get("booru", ""),
            "rating": normalized.get("rating", ""),
            "source_score": normalized.get("source_score", 0),
            "ranbooru_id": normalized.get("ranbooru_id", ""),
            "database_key": normalized.get("database_key", ""),
            "post_id": normalized.get("post_id", ""),
        }],
    })


def _test_connection(provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature):
    try:
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
        output = call_llm(
            provider, endpoint, model, resolved_key, "Reply exactly: READY", "Connection test",
            float(temperature or 0), int(timeout or 30), max(16, min(int(max_tokens or 64), 64)), bool(send_temperature),
        )
        return f"{provider} 连接成功：{output[:160]}"
    except Exception as error:
        return f"连接失败：{_safe_error(error)}"


def _inline_generate(
    request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags,
    structured_mode, region_count, save_score, cache_result,
    slot="",
):
    saved_workflow = _workflow_settings()
    shared_updates = {"preset": preset, "base_model": base_model, "safety": safety}
    if any(saved_workflow[key] != value for key, value in shared_updates.items()):
        _save_workflow_values(shared_updates)
    cancel_event = _INLINE_CANCEL_EVENTS.get(str(slot or ""))
    if cancel_event is not None:
        cancel_event.clear()
    connection = _connection_settings()
    generated, system, status = _generate(
        request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
        connection["provider"], connection["endpoint"], connection["model"], "",
        connection["temperature"], connection["timeout"], connection["max_tokens"], connection["send_temperature"],
        few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags,
        structured_mode, region_count, save_score, cache_result, "", "", False, cancel_event,
        _independent_creative_directive(),
    )
    return generated, system, status, generated if generated else gr.update()


def _inline_cached_prompt(cursor=0):
    try:
        records = [record for record in DB.list_prompts(limit=1000) if str(record.get("prompt") or "").strip()]
        if not records:
            return "", "缓存为空，请先生成或导入 Prompt。", int(cursor or 0)
        position = int(cursor or 0) % len(records)
        record = records[position]
        next_cursor = position + 1
        return str(record["prompt"]).strip(), f"已取出缓存第 {position + 1}/{len(records)} 条；下一次将继续向后读取。", next_cursor
    except Exception as error:
        return "", f"读取缓存失败：{_safe_error(error)}", int(cursor or 0)


def _unwrap_component(component):
    return component.component if hasattr(component, "component") else component


def capture_prompt_component(component, **kwargs):
    """Capture native prompt textboxes so the inline panel can write to them."""
    elem_id = kwargs.get("elem_id")
    if elem_id in {"txt2img_prompt", "img2img_prompt"}:
        with _INLINE_LOCK:
            _PROMPT_TARGETS[elem_id.split("_", 1)[0]] = _unwrap_component(component)


def inject_inline_before_negative(component, **kwargs):
    """Render after the prompt/Ranbooru area and immediately before negative prompt."""
    elem_id = kwargs.get("elem_id")
    if elem_id not in {"txt2img_neg_prompt_row", "img2img_neg_prompt_row"}:
        return
    slot = elem_id.split("_", 1)[0]
    with _INLINE_LOCK:
        if slot in _INLINE_SLOTS:
            return
        prompt_target = _PROMPT_TARGETS.get(slot)
        if prompt_target is None:
            LOGGER.warning("跳过内嵌面板：未找到 %s 正向提示词组件", slot)
            return
        _INLINE_SLOTS.add(slot)
    try:
        _create_inline_panel(slot, prompt_target)
    except Exception as error:
        with _INLINE_LOCK:
            _INLINE_SLOTS.discard(slot)
        LOGGER.exception("内嵌面板创建失败：%s", error)


def _create_inline_json_batch_panel(slot):
    """Render the JSON Prompt batch controls directly below Forge txt2img."""
    prefix = f"llm_prompt_studio_{slot}_json_batch"
    empty_payload = _png_batch_json({"schema_version": PNG_BATCH_SCHEMA, "producer": {"name": "LLM Prompt Studio"}, "records": []})
    initial_handoff_choices, initial_handoff_status = _inline_ranbooru_handoff_views()
    with gr.Accordion("JSON Prompt 批量润色 / 扩写", open=False, elem_id=prefix):
        json_file = gr.File(label="导入 Prompt JSON", file_types=[".json"], type="filepath", elem_id=f"{prefix}_file")
        with gr.Row(elem_classes=["lps-form-row"]):
            json_ranbooru_handoff = gr.Dropdown(
                label="Ranbooru 交接来源",
                choices=initial_handoff_choices.get("choices", []),
                value=initial_handoff_choices.get("value"),
                elem_id=f"{prefix}_ranbooru_handoff",
            )
            json_ranbooru_refresh = gr.Button("刷新 Ranbooru", elem_id=f"{prefix}_ranbooru_refresh")
            json_ranbooru_load = gr.Button("载入所选交接", elem_id=f"{prefix}_ranbooru_load")
        json_ranbooru_status = gr.Markdown(initial_handoff_status, elem_id=f"{prefix}_ranbooru_status")
        json_png_receive = gr.Button("接收 PNG Prompt Collector 当前批次", elem_id=f"{prefix}_png_receive")
        json_payload = gr.Textbox(label="批量输入 / 结果 JSON", value=empty_payload, lines=6, elem_id=f"{prefix}_payload")
        with gr.Row(elem_classes=["lps-form-row"]):
            json_preset = gr.Dropdown(
                label="转换 System Prompt 预设",
                choices=PRESET_UI_CHOICES,
                value="Krea 2 Natural",
                elem_id=f"{prefix}_preset",
            )
            json_base_model = gr.Dropdown(
                label="转换目标底模",
                choices=MODEL_UI_CHOICES,
                value="Krea 2",
                elem_id=f"{prefix}_base_model",
            )
        with gr.Row(elem_classes=["lps-form-row"]):
            json_action = gr.Radio(label="操作", choices=ACTION_UI_CHOICES, value="Convert", elem_id=f"{prefix}_action")
            json_variation_mode = gr.Radio(
                label="批量多样性模式", choices=JSON_VARIATION_MODE_CHOICES, value="independent",
                elem_id=f"{prefix}_variation_mode",
            )
            json_target = gr.Radio(label="目标 Prompt", choices=[("不写入", "none"), ("txt2img", "txt2img")], value="none", elem_id=f"{prefix}_target")
            json_append = gr.Radio(label="写入方式", choices=[("追加", "append"), ("覆盖", "replace")], value="append", elem_id=f"{prefix}_append")
        with gr.Row():
            json_run = gr.Button("开始处理", variant="primary", elem_id=f"{prefix}_run")
            json_cancel = gr.Button("取消", variant="stop", elem_id=f"{prefix}_cancel")
            json_append_all = gr.Button("全部结果写入正面 Prompt", elem_id=f"{prefix}_append_all")
            json_export = gr.DownloadButton("导出结果", elem_id=f"{prefix}_export")
        json_table = gr.Dataframe(
            headers=["序号", "文件", "原始正向 Prompt", "状态", "LLM 结果", "错误"],
            datatype=["number", "str", "str", "str", "str", "str"], interactive=False, wrap=True,
            elem_id=f"{prefix}_table", elem_classes=["lps-table"],
        )
        json_status = gr.HTML("等待导入 JSON。", elem_id=f"{prefix}_status", elem_classes=["lps-status"])
        json_selection = gr.Number(value=1, precision=0, visible=False, elem_id=f"{prefix}_selection")
        json_current = gr.Textbox(value="", visible=False, elem_id=f"{prefix}_current")
        json_append_succeeded = gr.Checkbox(value=False, visible=False, elem_id=f"{prefix}_append_succeeded")
        json_file.change(
            _png_batch_load,
            inputs=json_file,
            outputs=[json_payload, json_table, json_selection, json_current, json_status],
        )
        json_ranbooru_refresh.click(
            _inline_ranbooru_handoff_views,
            inputs=json_ranbooru_handoff,
            outputs=[json_ranbooru_handoff, json_ranbooru_status],
        )
        json_ranbooru_load.click(
            _inline_ranbooru_handoff_load,
            inputs=json_ranbooru_handoff,
            outputs=[json_payload, json_table, json_selection, json_current, json_status],
        )
        json_png_receive.click(
            fn=None,
            outputs=json_status,
            js=f"() => window.llmPromptStudioPngBatch.receiveCollectorBatch('{slot}')",
        )
        json_payload.input(
            _png_batch_refresh,
            inputs=[json_payload, json_selection],
            outputs=[json_table, json_selection, json_current, json_status],
        )
        json_preset.change(
            _recommended_base_model_for_preset,
            inputs=json_preset,
            outputs=json_base_model,
        )
        json_run.click(
            _inline_json_batch_run,
            inputs=[json_payload, json_action, json_preset, json_base_model, json_variation_mode],
            outputs=[json_payload, json_table, json_selection, json_current, json_status],
        )
        json_cancel.click(_cancel_png_batch, outputs=json_status, queue=False)
        json_export.click(_png_batch_export_file, inputs=json_payload, outputs=json_export)
        json_append_all.click(
            fn=None,
            inputs=[json_payload, json_target, json_append],
            outputs=[json_status, json_append_succeeded],
            js="(payload, target, mode) => window.llmPromptStudioPngBatch.appendAllToPrompt(payload, target, mode)",
        )


def _create_inline_panel(slot, prompt_target):
    workflow = _workflow_settings()
    with gr.Accordion("Prompt 批量生成", open=False, elem_id=f"llm_prompt_studio_{slot}_inline"):
        gr.Markdown("用于连续生图时换 Prompt；生成预设、目标底模和内容模式与独立面板使用同一组选项。")
        request = gr.Textbox(
            label="本轮创作要求", lines=2, placeholder="例如：复杂二次元场景，不要只生成风格词",
            elem_id=f"llm_prompt_studio_{slot}_inline_request",
        )
        inline_creative_template_button = gr.Button(
            "填入通用创作需求",
            elem_id=f"llm_prompt_studio_{slot}_creative_template",
        )
        inline_kemonimimi_template_button = gr.Button(
            "填入萌系兽耳批量模板",
            elem_id=f"llm_prompt_studio_{slot}_kemonimimi_template",
        )
        with gr.Row(elem_classes=["lps-form-row"]):
            inline_preset = gr.Dropdown(
                label="System Prompt 预设", choices=PRESET_UI_CHOICES, value=workflow["preset"],
                elem_id=f"llm_prompt_studio_{slot}_inline_preset",
            )
            inline_base_model = gr.Dropdown(
                label="目标底模", choices=MODEL_UI_CHOICES, value=workflow["base_model"],
                elem_id=f"llm_prompt_studio_{slot}_inline_base_model",
            )
            inline_safety = gr.Radio(
                label="内容模式", choices=["SFW", "NSFW"], value=workflow["safety"],
                elem_id=f"llm_prompt_studio_{slot}_inline_safety",
            )
        with gr.Row(elem_classes=["lps-form-row"]):
            inline_source = gr.Radio(
                label="Prompt 来源", choices=[("LLM 自动生成", "llm"), ("缓存顺序读取", "cache")], value="llm",
                elem_id=f"llm_prompt_studio_{slot}_inline_source",
            )
            inline_cycles = gr.Number(
                label="轮数（0 = 持续）", value=0, minimum=0, precision=0,
                elem_id=f"llm_prompt_studio_{slot}_inline_cycles",
            )
        with gr.Row():
            inline_once = gr.Button("只生成并写入 Prompt", elem_id=f"llm_prompt_studio_{slot}_inline_once")
            inline_start = gr.Button("启动 Prompt + Forge 流程", variant="primary", elem_id=f"llm_prompt_studio_{slot}_inline_start")
            inline_cancel = gr.Button("停止", variant="stop", elem_id=f"llm_prompt_studio_{slot}_inline_cancel")
        inline_loop_status = gr.HTML("尚未启动：选择 Prompt 来源后点击“启动 Prompt + Forge 流程”。", elem_id=f"llm_prompt_studio_{slot}_inline_loop_status", elem_classes=["lps-status"])
        inline_generate = gr.Button("内嵌 LLM 生成", visible=False, elem_id=f"llm_prompt_studio_{slot}_inline_generate")
        inline_output = gr.Textbox(visible=False, elem_id=f"llm_prompt_studio_{slot}_inline_output")
        inline_system_preview = gr.Textbox(visible=False, elem_id=f"llm_prompt_studio_{slot}_inline_system_preview")
        inline_status = gr.Markdown(visible=False, elem_id=f"llm_prompt_studio_{slot}_inline_status")
        inline_prompt_update = gr.Textbox(visible=False, elem_id=f"llm_prompt_studio_{slot}_inline_prompt_update")
        inline_cache_button = gr.Button("读取下一条缓存", visible=False, elem_id=f"llm_prompt_studio_{slot}_inline_cache_fetch")
        inline_cache_output = gr.Textbox(visible=False, elem_id=f"llm_prompt_studio_{slot}_inline_cache_output")
        inline_cache_status = gr.Textbox(visible=False, elem_id=f"llm_prompt_studio_{slot}_inline_cache_status")
        inline_cache_cursor = gr.State(0)
        if slot == "txt2img":
            _create_inline_json_batch_panel(slot)
        inline_creative_template_button.click(
            lambda: GENERAL_CREATIVE_REQUEST_TEMPLATE,
            outputs=request,
        )
        inline_kemonimimi_template_button.click(
            lambda: KEMONOMIMI_LOLI_BATCH_TEMPLATE,
            outputs=request,
        )
        inline_generate.click(
            _inline_generate,
            inputs=[
                request, gr.State(""), inline_preset, gr.State(workflow["system_override"]),
                inline_base_model, inline_safety, gr.State(workflow["nsfw_injection"]),
                gr.State(workflow["user_instruction"]), gr.State(workflow["few_shot_count"]),
                gr.State(workflow["rag_min_score"]), gr.State(workflow["remove_bad"]),
                gr.State(workflow["remove_terms"]), gr.State(workflow["shuffle"]),
                gr.State(workflow["spaces"]), gr.State(workflow["max_tags"]),
                gr.State(workflow["structured_mode"]), gr.State(workflow["region_count"]),
                gr.State(workflow["save_score"]), gr.State(workflow["cache_result"]),
                gr.State(slot),
            ],
            outputs=[inline_output, inline_system_preview, inline_status, inline_prompt_update],
        )
        inline_cache_button.click(_inline_cached_prompt, inputs=inline_cache_cursor, outputs=[inline_cache_output, inline_cache_status, inline_cache_cursor])
        inline_once.click(
            fn=None,
            inputs=[request, inline_source],
            outputs=inline_loop_status,
            js=f"(request, source) => window.llmPromptStudioAutoLoop.inlineOnce({{slot: '{slot}', request, source}})",
        )
        inline_start.click(
            fn=None,
            inputs=[request, inline_source, inline_cycles],
            outputs=inline_loop_status,
            js=f"(request, source, cycles) => window.llmPromptStudioAutoLoop.inlineLoop({{slot: '{slot}', request, source, cycles}})",
        )
        inline_cancel.click(
            fn=_cancel_inline_generation, inputs=gr.State(slot), outputs=inline_loop_status,
            js=f"(slot) => {{ window.llmPromptStudioAutoLoop.cancelInline('{slot}'); return [slot]; }}", queue=False,
        )
        with _INLINE_LOCK:
            _INLINE_WORKFLOW_COMPONENTS[slot] = {
                "preset": inline_preset,
                "base_model": inline_base_model,
                "safety": inline_safety,
            }


def _wd14_interrogate(image, endpoint, model, threshold):
    if image is None:
        return "", "请先选择用于 WD14 反推的图片。"
    try:
        from PIL import Image
        from io import BytesIO
        buffer = BytesIO()
        Image.fromarray(image).save(buffer, format="PNG")
        payload = json.dumps({"image": base64.b64encode(buffer.getvalue()).decode("ascii"), "model": model, "threshold": float(threshold)}).encode("utf-8")
        import urllib.request
        url = validate_endpoint(endpoint) + "/tagger/v1/interrogate"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=180) as response:
            response_body = response.read(4 * 1024 * 1024 + 1)
        if len(response_body) > 4 * 1024 * 1024:
            raise ValueError("WD14 响应超过 4 MiB 限制")
        data = json.loads(response_body.decode("utf-8"))
        tags = [key for key, value in data.get("caption", {}).items() if isinstance(value, (float, int)) and value >= float(threshold)]
        return ", ".join(tags), f"WD14 返回了 {len(tags)} 个标签"
    except Exception as error:
        return "", f"WD14 不可用：{_safe_error(error)}"


def _api_generate(payload: dict[str, Any]):
    saved_connection = _connection_settings()
    defaults = {
        "preset": "Danbooru Tags", "system_override": "", "base_model": "Auto / checkpoint default", "safety": "SFW",
        "provider": saved_connection["provider"], "endpoint": saved_connection["endpoint"], "model": saved_connection["model"], "api_key": "",
        "temperature": saved_connection["temperature"], "timeout": saved_connection["timeout"],
        "max_tokens": saved_connection["max_tokens"], "send_temperature": saved_connection["send_temperature"],
        "remove_bad": True, "remove_terms": "", "shuffle": False, "spaces": False, "max_tags": 0,
        "structured_mode": "Plain Prompt", "region_count": 1, "save_score": 0, "cache_result": False,
        "nsfw_injection": "", "user_instruction": "", "source_tags": "",
    }
    payload = dict(payload or {})
    allowed_fields = set(defaults) | {"request"}
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise ValueError(f"API request contains unsupported fields: {', '.join(unknown_fields)}")
    if payload.get("api_key"):
        raise ValueError("API Key cannot be supplied through the plugin API; save it in the Chinese UI")

    def require_choice(field: str, choices: set[str] | dict[str, Any], label: str) -> None:
        value = payload.get(field, defaults[field])
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f"Unsupported {label}: {value}")

    require_choice("preset", PRESETS, "prompt preset")
    require_choice("base_model", BASE_MODEL_GUIDANCE, "base model profile")
    require_choice("safety", {"SFW", "NSFW"}, "safety mode")
    require_choice("structured_mode", {"Plain Prompt", "Regional JSON", "Regional Markdown"}, "structured output mode")
    for field in ("send_temperature", "remove_bad", "shuffle", "spaces", "cache_result"):
        if field in payload and type(payload[field]) is not bool:
            raise ValueError(f"API field {field} must be a boolean")
    if "provider" in payload and payload["provider"] != saved_connection["provider"]:
        raise ValueError("API Provider must match the active connection saved in the plugin UI")
    if "endpoint" in payload and validate_endpoint(payload["endpoint"]) != validate_endpoint(saved_connection["endpoint"]):
        raise ValueError("API endpoint must match the connection saved in the plugin UI")
    values = {**defaults, **(payload or {})}
    generated, system, status = _generate(values.get("request", ""), *[values[key] for key in [
        "source_tags", "preset", "system_override", "base_model", "safety", "nsfw_injection", "user_instruction", "provider", "endpoint", "model", "api_key", "temperature", "timeout", "max_tokens", "send_temperature"
    ]], 0, 0, *[values[key] for key in [
        "remove_bad", "remove_terms", "shuffle", "spaces", "max_tags", "structured_mode", "region_count", "save_score", "cache_result"
    ]])
    if not generated:
        raise ValueError(status)
    return {"prompt": generated, "system_prompt": system, "status": status}


HANDOFF_STATUS_LABELS = {
    "pending": "待处理",
    "processing": "处理中",
    "completed": "已完成",
    "error": "处理失败",
    "skipped": "已跳过",
}


def _normalize_ranbooru_handoff(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload or {})
    allowed = {
        "ranbooru_id", "database_key", "tags_prompt", "natural_prompt", "selected_prompt",
        "selected_is_natural", "rating", "source_score", "booru", "post_id", "source_url",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"Ranbooru 交接包含不支持的字段：{', '.join(unknown)}")
    if "selected_is_natural" in payload and type(payload["selected_is_natural"]) is not bool:
        raise ValueError("Ranbooru 交接字段 selected_is_natural 必须是布尔值")
    tags_prompt = str(payload.get("tags_prompt") or "").strip()
    natural_prompt = str(payload.get("natural_prompt") or "").strip()
    selected_prompt = str(payload.get("selected_prompt") or "").strip()
    if max(len(tags_prompt), len(natural_prompt), len(selected_prompt)) > 100000:
        raise ValueError("Ranbooru 交接 Prompt 超过 100000 字符上限")
    selected_is_natural = bool(payload.get("selected_is_natural"))
    if not tags_prompt and selected_prompt and not selected_is_natural:
        tags_prompt = selected_prompt
    if not natural_prompt and selected_prompt and selected_is_natural:
        natural_prompt = selected_prompt
    if not tags_prompt and not natural_prompt:
        raise ValueError("Ranbooru 交接没有可用的 Tag Prompt 或自然语言 Prompt")
    database_key = str(payload.get("database_key") or "").strip().lower()
    if database_key and not re.fullmatch(r"[0-9a-f]{16}", database_key):
        raise ValueError("Ranbooru 交接字段 database_key 必须是 16 位十六进制字符串")
    if not database_key:
        database_key = hashlib.sha256((tags_prompt or natural_prompt).encode("utf-8")).hexdigest()[:16]
    source_id = str(payload.get("ranbooru_id") or "").strip()
    if source_id and not re.fullmatch(r"[a-zA-Z0-9_.-]{1,120}", source_id):
        raise ValueError("Ranbooru 交接字段 ranbooru_id 包含不支持的字符或长度")
    if not source_id:
        source_id = hashlib.sha256((tags_prompt or natural_prompt).encode("utf-8")).hexdigest()[:16]
    try:
        source_score = int(float(payload.get("source_score") or 0))
    except (TypeError, ValueError, OverflowError):
        source_score = 0
    return {
        "ranbooru_id": source_id,
        "database_key": database_key,
        "tags_prompt": tags_prompt,
        "natural_prompt": natural_prompt,
        "selected_prompt": selected_prompt or natural_prompt or tags_prompt,
        "selected_is_natural": selected_is_natural,
        "rating": str(payload.get("rating") or "").strip().lower()[:32],
        "source_score": source_score,
        "booru": str(payload.get("booru") or "").strip()[:80],
        "post_id": str(payload.get("post_id") or "").strip()[:120],
        "source_url": str(payload.get("source_url") or "").strip()[:1000],
    }


def _handoff_source_ref(payload: dict[str, Any]) -> str:
    return f"ranbooru:{payload['database_key']}:{payload['ranbooru_id']}"


def _handoff_safety(rating: str, fallback: str = "SFW") -> str:
    value = str(rating or "").strip().lower()
    if value in {"q", "questionable", "e", "explicit", "nsfw"}:
        return "NSFW"
    if value in {"g", "general", "safe", "s", "sensitive"}:
        return "SFW"
    return fallback if fallback in {"SFW", "NSFW"} else "SFW"


def receive_ranbooru_handoff(payload: dict[str, Any], action: str = "send") -> dict[str, Any]:
    normalized = _normalize_ranbooru_handoff(payload)
    handoff_id = DB.save_handoff(normalized, "ranbooru", _handoff_source_ref(normalized), action)
    return {
        "handoff_id": handoff_id,
        "status": "已发送到 LLM 提示词工作室。" if action == "send" else "已加入 LLM 处理队列。",
    }


def _process_handoff_record(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("status") == "completed" and str(record.get("result_prompt") or "").strip():
        return {
            "handoff_id": record["id"], "prompt": record["result_prompt"], "system_prompt": "",
            "status": "Ranbooru 实时处理已完成，复用已有结果，未重复请求 LLM。",
        }
    claimed = DB.claim_handoff(record["id"])
    if claimed is None:
        latest = DB.get_handoff(record["id"])
        if latest and latest.get("status") == "completed" and str(latest.get("result_prompt") or "").strip():
            return {
                "handoff_id": latest["id"], "prompt": latest["result_prompt"], "system_prompt": "",
                "status": "Ranbooru 实时处理已完成，复用已有结果，未重复请求 LLM。",
            }
        raise ValueError("交接记录正在处理，或当前状态不允许重复执行")
    record = claimed
    if record.get("payload_decode_error"):
        message = str(record["payload_decode_error"])
        DB.update_handoff(
            record["id"], "error", error=message,
            expected_claim_token=record.get("claim_token"), expected_revision=record.get("revision"),
        )
        raise ValueError(message)
    payload = record.get("payload") or {}
    workflow = _workflow_settings()
    connection = _connection_settings()
    preset = workflow["preset"]
    base_model = workflow["base_model"]
    safety = _handoff_safety(payload.get("rating", ""), workflow["safety"])
    selected_prompt = str(payload.get("selected_prompt") or "").strip()
    natural_prompt = str(payload.get("natural_prompt") or "").strip()
    selected_is_natural = bool(payload.get("selected_is_natural") and natural_prompt)
    source_tags = "" if selected_is_natural else str(payload.get("tags_prompt") or "").strip()
    request = natural_prompt if selected_is_natural else str(natural_prompt or selected_prompt).strip()
    if not source_tags and not request:
        raise ValueError("交接记录没有可处理的 Prompt")
    profile_key = hashlib.sha256(f"{preset}\x1f{base_model}".encode("utf-8")).hexdigest()[:12]
    prompt_source_ref = f"{record['source_ref']}:llm:{profile_key}"
    generated = system = status = ""
    try:
        generated, system, status = _generate(
            request, source_tags, preset, workflow["system_override"], base_model, safety,
            workflow["nsfw_injection"], workflow["user_instruction"],
            connection["provider"], connection["endpoint"], connection["model"], "",
            connection["temperature"], connection["timeout"], connection["max_tokens"],
            connection["send_temperature"], workflow["few_shot_count"], workflow["rag_min_score"],
            workflow["remove_bad"], workflow["remove_terms"], workflow["shuffle"], workflow["spaces"],
            workflow["max_tags"], workflow["structured_mode"], workflow["region_count"],
            0, False, "ranbooru", prompt_source_ref, True,
        )
    except Exception as error:
        generated = ""
        status = f"未预期异常：{error}"
    if generated:
        try:
            with DB.lock:
                current = DB.get_handoff(record["id"])
                owns_revision = bool(
                    current
                    and current.get("status") == "processing"
                    and current.get("claim_token") == record.get("claim_token")
                    and current.get("revision") == record.get("revision")
                )
                if not owns_revision:
                    raise ValueError("交接已被新版本替代；旧 LLM 结果已丢弃")
                DB.save_prompt(
                    generated, "", preset, base_model, 0, source_tags or request,
                    score_source="unrated", score_reason="Ranbooru 交接未评分",
                    source_kind="ranbooru", source_ref=prompt_source_ref, dedupe=True,
                )
                completed = DB.update_handoff(
                    record["id"], "completed", result_prompt=generated,
                    expected_claim_token=record.get("claim_token"), expected_revision=record.get("revision"),
                )
                if not completed:
                    raise ValueError("交接已被新版本替代；旧 LLM 结果已丢弃")
        except ValueError:
            raise
        except Exception as error:
            cache_error = f"缓存交接结果失败：{error}"
            DB.update_handoff(
                record["id"], "error", error=cache_error,
                expected_claim_token=record.get("claim_token"), expected_revision=record.get("revision"),
            )
            raise ValueError(f"{cache_error}；记录已保留，可手动重试") from error
        return {
            "handoff_id": record["id"], "prompt": generated, "system_prompt": system,
            "status": f"Ranbooru 实时处理完成（LLM 请求 1 次）：{status}",
        }
    if not DB.update_handoff(
        record["id"], "error", error=status,
        expected_claim_token=record.get("claim_token"), expected_revision=record.get("revision"),
    ):
        raise ValueError("交接已被新版本替代；旧 LLM 错误结果已丢弃")
    raise ValueError(f"Ranbooru 实时处理失败，已记录并可手动重试：{status}")


def process_ranbooru_handoff(payload_or_id: dict[str, Any] | int | str) -> dict[str, Any]:
    if isinstance(payload_or_id, dict):
        received = receive_ranbooru_handoff(payload_or_id, "process_and_cache")
        record = DB.get_handoff(received["handoff_id"])
    else:
        try:
            record = DB.get_handoff(int(payload_or_id))
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("请选择有效的 Ranbooru 交接记录") from error
    if not record:
        raise ValueError("Ranbooru 交接记录不存在或已被清理")
    return _process_handoff_record(record)


def _handoff_views(selected=None):
    records = DB.list_handoffs()
    rows, choices = [], []
    for record in records:
        payload = record.get("payload") or {}
        preview = " ".join(str(payload.get("selected_prompt") or payload.get("tags_prompt") or "").split())
        if len(preview) > 72:
            preview = preview[:69] + "..."
        label = HANDOFF_STATUS_LABELS.get(record["status"], record["status"])
        choices.append((f"#{record['id']} | {label} | {preview}", str(record["id"])))
        rows.append([
            record["id"], label, record["attempts"], payload.get("ranbooru_id", ""),
            payload.get("rating", ""), payload.get("source_score", 0),
            payload.get("tags_prompt", ""), payload.get("natural_prompt", ""),
            record.get("error") or record.get("payload_decode_error", ""), record.get("result_prompt", ""),
        ])
    available = {value for _, value in choices}
    selected_value = str(selected or "")
    retained = selected_value if selected_value in available else (choices[0][1] if choices else None)
    status = f"实时交接箱共 {len(records)} 条；失败和跳过记录会保留，支持统一查看后手动重试。"
    return gr.update(value=rows), gr.update(choices=choices, value=retained), status


def _inline_ranbooru_handoff_views(selected=None):
    records = [record for record in DB.list_handoffs() if record.get("source_kind") == "ranbooru"]
    choices = []
    for record in records:
        payload = record.get("payload") or {}
        preview = " ".join(str(
            payload.get("selected_prompt") or payload.get("natural_prompt") or payload.get("tags_prompt") or ""
        ).split())
        if len(preview) > 72:
            preview = preview[:69] + "..."
        label = HANDOFF_STATUS_LABELS.get(record.get("status"), record.get("status", ""))
        choices.append((f"#{record['id']} | {label} | {preview}", str(record["id"])))
    available = {value for _, value in choices}
    selected_value = str(selected or "")
    retained = selected_value if selected_value in available else (choices[0][1] if choices else None)
    status = f"Ranbooru 可用交接记录：{len(records)} 条。"
    return gr.update(choices=choices, value=retained), status


def _inline_ranbooru_handoff_load(handoff_id):
    try:
        data = _ranbooru_handoff_to_png_batch(handoff_id)
        selected, current = _png_batch_current(data, 1)
        return _png_batch_json(data), _png_batch_table(data), selected, current, (
            f"已载入 Ranbooru 交接 #{handoff_id}；可直接点击批处理进行润色/扩写。"
        )
    except Exception as error:
        return gr.update(), gr.update(), gr.update(), gr.update(), f"Ranbooru 载入失败：{_safe_error(error)}"


def _load_handoff_into_generation(handoff_id):
    record = DB.get_handoff(int(handoff_id)) if str(handoff_id or "").isdigit() else None
    if not record:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "请选择一条交接记录。"
    payload = record.get("payload") or {}
    has_tags = bool(str(payload.get("tags_prompt") or "").strip())
    preset = "NoobAI Tags" if has_tags else "Krea 2 Natural"
    base_model = "NoobAI" if has_tags else "Krea 2"
    workflow = _workflow_settings()
    return (
        payload.get("natural_prompt") or payload.get("selected_prompt") or "",
        payload.get("tags_prompt") or "",
        preset,
        base_model,
        _handoff_safety(payload.get("rating", ""), workflow["safety"]),
        f"已载入 Ranbooru 交接 #{record['id']}，可在“生成提示词”页继续编辑。",
    )


def _process_selected_handoff(handoff_id, query="", min_score=0, output_mode="全部", base_model="全部"):
    try:
        result = process_ranbooru_handoff(handoff_id)
        message = result["status"]
        prompt = result["prompt"]
        system = result["system_prompt"]
    except Exception as error:
        message = f"处理失败：{_safe_error(error)}"
        prompt = system = ""
    handoff_table, handoff_choices, _ = _handoff_views(handoff_id)
    cache_table, cache_choices = _filtered_cache_updates(query, min_score, output_mode, base_model)
    return prompt, system, message, handoff_table, handoff_choices, cache_table, cache_choices


def _skip_selected_handoff(handoff_id):
    if not str(handoff_id or "").isdigit() or not DB.update_handoff(
        int(handoff_id), "skipped", error="用户手动跳过", allowed_statuses={"pending", "error"},
    ):
        table, choices, _ = _handoff_views(handoff_id)
        return "请选择有效的交接记录。", table, choices
    table, choices, _ = _handoff_views(handoff_id)
    return f"已跳过交接 #{handoff_id}；记录仍保留，可稍后手动重试。", table, choices


def _clear_finished_handoffs():
    deleted = DB.delete_handoffs({"completed", "skipped"})
    table, choices, _ = _handoff_views()
    return f"已清理 {deleted} 条已完成或已跳过的交接记录；失败记录仍保留。", table, choices


def on_app_started(_, app):
    _ensure_server_queue_worker()
    recovered_handoffs = DB.recover_stale_handoffs()
    if recovered_handoffs:
        LOGGER.warning("recovered %s stale Ranbooru handoff claims", recovered_handoffs)
    saved_workflow = DB.get_setting("workflow_settings_v1", {}) or {}
    wildcard_source = Path(saved_workflow.get("wildcard_path") or DEFAULT_WILDCARDS) if isinstance(saved_workflow, dict) else DEFAULT_WILDCARDS
    if wildcard_source.is_dir():
        try:
            files, terms = DB.index_wildcards(wildcard_source)
            LOGGER.info("wildcard library ready: %s updated files, %s terms", files, terms)
        except Exception as error:
            LOGGER.warning("wildcard indexing skipped: %s", error)
    try:
        from fastapi import Depends, HTTPException
        from fastapi.security import HTTPBasic
        from modules import shared
        from secrets import compare_digest

        configured_auth = str(getattr(shared.cmd_opts, "api_auth", "") or "").strip()
        security = HTTPBasic(auto_error=False)
        credentials = dict(item.split(":", 1) for item in configured_auth.split(",") if ":" in item)

        def api_access(request: Request, value=Depends(security)):
            if configured_auth:
                if value and value.username in credentials and compare_digest(value.password, credentials[value.username]):
                    return True
                raise HTTPException(status_code=401, detail="Incorrect username or password", headers={"WWW-Authenticate": "Basic"})
            client_host = (request.client.host if request.client else "").split("%", 1)[0]
            try:
                if ipaddress.ip_address(client_host).is_loopback:
                    return True
            except ValueError:
                pass
            raise HTTPException(status_code=403, detail="Plugin API requires Forge --api-auth for remote access")

        api_dependencies = [Depends(api_access)]
        if configured_auth:
            LOGGER.info("API protected by Forge --api-auth")

        @app.post("/llm-prompt-studio/v1/generate", dependencies=api_dependencies)
        def prompt_studio_generate(payload: dict[str, Any]):
            try:
                return _api_generate(payload)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        @app.get("/llm-prompt-studio/v1/cache", dependencies=api_dependencies)
        def prompt_studio_cache(query: str = "", limit: int = 100):
            return {"records": DB.list_prompts(query, limit)}

        @app.post("/llm-prompt-studio/v1/queue", dependencies=api_dependencies)
        def prompt_studio_queue(payload: dict[str, Any]):
            try:
                return _enqueue_server_queue(payload)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        @app.get("/llm-prompt-studio/v1/queue/{batch_id}", dependencies=api_dependencies)
        def prompt_studio_queue_status(batch_id: str):
            return _server_queue_snapshot(batch_id)

        @app.post("/llm-prompt-studio/v1/queue/{batch_id}/cancel", dependencies=api_dependencies)
        def prompt_studio_queue_cancel(batch_id: str):
            with _SERVER_QUEUE_CANCEL_LOCK:
                _SERVER_QUEUE_CANCEL_BATCHES.add(str(batch_id))
            _SERVER_QUEUE_CANCEL.set()
            cancelled = DB.cancel_server_queue(batch_id)
            _SERVER_QUEUE_WAKE.set()
            return _server_queue_snapshot(batch_id) | {"cancelled_now": cancelled}

        @app.post("/llm-prompt-studio/v1/handoff", dependencies=api_dependencies)
        def prompt_studio_handoff(payload: dict[str, Any]):
            try:
                return receive_ranbooru_handoff(payload)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        @app.post("/llm-prompt-studio/v1/handoff/process", dependencies=api_dependencies)
        def prompt_studio_handoff_process(payload: dict[str, Any]):
            try:
                return process_ranbooru_handoff(payload)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        @app.get("/llm-prompt-studio/v1/handoffs", dependencies=api_dependencies)
        def prompt_studio_handoffs(limit: int = 100):
            return {"records": DB.list_handoffs(limit)}
    except Exception as error:
        LOGGER.exception("API registration failed: %s", error)


def on_ui_tabs():
    llm_settings = _connection_settings()
    workflow = _workflow_settings()
    ranbooru_link = _ranbooru_link_settings()
    initial_records = DB.list_prompts()
    initial_handoff_table, initial_handoff_choices, initial_handoff_status = _handoff_views()
    with gr.Blocks(analytics_enabled=False, css=UI_CSS, elem_id="llm_prompt_studio") as ui:
        gr.Markdown("## LLM 提示词工作室\n本地静态词库、提示词缓存与 Forge 扩展集成。")
        gr.Markdown("核心流程：生成单条 Prompt；批处理多条任务；缓存与联动负责筛选、评分、导入导出和 Ranbooru。连接设置只需首次配置，工具按需使用。")
        with gr.Tabs(elem_id="llm_prompt_studio_main_tabs"):
            with gr.Tab("生成", elem_id="llm_prompt_studio_generate_tab"):
                with gr.Row():
                    with gr.Column(scale=3):
                        request = gr.Textbox(
                            label="创作要求",
                            lines=4,
                            placeholder="描述希望生成的画面，或粘贴已有提示词",
                            elem_id="llm_prompt_studio_request",
                        )
                        source_tags = gr.Textbox(
                            label="源标签（PNG Tag 汇总可导入，优先使用）",
                            lines=3,
                            elem_id="llm_prompt_studio_source_tags",
                        )
                        creative_template_button = gr.Button(
                            "填入通用创作需求",
                            elem_id="llm_prompt_studio_creative_template",
                        )
                        kemonimimi_template_button = gr.Button(
                            "填入萌系兽耳批量模板",
                            elem_id="llm_prompt_studio_kemonimimi_template",
                        )
                        preset = gr.Dropdown(label="System Prompt 预设", choices=PRESET_UI_CHOICES, value=workflow["preset"])
                        base_model = gr.Dropdown(label="目标底模", choices=MODEL_UI_CHOICES, value=workflow["base_model"])
                        safety = gr.Radio(label="内容模式", choices=["SFW", "NSFW"], value=workflow["safety"])
                        with gr.Accordion("高级 Prompt 约束", open=False):
                            system_override = gr.Textbox(label="自定义 System Prompt（可选）", lines=6, value=workflow["system_override"], placeholder="留空则使用所选预设。安全策略、用户要求和静态词库会自动追加。")
                            nsfw_injection = gr.Textbox(label="NSFW System Prompt 注入", lines=2, value=workflow["nsfw_injection"], placeholder="仅在 NSFW 模式下生效")
                            user_instruction = gr.Textbox(label="用户输出要求（低优先级）", lines=2, value=workflow["user_instruction"], placeholder="例如：只返回最多 35 个标签，不使用权重")
                    with gr.Column(scale=2):
                        structured_mode = gr.Radio(label="输出格式", choices=OUTPUT_UI_CHOICES, value=workflow["structured_mode"])
                        region_count = gr.Slider(label="区域数量", minimum=1, maximum=8, value=workflow["region_count"], step=1)
                        with gr.Accordion("标签后处理", open=False):
                            remove_bad = gr.Checkbox(label="移除不良标签", value=workflow["remove_bad"])
                            remove_terms = gr.Textbox(label="额外排除标签 / 通配规则", value=workflow["remove_terms"], placeholder="watermark, *_text")
                            shuffle = gr.Checkbox(label="随机打乱标签", value=workflow["shuffle"])
                            spaces = gr.Checkbox(label='将“_”转换为空格', value=workflow["spaces"])
                            max_tags = gr.Slider(label="最大标签数（0 表示不限）", minimum=0, maximum=200, value=workflow["max_tags"], step=1)
                        few_shot_count = gr.State(0)
                        rag_min_score = gr.State(0)
                        with gr.Accordion("缓存", open=False):
                            save_score = gr.Slider(label="缓存评分", minimum=0, maximum=10, value=workflow["save_score"], step=0.5)
                            cache_result = gr.Checkbox(label="在本地缓存本次结果", value=workflow["cache_result"], elem_id="llm_prompt_studio_cache_result")
                with gr.Row():
                    generate = gr.Button("生成提示词", variant="primary", elem_id="llm_prompt_studio_generate_button")
                    save_workflow = gr.Button("保存全部工作参数")
                    reset_workflow = gr.Button("恢复默认工作参数")
                output = gr.Textbox(label="生成的提示词", lines=8, elem_id="llm_prompt_studio_output")
                system_preview = gr.Textbox(label="最终 System Prompt", lines=12)
                status = gr.Markdown(elem_id="llm_prompt_studio_status", elem_classes=["lps-status"])
                workflow_status = gr.Markdown("已自动载入上次保存的工作参数。" if DB.get_setting("workflow_settings_v1") else "当前使用默认工作参数；保存后下次会自动填入。")

            with gr.Tab("批处理", elem_id="llm_prompt_studio_batch_tab"):
                with gr.Row(elem_classes=["lps-form-row"]):
                    batch_preset = gr.Dropdown(
                        label="System Prompt 预设", choices=PRESET_UI_CHOICES, value=workflow["preset"],
                        elem_id="llm_prompt_studio_batch_preset",
                    )
                    batch_base_model = gr.Dropdown(
                        label="目标底模", choices=MODEL_UI_CHOICES, value=workflow["base_model"],
                        elem_id="llm_prompt_studio_batch_base_model",
                    )
                    batch_safety = gr.Radio(
                        label="内容模式", choices=["SFW", "NSFW"], value=workflow["safety"],
                        elem_id="llm_prompt_studio_batch_safety",
                    )
                with gr.Tabs():
                    with gr.Tab("服务端批量生成（仅 Prompt）"):
                        gr.Markdown(
                            "服务端逐行调用 LLM，只生成并缓存 Prompt，不会自动启动 Forge 生图；下方浏览器队列是独立链路。重复要求也会分别调用 LLM。"
                        )
                        batch_sources = gr.Textbox(
                            label="批量创作要求（每行一条）", lines=12,
                            placeholder="红发魔法师在月光图书馆阅读\n蓝发少女站在雨中的车站",
                            elem_id="llm_prompt_studio_auto_loop_request",
                        )
                        auto_loop_request = batch_sources
                        with gr.Row():
                            batch_skip_existing = gr.Checkbox(
                                label="跳过批次开始前已有缓存的要求（取消勾选可重复生成）",
                                value=workflow["batch_skip_existing"],
                            )
                            batch_skip_failed = gr.Checkbox(label="单条失败后跳过并继续", value=workflow["batch_skip_failed"])
                        gr.Markdown("批量结果统一保存为未评分，可稍后在缓存编辑器中手动调整评分。")
                        with gr.Row():
                            batch_preview_button = gr.Button("预览生成队列")
                            batch_generate = gr.Button("开始生成并缓存", variant="primary")
                            batch_cancel = gr.Button("取消批量任务", variant="stop")
                            save_batch_workflow = gr.Button("保存批量与工作参数")
                        batch_preview_status = gr.Markdown()
                        batch_queue = gr.Dataframe(
                            value=[], headers=["序号", "输入", "生成结果", "状态"],
                            datatype=["number", "str", "str", "str"], interactive=False, wrap=True,
                            label="批量输入与生成结果（最多显示 200 条）",
                        )
                        batch_status = gr.Markdown("尚未开始批量任务。取消会在当前 HTTP 请求返回后生效，已完成结果会保留。", elem_id="llm_prompt_studio_batch_status", elem_classes=["lps-status"])
                        batch_task_id = gr.State(lambda: uuid.uuid4().hex)
                        batch_issue_state = gr.State([])
                        batch_issues = gr.Dataframe(
                            headers=["序号", "Tag / 输入", "状态", "错误或跳过原因", "本轮尝试次数"],
                            datatype=["number", "str", "str", "str", "number"],
                            interactive=False, wrap=True, label="错误与跳过汇总",
                        )
                        batch_issue_selection = gr.CheckboxGroup(label="勾选需要手动重试的 Tag / 输入", choices=[])
                        with gr.Row():
                            batch_select_all_issues = gr.Button("全选错误与跳过项")
                            batch_clear_issue_selection = gr.Button("清空选择")
                            batch_retry_selected = gr.Button("重新提交所选（每条一次）", variant="primary")
                        with gr.Accordion("另一条路径：浏览器生图队列（可选）", open=False, elem_id="llm_prompt_studio_auto_loop_tab"):
                            gr.Markdown("这里不会读取上方已生成结果，而会重新调用 LLM 建立浏览器队列，再逐条写入 txt2img/img2img 并生图。")
                            with gr.Row(elem_classes=["lps-form-row"]):
                                auto_loop_target = gr.Radio(
                                    label="生图目标", choices=[("txt2img", "txt2img"), ("img2img", "img2img")],
                                    value="txt2img", elem_id="llm_prompt_studio_auto_loop_target",
                                )
                                auto_loop_write_mode = gr.Radio(
                                    label="Prompt 写入方式", choices=[("追加", "append"), ("覆盖", "replace")],
                                    value="append", elem_id="llm_prompt_studio_auto_loop_write_mode",
                                )
                                auto_loop_cache_result = gr.Checkbox(
                                    label="同时缓存到 SQLite Prompt 库", value=False,
                                    elem_id="llm_prompt_studio_auto_loop_cache",
                                )
                                auto_loop_continuous = gr.Checkbox(
                                    label="持续生成并生图", value=False,
                                    elem_id="llm_prompt_studio_auto_loop_continuous",
                                )
                                auto_loop_cycles = gr.Number(
                                    label="循环轮数（0 表示持续到取消）", value=1, minimum=0, precision=0,
                                    elem_id="llm_prompt_studio_auto_loop_cycles",
                                )
                            with gr.Row():
                                auto_loop_start = gr.Button("重新调用 LLM 并加入生图队列", variant="primary", elem_id="llm_prompt_studio_auto_loop_start")
                                auto_loop_generate_run = gr.Button("重新调用 LLM 并立即生图", variant="primary", elem_id="llm_prompt_studio_auto_loop_generate_run")
                                auto_loop_run = gr.Button("投入已有队列生图", elem_id="llm_prompt_studio_auto_loop_run")
                                auto_loop_clear = gr.Button("清空队列", elem_id="llm_prompt_studio_auto_loop_clear")
                                auto_loop_cancel = gr.Button("取消当前阶段", variant="stop", elem_id="llm_prompt_studio_auto_loop_cancel")
                            auto_loop_dispatch = gr.Button(
                                "自动队列单次生成",
                                elem_id="llm_prompt_studio_auto_loop_dispatch",
                                elem_classes=["lps-auto-loop-dispatch"],
                            )
                            auto_loop_status = gr.HTML(
                                "等待开始。生成到队列后可检查、追加到 Prompt，或直接投入生图。",
                                elem_id="llm_prompt_studio_auto_loop_status", elem_classes=["lps-status"],
                            )
                            gr.HTML("", elem_id="llm_prompt_studio_auto_loop_log", elem_classes=["lps-auto-loop-log"])
                            gr.Markdown("### 服务端队列（页面关闭后仍继续）")
                            gr.Markdown("服务端线程负责逐条调用 LLM，并可通过 Forge API 生成 txt2img。此处日志和已生成 Prompt 来自 SQLite，不依赖浏览器保持连接。")
                            server_queue_target = gr.Radio(
                                label="服务端生图目标", choices=[("只生成 Prompt", "none"), ("txt2img", "txt2img")],
                                value="none", elem_id="llm_prompt_studio_server_queue_target",
                            )
                            with gr.Row():
                                server_queue_start = gr.Button("加入服务端队列", variant="primary", elem_id="llm_prompt_studio_server_queue_start")
                                server_queue_refresh = gr.Button("刷新日志", elem_id="llm_prompt_studio_server_queue_refresh")
                                server_queue_cancel = gr.Button("取消服务端队列", variant="stop", elem_id="llm_prompt_studio_server_queue_cancel")
                            server_queue_id = gr.Textbox(label="服务端任务 ID", interactive=False, elem_id="llm_prompt_studio_server_queue_id")
                            server_queue_status = gr.HTML("尚未提交服务端任务。", elem_id="llm_prompt_studio_server_queue_status", elem_classes=["lps-status"])
                            server_queue_log = gr.HTML("", elem_id="llm_prompt_studio_server_queue_log", elem_classes=["lps-auto-loop-log"])
                    with gr.Tab("JSON Prompt 批量处理", visible=False, elem_id="llm_prompt_studio_png_batch_tab"):
                        png_batch_file = gr.File(label="导入 Prompt JSON", file_types=[".json"], type="filepath", elem_id="llm_prompt_studio_png_batch_file")
                        with gr.Accordion("批次 JSON", open=False):
                            png_batch_payload = gr.Textbox(
                                label="批量输入 / 结果 JSON",
                                value=_png_batch_json({"schema_version": PNG_BATCH_SCHEMA, "producer": {"name": "LLM Prompt Studio"}, "records": []}),
                                lines=8,
                                elem_id="llm_prompt_studio_png_batch_payload",
                            )
                        png_batch_action = gr.Radio(label="操作", choices=ACTION_UI_CHOICES, value="Expand", elem_id="llm_prompt_studio_png_batch_action")
                        png_batch_selection = gr.Number(label="当前序号", value=1, precision=0, elem_id="llm_prompt_studio_png_batch_selection")
                        png_batch_current = gr.Textbox(label="当前结果", lines=3, interactive=False, elem_id="llm_prompt_studio_png_batch_current")
                        with gr.Row():
                            png_batch_previous = gr.Button("上一条", elem_id="llm_prompt_studio_png_batch_previous")
                            png_batch_next = gr.Button("下一条", elem_id="llm_prompt_studio_png_batch_next")
                        png_batch_target = gr.Radio(label="目标 Prompt", choices=[("不写入", "none"), ("txt2img", "txt2img"), ("img2img", "img2img")], value="none", elem_id="llm_prompt_studio_png_batch_target")
                        png_batch_append = gr.Radio(label="写入方式", choices=[("追加", "append"), ("覆盖", "replace")], value="append", elem_id="llm_prompt_studio_png_batch_append")
                        with gr.Row():
                            png_batch_run = gr.Button("开始处理", variant="primary", elem_id="llm_prompt_studio_png_batch_run")
                            png_batch_cancel = gr.Button("取消", variant="stop", elem_id="llm_prompt_studio_png_batch_cancel")
                            png_batch_append_button = gr.Button("追加并下一条", variant="primary", elem_id="llm_prompt_studio_png_batch_append_button")
                            png_batch_append_all = gr.Button("全部结果写入正面 Prompt", elem_id="llm_prompt_studio_png_batch_append_all")
                            png_batch_export = gr.DownloadButton("导出结果", elem_id="llm_prompt_studio_png_batch_export")
                        png_batch_table = gr.Dataframe(headers=["序号", "文件", "原始正向 Prompt", "状态", "LLM 结果", "错误"], datatype=["number", "str", "str", "str", "str", "str"], interactive=False, wrap=True, elem_id="llm_prompt_studio_png_batch_table", elem_classes=["lps-table"])
                        gr.Markdown("", elem_id="llm_prompt_studio_png_batch_results")
                        png_batch_status = gr.HTML("等待导入 JSON。", elem_id="llm_prompt_studio_png_batch_status", elem_classes=["lps-status"])
                        png_batch_append_succeeded = gr.Checkbox(value=False, visible=False, elem_id="llm_prompt_studio_png_batch_append_succeeded")
                        png_batch_file.change(
                            _png_batch_load,
                            inputs=png_batch_file,
                            outputs=[png_batch_payload, png_batch_table, png_batch_selection, png_batch_current, png_batch_status],
                        )

                    with gr.Tab("直接批量导入"):
                        bulk_import = gr.Textbox(label="每行一条 Prompt，可使用“评分<TAB>Prompt”格式", lines=12)
                        with gr.Row():
                            bulk_output_mode = gr.Dropdown(label="缓存格式", choices=PRESET_UI_CHOICES, value=workflow["preset"])
                            bulk_base_model = gr.Dropdown(label="目标底模", choices=MODEL_UI_CHOICES, value=workflow["base_model"])
                            bulk_default_score = gr.Slider(label="手动导入评分", minimum=0, maximum=10, value=0, step=0.5)
                        with gr.Row():
                            bulk_preview_button = gr.Button("预览导入队列")
                            bulk_import_button = gr.Button("导入本地缓存", variant="primary")
                        bulk_import_status = gr.Markdown()
                        bulk_preview = gr.Dataframe(headers=["序号", "评分", "Prompt"], datatype=["number", "number", "str"], interactive=False, wrap=True, label="导入预览")

            with gr.Tab("缓存与联动", elem_id="llm_prompt_studio_library_tab"):
                with gr.Row():
                    cache_query = gr.Textbox(label="搜索 Prompt、负面词、源标签或外部来源", scale=4)
                    cache_min_score = gr.Slider(label="最低评分", minimum=0, maximum=10, value=0, step=0.5, scale=2)
                    cache_output_filter = gr.Dropdown(label="格式", choices=["全部"] + PRESET_UI_CHOICES, value="全部", scale=2)
                    cache_model_filter = gr.Dropdown(label="目标模型", choices=["全部"] + MODEL_UI_CHOICES, value="全部", scale=2)
                with gr.Row():
                    refresh = gr.Button("应用筛选", variant="primary")
                    clear_filters = gr.Button("清除筛选")
                    undo_delete = gr.Button("撤销上次删除")
                cache_status = gr.Markdown(f"本地缓存共 {len(initial_records)} 条记录。点击表格任意单元格可载入该行。", elem_id="llm_prompt_studio_cache_status", elem_classes=["lps-status"])
                selected_records = gr.Dropdown(label="选择缓存记录（支持多选）", choices=_cache_choices(initial_records), value=[], multiselect=True)
                delete_preview_state = gr.State([])
                with gr.Row():
                    load_selected = gr.Button("载入所选单条")
                    preview_selected = gr.Button("预览所选")
                    delete_selected = gr.Button("删除所选", variant="stop")
                selection_preview = gr.Textbox(label="选择 / 删除预览", lines=6, interactive=False)
                table = gr.Dataframe(
                    value=_as_rows(initial_records), label="已缓存 Prompt",
                    headers=["全库序号", "内部 ID", "评分", "评分来源", "评分模型", "格式", "目标模型", "正向提示词", "负面提示词", "源标签", "评分理由", "外部来源", "来源标识"],
                    datatype=["number", "number", "number", "str", "str", "str", "str", "str", "str", "str", "str", "str", "str"],
                    interactive=False, wrap=True, elem_id="llm_prompt_studio_cache_table", elem_classes=["lps-table"],
                )
                with gr.Accordion("记录编辑器", open=False):
                    with gr.Row():
                        record_id = gr.Textbox(label="当前内部 ID", interactive=False)
                        record_output_mode = gr.Dropdown(label="格式", choices=PRESET_UI_CHOICES, value=workflow["preset"])
                        record_base_model = gr.Dropdown(label="目标模型", choices=MODEL_UI_CHOICES, value=workflow["base_model"])
                        record_score = gr.Slider(label="评分", minimum=0, maximum=10, value=0, step=0.5)
                    record_tags = gr.Textbox(label="源标签")
                    record_prompt = gr.Textbox(label="正向提示词", lines=6)
                    record_negative = gr.Textbox(label="负面提示词", lines=3)
                    with gr.Row():
                        save = gr.Button("保存当前记录", variant="primary")
                        save_as_new = gr.Button("另存为新记录")
                with gr.Accordion("按全库序号批量管理", open=False):
                    position_spec = gr.Textbox(label="全库序号或范围", placeholder="1-100,205,300-320")
                    with gr.Row():
                        preview_positions = gr.Button("预览这些序号")
                        delete_positions = gr.Button("删除这些序号", variant="stop")
                with gr.Accordion("Ranbooru 缓存联动", open=False):
                    ranbooru_database_path = gr.Textbox(
                        label="Ranbooru tag_cache.db 路径", value=ranbooru_link["database_path"],
                    )
                    with gr.Row():
                        ranbooru_detect = gr.Button("自动检测路径")
                        ranbooru_content_mode = gr.Radio(
                            label="同步内容", choices=RANBOORU_CONTENT_CHOICES,
                            value=ranbooru_link["content_mode"],
                        )
                        ranbooru_rating_filter = gr.Dropdown(
                            label="内容分级筛选", choices=RANBOORU_RATING_CHOICES,
                            value=ranbooru_link["rating_filter"],
                        )
                    with gr.Row():
                        ranbooru_min_source_score = gr.Number(
                            label="Ranbooru 最低源评分", value=ranbooru_link["min_source_score"], precision=0,
                        )
                        ranbooru_source_limit = gr.Number(
                            label="最多读取源记录（0 表示安全上限 100000）", value=ranbooru_link["source_limit"], precision=0,
                        )
                    with gr.Row():
                        ranbooru_tag_output_mode = gr.Dropdown(
                            label="Tag 数据输出预设", choices=PRESET_UI_CHOICES,
                            value=ranbooru_link["tag_output_mode"],
                        )
                        ranbooru_tag_base_model = gr.Dropdown(
                            label="Tag 数据目标底模", choices=MODEL_UI_CHOICES,
                            value=ranbooru_link["tag_base_model"],
                        )
                    with gr.Row():
                        ranbooru_natural_output_mode = gr.Dropdown(
                            label="自然语言数据输出预设", choices=PRESET_UI_CHOICES,
                            value=ranbooru_link["natural_output_mode"],
                        )
                        ranbooru_natural_base_model = gr.Dropdown(
                            label="自然语言数据目标底模", choices=MODEL_UI_CHOICES,
                            value=ranbooru_link["natural_base_model"],
                        )
                    with gr.Row():
                        ranbooru_save = gr.Button("保存联动参数")
                        ranbooru_preview_button = gr.Button("预览 Ranbooru 缓存")
                        ranbooru_sync_button = gr.Button("同步到本插件缓存", variant="primary")
                    ranbooru_status = gr.Markdown("已自动填入上次保存的 Ranbooru 联动参数。")
                    ranbooru_preview = gr.Dataframe(
                        headers=["序号", "Ranbooru ID", "内容类型", "源评分", "分级", "输出预设", "目标底模", "Prompt"],
                        datatype=["number", "str", "str", "number", "str", "str", "str", "str"],
                        interactive=False, wrap=True, label="Ranbooru 同步预览",
                    )
                    with gr.Accordion("Ranbooru 实时交接箱", open=False):
                        handoff_selection = gr.Dropdown(
                            label="选择交接记录",
                            choices=initial_handoff_choices.get("choices", []),
                            value=initial_handoff_choices.get("value"),
                        )
                        with gr.Row():
                            handoff_refresh = gr.Button("刷新交接箱")
                            handoff_load = gr.Button("载入到生成页")
                            handoff_process = gr.Button("使用 LLM 处理并缓存", variant="primary")
                            handoff_skip = gr.Button("跳过所选")
                            handoff_clear_finished = gr.Button("清理已完成 / 已跳过")
                    handoff_status = gr.Markdown(initial_handoff_status, elem_id="llm_prompt_studio_handoff_status", elem_classes=["lps-status"])
                    handoff_table = gr.Dataframe(
                        value=initial_handoff_table.get("value", []),
                        headers=[
                                "交接 ID", "状态", "尝试次数", "Ranbooru ID", "分级", "源评分",
                                "Tag Prompt", "自然语言 Prompt", "错误", "LLM 结果",
                        ],
                        datatype=["number", "str", "number", "str", "str", "number", "str", "str", "str", "str"],
                        interactive=False, wrap=True, label="实时交接、错误与跳过汇总", elem_id="llm_prompt_studio_handoff_table", elem_classes=["lps-table"],
                    )
                with gr.Accordion("JSON / CSV 导入导出", open=False):
                    with gr.Row():
                        import_file = gr.File(label="导入文件", file_types=[".json", ".csv"], type="filepath")
                        import_dedupe = gr.Checkbox(label="导入时跳过重复记录", value=True)
                        import_button = gr.Button("导入文件")
                    with gr.Row():
                        export_format = gr.Radio(label="导出格式", choices=["JSON", "CSV"], value="JSON")
                        export_selected = gr.Button("导出所选")
                        export_button = gr.Button("导出全部缓存")
                    export_file = gr.File(label="导出文件", interactive=False)

            with gr.Tab("连接设置", elem_id="llm_prompt_studio_connection_tab"):
                provider = gr.Dropdown(label="Provider", choices=PROVIDER_UI_CHOICES, value=llm_settings["provider"])
                endpoint = gr.Textbox(label="接口地址", value=llm_settings["endpoint"])
                model = gr.Textbox(label="模型 ID", value=llm_settings["model"], placeholder="填写服务端暴露的模型名称", elem_id="llm_prompt_studio_model_id")
                api_key = gr.Textbox(label="API Key（留空则使用已保存凭据）", type="password")
                temperature = gr.Slider(label="温度", minimum=0, maximum=2, value=llm_settings["temperature"], step=0.05)
                timeout = gr.Slider(label="超时秒数", minimum=5, maximum=600, value=llm_settings["timeout"], step=5)
                max_tokens = gr.Number(label="最大输出 Token（0 表示使用 Provider 默认值）", value=llm_settings["max_tokens"], precision=0)
                send_temperature = gr.Checkbox(label="发送温度参数（推理模型不支持时关闭）", value=llm_settings["send_temperature"])
                test = gr.Button("测试 API", elem_id="llm_prompt_studio_test_connection")
                with gr.Row():
                    save_connection = gr.Button("保存全部 LLM 设置", variant="primary")
                    clear_credentials = gr.Button("清除已保存的 API Key")
                test_status = gr.Markdown(
                    _credential_status(llm_settings["provider"], llm_settings["endpoint"]),
                    elem_id="llm_prompt_studio_connection_status",
                    elem_classes=["lps-status"],
                )
            with gr.Tab("工具", elem_id="llm_prompt_studio_tools_tab"):
                with gr.Tabs(elem_id="llm_prompt_studio_tools_tabs"):
                    with gr.Tab("静态词库", elem_id="llm_prompt_studio_wildcards_tab"):
                        wildcard_path = gr.Textbox(label="静态词库目录", value=workflow["wildcard_path"], elem_id="llm_prompt_studio_wildcard_path")
                        wildcard_status = gr.Markdown(
                            "索引会在插件启动、页面加载和目录变更时自动增量刷新。",
                            elem_id="llm_prompt_studio_wildcard_status",
                            elem_classes=["lps-status"],
                        )
                        wildcard_query = gr.Textbox(label="搜索已索引词条", elem_id="llm_prompt_studio_wildcard_query")
                        wildcard_results = gr.Dropdown(label="匹配结果", choices=[], multiselect=True, elem_id="llm_prompt_studio_wildcard_results")
                    with gr.Tab("WD14 + LLM", elem_id="llm_prompt_studio_wd14_tab"):
                        image = gr.Image(label="待反推图片", type="numpy")
                        wd_endpoint = gr.Textbox(label="Forge WD14 API 地址", value=workflow["wd_endpoint"])
                        wd_model = gr.Textbox(label="WD14 模型", value=workflow["wd_model"])
                        wd_threshold = gr.Slider(label="WD14 阈值", minimum=0, maximum=1, value=workflow["wd_threshold"], step=0.01)
                        interrogate = gr.Button("调用已安装的 WD14 Tagger")
                        wd_tags = gr.Textbox(label="WD14 标签", lines=5)
                        wd_status = gr.Markdown(elem_classes=["lps-status"])
                        action = gr.Radio(label="LLM 操作", choices=ACTION_UI_CHOICES, value="Expand")
                        transform = gr.Button("使用 LLM 扩写 / 润色", variant="primary")
                        transform_output = gr.Textbox(label="LLM 处理结果", lines=8)

        workflow_inputs = [
            preset, system_override, base_model, safety, nsfw_injection, user_instruction,
            structured_mode, region_count, remove_bad, remove_terms, shuffle, spaces, max_tags,
            few_shot_count, rag_min_score, save_score, cache_result,
            batch_skip_existing, batch_skip_failed,
            wd_endpoint, wd_model, wd_threshold, wildcard_path,
        ]
        batch_workflow_inputs = [
            batch_preset, system_override, batch_base_model, batch_safety, nsfw_injection, user_instruction,
            structured_mode, region_count, remove_bad, remove_terms, shuffle, spaces, max_tags,
            few_shot_count, rag_min_score, save_score, cache_result,
            batch_skip_existing, batch_skip_failed,
            wd_endpoint, wd_model, wd_threshold, wildcard_path,
        ]
        creative_template_button.click(lambda: GENERAL_CREATIVE_REQUEST_TEMPLATE, outputs=request)
        kemonimimi_template_button.click(lambda: KEMONOMIMI_LOLI_BATCH_TEMPLATE, outputs=request)
        generate.click(_generate, inputs=[request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, save_score, cache_result], outputs=[output, system_preview, status])
        auto_loop_dispatch.click(
            _generate_auto_loop,
            inputs=[request, batch_preset, system_override, batch_base_model, batch_safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, auto_loop_cache_result],
            outputs=[output, system_preview, status],
        )
        save_workflow.click(_save_workflow_settings, inputs=workflow_inputs, outputs=workflow_status)
        save_batch_workflow.click(_save_workflow_settings, inputs=batch_workflow_inputs, outputs=batch_status)
        reset_workflow.click(_reset_workflow_settings, outputs=[*workflow_inputs, workflow_status])
        with _INLINE_LOCK:
            inline_workflows = list(_INLINE_WORKFLOW_COMPONENTS.values())
        shared_workflow_fields = {
            "preset": (preset, batch_preset),
            "base_model": (base_model, batch_base_model),
            "safety": (safety, batch_safety),
        }
        for field, (generate_component, batch_component) in shared_workflow_fields.items():
            inline_components = [components[field] for components in inline_workflows]
            _bind_workflow_sync(generate_component, [batch_component, *inline_components], event="change")
            _bind_workflow_sync(batch_component, [generate_component, *inline_components])
            for current in inline_components:
                other_inline = [component for component in inline_components if component is not current]
                _bind_workflow_sync(current, [generate_component, batch_component, *other_inline])
        provider.change(_load_provider_settings, inputs=provider, outputs=[endpoint, model, temperature, timeout, max_tokens, send_temperature, test_status])
        test.click(_test_connection, inputs=[provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=test_status)
        save_connection.click(_save_llm_settings, inputs=[provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=[test_status, endpoint, model])
        clear_credentials.click(_clear_llm_credentials, inputs=[provider, endpoint], outputs=test_status)
        ui.load(
            _load_active_connection_settings,
            outputs=[provider, endpoint, model, temperature, timeout, max_tokens, send_temperature, test_status],
        )
        ui.load(
            _index_wildcards,
            inputs=wildcard_path,
            outputs=[wildcard_status, wildcard_results],
        )
        png_batch_payload.input(_png_batch_refresh, inputs=[png_batch_payload, png_batch_selection], outputs=[png_batch_table, png_batch_selection, png_batch_current, png_batch_status])
        png_batch_previous.click(_png_batch_move, inputs=[png_batch_payload, png_batch_selection, gr.State(-1)], outputs=[png_batch_selection, png_batch_current])
        png_batch_next.click(_png_batch_move, inputs=[png_batch_payload, png_batch_selection, gr.State(1)], outputs=[png_batch_selection, png_batch_current])
        png_batch_selection.change(_png_batch_current, inputs=[png_batch_payload, png_batch_selection], outputs=[png_batch_selection, png_batch_current])
        png_batch_run.click(
            _png_batch_run,
            inputs=[png_batch_payload, png_batch_action, batch_preset, system_override, batch_base_model, batch_safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count],
            outputs=[png_batch_payload, png_batch_table, png_batch_selection, png_batch_current, png_batch_status],
        )
        png_batch_cancel.click(_cancel_png_batch, outputs=png_batch_status, queue=False)
        png_batch_export.click(_png_batch_export_file, inputs=png_batch_payload, outputs=png_batch_export)
        png_batch_append_all.click(
            fn=None,
            inputs=[png_batch_payload, png_batch_target, png_batch_append],
            outputs=[png_batch_status, png_batch_append_succeeded],
            js="(payload, target, mode) => window.llmPromptStudioPngBatch.appendAllToPrompt(payload, target, mode)",
        )
        png_append_event = png_batch_append_button.click(
            fn=None,
            inputs=[png_batch_current, png_batch_target, png_batch_append],
            outputs=[png_batch_status, png_batch_append_succeeded],
            js="(prompt, target, mode) => window.llmPromptStudioPngBatch.appendToPrompt(prompt, target, mode)",
        )
        png_append_event.then(
            _png_batch_advance_after_append,
            inputs=[png_batch_payload, png_batch_selection, png_batch_append_succeeded],
            outputs=[png_batch_payload, png_batch_selection, png_batch_current, png_batch_status],
            queue=False,
        )
        auto_loop_start.click(
            fn=None,
            inputs=auto_loop_request,
            outputs=auto_loop_status,
            js="(request) => window.llmPromptStudioAutoLoop.generateBatch({request})",
        )
        auto_loop_run.click(
            fn=None,
            inputs=[auto_loop_target, auto_loop_write_mode],
            outputs=auto_loop_status,
            js="(target, writeMode) => window.llmPromptStudioAutoLoop.runStored({target, writeMode})",
        )
        auto_loop_generate_run.click(
            fn=None,
            inputs=[auto_loop_request, auto_loop_target, auto_loop_write_mode, auto_loop_continuous, auto_loop_cycles],
            outputs=auto_loop_status,
            js="(request, target, writeMode, continuous, cycles) => window.llmPromptStudioAutoLoop.generateAndRun({request, target, writeMode, continuous, cycles})",
        )
        auto_loop_cancel.click(
            fn=_cancel_auto_loop_generation,
            inputs=[],
            outputs=auto_loop_status,
            js="() => { window.llmPromptStudioAutoLoop.cancel(); return []; }",
            queue=False,
        )
        auto_loop_clear.click(
            fn=None,
            inputs=[],
            outputs=auto_loop_status,
            js="() => window.llmPromptStudioAutoLoop.clearQueue()",
            queue=False,
        )
        wildcard_path.change(
            _index_wildcards,
            inputs=wildcard_path,
            outputs=[wildcard_status, wildcard_results],
        )
        wildcard_query.change(_search_wildcards, inputs=wildcard_query, outputs=wildcard_results)
        interrogate.click(_wd14_interrogate, inputs=[image, wd_endpoint, wd_model, wd_threshold], outputs=[wd_tags, wd_status])
        transform.click(_expand_or_polish, inputs=[wd_tags, action, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=[transform_output, wd_status])
        cache_filter_inputs = [cache_query, cache_min_score, cache_output_filter, cache_model_filter]
        ranbooru_inputs = [
            ranbooru_database_path, ranbooru_content_mode, ranbooru_rating_filter,
            ranbooru_min_source_score, ranbooru_source_limit,
            ranbooru_tag_output_mode, ranbooru_tag_base_model,
            ranbooru_natural_output_mode, ranbooru_natural_base_model,
        ]
        ranbooru_detect.click(_detect_ranbooru_cache, outputs=[ranbooru_database_path, ranbooru_status])
        ranbooru_save.click(_save_ranbooru_link_settings, inputs=ranbooru_inputs, outputs=ranbooru_status)
        ranbooru_preview_button.click(
            _preview_ranbooru_link, inputs=ranbooru_inputs, outputs=[ranbooru_preview, ranbooru_status],
        )
        ranbooru_sync_button.click(
            _sync_ranbooru_link,
            inputs=[*ranbooru_inputs, *cache_filter_inputs],
            outputs=[ranbooru_status, table, selected_records],
        )
        handoff_refresh.click(
            _handoff_views, inputs=handoff_selection,
            outputs=[handoff_table, handoff_selection, handoff_status],
        )
        handoff_load.click(
            _load_handoff_into_generation, inputs=handoff_selection,
            outputs=[request, source_tags, preset, base_model, safety, handoff_status],
        )
        handoff_process.click(
            _process_selected_handoff,
            inputs=[handoff_selection, *cache_filter_inputs],
            outputs=[output, system_preview, handoff_status, handoff_table, handoff_selection, table, selected_records],
        )
        handoff_skip.click(
            _skip_selected_handoff, inputs=handoff_selection,
            outputs=[handoff_status, handoff_table, handoff_selection],
        )
        handoff_clear_finished.click(
            _clear_finished_handoffs,
            outputs=[handoff_status, handoff_table, handoff_selection],
        )
        refresh.click(_refresh_cache, inputs=cache_filter_inputs, outputs=[table, selected_records, cache_status])
        cache_query.submit(_refresh_cache, inputs=cache_filter_inputs, outputs=[table, selected_records, cache_status])
        clear_filters.click(_clear_cache_filters, outputs=[cache_query, cache_min_score, cache_output_filter, cache_model_filter, table, selected_records, cache_status])
        record_outputs = [record_id, record_prompt, record_negative, record_output_mode, record_base_model, record_score, record_tags, cache_status]
        table.select(_select_cache_row, inputs=table, outputs=[selected_records, *record_outputs])
        selected_records.input(_load_selected_record, inputs=selected_records, outputs=record_outputs)
        load_selected.click(_load_selected_record, inputs=selected_records, outputs=record_outputs)
        preview_selected.click(_preview_selected, inputs=selected_records, outputs=[selection_preview, delete_preview_state])
        delete_selected.click(_delete_previewed_records, inputs=[selected_records, delete_preview_state, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        save.click(_save_record, inputs=[record_id, record_prompt, record_negative, record_output_mode, record_base_model, record_score, record_tags, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        save_as_new.click(_save_record_as_new, inputs=[record_prompt, record_negative, record_output_mode, record_base_model, record_score, record_tags, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        bulk_preview_button.click(_preview_bulk_cache, inputs=[bulk_import, bulk_output_mode, bulk_base_model, bulk_default_score], outputs=[bulk_preview, bulk_import_status])
        bulk_import_button.click(_bulk_cache, inputs=[bulk_import, bulk_output_mode, bulk_base_model, bulk_default_score, *cache_filter_inputs], outputs=[bulk_import_status, table, selected_records])
        batch_preview_button.click(_preview_batch_sources, inputs=[batch_sources, batch_skip_existing, batch_preset, batch_base_model], outputs=[batch_queue, batch_preview_status])
        batch_generate.click(
            _batch_generate,
            inputs=[batch_sources, batch_skip_existing, batch_skip_failed, batch_preset, system_override, batch_base_model, batch_safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, *cache_filter_inputs, batch_issue_state, batch_task_id],
            outputs=[batch_status, table, selected_records, batch_issues, batch_issue_selection, batch_issue_state, batch_queue],
        )
        batch_cancel.click(_cancel_batch_generation, inputs=batch_task_id, outputs=batch_status)
        server_queue_start_event = server_queue_start.click(
            _server_queue_start_ui,
            inputs=[batch_sources, server_queue_target],
            outputs=[server_queue_id, server_queue_status, server_queue_log],
            queue=False,
        )
        server_queue_start_event.then(
            fn=None,
            inputs=server_queue_id,
            outputs=server_queue_status,
            js="(batchId) => window.llmPromptStudioAutoLoop.watchServerQueue(batchId)",
            queue=False,
        )
        server_queue_refresh.click(
            _server_queue_refresh_ui,
            inputs=server_queue_id,
            outputs=[server_queue_status, server_queue_log],
            queue=False,
        )
        server_queue_cancel.click(
            _server_queue_cancel_ui,
            inputs=server_queue_id,
            outputs=[server_queue_status, server_queue_log],
            queue=False,
        )
        batch_select_all_issues.click(_select_all_batch_issues, inputs=batch_issue_state, outputs=batch_issue_selection)
        batch_clear_issue_selection.click(_clear_batch_issue_selection, outputs=batch_issue_selection)
        batch_retry_selected.click(
            _retry_batch_issues,
            inputs=[batch_issue_selection, batch_issue_state, batch_skip_failed, batch_preset, system_override, batch_base_model, batch_safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, *cache_filter_inputs, batch_task_id],
            outputs=[batch_status, table, selected_records, batch_issues, batch_issue_selection, batch_issue_state, batch_queue],
        )
        preview_positions.click(_preview_positions, inputs=position_spec, outputs=[cache_status, table, selected_records])
        delete_positions.click(_delete_positions, inputs=[position_spec, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        undo_delete.click(_undo_last_delete, inputs=cache_filter_inputs, outputs=[cache_status, table, selected_records])
        import_button.click(_import_cache, inputs=[import_file, import_dedupe, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        export_selected.click(_export_selected, inputs=[selected_records, export_format], outputs=[cache_status, export_file])
        export_button.click(_export_cache, inputs=export_format, outputs=[cache_status, export_file])
    return [(ui, "LLM 提示词工作室", "llm_prompt_studio")]
