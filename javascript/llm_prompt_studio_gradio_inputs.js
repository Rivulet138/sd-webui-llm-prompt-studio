(function () {
    "use strict";

    // Called by the optional Gradio input-collection compatibility patch.
    // Do not alter editors used by tools, or any enabled RP generation.
    window.llmPromptStudioGradioInputs = function (dependency, getData) {
        const nativeInputs = () => dependency.inputs.map(id => getData(id));
        const slot = dependency.js === "submit" ? "t2i"
            : dependency.js === "submit_img2img" ? "i2i" : "";
        const components = window.gradio_config?.components;
        if (!slot || !dependency.backend_fn || !Array.isArray(components)) return nativeInputs();
        const active = components.find(c => c.props?.elem_id === "RP_active" + slot);
        const mask = components.find(c => c.props?.elem_id === "polymask" + slot);
        if (!active || !mask || !dependency.inputs.includes(active.id)
            || !dependency.inputs.includes(mask.id)) return nativeInputs();
        const activeValue = getData(active.id);
        if (activeValue !== false) {
            return dependency.inputs.map(id => id === active.id ? activeValue : getData(id));
        }
        // RP ignores the mask when inactive. Keep the editor and its displayed
        // data intact; the next enabled generation reads its current value.
        return dependency.inputs.map(id => id === mask.id ? null
            : id === active.id ? activeValue : getData(id));
    };
})();
