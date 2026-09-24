"""Forge native processing bridge for one-cache-per-image generation."""

from modules import scripts

from prompt_studio_ui import apply_native_cache_context


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

    def process(self, p, *args):
        # Forge calls process after setup_prompts, so this list is the final
        # per-image prompt input and cannot be overwritten by initialization.
        apply_native_cache_context(p)
