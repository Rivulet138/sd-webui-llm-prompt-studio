"""Regressions for live model selections and Forge queue parameter delivery."""
import threading
import unittest
from unittest.mock import Mock

import test_model_preset_selection as model_tests
import test_queue_regressions as queue_tests


class LiveModelSelectionTests(unittest.TestCase):
    def api(self):
        base = model_tests.ModelPresetSelectionTests().ui(
            {"preset": "Pony / Illustrious Tags", "base_model": "Pony / Illustrious"})
        inline = Mock(return_value=("prompt", "system", "ok"))
        auto = Mock(return_value=("prompt", "system", "ok"))
        profiles = Mock(return_value=("supplement", "supplement"))
        api = queue_tests.ui_functions(
            "_api_inline_generate", "_api_auto_loop_generate",
            _canonical_preset=base._canonical_preset,
            _canonical_base_model=base._canonical_base_model,
            _resolve_preset_model=base._resolve_preset_model,
            _workflow_settings=base._workflow_settings,
            _connection_settings=lambda: dict(provider="fake", endpoint="local", model="fake",
                temperature=0, timeout=90, max_tokens=1000, send_temperature=False),
            PRESETS=model_tests.core.PRESETS,
            MODEL_PRESET_GUIDANCE=model_tests.core.MODEL_PRESET_GUIDANCE,
            _INLINE_CANCEL_EVENTS={"txt2img": threading.Event()},
            _INLINE_REQUEST_LOCKS={"txt2img": threading.Lock()},
            _INLINE_REQUEST_CONTROL_LOCK=threading.Lock(),
            _INLINE_ACTIVE_REQUEST_IDS={},
            _inline_request_event=lambda *args: threading.Event(),
            _release_inline_request=Mock(), _load_system_prompt_profile=profiles,
            _inline_generate=inline, _generate_auto_loop=auto,
            _coerce_bool=lambda value, default: default if value is None else bool(value),
        )
        return api, inline, auto, profiles, base.MODEL_PRESET_SELECTIONS

    def test_current_combined_selector_overrides_stale_legacy_values(self):
        api, inline, auto, profiles, selections = self.api()
        for selection, expected in selections.items():
            payload = dict(request="one cat", model_preset=selection,
                           preset="Danbooru Tags", base_model="Pony / Illustrious")
            with self.subTest(selection=selection, entry="inline"):
                api._api_inline_generate({**payload, "slot": "txt2img"})
                self.assertEqual((inline.call_args.args[2], inline.call_args.args[4]), expected)
                profiles.assert_called_with(*expected)
            with self.subTest(selection=selection, entry="auto"):
                api._api_auto_loop_generate(payload)
                self.assertEqual((auto.call_args.args[1], auto.call_args.args[3]), expected)
                profiles.assert_called_with(*expected)

    def test_localized_current_choice_and_saved_legacy_choice(self):
        api, inline, auto, _, _ = self.api()
        for callback, mock, extra, indexes in (
            (api._api_inline_generate, inline, {"slot": "txt2img"}, (2, 4)),
            (api._api_auto_loop_generate, auto, {}, (1, 3)),
        ):
            for payload, expected in (
                ({"model_preset": "自然语言"}, ("Natural Language", "")),
                ({"model_preset": "Danbooru + 自然语言"}, ("Danbooru + Natural", "")),
                ({"preset": "Anima Tags", "base_model": "Anima"}, ("Anima Tags", "Anima")),
                ({}, ("Pony / Illustrious Tags", "Pony / Illustrious")),
            ):
                with self.subTest(payload=payload, callback=callback.__name__):
                    callback({**payload, **extra})
                    self.assertEqual(tuple(mock.call_args.args[index] for index in indexes), expected)

    def test_invalid_combined_selection_is_reported(self):
        api, _, _, _, _ = self.api()
        with self.assertRaisesRegex(ValueError, "Unsupported prompt preset"):
            api._api_inline_generate({"slot": "txt2img", "model_preset": "invalid"})
        with self.assertRaisesRegex(ValueError, "Unsupported prompt preset"):
            api._api_auto_loop_generate({"model_preset": "invalid"})


class ForgeParameterDeliveryTests(unittest.TestCase):
    def setUp(self):
        queue_tests.RenderCancellationTests.setUp(self)

    def test_scheduler_styles_and_independent_hires_settings_reach_forge(self):
        settings = dict(
            scheduler="Karras", styles=["Illustration", "Warm"], enable_hr=True,
            hr_checkpoint_name="second.safetensors", hr_additional_modules=["vae.safetensors"],
            hr_sampler_name="DPM++ 2M", hr_scheduler="Exponential",
            hr_prompt="detailed cat", hr_negative_prompt="blur", hr_second_pass_steps=12,
        )
        self.api._server_render_prompt("cat", "txt2img", "job", {"generation_settings": settings})
        received = self.processing.StableDiffusionProcessingTxt2Img.call_args.kwargs
        for key, value in settings.items():
            with self.subTest(parameter=key):
                self.assertEqual(received[key], value)

    def test_same_as_first_pass_choices_use_host_sentinels(self):
        settings = dict(hr_checkpoint_name="Use same checkpoint",
                        hr_sampler_name="Use same sampler", hr_scheduler="Use same scheduler",
                        hr_additional_modules=["Use same choices"])
        self.api._server_render_prompt("cat", "txt2img", "job", {"generation_settings": settings})
        received = self.processing.StableDiffusionProcessingTxt2Img.call_args.kwargs
        for key in ("hr_checkpoint_name", "hr_sampler_name", "hr_scheduler"):
            self.assertIsNone(received[key])
        self.assertEqual(received["hr_additional_modules"], ["Use same choices"])

    def test_global_sample_directory_takes_effect(self):
        self.shared.opts.outdir_samples = "global-samples"
        self.shared.opts.outdir_txt2img_samples = "txt2img-samples"
        self.shared.opts.outdir_grids = "global-grids"
        self.api._server_render_prompt("cat", "txt2img", "job")
        received = self.processing.StableDiffusionProcessingTxt2Img.call_args.kwargs
        self.assertEqual(received["outpath_samples"], "global-samples")
        self.assertEqual(received["outpath_grids"], "global-grids")


if __name__ == "__main__":
    unittest.main()
