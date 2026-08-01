from __future__ import annotations

import base64
import html
import ipaddress
import json
import sys
import threading
from pathlib import Path
from typing import Any

import gradio as gr
from starlette.requests import Request

from prompt_studio_core import (
    BASE_MODEL_GUIDANCE, DEFAULT_WILDCARDS, PRESETS, PROVIDER_PROFILES, CredentialStore, StudioDB,
    build_system_prompt, build_user_message, call_llm, get_provider_profile, is_sfw_output,
    process_tags, regional_format, validate_endpoint,
)


DB = StudioDB()
CREDENTIALS = CredentialStore()
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
_PROMPT_TARGETS: dict[str, Any] = {}
_INLINE_SLOTS: set[str] = set()
_INLINE_LOCK = threading.RLock()
_BATCH_CANCEL = threading.Event()
_BATCH_LOCK = threading.Lock()


def _as_rows(records: list[dict[str, Any]]) -> list[list[Any]]:
    return [[row.get("visible_position", ""), row["id"], row["score"], row["output_mode"], row["base_model"], row["prompt"], row["negative_prompt"], row["tags"]] for row in records]


def _refresh_cache(query: str = ""):
    records = DB.list_prompts(query)
    return gr.update(value=_as_rows(records)), f"本地缓存共 {len(records)} 条记录"


def _safe_error(error: Exception) -> str:
    return html.escape(str(error), quote=False)


def _connection_store() -> dict[str, Any]:
    stored = DB.get_setting("llm_connections_v2", {}) or {}
    if isinstance(stored, dict) and isinstance(stored.get("providers"), dict):
        return stored
    legacy = DB.get_setting("llm_connection", {}) or {}
    provider = str(legacy.get("provider") or legacy.get("backend") or DEFAULT_LLM_SETTINGS["provider"])
    if provider not in PROVIDER_PROFILES:
        provider = DEFAULT_LLM_SETTINGS["provider"]
    return {"version": 2, "active_provider": provider, "providers": {provider: {
        "endpoint": legacy.get("endpoint") or get_provider_profile(provider)["default_endpoint"],
        "model": legacy.get("model") or "",
        "temperature": legacy.get("temperature", DEFAULT_LLM_SETTINGS["temperature"]),
        "timeout": legacy.get("timeout", DEFAULT_LLM_SETTINGS["timeout"]),
        "max_tokens": legacy.get("max_tokens", DEFAULT_LLM_SETTINGS["max_tokens"]),
        "send_temperature": legacy.get("send_temperature", get_provider_profile(provider)["send_temperature"]),
    }}}


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
        return "", "", 0, "", "未找到该记录"
    return record["prompt"], record["negative_prompt"], record["score"], record["tags"], f"已载入记录 #{record['id']}"


def _save_record(record_id, prompt, negative, output_mode, base_model, score, tags):
    try:
        parsed_id = int(record_id) if str(record_id).strip() else None
    except ValueError:
        parsed_id = None
    if not str(prompt).strip():
        return "提示词不能为空", gr.update()
    saved_id = DB.save_prompt(str(prompt).strip(), str(negative or ""), output_mode, base_model, float(score or 0), str(tags or ""), parsed_id)
    return f"已保存缓存记录 #{saved_id}", gr.update(value=_as_rows(DB.list_prompts()))


def _delete_records(ids):
    pieces = [piece.strip() for piece in str(ids or "").split(",") if piece.strip()]
    try:
        count = DB.delete_prompts([int(piece) for piece in pieces])
    except ValueError:
        return "记录 ID 必须是用逗号分隔的整数", gr.update()
    return f"已删除 {count} 条记录", gr.update(value=_as_rows(DB.list_prompts()))


