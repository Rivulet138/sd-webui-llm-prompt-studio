"""Pure Native Cache helpers shared by the UI and Forge bridge."""

import re


def validate_slot(slot):
    if slot not in {"txt2img", "img2img"}:
        raise ValueError("无效的 Forge 生图面板")
    return slot


def merge_prompt(base_prompt, cached_prompt, write_mode, marker):
    base = str(base_prompt or "").strip()
    cached = str(cached_prompt or "").strip()
    mode = str(write_mode or "append_end").strip()
    if mode not in {"replace", "marker", "append_start", "append_end"}:
        raise ValueError("不支持的缓存 Prompt 写入方式")
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


def extract_claim(processing, marker_start, marker_end, marker_zero, marker_one):
    prompt = getattr(processing, "prompt", None)
    if not isinstance(prompt, str):
        return None
    end = prompt.rfind(marker_end)
    if end < 0 or prompt[end + len(marker_end):].strip(" \t\r\n,，"):
        return None
    encoded_end = end
    encoded_start = encoded_end - 128
    start = encoded_start - len(marker_start)
    if start < 0 or prompt[start:encoded_start] != marker_start:
        return None
    original = prompt[:start]
    encoded = prompt[encoded_start:encoded_end]
    if len(encoded) != 128 or any(char not in {marker_zero, marker_one} for char in encoded):
        return None
    bits = "".join("1" if char == marker_one else "0" for char in encoded)
    token = "".join(f"{int(bits[index:index + 4], 2):x}" for index in range(0, 128, 4))
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        return None
    return token, original
