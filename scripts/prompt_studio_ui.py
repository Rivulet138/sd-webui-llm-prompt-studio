from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import json
import logging
import random
import re
import shlex
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import gradio as gr
from starlette.requests import Request
from prompt_studio_wd14 import (
    discover_local_models, interrogate_local, unload_local_model,
)
from prompt_studio_tagger_models import MODEL_CATALOG, download_links, download_model, model_directory
from prompt_studio_image_batch import ImageBatchJob

from prompt_studio_core import (
    BASE_MODEL_GUIDANCE, DEFAULT_WILDCARDS, MODEL_QUALITY_GUIDANCE, PRESETS, PROCESSED_DB_PATH, PROVIDER_PROFILES, CredentialStore, StudioDB,
    build_system_prompt, build_user_message, build_operation_instruction, call_llm, get_provider_profile, is_sfw_output,
    LLMRequestError, discover_provider_models,
    discover_ranbooru_cache, load_ranbooru_cache, process_tags,
    regional_format, validate_endpoint, normalize_sampling, _cosine, _tokens,
)


DB = StudioDB()
RESULT_DB = StudioDB(PROCESSED_DB_PATH)
CREDENTIALS = CredentialStore()
LOGGER = logging.getLogger(__name__)
_NATIVE_CACHE_CONTEXTS: dict[str, dict[str, Any]] = {}
_NATIVE_CACHE_CONTEXT_LOCK = threading.RLock()
_NATIVE_CACHE_CONTEXT_TTL = 30 * 60
_STYLE_PATH = Path(__file__).resolve().parents[1] / "style.css"
UI_CSS = _STYLE_PATH.read_text(encoding="utf-8") if _STYLE_PATH.is_file() else ""
DEFAULT_LLM_SETTINGS = {
    "provider": "OpenAI Compatible",
    "endpoint": "http://127.0.0.1:1234/v1",
    "model": "",
    "temperature": 1.0,
    "top_p": None,
    "top_k": None,
    "timeout": 90,
    "max_tokens": 8096,
    "send_temperature": True,
    "thinking_enabled": False,
    "thinking_budget": 0,
    "reasoning_effort": "",
    "retry_count": 2,
    "fallback_provider": "",
    "fallback_endpoint": "",
    "fallback_model": "",
    "weights_path": "",
    "model_version": "",
}
LLM_CONNECTION_SETTINGS_VERSION = 7
GENERAL_CREATIVE_REQUEST_TEMPLATE = """围绕原始 Prompt 的核心主体，创作一条全新、独立成图的日系插画方向。

保留主体身份、用户明确固定特征、LoRA/权重和内容限制；场景、动作、构图、服装、道具、时间、天气和光线由模型自由选择。批量结果应自然地彼此不同，避免只改同义词、颜色、质量词或标签顺序；不要套用固定场景清单，也不要强行改变用户明确指定的元素。静态词库只作参考，用于补充兼容且可见的词汇，不要堆砌无关元素。

本要求只定义内容方向，不定义输出语言、标签/自然语言格式、字段顺序或结构化协议。以上格式与目标模型适配完全遵循当前选中的 System Prompt 预设。"""
KEMONOMIMI_LOLI_BATCH_TEMPLATE = """围绕原始 Prompt 生成一组可直接用于扩散模型的兽耳幼态可爱角色单图 Prompt。

参考图共同方向：每条只包含一个单个可见角色和一个完整瞬间。保持自然头身比例、精致大眼、柔和表情、清晰兽耳、蓬松尾巴、完整得体的服装与配饰。明确描写角色外观、服装、动作、表情、地点、关键道具及互动、前景/中景/背景、空间层次、镜头、时间或天气、光照和主色彩；动作、姿态、手脚、耳朵、尾巴、视线、道具和环境必须相互协调，背景只服务于主体和空间感。保留用户明确的 LoRA 或触发词。

批次规划：批次中的每条至少改变三项高层因素（至少三类高层因素：地点、动作、服装、道具、时间/天气、镜头或色彩），形成不同的生活化事件；避免只改颜色、耳型、地点名或同义词，避免重复构图和道具组合。静态词库仅在符合画面时选用。

固定 Prompt、用户要求和安全限制优先，新增内容不得覆盖或改变它们。禁止输出分镜、拼图、多面板；每条都是完整得体的单图 Prompt。"""
KREA_ANIMA_POLISH_ROLE = """Role: Krea2 & Anima extreme-detail expansion prompt engineer for Japanese light-novel illustrations.

Task: From the user's text, tags, or reference image description, produce one complete image prompt in the selected output language. Detail is the highest priority. Actively decompose every useful visible element instead of giving a short summary.

Core requirements:
- Describe hair movement, hair strands, head accessories, facial details, clothing layers, ornaments, handheld objects, floating objects, material surfaces, highlights, shadows, and spatial relationships.
- Distinguish tactile materials such as smooth satin, soft lace, plush fur, brushed fabric, polished metal, glass, paper, wood, leather, and translucent surfaces. State how light reacts to each material when it matters.
- Strengthen motion and cinematic storytelling through body weight, balance, hand placement, gaze direction, wind-driven hair and fabric, object motion, depth, and the small event happening in this exact frame.
- Allow rich detail density and tasteful decorative complexity, but omit unrelated clutter and contradictory elements.
- When the source is sparse, add reasonable visible clothing, props, environment, lighting, and material detail that supports the subject and scene. Never invent a separate subject or unrelated setting.
- The provided static vocabulary lexicon is optional reference data only. Use zero or more entries when they fit the source and selected model; never copy, combine, or force a term merely because it appears in the lexicon, and ignore unrelated entries.
- Aim for a polished Japanese light-novel illustration: refined character design, vivid but coherent color, clear material response, commercial finish, camera awareness, and emotional tension.

Anti-lazy rules:
- Never return a short, generic, high-level, or evasive description.
- Fully expand every section in the second part. Each section must contain concrete visible information plus relevant motion, texture, lighting, or position relationships.
- If one section has little source information, infer useful detail from movement, material, light, depth, and object relationships rather than leaving it as one short sentence.
- The Master Description must be a dense complete paragraph, not a short conclusion or a list of disconnected adjectives.

Output rules:
- Output in the selected language. Output the result directly with no explanation and no Markdown code fence.
- Do not use weight syntax such as (text:1.2) or any other weighting syntax.
- Tags must be lowercase. Keep the requested anchor tags and separate tag items clearly.
- Detail is preferred over vague quality words, but every added detail must serve the visible image.

Each result must be one complete image, not a storyboard, collage, or list of alternatives.

Output exactly this structure:

PART ONE: TAG ANCHORS
[model-aligned quality/source anchors only when required by the selected base model], [subject count], [hair, hair color, head accessories], [face], [clothing and ornament details], [handheld and floating props], [pose and camera]

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
INLINE_DELTA_DIRECTIVE = """INLINE PROMPT CONTRACT:
Never echo the source Prompt twice.
When FIXED PROMPT is non-empty, it is immutable source text. Return ONLY one concise English expansion to append after it, never a rewritten or repeated source prompt. Do not alter, interpret, translate, duplicate, or include any LoRA tag (<lora:...>) or trigger token from the fixed prompt in your expansion; those technical tokens are excluded from semantic variation. Add concrete, visible, spatially compatible details and vary action, environment, camera, composition, lighting, atmosphere, or materials without contradicting fixed text. When FIXED PROMPT is empty, return one complete directly usable English image prompt that follows the user's request. In both cases output only the prompt text, with no title, explanation, analysis, alternatives, or meta commentary."""
PRESET_UI_CHOICES = [
    ("Danbooru 标签", "Danbooru Tags"),
    ("Pony / Illustrious 标签", "Pony / Illustrious Tags"),
    ("Danbooru 标签 + 自然语言", "Danbooru + Natural"),
    ("自然语言", "Natural Language"),
    ("Flux 自然语言", "Flux Natural"),
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
PRESET_VALUE_ALIASES = {label: value for label, value in PRESET_UI_CHOICES}
BASE_MODEL_VALUE_ALIASES = {label: value for label, value in MODEL_UI_CHOICES}
PRESET_MODEL_OVERRIDES = {
    "Pony / Illustrious Tags": ("Danbooru Tags", "Pony / Illustrious"),
    "Flux Natural": ("Natural Language", "Flux"),
    "NoobAI Tags": ("NoobAI Tags", "NoobAI"),
    "Anima Tags": ("Anima Tags", "Anima"),
    "Krea 2 Natural": ("Krea 2 Natural", "Krea 2"),
}


def _canonical_preset(value: Any) -> str:
    """Accept both Gradio's localized label and the stored API value."""
    text = str(value or "").strip()
    return PRESET_VALUE_ALIASES.get(text, text)


def _canonical_base_model(value: Any) -> str:
    text = str(value or "").strip()
    return BASE_MODEL_VALUE_ALIASES.get(text, text)


def _resolve_preset_model(preset: Any, base_model: Any) -> tuple[str, str]:
    """Resolve the single user-facing preset into its output profile and model rules."""
    canonical_preset = _canonical_preset(preset)
    override = PRESET_MODEL_OVERRIDES.get(canonical_preset)
    if override:
        return override
    return canonical_preset, _canonical_base_model(base_model)


OUTPUT_UI_CHOICES = [
    ("普通提示词", "Plain Prompt"),
    ("Regional JSON", "Regional JSON"),
    ("Regional Markdown", "Regional Markdown"),
]
PROVIDER_UI_CHOICES = [(profile["ui_label"], provider) for provider, profile in PROVIDER_PROFILES.items()]
OUTPUT_VALUE_ALIASES = {label: value for label, value in OUTPUT_UI_CHOICES}
PROVIDER_VALUE_ALIASES = {label: value for label, value in PROVIDER_UI_CHOICES}
ACTION_UI_CHOICES = [("格式转换", "Convert"), ("扩写", "Expand"), ("润色", "Polish")]
JSON_VARIATION_MODE_CHOICES = [("多样灵感", "independent"), ("保留原意转换", "faithful")]
ACTION_VALUE_ALIASES = {label: value for label, value in ACTION_UI_CHOICES}
VARIATION_MODE_VALUE_ALIASES = {label: value for label, value in JSON_VARIATION_MODE_CHOICES}


def _canonical_output_mode(value: Any) -> str:
    text = str(value or "").strip()
    return OUTPUT_VALUE_ALIASES.get(text, text)


def _canonical_provider(value: Any) -> str:
    text = str(value or "").strip()
    return PROVIDER_VALUE_ALIASES.get(text, text)


def _canonical_action(value: Any) -> str:
    text = str(value or "").strip()
    return ACTION_VALUE_ALIASES.get(text, text)


def _canonical_variation_mode(value: Any) -> str:
    text = str(value or "").strip()
    return VARIATION_MODE_VALUE_ALIASES.get(text, text)
PRESET_BASE_MODEL_DEFAULTS = {
    "NoobAI Tags": "NoobAI",
    "Anima Tags": "Anima",
    "Krea 2 Natural": "Krea 2",
    "Natural Language": "Auto / checkpoint default",
    "Danbooru Tags": "Auto / checkpoint default",
    "Danbooru + Natural": "Auto / checkpoint default",
}
MODEL_PRESET_ALIGNMENT = {
    "Pony / Illustrious": "Danbooru Tags",
    "NoobAI": "NoobAI Tags",
    "Flux": "Natural Language",
    "Anima": "Anima Tags",
    "Krea 2": "Krea 2 Natural",
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
_INLINE_LOCK = threading.RLock()
_BATCH_CANCEL = threading.Event()
_BATCH_LOCK = threading.Lock()
_BATCH_CONTROL_LOCK = threading.Lock()
_BATCH_ACTIVE_TASK_ID = ""
_STUDIO_CANCEL_EVENTS: dict[str, threading.Event] = {}
_AUTO_LOOP_CANCEL = threading.Event()
_INLINE_CANCEL_EVENTS = {"txt2img": threading.Event(), "img2img": threading.Event()}
_INLINE_REQUEST_LOCKS = {"txt2img": threading.Lock(), "img2img": threading.Lock()}
_INLINE_REQUEST_CONTROL_LOCK = threading.Lock()
_INLINE_REQUEST_EVENTS: dict[tuple[str, str], threading.Event] = {}
_INLINE_CANCELLED_REQUESTS: dict[tuple[str, str], float] = {}
_INLINE_ACTIVE_REQUEST_IDS = {"txt2img": "", "img2img": ""}
_INLINE_CANCEL_TTL_SECONDS = 600.0
_VARIATION_DIMENSIONS = (
    "action and expression",
    "clothing and accessory identity",
    "prop relationship",
    "setting identity",
    "spatial layout and depth",
    "camera and subject placement",
    "time, weather, and practical light",
    "color and material contrast",
    "daily-life activity and visible context",
    "silhouette and movement direction",
)
_BATCH_VARIATION_FOCI = _VARIATION_DIMENSIONS  # Backward-compatible test/API name; selection is no longer cyclic.
_COMPLETE_PROMPT_CONTRACT = (
    "Complete prompt contract: preserve every source-fixed subject, identity, tag, and restriction. "
    "Always keep the fixed subject and add a compact minimum of visible grounding: choose at least two useful details from action/pose, expression, environment, props, composition/camera, time/weather, or lighting. "
    "Use the selected output profile and let the model decide which other visible dimensions need detail for this image; "
    "add only compatible clothing, environment, props, composition, camera, time, weather, or lighting when useful. "
    "Keep one coherent single image and return the prompt without alternatives or explanation; do not force a checklist, storyboard, or collage."
)
_INDEPENDENT_BATCH_LOCK = threading.Lock()
_independent_batch_sequence = 0
_MIN_INDEPENDENT_BATCH_TEMPERATURE = 1.25
_MAX_BATCH_SIMILARITY = 0.62
_MIN_DISTINCTIVE_OVERLAP = 0.40
_MIN_DISTINCTIVE_COMMON = 4
_MAX_DIVERSITY_RETRIES = 3
_MAX_RESPONSE_RETRIES = 1
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
_DIVERSITY_CONCEPT_ALIASES = {
    "stand": "stand", "standing": "stand", "wait": "stand", "waiting": "stand", "stroll": "walk",
    "walk": "walk", "walking": "walk", "step": "walk", "stepping": "walk",
    "run": "run", "running": "run", "sprint": "run", "sprinting": "run",
    "sit": "sit", "sitting": "sit", "seated": "sit", "kneel": "kneel", "kneeling": "kneel",
    "hold": "hold", "holding": "hold", "carry": "hold", "carrying": "hold", "grip": "hold", "gripping": "hold",
    "look": "gaze", "looking": "gaze", "gaze": "gaze", "gazing": "gaze", "stare": "gaze", "staring": "gaze",
    "smile": "smile", "smiling": "smile", "grin": "smile", "grinning": "smile",
    "laugh": "laugh", "laughing": "laugh", "giggle": "laugh", "giggling": "laugh",
    "portrait": "portrait", "closeup": "portrait", "close_up": "portrait", "headshot": "portrait",
    "fullbody": "fullbody", "full_body": "fullbody", "wide_shot": "wide_shot", "long_shot": "wide_shot",
    "front_facing": "front_facing", "three_quarter": "three_quarter", "low_angle": "low_angle", "high_angle": "high_angle",
    "temple": "shrine", "shrine": "shrine", "sanctuary": "shrine", "shrine_gate": "shrine",
    "umbrella": "umbrella", "parasol": "umbrella", "rain_umbrella": "umbrella",
}
_RECENT_BATCH_OUTPUTS: dict[str, list[str]] = {}
_RECENT_BATCH_OUTPUTS_LOCK = threading.Lock()
_BATCH_CONTEXT = threading.local()
_INDEPENDENT_CREATIVE_LOCK = threading.Lock()
_independent_creative_sequence = 0
_INDEPENDENT_CREATIVE_FOCI = _VARIATION_DIMENSIONS
_INSPIRATION_TOPIC_POOL = (
    "日常生活", "校园时光", "节庆活动", "奇幻冒险", "科幻探索", "自然观察",
    "室内静物", "城市夜景", "旅行见闻", "运动瞬间", "职业场景", "传统文化",
    "海边度假", "森林秘境", "未来都市", "温馨居家",
)
_INSPIRATION_VARIATION_HINTS = (
    "捕捉动态动作与衣摆运动", "安排手部互动和一个叙事道具", "突出天气变化与空气透视",
    "采用低机位广角并强调前中后景", "采用近距离肖像构图并突出表情", "加入具有材质层次的环境细节",
    "使用逆光、轮廓光或彩色光源塑造氛围", "加入时间线索与独特的空间关系",
)


def _choose_variation_lenses(used: set[str] | None = None, count: int = 2) -> tuple[str, ...]:
    """Choose underused creative dimensions without a deterministic scene cycle."""
    pool = list(_VARIATION_DIMENSIONS)
    used = used if used is not None else set()
    available = [item for item in pool if item not in used] or pool
    chosen = random.SystemRandom().sample(available, min(max(1, int(count or 1)), len(available)))
    used.update(chosen)
    return tuple(chosen)


def _independent_batch_directive(sequence: int = 0, used_lenses: set[str] | None = None) -> str:
    """Create a compact directive; batch memory and underused lenses drive diversity."""
    global _independent_batch_sequence
    requested_sequence = int(sequence or 0)
    if requested_sequence > 0:
        focus_index = requested_sequence - 1
    else:
        with _INDEPENDENT_BATCH_LOCK:
            focus_index = _independent_batch_sequence
            _independent_batch_sequence += 1
    lenses = _choose_variation_lenses(used_lenses, count=2)
    nonce = uuid.uuid4().hex[:12]
    return (
        "Independent single-image batch item. " + _COMPLETE_PROMPT_CONTRACT + " "
        f"Optional underused variation lenses for this item: {', '.join(lenses)}. Use them as loose inspiration, never as a fixed template; "
        "choose fresh compatible details and avoid repeated concepts, actions, and props from the diversity ledger. Do not vary only style, quality words, synonyms, or tag order. "
        "Use static vocabulary only as a reference. "
        f"独立请求标识: {nonce}."
    )


def _independent_creative_directive(sequence: int = 0, used_lenses: set[str] | None = None) -> str:
    """Create the compact inline variation and conditional-completeness contract."""
    global _independent_creative_sequence
    requested_sequence = int(sequence or 0)
    if requested_sequence > 0:
        focus_index = requested_sequence - 1
    else:
        with _INDEPENDENT_CREATIVE_LOCK:
            focus_index = _independent_creative_sequence
            _independent_creative_sequence += 1
    lenses = _choose_variation_lenses(used_lenses, count=2)
    nonce = uuid.uuid4().hex[:12]
    return (
        "Independent single-image inline request. " + _COMPLETE_PROMPT_CONTRACT + " "
        f"Optional underused variation lenses: {', '.join(lenses)}. Reinterpret them freely and avoid repeated concepts from the diversity ledger. "
        "Use static vocabulary only as a reference. "
        f"Independent request id: {nonce}."
    )


def _distinctive_tokens(text: str, stable_source: str = "") -> set[str]:
    """Return content-bearing tokens, excluding the fixed subject and boilerplate."""
    stable = _canonical_diversity_tokens(stable_source)
    tokens = _canonical_diversity_tokens(text) - stable
    return {
        token for token in tokens
        if token not in _DIVERSITY_STOP_TOKENS and (len(token) >= 3 or "_" in token)
    }


def _prompt_similarity(left: str, right: str, stable_source: str = "") -> float:
    """Measure content overlap without requiring an embedding service."""
    stable = _canonical_diversity_tokens(stable_source)
    return _cosine(_canonical_diversity_tokens(left) - stable, _canonical_diversity_tokens(right) - stable)


def _canonical_diversity_tokens(text: str):
    tokens = _tokens(text)
    normalized = Counter()
    for token, count in tokens.items():
        normalized[_DIVERSITY_CONCEPT_ALIASES.get(token, token)] += count
    return normalized


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


_PNG_BATCH_CANCEL_EVENTS: dict[str, threading.Event] = {}
_PNG_BATCH_CANCEL_LOCK = threading.Lock()
# Backward-compatible test/integration handle. New UI jobs use their own
# tokenized events; callers that only clear this legacy event remain harmless.
_PNG_BATCH_CANCEL = threading.Event()


def _png_batch_cancel_event(cancel_id: str = "") -> tuple[str, threading.Event]:
    """Return the cancellation event for one UI job token."""
    key = str(cancel_id or "").strip() or f"direct:{threading.get_ident()}"
    with _PNG_BATCH_CANCEL_LOCK:
        event = _PNG_BATCH_CANCEL_EVENTS.setdefault(key, threading.Event())
    return key, event
_SERVER_QUEUE_WAKE = threading.Event()
_SERVER_QUEUE_CANCEL_EVENTS: dict[str, threading.Event] = {}
_SERVER_QUEUE_CANCEL_LOCK = threading.Lock()
_SERVER_QUEUE_THREAD: threading.Thread | None = None
_SERVER_QUEUE_START_LOCK = threading.Lock()


def _server_queue_cancel_event(batch_id: str) -> threading.Event:
    key = str(batch_id or "").strip()
    with _SERVER_QUEUE_CANCEL_LOCK:
        return _SERVER_QUEUE_CANCEL_EVENTS.setdefault(key, threading.Event())


def _release_server_queue_cancel_event(batch_id: str) -> None:
    key = str(batch_id or "").strip()
    records = DB.list_server_queue(key, 2000)
    if records and any(record["status"] in {"pending", "running"} for record in records):
        return
    with _SERVER_QUEUE_CANCEL_LOCK:
        _SERVER_QUEUE_CANCEL_EVENTS.pop(key, None)


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


def _parse_generation_settings(value) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _server_render_prompt(prompt: str, target: str, job_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render one queued prompt inside the current Forge process."""
    target = str(target or "none")
    if target == "none":
        return {}
    if target != "txt2img":
        raise RuntimeError("服务端队列目前只支持 txt2img；img2img 需要由队列任务提供 init image")

    # Reuse Forge's normal processing path. This keeps the queue alive when the
    # browser closes and avoids requiring the optional Forge HTTP API server.
    from contextlib import closing

    from modules import processing, shared
    from modules.call_queue import queue_lock

    settings = _parse_generation_settings((config or {}).get("generation_settings"))

    def _int(name: str, default: int, lower: int, upper: int) -> int:
        try:
            value = int(settings.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(lower, min(value, upper))

    def _float(name: str, default: float, lower: float, upper: float) -> float:
        try:
            value = float(settings.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(lower, min(value, upper))

    p = processing.StableDiffusionProcessingTxt2Img(
        sd_model=getattr(shared, "sd_model", None),
        outpath_samples=getattr(shared.opts, "outdir_txt2img_samples", None),
        outpath_grids=getattr(shared.opts, "outdir_txt2img_grids", None),
        prompt=str(prompt),
        negative_prompt=str(settings.get("negative_prompt") or ""),
        styles=(settings.get("styles") if isinstance(settings.get("styles"), list) else []),
        seed=_int("seed", -1, -1, 2**63 - 1),
        sampler_name=str(settings.get("sampler_name") or "Euler"),
        batch_size=_int("batch_size", 1, 1, 8),
        n_iter=_int("batch_count", _int("n_iter", 1, 1, 128), 1, 128),
        steps=_int("steps", 20, 1, 150),
        cfg_scale=_float("cfg_scale", 6, 0.1, 30),
        distilled_cfg_scale=_float("distilled_cfg_scale", 3.5, 0, 30),
        width=_int("width", 1024, 64, 4096),
        height=_int("height", 1024, 64, 4096),
        enable_hr=bool(settings.get("enable_hr", False)),
        denoising_strength=_float("denoising_strength", 0.75, 0, 1),
        hr_scale=_float("hr_scale", 2, 1, 4),
        hr_upscaler=(str(settings.get("hr_upscaler") or "").strip() or None),
        hr_second_pass_steps=_int("hr_second_pass_steps", 0, 0, 150),
        hr_resize_x=_int("hr_resize_x", 0, 0, 4096),
        hr_resize_y=_int("hr_resize_y", 0, 0, 4096),
        hr_cfg=_float("hr_cfg", 1, 0, 30),
        hr_distilled_cfg=_float("hr_distilled_cfg", 3.5, 0, 30),
        do_not_save_samples=False,
        do_not_save_grid=True,
    )
    p.force_task_id = f"server-queue-{job_id}"
    p.is_api = False
    # Queue jobs intentionally use the core txt2img path. Script arguments are
    # not available in the cache record and should not be guessed here.
    p.scripts = None
    p.script_args = []
    task_id = f"server-queue-{job_id}"
    try:
        from modules import progress

        progress.add_task_to_queue(task_id)
    except Exception:
        progress = None
    with queue_lock:
        shared.state.begin(job=task_id)
        if progress is not None:
            progress.start_task(task_id)
        try:
            with closing(p):
                processed = processing.process_images(p)
        finally:
            if progress is not None:
                progress.finish_task(task_id)
            shared.state.end()
            total_tqdm = getattr(shared, "total_tqdm", None)
            if total_tqdm is not None:
                total_tqdm.clear()

    images = []
    try:
        from io import BytesIO

        for image in (processed.images or []):
            if isinstance(image, str):
                images.append(image)
                continue
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            images.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
    except Exception:
        LOGGER.exception("failed to encode server queue images")
    return {"images": images, "info": processed.js() if processed else ""}


def _server_queue_prepare_prompt(job, config, history, lenses, cancel_event):
    if bool(config.get("direct_prompt")):
        prompt = str(job.get("request") or "").strip()
        if not prompt:
            raise RuntimeError("直接生图队列中的 Prompt 为空")
        return prompt, "直接使用已处理 Prompt", True
    generated, _system, status = _generate(
        job["request"], "", config.get("preset", "Danbooru Tags"), config.get("system_override", ""),
        config.get("base_model", "Auto / checkpoint default"), config.get("safety", "SFW"),
        config.get("nsfw_injection", ""), config.get("user_instruction", ""),
        config.get("provider", "OpenAI Compatible"), config.get("endpoint", ""), config.get("model", ""), "",
        float(config.get("temperature", 1.0)), int(config.get("timeout", 90) or 90),
        int(config.get("max_tokens", 8096) or 8096), bool(config.get("send_temperature", True)),
        bool(config.get("remove_bad", True)), config.get("remove_terms", ""), bool(config.get("shuffle", False)),
        bool(config.get("spaces", False)), int(config.get("max_tags", 0) or 0),
        config.get("structured_mode", "Plain Prompt"), int(config.get("region_count", 1) or 1),
        float(config.get("save_score", 0) or 0), False,
        "server_queue", f"server_queue:{job['batch_id']}:{job['position']}", True, cancel_event,
        _independent_batch_directive(job["position"], lenses), batch_history=history,
        preserve_inference_settings=True,
    )
    return generated, status, False


def _server_queue_worker() -> None:
    batch_histories: dict[str, list[str]] = {}
    batch_lenses: dict[str, set[str]] = {}
    while True:
        job = DB.claim_server_queue_job()
        if not job:
            _SERVER_QUEUE_WAKE.wait(1.0)
            _SERVER_QUEUE_WAKE.clear()
            continue
        cancel_event = _server_queue_cancel_event(job["batch_id"])
        if cancel_event.is_set():
            DB.update_server_queue_job(job["id"], "cancelled", error="服务端队列已取消")
            _release_server_queue_cancel_event(job["batch_id"])
            continue
        config = dict(job.get("config") or {})
        try:
            batch_id = str(job.get("batch_id") or "")
            if batch_id not in batch_histories and len(batch_histories) >= 32:
                stale_batch = next(iter(batch_histories))
                batch_histories.pop(stale_batch, None)
                batch_lenses.pop(stale_batch, None)
            history = batch_histories.setdefault(batch_id, [])
            lenses = batch_lenses.setdefault(batch_id, set())
            generated, status, direct_prompt = _server_queue_prepare_prompt(
                job, config, history, lenses, cancel_event,
            )
            if cancel_event.is_set():
                DB.update_server_queue_job(job["id"], "cancelled", error="服务端队列已取消")
                _release_server_queue_cancel_event(job["batch_id"])
                continue
            if not generated:
                raise RuntimeError(status or "LLM 未返回 Prompt")
            if not direct_prompt:
                history.append(generated)
                del history[:-_DIVERSITY_MEMORY_SIZE]
            if not direct_prompt and bool(config.get("cache_result", True)):
                DB.save_prompt(generated, "", config.get("preset", "Danbooru Tags"), config.get("base_model", ""), 0, job["request"], score_source="unrated", source_kind="server_queue", source_ref=f"server_queue:{job['id']}", dedupe=True)
            render_result = _server_render_prompt(generated, job.get("target", "none"), job["id"], config)
            DB.update_server_queue_job(job["id"], "completed", prompt=generated, images=render_result.get("images", []))
        except Exception as error:
            LOGGER.exception("server queue job failed: %s", job["id"])
            DB.update_server_queue_job(job["id"], "cancelled" if cancel_event.is_set() else "error", error=str(error))
        finally:
            _release_server_queue_cancel_event(job["batch_id"])


def _ensure_server_queue_worker() -> None:
    global _SERVER_QUEUE_THREAD
    with _SERVER_QUEUE_START_LOCK:
        if _SERVER_QUEUE_THREAD and _SERVER_QUEUE_THREAD.is_alive():
            return
        DB.recover_server_queue()
        _SERVER_QUEUE_THREAD = threading.Thread(target=_server_queue_worker, name="llm-prompt-studio-server-queue", daemon=True)
        _SERVER_QUEUE_THREAD.start()


def _enqueue_server_queue(payload: dict[str, Any], cancel_event=None) -> dict[str, Any]:
    if cancel_event is not None and cancel_event.is_set():
        raise ValueError("已取消入队")
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
        "temperature": connection["temperature"],
        "timeout": connection["timeout"], "max_tokens": connection["max_tokens"], "send_temperature": connection["send_temperature"],
        "provider": connection["provider"], "endpoint": connection["endpoint"], "model": connection["model"],
        "system_override": workflow["system_override"], "nsfw_injection": workflow["nsfw_injection"], "user_instruction": workflow["user_instruction"],
        **{key: workflow[key] for key in ("remove_bad", "remove_terms", "shuffle", "spaces", "max_tags", "structured_mode", "region_count", "save_score")},
        "cache_result": True, "target": target, "direct_prompt": False, "generation_settings": {}, "source": "",
    }
    merged.update({key: value for key, value in config.items() if key in merged and key not in {"provider", "endpoint", "model"}})
    if merged["direct_prompt"] and target != "txt2img":
        raise ValueError("直接 Prompt 队列只支持 txt2img 生图")
    merged["preset"], _preset_aligned = _aligned_preset(merged["preset"], merged["base_model"])
    batch_id = uuid.uuid4().hex
    if cancel_event is not None:
        with _SERVER_QUEUE_CANCEL_LOCK:
            _SERVER_QUEUE_CANCEL_EVENTS[batch_id] = cancel_event
    count = DB.enqueue_server_queue(batch_id, [
        {"request": request, "position": index, "target": merged["target"], "config": merged}
        for index, request in enumerate(requests, start=1)
    ])
    if cancel_event is not None and cancel_event.is_set():
        DB.cancel_server_queue(batch_id)
        _release_server_queue_cancel_event(batch_id)
        return _server_queue_snapshot(batch_id) | {"queued": 0}
    _ensure_server_queue_worker()
    _SERVER_QUEUE_WAKE.set()
    return _server_queue_snapshot(batch_id) | {"queued": count}


def _server_queue_html(snapshot: dict[str, Any]) -> str:
    rows = []
    for item in snapshot.get("jobs", []):
        config = item.get("config") or {}
        settings = _parse_generation_settings(config.get("generation_settings"))
        parameter_text = ""
        if settings:
            parameter_text = (
                f"{settings.get('width', '?')}×{settings.get('height', '?')} · "
                f"{settings.get('steps', '?')} 步 · CFG {settings.get('cfg_scale', '?')} · "
                f"{settings.get('sampler_name', '?')}"
            )
        image_html = "".join(
            f"<img class='lps-queue-image' src='{html.escape(str(image if str(image).startswith('data:') else 'data:image/png;base64,' + str(image)), quote=True)}' alt='generated image'>"
            for image in (item.get("images") or [])
            if str(image or "").strip()
        )
        rows.append(
            "<div class='lps-server-queue-row'>"
            f"<span>{int(item.get('position', 0))}</span>"
            f"<b>{html.escape(str(item.get('status', '')))}</b>"
            f"<code>{html.escape(str(item.get('prompt') or item.get('request') or ''))}</code>"
            f"<small>{html.escape(parameter_text)}</small>"
            f"<small>{html.escape(str(item.get('error') or ''))}</small>"
            f"<div class='lps-queue-images'>{image_html}</div>"
            "</div>"
        )
    return "".join(rows) or "<div class='lps-auto-loop-empty'>暂无服务端队列记录。</div>"


def _server_queue_start_ui(source_text: str, target: str, generation_settings=None):
    try:
        snapshot = _enqueue_server_queue({
            "source_text": source_text,
            "target": target,
            "config": {"generation_settings": _parse_generation_settings(generation_settings)},
        })
        return snapshot["batch_id"], snapshot["status"], _server_queue_html(snapshot)
    except Exception as error:
        return "", f"服务端队列提交失败：{_safe_error(error)}", ""


def _server_queue_refresh_ui(batch_id: str):
    snapshot = _server_queue_snapshot(batch_id)
    return snapshot["status"], _server_queue_html(snapshot)


def _server_queue_cancel_ui(batch_id: str):
    _server_queue_cancel_event(batch_id).set()
    DB.cancel_server_queue(batch_id)
    _release_server_queue_cancel_event(batch_id)
    snapshot = _server_queue_snapshot(batch_id)
    return snapshot["status"] + "；已请求取消", _server_queue_html(snapshot)


CACHE_PAGE_SIZE = 50


def _as_rows(records: list[dict[str, Any]]) -> list[list[Any]]:
    def summary(value):
        text = " ".join(str(value or "").split())
        return text[:597] + "…" if len(text) > 600 else text
    return [[
        row.get("visible_position", ""), row["id"], row["output_mode"], row["base_model"],
        summary(row["prompt"]),
        row.get("source_kind", ""),
    ] for row in records[:CACHE_PAGE_SIZE]]


def _cache_choices(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    choices = []
    for row in records[:CACHE_PAGE_SIZE]:
        preview = " ".join(str(row.get("prompt") or "").split())
        if len(preview) > 72:
            preview = preview[:69] + "..."
        choices.append((f"#{row['visible_position']} · ID {row['id']} · {preview}", str(row["id"])))
    return choices


def _cache_records(query: str = "", min_score: float = 0, output_mode: str = "全部", base_model: str = "全部") -> list[dict[str, Any]]:
    normalized_output_mode = _canonical_preset(output_mode)
    normalized_base_model = _canonical_base_model(base_model)
    return DB.list_prompts(
        str(query or ""), limit=None,
        min_score=float(min_score or 0),
        output_mode="" if normalized_output_mode == "全部" else normalized_output_mode,
        base_model="" if normalized_base_model == "全部" else normalized_base_model,
    )


def _refresh_cache(query: str = "", min_score: float = 0, output_mode: str = "全部", base_model: str = "全部", page=1):
    return _cache_page_update(query, min_score, output_mode, base_model, page)[:3]


def _cache_page_update(query="", min_score=0, output_mode="全部", base_model="全部", page=1):
    records = _cache_records(query, min_score, output_mode, base_model)
    pages = max(1, (len(records) + CACHE_PAGE_SIZE - 1) // CACHE_PAGE_SIZE)
    page = max(1, min(int(page or 1), pages))
    visible = records[(page - 1) * CACHE_PAGE_SIZE:page * CACHE_PAGE_SIZE]
    message = f"匹配 {len(records)} 条 · 第 {page}/{pages} 页 · 每页 {CACHE_PAGE_SIZE} 条。点击行编辑全文。"
    return gr.update(value=_as_rows(visible)), gr.update(choices=_cache_choices(visible), value=[]), message, page


def _filtered_cache_updates(query: str = "", min_score: float = 0, output_mode: str = "全部", base_model: str = "全部", selected=None):
    records = _cache_records(query, min_score, output_mode, base_model)
    available_ids = {str(record["id"]) for record in records}
    retained_selection = [value for value in _selected_values(selected) if value in available_ids]
    return (
        gr.update(value=_as_rows(records)),
        gr.update(choices=_cache_choices(records), value=retained_selection),
    )


CACHE_EDIT_FIELDS = {"正向提示词": "prompt", "负面提示词": "negative_prompt", "源标签": "tags", "格式": "output_mode", "目标模型": "base_model"}
CACHE_EDIT_OPERATIONS = {"查找替换": "replace", "追加": "append", "前置": "prepend", "整体设为": "set"}


def _preview_cache_edit(scope, record_id, field, operation, find, value, query, min_score, output_mode, base_model):
    config = [scope, record_id, field, operation, find, value, query, min_score, output_mode, base_model]
    if scope == "当前记录":
        try:
            record = DB.get_prompt(int(record_id))
        except (TypeError, ValueError):
            record = None
        records = [record] if record else []
    else:
        records = _cache_records(query, min_score, output_mode, base_model)
    if not records:
        return "没有可修改的记录，请选择记录或调整筛选。", {}
    if operation == "查找替换" and not find:
        return "查找内容不能为空。", {}
    if field in {"格式", "目标模型"} and operation != "整体设为":
        return "格式和目标模型仅支持“整体设为”。", {}
    target = CACHE_EDIT_FIELDS[field]
    value = _canonical_preset(value) if target == "output_mode" else _canonical_base_model(value) if target == "base_model" else value
    previews = []
    affected = 0
    for record in records:
        before = str(record[target] or "")
        after = (before.replace(find, value) if operation == "查找替换" else
                 before + value if operation == "追加" else value + before if operation == "前置" else value)
        if target == "prompt" and not after.strip():
            return "修改会产生空提示词，请调整操作。", {}
        if before != after:
            affected += 1
            if len(previews) < 3:
                previews.append(f"#{record['id']}: {' '.join(after.split())[:160]}")
    message = f"范围：{scope}，共 {len(records)} 条；实际变化 {affected} 条（包含所有分页）。\n" + "\n".join(previews)
    return message, {"config": config, "records": records}


def _apply_cache_edit(preview, scope, record_id, field, operation, find, value, query, min_score, output_mode, base_model):
    config = [scope, record_id, field, operation, find, value, query, min_score, output_mode, base_model]
    if not preview or preview.get("config") != config:
        return "操作或筛选已变化，请先预览修改。", gr.update(), gr.update(), {}
    value = _canonical_preset(value) if field == "格式" else _canonical_base_model(value) if field == "目标模型" else value
    try:
        count = DB.edit_prompts(preview["records"], CACHE_EDIT_FIELDS[field], CACHE_EDIT_OPERATIONS[operation], find, value)
    except ValueError as error:
        return str(error), gr.update(), gr.update(), {}
    table, choices = _filtered_cache_updates(query, min_score, output_mode, base_model)
    return f"已修改 {count} 条记录；列表已返回第一页。重新点击记录可载入最新内容。", table, choices, {}


def _clear_cache_filters():
    table, choices, status = _refresh_cache()
    return "", 0, "全部", "全部", table, choices, status


def _safe_error(error: Exception) -> str:
    return html.escape(str(error), quote=False)


def _ranbooru_link_settings() -> dict[str, Any]:
    stored = DB.get_setting("ranbooru_link_v1", {}) or {}
    values = {**RANBOORU_LINK_DEFAULTS, **(stored if isinstance(stored, dict) else {})}
    values["tag_output_mode"] = _canonical_preset(values["tag_output_mode"])
    values["natural_output_mode"] = _canonical_preset(values["natural_output_mode"])
    values["tag_base_model"] = _canonical_base_model(values["tag_base_model"])
    values["natural_base_model"] = _canonical_base_model(values["natural_base_model"])
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
        "tag_output_mode": _canonical_preset(tag_output_mode),
        "tag_base_model": _canonical_base_model(tag_base_model),
        "natural_output_mode": _canonical_preset(natural_output_mode),
        "natural_base_model": _canonical_base_model(natural_base_model),
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
    values["preset"] = _canonical_preset(values["preset"])
    values["base_model"] = _canonical_base_model(values["base_model"])
    values["structured_mode"] = _canonical_output_mode(values["structured_mode"])
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
        "region_count": (1, 8), "max_tags": (0, 200),
    }
    float_limits = {
        "save_score": (0, 10),
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


def _save_workflow_settings(
    preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    structured_mode, region_count, remove_bad, remove_terms, shuffle, spaces, max_tags,
    save_score, cache_result,
    batch_skip_existing, batch_skip_failed,
    wd_endpoint, wd_model, wd_threshold, wildcard_path,
):
    return _save_workflow_values({
        "preset": _canonical_preset(preset), "system_override": system_override,
        "base_model": _canonical_base_model(base_model),
        "safety": safety, "nsfw_injection": nsfw_injection, "user_instruction": user_instruction,
        "structured_mode": _canonical_output_mode(structured_mode), "region_count": int(region_count or 1),
        "remove_bad": bool(remove_bad), "remove_terms": remove_terms, "shuffle": bool(shuffle),
        "spaces": bool(spaces), "max_tags": int(max_tags or 0),
        "save_score": float(save_score or 0), "cache_result": bool(cache_result),
        "batch_skip_existing": bool(batch_skip_existing), "batch_skip_failed": bool(batch_skip_failed),
        "wd_endpoint": wd_endpoint, "wd_model": wd_model,
        "wd_threshold": float(wd_threshold or 0), "wildcard_path": wildcard_path,
    })


def _workflow_component_values(values: dict[str, Any]) -> list[Any]:
    return [values[key] for key in [
        "preset", "system_override", "base_model", "safety", "nsfw_injection", "user_instruction",
        "structured_mode", "region_count", "remove_bad", "remove_terms", "shuffle", "spaces", "max_tags",
        "save_score", "cache_result",
        "batch_skip_existing", "batch_skip_failed",
        "wd_endpoint", "wd_model", "wd_threshold", "wildcard_path",
    ]]


def _reset_workflow_settings():
    DB.delete_setting("workflow_settings_v1")
    return (*_workflow_component_values(WORKFLOW_DEFAULTS), "已恢复默认工作参数。下次打开界面也会使用默认值。")


def _connection_store() -> dict[str, Any]:
    stored = DB.get_setting("llm_connections_v2", {}) or {}
    if isinstance(stored, dict) and isinstance(stored.get("providers"), dict):
        try:
            stored_version = int(stored.get("version") or LLM_CONNECTION_SETTINGS_VERSION)
        except (TypeError, ValueError):
            stored_version = LLM_CONNECTION_SETTINGS_VERSION
        providers = {
            _canonical_provider(provider): values
            for provider, values in stored["providers"].items()
            if _canonical_provider(provider) in PROVIDER_PROFILES and isinstance(values, dict)
        }
        active_provider = _canonical_provider(stored.get("active_provider") or DEFAULT_LLM_SETTINGS["provider"])
        normalized = {
            "version": stored_version,
            "active_provider": active_provider if active_provider in PROVIDER_PROFILES else DEFAULT_LLM_SETTINGS["provider"],
            "providers": providers,
        }
        if normalized != stored:
            DB.set_setting("llm_connections_v2", normalized)
        DB.delete_setting("llm_connection")
        return normalized
    legacy = DB.get_setting("llm_connection", {}) or {}
    if isinstance(legacy, dict) and legacy:
        provider = _canonical_provider(legacy.get("provider") or DEFAULT_LLM_SETTINGS["provider"])
        if provider not in PROVIDER_PROFILES:
            provider = DEFAULT_LLM_SETTINGS["provider"]
        migrated = {
            key: legacy[key]
            for key in (
                "endpoint", "model", "temperature", "timeout", "max_tokens", "send_temperature", "retry_count", "thinking_enabled", "thinking_budget", "reasoning_effort",
                "fallback_provider", "fallback_endpoint", "fallback_model", "weights_path", "model_version",
            )
            if key in legacy
        }
        stored = {
            "version": LLM_CONNECTION_SETTINGS_VERSION,
            "active_provider": provider,
            "providers": {provider: migrated},
        }
        DB.set_setting("llm_connections_v2", stored)
        DB.delete_setting("llm_connection")
        return stored
    provider = DEFAULT_LLM_SETTINGS["provider"]
    return {"version": 2, "active_provider": provider, "providers": {}}


def _connection_settings(provider: str | None = None) -> dict[str, Any]:
    store = _connection_store()
    provider = _canonical_provider(provider or store.get("active_provider") or DEFAULT_LLM_SETTINGS["provider"])
    if provider not in PROVIDER_PROFILES:
        provider = DEFAULT_LLM_SETTINGS["provider"]
    profile = get_provider_profile(provider)
    saved = store.get("providers", {}).get(provider, {})
    if not isinstance(saved, dict):
        saved = {}
    try:
        stored_version = int(store.get("version") or 0)
    except (TypeError, ValueError):
        stored_version = 0
    try:
        temperature = max(0.0, min(float(saved.get("temperature", DEFAULT_LLM_SETTINGS["temperature"])), 2.0))
    except (TypeError, ValueError):
        temperature = DEFAULT_LLM_SETTINGS["temperature"]
    try:
        timeout = max(5, min(int(saved.get("timeout", DEFAULT_LLM_SETTINGS["timeout"])), 600))
    except (TypeError, ValueError):
        timeout = DEFAULT_LLM_SETTINGS["timeout"]
    migrated_max_tokens = False
    try:
        saved_max_tokens = saved.get("max_tokens", DEFAULT_LLM_SETTINGS["max_tokens"])
        # Older releases used 1024 or 4096 as the implicit default; migrate those values only.
        if stored_version < LLM_CONNECTION_SETTINGS_VERSION and int(saved_max_tokens) in {1024, 4096}:
            saved_max_tokens = DEFAULT_LLM_SETTINGS["max_tokens"]
            migrated_max_tokens = True
        max_tokens = max(0, min(int(saved_max_tokens), 262144))
    except (TypeError, ValueError):
        max_tokens = DEFAULT_LLM_SETTINGS["max_tokens"]
    try:
        retry_count = max(0, min(int(saved.get("retry_count", DEFAULT_LLM_SETTINGS["retry_count"])), 5))
    except (TypeError, ValueError):
        retry_count = DEFAULT_LLM_SETTINGS["retry_count"]
    thinking_enabled = bool(saved.get("thinking_enabled", DEFAULT_LLM_SETTINGS["thinking_enabled"]))
    try:
        thinking_budget = max(0, min(int(saved.get("thinking_budget", DEFAULT_LLM_SETTINGS["thinking_budget"])), 262144))
    except (TypeError, ValueError):
        thinking_budget = DEFAULT_LLM_SETTINGS["thinking_budget"]
    reasoning_effort = str(saved.get("reasoning_effort") or "").strip()
    fallback_model = str(saved.get("fallback_model") or "").strip()
    fallback_provider = _canonical_provider(saved.get("fallback_provider") or (provider if fallback_model else ""))
    if fallback_provider not in PROVIDER_PROFILES:
        fallback_provider = provider if fallback_model else ""
    fallback_endpoint = ""
    if fallback_model:
        fallback_endpoint = str(
            saved.get("fallback_endpoint")
            or (saved.get("endpoint") if fallback_provider == provider else get_provider_profile(fallback_provider)["default_endpoint"])
            or ""
        ).strip()
    if migrated_max_tokens:
        providers = dict(store.get("providers", {}))
        migrated = dict(saved)
        migrated["max_tokens"] = max_tokens
        providers[provider] = migrated
        DB.set_setting(
            "llm_connections_v2",
            {"version": LLM_CONNECTION_SETTINGS_VERSION, "active_provider": provider, "providers": providers},
        )
    return {
        "provider": provider,
        "endpoint": str(saved.get("endpoint") or profile["default_endpoint"]),
        "model": str(saved.get("model") or ""),
        "temperature": temperature,
        "top_p": saved.get("top_p"),
        "top_k": saved.get("top_k"),
        "timeout": timeout,
        "max_tokens": max_tokens,
        "send_temperature": bool(saved.get("send_temperature", profile["send_temperature"])),
        "retry_count": retry_count,
        "thinking_enabled": thinking_enabled,
        "thinking_budget": thinking_budget,
        "reasoning_effort": reasoning_effort,
        "fallback_provider": fallback_provider,
        "fallback_endpoint": fallback_endpoint,
        "fallback_model": fallback_model,
        "weights_path": str(saved.get("weights_path") or ""),
        "model_version": str(saved.get("model_version") or ""),
    }


def _credential_status(provider: str, endpoint: str) -> str:
    if CREDENTIALS.has_matching(provider, endpoint):
        return "已找到该 Provider 与 URL 对应的服务端 API Key。"
    if get_provider_profile(provider).get("requires_api_key"):
        return "该 Provider 需要 API Key；保存后输入框可留空。"
    return "该 Provider 默认不要求 API Key；如代理服务要求认证仍可填写。"


def _supports_top_k(provider):
    return _canonical_provider(provider) not in {"OpenAI", "OpenAI Chat Completions", "DeepSeek"}


def _fallback_status(settings):
    if not settings.get("fallback_model"):
        return "备用模型已关闭。"
    provider = settings["fallback_provider"]
    endpoint = settings["fallback_endpoint"]
    credential = _credential_status(provider, endpoint)
    return f"备用连接：{provider} / {settings['fallback_model']}。{credential}"


def _load_fallback_provider_settings(provider):
    provider = _canonical_provider(provider)
    if provider not in PROVIDER_PROFILES:
        return "", gr.update(value=""), "备用模型已关闭。"
    settings = _connection_settings(provider)
    return settings["endpoint"], gr.update(value=settings["model"]), _credential_status(provider, settings["endpoint"])


def _load_provider_settings(provider):
    settings = _connection_settings(provider)
    model_choices = [settings["model"]] if settings["model"] else []
    return (
        settings["endpoint"], gr.update(choices=model_choices, value=settings["model"]),
        settings["fallback_provider"], settings["fallback_endpoint"],
        gr.update(value=settings["fallback_model"]),
        settings["weights_path"], settings["model_version"], settings["temperature"], settings["timeout"],
        settings["max_tokens"], settings["send_temperature"], settings["retry_count"], settings["thinking_enabled"], settings["thinking_budget"], settings["reasoning_effort"],
        _credential_status(settings["provider"], settings["endpoint"]) + " " + _fallback_status(settings),
        settings["top_p"], gr.update(value=settings["top_k"], visible=_supports_top_k(provider)),
    )


def _load_active_connection_settings():
    settings = _connection_settings()
    model_choices = [settings["model"]] if settings["model"] else []
    return (
        settings["provider"], settings["endpoint"], gr.update(choices=model_choices, value=settings["model"]),
        settings["fallback_provider"], settings["fallback_endpoint"],
        gr.update(value=settings["fallback_model"]),
        settings["weights_path"], settings["model_version"], settings["temperature"],
        settings["timeout"], settings["max_tokens"], settings["send_temperature"], settings["retry_count"], settings["thinking_enabled"], settings["thinking_budget"], settings["reasoning_effort"],
        _credential_status(settings["provider"], settings["endpoint"]) + " " + _fallback_status(settings),
        settings["top_p"], gr.update(value=settings["top_k"], visible=_supports_top_k(settings["provider"])),
    )


def _fallback_request_kwargs(settings: dict[str, Any]) -> dict[str, str]:
    model = str(settings.get("fallback_model") or "").strip()
    if not model:
        return {"fallback_model": "", "fallback_provider": "", "fallback_endpoint": "", "fallback_api_key": ""}
    provider = _canonical_provider(settings.get("fallback_provider") or settings.get("provider"))
    endpoint = validate_endpoint(settings.get("fallback_endpoint") or settings.get("endpoint"))
    return {
        "fallback_model": model,
        "fallback_provider": provider,
        "fallback_endpoint": endpoint,
        "fallback_api_key": CREDENTIALS.resolve("", provider, endpoint),
    }


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
        f"已载入记录 #{record['id']}，可在下方编辑全文。",
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


def _select_cache_row_from_view(page, query, min_score, output_mode, base_model, evt: gr.SelectData):
    """Resolve a cache-row click without sending Dataframe state through Gradio."""
    try:
        page_number = max(1, int(page or 1))
    except (TypeError, ValueError, OverflowError):
        page_number = 1
    records = _cache_records(query, min_score, output_mode, base_model)
    visible = records[(page_number - 1) * CACHE_PAGE_SIZE:page_number * CACHE_PAGE_SIZE]
    index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    try:
        record_id = str(visible[int(index)]["id"])
    except (TypeError, ValueError, IndexError, KeyError):
        record_id = ""
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
    metadata = DB.get_prompt(parsed_id) if parsed_id else {}
    if parsed_id and not metadata:
        return "记录已被删除，请刷新列表", gr.update(), gr.update()
    metadata = metadata or {}
    saved_id = DB.save_prompt(
        str(prompt).strip(), str(negative or ""), _canonical_preset(output_mode),
        _canonical_base_model(base_model), float(metadata.get("score", 0)),
        str(tags or ""), parsed_id,
        score_source=metadata.get("score_source", "unrated"),
        score_reason=metadata.get("score_reason", ""), score_model=metadata.get("score_model", ""),
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
            "可在缓存库筛选并编辑。"
        )
        if result["stale_natural"]:
            message += f" 跳过失效自然语言缓存 {result['stale_natural']} 条。"
        return message, table, choices
    except Exception as error:
        return f"Ranbooru 同步失败：{_safe_error(error)}", gr.update(), gr.update()


def _load_ranbooru_to_png_batch(
    database_path, content_mode, rating_filter, min_source_score, source_limit,
    tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
):
    """Build a shared batch from the selected Ranbooru cache slice.

    This keeps source metadata and lets the existing batch controls apply the
    selected model-aware Convert, Expand, or Polish operation to every row.
    """
    try:
        _save_ranbooru_link_settings(
            database_path, content_mode, rating_filter, min_source_score, source_limit,
            tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
        )
        result = _ranbooru_load(
            database_path, content_mode, rating_filter, min_source_score, source_limit,
            tag_output_mode, tag_base_model, natural_output_mode, natural_base_model,
        )
        records = []
        for index, item in enumerate(result["records"], start=1):
            prompt_text = str(item.get("prompt") or "").strip()
            if not prompt_text:
                continue
            variant = str(item.get("_ranbooru_variant") or "tags").strip().lower()
            prompt = {"positive": prompt_text}
            if variant == "natural":
                prompt["natural"] = prompt_text
            source_id = str(item.get("_ranbooru_id") or index)
            source_ref = str(item.get("source_ref") or f"ranbooru:{source_id}:{variant}")
            records.append({
                "record_id": f"ranbooru-{source_id}-{variant}",
                "source_identity": source_ref,
                "index": index,
                "image": {"filename": f"ranbooru-{source_id}.png"},
                "prompt": prompt,
                "booru": {"score": item.get("_ranbooru_score", 0), "rating": item.get("_ranbooru_rating", "")},
                "status": "源 Tag" if variant == "tags" else "源自然语言",
            })
        if not records:
            return _png_batch_json({"schema_version": PNG_BATCH_SCHEMA, "producer": {"name": "Ranbooru"}, "records": []}), "Ranbooru 缓存没有可处理的 Prompt。"
        payload = _normalize_png_batch_payload({
            "schema_version": PNG_BATCH_SCHEMA,
            "producer": {"name": "Ranbooru"},
            "records": records,
        })
        message = f"已载入 Ranbooru 缓存 {len(records)} 条到 LLM 批处理；请选择转换、扩写或润色并开始处理。"
        if result.get("truncated"):
            message += " 当前数量受读取上限限制。"
        return _png_batch_json(payload), message
    except Exception as error:
        return gr.update(), f"Ranbooru 批处理载入失败：{_safe_error(error)}"


def _cancel_batch_generation(task_id=""):
    with _BATCH_CONTROL_LOCK:
        studio_event = _STUDIO_CANCEL_EVENTS.get(str(task_id))
        if studio_event is not None:
            studio_event.set()
        if not task_id or (str(task_id) != _BATCH_ACTIVE_TASK_ID and studio_event is None):
            return "当前会话没有正在运行的批量任务，未发送取消请求。"
        if str(task_id) == _BATCH_ACTIVE_TASK_ID:
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


def _normalize_generation_count(value, default=8):
    if value is None or isinstance(value, bool) or not str(value).strip():
        return default
    try:
        number = float(value)
        return 0 if number == 0 else max(1, min(200, int(number)))
    except (TypeError, ValueError, OverflowError):
        return default


def _build_inspiration_sources(
    source_text: str,
    generation_count: int = 8,
    topic_pool: str = "",
    base_prompt: str = "",
    lock_known: bool = True,
    sample_static: bool = True,
    shared_request: str = "",
) -> tuple[list[str], dict[str, int]]:
    """Create varied batch requests when the user has no per-line request."""
    parsed, stats = _parse_batch_sources(source_text)
    # Zero previews one bounded block; only the explicit run starts repetition.
    count = _normalize_generation_count(generation_count) or 8
    if not parsed and str(shared_request or "").strip():
        parsed = [str(shared_request).strip()] * count
    anchor = str(base_prompt or "").strip()
    if parsed:
        if anchor:
            prefix = (
                "已有固定主体/角色 Tag：" + anchor + "。"
                + ("保留其中的身份、LoRA、权重、明确属性和安全限制；" if lock_known else "")
            )
            parsed = [prefix + "在此基础上完成本条创作要求：" + item for item in parsed]
        return parsed[:200], stats
    topics = [item.strip() for item in str(topic_pool or "").replace(",", "\n").splitlines() if item.strip()]
    topics = topics or list(_INSPIRATION_TOPIC_POOL)
    rng = random.SystemRandom()
    rng.shuffle(topics)
    used_terms: set[str] = set()
    sources: list[str] = []
    for index in range(count):
        topic = topics[index % len(topics)]
        variation_hint = _INSPIRATION_VARIATION_HINTS[index % len(_INSPIRATION_VARIATION_HINTS)]
        sampled: list[str] = []
        if sample_static:
            categorized = DB.wildcard_samples_by_category(1, exclude=used_terms, limit=8, max_categories=8)
            categories = list(categorized)
            rng.shuffle(categories)
            for category in categories:
                values = categorized.get(category) or []
                if values:
                    term = str(values[0]).strip()
                    if term and term.casefold() not in used_terms:
                        sampled.append(f"[{category}] {term}")
                        used_terms.add(term.casefold())
        reference = "；".join(sampled[:8])
        source = f"随机题材：{topic}；本条变化重点：{variation_hint}。请创作一条完整、可直接生图的单图 Prompt，主动补足动作、表情、服装、道具、环境、前中后景、镜头、时间天气和光线。"
        if reference:
            source += f" 静态词库本条抽样参考（可按语义取舍）：{reference}。"
        if anchor:
            source = (
                "已有固定主体/角色 Tag：" + anchor + "。"
                + ("必须保留身份、LoRA、权重、明确属性和安全限制；" if lock_known else "")
                + source
            )
        sources.append(source)
    return sources, {"ignored": 0, "duplicates": 0, "generated": len(sources)}


def _preview_batch_sources(source_text, skip_existing, preset, base_model, generation_count=8, topic_pool="", base_prompt="", lock_known=True, sample_static=True, shared_request=""):
    sources, stats = _build_inspiration_sources(source_text, generation_count, topic_pool, base_prompt, lock_known, sample_static, shared_request)
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
    if stats.get("generated"):
        message += f" 已按随机题材生成 {stats['generated']} 条内部创作要求。"
    if len(sources) > 200:
        message += " 预览仅显示前 200 条。"
    if _normalize_generation_count(generation_count) == 0:
        message = "无限生成；以下仅预览首轮，点击停止结束。 " + message
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
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
    query="", min_score=0, filter_output_mode="全部", filter_base_model="全部", existing_issues=None, task_id="",
    generation_count=8, topic_pool="", base_prompt="", lock_known=True, sample_static=True,
    shared_request="",
    sources_override=None,
):
    global _BATCH_ACTIVE_TASK_ID
    continuous = sources_override is None and _normalize_generation_count(generation_count) == 0
    sources, _parse_stats = _build_inspiration_sources(
        source_text, generation_count, topic_pool, base_prompt, lock_known, sample_static, shared_request,
    )
    if sources_override is not None:
        sources = list(sources_override)[:200]
    preset, base_model = _resolve_preset_model(preset, base_model)
    preset, _preset_aligned = _aligned_preset(preset, base_model)
    if not sources:
        yield _batch_output("没有可生成的批量任务。", gr.update(), gr.update(), existing_issues or [])
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
    batch_history = []
    used_lenses: set[str] = set()
    _BATCH_CONTEXT.history = batch_history
    issues = []
    result_rows = [] if continuous else [[index, source, "", "等待处理"] for index, source in enumerate(sources[:200], start=1)]
    total_label = "无限" if continuous else str(len(sources))
    consecutive_failures = 0
    random_sources = not _parse_batch_sources(source_text)[0] and not str(shared_request or "").strip()

    def source_iterator():
        while True:
            yield from sources
            if not continuous:
                return
            if random_sources:
                sources[:] = _build_inspiration_sources("", 8, topic_pool, base_prompt, lock_known, sample_static)[0]

    def cache_updates():
        if continuous:
            return gr.update(), gr.update()
        return _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)

    def set_result(index, prompt, state):
        if continuous:
            row = [index, source, str(prompt or ""), state]
            if result_rows and result_rows[-1][0] == index:
                result_rows[-1] = row
            else:
                result_rows.append(row)
                del result_rows[:-200]
        elif 1 <= index <= len(result_rows):
            result_rows[index - 1] = [index, sources[index - 1], str(prompt or ""), state]

    def flush_pending():
        nonlocal inserted, duplicates, score_updates
        if not pending:
            return
        stats = DB.save_prompts_batch(pending, dedupe=False, trust_score_metadata=True)
        inserted += stats["inserted"]
        duplicates += stats["duplicates"]
        score_updates += stats.get("updated", 0)
        pending.clear()

    try:
        if continuous and all(source in cached_sources for source in sources):
            yield _batch_output("无限生成已停止：全部输入已存在缓存，请关闭跳过已有缓存或修改输入。", gr.update(), gr.update(), [])
            return
        for index, source in enumerate(source_iterator(), start=1):
            if continuous:
                del issues[:-199]
            if _BATCH_CANCEL.is_set():
                for remaining_index, remaining_source in enumerate([] if continuous else sources[index - 1:], start=index):
                    set_result(remaining_index, "", "已取消")
                    issues.append({
                        "index": remaining_index, "source": remaining_source, "status": "已取消",
                        "reason": "批量任务已取消，尚未处理", "attempts": 0,
                    })
                flush_pending()
                table, choices = cache_updates()
                yield _batch_output(
                    f"任务已取消：处理 {index - 1}/{total_label}，新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}",
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
                    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
                    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
                    0, False, "", "", False, _BATCH_CANCEL,
                    _independent_batch_directive(index, used_lenses) + "\n" + (
                        f"这是同一批次中的独立任务第 {index}/{total_label} 条。"
                        "批次上下文中的结果只用于排除重复，不要复制其措辞、构图、动作或物品组合；"
                        "请根据原要求自由选择变化，不要把任何变化维度当作硬性模板。"
                        + ("已有主体/角色 Tag、LoRA、权重和明确属性是锁定内容，只补全动作、表情、道具、环境、构图、天气和光线。" if lock_known and str(base_prompt or "").strip() else "")
                    ),
                    preserve_inference_settings=True,
                )
            except Exception as error:
                generated, last_status = "", f"生成失败：{_safe_error(error)}"
            if _BATCH_CANCEL.is_set():
                set_result(index, "", "已取消")
                issues.append({
                    "index": index, "source": source, "status": "已取消",
                    "reason": "当前 LLM 请求已取消，结果未写入缓存", "attempts": 1,
                })
                for remaining_index, remaining_source in enumerate([] if continuous else sources[index:], start=index + 1):
                    set_result(remaining_index, "", "已取消")
                    issues.append({
                        "index": remaining_index, "source": remaining_source, "status": "已取消",
                        "reason": "批量任务已取消，尚未处理", "attempts": 0,
                    })
                flush_pending()
                table, choices = cache_updates()
                yield _batch_output(
                    f"任务已取消：完成 {index - 1}/{total_label}，新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}",
                    table, choices, issues, result_rows=result_rows,
                )
                return
            if generated:
                consecutive_failures = 0
                batch_history.append(generated)
                if continuous:
                    del batch_history[:-200]
                set_result(index, generated, "生成成功")
                pending.append({
                    "prompt": generated, "output_mode": preset, "base_model": base_model, "score": 0,
                    "score_source": "unrated", "score_reason": "批量生成未评分",
                    "score_model": "", "tags": source,
                })
            else:
                failed += 1
                consecutive_failures += 1
                set_result(index, "", last_status or "生成错误")
                issues.append({
                    "index": index, "source": source, "status": "生成错误",
                    "reason": last_status or "未返回结果", "attempts": 1,
                })
                if (not skip_failed or (continuous and consecutive_failures >= 3)) and not _BATCH_CANCEL.is_set():
                    for remaining_index, remaining_source in enumerate([] if continuous else sources[index:], start=index + 1):
                        set_result(remaining_index, "", "未处理")
                        issues.append({
                            "index": remaining_index, "source": remaining_source, "status": "未处理",
                            "reason": "前一项单次请求失败，批量任务已停止", "attempts": 0,
                        })
                    flush_pending()
                    table, choices = cache_updates()
                    yield _batch_output(
                        f"批量任务因错误停止：处理 {index}/{total_label}，新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}",
                        table, choices, issues, result_rows=result_rows,
                    )
                    return
            if pending or continuous or index == len(sources):
                flush_pending()
                table, choices = cache_updates()
                yield _batch_output(
                    f"进度 {index}/{total_label}：LLM 请求 {request_count}，新增 {inserted}，重复 {duplicates}，评分更新 {score_updates}，跳过 {skipped}，失败 {failed}" + (f"；最近状态：{last_status}" if last_status and not generated else ""),
                    table, choices, issues, result_rows=result_rows,
                )
                if continuous and not generated:
                    _BATCH_CANCEL.wait(0.5)
        table, choices = cache_updates()
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
            _BATCH_CONTEXT.history = None
            _BATCH_LOCK.release()


def _studio_continuous_result_stream(generator, destination, generation_settings, cancel_event):
    """Keep one render outstanding and stop producing when it fails or is cancelled."""
    last_submitted = 0
    last_prompt = ""
    for item in generator:
        rows = item[6].get("value", []) if isinstance(item[6], dict) else []
        successful = [row for row in rows if len(row) > 2 and row[2] and int(row[0]) > last_submitted]
        if rows:
            last_prompt = next((str(row[2]) for row in reversed(rows) if row[2]), last_prompt)
        if destination != "queue" or not successful or cancel_event.is_set() or "取消" in str(item[0]):
            yield (*item, last_prompt or gr.update(), gr.update(), gr.update(), gr.update())
            if cancel_event.is_set():
                if "取消" not in str(item[0]):
                    yield (f"无限生成已取消。{item[0]}", *item[1:], last_prompt or gr.update(),
                           gr.update(), gr.update(), gr.update())
                return
            continue
        for row in successful:
            if cancel_event.is_set():
                return
            try:
                snapshot = _enqueue_server_queue({
                    "requests": [str(row[2])], "target": "txt2img",
                    "config": {"direct_prompt": True, "cache_result": False,
                               "generation_settings": _parse_generation_settings(generation_settings)},
                }, cancel_event=cancel_event)
            except Exception as error:
                yield (f"无限生成已暂停，已生成结果保留在缓存。{item[0]}", *item[1:], last_prompt,
                       gr.update(), f"生图入队失败：{_safe_error(error)}", gr.update())
                return
            last_submitted = int(row[0])
            batch_id = snapshot["batch_id"]
            while True:
                yield (*item, last_prompt, batch_id, snapshot["status"], _server_queue_html(snapshot))
                if cancel_event.is_set():
                    status, log = _server_queue_cancel_ui(batch_id)
                    yield (f"无限生成已取消。{item[0]}", *item[1:], last_prompt, batch_id, status, log)
                    return
                counts = snapshot["counts"]
                if counts.get("error") or counts.get("cancelled") or not counts:
                    yield (f"无限生成已暂停：本轮生图未完成，Prompt 已缓存。{item[0]}",
                           *item[1:], last_prompt, batch_id, snapshot["status"], _server_queue_html(snapshot))
                    return
                if not counts.get("pending") and not counts.get("running"):
                    break
                if not cancel_event.wait(0.5):
                    snapshot = _server_queue_snapshot(batch_id)


def _studio_result_stream(generator, destination, generation_settings, cancel_event, continuous=False):
    """Share cache/retry behavior, then submit completed prompts on explicit request."""
    if continuous:
        yield from _studio_continuous_result_stream(generator, destination, generation_settings, cancel_event)
        return
    latest = None
    rows = []
    for item in generator:
        latest = item
        update = item[6]
        if isinstance(update, dict) and "value" in update:
            rows = update["value"] or []
        prompts = [str(row[2]) for row in rows if len(row) > 2 and row[2]]
        yield (*item, prompts[-1] if prompts else gr.update(), gr.update(), gr.update(), gr.update())
    if latest is None or destination != "queue" or cancel_event.is_set() or "取消" in str(latest[0]):
        return
    prompts = [str(row[2]) for row in rows if len(row) > 2 and row[2]]
    if not prompts:
        return
    try:
        snapshot = _enqueue_server_queue({
            "requests": prompts, "target": "txt2img",
            "config": {"direct_prompt": True, "cache_result": False,
                       "generation_settings": _parse_generation_settings(generation_settings)},
        }, cancel_event=cancel_event)
        yield (*latest, prompts[-1], snapshot["batch_id"], snapshot["status"], _server_queue_html(snapshot))
    except Exception as error:
        yield (*latest, prompts[-1], "", f"已保存到缓存；生图入队失败：{_safe_error(error)}", "")


def _studio_run(generator, destination, generation_settings, task_id, continuous=False):
    key = str(task_id)
    event = threading.Event()
    with _BATCH_CONTROL_LOCK:
        if key in _STUDIO_CANCEL_EVENTS:
            raise ValueError("当前面板已有生成任务")
        _STUDIO_CANCEL_EVENTS[key] = event
    try:
        yield from _studio_result_stream(generator, destination, generation_settings, event, continuous)
    finally:
        generator.close()
        with _BATCH_CONTROL_LOCK:
            _STUDIO_CANCEL_EVENTS.pop(key, None)


def _studio_generate(
    request, destination, generation_settings, task_id,
    source_text, skip_existing, skip_failed,
    preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
    query="", min_score=0, filter_output_mode="全部", filter_base_model="全部", existing_issues=None, batch_task_id="",
    generation_count=8, topic_pool="", base_prompt="", lock_known=True, sample_static=True,
):
    if destination not in {"cache", "queue"}:
        raise ValueError("请选择生成到缓存或生成并加入生图队列")
    yield from _studio_run(
        _batch_generate(
            source_text, skip_existing, skip_failed,
            preset, system_override, base_model, safety, nsfw_injection, user_instruction,
            provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
            remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
            query, min_score, filter_output_mode, filter_base_model, existing_issues, batch_task_id,
            generation_count, topic_pool, base_prompt, lock_known, sample_static, shared_request=request,
        ), destination, generation_settings, task_id, continuous=_normalize_generation_count(generation_count) == 0,
    )


def _studio_retry(destination, generation_settings, task_id, *retry_args):
    yield from _studio_run(_retry_batch_issues(*retry_args), destination, generation_settings, task_id)


def _stop_studio_generation(task_id, batch_id):
    status = _cancel_batch_generation(task_id)
    if batch_id:
        queue_status, log = _server_queue_cancel_ui(batch_id)
        return status, queue_status, log
    return status, gr.update(), gr.update()


def _retry_batch_issues(
    selected_sources, issue_records, skip_failed,
    preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
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
        provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
        remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
        query, min_score, filter_output_mode, filter_base_model, selected, task_id,
        sources_override=[str(item["source"]) for item in selected],
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
        categories = DB.wildcard_categories()
        matches = DB.wildcard_matches("")
        return (f"索引完成：更新 {files} 个文件，{len(categories)} 个分类，共 {terms} 个词条",
                gr.update(choices=matches, value=[]),
                gr.update(choices=["全部"] + categories, value="全部"))
    except Exception as error:
        return f"索引失败：{_safe_error(error)}", gr.update(), gr.update()


def _search_wildcards(query, category="全部"):
    matches = DB.wildcard_matches(query, category=None if category == "全部" else category)
    return gr.update(choices=matches, value=[])


def _apply_wildcard_selection(current_prompt, selected):
    """Append explicitly selected wildcard terms to the fixed prompt only."""
    current = str(current_prompt or "")
    terms = []
    for value in selected or []:
        term = str(value or "").strip()
        if term and term not in terms:
            terms.append(term)
    if not terms:
        return current_prompt, "未选择词条，固定 Prompt 未改变。"
    addition = ", ".join(terms)
    updated = f"{current}, {addition}" if current.strip() else addition
    return updated, f"已追加 {len(terms)} 个静态词条到固定 Prompt。"


def _save_llm_settings(
    provider, endpoint, model, api_key, fallback_model, weights_path, model_version,
    temperature, timeout, max_tokens, send_temperature, retry_count, thinking_enabled=False, thinking_budget=0, reasoning_effort="",
    top_p=None, top_k=None,
    fallback_provider=None, fallback_endpoint="", fallback_api_key="",
):
    provider = _canonical_provider(provider or DEFAULT_LLM_SETTINGS["provider"])
    if provider not in PROVIDER_PROFILES:
        return ("保存失败：不支持的 Provider。", gr.update(), gr.update(), gr.update(),
                gr.update(), gr.update(), gr.update(), gr.update())
    try:
        endpoint = validate_endpoint(endpoint)
        model = str(model or "").strip()
        if not model:
            raise ValueError("LLM model ID is required")
        fallback_model = str(fallback_model or "").strip()
        if fallback_provider is None or (not str(fallback_provider).strip() and fallback_model):
            fallback_provider = provider if fallback_model else ""
        fallback_provider = _canonical_provider(fallback_provider)
        if fallback_provider:
            if fallback_provider not in PROVIDER_PROFILES:
                raise ValueError("不支持的备用 Provider")
            fallback_endpoint = validate_endpoint(
                fallback_endpoint or (endpoint if fallback_provider == provider else get_provider_profile(fallback_provider)["default_endpoint"])
            )
            if not fallback_model:
                raise ValueError("启用备用服务时必须填写备用模型 ID")
            if (fallback_provider == provider and fallback_endpoint.rstrip("/") == endpoint.rstrip("/")
                    and fallback_model.casefold() == model.casefold()):
                fallback_provider = ""
                fallback_endpoint = ""
                fallback_model = ""
        else:
            fallback_endpoint = ""
            fallback_model = ""
        weights_path = str(weights_path or "").strip()
        model_version = str(model_version or "").strip()
        sampling = normalize_sampling(top_p, top_k)
        settings = {
            "endpoint": endpoint,
            "model": model,
            "fallback_provider": fallback_provider,
            "fallback_endpoint": fallback_endpoint,
            "fallback_model": fallback_model,
            "weights_path": weights_path,
            "model_version": model_version,
            "temperature": max(0.0, min(float(temperature), 2.0)),
            "timeout": max(5, min(int(timeout), 600)),
            "max_tokens": max(0, min(int(max_tokens), 262144)),
            "send_temperature": bool(send_temperature),
            "retry_count": max(0, min(int(retry_count), 5)),
            "thinking_enabled": bool(thinking_enabled),
            "thinking_budget": max(0, min(int(thinking_budget or 0), 262144)),
            "reasoning_effort": str(reasoning_effort or "").strip(),
            "top_p": sampling.get("top_p"),
            "top_k": sampling.get("top_k"),
        }
    except (TypeError, ValueError) as error:
        return (f"保存失败：{_safe_error(error)}", gr.update(), gr.update(), gr.update(),
                gr.update(), gr.update(), gr.update(), gr.update())
    store = _connection_store()
    providers = dict(store.get("providers", {}))
    providers[provider] = settings
    DB.set_setting("llm_connections_v2", {"version": LLM_CONNECTION_SETTINGS_VERSION, "active_provider": provider, "providers": providers})
    key_saved = CREDENTIALS.save(provider, endpoint, api_key)
    key_available = key_saved or CREDENTIALS.has_matching(provider, endpoint)
    fallback_key_saved = bool(fallback_provider) and CREDENTIALS.save(fallback_provider, fallback_endpoint, fallback_api_key)
    fallback_key_available = bool(fallback_provider) and (
        fallback_key_saved or CREDENTIALS.has_matching(fallback_provider, fallback_endpoint)
    )
    message = f"{provider} 设置已保存。模型 ID：{model}。URL、模型 ID 和生成参数下次会自动恢复。"
    if key_available:
        message += " API Key 已按 Provider 与 URL 保存在服务端，下次可留空。"
    elif get_provider_profile(provider).get("requires_api_key"):
        message += " 尚未保存 API Key，调用前必须填写。"
    else:
        message += " 当前未保存 API Key。"
    if weights_path:
        message += " 已登记本地模型权重路径；实际加载由本地 LLM 服务负责。"
    if fallback_model:
        fallback_label = fallback_model if fallback_provider == provider else f"{fallback_provider} / {fallback_model}"
        message += f" 主模型失败时回退到 {fallback_label}。"
        if fallback_key_available:
            message += " 备用 API Key 已按其服务商与 URL 独立保存。"
        elif get_provider_profile(fallback_provider).get("requires_api_key"):
            message += " 备用服务尚未保存 API Key。"
    return (
        message, gr.update(value=endpoint), gr.update(value=model), gr.update(value=fallback_provider),
        gr.update(value=fallback_endpoint), gr.update(value=fallback_model),
        gr.update(value=weights_path), gr.update(value=model_version),
    )


def _load_model_file(file_path):
    """Load a JSON model profile or register a local weight file path."""
    path_text = str(file_path or "").strip()
    if not path_text:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "未选择模型配置或权重文件。"
    path = Path(path_text)
    if not path.is_file():
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), f"加载失败：文件不存在：{path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return gr.update(value=str(path)), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), (
            f"已登记权重文件路径：{path}。该文件不是 Prompt Studio 配置 JSON，实际加载请由 Ollama、LM Studio 或其他本地服务完成。"
        )
    if not isinstance(payload, dict):
        return gr.update(value=str(path)), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "加载失败：模型配置 JSON 必须是对象。"
    model_payload = payload.get("model") if isinstance(payload.get("model"), dict) else payload
    provider = _canonical_provider(model_payload.get("provider") or "")
    endpoint = str(model_payload.get("endpoint") or model_payload.get("base_url") or "").strip()
    model = str(model_payload.get("model_id") or model_payload.get("model_name") or (model_payload.get("model") if isinstance(model_payload.get("model"), str) else "") or "").strip()
    fallback = str(model_payload.get("fallback_model") or model_payload.get("fallback") or "").strip()
    weights = str(model_payload.get("weights_path") or model_payload.get("weight_path") or "").strip()
    version = str(model_payload.get("model_version") or model_payload.get("version") or "").strip()
    if provider not in PROVIDER_PROFILES:
        provider = ""
    if endpoint:
        try:
            endpoint = validate_endpoint(endpoint)
        except ValueError as error:
            return gr.update(value=weights), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), f"加载失败：{_safe_error(error)}"
    choices = [model] if model else []
    status = f"已加载模型配置：{path.name}。请检查字段后点击‘保存并应用’。"
    return (
        gr.update(value=weights), gr.update(value=provider) if provider else gr.update(), gr.update(value=endpoint),
        gr.update(choices=choices, value=model) if model else gr.update(), gr.update(value=fallback),
        gr.update(value=version), status,
    )


def _discover_models(provider, endpoint, api_key, timeout):
    try:
        settings = _connection_settings(provider)
        resolved_key = CREDENTIALS.resolve(api_key, _canonical_provider(provider), endpoint)
        models = discover_provider_models(_canonical_provider(provider), endpoint, resolved_key, max(5, min(int(timeout or 30), 120)))
        selected = settings.get("model") if settings.get("model") in models else (models[0] if models else "")
        return gr.update(choices=models, value=selected), f"已发现 {len(models)} 个模型，可直接选择或手动输入模型 ID。"
    except Exception as error:
        current = str(_connection_settings(provider).get("model") or "").strip()
        return gr.update(choices=[current] if current else [], value=current), f"模型发现失败：{_safe_error(error)}；仍可手动填写模型 ID。"


def _discover_fallback_model(provider, endpoint, api_key, timeout):
    try:
        settings = _connection_settings(provider)
        resolved_key = CREDENTIALS.resolve(api_key, _canonical_provider(provider), endpoint)
        models = discover_provider_models(_canonical_provider(provider), endpoint, resolved_key, max(5, min(int(timeout or 30), 120)))
        selected = settings.get("fallback_model") if settings.get("fallback_model") in models else (models[0] if models else "")
        return gr.update(value=selected), f"已发现 {len(models)} 个备用模型。"
    except Exception as error:
        current = str(_connection_settings(provider).get("fallback_model") or "").strip()
        return gr.update(value=current), f"备用模型发现失败：{_safe_error(error)}；仍可手动填写。"


def _clear_llm_credentials(provider, endpoint):
    try:
        cleared = CREDENTIALS.clear(_canonical_provider(provider), validate_endpoint(endpoint))
    except ValueError as error:
        return f"清除失败：{_safe_error(error)}"
    return "已清除当前 Provider 与 URL 对应的 API Key。" if cleared else "当前连接没有已保存的 API Key。"


_LORA_TOKEN_RE = re.compile(r"<lora:[^>]+>", re.IGNORECASE)


def _immutable_technical_tokens(prompt: str) -> list[str]:
    """Extract LoRA tags and standalone trigger tokens without interpreting them."""
    text = str(prompt or "")
    loras = _LORA_TOKEN_RE.findall(text)
    # Natural-language words remain ordinary fixed prompt context. Compact
    # activation tokens are conventionally marked by an underscore, digit, or
    # technical separator; preserve those verbatim without semantic expansion.
    words = re.findall(r"(?<![\w<])([A-Za-z][A-Za-z0-9_.:-]*[_\d][A-Za-z0-9_.:-]*)(?![\w>])", text)
    return list(dict.fromkeys([*loras, *words]))


def _preserves_immutable_technical_tokens(source: str, candidate: str) -> bool:
    candidate_text = str(candidate or "")
    return all(token in candidate_text for token in _immutable_technical_tokens(source))


def _finalize_generated_prompt(
    result, preset, safety, remove_bad=True, remove_terms="", shuffle=False, spaces=False,
    max_tags=0, structured_mode="Plain Prompt", region_count=1,
):
    preset = _canonical_preset(preset)
    structured_mode = _canonical_output_mode(structured_mode)
    result = str(result or "").strip()
    lowered = result.casefold()
    meta_markers = ("let me ", "here is", "as an ai", "writing prompt", "i will ", "restart inspection", "corrected prompt")
    if any(marker in lowered for marker in meta_markers):
        raise ValueError("Prompt 包含解释或自我审阅内容。")
    if "daylight" in lowered and any(term in lowered for term in ("midnight", "moonlit", "nighttime")):
        raise ValueError("Prompt 包含白天与夜晚冲突。")
    if "indoors" in lowered and "outdoors" in lowered:
        raise ValueError("Prompt 包含室内与室外冲突。")
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
    preset = _canonical_preset(preset)
    if preset in {"Natural Language", "Krea 2 Natural"}:
        return "natural"
    if preset == "Danbooru + Natural":
        return "mixed"
    return "tags"


def _recommended_base_model_for_preset(preset: str):
    return PRESET_BASE_MODEL_DEFAULTS.get(_canonical_preset(preset), "Auto / checkpoint default")


def _preset_text(preset: str):
    """Return the selected output preset text for the UI editor."""
    resolved, _ = _resolve_preset_model(_canonical_preset(preset), "Auto / checkpoint default")
    return PRESETS.get(resolved, "")


def _system_prompt_editor_value(preset, override="", base_model="Auto / checkpoint default"):
    if str(override or "").strip():
        return str(override).strip()
    resolved, model = _resolve_preset_model(preset, base_model)
    aligned, _ = _aligned_preset(resolved, model)
    return _preset_text(aligned)


def _system_prompt_override_value(preset, text, base_model="Auto / checkpoint default"):
    value = str(text or "").strip()
    return "" if value == _system_prompt_editor_value(preset, base_model=base_model).strip() else value


def _load_system_prompt_editor():
    values = _workflow_settings()
    preset, model = values["preset"], values["base_model"]
    override = _system_prompt_override_value(preset, values["system_override"], model)
    return preset, model, override, _system_prompt_editor_value(preset, override, model)


def _aligned_preset(preset: str, base_model: str) -> tuple[str, bool]:
    """Keep an explicit checkpoint and output profile on the same prompt protocol."""
    current = _canonical_preset(preset or "Danbooru Tags")
    combined = PRESET_MODEL_OVERRIDES.get(current)
    if combined:
        current = combined[0]
        if not base_model or _canonical_base_model(base_model) in {"", "Auto / checkpoint default", combined[1]}:
            base_model = combined[1]
    model = _canonical_base_model(base_model or "Auto / checkpoint default")
    expected = MODEL_PRESET_ALIGNMENT.get(model)
    if expected and current != expected:
        return expected, True
    return current, False


def _aligned_quality_guidance(base_model: str) -> str:
    return MODEL_QUALITY_GUIDANCE.get(
        str(base_model or "Auto / checkpoint default").strip(),
        MODEL_QUALITY_GUIDANCE["Auto / checkpoint default"],
    )


def _static_prompt_reference(
    source: str, related_limit: int = 8, sample_limit: int = 8,
    exclude_terms: list[str] | None = None,
) -> list[str]:
    """Provide optional, category-balanced lexicon references, excluding batch concepts."""
    related = DB.wildcard_matches(str(source or ""), max(0, min(30, int(related_limit or 0))))
    excluded = {str(item).strip().casefold() for item in (exclude_terms or []) if str(item).strip()}
    sample_excluded = excluded | {str(item).strip().casefold() for item in related if str(item).strip()}
    requested = max(0, min(30 - len(related), int(sample_limit or 0)))
    categorized = DB.wildcard_samples_by_category(
        12, exclude=sample_excluded, limit=requested,
    )
    category_order = list(categorized)
    random.SystemRandom().shuffle(category_order)
    samples = [(term, category) for category in category_order for term in categorized[category]][:requested]
    result = []
    seen = set()
    for term, category in [*((term, "") for term in related), *samples]:
        normalized = str(term or "").strip()
        if normalized and normalized.casefold() not in seen and normalized.casefold() not in excluded:
            seen.add(normalized.casefold())
            result.append(f"[{category}] {normalized}" if category else normalized)
    return result


def _generate(
    request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
    save_score, cache_result, source_kind="", source_ref="", cache_unrated=False,
    cancel_event=None,
    batch_directive="", batch_history=None,
    connection_settings=None, preserve_inference_settings=False,
):
    provider = _canonical_provider(provider)
    preset, base_model = _resolve_preset_model(preset, base_model)
    structured_mode = _canonical_output_mode(structured_mode)
    preset, preset_aligned = _aligned_preset(preset, base_model)
    request_text = str(request or "").strip()
    source_tags_text = str(source_tags or "").strip()
    if source_tags_text and request_text:
        source = f"SOURCE TAGS:\n{source_tags_text}\n\nCREATIVE REQUEST:\n{request_text}"
    else:
            source = source_tags_text or request_text
    if not source:
        return "", "", "请输入创作要求或源 Danbooru 标签。"
    examples = []
    if batch_history is None:
        batch_history = list(getattr(_BATCH_CONTEXT, "history", None) or [])
    batch_history = [str(item).strip() for item in batch_history if str(item).strip()]
    effective_batch_directive = str(batch_directive or "").strip()
    if effective_batch_directive:
        recent_outputs = _recent_diverse_outputs(source)
        context_outputs = [*recent_outputs, *batch_history]
        exclusion_terms = _diversity_exclusion_terms_from_outputs(context_outputs, source)
        static_tags = _static_prompt_reference(source, exclude_terms=exclusion_terms)
        if exclusion_terms:
            effective_batch_directive += (
                "\nDIVERSITY LEDGER: these concepts have appeared repeatedly in earlier outputs. "
                "Prefer fresh alternatives and do not reuse them unless the source explicitly fixes them: "
                + ", ".join(exclusion_terms)
                + "."
            )
        if context_outputs:
            exclusions = "\n".join(f"- {item[:520]}" for item in context_outputs[-_DIVERSITY_REFERENCE_LIMIT:])
            effective_batch_directive += (
                "\nRecent outputs are exclusion references only. Do not reuse their scene structure, action, "
                "camera arrangement, prop combination, or distinctive decorative elements:\n" + exclusions
            )
    else:
        static_tags = _static_prompt_reference(source)
    operation_instruction = build_operation_instruction("Generate", base_model)
    system = build_system_prompt(
        preset, base_model, safety, nsfw_injection, user_instruction, examples,
        static_tags, system_override, effective_batch_directive, operation_instruction,
    )
    runtime_connection = dict(connection_settings or _connection_settings(provider))
    try:
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
        request_temperature = float(1.0 if temperature is None else temperature)
        if effective_batch_directive and not preserve_inference_settings:
            request_temperature = max(request_temperature, _MIN_INDEPENDENT_BATCH_TEMPERATURE)
        for attempt in range(_MAX_DIVERSITY_RETRIES + 1):
            attempt_system = system
            if attempt:
                attempt_system = build_system_prompt(
                    preset, base_model, safety, nsfw_injection, user_instruction, examples,
                    static_tags, system_override,
                    effective_batch_directive + "\nRESPONSE RETRY: the previous provider response reached its output limit without assistant text; return one concise result directly and do not spend output on analysis or reasoning.",
                    operation_instruction,
                )
            try:
                result = call_llm(
                    provider, endpoint, model, resolved_key, attempt_system, build_user_message(source),
                    request_temperature if preserve_inference_settings else min(2.0, request_temperature + 0.15 * attempt),
                    int(timeout or 90), int(max_tokens or 0), bool(send_temperature),
                    max_retries=runtime_connection["retry_count"], cancel_event=cancel_event,
                    thinking_enabled=runtime_connection.get("thinking_enabled"),
                    thinking_budget=runtime_connection.get("thinking_budget", 0),
                    reasoning_effort=runtime_connection.get("reasoning_effort", ""),
                    top_p=runtime_connection.get("top_p"), top_k=runtime_connection.get("top_k"),
                    **_fallback_request_kwargs(runtime_connection),
                )
            except RuntimeError as error:
                if "response did not contain assistant text" not in str(error).lower() or attempt >= _MAX_RESPONSE_RETRIES:
                    raise
                continue
            candidate = _finalize_generated_prompt(
                result, preset, safety, remove_bad, remove_terms, shuffle, spaces,
                max_tags, structured_mode, region_count,
            )
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
    if cache_result and result:
        DB.save_prompt(
            result, "", preset, base_model, float(save_score or 0), source,
            score_source="unrated",
            score_reason="",
            source_kind=source_kind or None, source_ref=source_ref or None,
            dedupe=False,
        )
    status = "生成完成" + ("（预设已按底模对齐）" if preset_aligned else "")
    return result, system, status


def _generate_auto_loop(
    request, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
    remove_bad, remove_terms, shuffle, spaces, max_tags,
    structured_mode, region_count, cache_result=False,
):
    _AUTO_LOOP_CANCEL.clear()
    provider = _canonical_provider(provider)
    preset, base_model = _resolve_preset_model(preset, base_model)
    structured_mode = _canonical_output_mode(structured_mode)
    preset, _preset_aligned = _aligned_preset(preset, base_model)
    if cache_result:
        identity = {
            "request": str(request or "").strip(), "preset": preset, "system_override": system_override,
            "base_model": base_model, "safety": safety, "nsfw_injection": nsfw_injection,
            "user_instruction": user_instruction, "provider": provider, "endpoint": endpoint, "model": model,
            "temperature": temperature, "max_tokens": max_tokens, "send_temperature": bool(send_temperature),
            "remove_bad": bool(remove_bad),
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
            remove_bad, remove_terms, shuffle, spaces, max_tags,
            structured_mode, region_count, 0, True, "auto_loop", source_ref, True, _AUTO_LOOP_CANCEL,
            _independent_creative_directive(),
        )
    return _generate(
        request, "", preset, system_override, base_model, safety, nsfw_injection, user_instruction,
        provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
            remove_bad, remove_terms, shuffle, spaces, max_tags,
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
    connection_settings=None, preserve_inference_settings=False,
):
    action_name = _canonical_action(action)
    provider = _canonical_provider(provider)
    preset, base_model = _resolve_preset_model(preset, base_model)
    structured_mode = _canonical_output_mode(structured_mode)
    preset, _preset_aligned = _aligned_preset(preset, base_model)
    instruction = build_operation_instruction(action_name, base_model)
    static_tags = _static_prompt_reference(source)
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
            " Preserve the core subject identity and all explicit user constraints. Vary only the dimensions named by the batch directive "
            "(for example action, prop, scene, camera, or lighting); keep unlisted dimensions stable. Every item must be independently "
            "usable as a complete prompt and must not mention the batch or previous items."
        )
    use_fixed_krea_polish = action_name == "Polish" and str(base_model or "").strip() in {"Krea 2", "Anima"}

    def build_transform_system(active_directive: str) -> str:
        if not use_fixed_krea_polish:
            return build_system_prompt(
                preset, base_model, safety, nsfw_injection, user_instruction, [], static_tags=static_tags,
                system_override=system_override, batch_directive=active_directive,
                operation_instruction=instruction,
            )
        sections = [KREA_ANIMA_POLISH_ROLE]
        sections.append("MODEL-ALIGNED QUALITY POLICY:\n" + _aligned_quality_guidance(base_model))
        if str(system_override or "").strip():
            sections.append("Additional system requirements:\n" + str(system_override).strip())
        if str(user_instruction or "").strip():
            sections.append("Additional user requirements:\n" + str(user_instruction).strip())
        sections.append(instruction)
        if static_tags:
            sections.append(
                "OPTIONAL STATIC VOCABULARY REFERENCE (use zero or more compatible visible terms; never copy or force entries, "
                "and ignore unrelated terms):\n" + ", ".join(static_tags[:60])
            )
        if active_directive:
            sections.append(active_directive)
        return "\n\n".join(sections)

    system = build_transform_system(directive)
    try:
        request_temperature = float(temperature or 1.0)
    except (TypeError, ValueError):
        request_temperature = 1.0
    if directive and not preserve_inference_settings:
        request_temperature = max(request_temperature, _MIN_INDEPENDENT_BATCH_TEMPERATURE)
    runtime_connection = dict(connection_settings or _connection_settings(provider))
    try:
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
        result = ""
        for attempt in range(_MAX_DIVERSITY_RETRIES + 1):
            attempt_system = system
            if attempt:
                retry_directive = ""
                if directive:
                    retry_directive = (
                        f"{directive}\nDIVERSITY RETRY {attempt}: the earlier candidate was too similar to an existing item. "
                        "Favor a different compatible combination of setting, action, prop relationship, spatial layout, camera angle, "
                        "time/weather, or visible daily-life context, while leaving the model free to choose; follow the required output contract and return exactly one result."
                    )
                retry_directive += "\nRESPONSE RETRY: return one concise result directly and do not spend output on analysis or reasoning."
                attempt_system = build_transform_system(retry_directive)
            try:
                result = call_llm(
                    provider, endpoint, model, resolved_key, attempt_system,
                    build_user_message(source), request_temperature if preserve_inference_settings else min(2.0, request_temperature + 0.15 * attempt),
                    int(timeout or 90), int(max_tokens or 0), bool(send_temperature),
                    max_retries=runtime_connection["retry_count"], cancel_event=cancel_event,
                    thinking_enabled=runtime_connection.get("thinking_enabled"),
                    thinking_budget=runtime_connection.get("thinking_budget", 0),
                    reasoning_effort=runtime_connection.get("reasoning_effort", ""),
                    top_p=runtime_connection.get("top_p"), top_k=runtime_connection.get("top_k"),
                    **_fallback_request_kwargs(runtime_connection),
                )
            except RuntimeError as error:
                if "response did not contain assistant text" not in str(error).lower() or attempt >= _MAX_RESPONSE_RETRIES:
                    raise
                continue
            finalize_preset = "Krea 2 Natural" if use_fixed_krea_polish else preset
            result = _finalize_generated_prompt(
                result, finalize_preset, safety, remove_bad, remove_terms, shuffle, spaces,
                max_tags, structured_mode, region_count,
            )
            if result:
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
        item["selected"] = bool(record.get("selected", False))
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
        choices, values = _png_batch_selection_choices(data)
        return _png_batch_json(data), _png_batch_table(data), selected, current, f"已导入 {len(data['records'])} 条逐图 Prompt。", gr.update(choices=choices, value=values)
    except Exception as error:
        return gr.update(), [], 1, "", f"导入失败：{_safe_error(error)}", gr.update(choices=[], value=[])


def _inline_png_batch_load(file_path):
    """Drop the standalone panel's explicit record-selection update."""
    return _png_batch_load(file_path)[:5]


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


def _png_batch_selection_choices(payload):
    """Return processed records as explicit write-back choices."""
    try:
        data = _normalize_png_batch_payload(payload or {})
    except Exception:
        return [], []
    choices = []
    selected = []
    for record in data["records"]:
        processed = str(record.get("prompt", {}).get("processed") or "").strip()
        if not processed or record.get("appended"):
            continue
        record_id = str(record.get("record_id") or "")
        label = f"{record['index']} · {record['image']['filename']}"
        choices.append((label, record_id))
        if record.get("selected") is True:
            selected.append(record_id)
    return choices, selected


def _png_batch_set_selection(payload, selected_ids):
    try:
        data = _normalize_png_batch_payload(payload or {})
    except Exception as error:
        return payload, [], 1, "", f"批次 JSON 无效：{_safe_error(error)}", gr.update(choices=[], value=[])
    selected = {str(value) for value in (selected_ids or [])}
    for record in data["records"]:
        record["selected"] = str(record.get("record_id") or "") in selected
    choices, values = _png_batch_selection_choices(data)
    current_index, current = _png_batch_current(data, 1)
    return _png_batch_json(data), _png_batch_table(data), current_index, current, f"已选择 {len(values)} 条结果。", gr.update(choices=choices, value=values)


def _png_batch_mark_selected_appended(payload, selected_ids, succeeded):
    if not succeeded:
        data = _normalize_png_batch_payload(payload or {})
        choices, values = _png_batch_selection_choices(data)
        current_index, current = _png_batch_current(data, 1)
        return _png_batch_json(data), _png_batch_table(data), current_index, current, "所选结果未写入。", gr.update(choices=choices, value=values)
    data = _normalize_png_batch_payload(payload or {})
    selected = {str(value) for value in (selected_ids or [])}
    marked = 0
    for record in data["records"]:
        if str(record.get("record_id") or "") in selected:
            record["appended"] = True
            record["selected"] = False
            marked += 1
    choices, values = _png_batch_selection_choices(data)
    current_index, current = _png_batch_current(data, 1)
    return _png_batch_json(data), _png_batch_table(data), current_index, current, f"已写入 {marked} 条到 txt2img 正面 Prompt。", gr.update(choices=choices, value=values)


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
        return [], 1, "", f"批次 JSON 无效：{_safe_error(error)}", gr.update(choices=[], value=[])
    selected, current = _png_batch_current(data, selection)
    table = _png_batch_table(data)
    choices, values = _png_batch_selection_choices(data)
    return table, selected, current, f"已载入 {len(table)} 条逐图 Prompt。", gr.update(choices=choices, value=values)


def _inline_png_batch_refresh(payload, selection=1):
    """Return exactly the four outputs rendered by the inline JSON panel."""
    return _png_batch_refresh(payload, selection)[:4]


def _png_batch_move(payload, selection, offset):
    selected, current = _png_batch_current(payload, int(selection or 1) + int(offset))
    return selected, current


def _cancel_png_batch(cancel_id=""):
    _key, event = _png_batch_cancel_event(cancel_id)
    event.set()
    return "已请求取消；当前 LLM 请求返回后停止，已完成结果会保留。"


def _inline_json_batch_run(payload, action, preset, base_model, variation_mode="independent", cancel_id=""):
    """Run JSON Prompt processing from the Forge txt2img inline panel."""
    workflow = _workflow_settings()
    connection = _connection_settings()
    action = _canonical_action(action)
    variation_mode = _canonical_variation_mode(variation_mode)
    preset = _canonical_preset(preset)
    base_model = _canonical_base_model(base_model)
    selected_preset = preset if preset in PRESETS else workflow["preset"]
    selected_base_model = base_model if base_model in BASE_MODEL_GUIDANCE else workflow["base_model"]
    selected_preset, _preset_aligned = _aligned_preset(selected_preset, selected_base_model)
    for update in _png_batch_run(
        payload, action,
        selected_preset, workflow["system_override"], selected_base_model, workflow["safety"],
        workflow["nsfw_injection"], workflow["user_instruction"],
        connection["provider"], connection["endpoint"], connection["model"], "",
        connection["temperature"], connection["timeout"], connection["max_tokens"], connection["send_temperature"],
        workflow["remove_bad"], workflow["remove_terms"], workflow["shuffle"], workflow["spaces"],
        workflow["max_tags"], workflow["structured_mode"], workflow["region_count"],
        variation_mode, cancel_id,
    ):
        # The compact inline panel predates the optional selection control.
        yield tuple(update[:5])


def _cancel_auto_loop_generation():
    _AUTO_LOOP_CANCEL.set()
    return "已请求取消当前 LLM 请求；已返回的结果不会写入队列。"


def _prune_inline_cancelled_requests(now: float) -> None:
    expired = [
        key for key, cancelled_at in _INLINE_CANCELLED_REQUESTS.items()
        if now - cancelled_at > _INLINE_CANCEL_TTL_SECONDS
    ]
    for key in expired:
        _INLINE_CANCELLED_REQUESTS.pop(key, None)


def _inline_request_event(slot: str, request_id: str) -> threading.Event:
    key = (slot, request_id)
    with _INLINE_REQUEST_CONTROL_LOCK:
        _prune_inline_cancelled_requests(time.monotonic())
        event = _INLINE_REQUEST_EVENTS.setdefault(key, threading.Event())
        if key in _INLINE_CANCELLED_REQUESTS:
            event.set()
        return event


def _release_inline_request(slot: str, request_id: str, event: threading.Event) -> None:
    key = (slot, request_id)
    with _INLINE_REQUEST_CONTROL_LOCK:
        if _INLINE_REQUEST_EVENTS.get(key) is event:
            _INLINE_REQUEST_EVENTS.pop(key, None)
        _INLINE_CANCELLED_REQUESTS.pop(key, None)
        if _INLINE_ACTIVE_REQUEST_IDS.get(slot) == request_id:
            _INLINE_ACTIVE_REQUEST_IDS[slot] = ""


def _cancel_inline_generation(slot, request_id=""):
    normalized_slot = str(slot or "").strip()
    normalized_request_id = str(request_id or "").strip()
    if normalized_slot not in _INLINE_CANCEL_EVENTS:
        return "没有可停止的 LLM 请求。"
    with _INLINE_REQUEST_CONTROL_LOCK:
        _prune_inline_cancelled_requests(time.monotonic())
        if not normalized_request_id:
            normalized_request_id = _INLINE_ACTIVE_REQUEST_IDS.get(normalized_slot, "")
        if normalized_request_id:
            key = (normalized_slot, normalized_request_id)
            _INLINE_CANCELLED_REQUESTS[key] = time.monotonic()
            _INLINE_REQUEST_EVENTS.setdefault(key, threading.Event()).set()
        _INLINE_CANCEL_EVENTS[normalized_slot].set()
    return "已请求停止；LLM 等待已中断，迟到响应不会写入 Prompt。"


def _png_batch_run(
    payload, action, preset, system_override, base_model, safety, nsfw_injection,
    user_instruction, provider, endpoint, model, api_key, temperature, timeout,
    max_tokens, send_temperature, remove_bad=True, remove_terms="", shuffle=False,
    spaces=False, max_tags=0, structured_mode="Plain Prompt", region_count=1,
    variation_mode="faithful",
    cancel_id="",
):
    action = _canonical_action(action)
    preset = _canonical_preset(preset)
    base_model = _canonical_base_model(base_model)
    variation_mode = _canonical_variation_mode(variation_mode)
    try:
        data = _normalize_png_batch_payload(payload or {})
    except Exception as error:
        yield payload, [], 1, "", f"处理失败：{_safe_error(error)}", gr.update(choices=[], value=[])
        return
    if not data["records"]:
        yield _png_batch_json(data), [], 1, "", "批次为空，请先导入逐图 Prompt。", gr.update(choices=[], value=[])
        return
    event_key, cancel_event = _png_batch_cancel_event(cancel_id)
    cancel_event.clear()
    records = [dict(record) for record in data["records"]]
    independent = str(variation_mode or "faithful") == "independent"
    progress_interval = max(1, (len(records) + 99) // 100)
    skipped_existing = reused = 0
    outcomes = {}
    previous_outputs = []
    try:
        for position, record in enumerate(records, 1):
            if cancel_event.is_set():
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
                    yield gr.update(), gr.update(), gr.update(), gr.update(), f"处理中 {position}/{len(records)}", gr.update()
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
                    cancel_event, batch_directive, previous_outputs,
                )
                if not independent and not cancel_event.is_set():
                    outcomes[outcome_key] = (processed, llm_status)
            if not processed and cancel_event.is_set() and "request cancelled" in str(llm_status or "").lower():
                record["status"], record["error"] = "已取消", "当前 LLM 请求已取消"
                for pending in records[position:]:
                    if not str(pending.get("prompt", {}).get("processed") or "").strip():
                        pending["status"], pending["error"] = "已取消", "尚未处理"
                break
            if processed:
                if independent:
                    previous_outputs.append(processed)
                # The receiver uses this field to decide whether the result is
                # safe to store as natural language. The selected output
                # preset defines the protocol; the operation only changes how
                # the source is transformed.
                processed_kind = _processed_kind_for_preset(preset)
                record["prompt"] = {
                    **record["prompt"], "processed": processed,
                    "processed_kind": processed_kind, "output_kind": processed_kind,
                    "processed_preset": preset, "processed_base_model": base_model,
                }
                record["status"], record["error"] = "已完成", ""
            else:
                record["status"], record["error"] = "失败", llm_status or "LLM 未返回结果"
            if position % progress_interval == 0 or position == len(records):
                yield gr.update(), gr.update(), gr.update(), gr.update(), f"处理中 {position}/{len(records)}", gr.update()

        result = {"schema_version": PNG_BATCH_SCHEMA, "producer": {"name": "LLM Prompt Studio"}, "records": records}
        selected, current = _png_batch_current(result, 1)
        completed = sum(1 for record in records if record.get("status") == "已完成")
        failed = sum(1 for record in records if record.get("status") == "失败")
        cancelled = sum(1 for record in records if record.get("status") == "已取消")
        choices, values = _png_batch_selection_choices(result)
        yield _png_batch_json(result), _png_batch_table(result), selected, current, (
            f"批处理结束：目标 {preset} / {base_model}；完成 {completed}，相同 Prompt 复用 {reused}，"
            f"已有结果跳过 {skipped_existing}（同目标），失败 {failed}，取消 {cancelled}。"
        ), gr.update(choices=choices, value=values)
    finally:
        cancel_event.clear()
        with _PNG_BATCH_CANCEL_LOCK:
            if _PNG_BATCH_CANCEL_EVENTS.get(event_key) is cancel_event:
                _PNG_BATCH_CANCEL_EVENTS.pop(event_key, None)


def _png_batch_advance_after_append(payload, selection, succeeded):
    if not succeeded:
        selected, current = _png_batch_current(payload, selection)
        data = _normalize_png_batch_payload(payload or {})
        choices, values = _png_batch_selection_choices(data)
        return payload, selected, current, "当前结果未写入。", gr.update(choices=choices, value=values)
    data = _normalize_png_batch_payload(payload or {})
    selected = int(selection or 0)
    if selected < 1 or selected > len(data["records"]):
        choices, values = _png_batch_selection_choices(data)
        return _png_batch_json(data), 0, "", "没有待追加的结果。", gr.update(choices=choices, value=values)
    data["records"][selected - 1]["appended"] = True
    data["records"][selected - 1]["selected"] = False
    if selected == len(data["records"]):
        choices, values = _png_batch_selection_choices(data)
        return _png_batch_json(data), 0, "", "全部逐图结果已追加完成。", gr.update(choices=choices, value=values)
    next_selection, current = _png_batch_current(data, selected + 1)
    choices, values = _png_batch_selection_choices(data)
    return _png_batch_json(data), next_selection, current, f"已写入第 {selected} 条，当前为第 {next_selection} 条。", gr.update(choices=choices, value=values)


def _png_batch_mark_appended(payload, selection, selected_ids, scope, succeeded):
    """Apply the single JSON write-back action to the chosen scope."""
    if str(scope or "selected") == "current":
        updated = _png_batch_advance_after_append(payload, selection, succeeded)
        next_payload, next_selection, current, status, choices = updated
        return next_payload, _png_batch_table(next_payload), next_selection, current, status, choices
    return _png_batch_mark_selected_appended(payload, selected_ids, succeeded)


BUILTIN_TEMPLATE_CHOICES = [
    ("通用创作", "general"),
    ("兽耳角色批量", "kemonimimi"),
]
CUSTOM_TEMPLATES_SETTING = "prompt_templates_v1"
DEFAULT_TEMPLATE_SETTING = "prompt_templates_default_v1"
_INLINE_TEMPLATE_COMPONENTS: dict[str, Any] = {}
_INLINE_TEMPLATE_EDITORS: dict[str, Any] = {}


def _custom_templates() -> dict[str, dict[str, Any]]:
    """Return normalized structured templates, accepting legacy text-only values."""
    stored = DB.get_setting(CUSTOM_TEMPLATES_SETTING, {}) or {}
    if not isinstance(stored, dict):
        return {}
    result = {}
    for name, value in stored.items():
        clean_name = str(name).strip()
        record = value if isinstance(value, dict) else {"content": value}
        clean_value = str(record.get("content", "") or "").strip()
        if 1 <= len(clean_name) <= 64 and 1 <= len(clean_value) <= 12000:
            dimensions = record.get("variation_dimensions", [])
            if isinstance(dimensions, str):
                dimensions = [item.strip() for item in dimensions.split(",") if item.strip()]
            result[clean_name] = {
                "content": clean_value,
                "preserve_fixed_prompt": bool(record.get("preserve_fixed_prompt", True)),
                "obey_user_prompt": bool(record.get("obey_user_prompt", True)),
                "variation_dimensions": [str(item) for item in dimensions if str(item).strip()],
                "style_rules": str(record.get("style_rules", "") or "").strip(),
                "forbidden_content": str(record.get("forbidden_content", "") or "").strip(),
                "conflict_policy": str(record.get("conflict_policy", "reject_and_retry") or "reject_and_retry"),
                "duplicate_policy": str(record.get("duplicate_policy", "skip_and_continue") or "skip_and_continue"),
            }
    return result


def _template_choices():
    return [*BUILTIN_TEMPLATE_CHOICES, *[(name, f"custom:{name}") for name in _custom_templates()]]


def _template_request(template_name):
    key = str(template_name or "general")
    if key == "kemonimimi":
        return KEMONOMIMI_LOLI_BATCH_TEMPLATE
    if key.startswith("custom:"):
        record = _custom_templates().get(key[7:], {})
        content = str(record.get("content", "")).strip()
        if not content:
            return ""
        rules = [
            f"Variation dimensions: {', '.join(record.get('variation_dimensions', []))}.",
            f"Style rules: {record.get('style_rules')}.",
            f"Forbidden content: {record.get('forbidden_content')}." ,
            "Preserve the fixed Prompt verbatim." if record.get("preserve_fixed_prompt", True) else "",
            "Follow the user's prompt as a semantic hard constraint." if record.get("obey_user_prompt", True) else "",
            f"Conflict policy: {record.get('conflict_policy', 'reject_and_retry')}; duplicate policy: {record.get('duplicate_policy', 'skip_and_continue')}.",
        ]
        rendered = [item for item in rules if item and not item.endswith(": .")]
        defaults = {"action", "environment_detail", "camera", "composition", "lighting", "atmosphere"}
        if (set(record.get("variation_dimensions", [])) == defaults
                and not record.get("style_rules") and not record.get("forbidden_content")
                and record.get("preserve_fixed_prompt", True) and record.get("obey_user_prompt", True)
                and record.get("conflict_policy") == "reject_and_retry"
                and record.get("duplicate_policy") == "skip_and_continue"):
            return content
        return content + "\n\nTEMPLATE RULES:\n" + "\n".join(rendered)
    return GENERAL_CREATIVE_REQUEST_TEMPLATE


def _native_cache_database(source: str) -> StudioDB:
    source_key = str(source or "").strip()
    if source_key == "cache":
        return DB
    if source_key == "processed_cache":
        return RESULT_DB
    raise ValueError("缓存 Prompt 生图必须选择原始缓存库或处理结果库")


def _native_cache_merge_prompt(base_prompt: str, cached_prompt: str, write_mode: str, marker: str) -> str:
    base = str(base_prompt or "").strip()
    cached = str(cached_prompt or "").strip()
    mode = str(write_mode or "append_end").strip()
    if mode == "replace":
        return cached
    if mode == "marker":
        token = str(marker or "{{LLM}}").strip() or "{{LLM}}"
        if token not in base:
            raise ValueError("当前 Prompt 中没有插入标记，请添加标记或更换写入方式")
        return base.replace(token, cached)
    if not base:
        return cached
    if not cached:
        return base
    if mode == "append_start":
        return f"{cached}, {base}"
    return f"{base}, {cached}"


def _native_cache_cleanup_locked(now: float | None = None) -> None:
    stamp = float(now if now is not None else time.time())
    expired = [
        token for token, context in _NATIVE_CACHE_CONTEXTS.items()
        if stamp - float(context.get("created_at", stamp)) > _NATIVE_CACHE_CONTEXT_TTL
    ]
    for token in expired:
        _NATIVE_CACHE_CONTEXTS.pop(token, None)


def _native_cache_prepare(payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source") or "").strip()
    database = _native_cache_database(source)
    try:
        total_images = max(1, int(payload.get("total_images") or 1))
        after_id = max(0, int(payload.get("after_id") or 0))
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("缓存读取序号和本次图片数量必须是有效整数") from error
    base_prompt = str(payload.get("base_prompt") or "")
    write_mode = str(payload.get("write_mode") or "append_end").strip()
    marker = str(payload.get("marker") or "{{LLM}}")
    # Native Forge Generate forever is a cycling consumer. Once it reaches
    # the end of a cache library, start again from the first record so the
    # browser's native loop remains infinite. The API keeps strict exhaustion
    # behavior unless the caller explicitly opts into cycling.
    allow_wrap = bool(payload.get("allow_wrap", False))
    records: list[dict[str, Any]] = []
    cursor = after_id
    wrapped = False
    while len(records) < total_images:
        page = database.list_prompts_after("", min(1000, total_images - len(records)), cursor)
        if page:
            records.extend(page)
            cursor = int(page[-1]["id"])
            continue
        if allow_wrap and cursor > 0:
            cursor = 0
            wrapped = True
            continue
        break
    usable = [
        {"id": int(record["id"]), "prompt": str(record.get("prompt") or "").strip()}
        for record in records
        if str(record.get("prompt") or "").strip()
    ]
    if len(usable) < total_images:
        raise ValueError(
            f"{('处理结果库' if source == 'processed_cache' else '原始缓存库')}剩余 {len(usable)} 条，"
            f"本次需要 {total_images} 条；未启动生图，缓存读取位置未推进"
        )
    token = uuid.uuid4().hex
    context = {
        "token": token,
        "slot": str(payload.get("slot") or "txt2img"),
        "source": source,
        "base_prompt": base_prompt,
        "write_mode": write_mode,
        "marker": marker,
        "records": usable,
        "next_cursor": usable[-1]["id"],
        "created_at": time.time(),
        "state": "pending",
    }
    with _NATIVE_CACHE_CONTEXT_LOCK:
        _native_cache_cleanup_locked()
        _NATIVE_CACHE_CONTEXTS[token] = context
    return {
        "token": token,
        "count": total_images,
        "next_cursor": context["next_cursor"],
        "records": usable,
        "wrapped": wrapped,
    }


def apply_native_cache_context(processing) -> str | None:
    """Apply one prepared cache context to Forge's per-image prompt list.

    The context is consumed by Forge's native processing hook, so the browser
    Prompt textbox remains the user's fixed base Prompt for every native job.
    """
    existing = getattr(processing, "_llm_prompt_studio_native_cache_token", "")
    if existing:
        return str(existing)
    slot = "img2img" if type(processing).__name__.lower().endswith("img2img") else "txt2img"
    with _NATIVE_CACHE_CONTEXT_LOCK:
        _native_cache_cleanup_locked()
        context = next(
            (
                item for item in _NATIVE_CACHE_CONTEXTS.values()
                if item.get("state") == "pending" and item.get("slot") == slot
            ),
            None,
        )
        if context is None:
            return None
        context["state"] = "active"
    records = context["records"]
    prompts = [
        _native_cache_merge_prompt(
            context["base_prompt"], record["prompt"], context["write_mode"], context["marker"],
        )
        for record in records
    ]
    batch_size = max(1, int(getattr(processing, "batch_size", 1) or 1))
    negative = getattr(processing, "negative_prompt", "")
    negative_prompts = negative if isinstance(negative, list) else [negative]
    negative_prompts = [
        negative_prompts[index % len(negative_prompts)] if negative_prompts else ""
        for index in range(len(prompts))
    ]
    try:
        from modules import shared
        styles = getattr(processing, "styles", [])
        prompts = [shared.prompt_styles.apply_styles_to_prompt(prompt, styles) for prompt in prompts]
        negative_prompts = [
            shared.prompt_styles.apply_negative_styles_to_prompt(prompt, styles)
            for prompt in negative_prompts
        ]
    except (ImportError, AttributeError):
        # Unit-test doubles and non-Forge callers do not provide prompt styles.
        pass
    processing.all_prompts = list(prompts)
    processing.all_negative_prompts = list(negative_prompts)
    processing.main_prompt = prompts[0]
    processing.main_negative_prompt = negative_prompts[0]
    if hasattr(processing, "hr_prompt"):
        # Forge's comments script treats hr_prompt as a scalar string. Keep
        # that public field scalar while the per-image list lives in all_hr_*.
        processing.hr_prompt = prompts[0]
        processing.hr_negative_prompt = negative_prompts[0]
        processing.all_hr_prompts = list(prompts)
        processing.all_hr_negative_prompts = list(negative_prompts)
    processing.n_iter = max(1, (len(prompts) + batch_size - 1) // batch_size)
    setattr(processing, "_llm_prompt_studio_native_cache_token", context["token"])
    LOGGER.info(
        "Native cache Prompt injection applied: %s images, source=%s, cursor=%s",
        len(prompts), context["source"], context["next_cursor"],
    )
    return context["token"]


def _native_cache_release(token: str, commit: bool = False) -> dict[str, Any]:
    key = str(token or "").strip()
    if not key:
        return {"released": False, "next_cursor": 0}
    with _NATIVE_CACHE_CONTEXT_LOCK:
        context = _NATIVE_CACHE_CONTEXTS.get(key)
        if context is None:
            return {"released": False, "next_cursor": 0}
        if commit and context.get("state") != "active":
            return {"released": False, "committed": False, "next_cursor": int(context.get("next_cursor") or 0)}
        if commit:
            context["state"] = "committed"
        _NATIVE_CACHE_CONTEXTS.pop(key, None)
        return {
            "released": True,
            "committed": bool(commit),
            "next_cursor": int(context.get("next_cursor") or 0),
        }


def _set_default_template(template_name):
    key = str(template_name or "general").strip()
    valid = {value for _, value in BUILTIN_TEMPLATE_CHOICES} | {f"custom:{name}" for name in _custom_templates()}
    if key not in valid:
        key = "general"
    DB.set_setting(DEFAULT_TEMPLATE_SETTING, key)
    return f"默认模板已设为：{key}"


def _default_template_choice():
    key = str(DB.get_setting(DEFAULT_TEMPLATE_SETTING, "general") or "general")
    valid = {value for _, value in BUILTIN_TEMPLATE_CHOICES} | {f"custom:{name}" for name in _custom_templates()}
    return key if key in valid else "general"


def _save_custom_template(name, content, preserve_fixed_prompt=True, obey_user_prompt=True,
                          variation_dimensions="", style_rules="", forbidden_content="",
                          conflict_policy="reject_and_retry", duplicate_policy="skip_and_continue"):
    name = str(name or "").strip()
    content = str(content or "").strip()
    if not name or len(name) > 64 or any(ord(char) < 32 for char in name):
        return ("模板名称不能为空且不能超过 64 个字符。", *(_template_dropdown_update() for _ in range(1 + len(_INLINE_TEMPLATE_COMPONENTS))))
    if not content or len(content) > 12000:
        return ("模板内容不能为空且不能超过 12000 个字符。", *(_template_dropdown_update() for _ in range(1 + len(_INLINE_TEMPLATE_COMPONENTS))))
    if name.startswith("custom:") or name in {label for label, _ in BUILTIN_TEMPLATE_CHOICES} or name in {"general", "kemonimimi"}:
        return ("该名称保留给内置模板，请换一个名称。", *(_template_dropdown_update() for _ in range(1 + len(_INLINE_TEMPLATE_COMPONENTS))))
    templates = _custom_templates()
    # Store a structured record so future editor fields can evolve without
    # breaking existing text-only templates or callers.
    dimensions = variation_dimensions if isinstance(variation_dimensions, list) else str(variation_dimensions or "").split(",")
    dimensions = [item.strip() for item in dimensions if str(item).strip()]
    templates[name] = {
        "content": content,
        "preserve_fixed_prompt": bool(preserve_fixed_prompt),
        "obey_user_prompt": bool(obey_user_prompt),
        "variation_dimensions": dimensions or ["action", "environment_detail", "camera", "composition", "lighting", "atmosphere"],
        "style_rules": str(style_rules or "").strip(),
        "forbidden_content": str(forbidden_content or "").strip(),
        "conflict_policy": str(conflict_policy or "reject_and_retry"),
        "duplicate_policy": str(duplicate_policy or "skip_and_continue"),
    }
    DB.set_setting(CUSTOM_TEMPLATES_SETTING, templates)
    return (f"模板“{name}”已保存。", *(_template_dropdown_update(value=f"custom:{name}") for _ in range(1 + len(_INLINE_TEMPLATE_COMPONENTS))))


def _delete_custom_template(template_name):
    key = str(template_name or "")
    if not key.startswith("custom:"):
        return ("内置模板不能删除。", *(_template_dropdown_update() for _ in range(1 + len(_INLINE_TEMPLATE_COMPONENTS))))
    name = key[7:]
    templates = _custom_templates()
    if name not in templates:
        return ("找不到要删除的自定义模板。", *(_template_dropdown_update(value="general") for _ in range(1 + len(_INLINE_TEMPLATE_COMPONENTS))))
    del templates[name]
    DB.set_setting(CUSTOM_TEMPLATES_SETTING, templates)
    if DB.get_setting(DEFAULT_TEMPLATE_SETTING, "general") == key:
        DB.set_setting(DEFAULT_TEMPLATE_SETTING, "general")
    return (f"模板“{name}”已删除。", *(_template_dropdown_update(value="general") for _ in range(1 + len(_INLINE_TEMPLATE_COMPONENTS))))


def _template_dropdown_update(value=None):
    updates = {"choices": _template_choices()}
    if value is not None:
        updates["value"] = value
    return gr.update(**updates)


def _load_custom_template_editor(template_name):
    key = str(template_name or "")
    if not key.startswith("custom:"):
        return "", _template_request(key), True, True, "action, environment_detail, camera, composition, lighting, atmosphere", "", "", "reject_and_retry", "skip_and_continue"
    name = key[7:]
    record = _custom_templates().get(name, {})
    return (name, record.get("content", ""), record.get("preserve_fixed_prompt", True),
            record.get("obey_user_prompt", True), ", ".join(record.get("variation_dimensions", [])),
            record.get("style_rules", ""), record.get("forbidden_content", ""),
            record.get("conflict_policy", "reject_and_retry"), record.get("duplicate_policy", "skip_and_continue"))


def _create_template_editor(prefix, choice):
    initial = _load_custom_template_editor(_default_template_choice())
    with gr.Accordion("编辑 / 保存自定义模板", open=False, elem_classes=["lps-template-editor"]):
        name = gr.Textbox(label="模板名称", value=initial[0], max_lines=1, placeholder="填写新名称可另存为模板", elem_id=f"{prefix}_template_name")
        content = gr.Textbox(label="模板内容", value=initial[1], lines=3, max_lines=12, elem_id=f"{prefix}_template_content")
        with gr.Row():
            preserve = gr.Checkbox(label="固定 Prompt 完全保留", value=initial[2])
            obey = gr.Checkbox(label="严格遵从用户提示词", value=initial[3])
        dimensions = gr.Textbox(label="变化维度（逗号分隔）", value=initial[4], lines=2, elem_id=f"{prefix}_template_variation_dimensions")
        style = gr.Textbox(label="风格规则（可选）", value=initial[5], lines=2)
        forbidden = gr.Textbox(label="禁止内容（可选）", value=initial[6], lines=2)
        with gr.Row():
            conflict = gr.Dropdown(label="冲突处理", choices=[("拒绝并重试", "reject_and_retry"), ("允许", "allow")], value=initial[7])
            duplicate = gr.Dropdown(label="重复处理", choices=[("跳过并继续", "skip_and_continue"), ("接受", "accept")], value=initial[8])
        with gr.Row():
            save = gr.Button("保存模板", variant="primary", elem_id=f"{prefix}_template_save")
            default = gr.Button("设为默认模板", elem_id=f"{prefix}_template_default")
            delete = gr.Button("删除所选自定义模板", variant="stop", elem_id=f"{prefix}_template_delete")
        status = gr.Markdown(elem_id=f"{prefix}_template_status")
    return dict(choice=choice, fields=[name, content, preserve, obey, dimensions, style, forbidden, conflict, duplicate],
                save=save, default=default, delete=delete, status=status)


def _template_sync_values(status, update, panel_count):
    """Return explicit editor updates even when the selected template did not change."""
    values = _load_custom_template_editor(update["value"]) if "value" in update else [gr.update() for _ in range(9)]
    # Gradio consumes update dictionaries during postprocessing. Each output
    # needs its own object or the second dropdown loses its new selection.
    return tuple(dict(value) if isinstance(value, dict) else value
                 for _ in range(panel_count) for value in (status, update, *values))


def _bind_template_editors(editors, interface=None):
    # Freeze this UI's outputs: callbacks must not depend on registries changing
    # when Forge rebuilds the UI later.
    panels = tuple(editors)
    outputs = [component for panel in panels for component in (panel["status"], panel["choice"], *panel["fields"])]

    def save(*values):
        result = _save_custom_template(*values)
        return _template_sync_values(result[0], result[1], len(panels))

    def delete(key):
        result = _delete_custom_template(key)
        return _template_sync_values(result[0], result[1], len(panels))

    def select(key):
        return _template_sync_values("", _template_dropdown_update(value=key), len(panels))

    def default(key):
        status = _set_default_template(key)
        return _template_sync_values(status, _template_dropdown_update(value=_default_template_choice()), len(panels))

    for panel in panels:
        panel["choice"].input(select, inputs=panel["choice"], outputs=outputs, queue=False)
        panel["save"].click(save, inputs=panel["fields"], outputs=outputs)
        panel["delete"].click(delete, inputs=panel["choice"], outputs=outputs)
        panel["default"].click(default, inputs=panel["choice"], outputs=outputs)
    if interface is not None:
        interface.load(lambda: select(_default_template_choice()), outputs=outputs, queue=False)


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


def _test_connection(provider, endpoint, model, api_key, fallback_model, temperature, timeout, max_tokens, send_temperature, retry_count, thinking_enabled=False, thinking_budget=0, reasoning_effort="", top_p=None, top_k=None, fallback_provider="", fallback_endpoint="", fallback_api_key=""):
    try:
        provider = _canonical_provider(provider)
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
        fallback_provider = _canonical_provider(fallback_provider or provider)
        fallback_endpoint = validate_endpoint(fallback_endpoint or endpoint)
        resolved_fallback_key = CREDENTIALS.resolve(fallback_api_key, fallback_provider, fallback_endpoint)
        output = call_llm(
            provider, endpoint, model, resolved_key, "Reply exactly: READY", "Connection test",
            float(temperature or 0), int(timeout or 30), max(16, min(int(max_tokens or 64), 64)), bool(send_temperature),
            max_retries=max(0, min(int(retry_count or 0), 5)),
            fallback_model=str(fallback_model or "").strip(),
            fallback_provider=fallback_provider, fallback_endpoint=fallback_endpoint,
            fallback_api_key=resolved_fallback_key,
            thinking_enabled=bool(thinking_enabled), thinking_budget=int(thinking_budget or 0), reasoning_effort=str(reasoning_effort or ""),
            top_p=top_p, top_k=top_k,
        )
        return f"{provider} 连接成功：{output[:160]}"
    except Exception as error:
        return f"连接失败：{_safe_error(error)}"


def _test_fallback_connection(provider, endpoint, model, api_key, temperature, timeout, max_tokens,
                              send_temperature, retry_count, thinking_enabled=False, thinking_budget=0,
                              reasoning_effort="", top_p=None, top_k=None):
    if not str(provider or "").strip() or not str(model or "").strip():
        return "请先选择备用服务商并填写备用模型 ID。"
    result = _test_connection(
        provider, endpoint, model, api_key, "", temperature, timeout, max_tokens,
        send_temperature, retry_count, thinking_enabled, thinking_budget, reasoning_effort,
        top_p, top_k,
    )
    return result.replace("连接成功", "备用连接成功", 1)


def _inline_generate(
    request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    remove_bad, remove_terms, shuffle, spaces, max_tags,
    structured_mode, region_count, save_score, cache_result,
    cancel_event=None,
):
    preset_key = _canonical_preset(preset)
    preset, base_model = _resolve_preset_model(preset_key, base_model)
    preset, _preset_aligned = _aligned_preset(preset, base_model)
    saved_workflow = _workflow_settings()
    shared_updates = {"preset": preset_key, "base_model": base_model, "safety": safety}
    if any(saved_workflow[key] != value for key, value in shared_updates.items()):
        _save_workflow_values(shared_updates)
    connection = _connection_settings()
    current_prompt = str(source_tags or "").strip()
    variation_request = str(request or "").strip()
    if not current_prompt and not variation_request:
        variation_request = (
            "Create one original, directly usable English image-generation prompt for a single coherent scene. "
            "Choose a clear subject, action, setting, camera, lighting, and atmosphere; output only the prompt text."
        )
    inline_source = current_prompt
    if variation_request:
        inline_source = (
            f"FIXED PROMPT (IMMUTABLE):\n{current_prompt}\n\nINLINE VARIATION REQUEST:\n{variation_request}"
            if current_prompt else variation_request
        )
    elif current_prompt:
        inline_source = f"FIXED PROMPT (IMMUTABLE):\n{current_prompt}"
    if current_prompt:
        technical_tokens = _immutable_technical_tokens(current_prompt)
        inline_source += (
            "\n\nTECHNICAL TOKENS (verbatim, excluded from semantic expansion): "
            + ", ".join(technical_tokens)
            + ". Preserve each token exactly and do not generate replacements."
        )
    else:
        inline_source = f"USER REQUEST (FOLLOW STRICTLY):\n{inline_source}" if inline_source else ""
    generated, system, status = _generate(
        "", inline_source, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
        connection["provider"], connection["endpoint"], connection["model"], "",
        connection["temperature"], connection["timeout"], connection["max_tokens"], connection["send_temperature"],
        remove_bad, remove_terms, shuffle, spaces, max_tags,
        structured_mode, region_count, save_score, cache_result, "", "", False, cancel_event,
        INLINE_DELTA_DIRECTIVE + "\n\n" + _independent_creative_directive(),
        connection_settings=connection, preserve_inference_settings=True,
    )
    return generated, system, status


def _unwrap_component(component):
    return component.component if hasattr(component, "component") else component


def capture_prompt_component(component, **kwargs):
    """Capture the native txt2img prompt so the inline workbench can write to it."""
    elem_id = kwargs.get("elem_id")
    if elem_id != "txt2img_prompt":
        return
    slot = "txt2img"
    captured = _unwrap_component(component)
    with _INLINE_LOCK:
        # A rebuilt UI (Forge's "重启 UI" path re-runs create_ui()) creates a new prompt
        # component, so the replacement layout must be allowed to inject its panel again.
        if _PROMPT_TARGETS.get(slot) is not captured:
            _INLINE_SLOTS.discard(slot)
        _PROMPT_TARGETS[slot] = captured


def inject_inline_before_negative(component, **kwargs):
    """Render the txt2img workbench immediately before its negative prompt."""
    elem_id = kwargs.get("elem_id")
    if elem_id != "txt2img_neg_prompt_row":
        return
    slot = "txt2img"
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


def _inline_cache_transform(source, action, preset, base_model, instruction, cancel_event):
    workflow, connection = _workflow_settings(), _connection_settings()
    return _expand_or_polish(
        source, _canonical_action(action), _canonical_preset(preset), workflow["system_override"],
        _canonical_base_model(base_model), workflow["safety"], workflow["nsfw_injection"], instruction,
        connection["provider"], connection["endpoint"], connection["model"], "",
        connection["temperature"], connection["timeout"], connection["max_tokens"], connection["send_temperature"],
        workflow["remove_bad"], workflow["remove_terms"], workflow["shuffle"], workflow["spaces"],
        workflow["max_tags"], "Plain Prompt", 1, cancel_event,
        connection_settings=connection, preserve_inference_settings=True,
    )


def _inline_cache_result_rows(stage, page=1):
    items = (stage or {}).get("items", [])
    page = max(1, min(int(page or 1), max(1, (len(items) + CACHE_PAGE_SIZE - 1) // CACHE_PAGE_SIZE)))
    return [[index + 1, item["record"]["id"], item["result"]]
            for index, item in enumerate(items)
            if (page - 1) * CACHE_PAGE_SIZE <= index < page * CACHE_PAGE_SIZE]


def _inline_cache_result_view(stage, page=1):
    rows = _inline_cache_result_rows(stage, page)
    actual_page = (rows[0][0] - 1) // CACHE_PAGE_SIZE + 1 if rows else 1
    return rows, (rows[0][1] if rows else 0), (rows[0][2] if rows else ""), actual_page


def _inline_cache_stage_counts(stage):
    tasks = list((stage or {}).get("tasks") or [])
    items = list((stage or {}).get("items") or [])
    total = len(tasks) if tasks else len(items)
    processed = len(items)
    return total, processed, max(0, total - processed), len((stage or {}).get("errors") or {})


def _inline_cache_stage_message(stage, prefix=""):
    total, processed, remaining, failed = _inline_cache_stage_counts(stage)
    counts = f"总计 {total} · 已处理 {processed} · 未处理 {remaining} · 失败 {failed}"
    return f"{prefix}；{counts}" if prefix else counts


def _save_inline_processed_result(stage, record, result):
    """Persist an inline transformation in the separate processed-result DB."""
    run_id = str((stage or {}).get("run_id") or "inline")
    source_id = str((record or {}).get("id") or "")
    source_ref = f"inline_processed:{run_id}:{source_id}"
    result_id = RESULT_DB.save_prompt(
        str(result or "").strip(), "", (stage or {}).get("config", {}).get("preset", "Danbooru Tags"),
        (stage or {}).get("config", {}).get("resolved_model", ""), 0,
        tags=f"source_cache_id:{source_id}", score_source="processed",
        source_kind="inline_processed", source_ref=source_ref, dedupe=False,
    )
    return int(result_id)


def _processed_result_records(query=""):
    return RESULT_DB.list_prompts(str(query or ""), limit=None, output_mode="", base_model="")


def _processed_result_choices(records):
    choices = []
    for record in records[:1000]:
        preview = " ".join(str(record.get("prompt") or "").split())
        if len(preview) > 72:
            preview = preview[:69] + "..."
        choices.append((f"#{record['id']} · {preview}", str(record["id"])))
    return choices


def _processed_result_rows(records):
    return [[
        record.get("visible_position", ""), record["id"], record.get("output_mode", ""),
        record.get("base_model", ""), " ".join(str(record.get("prompt") or "").split()),
        record.get("source_ref", ""),
    ] for record in records[:1000]]


def _processed_result_view(query=""):
    records = _processed_result_records(query)
    return (
        gr.update(value=_processed_result_rows(records)),
        gr.update(choices=_processed_result_choices(records), value=[]),
        f"处理结果 {len(records)} 条",
    )


def _processed_result_append(selected_ids, query="", scope="selected"):
    records = _processed_result_records(query)
    selected = {str(value) for value in _selected_values(selected_ids)}
    if scope != "filtered":
        if not selected:
            return "", "请先选择处理结果。"
        records = [record for record in records if str(record["id"]) in selected]
    prompts = [str(record.get("prompt") or "").strip() for record in records]
    prompts = [prompt for prompt in prompts if prompt]
    return "\n".join(prompts), (f"已准备 {len(prompts)} 条处理结果，可追加到当前 Prompt。" if prompts else "没有可追加的处理结果。")


def _processed_result_enqueue(selected_ids, query="", queue_ids=None, generation_settings=None, scope="selected"):
    records = _processed_result_records(query)
    selected = {str(value) for value in _selected_values(selected_ids)}
    if scope != "filtered":
        if not selected:
            return list(queue_ids or []), "请先选择处理结果。"
        records = [record for record in records if str(record["id"]) in selected]
    if not records:
        return list(queue_ids or []), "没有可入队的处理结果。"
    prompts = [str(record.get("prompt") or "").strip() for record in records]
    prompts = [prompt for prompt in prompts if prompt]
    if not prompts:
        return list(queue_ids or []), "选中的处理结果没有有效 Prompt。"
    batch_ids = list(queue_ids or [])
    settings = _parse_generation_settings(generation_settings)
    submitted = Counter()
    for batch_id in dict.fromkeys(batch_ids):
        for job in DB.list_server_queue(batch_id, 2000):
            config = job.get("config") or {}
            if (
                job.get("status") in {"pending", "running", "completed"}
                and config.get("source") == "processed_result_db"
                and _parse_generation_settings(config.get("generation_settings")) == settings
            ):
                submitted[str(job.get("request") or "").strip()] += 1
    remaining = []
    for prompt in prompts:
        if submitted[prompt]:
            submitted[prompt] -= 1
        else:
            remaining.append(prompt)
    prompts = remaining
    if not prompts:
        return batch_ids, "选中的处理结果已入队，没有新的未入队结果。"
    submitted_batches = 0
    try:
        for start in range(0, len(prompts), 1000):
            snapshot = _enqueue_server_queue({
                "requests": prompts[start:start + 1000], "target": "txt2img",
                "config": {
                    "direct_prompt": True, "cache_result": False, "source": "processed_result_db",
                    "generation_settings": settings,
                },
            })
            batch_ids.append(snapshot["batch_id"])
            submitted_batches += 1
    except Exception as error:
        return batch_ids, f"处理结果入队失败：{_safe_error(error)}"
    return batch_ids, f"已将 {len(prompts)} 条处理结果加入 {submitted_batches} 个 txt2img 后台队列。"


def _inline_cache_process_stage(stage, cancel_id):
    if not stage or not stage.get("tasks") or not stage.get("config"):
        yield stage or {}, _inline_cache_result_rows(stage), "", "没有可继续的缓存处理任务。"
        return
    config = stage["config"]
    completed_ids = {str(item["record"]["id"]) for item in stage.get("items", [])}
    pending = [task for task in stage["tasks"] if str(task["record"]["id"]) not in completed_ids]
    event_key, event = _png_batch_cancel_event(cancel_id)
    event.clear()
    interval = max(1, (len(pending) + 99) // 100)
    stopped_by_error = False
    try:
        for index, task in enumerate(pending, 1):
            if event.is_set():
                break
            record = task["record"]
            ident = str(record["id"])
            try:
                result, transform_status = _inline_cache_transform(
                    task["source"], config["action"], config["preset"], config["base_model"],
                    config["instruction"], event,
                )
                if event.is_set():
                    break
                if not str(result or "").strip():
                    raise ValueError(transform_status or "LLM 未返回结果")
            except Exception as error:
                stage.setdefault("errors", {})[ident] = _safe_error(error)
                stopped_by_error = True
                break
            stage.setdefault("errors", {}).pop(ident, None)
            result_db_id = _save_inline_processed_result(stage, record, result)
            stage.setdefault("items", []).append({
                "record": dict(record), "result": str(result).strip(),
                "preset": config["preset"], "base_model": config["resolved_model"],
                "result_db_id": result_db_id,
            })
            if index % interval == 0 and index < len(pending):
                rows = _inline_cache_result_rows(stage)
                yield stage, rows, rows[0][2] if rows else "", _inline_cache_stage_message(stage, "处理中")
        total, processed, remaining, _failed = _inline_cache_stage_counts(stage)
        stage["complete"] = bool(total) and remaining == 0
        if stage["complete"]:
            prefix = "处理完成，已存入处理结果库"
        elif event.is_set():
            prefix = "已取消，已完成结果仍保留；可继续处理未完成"
        elif stopped_by_error:
            prefix = "处理暂停；修正连接或设置后可继续处理未完成"
        else:
            prefix = "处理尚未完成，可继续处理未完成"
        message = _inline_cache_stage_message(stage, prefix)
    finally:
        with _PNG_BATCH_CANCEL_LOCK:
            if _PNG_BATCH_CANCEL_EVENTS.get(event_key) is event:
                _PNG_BATCH_CANCEL_EVENTS.pop(event_key, None)
    rows = _inline_cache_result_rows(stage)
    yield stage, rows, rows[0][2] if rows else "", message


def _inline_cache_run(scope, record, source, query, output_mode, base_model_filter,
                      action, preset, base_model, instruction, cancel_id):
    records = [record] if scope == "当前记录" and record else (
        _cache_records(query, 0, output_mode, base_model_filter) if scope == "全部筛选结果" else [])
    canonical_preset = _canonical_preset(preset)
    _, resolved_model = _resolve_preset_model(canonical_preset, base_model)
    stage = {
        "version": 2, "run_id": uuid.uuid4().hex,
        "tasks": [
            {"record": dict(item), "source": str(source if scope == "当前记录" else item["prompt"])}
            for item in records
        ],
        "items": [], "errors": {}, "complete": False,
        "config": {
            "action": _canonical_action(action), "preset": canonical_preset,
            "base_model": _canonical_base_model(base_model), "resolved_model": resolved_model,
            "instruction": str(instruction or ""),
        },
    }
    if not records:
        yield stage, [], "", "请先点击缓存记录，或调整筛选。"
        return
    yield from _inline_cache_process_stage(stage, cancel_id)


def _inline_cache_resume(stage, cancel_id):
    yield from _inline_cache_process_stage(stage, cancel_id)


def _inline_cache_enqueue_results(stage, selected_id=0, edited_result="", generation_settings=None, queue_ids=None):
    batch_ids = list(dict.fromkeys([*(queue_ids or []), *((stage or {}).get("queue_ids") or [])]))
    if not stage:
        return stage or {}, batch_ids, "没有已处理结果可入队。", _server_queue_gallery(batch_ids)
    queued_ids = {str(item) for item in (stage or {}).get("queued_ids", [])}
    items = [
        dict(item) for item in (stage or {}).get("items", [])
        if str(item["record"]["id"]) not in queued_ids
    ]
    if not items:
        return stage, batch_ids, "没有新的未入队处理结果。", _server_queue_gallery(batch_ids)
    selected_id = str(selected_id or "")
    edited_result = str(edited_result or "").strip()
    pending = []
    for item in items:
        prompt = edited_result if edited_result and str(item["record"]["id"]) == selected_id else str(item["result"] or "").strip()
        if prompt:
            pending.append((str(item["record"]["id"]), prompt))
    if not pending:
        return stage, batch_ids, "没有有效 Prompt 可入队。", _server_queue_gallery(batch_ids)
    submitted_batches = 0
    updated_stage = {**stage, "queued_ids": list(stage.get("queued_ids") or []), "queue_ids": batch_ids}
    try:
        for start in range(0, len(pending), 1000):
            chunk = pending[start:start + 1000]
            snapshot = _enqueue_server_queue({
                "requests": [prompt for _, prompt in chunk], "target": "txt2img",
                "config": {
                    "direct_prompt": True, "cache_result": False,
                    "generation_settings": _parse_generation_settings(generation_settings),
                },
            })
            batch_ids.append(snapshot["batch_id"])
            updated_stage["queued_ids"].extend(ident for ident, _ in chunk)
            submitted_batches += 1
    except Exception as error:
        return updated_stage, batch_ids, f"生图队列提交失败：{_safe_error(error)}", _server_queue_gallery(batch_ids)
    return updated_stage, batch_ids, (
        f"已将 {len(pending)} 条已处理 Prompt 加入 {submitted_batches} 个 txt2img 后台生图队列。"
        "队列由 Forge 进程继续执行，关闭或离开浏览器不会停止。"
    ), _server_queue_gallery(batch_ids)


def _inline_cache_queue_status(batch_ids):
    identifiers = [str(item) for item in (batch_ids or []) if str(item).strip()]
    jobs = [job for batch_id in identifiers for job in DB.list_server_queue(batch_id, 2000)]
    counts = Counter(job["status"] for job in jobs)
    settings = {}
    for job in jobs:
        settings = _parse_generation_settings((job.get("config") or {}).get("generation_settings"))
        if settings:
            break
    parameter_hint = ""
    if settings:
        parameter_hint = (
            f" · 参数：{settings.get('width', '?')}×{settings.get('height', '?')}，"
            f"{settings.get('steps', '?')} 步，CFG {settings.get('cfg_scale', '?')}，"
            f"{settings.get('sampler_name', '?')}"
        )
    return (
        f"生图队列：总计 {len(jobs)} · 完成 {counts['completed']} · 等待 {counts['pending']} · "
        f"运行中 {counts['running']} · 失败 {counts['error']} · 取消 {counts['cancelled']}" + parameter_hint
        if jobs else "尚未提交生图队列。"
    )


def _server_queue_gallery(batch_ids):
    identifiers = [str(item) for item in (batch_ids or []) if str(item).strip()]
    images = []
    for batch_id in identifiers:
        for job in DB.list_server_queue(batch_id, 2000):
            for image in job.get("images", []) or []:
                value = str(image or "").strip()
                if value:
                    images.append((value if value.startswith("data:") else f"data:image/png;base64,{value}", f"队列 {job.get('position', '')}"))
    return images[-100:]


def _processed_result_queue_view(batch_ids):
    return _inline_cache_queue_status(batch_ids), _server_queue_gallery(batch_ids)


def _inline_cache_cancel_queues(batch_ids):
    for batch_id in [str(item) for item in (batch_ids or []) if str(item).strip()]:
        _server_queue_cancel_event(batch_id).set()
        DB.cancel_server_queue(batch_id)
        _release_server_queue_cancel_event(batch_id)
    return _inline_cache_queue_status(batch_ids) + "；已请求取消。"


def _inline_cache_save(stage, scope, selected_id, edited_result):
    if not stage or not stage.get("complete"):
        return stage or {}, "本次处理未完整完成；请重新处理后再保存。"
    items = stage["items"]
    if scope == "当前结果":
        items = [item for item in items if str(item["record"]["id"]) == str(selected_id)]
        items = [{**item, "result": str(edited_result or "").strip()} for item in items]
    if not items or any(not item["result"].strip() for item in items):
        return stage, "请选择有效结果，正面提示词不能为空。"
    try:
        # All snapshots are checked within the same transaction as the writes.
        # A concurrent cache edit or deletion rolls the entire save back.
        with DB.lock, DB._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for item in items:
                before = item["record"]
                row = conn.execute("SELECT * FROM prompts WHERE id=?", (before["id"],)).fetchone()
                if row is None or any(row[key] != value for key, value in before.items() if key in row.keys()):
                    raise ValueError(f"缓存记录 #{before['id']} 已变化，请重新载入并处理。")
                updated = {**dict(row), "prompt": item["result"], "output_mode": item["preset"], "base_model": item["base_model"]}
                conn.execute("UPDATE prompts SET prompt=?, output_mode=?, base_model=?, content_hash=?, updated_at=? WHERE id=?",
                             (updated["prompt"], updated["output_mode"], updated["base_model"],
                              DB._record_hash(updated), int(time.time()), before["id"]))
        # Remove saved results so each remaining result can be saved independently.
        saved_ids = {item["record"]["id"] for item in items}
        remaining = [item for item in stage["items"] if item["record"]["id"] not in saved_ids]
        return ({**stage, "items": remaining} if remaining else {}), f"已保存 {len(items)} 条缓存记录。"
    except Exception as error:
        return stage, f"未保存：{_safe_error(error)}"


def _inline_cache_save_view(stage, scope, selected_id, edited_result, page, record, source):
    remaining, message = _inline_cache_save(stage, scope, selected_id, edited_result)
    if not stage or remaining is stage:
        # Validation failed: keep the user's selection and unsaved edits intact.
        return (stage, message, _inline_cache_result_rows(stage, page), selected_id,
                edited_result, page, record, source)
    if record and any(item["record"]["id"] == record["id"] for item in stage["items"]
                      if scope != "当前结果" or str(item["record"]["id"]) == str(selected_id)):
        record = DB.get_prompt(record["id"]) or {}
        source = record.get("prompt", "")
    return remaining, message, *_inline_cache_result_view(remaining, page), record, source


def _inline_cache_write(current, result, mode):
    result = str(result or "").strip()
    if not result:
        return current, "请先选择处理结果。"
    current = str(current or "").strip()
    return (current + "\n" + result if mode == "append" and current else result), "已写入正面 Prompt。"


def _cache_picker_view(query="", output_mode="全部", base_model="全部", page=1):
    table, _, message, page = _cache_page_update(query, 0, output_mode, base_model, page)
    choices = [(f"#{row[1]} · {str(row[4])[:90]}", str(row[1])) for row in table["value"]]
    return gr.update(choices=choices, value=None), message.split("。", 1)[0], page, {}, ""


def _cache_picker_select(ident):
    try:
        record = DB.get_prompt(int(ident)) if ident else None
    except (TypeError, ValueError):
        record = None
    return record or {}, (record or {}).get("prompt", "")


def _create_inline_cache_panel(slot, prompt_target, queue_controls):
    """Select source records for processing; cache management stays in its tab."""
    prefix = f"llm_prompt_studio_{slot}_cache"
    workflow = _workflow_settings()
    initial_picker, initial_message, _, _, _ = _cache_picker_view()
    with gr.Accordion("缓存 Prompt 处理", open=False, elem_id=prefix, elem_classes=["lps-inline-cache"]):
        with gr.Row(elem_classes=["lps-form-row"]):
            query = gr.Textbox(label="搜索缓存", lines=1, scale=3, elem_id=f"{prefix}_query")
            fmt = gr.Dropdown(label="格式筛选", choices=["全部"] + list(PRESET_UI_CHOICES), value="全部", elem_id=f"{prefix}_format")
            model_filter = gr.Dropdown(label="模型筛选", choices=["全部"] + list(MODEL_UI_CHOICES), value="全部", elem_id=f"{prefix}_model_filter")
            refresh = gr.Button("筛选 / 刷新", size="sm", elem_id=f"{prefix}_refresh")
        with gr.Row(elem_classes=["lps-form-row"]):
            previous = gr.Button("上一页", size="sm", elem_id=f"{prefix}_previous")
            page = gr.Number(label="缓存页码", value=1, minimum=1, precision=0, elem_id=f"{prefix}_page")
            following = gr.Button("下一页", size="sm", elem_id=f"{prefix}_next")
            browse_status = gr.Markdown(initial_message)
        picker = gr.Dropdown(label="选择原始缓存", choices=initial_picker["choices"], value=None,
                             elem_id=f"{prefix}_selection")
        selected = gr.State({})
        with gr.Accordion("查看 / 编辑本次输入", open=False):
            source = gr.Textbox(label="原始 Prompt", lines=4, max_lines=24, elem_id=f"{prefix}_source")
        with gr.Row(elem_classes=["lps-form-row"]):
            preset = gr.Dropdown(label="输出预设", choices=PRESET_UI_CHOICES, value=workflow["preset"], elem_id=f"{prefix}_preset")
            model = gr.Dropdown(label="目标模型", choices=MODEL_UI_CHOICES, value=workflow["base_model"], elem_id=f"{prefix}_model")
            action = gr.Radio(label="操作", choices=ACTION_UI_CHOICES, value="Polish", elem_id=f"{prefix}_action")
        instruction = gr.Textbox(label="处理要求（可选）", lines=1, max_lines=6, elem_id=f"{prefix}_instruction")
        with gr.Row(elem_classes=["lps-form-row"]):
            scope = gr.Radio(label="处理范围", choices=["当前记录", "全部筛选结果"], value="当前记录", elem_id=f"{prefix}_scope")
            run = gr.Button("开始处理", variant="primary", size="sm", elem_id=f"{prefix}_run")
            resume = gr.Button("继续处理未完成", size="sm", elem_id=f"{prefix}_resume")
            cancel = gr.Button("取消", variant="stop", size="sm", elem_id=f"{prefix}_cancel")
        status = gr.Markdown("总计 0 · 已处理 0 · 未处理 0 · 失败 0", elem_id=f"{prefix}_status")
        stage, cancel_id, result_id = (
            gr.State({}), gr.State(lambda: uuid.uuid4().hex), gr.State(0),
        )
        with gr.Column(visible=False) as result_panel:
            result_page = gr.Number(label="结果页码（每页 50 条）", value=1, minimum=1, precision=0, elem_id=f"{prefix}_result_page")
            results = gr.Dataframe(headers=["序号", "ID", "处理后的正面提示词"], datatype=["number", "number", "str"],
                                   type="array", interactive=False, wrap=True, height=260, visible=False,
                                   elem_id=f"{prefix}_results", elem_classes=["lps-table", "lps-cache-table", "lps-result-table"])
            result = gr.Textbox(label="选中结果（可编辑）", lines=5, max_lines=24, elem_id=f"{prefix}_result")
            with gr.Row(elem_classes=["lps-form-row"]):
                write_mode = gr.Radio(label="写入方式", choices=[("追加", "append"), ("替换", "replace")], value="append", elem_id=f"{prefix}_write_mode")
                write = gr.Button("写入 txt2img", size="sm", interactive=prompt_target is not None, elem_id=f"{prefix}_write")
                enqueue_images = gr.Button(
                    "将已处理结果加入生图队列", variant="primary", size="sm",
                    elem_id=f"{prefix}_enqueue_images",
                )
            if prompt_target is None:
                gr.Markdown("txt2img 尚未就绪，写入不可用。")
            enqueue_status = gr.Markdown(elem_id=f"{prefix}_queue_status")
            with gr.Accordion("覆盖原始缓存", open=False):
                with gr.Row(elem_classes=["lps-form-row"]):
                    save_one = gr.Button("用当前结果覆盖原始记录", size="sm", elem_id=f"{prefix}_save_one")
                    save_all = gr.Button("用全部结果覆盖原始记录", size="sm", elem_id=f"{prefix}_save_all")

        def process(*args):
            for snapshot, rows, text, message in _inline_cache_run(*args):
                yield snapshot, gr.update(value=rows, visible=bool(rows)), text, message, (rows[0][1] if rows else 0), 1, gr.update(visible=bool(rows))

        def resume_process(snapshot, ident, edited, request_id):
            if edited and ident:
                for item in (snapshot or {}).get("items", []):
                    if str(item["record"]["id"]) == str(ident):
                        item["result"] = str(edited).strip()
                        break
            for updated, rows, text, message in _inline_cache_resume(snapshot, request_id):
                yield updated, gr.update(value=rows, visible=bool(rows)), text, message, (rows[0][1] if rows else 0), 1, gr.update(visible=bool(rows))

        def result_browse(s, p):
            rows, ident, text, actual_page = _inline_cache_result_view(s, p)
            return gr.update(value=rows, visible=bool(rows)), ident, text, actual_page

        def select_result(stage_value, page_value, evt: gr.SelectData):
            rows = _inline_cache_result_rows(stage_value, page_value)
            try:
                row = rows[int(evt.index[0])]
            except (TypeError, ValueError, IndexError):
                return 0, ""
            ident = _table_row_id([row], 0)
            return int(ident or 0), row[2] if ident and len(row) > 2 else ""

        def save_current(s, i, r, p, record, source_text):
            return save_view(s, "当前结果", i, r, p, record, source_text)

        def save_everything(s, i, r, p, record, source_text):
            return save_view(s, "全部处理结果", i, r, p, record, source_text)

        def save_view(s, save_scope, i, r, p, record, source_text):
            values = list(_inline_cache_save_view(s, save_scope, i, r, p, record, source_text))
            values[2] = gr.update(value=values[2], visible=bool(values[2]))
            values.append(gr.update(visible=bool(values[0])))
            return values

        browse_inputs = [query, fmt, model_filter, page]
        browse_outputs = [picker, browse_status, page, selected, source]
        refresh.click(_cache_picker_view, inputs=[query, fmt, model_filter], outputs=browse_outputs)
        query.submit(_cache_picker_view, inputs=[query, fmt, model_filter], outputs=browse_outputs)
        previous.click(lambda q, f, m, p: _cache_picker_view(q, f, m, int(p or 1) - 1), inputs=browse_inputs, outputs=browse_outputs)
        following.click(lambda q, f, m, p: _cache_picker_view(q, f, m, int(p or 1) + 1), inputs=browse_inputs, outputs=browse_outputs)
        page.submit(_cache_picker_view, inputs=browse_inputs, outputs=browse_outputs)
        picker.input(_cache_picker_select, inputs=picker, outputs=[selected, source])
        preset.change(_recommended_base_model_for_preset, inputs=preset, outputs=model)
        run.click(process, inputs=[scope, selected, source, query, fmt, model_filter, action, preset, model, instruction, cancel_id],
                  outputs=[stage, results, result, status, result_id, result_page, result_panel])
        resume.click(
            resume_process, inputs=[stage, result_id, result, cancel_id],
            outputs=[stage, results, result, status, result_id, result_page, result_panel],
        )
        cancel.click(_cancel_png_batch, inputs=cancel_id, outputs=status, queue=False)
        result_page.submit(result_browse, inputs=[stage, result_page], outputs=[results, result_id, result, result_page])
        results.select(select_result, inputs=[stage, result_page], outputs=[result_id, result])
        save_inputs = [stage, result_id, result, result_page, selected, source]
        save_outputs = [stage, status, results, result_id, result, result_page, selected, source, result_panel]
        def refresh_saved(q, f, m, p, record):
            choices, message, actual_page, _, _ = _cache_picker_view(q, f, m, p)
            if record:
                ident = str(record["id"])
                if not any(value == ident for _, value in choices["choices"]):
                    choices["choices"].append((f"当前 #{ident} · {record['prompt'][:90]}", ident))
                choices["value"] = ident
            return choices, message, actual_page

        for button, callback in [(save_one, save_current), (save_all, save_everything)]:
            button.click(callback, inputs=save_inputs, outputs=save_outputs).then(
                refresh_saved, inputs=[*browse_inputs, selected], outputs=[picker, browse_status, page])
        if prompt_target is not None:
            write.click(_inline_cache_write, inputs=[prompt_target, result, write_mode], outputs=[prompt_target, status])
        enqueue_event = enqueue_images.click(
            _inline_cache_enqueue_results,
            inputs=[stage, result_id, result, queue_controls["settings"], queue_controls["ids"]],
            outputs=[stage, queue_controls["ids"], enqueue_status, queue_controls["gallery"]],
            js="(stage, resultId, result, settings, ids) => [stage, resultId, result, window.llmPromptStudioAutoLoop.readTxt2imgSettings() || settings, ids]",
        )
        enqueue_event.then(
            lambda ids: (*_processed_result_queue_view(ids), gr.update(open=True)),
            inputs=queue_controls["ids"], outputs=[queue_controls["status"], queue_controls["gallery"], queue_controls["panel"]], queue=False,
        )


def _inline_source_controls(source):
    names = {"cache": "原始缓存", "processed_cache": "处理结果"}
    name = names.get(source)
    return (
        gr.update(visible=name is None),
        (f"{name}：勾选缓存注入后，Forge 每张图读取一条；Batch Count / Batch Size 和无限生图由 Forge 控制。" if name else "使用 LLM 提示词工作室的模型与推理设置。"),
        gr.update(visible=name is not None),
    )


def _create_inline_panel(slot, prompt_target):
    workflow = _workflow_settings()
    # Keep the txt2img injection focused on optional prompt batch generation.
    # Cache processing is rendered in the Studio tab so it has one canonical UI.
    with gr.Column(elem_id=f"llm_prompt_studio_{slot}_inline", elem_classes=["lps-inline-workbench"]):
        with gr.Accordion("Prompt 批量生成与缓存注入", open=False, elem_id=f"llm_prompt_studio_{slot}_inline_batch"):
            inline_source = gr.Radio(
                label="Prompt 来源", choices=[
                    ("LLM 自动生成", "llm"),
                    ("原始缓存库", "cache"),
                    ("处理结果库", "processed_cache"),
                ], value="llm", elem_classes=["lps-source-picker"],
                elem_id=f"llm_prompt_studio_{slot}_inline_source",
            )
            source_hint = gr.Markdown(
                "使用 LLM 提示词工作室的模型与推理设置。",
                elem_id=f"llm_prompt_studio_{slot}_inline_source_hint", elem_classes=["lps-source-hint"],
            )
            inline_cache_cursor = gr.Number(
                label="缓存读取序号（ID）", value=None, minimum=0, precision=0, visible=False,
                elem_id=f"llm_prompt_studio_{slot}_inline_cache_cursor", scale=1,
            )
            with gr.Column(elem_id=f"llm_prompt_studio_{slot}_inline_llm") as llm_fields:
                with gr.Row(elem_classes=["lps-inline-writing"]):
                    request = gr.Textbox(
                        label="创作要求", lines=2, max_lines=8, placeholder="描述主体、场景与风格",
                        elem_id=f"llm_prompt_studio_{slot}_inline_request",
                    )
                    inline_variation = gr.Textbox(
                        label="变化维度（可选）", lines=2, max_lines=8,
                        placeholder="动作、场景、镜头、光线",
                        elem_id=f"llm_prompt_studio_{slot}_inline_variation",
                    )
                with gr.Row(elem_classes=["lps-template-picker"]):
                    inline_template_choice = gr.Dropdown(
                        label="快速模板", choices=_template_choices(),
                        value=_default_template_choice(), elem_id=f"llm_prompt_studio_{slot}_inline_template_choice",
                    )
                    inline_template_button = gr.Button("填入模板", size="sm", scale=0, min_width=100, elem_id=f"llm_prompt_studio_{slot}_inline_template_button")
                    _INLINE_TEMPLATE_COMPONENTS[slot] = inline_template_choice
                _INLINE_TEMPLATE_EDITORS[slot] = _create_template_editor(
                    f"llm_prompt_studio_{slot}_inline", inline_template_choice,
                )
            inline_cache_enabled = gr.Checkbox(
                label="启用缓存 Prompt 注入（使用 Forge 原生 Generate）", value=False,
                elem_id=f"llm_prompt_studio_{slot}_inline_cache_enabled",
                info="勾选后，每张图读取一条缓存；基础 Prompt 不被改写，数量、批次、无限生图和停止由 Forge 控件控制。",
            )
            with gr.Row(elem_classes=["lps-form-row", "lps-inline-merge"]) as inline_merge:
                inline_write_mode = gr.Dropdown(
                    label="与当前 Prompt 合并方式",
                    choices=[
                        ("追加到后面", "append_end"),
                        ("追加到前面", "append_start"),
                        ("替换当前 Prompt", "replace"),
                        ("插入到标记位置", "marker"),
                    ],
                    value="append_end",
                    elem_id=f"llm_prompt_studio_{slot}_inline_write_mode",
                )
                inline_marker = gr.Textbox(
                    label="插入标记", value="{{LLM}}", max_lines=1, visible=False,
                    placeholder="例如 {{LLM}}",
                    elem_id=f"llm_prompt_studio_{slot}_inline_marker",
                )
            inline_loop_status = gr.HTML("缓存注入关闭 · Forge 原生 Generate 保持原行为", elem_id=f"llm_prompt_studio_{slot}_inline_loop_status", elem_classes=["lps-status"])
            gr.HTML("", elem_id=f"llm_prompt_studio_{slot}_inline_queue_log", elem_classes=["lps-auto-loop-log"])
            source_outputs = [llm_fields, source_hint, inline_cache_cursor]
            inline_source.change(_inline_source_controls, inputs=[inline_source], outputs=source_outputs, queue=False)
            inline_source.change(
                fn=None, inputs=[inline_source], outputs=[inline_cache_cursor],
                js=f"(source) => window.llmPromptStudioAutoLoop.syncCacheCursor('{slot}', source)", queue=False,
            )
            inline_write_mode.change(lambda mode: gr.update(visible=mode == "marker"), inputs=inline_write_mode, outputs=inline_marker, queue=False)
            inline_template_button.click(_template_request, inputs=inline_template_choice, outputs=request)

def _wd14_model_choices(directory="", current=""):
    choices, default = discover_local_models(directory)
    paths = {path for _label, path in choices}
    DB.set_setting("wd14_local_directory", str(directory or "").strip())
    return (gr.update(choices=choices, value=current if current in paths else default),
            _wd14_model_status(choices), gr.update(open=not bool(choices)))


def _wd14_model_status(choices):
    return f"已检测到 {len(choices)} 个本地模型" if choices else "未检测到完整的 WD14 / CL 模型"


def _wd14_download_details(model_id):
    if model_id not in MODEL_CATALOG:
        return "", "", gr.update(interactive=False)
    links = "官方手动下载：" + " · ".join(f"[{name}]({url})" for name, url in download_links(model_id).items())
    return links, str(model_directory(model_id)), gr.update(interactive=True)


def _wd14_download_model(model_id, directory="", current=""):
    if model_id not in MODEL_CATALOG:
        yield gr.update(), "请先选择要下载的模型。", gr.update(open=True), gr.update(interactive=False)
        return
    yield gr.update(), "正在检查所选模型…", gr.update(open=True), gr.update(interactive=False)
    try:
        for message, model_path in download_model(model_id, directory):
            if model_path:
                model, status, panel = _wd14_model_choices(directory, model_path)
                yield model, f"{message} {status}", panel, gr.update(interactive=True)
            else:
                yield gr.update(), message, gr.update(), gr.update(interactive=False)
    except Exception as error:
        yield gr.update(), f"下载失败：{_safe_error(error)}。可重试或使用下方官方链接。", gr.update(open=True), gr.update(interactive=True)


def _wd14_interrogate(image, model, threshold, character_threshold=0.85):
    return interrogate_local(image, model, threshold, character_threshold)


def _wd14_process(image, model, threshold, character_threshold, *llm_args):
    tags, status = _wd14_interrogate(image, model, threshold, character_threshold)
    if not tags:
        return tags, "", status
    result, llm_status = _expand_or_polish(tags, *llm_args)
    return tags, result, f"{status} · {llm_status}"



_WD14_BATCH_JOBS: dict[str, ImageBatchJob] = {}
_WD14_BATCH_LOCK = threading.Lock()


def _wd14_batch_view(snapshot):
    counts = snapshot["counts"]
    status = (f"总数 {snapshot['total']} · 已完成 {counts['completed']} · "
              f"未处理 {counts['pending']} · 失败 {counts['failed']} · 空标签 {counts['skipped']}")
    if snapshot.get("cancelled"):
        status = "已停止 · " + status
    labels = {"completed": "已完成", "failed": "失败", "pending": "未处理", "skipped": "空标签"}
    rows = [[index, record["path"], labels[record["status"]], record["prompt"], record["error"]]
            for index, record in enumerate(snapshot["records"], 1)]
    # Gradio's Dataframe can retain stale rows during streamed updates. Send an
    # immutable, escaped HTML view; the complete records remain in server state.
    header = "".join(f'<th scope="col">{label}</th>' for label in ("序号", "图片", "状态", "Prompt", "错误"))
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>" for row in rows)
    table = f'<div class="lps-wd14-results"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'
    return status, table, snapshot["records"]


def _wd14_run_batch(folder, recursive, model, threshold, character_threshold, task_id, mode="start"):
    key = str(task_id)
    error_message = ""
    with _WD14_BATCH_LOCK:
        previous = _WD14_BATCH_JOBS.get(key)
        try:
            if previous is not None and getattr(previous, "_studio_running", False):
                raise ValueError("当前批次正在处理。")
            signature = (str(Path(str(folder or "")).expanduser().resolve()), str(model or ""),
                         float(threshold), float(character_threshold), bool(recursive))
            if not model:
                raise ValueError("请先选择本地反推模型。")
            if mode == "start":
                job = ImageBatchJob(folder, model, threshold, character_threshold, recursive)
                _WD14_BATCH_JOBS[key] = job
            else:
                if previous is None:
                    raise ValueError("请先开始一个批次。")
                if previous.signature != signature:
                    raise ValueError("路径或反推参数已变化，请重新开始批次。")
                job = previous
            job.cancel.clear()
            job._studio_running = True
        except Exception as error:
            error_message = f"批次未启动：{_safe_error(error)}"
    if error_message:
        yield error_message, gr.update(), gr.update()
        return
    def infer(image, model_path, general, character):
        tags, message = interrogate_local(image, model_path, general, character)
        if not tags and ("失败" in message or "缺少" in message):
            raise RuntimeError(message)
        return tags
    try:
        yield _wd14_batch_view(job.snapshot())
        for snapshot in job.iter_run(interrogate_fn=infer, retry_failed=mode == "retry"):
            yield _wd14_batch_view(snapshot)
        yield _wd14_batch_view(job.snapshot())
    finally:
        with _WD14_BATCH_LOCK:
            job._studio_running = False


def _wd14_cancel_batch(task_id):
    with _WD14_BATCH_LOCK:
        job = _WD14_BATCH_JOBS.get(str(task_id))
        if job is not None:
            job.cancel.set()
    return "已请求停止，当前图片完成后暂停。" if job is not None else "当前没有反推批次。"


def _wd14_release_batch(task_id):
    with _WD14_BATCH_LOCK:
        job = _WD14_BATCH_JOBS.pop(str(task_id), None)
        if job is not None:
            job.cancel.set()


def _wd14_save_records(records, base_model, *filters, output_mode="Danbooru Tags"):
    records = [record for record in (records or []) if record.get("status") == "completed" and str(record.get("prompt") or "").strip()]
    if not records:
        return "没有可保存的反推结果。", gr.update(), gr.update()
    stats = DB.sync_external_prompts([
        {"prompt": record["prompt"], "output_mode": output_mode, "base_model": base_model,
         "source_kind": "wd14", "source_ref": record.get("path") or "prompt:" + hashlib.sha256((str(record["prompt"]) + output_mode + str(base_model)).encode()).hexdigest(),
         "tags": record.get("path", ""),
         "score": 0, "score_source": "unrated"}
        for record in records
    ])
    return (f"已保存到缓存：新增 {stats['inserted']} · 更新 {stats['updated']} · 已有 {stats['unchanged']}", *_filtered_cache_updates(*filters))


def _wd14_save_single(prompt, base_model, output_mode="Danbooru Tags", *filters):
    return _wd14_save_records([{"prompt": str(prompt or "").strip(), "status": "completed"}], base_model, *filters, output_mode=output_mode)


def _wd14_write_single(prompt, current, mode, current_script=None):
    incoming = str(prompt or "").strip()
    if not incoming:
        return gr.update(), "没有可写入的反推结果。", gr.update()
    current = str(current or "").strip()
    value = f"{current.rstrip(', ')}, {incoming}" if mode == "append" and current else incoming
    script = gr.update(value="None") if current_script == "Prompts from File or Textbox" else gr.update()
    return value, "已送入 txt2img，点击生成即可生图。", script


def _wd14_write_batch(records):
    prompts = [str(record.get("prompt") or "").strip() for record in (records or []) if record.get("status") == "completed"]
    prompts = [prompt for prompt in prompts if prompt]
    if not prompts:
        return gr.update(), gr.update(), "没有可送入的反推结果。"
    # Forge uses shlex: quote each prompt so apparent --options remain text.
    lines = "\n".join("--prompt " + shlex.quote(" ".join(prompt.splitlines())) for prompt in prompts)
    return lines, gr.update(value="Prompts from File or Textbox"), f"已送入 txt2img 批量列表：{len(prompts)} 条，点击生成开始。"


def _wd14_native_batch_components():
    try:
        from modules import scripts
        runner = scripts.scripts_txt2img
        script = runner.title_map.get("prompts from file or textbox")
        textbox = next(control for control in script.controls if str(getattr(control, "elem_id", "")).endswith("_prompt_txt"))
        return textbox, runner.inputs[0]
    except (ImportError, AttributeError, StopIteration, IndexError, TypeError):
        return None, None


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
    payload["preset"] = _canonical_preset(payload.get("preset", defaults["preset"]))
    payload["base_model"] = _canonical_base_model(payload.get("base_model", defaults["base_model"]))
    payload["preset"], payload["base_model"] = _resolve_preset_model(payload["preset"], payload["base_model"])
    payload["structured_mode"] = _canonical_output_mode(payload.get("structured_mode", defaults["structured_mode"]))
    if "provider" in payload:
        payload["provider"] = _canonical_provider(payload["provider"])
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
    values = {**defaults, **payload}
    generated, system, status = _generate(
        request=values.get("request", ""),
        source_tags=values["source_tags"],
        preset=values["preset"],
        system_override=values["system_override"],
        base_model=values["base_model"],
        safety=values["safety"],
        nsfw_injection=values["nsfw_injection"],
        user_instruction=values["user_instruction"],
        provider=values["provider"],
        endpoint=values["endpoint"],
        model=values["model"],
        api_key=values["api_key"],
        temperature=values["temperature"],
        timeout=values["timeout"],
        max_tokens=values["max_tokens"],
        send_temperature=values["send_temperature"],
        remove_bad=values["remove_bad"],
        remove_terms=values["remove_terms"],
        shuffle=values["shuffle"],
        spaces=values["spaces"],
        max_tags=values["max_tags"],
        structured_mode=values["structured_mode"],
        region_count=values["region_count"],
        save_score=values["save_score"],
        cache_result=values["cache_result"],
    )
    if not generated:
        raise ValueError(status)
    return {"prompt": generated, "system_prompt": system, "status": status}


def _api_inline_generate(payload: dict[str, Any]):
    payload = dict(payload or {})
    if "preset" in payload:
        payload["preset"] = _canonical_preset(payload["preset"])
    if "base_model" in payload:
        payload["base_model"] = _canonical_base_model(payload["base_model"])
    allowed_fields = {"request", "source_tags", "variation", "template", "preset", "base_model", "safety", "slot", "request_id", "cache_result"}
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise ValueError(f"Inline API request contains unsupported fields: {', '.join(unknown_fields)}")
    if "cache_result" in payload and not isinstance(payload["cache_result"], bool):
        raise ValueError("cache_result must be a boolean")
    workflow = _workflow_settings()
    connection = _connection_settings()
    preset, base_model = _resolve_preset_model(
        payload.get("preset", workflow["preset"]), payload.get("base_model", workflow["base_model"])
    )
    safety = payload.get("safety", workflow["safety"])
    if preset not in PRESETS:
        raise ValueError(f"Unsupported prompt preset: {preset}")
    if base_model not in BASE_MODEL_GUIDANCE:
        raise ValueError(f"Unsupported base model profile: {base_model}")
    if safety not in {"SFW", "NSFW"}:
        raise ValueError(f"Unsupported safety mode: {safety}")
    slot = str(payload.get("slot") or "").strip()
    if slot not in _INLINE_CANCEL_EVENTS:
        raise ValueError(f"Unsupported inline slot: {slot}")
    request_id = str(payload.get("request_id") or uuid.uuid4().hex).strip()
    if not request_id or len(request_id) > 128:
        raise ValueError("Inline request_id must contain 1 to 128 characters")
    request = str(payload.get("request") or "").strip()
    template_key = str(payload.get("template") or "").strip()
    if template_key:
        template_request = _template_request(template_key)
        if template_request and template_request not in request:
            request = f"{request}\n\n{template_request}".strip()
    variation = str(payload.get("variation") or "").strip()
    if variation:
        request = f"{request}\n\nBATCH VARIATION SCOPE:\n{variation}" if request else variation
    cancel_event = _inline_request_event(slot, request_id)
    with _INLINE_REQUEST_LOCKS[slot]:
        try:
            if cancel_event.is_set():
                raise ValueError("Inline request cancelled")
            with _INLINE_REQUEST_CONTROL_LOCK:
                _INLINE_ACTIVE_REQUEST_IDS[slot] = request_id
                _INLINE_CANCEL_EVENTS[slot].clear()
            generated, system, status = _inline_generate(
                request, payload.get("source_tags", ""), preset,
                workflow["system_override"], base_model, safety, workflow["nsfw_injection"],
                workflow["user_instruction"], workflow["remove_bad"], workflow["remove_terms"],
                workflow["shuffle"], workflow["spaces"], workflow["max_tags"],
                workflow["structured_mode"], workflow["region_count"], workflow["save_score"],
                payload.get("cache_result", workflow["cache_result"]), cancel_event,
            )
        finally:
            _release_inline_request(slot, request_id, cancel_event)
    if not generated:
        raise ValueError(status)
    return {"prompt": generated, "system_prompt": system, "status": status}


def _api_inline_cancel(payload: dict[str, Any]):
    payload = dict(payload or {})
    unknown_fields = sorted(set(payload) - {"slot", "request_id"})
    if unknown_fields:
        raise ValueError(f"Inline cancel request contains unsupported fields: {', '.join(unknown_fields)}")
    slot = str(payload.get("slot") or "").strip()
    if slot not in _INLINE_CANCEL_EVENTS:
        raise ValueError(f"Unsupported inline slot: {slot}")
    request_id = str(payload.get("request_id") or "").strip()
    if len(request_id) > 128:
        raise ValueError("Inline request_id must contain at most 128 characters")
    _cancel_inline_generation(slot, request_id)
    return {"cancelled": True, "slot": slot, "request_id": request_id}


def _api_auto_loop_generate(payload: dict[str, Any]):
    payload = dict(payload or {})
    allowed_fields = {"request", "preset", "base_model", "safety", "cache_result"}
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise ValueError(f"Auto loop API request contains unsupported fields: {', '.join(unknown_fields)}")
    workflow = _workflow_settings()
    connection = _connection_settings()
    preset, base_model = _resolve_preset_model(
        payload.get("preset", workflow["preset"]), payload.get("base_model", workflow["base_model"])
    )
    safety = payload.get("safety", workflow["safety"])
    if preset not in PRESETS:
        raise ValueError(f"Unsupported prompt preset: {preset}")
    if base_model not in BASE_MODEL_GUIDANCE:
        raise ValueError(f"Unsupported base model profile: {base_model}")
    if safety not in {"SFW", "NSFW"}:
        raise ValueError(f"Unsupported safety mode: {safety}")
    generated, system, status = _generate_auto_loop(
        payload.get("request", ""), preset, workflow["system_override"], base_model, safety,
        workflow["nsfw_injection"], workflow["user_instruction"], connection["provider"],
        connection["endpoint"], connection["model"], "", connection["temperature"],
        connection["timeout"], connection["max_tokens"], connection["send_temperature"],
        workflow["remove_bad"], workflow["remove_terms"], workflow["shuffle"],
        workflow["spaces"], workflow["max_tags"], workflow["structured_mode"],
        workflow["region_count"], bool(payload.get("cache_result", workflow["cache_result"])),
    )
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
            connection["send_temperature"],
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
    status = f"交接 {len(records)} 条"
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
    status = f"Ranbooru {len(records)} 条"
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
    try:
        from fastapi import Depends, HTTPException
        from fastapi.security import HTTPBasic
        from modules import shared
        from secrets import compare_digest

        # Local Forge sessions should keep native Generate/Generate forever
        # alive when the browser is minimized. Do not change shared or remote
        # sessions, where the owner may intentionally control this setting.
        cmd_opts = getattr(shared, "cmd_opts", None)
        runtime_opts = getattr(shared, "opts", None)
        if (
            runtime_opts is not None
            and not bool(getattr(cmd_opts, "webui_is_non_local", False))
            and isinstance(getattr(runtime_opts, "data", None), dict)
        ):
            runtime_opts.data["keep_alive"] = True

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

        @app.post("/llm-prompt-studio/v1/inline-generate", dependencies=api_dependencies)
        def prompt_studio_inline_generate(payload: dict[str, Any]):
            try:
                return _api_inline_generate(payload)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        @app.post("/llm-prompt-studio/v1/inline-cancel", dependencies=api_dependencies)
        def prompt_studio_inline_cancel(payload: dict[str, Any]):
            try:
                return _api_inline_cancel(payload)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        @app.post("/llm-prompt-studio/v1/native-cache/prepare", dependencies=api_dependencies)
        def prompt_studio_native_cache_prepare(payload: dict[str, Any]):
            try:
                return _native_cache_prepare(payload)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        @app.post("/llm-prompt-studio/v1/native-cache/release", dependencies=api_dependencies)
        def prompt_studio_native_cache_release(payload: dict[str, Any]):
            return _native_cache_release(payload.get("token", ""), bool(payload.get("commit")))

        @app.post("/llm-prompt-studio/v1/auto-loop-generate", dependencies=api_dependencies)
        def prompt_studio_auto_loop_generate(payload: dict[str, Any]):
            try:
                return _api_auto_loop_generate(payload)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        @app.get("/llm-prompt-studio/v1/cache", dependencies=api_dependencies)
        def prompt_studio_cache(query: str = "", limit: int = 100, after_id: int | None = None):
            if after_id is not None:
                return {"records": DB.list_prompts_after(query, limit, after_id)}
            return {"records": DB.list_prompts(query, limit)}

        @app.get("/llm-prompt-studio/v1/processed-cache", dependencies=api_dependencies)
        def prompt_studio_processed_cache(query: str = "", limit: int = 100, after_id: int | None = None):
            if after_id is not None:
                return {"records": RESULT_DB.list_prompts_after(query, limit, after_id)}
            safe_limit = max(1, min(int(limit or 100), 1000))
            return {"records": RESULT_DB.list_prompts(str(query or ""), safe_limit, output_mode="", base_model="")}

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
            _server_queue_cancel_event(batch_id).set()
            cancelled = DB.cancel_server_queue(batch_id)
            _release_server_queue_cancel_event(batch_id)
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
    # Do not start the server-owned generation worker during WebUI startup.
    # The worker is started by _enqueue_server_queue after an explicit user
    # action, so launching webui.bat cannot consume an old pending queue.
    try:
        recovered_handoffs = DB.recover_stale_handoffs()
        if recovered_handoffs:
            LOGGER.warning("recovered %s stale Ranbooru handoff claims", recovered_handoffs)
    except Exception as error:
        LOGGER.warning("handoff recovery skipped: %s", error)


def on_ui_tabs():
    llm_settings = _connection_settings()
    workflow = _workflow_settings()
    ranbooru_link = _ranbooru_link_settings()
    initial_records = DB.list_prompts(limit=None)
    initial_processed_records = _processed_result_records()
    initial_handoff_table, initial_handoff_choices, initial_handoff_status = _handoff_views()
    with gr.Blocks(analytics_enabled=False, css=UI_CSS, elem_id="llm_prompt_studio") as ui:
        gr.Markdown("## LLM 提示词工作室", elem_classes=["lps-heading"])
        processed_result_queue_ids = gr.State([])
        processed_result_generation_settings = gr.Textbox(value="{}", visible=False)
        result_queue_panel = gr.Accordion("缓存结果生图队列", open=False, render=False,
                                         elem_id="llm_prompt_studio_result_queue")
        processed_result_queue_status = gr.Markdown("尚未提交生图队列。", render=False,
            elem_id="llm_prompt_studio_processed_result_queue_status")
        processed_result_gallery = gr.Gallery(label="已生成图片", columns=4, height=320, render=False,
            elem_id="llm_prompt_studio_processed_result_gallery")
        result_queue_controls = dict(ids=processed_result_queue_ids, settings=processed_result_generation_settings,
            panel=result_queue_panel, status=processed_result_queue_status, gallery=processed_result_gallery)
        with gr.Tabs(elem_id="llm_prompt_studio_main_tabs"):
            with gr.Tab("生成", elem_id="llm_prompt_studio_generate_tab"):
                with gr.Row(elem_classes=["lps-main-workbench"]):
                    with gr.Column(scale=3, elem_classes=["lps-source-column"]):
                        request = gr.Textbox(
                            label="创作要求",
                            value=_template_request(_default_template_choice()),
                            lines=4, max_lines=24,
                            placeholder="输入画面要求，文本框会随内容增高",
                            elem_id="llm_prompt_studio_request",
                        )
                        source_tags = gr.Textbox(
                            label="固定 Prompt",
                            lines=4, max_lines=24,
                            elem_id="llm_prompt_studio_source_tags",
                        )
                        with gr.Row(elem_classes=["lps-template-picker"]):
                            template_choice = gr.Dropdown(
                                label="快速模板", choices=_template_choices(),
                                value=_default_template_choice(), elem_id="llm_prompt_studio_template_choice",
                            )
                            template_button = gr.Button("套用模板", elem_id="llm_prompt_studio_template_button")
                        with gr.Accordion("自定义快速模板", open=False, elem_classes=["lps-template-editor"]):
                            template_initial = _load_custom_template_editor(_default_template_choice())
                            template_name = gr.Textbox(
                                label="模板名称", value=template_initial[0], max_lines=1, placeholder="填写新名称可另存为模板",
                                elem_id="llm_prompt_studio_template_name",
                            )
                            template_content = gr.Textbox(
                                label="模板内容", value=template_initial[1], lines=2, max_lines=10,
                                placeholder="填写会反复调用的创作要求、固定风格或输出约束",
                                elem_id="llm_prompt_studio_template_content",
                            )
                            with gr.Row():
                                template_preserve_fixed = gr.Checkbox(label="固定 Prompt 完全保留", value=template_initial[2])
                                template_obey_user = gr.Checkbox(label="严格遵从用户提示词", value=template_initial[3])
                            template_variation_dimensions = gr.Textbox(
                                label="变化维度（逗号分隔）", value=template_initial[4],
                                lines=2, elem_id="llm_prompt_studio_template_variation_dimensions",
                            )
                            template_style_rules = gr.Textbox(label="风格规则（可选）", value=template_initial[5], lines=2)
                            template_forbidden_content = gr.Textbox(label="禁止内容（可选）", value=template_initial[6], lines=2)
                            with gr.Row():
                                template_conflict_policy = gr.Dropdown(label="冲突处理", choices=[("拒绝并重试", "reject_and_retry"), ("允许", "allow")], value=template_initial[7])
                                template_duplicate_policy = gr.Dropdown(label="重复处理", choices=[("跳过并继续", "skip_and_continue"), ("接受", "accept")], value=template_initial[8])
                            with gr.Row():
                                template_save = gr.Button("保存模板", variant="primary", elem_id="llm_prompt_studio_template_save")
                                template_default = gr.Button("设为默认模板", elem_id="llm_prompt_studio_template_default")
                                template_delete = gr.Button("删除所选自定义模板", variant="stop", elem_id="llm_prompt_studio_template_delete")
                            template_status = gr.Markdown(elem_id="llm_prompt_studio_template_status")
                    with gr.Column(scale=2, elem_classes=["lps-rules-column"]):
                        with gr.Row():
                            preset = gr.Dropdown(label="System Prompt 预设", choices=PRESET_UI_CHOICES, value=workflow["preset"], elem_id="llm_prompt_studio_preset")
                            base_model = gr.Dropdown(label="模型预设", choices=MODEL_UI_CHOICES, value=workflow["base_model"], elem_id="llm_prompt_studio_base_model")
                        with gr.Accordion("编辑 System Prompt", open=False, elem_classes=["lps-preset-editor"]):
                            system_override = gr.State(_system_prompt_override_value(workflow["preset"], workflow["system_override"], workflow["base_model"]))
                            system_editor = gr.Textbox(
                                label="当前生效内容", lines=5, max_lines=20,
                                value=_system_prompt_editor_value(workflow["preset"], workflow["system_override"], workflow["base_model"]),
                                elem_id="llm_prompt_studio_system_override",
                            )
                            restore_preset = gr.Button("恢复所选预设", size="sm", elem_id="llm_prompt_studio_restore_preset")
                        safety = gr.Radio(label="内容模式", choices=["SFW", "NSFW"], value=workflow["safety"], elem_id="llm_prompt_studio_safety")
                        structured_mode = gr.Dropdown(label="输出格式", choices=OUTPUT_UI_CHOICES, value=workflow["structured_mode"])
                        with gr.Accordion("高级 Prompt 约束", open=False):
                            nsfw_injection = gr.Textbox(label="NSFW System Prompt 注入", lines=2, value=workflow["nsfw_injection"], placeholder="仅在 NSFW 模式下生效")
                            user_instruction = gr.Textbox(label="用户输出要求（低优先级）", lines=2, value=workflow["user_instruction"], placeholder="例如：只返回最多 35 个标签，不使用权重")
                        region_count = gr.Number(label="区域数量", minimum=1, maximum=8, value=workflow["region_count"], precision=0, visible=workflow["structured_mode"] != "Plain Prompt", elem_id="llm_prompt_studio_region_count")
                        with gr.Accordion("标签后处理", open=False):
                            remove_bad = gr.Checkbox(label="移除不良标签", value=workflow["remove_bad"])
                            remove_terms = gr.Textbox(label="额外排除标签 / 通配规则", value=workflow["remove_terms"], placeholder="watermark, *_text")
                            shuffle = gr.Checkbox(label="随机打乱标签", value=workflow["shuffle"])
                            spaces = gr.Checkbox(label='将“_”转换为空格', value=workflow["spaces"])
                            max_tags = gr.Slider(label="最大标签数（0 表示不限）", minimum=0, maximum=200, value=workflow["max_tags"], step=1)
                        save_score = gr.Number(value=0, visible=False)
                        cache_result = gr.State(True)
                with gr.Row(elem_classes=["lps-generation-destination"]):
                    generation_destination = gr.Radio(
                        label="生成去向", choices=[("仅保存到缓存", "cache"), ("生成并加入生图队列", "queue")],
                        value="cache", elem_id="llm_prompt_studio_generation_destination", scale=3,
                    )
                    batch_generation_count = gr.Number(
                        label="生成数量（0 = 无限）", value=1, minimum=0, maximum=200, precision=0,
                        elem_id="llm_prompt_studio_batch_generation_count", scale=1,
                    )
                with gr.Accordion("批量选项", open=False):
                    batch_sources = gr.Textbox(
                        label="逐条创作要求（可选，每行一条）", lines=2, max_lines=12,
                        placeholder="留空则按上方创作要求和数量生成",
                        elem_id="llm_prompt_studio_auto_loop_request",
                    )
                    batch_topic_pool = gr.Textbox(
                        label="随机题材（创作要求留空时使用）", lines=2,
                        placeholder="日常生活、奇幻冒险、城市夜景",
                        elem_id="llm_prompt_studio_batch_topic_pool",
                    )
                    batch_base_prompt = source_tags
                    with gr.Row():
                        batch_lock_known = gr.Checkbox(label="保留固定主体、LoRA 和权重", value=True, elem_id="llm_prompt_studio_batch_lock_known")
                        batch_sample_static = gr.Checkbox(label="静态词库抽样", value=True, elem_id="llm_prompt_studio_batch_sample_static")
                    with gr.Row():
                        batch_skip_existing = gr.Checkbox(label="跳过已有缓存的要求", value=workflow["batch_skip_existing"])
                        batch_skip_failed = gr.Checkbox(label="失败后继续", value=workflow["batch_skip_failed"])
                    batch_preview_button = gr.Button("预览任务")
                    batch_preview_status = gr.Markdown()
                with gr.Row(elem_classes=["lps-primary-actions"]):
                    generate = gr.Button("生成到缓存", variant="primary", elem_id="llm_prompt_studio_generate_button", elem_classes=["lps-primary"])
                    batch_cancel = gr.Button("停止", variant="stop", elem_id="llm_prompt_studio_generation_stop")
                batch_status = gr.Markdown("就绪", elem_id="llm_prompt_studio_batch_status", elem_classes=["lps-status"])
                output = gr.Textbox(label="生成的正面提示词", lines=8, max_lines=40, elem_id="llm_prompt_studio_output", elem_classes=["lps-output"])
                system_preview = gr.Textbox(visible=False, elem_id="llm_prompt_studio_system_preview")
                batch_task_id = gr.State(lambda: uuid.uuid4().hex)
                batch_issue_state = gr.State([])
                with gr.Accordion("本次结果", open=False):
                    batch_queue = gr.Dataframe(
                        value=[], headers=["序号", "输入", "生成结果", "状态"],
                        datatype=["number", "str", "str", "str"], interactive=False, wrap=True,
                        label="生成记录", elem_id="llm_prompt_studio_generation_results",
                    )
                    batch_issues = gr.Dataframe(
                        headers=["序号", "输入", "状态", "原因", "尝试次数"],
                        datatype=["number", "str", "str", "str", "number"], interactive=False, wrap=True,
                        label="待重试记录",
                    )
                    batch_issue_selection = gr.CheckboxGroup(label="选择重试项", choices=[])
                    with gr.Row():
                        batch_select_all_issues = gr.Button("全选")
                        batch_clear_issue_selection = gr.Button("清空选择")
                        batch_retry_selected = gr.Button("重试所选", variant="primary")
                with gr.Accordion("生图队列", open=True, visible=False, elem_id="llm_prompt_studio_generation_queue") as generation_queue_panel:
                    with gr.Row():
                        server_queue_refresh = gr.Button("刷新进度", elem_id="llm_prompt_studio_server_queue_refresh")
                        server_queue_cancel = gr.Button("取消等待中的生图", variant="stop", elem_id="llm_prompt_studio_server_queue_cancel")
                    server_queue_id = gr.Textbox(visible=False, elem_id="llm_prompt_studio_server_queue_id")
                    server_queue_generation_settings = gr.Textbox(value="{}", visible=False, elem_id="llm_prompt_studio_server_queue_generation_settings")
                    server_queue_status = gr.HTML("", elem_id="llm_prompt_studio_server_queue_status", elem_classes=["lps-status"])
                    server_queue_log = gr.HTML("", elem_id="llm_prompt_studio_server_queue_log", elem_classes=["lps-auto-loop-log"])
                with gr.Accordion("工作参数", open=False, elem_classes=["lps-secondary-panel"]):
                    with gr.Row():
                        save_workflow = gr.Button("保存当前参数")
                        reset_workflow = gr.Button("恢复默认")
                    workflow_status = gr.Markdown(elem_id="llm_prompt_studio_workflow_status")
                with gr.Column(elem_id="llm_prompt_studio_cache_processing", elem_classes=["lps-cache-workbench"]):
                    _create_inline_cache_panel("txt2img", _PROMPT_TARGETS.get("txt2img"), result_queue_controls)

            with gr.Tab("缓存", elem_id="llm_prompt_studio_library_tab"):
                with gr.Accordion("导入与插件批次", open=False, elem_id="llm_prompt_studio_png_batch_tab"):
                    with gr.Row(elem_classes=["lps-plugin-source-actions"]):
                        png_collector_pull = gr.Button(
                            "读取 PNG Collector 当前缓存",
                            elem_id="llm_prompt_studio_png_collector_pull",
                        )
                        ranbooru_batch_button = gr.Button(
                            "读取 Ranbooru 缓存",
                            elem_id="llm_prompt_studio_ranbooru_batch_load",
                        )
                    with gr.Accordion("Ranbooru 读取范围", open=False, elem_classes=["lps-source-settings"]):
                        ranbooru_database_path = gr.Textbox(
                            label="Ranbooru tag_cache.db 路径", value=ranbooru_link["database_path"],
                        )
                        with gr.Row():
                            ranbooru_detect = gr.Button("自动检测路径")
                            ranbooru_content_mode = gr.Radio(
                                label="读取内容", choices=RANBOORU_CONTENT_CHOICES,
                                value=ranbooru_link["content_mode"],
                            )
                            ranbooru_rating_filter = gr.Dropdown(
                                label="内容分级筛选", choices=RANBOORU_RATING_CHOICES,
                                value=ranbooru_link["rating_filter"],
                            )
                        with gr.Row():
                            ranbooru_min_source_score = gr.Number(
                                label="最低源评分", value=ranbooru_link["min_source_score"], precision=0,
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
                        with gr.Row(elem_classes=["lps-secondary-actions"]):
                            ranbooru_preview_button = gr.Button("预览 Ranbooru 缓存")
                            ranbooru_sync_button = gr.Button("同步到本插件缓存")
                        ranbooru_status = gr.Markdown(elem_classes=["lps-status"])
                        ranbooru_preview = gr.Dataframe(
                            headers=["序号", "Ranbooru ID", "内容类型", "源评分", "分级", "输出预设", "目标底模", "Prompt"],
                            datatype=["number", "str", "str", "number", "str", "str", "str", "str"],
                            interactive=False, wrap=True, label="Ranbooru 缓存预览",
                        )
                    with gr.Accordion("导入 prompt_batch.v1 JSON", open=False, elem_classes=["lps-source-settings"]):
                        png_batch_file = gr.File(
                            label="选择 Prompt JSON 文件", file_types=[".json"], type="filepath", height=100,
                            elem_id="llm_prompt_studio_png_batch_file",
                        )
                    png_batch_payload = gr.Textbox(
                        value=_png_batch_json({"schema_version": PNG_BATCH_SCHEMA, "producer": {"name": "LLM Prompt Studio"}, "records": []}),
                        visible=False, elem_id="llm_prompt_studio_png_batch_payload",
                    )
                    with gr.Row():
                        png_batch_action = gr.Radio(label="操作", choices=ACTION_UI_CHOICES, value="Expand", elem_id="llm_prompt_studio_png_batch_action")
                        png_batch_selection = gr.Number(label="当前序号", value=1, precision=0, elem_id="llm_prompt_studio_png_batch_selection")
                    png_batch_current = gr.Textbox(label="当前结果", lines=5, max_lines=30, interactive=False, elem_id="llm_prompt_studio_png_batch_current")
                    with gr.Row():
                        png_batch_previous = gr.Button("上一条", elem_id="llm_prompt_studio_png_batch_previous")
                        png_batch_next = gr.Button("下一条", elem_id="llm_prompt_studio_png_batch_next")
                    png_batch_target = gr.Radio(label="写入目标", choices=[("正面 Prompt（txt2img）", "txt2img")], value="txt2img", elem_id="llm_prompt_studio_png_batch_target")
                    with gr.Row(elem_classes=["lps-primary-actions"]):
                        png_batch_run = gr.Button("处理批次", variant="primary", elem_id="llm_prompt_studio_png_batch_run", elem_classes=["lps-primary"])
                        png_batch_cancel = gr.Button("停止处理", variant="stop", elem_id="llm_prompt_studio_png_batch_cancel", elem_classes=["lps-danger"])
                    with gr.Row(elem_classes=["lps-secondary-actions"]):
                        png_batch_write_scope = gr.Radio(
                            label="写回范围", choices=[("当前结果", "current"), ("已选结果", "selected")],
                            value="current", elem_id="llm_prompt_studio_png_batch_write_scope",
                        )
                        png_batch_append = gr.Radio(label="写入方式", choices=[("追加", "append"), ("覆盖", "replace")], value="append", elem_id="llm_prompt_studio_png_batch_append")
                        png_batch_append_button = gr.Button("写入结果", variant="secondary", elem_id="llm_prompt_studio_png_batch_append_button")
                        png_batch_export = gr.DownloadButton("导出结果", elem_id="llm_prompt_studio_png_batch_export")
                    png_batch_selected = gr.CheckboxGroup(
                        label="选择结果（写回范围为“已选结果”时生效）",
                        choices=[], value=[], elem_id="llm_prompt_studio_png_batch_selected",
                    )
                    png_batch_table = gr.Dataframe(headers=["序号", "文件", "原始正向 Prompt", "状态", "LLM 结果", "错误"], datatype=["number", "str", "str", "str", "str", "str"], interactive=False, wrap=True, height=360, elem_id="llm_prompt_studio_png_batch_table", elem_classes=["lps-table"])
                    gr.Markdown("", elem_id="llm_prompt_studio_png_batch_results")
                    png_batch_status = gr.HTML("请选择 PNG Collector、Ranbooru 或 JSON 文件作为批次来源。", elem_id="llm_prompt_studio_png_batch_status", elem_classes=["lps-status"])
                    png_batch_append_succeeded = gr.Checkbox(value=False, visible=False, elem_id="llm_prompt_studio_png_batch_append_succeeded")
                    png_batch_cancel_id = gr.State(lambda: uuid.uuid4().hex)
                    png_batch_file.change(
                        _png_batch_load,
                        inputs=png_batch_file,
                        outputs=[png_batch_payload, png_batch_table, png_batch_selection, png_batch_current, png_batch_status, png_batch_selected],
                    )

                with gr.Row():
                    cache_query = gr.Textbox(label="搜索 Prompt、负面词、源标签或外部来源", scale=4)
                    cache_min_score = gr.Number(value=0, visible=False)
                    cache_output_filter = gr.Dropdown(label="格式", choices=["全部"] + PRESET_UI_CHOICES, value="全部", scale=2)
                    cache_model_filter = gr.Dropdown(label="目标模型", choices=["全部"] + MODEL_UI_CHOICES, value="全部", scale=2)
                with gr.Row():
                    refresh = gr.Button("应用筛选", variant="primary", elem_classes=["lps-primary"])
                    clear_filters = gr.Button("清除筛选", elem_classes=["lps-secondary"])
                    undo_delete = gr.Button("撤销删除", elem_classes=["lps-secondary"])
                    cache_previous = gr.Button("上一页")
                    cache_next = gr.Button("下一页")
                    cache_page = gr.Number(label="页码", value=1, minimum=1, precision=0, scale=0, min_width=100)
                cache_status = gr.Markdown(f"缓存 {len(initial_records)} 条", elem_id="llm_prompt_studio_cache_status", elem_classes=["lps-status"])
                selected_records = gr.Dropdown(label="选择缓存记录（支持多选）", choices=_cache_choices(initial_records), value=[], multiselect=True, visible=False)
                delete_preview_state = gr.State([])
                table = gr.Dataframe(
                    value=_as_rows(initial_records), label="已缓存 Prompt",
                    headers=["序号", "ID", "格式", "模型", "正向提示词", "来源"],
                    datatype=["number", "number", "str", "str", "str", "str"],
                    interactive=False, wrap=False, height=520, elem_id="llm_prompt_studio_cache_table", elem_classes=["lps-table"],
                )
                with gr.Accordion("单条编辑 · 点击上方记录载入全文", open=True):
                    with gr.Row():
                        record_id = gr.Textbox(label="当前内部 ID", interactive=False)
                        record_output_mode = gr.Dropdown(label="格式", choices=PRESET_UI_CHOICES, value=workflow["preset"], allow_custom_value=True)
                        record_base_model = gr.Dropdown(label="目标模型", choices=MODEL_UI_CHOICES, value=workflow["base_model"], allow_custom_value=True)
                        record_score = gr.Number(value=0, visible=False)
                    record_tags = gr.Textbox(label="源标签", visible=False)
                    record_prompt = gr.Textbox(label="正向提示词", lines=6, max_lines=30, elem_classes=["lps-output"])
                    record_negative = gr.Textbox(label="负面提示词", lines=2, max_lines=4, visible=False)
                    with gr.Row():
                        save = gr.Button("保存当前记录", variant="primary")
                        save_as_new = gr.Button("另存为新记录")
                with gr.Accordion("删除当前记录", open=False):
                    with gr.Row():
                        load_selected = gr.Button("载入所选单条", visible=False)
                        preview_selected = gr.Button("预览删除当前记录")
                        delete_selected = gr.Button("确认删除当前记录", variant="stop")
                    selection_preview = gr.Textbox(label="删除预览", lines=1, max_lines=2, interactive=False)
                with gr.Accordion("批量修改 · 当前记录 / 全部筛选结果", open=False):
                    edit_snapshot = gr.State({})
                    with gr.Row():
                        edit_scope = gr.Radio(label="范围", choices=["当前记录", "全部筛选结果"], value="全部筛选结果")
                        edit_field = gr.Dropdown(label="字段", choices=list(CACHE_EDIT_FIELDS), value="正向提示词")
                        edit_operation = gr.Dropdown(label="操作", choices=list(CACHE_EDIT_OPERATIONS), value="查找替换")
                    with gr.Row():
                        edit_find = gr.Textbox(label="查找文字（精确匹配）")
                        edit_value = gr.Textbox(label="替换 / 写入内容（追加时自行包含空格或逗号）")
                    with gr.Row():
                        edit_preview_button = gr.Button("预览影响范围")
                        edit_apply = gr.Button("应用预览中的修改", variant="primary")
                    edit_preview_text = gr.Textbox(label="修改预览", lines=3, max_lines=5, interactive=False)
                with gr.Accordion("按全库序号批量管理", open=False):
                    position_spec = gr.Textbox(label="全库序号或范围", placeholder="1-100,205,300-320")
                    with gr.Row():
                        preview_positions = gr.Button("预览这些序号")
                        delete_positions = gr.Button("删除这些序号", variant="stop")
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
                        export_button = gr.Button("导出全部缓存")
                    export_file = gr.File(label="导出文件", interactive=False)

            with gr.Tab("处理结果库", elem_id="llm_prompt_studio_processed_results_tab"):
                with gr.Row(elem_classes=["lps-form-row"]):
                    processed_result_query = gr.Textbox(label="搜索处理结果", scale=4)
                    processed_result_refresh = gr.Button("刷新结果库", variant="primary")
                processed_result_status = gr.Markdown(
                    f"处理结果 {len(initial_processed_records)} 条",
                    elem_id="llm_prompt_studio_processed_result_status", elem_classes=["lps-status"],
                )
                processed_result_selection = gr.Dropdown(
                    label="选择处理结果",
                    choices=_processed_result_choices(initial_processed_records), value=[], multiselect=True,
                    elem_id="llm_prompt_studio_processed_result_selection",
                )
                processed_result_table = gr.Dataframe(
                    value=_processed_result_rows(initial_processed_records),
                    headers=["序号", "结果 ID", "格式", "模型", "处理后的 Prompt", "来源批次"],
                    datatype=["number", "number", "str", "str", "str", "str"],
                    interactive=False, wrap=True, height=520,
                    elem_id="llm_prompt_studio_processed_result_table", elem_classes=["lps-table"],
                )
                with gr.Row(elem_classes=["lps-form-row"]):
                    processed_result_scope = gr.Radio(
                        label="使用范围", choices=[("所选结果", "selected"), ("全部筛选结果", "filtered")],
                        value="selected", elem_id="llm_prompt_studio_processed_result_scope",
                    )
                    processed_result_append = gr.Button("追加到 txt2img", interactive=_PROMPT_TARGETS.get("txt2img") is not None,
                        elem_id="llm_prompt_studio_processed_result_append")
                    processed_result_enqueue = gr.Button("加入生图队列", variant="primary",
                        elem_id="llm_prompt_studio_processed_result_enqueue")
                processed_result_append_payload = gr.State("")

            with gr.Tab("设置", elem_id="llm_prompt_studio_connection_tab"):
                gr.Markdown("### LLM 模型设置", elem_classes=["lps-heading"])
                with gr.Row(elem_classes=["lps-model-settings-row"]):
                    provider = gr.Dropdown(label="服务提供方", choices=PROVIDER_UI_CHOICES, value=llm_settings["provider"], scale=1)
                    endpoint = gr.Textbox(label="模型服务地址", value=llm_settings["endpoint"], scale=2)
                with gr.Row(elem_classes=["lps-model-settings-row"]):
                    model = gr.Dropdown(
                        label="预训练模型 / 模型 ID", choices=[llm_settings["model"]] if llm_settings["model"] else [],
                        value=llm_settings["model"], allow_custom_value=True,
                        elem_id="llm_prompt_studio_model_id", scale=3,
                    )
                    discover_models = gr.Button("发现模型", elem_id="llm_prompt_studio_discover_models", scale=1)
                api_key = gr.Textbox(label="API Key（留空则使用已保存凭据）", type="password")
                weights_path = gr.State(llm_settings["weights_path"])
                model_version = gr.State(llm_settings["model_version"])
                with gr.Group(elem_classes=["lps-model-advanced"]):
                    with gr.Row(elem_classes=["lps-model-settings-row"]):
                        temperature = gr.Number(label="温度", value=llm_settings["temperature"], minimum=0, maximum=2, step=0.05)
                        top_p = gr.Number(label="Top P（留空用模型默认）", value=llm_settings["top_p"], minimum=0, maximum=1, step=0.05)
                        top_k = gr.Number(label="Top K（留空用模型默认）", value=llm_settings["top_k"], minimum=0, maximum=100000, precision=0, visible=_supports_top_k(llm_settings["provider"]))
                        max_tokens = gr.Number(label="最大输出 Token", value=llm_settings["max_tokens"], minimum=0, maximum=262144, precision=0)
                    send_temperature = gr.Checkbox(label="发送温度参数", value=llm_settings["send_temperature"])
                with gr.Accordion("请求与推理", open=False):
                    with gr.Row(elem_classes=["lps-model-settings-row"]):
                        timeout = gr.Number(label="超时（秒）", value=llm_settings["timeout"], minimum=5, maximum=600, precision=0)
                        retry_count = gr.Number(label="重试次数", value=llm_settings["retry_count"], minimum=0, maximum=5, precision=0)
                        thinking_enabled = gr.Checkbox(label="启用思考", value=llm_settings["thinking_enabled"])
                        thinking_budget = gr.Number(label="思考预算（0=默认）", value=llm_settings["thinking_budget"], minimum=0, maximum=262144, precision=0)
                        reasoning_effort = gr.Dropdown(label="推理强度", choices=[("默认", ""), ("低", "low"), ("中", "medium"), ("高", "high")], value=llm_settings["reasoning_effort"])
                    gr.Markdown("#### 独立备用连接", elem_classes=["lps-heading"])
                    with gr.Row(elem_classes=["lps-model-settings-row"]):
                        fallback_provider = gr.Dropdown(
                            label="备用服务提供方", choices=[("关闭备用模型", ""), *PROVIDER_UI_CHOICES],
                            value=llm_settings["fallback_provider"], scale=1,
                            elem_id="llm_prompt_studio_fallback_provider",
                        )
                        fallback_endpoint = gr.Textbox(
                            label="备用服务地址", value=llm_settings["fallback_endpoint"], scale=2,
                            placeholder="可与主服务不同", elem_id="llm_prompt_studio_fallback_endpoint",
                        )
                    with gr.Row(elem_classes=["lps-model-settings-row"]):
                        fallback_model = gr.Textbox(
                            label="备用模型 ID", value=llm_settings["fallback_model"],
                            elem_id="llm_prompt_studio_fallback_model", scale=3,
                        )
                        fallback_discover_models = gr.Button("发现备用模型", scale=1, elem_id="llm_prompt_studio_fallback_discover")
                    fallback_api_key = gr.Textbox(
                        label="备用 API Key（留空则使用该备用服务已保存凭据）", type="password",
                        elem_id="llm_prompt_studio_fallback_api_key",
                    )
                    with gr.Row():
                        fallback_test = gr.Button("测试备用连接", elem_id="llm_prompt_studio_fallback_test")
                        fallback_clear_credentials = gr.Button("清除备用 API Key", elem_id="llm_prompt_studio_fallback_clear")
                with gr.Row(elem_classes=["lps-primary-actions"]):
                    test = gr.Button("测试连接", elem_id="llm_prompt_studio_test_connection")
                    save_connection = gr.Button("保存并应用", variant="primary")
                    clear_credentials = gr.Button("清除已保存的 API Key")
                test_status = gr.Markdown(
                    _credential_status(llm_settings["provider"], llm_settings["endpoint"])
                    + " " + _fallback_status(llm_settings),
                    elem_id="llm_prompt_studio_connection_status",
                    elem_classes=["lps-status"],
                )
            with gr.Tab("更多", elem_id="llm_prompt_studio_tools_tab"):
                with gr.Tabs(elem_id="llm_prompt_studio_tools_tabs"):
                    with gr.Tab("静态词库", elem_id="llm_prompt_studio_wildcards_tab"):
                        wildcard_path = gr.Textbox(label="静态词库目录", value=workflow["wildcard_path"], elem_id="llm_prompt_studio_wildcard_path")
                        wildcard_index = gr.Button("索引静态词库", elem_id="llm_prompt_studio_wildcard_index")
                        wildcard_status = gr.Markdown(
                            "按文件夹／文件名保留分类，支持 TXT、CSV、JSON、YAML。修改文件后点击索引更新。",
                            elem_id="llm_prompt_studio_wildcard_status",
                            elem_classes=["lps-status"],
                        )
                        with gr.Row():
                            wildcard_category = gr.Dropdown(label="词库分类", choices=["全部"] + DB.wildcard_categories(), value="全部", elem_id="llm_prompt_studio_wildcard_category")
                            wildcard_query = gr.Textbox(label="搜索已索引词条", elem_id="llm_prompt_studio_wildcard_query")
                        wildcard_results = gr.Dropdown(label="匹配结果", choices=[], multiselect=True, elem_id="llm_prompt_studio_wildcard_results")
                        with gr.Row():
                            wildcard_apply = gr.Button("追加到固定 Prompt", variant="primary", elem_id="llm_prompt_studio_wildcard_apply")
                            wildcard_apply_status = gr.Markdown("", elem_id="llm_prompt_studio_wildcard_apply_status", elem_classes=["lps-status"])
                    with gr.Tab("WD14 / CL 反推", elem_id="llm_prompt_studio_wd14_tab"):
                        wd_directory_value = DB.get_setting("wd14_local_directory", "")
                        wd_models, wd_default = discover_local_models(wd_directory_value)
                        wd_paths = {path for _label, path in wd_models}
                        wd_endpoint = gr.State(workflow["wd_endpoint"])
                        with gr.Row():
                            wd_model = gr.Dropdown(label="本地模型", choices=wd_models,
                                value=workflow["wd_model"] if workflow["wd_model"] in wd_paths else wd_default,
                                elem_id="llm_prompt_studio_wd14_model", scale=4)
                            wd_refresh = gr.Button("重新检测模型", scale=1, elem_id="llm_prompt_studio_wd14_model_refresh")
                            wd_unload = gr.Button("卸载模型", scale=1)
                        wd_model_status = gr.Markdown(_wd14_model_status(wd_models), elem_id="llm_prompt_studio_wd14_model_status")
                        with gr.Accordion("下载模型", open=not bool(wd_models), elem_id="llm_prompt_studio_wd14_download_panel") as wd_download_panel:
                            with gr.Row():
                                wd_download_choice = gr.Dropdown(label="模型", choices=[(spec["label"], key) for key, spec in MODEL_CATALOG.items()],
                                    value=None, elem_id="llm_prompt_studio_wd14_download_choice")
                                wd_download = gr.Button("下载所选模型", variant="primary", interactive=False, elem_id="llm_prompt_studio_wd14_download")
                            wd_download_links = gr.Markdown(elem_id="llm_prompt_studio_wd14_download_links")
                            wd_download_directory = gr.Textbox(label="配套文件保存目录", value="", interactive=False)
                        with gr.Accordion("其他模型位置", open=False):
                            wd_directory = gr.Textbox(label="模型目录或 ONNX 文件", value=wd_directory_value,
                                placeholder="模型目录或 Hugging Face 缓存目录（WD14 / CL Tagger）",
                                elem_id="llm_prompt_studio_wd14_model_directory")
                        with gr.Row():
                            wd_threshold = gr.Slider(label="标签阈值", minimum=0, maximum=1, value=workflow["wd_threshold"], step=0.01)
                            wd_character_threshold = gr.Slider(label="角色阈值", minimum=0, maximum=1, value=0.85, step=0.01)
                        wd_native_batch, wd_native_script = _wd14_native_batch_components()
                        wd_native_prompt = _PROMPT_TARGETS.get("txt2img")
                        with gr.Tabs():
                            with gr.Tab("单图"):
                                with gr.Row():
                                    image = gr.Image(label="图片", type="pil", elem_id="llm_prompt_studio_wd14_image")
                                    wd_tags = gr.Textbox(label="反推结果（可编辑）", lines=10, max_lines=30, elem_id="llm_prompt_studio_wd14_tags")
                                interrogate = gr.Button("开始反推", variant="primary", elem_id="llm_prompt_studio_wd14_interrogate")
                                wd_status = gr.Markdown(elem_id="llm_prompt_studio_wd14_status", elem_classes=["lps-status"])
                                with gr.Row():
                                    wd_write_mode = gr.Radio(label="写入方式", choices=[("追加", "append"), ("替换", "replace")], value="append")
                                    wd_send = gr.Button("送入 txt2img", interactive=wd_native_prompt is not None, elem_id="llm_prompt_studio_wd14_send")
                                    wd_save = gr.Button("保存到缓存", elem_id="llm_prompt_studio_wd14_save")
                                with gr.Accordion("LLM 处理（可选）", open=False):
                                    action = gr.Radio(label="操作", choices=ACTION_UI_CHOICES, value="Expand")
                                    with gr.Row():
                                        wd_full_process = gr.Button("反推并用 LLM 处理", elem_id="llm_prompt_studio_wd14_process")
                                        transform = gr.Button("用 LLM 处理当前结果")
                                    wd_llm_status = gr.Markdown()
                                wd_raw_tags = gr.State("")
                                wd_output_mode = gr.State("Danbooru Tags")
                            with gr.Tab("文件夹批量"):
                                wd_folder = gr.Textbox(label="图片文件夹", placeholder=r"E:\图片",
                                    elem_id="llm_prompt_studio_wd14_folder")
                                wd_recursive = gr.Checkbox(label="包含子文件夹", value=True, elem_id="llm_prompt_studio_wd14_recursive")
                                with gr.Row(elem_classes=["lps-primary-actions"]):
                                    wd_batch_start = gr.Button("开始批量反推", variant="primary", elem_id="llm_prompt_studio_wd14_batch_start")
                                    wd_batch_resume = gr.Button("继续未完成", elem_id="llm_prompt_studio_wd14_batch_resume")
                                    wd_batch_retry = gr.Button("重试失败", elem_id="llm_prompt_studio_wd14_batch_retry")
                                    wd_batch_cancel = gr.Button("停止", variant="stop", elem_id="llm_prompt_studio_wd14_batch_cancel")
                                wd_batch_status = gr.Markdown("就绪", elem_id="llm_prompt_studio_wd14_batch_status", elem_classes=["lps-status"])
                                wd_batch_table = gr.HTML(elem_id="llm_prompt_studio_wd14_batch_table")
                                with gr.Row():
                                    wd_batch_send = gr.Button("全部送入 txt2img 批量列表", interactive=wd_native_batch is not None,
                                        elem_id="llm_prompt_studio_wd14_batch_send")
                                    wd_batch_save = gr.Button("全部结果保存到缓存", elem_id="llm_prompt_studio_wd14_batch_save")
                                wd_batch_output_status = gr.Markdown(elem_id="llm_prompt_studio_wd14_batch_output_status")
                                wd_batch_records = gr.State([])
                                wd_batch_id = gr.State(lambda: uuid.uuid4().hex, delete_callback=_wd14_release_batch)

        with result_queue_panel.render():
            with gr.Row(elem_classes=["lps-form-row"]):
                processed_result_refresh_queue = gr.Button("刷新生图进度", size="sm")
                processed_result_cancel_queue = gr.Button("取消等待中的生图", variant="stop", size="sm")
            processed_result_queue_status.render()
            processed_result_gallery.render()

        workflow_inputs = [
            preset, system_override, base_model, safety, nsfw_injection, user_instruction,
            structured_mode, region_count, remove_bad, remove_terms, shuffle, spaces, max_tags,
            save_score, cache_result,
            batch_skip_existing, batch_skip_failed,
            wd_endpoint, wd_model, wd_threshold, wildcard_path,
        ]
        template_button.click(_template_request, inputs=template_choice, outputs=request)
        _bind_template_editors([
            dict(choice=template_choice,
                 fields=[template_name, template_content, template_preserve_fixed, template_obey_user,
                         template_variation_dimensions, template_style_rules, template_forbidden_content,
                         template_conflict_policy, template_duplicate_policy],
                 save=template_save, default=template_default, delete=template_delete, status=template_status),
            *_INLINE_TEMPLATE_EDITORS.values(),
        ], interface=ui)
        save_workflow.click(_save_workflow_settings, inputs=workflow_inputs, outputs=workflow_status)
        reset_workflow.click(_reset_workflow_settings, outputs=[*workflow_inputs, workflow_status]).then(
            _system_prompt_editor_value, inputs=[preset, system_override, base_model], outputs=system_editor, queue=False,
        )
        structured_mode.change(lambda mode: gr.update(visible=_canonical_output_mode(mode) != "Plain Prompt"), inputs=structured_mode, outputs=region_count, queue=False)
        for event in (preset.input, restore_preset.click):
            event(lambda p, m: (_system_prompt_editor_value(p, base_model=m), ""),
                  inputs=[preset, base_model], outputs=[system_editor, system_override], queue=False)
        system_editor.input(_system_prompt_override_value, inputs=[preset, system_editor, base_model], outputs=system_override, queue=False)
        base_model.input(_system_prompt_editor_value, inputs=[preset, system_override, base_model], outputs=system_editor, queue=False)
        ui.load(_load_system_prompt_editor, outputs=[preset, base_model, system_override, system_editor])
        provider.change(
            _load_provider_settings, inputs=provider,
            outputs=[endpoint, model, fallback_provider, fallback_endpoint, fallback_model, weights_path, model_version, temperature, timeout, max_tokens, send_temperature, retry_count, thinking_enabled, thinking_budget, reasoning_effort, test_status, top_p, top_k],
        )
        discover_models.click(
            _discover_models, inputs=[provider, endpoint, api_key, timeout], outputs=[model, test_status],
        )
        test.click(
            _test_connection,
            inputs=[provider, endpoint, model, api_key, fallback_model, temperature, timeout, max_tokens, send_temperature, retry_count, thinking_enabled, thinking_budget, reasoning_effort, top_p, top_k, fallback_provider, fallback_endpoint, fallback_api_key],
            outputs=test_status,
        )
        fallback_provider.input(
            _load_fallback_provider_settings, inputs=fallback_provider,
            outputs=[fallback_endpoint, fallback_model, test_status],
        )
        fallback_discover_models.click(
            _discover_fallback_model,
            inputs=[fallback_provider, fallback_endpoint, fallback_api_key, timeout],
            outputs=[fallback_model, test_status],
        )
        fallback_test.click(
            _test_fallback_connection,
            inputs=[fallback_provider, fallback_endpoint, fallback_model, fallback_api_key, temperature, timeout, max_tokens, send_temperature, retry_count, thinking_enabled, thinking_budget, reasoning_effort, top_p, top_k],
            outputs=test_status,
        )
        save_connection.click(
            _save_llm_settings,
            inputs=[provider, endpoint, model, api_key, fallback_model, weights_path, model_version, temperature, timeout, max_tokens, send_temperature, retry_count, thinking_enabled, thinking_budget, reasoning_effort, top_p, top_k, fallback_provider, fallback_endpoint, fallback_api_key],
            outputs=[test_status, endpoint, model, fallback_provider, fallback_endpoint, fallback_model, weights_path, model_version],
        )
        clear_credentials.click(_clear_llm_credentials, inputs=[provider, endpoint], outputs=test_status)
        fallback_clear_credentials.click(
            _clear_llm_credentials, inputs=[fallback_provider, fallback_endpoint], outputs=test_status,
        )
        ui.load(
            _load_active_connection_settings,
            outputs=[provider, endpoint, model, fallback_provider, fallback_endpoint, fallback_model, weights_path, model_version, temperature, timeout, max_tokens, send_temperature, retry_count, thinking_enabled, thinking_budget, reasoning_effort, test_status, top_p, top_k],
        )
        png_collector_pull.click(
            fn=None,
            outputs=png_batch_status,
            js="() => window.llmPromptStudioPngBatch.loadCollectorCache()",
            queue=False,
        )
        png_batch_payload.input(_png_batch_refresh, inputs=[png_batch_payload, png_batch_selection], outputs=[png_batch_table, png_batch_selection, png_batch_current, png_batch_status, png_batch_selected])
        png_batch_selected.change(
            _png_batch_set_selection,
            inputs=[png_batch_payload, png_batch_selected],
            outputs=[png_batch_payload, png_batch_table, png_batch_selection, png_batch_current, png_batch_status, png_batch_selected],
        )
        png_batch_previous.click(_png_batch_move, inputs=[png_batch_payload, png_batch_selection, gr.State(-1)], outputs=[png_batch_selection, png_batch_current])
        png_batch_next.click(_png_batch_move, inputs=[png_batch_payload, png_batch_selection, gr.State(1)], outputs=[png_batch_selection, png_batch_current])
        png_batch_selection.change(_png_batch_current, inputs=[png_batch_payload, png_batch_selection], outputs=[png_batch_selection, png_batch_current])
        png_batch_run.click(
            _png_batch_run,
            inputs=[png_batch_payload, png_batch_action, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, gr.State("faithful"), png_batch_cancel_id],
            outputs=[png_batch_payload, png_batch_table, png_batch_selection, png_batch_current, png_batch_status, png_batch_selected],
        )
        png_batch_cancel.click(_cancel_png_batch, inputs=png_batch_cancel_id, outputs=png_batch_status, queue=False)
        png_batch_export.click(_png_batch_export_file, inputs=png_batch_payload, outputs=png_batch_export)
        png_append_event = png_batch_append_button.click(
            fn=None,
            inputs=[png_batch_payload, png_batch_current, png_batch_selected, png_batch_write_scope, png_batch_target, png_batch_append],
            outputs=[png_batch_status, png_batch_append_succeeded],
            js="(payload, current, selected, scope, target, mode) => window.llmPromptStudioPngBatch.appendScopedToPrompt(payload, current, selected, scope, target, mode)",
        )
        png_append_event.then(
            _png_batch_mark_appended,
            inputs=[png_batch_payload, png_batch_selection, png_batch_selected, png_batch_write_scope, png_batch_append_succeeded],
            outputs=[png_batch_payload, png_batch_table, png_batch_selection, png_batch_current, png_batch_status, png_batch_selected],
            queue=False,
        )
        wildcard_index.click(
            _index_wildcards,
            inputs=wildcard_path,
            outputs=[wildcard_status, wildcard_results, wildcard_category],
        )
        wildcard_query.submit(_search_wildcards, inputs=[wildcard_query, wildcard_category], outputs=wildcard_results)
        wildcard_query.blur(_search_wildcards, inputs=[wildcard_query, wildcard_category], outputs=wildcard_results)
        wildcard_category.input(_search_wildcards, inputs=[wildcard_query, wildcard_category], outputs=wildcard_results)
        wildcard_apply.click(
            _apply_wildcard_selection,
            inputs=[source_tags, wildcard_results],
            outputs=[source_tags, wildcard_apply_status],
            queue=False,
        )
        cache_filter_inputs = [cache_query, cache_min_score, cache_output_filter, cache_model_filter]
        wd_model_outputs = [wd_model, wd_model_status, wd_download_panel]
        wd_refresh.click(_wd14_model_choices, inputs=[wd_directory, wd_model], outputs=wd_model_outputs)
        wd_directory.submit(_wd14_model_choices, inputs=[wd_directory, wd_model], outputs=wd_model_outputs)
        ui.load(_wd14_model_choices, inputs=[wd_directory, wd_model], outputs=wd_model_outputs)
        wd_download_choice.change(_wd14_download_details, inputs=wd_download_choice,
            outputs=[wd_download_links, wd_download_directory, wd_download], queue=False)
        wd_download.click(_wd14_download_model, inputs=[wd_download_choice, wd_directory, wd_model], outputs=[*wd_model_outputs, wd_download], api_name=False)
        wd_unload.click(unload_local_model, outputs=wd_status)
        interrogate.click(_wd14_interrogate, inputs=[image, wd_model, wd_threshold, wd_character_threshold], outputs=[wd_tags, wd_status]).then(
            lambda: "Danbooru Tags", outputs=wd_output_mode, queue=False,
        )
        wd_full_process.click(
            _wd14_process,
            inputs=[image, wd_model, wd_threshold, wd_character_threshold, action, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature],
            outputs=[wd_raw_tags, wd_tags, wd_llm_status],
        ).then(lambda value: value, inputs=preset, outputs=wd_output_mode, queue=False)
        transform.click(_expand_or_polish, inputs=[wd_tags, action, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=[wd_tags, wd_llm_status]).then(
            lambda value: value, inputs=preset, outputs=wd_output_mode, queue=False,
        )
        wd_save.click(_wd14_save_single, inputs=[wd_tags, base_model, wd_output_mode, *cache_filter_inputs], outputs=[wd_status, table, selected_records])
        wd_batch_inputs = [wd_folder, wd_recursive, wd_model, wd_threshold, wd_character_threshold, wd_batch_id]
        for button, mode in ((wd_batch_start, "start"), (wd_batch_resume, "resume"), (wd_batch_retry, "retry")):
            button.click(_wd14_run_batch, inputs=[*wd_batch_inputs, gr.State(mode)],
                         outputs=[wd_batch_status, wd_batch_table, wd_batch_records])
        wd_batch_cancel.click(_wd14_cancel_batch, inputs=wd_batch_id, outputs=wd_batch_status, queue=False)
        wd_batch_save.click(_wd14_save_records, inputs=[wd_batch_records, base_model, *cache_filter_inputs],
                            outputs=[wd_batch_output_status, table, selected_records])
        if wd_native_prompt is not None:
            wd_single_script = wd_native_script if wd_native_script is not None else gr.State(None)
            wd_send.click(_wd14_write_single, inputs=[wd_tags, wd_native_prompt, wd_write_mode, wd_single_script], outputs=[wd_native_prompt, wd_status, wd_single_script]).then(
                fn=None, inputs=[], outputs=[], js="() => { window.switch_to_txt2img?.(); return []; }", queue=False,
            )
        if wd_native_batch is not None:
            wd_batch_send.click(_wd14_write_batch, inputs=wd_batch_records, outputs=[wd_native_batch, wd_native_script, wd_batch_output_status]).then(
                fn=None, inputs=[], outputs=[], js="() => { window.switch_to_txt2img?.(); return []; }", queue=False,
            )
        ranbooru_inputs = [
            ranbooru_database_path, ranbooru_content_mode, ranbooru_rating_filter,
            ranbooru_min_source_score, ranbooru_source_limit,
            ranbooru_tag_output_mode, ranbooru_tag_base_model,
            ranbooru_natural_output_mode, ranbooru_natural_base_model,
        ]
        ranbooru_detect.click(_detect_ranbooru_cache, outputs=[ranbooru_database_path, ranbooru_status])
        ranbooru_preview_button.click(
            _preview_ranbooru_link, inputs=ranbooru_inputs, outputs=[ranbooru_preview, ranbooru_status],
        )
        ranbooru_sync_button.click(
            _sync_ranbooru_link,
            inputs=[*ranbooru_inputs, *cache_filter_inputs],
            outputs=[ranbooru_status, table, selected_records],
        )
        ranbooru_batch_button.click(
            _load_ranbooru_to_png_batch,
            inputs=ranbooru_inputs,
            outputs=[png_batch_payload, ranbooru_status],
        ).then(
            lambda payload: _png_batch_refresh(payload, 1), inputs=png_batch_payload,
            outputs=[png_batch_table, png_batch_selection, png_batch_current, png_batch_status, png_batch_selected],
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
        page_outputs = [table, selected_records, cache_status, cache_page]
        refresh.click(_cache_page_update, inputs=cache_filter_inputs, outputs=page_outputs)
        cache_query.submit(_cache_page_update, inputs=cache_filter_inputs, outputs=page_outputs)
        clear_filters.click(_clear_cache_filters, outputs=[cache_query, cache_min_score, cache_output_filter, cache_model_filter, table, selected_records, cache_status]).then(lambda: 1, outputs=cache_page, queue=False)
        cache_page.submit(_cache_page_update, inputs=[*cache_filter_inputs, cache_page], outputs=page_outputs)
        cache_previous.click(lambda q, score, fmt, model, page: _cache_page_update(q, score, fmt, model, (page or 1) - 1), inputs=[*cache_filter_inputs, cache_page], outputs=page_outputs)
        cache_next.click(lambda q, score, fmt, model, page: _cache_page_update(q, score, fmt, model, (page or 1) + 1), inputs=[*cache_filter_inputs, cache_page], outputs=page_outputs)
        cache_output_filter.input(_cache_page_update, inputs=cache_filter_inputs, outputs=page_outputs)
        cache_model_filter.input(_cache_page_update, inputs=cache_filter_inputs, outputs=page_outputs)
        edit_inputs = [edit_scope, record_id, edit_field, edit_operation, edit_find, edit_value, *cache_filter_inputs]
        edit_preview_button.click(_preview_cache_edit, inputs=edit_inputs, outputs=[edit_preview_text, edit_snapshot])
        cache_edit_event = edit_apply.click(_apply_cache_edit, inputs=[edit_snapshot, *edit_inputs], outputs=[cache_status, table, selected_records, edit_snapshot]).then(lambda: 1, outputs=cache_page, queue=False)
        record_outputs = [record_id, record_prompt, record_negative, record_output_mode, record_base_model, record_score, record_tags, cache_status]
        cache_edit_event.then(lambda current_id: _load_record(current_id)[:-1], inputs=record_id, outputs=record_outputs[:-1])
        table.select(
            _select_cache_row_from_view,
            inputs=[cache_page, *cache_filter_inputs],
            outputs=[selected_records, *record_outputs],
        )
        selected_records.input(_load_selected_record, inputs=selected_records, outputs=record_outputs)
        load_selected.click(_load_selected_record, inputs=selected_records, outputs=record_outputs)
        preview_selected.click(_preview_selected, inputs=selected_records, outputs=[selection_preview, delete_preview_state])
        delete_selected.click(_delete_previewed_records, inputs=[selected_records, delete_preview_state, *cache_filter_inputs], outputs=[cache_status, table, selected_records]).then(lambda: 1, outputs=cache_page, queue=False)
        save.click(_save_record, inputs=[record_id, record_prompt, record_negative, record_output_mode, record_base_model, record_score, record_tags, *cache_filter_inputs], outputs=[cache_status, table, selected_records]).then(lambda: 1, outputs=cache_page, queue=False)
        save_as_new.click(_save_record_as_new, inputs=[record_prompt, record_negative, record_output_mode, record_base_model, record_score, record_tags, *cache_filter_inputs], outputs=[cache_status, table, selected_records]).then(lambda: 1, outputs=cache_page, queue=False)
        batch_preview_button.click(
            _preview_batch_sources,
            inputs=[batch_sources, batch_skip_existing, preset, base_model, batch_generation_count, batch_topic_pool, batch_base_prompt, batch_lock_known, batch_sample_static, request],
            outputs=[batch_queue, batch_preview_status],
        )
        generation_outputs = [batch_status, table, selected_records, batch_issues, batch_issue_selection, batch_issue_state, batch_queue,
                              output, server_queue_id, server_queue_status, server_queue_log]
        generate.click(
            lambda settings: settings, inputs=server_queue_generation_settings, outputs=server_queue_generation_settings,
            js="(settings) => [window.llmPromptStudioAutoLoop?.readTxt2imgSettings?.() || settings]", queue=False,
        ).then(
            _studio_generate,
            inputs=[request, generation_destination, server_queue_generation_settings, batch_task_id, batch_sources, batch_skip_existing, batch_skip_failed, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, *cache_filter_inputs, batch_issue_state, batch_task_id, batch_generation_count, batch_topic_pool, batch_base_prompt, batch_lock_known, batch_sample_static],
            outputs=generation_outputs,
        )
        generation_destination.change(
            lambda destination: (gr.update(value="生成并入队生图" if destination == "queue" else "生成到缓存"), gr.update(visible=destination == "queue")),
            inputs=generation_destination, outputs=[generate, generation_queue_panel], queue=False,
        )
        batch_cancel.click(
            _stop_studio_generation, inputs=[batch_task_id, server_queue_id],
            outputs=[batch_status, server_queue_status, server_queue_log], queue=False,
        )
        server_queue_id.change(
            fn=None, inputs=server_queue_id, outputs=[],
            js="(batchId) => { if (batchId) window.llmPromptStudioAutoLoop?.watchServerQueue(batchId); return []; }",
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
            lambda settings: settings, inputs=server_queue_generation_settings, outputs=server_queue_generation_settings,
            js="(settings) => [window.llmPromptStudioAutoLoop?.readTxt2imgSettings?.() || settings]", queue=False,
        ).then(
            _studio_retry,
            inputs=[generation_destination, server_queue_generation_settings, batch_task_id, batch_issue_selection, batch_issue_state, batch_skip_failed, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, *cache_filter_inputs, batch_task_id],
            outputs=generation_outputs,
        )
        preview_positions.click(_preview_positions, inputs=position_spec, outputs=[cache_status, table, selected_records])
        delete_positions.click(_delete_positions, inputs=[position_spec, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        undo_delete.click(_undo_last_delete, inputs=cache_filter_inputs, outputs=[cache_status, table, selected_records])
        import_button.click(_import_cache, inputs=[import_file, import_dedupe, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        export_button.click(_export_cache, inputs=export_format, outputs=[cache_status, export_file])
        processed_result_refresh.click(
            _processed_result_view,
            inputs=processed_result_query,
            outputs=[processed_result_table, processed_result_selection, processed_result_status],
        )
        processed_result_query.submit(_processed_result_view, inputs=processed_result_query,
            outputs=[processed_result_table, processed_result_selection, processed_result_status])
        processed_result_append_event = processed_result_append.click(
            _processed_result_append,
            inputs=[processed_result_selection, processed_result_query, processed_result_scope],
            outputs=[processed_result_append_payload, processed_result_status],
        )
        processed_result_append_event.then(
            fn=None,
            inputs=processed_result_append_payload,
            outputs=processed_result_status,
            js="""(payload) => {
                const text = String(payload || '').trim();
                if (!text) return '没有可追加的处理结果。';
                const root = typeof gradioApp === 'function' ? gradioApp() : document;
                const host = root.querySelector('#txt2img_prompt');
                const field = host?.querySelector('textarea, input');
                if (!field) return '未找到 txt2img 正面 Prompt 输入框。';
                const current = String(field.value || '').trim();
                const next = current ? `${current}\\n${text}` : text;
                const setter = Object.getOwnPropertyDescriptor(
                    field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
                    'value',
                )?.set;
                if (setter) setter.call(field, next); else field.value = next;
                field.dispatchEvent(new Event('input', {bubbles: true}));
                field.dispatchEvent(new Event('change', {bubbles: true}));
                return `已追加 ${text.split(/\\r?\\n/).filter(Boolean).length} 条处理结果。`;
            }""",
            queue=False,
        )
        processed_result_enqueue_event = processed_result_enqueue.click(
            _processed_result_enqueue,
            inputs=[processed_result_selection, processed_result_query, processed_result_queue_ids, processed_result_generation_settings, processed_result_scope],
            outputs=[processed_result_queue_ids, processed_result_status],
            js="(selected, query, queueIds, settings, scope) => [selected, query, queueIds, window.llmPromptStudioAutoLoop.readTxt2imgSettings() || settings, scope]",
        )
        processed_result_enqueue_event.then(
            lambda ids: (*_processed_result_queue_view(ids), gr.update(open=bool(ids))),
            inputs=processed_result_queue_ids,
            outputs=[processed_result_queue_status, processed_result_gallery, result_queue_panel],
            queue=False,
        )
        processed_result_refresh_queue.click(
            _processed_result_queue_view,
            inputs=processed_result_queue_ids,
            outputs=[processed_result_queue_status, processed_result_gallery],
            queue=False,
        )
        processed_result_cancel_queue.click(
            _inline_cache_cancel_queues,
            inputs=processed_result_queue_ids,
            outputs=processed_result_queue_status,
            queue=False,
        )
    return [(ui, "LLM 提示词工作室", "llm_prompt_studio")]