def _bulk_cache(import_text, output_mode, base_model, default_score):
    """Import one prompt per line, optionally prefixed by `score<TAB>`."""
    records = []
    for line in str(import_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
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
    stats = DB.save_prompts_batch(records, dedupe=True)
    return f"批量导入完成：新增 {stats['inserted']} 条，跳过重复 {stats['duplicates']} 条", gr.update(value=_as_rows(DB.list_prompts()))


def _preview_positions(position_spec):
    try:
        records = DB.get_by_positions(position_spec)
        return f"范围预览命中 {len(records)} 条记录", gr.update(value=_as_rows(records))
    except ValueError as error:
        return f"序号范围格式错误：{error}", gr.update()


def _delete_positions(position_spec):
    try:
        count = DB.delete_by_positions(position_spec)
        return f"已删除 {count} 条记录，并创建自动备份；可使用撤销按钮恢复。", gr.update(value=_as_rows(DB.list_prompts()))
    except ValueError as error:
        return f"序号范围格式错误：{error}", gr.update()


def _undo_last_delete():
    count = DB.undo_last_delete()
    message = f"已恢复上次删除的 {count} 条记录" if count else "没有可撤销的删除操作"
    return message, gr.update(value=_as_rows(DB.list_prompts()))


def _export_cache(file_format):
    try:
        path = DB.export_records(str(file_format or "JSON").lower())
        return f"导出完成：{path}", path
    except Exception as error:
        return f"导出失败：{_safe_error(error)}", None


def _import_cache(file_value, dedupe):
    path = getattr(file_value, "name", file_value)
    if not path:
        return "请选择 JSON 或 CSV 文件", gr.update()
    try:
        stats = DB.import_records(path, bool(dedupe))
        return f"导入完成：新增 {stats['inserted']} 条，跳过重复 {stats['duplicates']} 条", gr.update(value=_as_rows(DB.list_prompts()))
    except Exception as error:
        return f"导入失败：{_safe_error(error)}", gr.update()


def _cancel_batch_generation():
    _BATCH_CANCEL.set()
    return "已请求取消。当前 HTTP 请求返回后会停止，并保存已完成结果。"


def _batch_generate(
    source_text, skip_existing, retries, batch_score,
    preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
):
    sources = []
    seen = set()
    for line in str(source_text or "").splitlines():
        value = line.strip()
        if value and not value.startswith("#") and value not in seen:
            seen.add(value)
            sources.append(value)
        if len(sources) >= 10000:
            break
    if not sources:
        yield "请输入批量请求，每行一条。", gr.update()
        return
    if not _BATCH_LOCK.acquire(blocking=False):
        yield "已有批量任务正在运行。", gr.update()
        return
    _BATCH_CANCEL.clear()
    pending, inserted, duplicates, skipped, failed = [], 0, 0, 0, 0

    def flush_pending():
        nonlocal inserted, duplicates
        if not pending:
            return
        stats = DB.save_prompts_batch(pending, dedupe=True)
        inserted += stats["inserted"]
        duplicates += stats["duplicates"]
        pending.clear()

    try:
        for index, source in enumerate(sources, start=1):
            if _BATCH_CANCEL.is_set():
                flush_pending()
                yield f"任务已取消：处理 {index - 1}/{len(sources)}，新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}", gr.update(value=_as_rows(DB.list_prompts()))
                return
            if skip_existing and DB.has_source_prompt(source, preset, base_model):
                skipped += 1
                continue
            generated, last_status = "", ""
            for _attempt in range(max(0, min(int(retries or 0), 3)) + 1):
                generated, _system, last_status = _generate(
                    source, "", preset, system_override, base_model, safety, nsfw_injection, user_instruction,
                    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
                    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
                    batch_score, False,
                )
                if generated or _BATCH_CANCEL.is_set():
                    break
            if generated:
                pending.append({"prompt": generated, "output_mode": preset, "base_model": base_model, "score": batch_score, "tags": source})
            else:
                failed += 1
            if len(pending) >= 10 or index == len(sources):
                flush_pending()
                yield f"进度 {index}/{len(sources)}：新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}" + (f"；最近状态：{last_status}" if last_status and not generated else ""), gr.update(value=_as_rows(DB.list_prompts()))
        yield f"批量任务完成：新增 {inserted}，重复 {duplicates}，跳过 {skipped}，失败 {failed}", gr.update(value=_as_rows(DB.list_prompts()))
    finally:
        try:
            flush_pending()
        finally:
            _BATCH_CANCEL.clear()
            _BATCH_LOCK.release()


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
        return "保存失败：不支持的 Provider。", gr.update()
    try:
        endpoint = validate_endpoint(endpoint)
        settings = {
            "endpoint": endpoint,
            "model": str(model or "").strip(),
            "temperature": max(0.0, min(float(temperature), 2.0)),
            "timeout": max(5, min(int(timeout), 600)),
            "max_tokens": max(0, min(int(max_tokens), 262144)),
            "send_temperature": bool(send_temperature),
        }
    except (TypeError, ValueError) as error:
        return f"保存失败：{_safe_error(error)}", gr.update()
    store = _connection_store()
    providers = dict(store.get("providers", {}))
    providers[provider] = settings
    DB.set_setting("llm_connections_v2", {"version": 2, "active_provider": provider, "providers": providers})
    DB.set_setting("llm_connection", {"backend": provider, **settings})
    key_saved = CREDENTIALS.save(provider, endpoint, api_key)
    key_available = key_saved or CREDENTIALS.has_matching(provider, endpoint)
    message = f"{provider} 设置已保存，URL、模型和生成参数下次会自动恢复。"
    if key_available:
        message += " API Key 已按 Provider 与 URL 保存在服务端，下次可留空。"
    elif get_provider_profile(provider).get("requires_api_key"):
        message += " 尚未保存 API Key，调用前必须填写。"
    else:
        message += " 当前未保存 API Key。"
    return message, gr.update(value=endpoint)


def _clear_llm_credentials(provider, endpoint):
    try:
        cleared = CREDENTIALS.clear(provider, validate_endpoint(endpoint))
    except ValueError as error:
        return f"清除失败：{_safe_error(error)}"
    return "已清除当前 Provider 与 URL 对应的 API Key。" if cleared else "当前连接没有已保存的 API Key。"


def _generate(
    request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction,
    provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score,
    remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count,
    save_score, cache_result,
):
    source = str(source_tags or request or "").strip()
    if not source:
        return "", "", "请输入创作要求或源 Danbooru 标签。"
    examples = DB.retrieve(source, int(few_shot_count or 0), float(rag_min_score or 0)) if int(few_shot_count or 0) else []
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
    if cache_result:
        DB.save_prompt(result, "", preset, base_model, float(save_score or 0), source)
    return result, system, f"生成完成，使用 {len(examples)} 条 RAG 示例" + ("，结果已缓存" if cache_result else "")


def _expand_or_polish(source, action, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature):
    instruction = "Expand this while keeping all explicit facts and the requested output format." if action == "Expand" else "Polish this for clarity, visual specificity, and model compatibility without adding unsupported facts."
    system = build_system_prompt(preset, base_model, safety, nsfw_injection, f"{user_instruction}\n{instruction}", [], system_override=system_override)
    try:
        resolved_key = CREDENTIALS.resolve(api_key, provider, endpoint)
        return call_llm(provider, endpoint, model, resolved_key, system, build_user_message(source), float(temperature or 0.35), int(timeout or 90), int(max_tokens or 0), bool(send_temperature)), "LLM 提示词处理完成"
    except Exception as error:
        return "", f"处理失败：{_safe_error(error)}"


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
    return generated, system, status, generated


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
    with gr.Accordion("LLM 提示词工作室", open=False, elem_id=f"llm_prompt_studio_{slot}_inline"):
        gr.Markdown("生成结果会直接写入上方正向提示词。系统策略和模型规则优先于用户要求；RAG 与静态词库仅作为参考数据。")
        with gr.Row():
            with gr.Column(scale=3):
                request = gr.Textbox(label="创作要求", lines=3, placeholder="描述希望生成的画面")
                source_tags = gr.Textbox(label="源 Danbooru 标签（可选，优先使用）", lines=2)
                preset = gr.Dropdown(label="System Prompt 预设", choices=PRESET_UI_CHOICES, value="Danbooru Tags")
                system_override = gr.Textbox(label="自定义 System Prompt（可选）", lines=3, placeholder="留空则使用所选预设")
                user_instruction = gr.Textbox(label="用户输出要求（低优先级）", lines=2, placeholder="例如：最多 30 个标签，不使用权重")
            with gr.Column(scale=2):
                base_model = gr.Dropdown(label="目标底模", choices=MODEL_UI_CHOICES, value="Auto / checkpoint default")
                safety = gr.Radio(label="内容模式", choices=["SFW", "NSFW"], value="SFW")
                nsfw_injection = gr.Textbox(label="NSFW 策略注入", lines=2, placeholder="仅在 NSFW 模式下生效")
                structured_mode = gr.Radio(label="输出格式", choices=OUTPUT_UI_CHOICES, value="Plain Prompt")
                region_count = gr.Slider(label="区域数量", minimum=1, maximum=8, value=2, step=1)
        with gr.Accordion("LLM 连接设置", open=False):
            with gr.Row():
                provider = gr.Dropdown(label="Provider", choices=PROVIDER_UI_CHOICES, value=settings["provider"])
                endpoint = gr.Textbox(label="接口地址", value=settings["endpoint"])
                model = gr.Textbox(label="模型 ID", value=settings["model"])
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
                remove_bad = gr.Checkbox(label="移除不良标签", value=True)
                shuffle = gr.Checkbox(label="随机打乱标签", value=False)
                spaces = gr.Checkbox(label='将“_”转换为空格', value=False)
                max_tags = gr.Slider(label="最大标签数（0 表示不限）", minimum=0, maximum=200, value=0, step=1)
            remove_terms = gr.Textbox(label="额外排除标签 / 通配规则", placeholder="watermark, *_text")
            with gr.Row():
                few_shot_count = gr.Slider(label="Few-Shot 示例数", minimum=0, maximum=8, value=3, step=1)
                rag_min_score = gr.Slider(label="RAG 最低评分", minimum=0, maximum=10, value=7, step=0.5)
                save_score = gr.Slider(label="生成结果保存评分", minimum=0, maximum=10, value=0, step=0.5)
                cache_result = gr.Checkbox(label="缓存生成结果", value=True)
        generate = gr.Button("生成并写入正向提示词", variant="primary")
        output = gr.Textbox(label="生成的提示词", lines=5)
        system_preview = gr.Textbox(label="最终 System Prompt 与策略", lines=8)
        status = gr.Markdown()
        inputs = [request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, save_score, cache_result]
        generate.click(_inline_generate, inputs=inputs, outputs=[output, system_preview, status, prompt_target])
        provider.change(_load_provider_settings, inputs=provider, outputs=[endpoint, model, temperature, timeout, max_tokens, send_temperature, connection_status])
        test.click(_test_connection, inputs=[provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=connection_status)
        save_connection.click(_save_llm_settings, inputs=[provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=[connection_status, endpoint])
        clear_credentials.click(_clear_llm_credentials, inputs=[provider, endpoint], outputs=connection_status)


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
        "structured_mode": "Plain Prompt", "region_count": 1, "save_score": 0, "cache_result": False,
        "nsfw_injection": "", "user_instruction": "", "source_tags": "",
    }
    payload = dict(payload or {})
    allowed_fields = set(defaults) | {"request", "backend"}
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise ValueError(f"API request contains unsupported fields: {', '.join(unknown_fields)}")
    if "backend" in payload:
        if "provider" in payload and payload["provider"] != payload["backend"]:
            raise ValueError("API provider and legacy backend values conflict")
        payload["provider"] = payload.pop("backend")
    if payload.get("api_key"):
        raise ValueError("API Key cannot be supplied through the plugin API; save it in the Chinese UI")
    if "provider" in payload and payload["provider"] != saved_connection["provider"]:
        raise ValueError("API Provider must match the active connection saved in the plugin UI")
    if "endpoint" in payload and validate_endpoint(payload["endpoint"]) != validate_endpoint(saved_connection["endpoint"]):
        raise ValueError("API endpoint must match the connection saved in the plugin UI")
    values = {**defaults, **(payload or {})}
    generated, system, status = _generate(values.get("request", ""), *[values[key] for key in [
        "source_tags", "preset", "system_override", "base_model", "safety", "nsfw_injection", "user_instruction", "provider", "endpoint", "model", "api_key", "temperature", "timeout", "max_tokens", "send_temperature", "few_shot_count", "rag_min_score", "remove_bad", "remove_terms", "shuffle", "spaces", "max_tags", "structured_mode", "region_count", "save_score", "cache_result"
    ]])
    if not generated:
        raise ValueError(status)
    return {"prompt": generated, "system_prompt": system, "status": status}


def on_app_started(_, app):
    if DEFAULT_WILDCARDS.is_dir():
        try:
            files, terms = DB.index_wildcards(DEFAULT_WILDCARDS)
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
    except Exception as error:
        print(f"[LLM Prompt Studio] API registration failed: {error}")


def on_ui_tabs():
    llm_settings = _connection_settings()
    with gr.Blocks(analytics_enabled=False, elem_id="llm_prompt_studio") as ui:
        gr.Markdown("## LLM 提示词工作室\n本地静态词库、RAG Few-Shot、提示词缓存与 Forge 扩展集成。")
        with gr.Tabs():
            with gr.Tab("生成提示词"):
                with gr.Row():
                    with gr.Column(scale=3):
                        request = gr.Textbox(label="创作要求", lines=4, placeholder="描述希望生成的画面，或粘贴已有提示词")
                        source_tags = gr.Textbox(label="源 Danbooru 标签（可选，优先使用）", lines=3)
                        preset = gr.Dropdown(label="System Prompt 预设", choices=PRESET_UI_CHOICES, value="Danbooru Tags")
                        system_override = gr.Textbox(label="自定义 System Prompt（可选）", lines=6, placeholder="留空则使用所选预设。安全策略、用户要求、RAG 和静态词库会自动追加。")
                        base_model = gr.Dropdown(label="目标底模", choices=MODEL_UI_CHOICES, value="Auto / checkpoint default")
                        safety = gr.Radio(label="内容模式", choices=["SFW", "NSFW"], value="SFW")
                        nsfw_injection = gr.Textbox(label="NSFW System Prompt 注入", lines=2, placeholder="仅在 NSFW 模式下生效")
                        user_instruction = gr.Textbox(label="用户输出要求（低优先级）", lines=2, placeholder="例如：只返回最多 35 个标签，不使用权重")
                    with gr.Column(scale=2):
                        structured_mode = gr.Radio(label="输出格式", choices=OUTPUT_UI_CHOICES, value="Plain Prompt")
                        region_count = gr.Slider(label="区域数量", minimum=1, maximum=8, value=2, step=1)
                        with gr.Accordion("标签后处理", open=False):
                            remove_bad = gr.Checkbox(label="移除不良标签", value=True)
                            remove_terms = gr.Textbox(label="额外排除标签 / 通配规则", placeholder="watermark, *_text")
                            shuffle = gr.Checkbox(label="随机打乱标签", value=False)
                            spaces = gr.Checkbox(label='将“_”转换为空格', value=False)
                            max_tags = gr.Slider(label="最大标签数（0 表示不限）", minimum=0, maximum=200, value=0, step=1)
                        with gr.Accordion("RAG 与缓存", open=False):
                            few_shot_count = gr.Slider(label="Few-Shot 示例数", minimum=0, maximum=8, value=3, step=1)
                            rag_min_score = gr.Slider(label="RAG 最低缓存评分", minimum=0, maximum=10, value=7, step=0.5)
                            save_score = gr.Slider(label="保存生成结果的评分", minimum=0, maximum=10, value=0, step=0.5)
                            cache_result = gr.Checkbox(label="在本地缓存本次结果", value=True)
                generate = gr.Button("生成提示词", variant="primary")
                output = gr.Textbox(label="生成的提示词", lines=8)
                system_preview = gr.Textbox(label="最终 System Prompt", lines=12)
                status = gr.Markdown()
            with gr.Tab("LLM 连接"):
                provider = gr.Dropdown(label="Provider", choices=PROVIDER_UI_CHOICES, value=llm_settings["provider"])
                endpoint = gr.Textbox(label="接口地址", value=llm_settings["endpoint"])
                model = gr.Textbox(label="模型 ID", value=llm_settings["model"], placeholder="填写服务端暴露的模型名称")
                api_key = gr.Textbox(label="API Key（留空则使用已保存凭据）", type="password")
                temperature = gr.Slider(label="温度", minimum=0, maximum=2, value=llm_settings["temperature"], step=0.05)
                timeout = gr.Slider(label="超时秒数", minimum=5, maximum=600, value=llm_settings["timeout"], step=5)
                max_tokens = gr.Number(label="最大输出 Token（0 表示使用 Provider 默认值）", value=llm_settings["max_tokens"], precision=0)
                send_temperature = gr.Checkbox(label="发送温度参数（推理模型不支持时关闭）", value=llm_settings["send_temperature"])
                test = gr.Button("测试 API")
                with gr.Row():
                    save_connection = gr.Button("保存全部 LLM 设置")
                    clear_credentials = gr.Button("清除已保存的 API Key")
                test_status = gr.Markdown(_credential_status(llm_settings["provider"], llm_settings["endpoint"]))
            with gr.Tab("静态词库"):
                wildcard_path = gr.Textbox(label="静态词库目录", value=str(DEFAULT_WILDCARDS))
                index = gr.Button("建立 / 刷新本地索引")
                wildcard_status = gr.Markdown()
                wildcard_query = gr.Textbox(label="搜索已索引词条")
                wildcard_results = gr.Dropdown(label="匹配结果", choices=[], multiselect=True)
            with gr.Tab("WD14 + LLM"):
                image = gr.Image(label="待反推图片", type="numpy")
                wd_endpoint = gr.Textbox(label="Forge WD14 API 地址", value="http://127.0.0.1:7860")
                wd_model = gr.Textbox(label="WD14 模型", value="wd14-moat-v2")
                wd_threshold = gr.Slider(label="WD14 阈值", minimum=0, maximum=1, value=0.35, step=0.01)
                interrogate = gr.Button("调用已安装的 WD14 Tagger")
                wd_tags = gr.Textbox(label="WD14 标签", lines=5)
                wd_status = gr.Markdown()
                action = gr.Radio(label="LLM 操作", choices=ACTION_UI_CHOICES, value="Expand")
                transform = gr.Button("使用 LLM 扩写 / 润色")
                transform_output = gr.Textbox(label="LLM 处理结果", lines=8)
            with gr.Tab("缓存管理"):
                cache_query = gr.Textbox(label="搜索本地缓存")
                refresh = gr.Button("刷新缓存")
                cache_status = gr.Markdown()
                table = gr.Dataframe(label="已缓存提示词", headers=["序号", "内部 ID", "评分", "格式", "目标模型", "正向提示词", "负面提示词", "源标签"], datatype=["number", "number", "number", "str", "str", "str", "str", "str"], interactive=False)
                with gr.Accordion("按连续序号管理", open=False):
                    position_spec = gr.Textbox(label="序号或范围", placeholder="1-100,205,300-320")
                    with gr.Row():
                        preview_positions = gr.Button("预览这些序号")
                        delete_positions = gr.Button("删除这些序号")
                        undo_delete = gr.Button("撤销上次删除")
                with gr.Row():
                    record_id = gr.Textbox(label="记录 ID")
                    record_score = gr.Slider(label="评分", minimum=0, maximum=10, value=0, step=0.5)
                    record_tags = gr.Textbox(label="源标签")
                record_prompt = gr.Textbox(label="正向提示词", lines=4)
                record_negative = gr.Textbox(label="负面提示词", lines=2)
                with gr.Row():
                    load = gr.Button("载入记录")
                    save = gr.Button("保存记录", variant="primary")
                    delete_ids = gr.Textbox(label="要删除的 ID", placeholder="12, 17, 20")
                    delete = gr.Button("删除指定记录")
                with gr.Accordion("批量导入缓存", open=False):
                    bulk_import = gr.Textbox(label="每行一条提示词，可使用“评分<TAB>提示词”格式", lines=8)
                    bulk_import_button = gr.Button("批量导入本地缓存")
                with gr.Accordion("批量 LLM 生成并缓存", open=False):
                    batch_sources = gr.Textbox(label="每行一条创作要求或源标签", lines=10, placeholder="红发魔法师在月光图书馆阅读\n蓝发少女站在雨中的车站")
                    with gr.Row():
                        batch_skip_existing = gr.Checkbox(label="跳过已经缓存的相同输入", value=True)
                        batch_retries = gr.Slider(label="失败重试次数", minimum=0, maximum=3, value=2, step=1)
                        batch_score = gr.Slider(label="批量结果评分", minimum=0, maximum=10, value=7, step=0.5)
                    with gr.Row():
                        batch_generate = gr.Button("开始批量生成并缓存", variant="primary")
                        batch_cancel = gr.Button("取消批量任务")
                with gr.Accordion("JSON / CSV 导入导出", open=False):
                    with gr.Row():
                        import_file = gr.File(label="导入文件", file_types=[".json", ".csv"], type="filepath")
                        import_dedupe = gr.Checkbox(label="导入时跳过重复记录", value=True)
                    import_button = gr.Button("导入文件")
                    with gr.Row():
                        export_format = gr.Radio(label="导出格式", choices=["JSON", "CSV"], value="JSON")
                        export_button = gr.Button("导出全部缓存")
                    export_file = gr.File(label="导出文件", interactive=False)
        generate.click(_generate, inputs=[request, source_tags, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count, save_score, cache_result], outputs=[output, system_preview, status])
        provider.change(_load_provider_settings, inputs=provider, outputs=[endpoint, model, temperature, timeout, max_tokens, send_temperature, test_status])
        test.click(_test_connection, inputs=[provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=test_status)
        save_connection.click(_save_llm_settings, inputs=[provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=[test_status, endpoint])
        clear_credentials.click(_clear_llm_credentials, inputs=[provider, endpoint], outputs=test_status)
        index.click(_index_wildcards, inputs=wildcard_path, outputs=[wildcard_status, wildcard_results])
        wildcard_query.change(_search_wildcards, inputs=wildcard_query, outputs=wildcard_results)
        interrogate.click(_wd14_interrogate, inputs=[image, wd_endpoint, wd_model, wd_threshold], outputs=[wd_tags, wd_status])
        transform.click(_expand_or_polish, inputs=[wd_tags, action, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature], outputs=[transform_output, wd_status])
        refresh.click(_refresh_cache, inputs=cache_query, outputs=[table, cache_status])
        cache_query.submit(_refresh_cache, inputs=cache_query, outputs=[table, cache_status])
        load.click(_load_record, inputs=record_id, outputs=[record_prompt, record_negative, record_score, record_tags, cache_status])
        save.click(_save_record, inputs=[record_id, record_prompt, record_negative, preset, base_model, record_score, record_tags], outputs=[cache_status, table])
        delete.click(_delete_records, inputs=delete_ids, outputs=[cache_status, table])
        bulk_import_button.click(_bulk_cache, inputs=[bulk_import, preset, base_model, record_score], outputs=[cache_status, table])
        batch_generate.click(
            _batch_generate,
            inputs=[batch_sources, batch_skip_existing, batch_retries, batch_score, preset, system_override, base_model, safety, nsfw_injection, user_instruction, provider, endpoint, model, api_key, temperature, timeout, max_tokens, send_temperature, few_shot_count, rag_min_score, remove_bad, remove_terms, shuffle, spaces, max_tags, structured_mode, region_count],
            outputs=[cache_status, table],
        )
        batch_cancel.click(_cancel_batch_generation, outputs=cache_status)
        preview_positions.click(_preview_positions, inputs=position_spec, outputs=[cache_status, table])
        delete_positions.click(_delete_positions, inputs=position_spec, outputs=[cache_status, table])
        undo_delete.click(_undo_last_delete, outputs=[cache_status, table])
        import_button.click(_import_cache, inputs=[import_file, import_dedupe], outputs=[cache_status, table])
        export_button.click(_export_cache, inputs=export_format, outputs=[cache_status, export_file])
    return [(ui, "LLM 提示词工作室", "llm_prompt_studio")]
