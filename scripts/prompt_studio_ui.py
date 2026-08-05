from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

import gradio as gr
from starlette.requests import Request

from prompt_studio_core import (
    BASE_MODEL_GUIDANCE, DEFAULT_WILDCARDS, PRESETS, PROVIDER_PROFILES, CredentialStore, StudioDB,
    build_system_prompt, build_user_message, call_llm, get_provider_profile, is_sfw_output,
    discover_ranbooru_cache, evaluate_prompt_quality, load_ranbooru_cache, process_tags,
    regional_format, validate_endpoint,
)


DB = StudioDB()
CREDENTIALS = CredentialStore()
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
ACTION_UI_CHOICES = [("扩写", "Expand"), ("润色", "Polish")]
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
    "auto_score": True,
    "batch_skip_existing": True,
    "batch_skip_failed": True,
    "batch_retries": 2,
    "batch_score": 7,
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
_PNG_BATCH_CANCEL = threading.Event()
_PNG_BATCH_LOCK = threading.Lock()
_PNG_BATCH_CONTROL_LOCK = threading.Lock()
_PNG_BATCH_ACTIVE_TASK_ID = ""


def _as_rows(records: list[dict[str, Any]]) -> list[list[Any]]:
    labels = {"llm": "LLM", "manual": "手动", "unrated": "未评分"}
    source_labels = {"ranbooru": "Ranbooru"}
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
    values = {**WORKFLOW_DEFAULTS, **(stored if isinstance(stored, dict) else {})}
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
        "batch_retries": (0, 3),
    }
    float_limits = {
        "rag_min_score": (0, 10), "save_score": (0, 10), "batch_score": (0, 10),
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
    for key in ["remove_bad", "shuffle", "spaces", "cache_result", "auto_score", "batch_skip_existing", "batch_skip_failed"]:
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
    DB.set_setting("workflow_settings_v1", {"version": 1, **values})
    return "工作参数已保存。下次打开完整页和内嵌面板时会自动填入。"


def _save_workflow_settings(
    preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    structured_mode, region_count, remove_bad, remove_terms, shuffle, spaces, max_tags,
    few_shot_count, rag_min_score, save_score, cache_result, auto_score,
    batch_skip_existing, batch_skip_failed, batch_retries, batch_score,
    wd_endpoint, wd_model, wd_threshold, wildcard_path,
):
    return _save_workflow_values({
        "preset": preset, "system_override": system_override, "base_model": base_model,
        "safety": safety, "nsfw_injection": nsfw_injection, "user_instruction": user_instruction,
        "structured_mode": structured_mode, "region_count": int(region_count or 1),
        "remove_bad": bool(remove_bad), "remove_terms": remove_terms, "shuffle": bool(shuffle),
        "spaces": bool(spaces), "max_tags": int(max_tags or 0),
        "few_shot_count": int(few_shot_count or 0), "rag_min_score": float(rag_min_score or 0),
        "save_score": float(save_score or 0), "cache_result": bool(cache_result), "auto_score": bool(auto_score),
        "batch_skip_existing": bool(batch_skip_existing), "batch_skip_failed": bool(batch_skip_failed),
        "batch_retries": int(batch_retries or 0),
        "batch_score": float(batch_score or 0), "wd_endpoint": wd_endpoint, "wd_model": wd_model,
        "wd_threshold": float(wd_threshold or 0), "wildcard_path": wildcard_path,
    })


def _workflow_component_values(values: dict[str, Any]) -> list[Any]:
    return [values[key] for key in [
        "preset", "system_override", "base_model", "safety", "nsfw_injection", "user_instruction",
        "structured_mode", "region_count", "remove_bad", "remove_terms", "shuffle", "spaces", "max_tags",
        "few_shot_count", "rag_min_score", "save_score", "cache_result", "auto_score",
        "batch_skip_existing", "batch_skip_failed", "batch_retries", "batch_score",
        "wd_endpoint", "wd_model", "wd_threshold", "wildcard_path",
    ]]


def _reset_workflow_settings():
    DB.delete_setting("workflow_settings_v1")
    return (*_workflow_component_values(WORKFLOW_DEFAULTS), "已恢复默认工作参数。下次打开界面也会使用默认值。")


def _save_inline_workflow_settings(
    preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    structured_mode, region_count, remove_bad, remove_terms, shuffle, spaces, max_tags,
    few_shot_count, rag_min_score, save_score, cache_result, auto_score,
):
    return _save_workflow_values({
        "preset": preset, "system_override": system_override, "base_model": base_model,
        "safety": safety, "nsfw_injection": nsfw_injection, "user_instruction": user_instruction,
        "structured_mode": structured_mode, "region_count": int(region_count or 1),
        "remove_bad": bool(remove_bad), "remove_terms": remove_terms, "shuffle": bool(shuffle),
        "spaces": bool(spaces), "max_tags": int(max_tags or 0),
        "few_shot_count": int(few_shot_count or 0), "rag_min_score": float(rag_min_score or 0),
        "save_score": float(save_score or 0), "cache_result": bool(cache_result), "auto_score": bool(auto_score),
    })


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


def _score_selected_records(
    selected_ids, provider, endpoint, model, api_key, timeout, send_temperature,
    query="", min_score=0, filter_output_mode="全部", filter_base_model="全部",
):
    ids = _selected_values(selected_ids)[:200]
    if not ids:
        yield "请先选择需要 LLM 评分的缓存记录。", gr.update(), gr.update()
        return
    try:
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
    except Exception as error:
        yield f"LLM 评分无法开始：{_safe_error(error)}", gr.update(), gr.update()
        return

    completed, failures = 0, []
    for position, record_id in enumerate(ids, start=1):
        try:
            parsed_id = int(record_id)
        except (TypeError, ValueError):
            failures.append(f"无效记录 ID {record_id}")
            continue
        record = DB.get_prompt(parsed_id)
        if not record:
            failures.append(f"ID {record_id} 不存在")
            continue
        try:
            evaluation = evaluate_prompt_quality(
                provider, endpoint, model, resolved_key, record["prompt"], record["tags"],
                record["output_mode"], record["base_model"], int(timeout or 90), bool(send_temperature),
            )
            DB.save_prompt(
                record["prompt"], record["negative_prompt"], record["output_mode"], record["base_model"],
                evaluation["score"], record["tags"], parsed_id, "llm", evaluation["reason"], model,
            )
            completed += 1
        except Exception as error:
            failures.append(f"ID {record_id}: {_safe_error(error)}")
        yield f"LLM 评分进度 {position}/{len(ids)}：成功 {completed}，失败 {len(failures)}", gr.update(), gr.update()

    table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model, ids)
    message = f"LLM 评分完成：成功 {completed}，失败 {len(failures)}。只有成功评分的记录会进入高分 RAG。"
    if failures:
        message += " 最近错误：" + "；".join(failures[-3:])
    yield message, table, choices


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
            "Ranbooru 自然语言缓存已失效：源 Tag 已变化，需要重新转换并由 LLM 评价",
        )
        table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
        message = (
            f"Ranbooru 同步完成：新增 {stats['inserted']}，源内容更新 {stats['updated']}，"
            f"未变化 {stats['unchanged']}，失效评分 {invalidated}。新记录和发生变化的记录均为未评分，"
            "需要在缓存库选择后执行 LLM 评分才能进入高分 RAG。"
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
            continue
        seen.add(value)
        sources.append(value)
    return sources, {"ignored": ignored, "duplicates": duplicates}


def _preview_batch_sources(source_text, skip_existing, preset, base_model):
    sources, stats = _parse_batch_sources(source_text)
    cached = sum(1 for source in sources if skip_existing and DB.has_source_prompt(source, preset, base_model))
    rows = []
    for index, source in enumerate(sources[:200], start=1):
        state = "将跳过：已有缓存" if skip_existing and DB.has_source_prompt(source, preset, base_model) else "等待生成"
        rows.append([index, source, state])
    message = (
        f"队列共 {len(sources)} 条；重复输入 {stats['duplicates']} 条；空行或注释 {stats['ignored']} 条；"
        f"按当前规则将跳过已有缓存 {cached} 条。"
    )
    if len(sources) > 200:
        message += " 预览仅显示前 200 条。"
    return gr.update(value=rows), message


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
        choices.append((f"#{item.get('index', '')} · {item.get('status', '')} · {preview}", str(item["source"])))
    available = {value for _, value in choices}
    retained = [value for value in _selected_values(selected) if value in available]
    return gr.update(value=rows), gr.update(choices=choices, value=retained), records


def _select_all_batch_issues(issues):
    values = [str(item.get("source") or "") for item in (issues or []) if isinstance(item, dict) and item.get("source")]
    return gr.update(value=values)


def _clear_batch_issue_selection():
    return gr.update(value=[])


def _batch_output(status, table, cache_choices, issues, selected=None):
    issue_table, issue_choices, issue_state = _batch_issue_views(issues, selected)
    return status, table, cache_choices, issue_table, issue_choices, issue_state


def _batch_generate(
    source_text, skip_existing, skip_failed, retries, batch_score, auto_score,
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
    pending, inserted, duplicates, score_updates, skipped, failed, request_count = [], 0, 0, 0, 0, 0, 0
    issues = []

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
                    issues.append({
                        "index": remaining_index, "source": remaining_source, "status": "已取消",
                        "reason": "批量任务已取消，尚未处理", "attempts": 0,
                    })
                flush_pending()
                table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
                yield _batch_output(
                    f"任务已取消：处理 {index - 1}/{len(sources)}，新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}",
                    table, choices, issues,
                )
                return
            if skip_existing and DB.has_source_prompt(source, preset, base_model):
                skipped += 1
                issues.append({
                    "index": index, "source": source, "status": "已跳过",
                    "reason": "已有相同输入、输出预设和目标底模的缓存", "attempts": 0,
                })
                continue
            generated, last_status = "", ""
            request_count += 1
            try:
                generated, _system, last_status = _generate(
                    source, "", preset, system_override, base_model, safety, nsfw_injection, user_instruction,
                    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
                    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
                    batch_score, False, False,
                )
            except Exception as error:
                generated, last_status = "", f"生成失败：{_safe_error(error)}"
            if generated:
                score = float(batch_score or 0)
                pending.append({
                    "prompt": generated, "output_mode": preset, "base_model": base_model, "score": score,
                    "score_source": "manual", "score_reason": "批处理本地评分；未调用 LLM 评分",
                    "score_model": "", "tags": source,
                })
            else:
                failed += 1
                issues.append({
                    "index": index, "source": source, "status": "生成错误",
                    "reason": last_status or "未返回结果", "attempts": 1,
                })
                if not skip_failed and not _BATCH_CANCEL.is_set():
                    for remaining_index, remaining_source in enumerate(sources[index:], start=index + 1):
                        issues.append({
                            "index": remaining_index, "source": remaining_source, "status": "未处理",
                            "reason": "前一项单次请求失败，批量任务已停止", "attempts": 0,
                        })
                    flush_pending()
                    table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
                    yield _batch_output(
                        f"批量任务因错误停止：处理 {index}/{len(sources)}，新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}",
                        table, choices, issues,
                    )
                    return
            if len(pending) >= 10 or index == len(sources):
                flush_pending()
                table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
                yield _batch_output(
                    f"进度 {index}/{len(sources)}：LLM 请求 {request_count}，新增 {inserted}，重复 {duplicates}，评分更新 {score_updates}，跳过 {skipped}，失败 {failed}" + (f"；最近状态：{last_status}" if last_status and not generated else ""),
                    table, choices, issues,
                )
        table, choices = _filtered_cache_updates(query, min_score, filter_output_mode, filter_base_model)
        yield _batch_output(
            f"批量任务完成：LLM 请求 {request_count}，新增 {inserted}，重复 {duplicates}，评分更新 {score_updates}，跳过 {skipped}，失败 {failed}；问题汇总 {len(issues)} 条",
            table, choices, issues,
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
    selected_sources, issue_records, retries, skip_failed, batch_score, auto_score,
    preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
    query="", min_score=0, filter_output_mode="全部", filter_base_model="全部", task_id="",
):
    issues = [dict(item) for item in (issue_records or []) if isinstance(item, dict) and item.get("source")]
    requested = set(_selected_values(selected_sources))
    selected = [item for item in issues if str(item["source"]) in requested]
    if not selected:
        yield _batch_output("请先勾选需要手动重试的错误或跳过项。", gr.update(), gr.update(), issues, selected_sources)
        return

    selected_values = {str(item["source"]) for item in selected}
    original_indices = {str(item["source"]): item.get("index", "") for item in selected}
    remaining = [item for item in issues if str(item["source"]) not in selected_values]
    source_text = "\n".join(str(item["source"]) for item in selected)
    generator = _batch_generate(
        source_text, False, skip_failed, retries, batch_score, auto_score,
        preset, system_override, base_model, safety, nsfw_injection, user_instruction,
        provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
        remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
        query, min_score, filter_output_mode, filter_base_model, selected, task_id,
    )
    for status, table, cache_choices, _issue_table, _issue_choices, retry_issues in generator:
        remapped = []
        for item in retry_issues:
            updated = dict(item)
            updated["index"] = original_indices.get(str(updated.get("source") or ""), updated.get("index", ""))
            remapped.append(updated)
        combined = sorted([*remaining, *remapped], key=lambda item: int(item.get("index") or 0))
        retained_selection = selected_sources if status == "已有批量任务正在运行。" else None
        yield _batch_output(f"手动重试：{status}", table, cache_choices, combined, retained_selection)


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
    message = f"{provider} 设置已保存。模型 ID：{model or '未指定（使用服务端默认模型）'}。URL、模型 ID 和生成参数下次会自动恢复。"
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


def _score_prompt_for_cache(
    enabled, fallback_score, prompt, source, preset, base_model,
    provider, endpoint, model, api_key, timeout, send_temperature,
):
    if not enabled:
        return float(fallback_score or 0), "manual", "自动评分已关闭", "使用手动评分；该记录不会进入 LLM 高分 RAG"
    try:
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
        evaluation = evaluate_prompt_quality(
            provider, endpoint, model, resolved_key, prompt, source, preset, base_model,
            int(timeout or 90), bool(send_temperature),
        )
        score = float(evaluation["score"])
        reason = str(evaluation["reason"])
        return score, "llm", reason, f"LLM 评分 {score:.1f}/10：{_safe_error(reason)}"
    except Exception as error:
        reason = f"LLM 评分失败：{_safe_error(error)}"
        return 0.0, "unrated", reason, reason + "；已按 0 分保存，不进入高分 RAG"


def _generate(
    request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
    save_score, cache_result, auto_score, source_kind="", source_ref="",
):
    source = str(source_tags or request or "").strip()
    if not source:
        return "", "", "请输入创作要求或源 Danbooru 标签。"
    examples = DB.retrieve(
        source, int(few_shot_count or 0), float(rag_min_score or 0), preset, base_model,
    ) if int(few_shot_count or 0) else []
    static_tags = DB.wildcard_matches(source, 40)
    system = build_system_prompt(preset, base_model, safety, nsfw_injection, user_instruction, examples, static_tags, system_override)
    try:
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
        result = call_llm(provider, endpoint, model, resolved_key, system, build_user_message(source), float(temperature or 0.35), int(timeout or 90), int(max_tokens or 0), bool(send_temperature))
    except Exception as error:
        return "", system, f"生成失败：{_safe_error(error)}"
    if safety == "SFW" and not is_sfw_output(result):
        return "", system, "SFW 校验拦截了成人内容。请修改要求，或明确切换为 NSFW 模式。"
    if preset in {"Danbooru Tags", "NoobAI Tags", "Anima Tags"}:
        result = process_tags(result, bool(remove_bad), remove_terms, bool(shuffle), bool(spaces), int(max_tags or 0))
    if structured_mode != "Plain Prompt":
        result = regional_format(result, structured_mode, int(region_count or 1))
    score_status = ""
    if cache_result:
        score, score_source, score_reason, score_status = _score_prompt_for_cache(
            auto_score, save_score, result, source, preset, base_model,
            provider, endpoint, model, api_key, timeout, send_temperature,
        )
        DB.save_prompt(
            result, "", preset, base_model, score, source,
            score_source=score_source, score_reason=score_reason, score_model=model if score_source == "llm" else "",
            source_kind=source_kind or None, source_ref=source_ref or None,
        )
    status = f"生成完成，使用 {len(examples)} 条 RAG 示例" + ("，结果已缓存" if cache_result else "")
    if score_status:
        status += f"；{score_status}"
    return result, system, status


def _generate_auto_loop(
    request, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
    few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags,
    structured_mode, region_count,
):
    return _generate(
        request, "", preset, system_override, base_model, safety, nsfw_injection, user_instruction,
        provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature,
        few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags,
        structured_mode, region_count, 0, False, False,
    )


def _expand_or_polish(source, action, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature):
    instruction = "Expand this while keeping all explicit facts and the requested output format." if action == "Expand" else "Polish this for clarity, visual specificity, and model compatibility without adding unsupported facts."
    system = build_system_prompt(preset, base_model, safety, nsfw_injection, f"{user_instruction}\n{instruction}", [], system_override=system_override)
    try:
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
        return call_llm(provider, endpoint, model, resolved_key, system, build_user_message(source), float(temperature or 0.35), int(timeout or 90), int(max_tokens or 0), bool(send_temperature)), "LLM 提示词处理完成"
    except Exception as error:
        return "", f"处理失败：{_safe_error(error)}"


PNG_BATCH_SCHEMA = "prompt_batch.v1"
PNG_BATCH_MAX_PROMPT_LENGTH = 12000


def _png_batch_json(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _normalize_png_batch_payload(payload):
    if isinstance(payload, str):
        payload = json.loads(payload or "{}")
    if not isinstance(payload, dict) or payload.get("schema_version") != PNG_BATCH_SCHEMA:
        raise ValueError("仅支持 prompt_batch.v1 JSON")
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
        if record.get("status"):
            item["status"] = str(record["status"])
        if record.get("error"):
            item["error"] = str(record["error"])
        if record.get("appended") is True:
            item["appended"] = True
        if "booru" in record:
            item["booru"] = record["booru"]
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
        return _png_batch_json(data), f"已导入 {len(data['records'])} 条逐图 Prompt。"
    except Exception as error:
        return {}, f"导入失败：{_safe_error(error)}"


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
    selected, current = _png_batch_current(payload, selection)
    return _png_batch_table(payload), selected, current, f"已载入 {len(_png_batch_table(payload))} 条逐图 Prompt。"


def _png_batch_move(payload, selection, offset):
    selected, current = _png_batch_current(payload, int(selection or 1) + int(offset))
    return selected, current


def _cancel_png_batch(task_id=""):
    with _PNG_BATCH_CONTROL_LOCK:
        if not task_id or str(task_id) != _PNG_BATCH_ACTIVE_TASK_ID:
            return "当前没有正在运行的 PNG 批处理。"
        _PNG_BATCH_CANCEL.set()
    return "已请求取消；当前 LLM 请求返回后停止，已完成结果会保留。"


def _png_batch_run(
    payload, action, preset, system_override, base_model, safety, nsfw_injection,
    user_instruction, provider, endpoint, model, api_key, temperature, timeout,
    max_tokens, send_temperature, task_id,
):
    global _PNG_BATCH_ACTIVE_TASK_ID
    try:
        data = _normalize_png_batch_payload(payload or {})
    except Exception as error:
        yield payload, [], 1, "", f"处理失败：{_safe_error(error)}"
        return
    if not data["records"]:
        yield _png_batch_json(data), [], 1, "", "批次为空，请先导入逐图 Prompt。"
        return
    if not _PNG_BATCH_LOCK.acquire(blocking=False):
        selected, current = _png_batch_current(data, 1)
        yield _png_batch_json(data), _png_batch_table(data), selected, current, "已有 PNG 批处理正在运行。"
        return

    task_id = str(task_id or "")
    with _PNG_BATCH_CONTROL_LOCK:
        _PNG_BATCH_ACTIVE_TASK_ID = task_id
        _PNG_BATCH_CANCEL.clear()
    records = [dict(record) for record in data["records"]]
    progress_interval = max(1, (len(records) + 99) // 100)
    skipped_existing = reused = 0
    outcomes = {}
    try:
        for position, record in enumerate(records, 1):
            if _PNG_BATCH_CANCEL.is_set():
                for pending in records[position - 1:]:
                    if not str(pending.get("prompt", {}).get("processed") or "").strip():
                        pending["status"] = "已取消"
                        pending["error"] = "尚未处理"
                break
            if str(record.get("prompt", {}).get("processed") or "").strip():
                skipped_existing += 1
                if not record.get("status"):
                    record["status"] = "已完成"
                if position % progress_interval == 0 or position == len(records):
                    yield gr.update(), gr.update(), gr.update(), gr.update(), f"处理中 {position}/{len(records)}"
                continue
            source = record["prompt"]["positive"]
            outcome_key = source.strip()
            if outcome_key in outcomes:
                processed, llm_status = outcomes[outcome_key]
                reused += 1
            else:
                processed, llm_status = _expand_or_polish(
                    source, action, preset, system_override, base_model, safety,
                    nsfw_injection, user_instruction, provider, endpoint, model,
                    api_key, temperature, timeout, max_tokens, send_temperature,
                )
                outcomes[outcome_key] = (processed, llm_status)
            if processed:
                record["prompt"] = {**record["prompt"], "processed": processed}
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
        yield _png_batch_json(result), _png_batch_table(result), selected, current, f"批处理结束：完成 {completed}，相同 Prompt 复用 {reused}，已有结果跳过 {skipped_existing}，失败 {failed}，取消 {cancelled}。"
    finally:
        with _PNG_BATCH_CONTROL_LOCK:
            if _PNG_BATCH_ACTIVE_TASK_ID == task_id:
                _PNG_BATCH_ACTIVE_TASK_ID = ""
                _PNG_BATCH_CANCEL.clear()
        _PNG_BATCH_LOCK.release()


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


def _inline_generate(*values):
    generated, system, status = _generate(*values)
    return generated, system, status, generated if generated else gr.update()


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
            print(f"[LLM Prompt Studio] 跳过内嵌面板：未找到 {slot} 正向提示词组件")
            return
        _INLINE_SLOTS.add(slot)
    try:
        _create_inline_panel(slot, prompt_target)
    except Exception as error:
        with _INLINE_LOCK:
            _INLINE_SLOTS.discard(slot)
        print(f"[LLM Prompt Studio] 内嵌面板创建失败：{error}")


def _create_inline_panel(slot, prompt_target):
    settings = _connection_settings()
    workflow = _workflow_settings()
    with gr.Accordion("LLM 提示词工作室", open=False, elem_id=f"llm_prompt_studio_{slot}_inline"):
        gr.Markdown("生成结果会直接写入上方正向提示词。系统策略和模型规则优先于用户要求；RAG 与静态词库仅作为参考数据。")
        with gr.Row():
            with gr.Column(scale=3):
                request = gr.Textbox(label="创作要求", lines=3, placeholder="描述希望生成的画面")
                source_tags = gr.Textbox(
                    label="源 Danbooru 标签（可选，优先使用）",
                    lines=2,
                    elem_id=f"llm_prompt_studio_{slot}_source_tags",
                )
                preset = gr.Dropdown(label="System Prompt 预设", choices=PRESET_UI_CHOICES, value=workflow["preset"])
                system_override = gr.Textbox(label="自定义 System Prompt（可选）", lines=3, value=workflow["system_override"], placeholder="留空则使用所选预设")
                user_instruction = gr.Textbox(label="用户输出要求（低优先级）", lines=2, value=workflow["user_instruction"], placeholder="例如：最多 30 个标签，不使用权重")
            with gr.Column(scale=2):
                base_model = gr.Dropdown(label="目标底模", choices=MODEL_UI_CHOICES, value=workflow["base_model"])
                safety = gr.Radio(label="内容模式", choices=["SFW", "NSFW"], value=workflow["safety"])
                nsfw_injection = gr.Textbox(label="NSFW 策略注入", lines=2, value=workflow["nsfw_injection"], placeholder="仅在 NSFW 模式下生效")
                structured_mode = gr.Radio(label="输出格式", choices=OUTPUT_UI_CHOICES, value=workflow["structured_mode"])
                region_count = gr.Slider(label="区域数量", minimum=1, maximum=8, value=workflow["region_count"], step=1)
        with gr.Accordion("LLM 连接设置", open=False):
            with gr.Row():
                provider = gr.Dropdown(label="Provider", choices=PROVIDER_UI_CHOICES, value=settings["provider"])
                endpoint = gr.Textbox(label="接口地址", value=settings["endpoint"])
                model = gr.Textbox(label="模型 ID", value=settings["model"], elem_id=f"llm_prompt_studio_{slot}_model_id")
                api_key = gr.Textbox(label="API Key（留空则使用已保存凭据）", type="password")
                temperature = gr.Slider(label="温度", minimum=0, maximum=2, value=settings["temperature"], step=0.05)
                timeout = gr.Slider(label="超时秒数", minimum=5, maximum=600, value=settings["timeout"], step=5)
            with gr.Row():
                max_tokens = gr.Number(label="最大输出 Token（0 表示使用 Provider 默认值）", value=settings["max_tokens"], precision=0)
                send_temperature = gr.Checkbox(label="发送温度参数（推理模型不支持时关闭）", value=settings["send_temperature"])
            with gr.Row():
                test = gr.Button("测试 API")
                save_connection = gr.Button("保存全部 LLM 设置")
                clear_credentials = gr.Button("清除已保存的 API Key")
            connection_status = gr.Markdown(_credential_status(settings["provider"], settings["endpoint"]))
        with gr.Accordion("标签处理与 RAG", open=False):
            with gr.Row():
                remove_bad = gr.Checkbox(label="移除不良标签", value=workflow["remove_bad"])
                shuffle = gr.Checkbox(label="随机打乱标签", value=workflow["shuffle"])
                spaces = gr.Checkbox(label='将“_”转换为空格', value=workflow["spaces"])
                max_tags = gr.Slider(label="最大标签数（0 表示不限）", minimum=0, maximum=200, value=workflow["max_tags"], step=1)
            remove_terms = gr.Textbox(label="额外排除标签 / 通配规则", value=workflow["remove_terms"], placeholder="watermark, *_text")
            with gr.Row():
                few_shot_count = gr.Slider(label="Few-Shot 示例数", minimum=0, maximum=8, value=workflow["few_shot_count"], step=1)
                rag_min_score = gr.Slider(label="RAG 最低评分", minimum=0, maximum=10, value=workflow["rag_min_score"], step=0.5)
                save_score = gr.Slider(label="手动评分（关闭自动评分时使用）", minimum=0, maximum=10, value=workflow["save_score"], step=0.5)
                cache_result = gr.Checkbox(label="缓存生成结果", value=workflow["cache_result"])
                auto_score = gr.Checkbox(label="使用 LLM 自动评分", value=workflow["auto_score"])
        with gr.Row():
            generate = gr.Button("生成并写入正向提示词", variant="primary")
            save_workflow = gr.Button("保存提示词参数")
        output = gr.Textbox(label="生成的提示词", lines=5)
        system_preview = gr.Textbox(label="最终 System Prompt 与策略", lines=8)
        status = gr.Markdown("已自动填入上次保存的提示词参数。" if DB.get_setting("workflow_settings_v1") else "当前使用默认提示词参数。")
        inputs = [request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, save_score, cache_result, auto_score]
        generate.click(_inline_generate, inputs=inputs, outputs=[output, system_preview, status, prompt_target])
        provider.change(_load_provider_settings, inputs=provider, outputs=[endpoint, model, temperature, timeout, max_tokens, send_temperature, connection_status])
        test.click(_test_connection, inputs=[provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=connection_status)
        save_connection.click(_save_llm_settings, inputs=[provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=[connection_status, endpoint, model])
        clear_credentials.click(_clear_llm_credentials, inputs=[provider, endpoint], outputs=connection_status)
        save_workflow.click(
            _save_inline_workflow_settings,
            inputs=[preset, system_override, base_model, safety, nsfw_injection, user_instruction, structured_mode, region_count, remove_bad, remove_terms, shuffle, spaces, max_tags, few_shot_count, rag_min_score, save_score, cache_result, auto_score],
            outputs=status,
        )


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
        url = endpoint.rstrip("/") + "/tagger/v1/interrogate"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
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
        "few_shot_count": 3, "rag_min_score": 0,
        "remove_bad": True, "remove_terms": "", "shuffle": False, "spaces": False, "max_tags": 0,
        "structured_mode": "Plain Prompt", "region_count": 1, "save_score": 0, "cache_result": False, "auto_score": True,
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
    for field in ("send_temperature", "remove_bad", "shuffle", "spaces", "cache_result", "auto_score"):
        if field in payload and type(payload[field]) is not bool:
            raise ValueError(f"API field {field} must be a boolean")
    if "provider" in payload and payload["provider"] != saved_connection["provider"]:
        raise ValueError("API Provider must match the active connection saved in the plugin UI")
    if "endpoint" in payload and validate_endpoint(payload["endpoint"]) != validate_endpoint(saved_connection["endpoint"]):
        raise ValueError("API endpoint must match the connection saved in the plugin UI")
    values = {**defaults, **(payload or {})}
    generated, system, status = _generate(values.get("request", ""), *[values[key] for key in [
        "source_tags", "preset", "system_override", "base_model", "safety", "nsfw_injection", "user_instruction", "provider", "endpoint", "model", "api_key", "temperature", "timeout", "max_tokens", "send_temperature", "few_shot_count", "rag_min_score", "remove_bad", "remove_terms", "shuffle", "spaces", "max_tags", "structured_mode", "region_count", "save_score", "cache_result", "auto_score"
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
    if record.get("payload_decode_error"):
        message = str(record["payload_decode_error"])
        DB.update_handoff(record["id"], "error", attempts=0, error=message)
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
    DB.update_handoff(record["id"], "processing", attempts=0)
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
            workflow["save_score"], True, False, "ranbooru", prompt_source_ref,
        )
    except Exception as error:
        generated = ""
        status = f"未预期异常：{error}"
    if generated:
        DB.update_handoff(record["id"], "completed", attempts=1, result_prompt=generated)
        return {
            "handoff_id": record["id"], "prompt": generated, "system_prompt": system,
            "status": f"Ranbooru 实时处理完成（LLM 请求 1 次）：{status}",
        }
    DB.update_handoff(record["id"], "error", attempts=1, error=status)
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
    if not str(handoff_id or "").isdigit() or not DB.update_handoff(int(handoff_id), "skipped", error="用户手动跳过"):
        table, choices, _ = _handoff_views(handoff_id)
        return "请选择有效的交接记录。", table, choices
    table, choices, _ = _handoff_views(handoff_id)
    return f"已跳过交接 #{handoff_id}；记录仍保留，可稍后手动重试。", table, choices


def _clear_finished_handoffs():
    deleted = DB.delete_handoffs({"completed", "skipped"})
    table, choices, _ = _handoff_views()
    return f"已清理 {deleted} 条已完成或已跳过的交接记录；失败记录仍保留。", table, choices


def on_app_started(_, app):
    saved_workflow = DB.get_setting("workflow_settings_v1", {}) or {}
    wildcard_source = Path(saved_workflow.get("wildcard_path") or DEFAULT_WILDCARDS) if isinstance(saved_workflow, dict) else DEFAULT_WILDCARDS
    if wildcard_source.is_dir():
        try:
            files, terms = DB.index_wildcards(wildcard_source)
            print(f"[LLM Prompt Studio] wildcard library ready: {files} updated files, {terms} terms")
        except Exception as error:
            print(f"[LLM Prompt Studio] wildcard indexing skipped: {error}")
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
            print("[LLM Prompt Studio] API protected by Forge --api-auth")

        @app.post("/llm-prompt-studio/v1/generate", dependencies=api_dependencies)
        def prompt_studio_generate(payload: dict[str, Any]):
            try:
                return _api_generate(payload)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error

        @app.get("/llm-prompt-studio/v1/cache", dependencies=api_dependencies)
        def prompt_studio_cache(query: str = "", limit: int = 100):
            return {"records": DB.list_prompts(query, limit)}

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
        print(f"[LLM Prompt Studio] API registration failed: {error}")


def on_ui_tabs():
    llm_settings = _connection_settings()
    workflow = _workflow_settings()
    ranbooru_link = _ranbooru_link_settings()
    initial_records = DB.list_prompts()
    initial_handoff_table, initial_handoff_choices, initial_handoff_status = _handoff_views()
    with gr.Blocks(analytics_enabled=False, css=UI_CSS, elem_id="llm_prompt_studio") as ui:
        gr.Markdown("## LLM 提示词工作室\n本地静态词库、RAG Few-Shot、提示词缓存与 Forge 扩展集成。")
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
                        preset = gr.Dropdown(label="System Prompt 预设", choices=PRESET_UI_CHOICES, value=workflow["preset"])
                        base_model = gr.Dropdown(label="目标底模", choices=MODEL_UI_CHOICES, value=workflow["base_model"])
                        safety = gr.Radio(label="内容模式", choices=["SFW", "NSFW"], value=workflow["safety"])
                        with gr.Accordion("高级 Prompt 约束", open=False):
                            system_override = gr.Textbox(label="自定义 System Prompt（可选）", lines=6, value=workflow["system_override"], placeholder="留空则使用所选预设。安全策略、用户要求、RAG 和静态词库会自动追加。")
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
                        with gr.Accordion("RAG 与缓存", open=False):
                            few_shot_count = gr.Slider(label="Few-Shot 示例数", minimum=0, maximum=8, value=workflow["few_shot_count"], step=1)
                            rag_min_score = gr.Slider(label="RAG 最低缓存评分", minimum=0, maximum=10, value=workflow["rag_min_score"], step=0.5)
                            save_score = gr.Slider(label="手动评分（关闭自动评分时使用）", minimum=0, maximum=10, value=workflow["save_score"], step=0.5)
                            cache_result = gr.Checkbox(label="在本地缓存本次结果", value=workflow["cache_result"], elem_id="llm_prompt_studio_cache_result")
                            auto_score = gr.Checkbox(label="使用 LLM 自动评价并评分", value=workflow["auto_score"], elem_id="llm_prompt_studio_auto_score")
                with gr.Row():
                    generate = gr.Button("生成提示词", variant="primary", elem_id="llm_prompt_studio_generate_button")
                    save_workflow = gr.Button("保存全部工作参数")
                    reset_workflow = gr.Button("恢复默认工作参数")
                output = gr.Textbox(label="生成的提示词", lines=8, elem_id="llm_prompt_studio_output")
                system_preview = gr.Textbox(label="最终 System Prompt", lines=12)
                status = gr.Markdown(elem_id="llm_prompt_studio_status", elem_classes=["lps-status"])
                workflow_status = gr.Markdown("已自动载入上次保存的工作参数。" if DB.get_setting("workflow_settings_v1") else "当前使用默认工作参数；保存后下次会自动填入。")

            with gr.Tab("批处理", elem_id="llm_prompt_studio_batch_tab"):
                with gr.Tabs():
                    with gr.Tab("LLM 批量生成"):
                        gr.Markdown("每条去重后的输入只发送一次 LLM 生成请求；失败不会自动重试，批量评分不会调用 LLM。")
                        batch_sources = gr.Textbox(label="生成队列：每行一条创作要求或源标签", lines=12, placeholder="红发魔法师在月光图书馆阅读\n蓝发少女站在雨中的车站")
                        with gr.Row():
                            batch_skip_existing = gr.Checkbox(label="跳过相同输入的已有缓存", value=workflow["batch_skip_existing"])
                            batch_skip_failed = gr.Checkbox(label="单条失败后跳过并继续", value=workflow["batch_skip_failed"])
                            batch_retries = gr.State(0)
                            batch_score = gr.Slider(label="本地评分（不调用 LLM）", minimum=0, maximum=10, value=workflow["batch_score"], step=0.5)
                        with gr.Row():
                            batch_preview_button = gr.Button("预览生成队列")
                            batch_generate = gr.Button("开始生成并缓存", variant="primary")
                            batch_cancel = gr.Button("取消批量任务", variant="stop")
                            save_batch_workflow = gr.Button("保存批量与工作参数")
                        batch_preview_status = gr.Markdown()
                        batch_queue = gr.Dataframe(headers=["序号", "输入", "状态"], datatype=["number", "str", "str"], interactive=False, wrap=True, label="队列预览")
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
                    with gr.Tab("PNG 润色 / 扩写", elem_id="llm_prompt_studio_png_batch_tab"):
                        png_batch_file = gr.File(label="导入 prompt_batch.v1 JSON", file_types=[".json"], type="filepath", elem_id="llm_prompt_studio_png_batch_file")
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
                            png_batch_export = gr.DownloadButton("导出结果", elem_id="llm_prompt_studio_png_batch_export")
                        png_batch_table = gr.Dataframe(headers=["序号", "文件", "原始正向 Prompt", "状态", "LLM 结果", "错误"], datatype=["number", "str", "str", "str", "str", "str"], interactive=False, wrap=True, elem_id="llm_prompt_studio_png_batch_table", elem_classes=["lps-table"])
                        gr.Markdown("", elem_id="llm_prompt_studio_png_batch_results")
                        png_batch_status = gr.HTML("等待导入 JSON。", elem_id="llm_prompt_studio_png_batch_status", elem_classes=["lps-status"])
                        png_batch_append_succeeded = gr.Checkbox(value=False, visible=False, elem_id="llm_prompt_studio_png_batch_append_succeeded")
                        png_batch_task_id = gr.State(lambda: uuid.uuid4().hex)
                        png_batch_file_event = png_batch_file.change(
                            _png_batch_load,
                            inputs=png_batch_file,
                            outputs=[png_batch_payload, png_batch_status],
                        )

                    with gr.Tab("自动生图循环", elem_id="llm_prompt_studio_auto_loop_tab"):
                        gr.Markdown(
                            "分两步工作：先按每行一个创作要求批量生成并保存逐条 Prompt；确认队列后，再投入 Forge 原生 txt2img / img2img 生图。"
                            "重复行会自动去重，每条唯一输入只发送一次 LLM 请求。"
                            "生成阶段不会评分，也不会写入 Prompt 缓存。队列保存在当前浏览器中，页面关闭后仍可恢复。"
                        )
                        with gr.Row(elem_classes=["lps-form-row"]):
                            auto_loop_target = gr.Radio(
                                label="生图目标", choices=[("txt2img", "txt2img"), ("img2img", "img2img")],
                                value="txt2img", elem_id="llm_prompt_studio_auto_loop_target",
                            )
                            auto_loop_write_mode = gr.Radio(
                                label="Prompt 写入方式", choices=[("覆盖", "replace"), ("追加", "append")],
                                value="replace", elem_id="llm_prompt_studio_auto_loop_write_mode",
                            )
                        auto_loop_request = gr.Textbox(
                            label="批量创作要求（每行一条）", lines=8,
                            placeholder="赛博朋克城市夜景\n月光图书馆中的红发魔法师",
                            elem_id="llm_prompt_studio_auto_loop_request",
                        )
                        with gr.Row():
                            auto_loop_start = gr.Button("批量生成 Prompt", variant="primary", elem_id="llm_prompt_studio_auto_loop_start")
                            auto_loop_run = gr.Button("投入队列生图", variant="primary", elem_id="llm_prompt_studio_auto_loop_run")
                            auto_loop_clear = gr.Button("清空队列", elem_id="llm_prompt_studio_auto_loop_clear")
                            auto_loop_cancel = gr.Button("取消当前阶段", variant="stop", elem_id="llm_prompt_studio_auto_loop_cancel")
                        auto_loop_dispatch = gr.Button(
                            "自动队列单次生成",
                            elem_id="llm_prompt_studio_auto_loop_dispatch",
                            elem_classes=["lps-auto-loop-dispatch"],
                        )
                        auto_loop_status = gr.HTML(
                            "等待开始。先批量生成 Prompt，确认队列后再投入生图。",
                            elem_id="llm_prompt_studio_auto_loop_status", elem_classes=["lps-status"],
                        )
                        gr.HTML("", elem_id="llm_prompt_studio_auto_loop_log", elem_classes=["lps-auto-loop-log"])

                    with gr.Tab("直接批量导入"):
                        bulk_import = gr.Textbox(label="每行一条 Prompt，可使用“评分<TAB>Prompt”格式", lines=12)
                        with gr.Row():
                            bulk_output_mode = gr.Dropdown(label="缓存格式", choices=PRESET_UI_CHOICES, value=workflow["preset"])
                            bulk_base_model = gr.Dropdown(label="目标底模", choices=MODEL_UI_CHOICES, value=workflow["base_model"])
                            bulk_default_score = gr.Slider(label="默认评分", minimum=0, maximum=10, value=workflow["batch_score"], step=0.5)
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
                    score_selected = gr.Button("使用 LLM 评分所选")
                    delete_selected = gr.Button("删除所选", variant="stop")
                selection_preview = gr.Textbox(label="选择 / 删除预览", lines=6, interactive=False)
                table = gr.Dataframe(
                    value=_as_rows(initial_records), label="已缓存 Prompt",
                    headers=["全库序号", "内部 ID", "评分", "评分来源", "评分模型", "格式", "目标模型", "正向提示词", "负面提示词", "源标签", "评分理由", "外部来源", "来源标识"],
                    datatype=["number", "number", "number", "str", "str", "str", "str", "str", "str", "str", "str", "str", "str"],
                    interactive=False, wrap=True, elem_id="llm_prompt_studio_cache_table", elem_classes=["lps-table"],
                )
                with gr.Accordion("记录编辑器", open=True):
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
                with gr.Accordion("Ranbooru 缓存联动", open=True):
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
                    with gr.Accordion("Ranbooru 实时交接箱", open=True):
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
                        index = gr.Button("建立 / 刷新本地索引", elem_id="llm_prompt_studio_wildcard_index", elem_classes=["lps-primary"])
                        wildcard_status = gr.Markdown(elem_id="llm_prompt_studio_wildcard_status", elem_classes=["lps-status"])
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
            few_shot_count, rag_min_score, save_score, cache_result, auto_score,
            batch_skip_existing, batch_skip_failed, batch_retries, batch_score,
            wd_endpoint, wd_model, wd_threshold, wildcard_path,
        ]
        generate.click(_generate, inputs=[request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, save_score, cache_result, auto_score], outputs=[output, system_preview, status])
        auto_loop_dispatch.click(
            _generate_auto_loop,
            inputs=[request, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count],
            outputs=[output, system_preview, status],
        )
        save_workflow.click(_save_workflow_settings, inputs=workflow_inputs, outputs=workflow_status)
        save_batch_workflow.click(_save_workflow_settings, inputs=workflow_inputs, outputs=batch_status)
        reset_workflow.click(_reset_workflow_settings, outputs=[*workflow_inputs, workflow_status])
        provider.change(_load_provider_settings, inputs=provider, outputs=[endpoint, model, temperature, timeout, max_tokens, send_temperature, test_status])
        test.click(_test_connection, inputs=[provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=test_status)
        save_connection.click(_save_llm_settings, inputs=[provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=[test_status, endpoint, model])
        clear_credentials.click(_clear_llm_credentials, inputs=[provider, endpoint], outputs=test_status)
        png_batch_payload.input(_png_batch_refresh, inputs=[png_batch_payload, png_batch_selection], outputs=[png_batch_table, png_batch_selection, png_batch_current, png_batch_status])
        png_batch_file_event.then(
            _png_batch_refresh,
            inputs=[png_batch_payload, png_batch_selection],
            outputs=[png_batch_table, png_batch_selection, png_batch_current, png_batch_status],
        )
        png_batch_previous.click(_png_batch_move, inputs=[png_batch_payload, png_batch_selection, gr.State(-1)], outputs=[png_batch_selection, png_batch_current])
        png_batch_next.click(_png_batch_move, inputs=[png_batch_payload, png_batch_selection, gr.State(1)], outputs=[png_batch_selection, png_batch_current])
        png_batch_selection.change(_png_batch_current, inputs=[png_batch_payload, png_batch_selection], outputs=[png_batch_selection, png_batch_current])
        png_batch_run.click(
            _png_batch_run,
            inputs=[png_batch_payload, png_batch_action, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, png_batch_task_id],
            outputs=[png_batch_payload, png_batch_table, png_batch_selection, png_batch_current, png_batch_status],
        )
        png_batch_cancel.click(_cancel_png_batch, inputs=png_batch_task_id, outputs=png_batch_status, queue=False)
        png_batch_export.click(_png_batch_export_file, inputs=png_batch_payload, outputs=png_batch_export)
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
        auto_loop_cancel.click(
            fn=None,
            inputs=[],
            outputs=auto_loop_status,
            js="() => window.llmPromptStudioAutoLoop.cancel()",
            queue=False,
        )
        auto_loop_clear.click(
            fn=None,
            inputs=[],
            outputs=auto_loop_status,
            js="() => window.llmPromptStudioAutoLoop.clearQueue()",
            queue=False,
        )
        index.click(_index_wildcards, inputs=wildcard_path, outputs=[wildcard_status, wildcard_results])
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
        score_selected.click(
            _score_selected_records,
            inputs=[selected_records, provider, endpoint, model, api_key, timeout, send_temperature, *cache_filter_inputs],
            outputs=[cache_status, table, selected_records],
        )
        delete_selected.click(_delete_previewed_records, inputs=[selected_records, delete_preview_state, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        save.click(_save_record, inputs=[record_id, record_prompt, record_negative, record_output_mode, record_base_model, record_score, record_tags, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        save_as_new.click(_save_record_as_new, inputs=[record_prompt, record_negative, record_output_mode, record_base_model, record_score, record_tags, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        bulk_preview_button.click(_preview_bulk_cache, inputs=[bulk_import, bulk_output_mode, bulk_base_model, bulk_default_score], outputs=[bulk_preview, bulk_import_status])
        bulk_import_button.click(_bulk_cache, inputs=[bulk_import, bulk_output_mode, bulk_base_model, bulk_default_score, *cache_filter_inputs], outputs=[bulk_import_status, table, selected_records])
        batch_preview_button.click(_preview_batch_sources, inputs=[batch_sources, batch_skip_existing, preset, base_model], outputs=[batch_queue, batch_preview_status])
        batch_generate.click(
            _batch_generate,
            inputs=[batch_sources, batch_skip_existing, batch_skip_failed, batch_retries, batch_score, auto_score, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, *cache_filter_inputs, batch_issue_state, batch_task_id],
            outputs=[batch_status, table, selected_records, batch_issues, batch_issue_selection, batch_issue_state],
        )
        batch_cancel.click(_cancel_batch_generation, inputs=batch_task_id, outputs=batch_status)
        batch_select_all_issues.click(_select_all_batch_issues, inputs=batch_issue_state, outputs=batch_issue_selection)
        batch_clear_issue_selection.click(_clear_batch_issue_selection, outputs=batch_issue_selection)
        batch_retry_selected.click(
            _retry_batch_issues,
            inputs=[batch_issue_selection, batch_issue_state, batch_retries, batch_skip_failed, batch_score, auto_score, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, *cache_filter_inputs, batch_task_id],
            outputs=[batch_status, table, selected_records, batch_issues, batch_issue_selection, batch_issue_state],
        )
        preview_positions.click(_preview_positions, inputs=position_spec, outputs=[cache_status, table, selected_records])
        delete_positions.click(_delete_positions, inputs=[position_spec, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        undo_delete.click(_undo_last_delete, inputs=cache_filter_inputs, outputs=[cache_status, table, selected_records])
        import_button.click(_import_cache, inputs=[import_file, import_dedupe, *cache_filter_inputs], outputs=[cache_status, table, selected_records])
        export_selected.click(_export_selected, inputs=[selected_records, export_format], outputs=[cache_status, export_file])
        export_button.click(_export_cache, inputs=export_format, outputs=[cache_status, export_file])
    return [(ui, "LLM 提示词工作室", "llm_prompt_studio")]
