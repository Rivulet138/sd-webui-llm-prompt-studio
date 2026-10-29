"""Forge native processing bridge for one-cache-per-image generation."""

from modules import scripts, shared

from prompt_studio_ui import (
    apply_native_cache_context,
    prepare_native_cache_processing,
    track_native_cache_image,
    complete_native_cache_context,
    _native_cache_fail,
)


class Script(scripts.Script):
    """Apply a prepared Studio cache batch inside Forge's processing object."""

    sorting_priority = 12_000
    create_group = False

    def title(self):
        return "LLM Prompt Studio native cache injection"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        # The visible controls live in the Studio panel. The native script is
        # intentionally UI-less so Forge does not expose a duplicate panel.
        return []

    def before_process(self, p, *args):
        prepare_native_cache_processing(p)

    def process(self, p, *args):
        try:
            token = apply_native_cache_context(p)
            if getattr(p, "_llm_prompt_studio_native_cache_claim", None) and not token:
                raise ValueError("缓存注入任务无效，已停止本次生图，请重新生成")
        except Exception:
            # Forge logs and swallows script errors, so explicitly stop this job.
            shared.state.interrupt()
            _native_cache_fail(p)
            raise

    def postprocess_image(self, p, pp, *args):
        track_native_cache_image(p, pp)

    def before_process_batch(self, p, *args, **kwargs):
        # Comments may run before or after our process hook. Restore only the
        # positive raw infotext after all process hooks have finished.
        raw = getattr(p, "_llm_prompt_studio_native_cache_raw_prompts", None)
        if raw is not None:
            p._all_prompts_c = list(raw)

    def postprocess(self, p, processed, *args):
        complete_native_cache_context(p, processed)
