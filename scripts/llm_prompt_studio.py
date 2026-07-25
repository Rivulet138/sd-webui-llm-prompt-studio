import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from prompt_studio_ui import capture_prompt_component, inject_inline_before_negative, on_app_started, on_ui_tabs
from modules import script_callbacks
from modules import scripts
import gradio as gr
from prompt_studio_core import process_tags

script_callbacks.on_ui_tabs(on_ui_tabs)
script_callbacks.on_app_started(on_app_started)
script_callbacks.on_after_component(capture_prompt_component)
script_callbacks.on_before_component(inject_inline_before_negative)


class Script(scripts.Script):
    """Native generation hook for the Ranbooru-style batch prompt workflow."""

    def title(self):
        return "LLM 提示词工作室：批量标签处理"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        enabled = gr.Checkbox(label="启用 LLM 提示词工作室标签处理", value=False)
        remove_bad = gr.Checkbox(label="移除不良标签", value=True)
        remove_terms = gr.Textbox(label="额外排除标签 / 通配规则", placeholder="watermark, *_text")
        shuffle = gr.Checkbox(label="随机打乱标签", value=False)
        spaces = gr.Checkbox(label='将“_”转换为空格', value=False)
        max_tags = gr.Slider(label="最大标签数（0 表示不限）", minimum=0, maximum=200, value=0, step=1)
        same_prompt = gr.Checkbox(label="本批次所有图片使用同一条处理后提示词", value=False)
        return [enabled, remove_bad, remove_terms, shuffle, spaces, max_tags, same_prompt]

    def process(self, p, enabled, remove_bad, remove_terms, shuffle, spaces, max_tags, same_prompt):
        if not enabled:
            return
        cleaned = process_tags(p.prompt, remove_bad, remove_terms, shuffle, spaces, int(max_tags or 0))
        p.prompt = cleaned
        if not hasattr(p, "all_prompts"):
            return
        if same_prompt:
            p.all_prompts = [cleaned] * len(p.all_prompts)
        else:
            p.all_prompts = [process_tags(value, remove_bad, remove_terms, shuffle, spaces, int(max_tags or 0)) for value in p.all_prompts]
