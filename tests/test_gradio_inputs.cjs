"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../javascript/llm_prompt_studio_gradio_inputs.js"), "utf8");
function setup(slot = "t2i", active = false) {
    const components = [
        { id: 1, props: { elem_id: "RP_active" + slot } },
        { id: 2, props: { elem_id: "polymask" + slot } },
        { id: 3, props: { elem_id: "unrelated_editor" } },
    ];
    const window = { gradio_config: { components } };
    vm.runInNewContext(source, { window });
    const calls = [];
    const values = new Map([[0, "prompt"], [1, active], [2, { composite: "fresh mask" }], [3, { composite: "other mask" }]]);
    const getData = id => { calls.push(id); return values.get(id); };
    const dependency = { js: slot === "t2i" ? "submit" : "submit_img2img", backend_fn: true, inputs: [0, 1, 2, 3] };
    return { window, calls, values, getData, dependency, collect: () => window.llmPromptStudioGradioInputs(dependency, getData) };
}
test("inactive Regional Prompter skips only its mask on native generation", async () => {
    for (const slot of ["t2i", "i2i"]) {
        const h = setup(slot);
        const result = await Promise.all(h.collect());
        assert.deepEqual(Array.from(result), ["prompt", false, null, { composite: "other mask" }]);
        assert.equal(h.calls.includes(2), false);
        assert.equal(h.calls.filter(id => id === 1).length, 1);
    }
});
test("enabling, editing and disabling RP between infinite rounds uses current inputs", async () => {
    const h = setup();
    assert.equal((await Promise.all(h.collect()))[2], null);
    h.values.set(1, true);
    const mask = { composite: "newly edited mask" };
    h.values.set(2, mask);
    assert.equal((await Promise.all(h.collect()))[2], mask);
    h.values.set(2, { composite: "second edit" });
    assert.equal((await Promise.all(h.collect()))[2].composite, "second edit");
    h.values.set(1, false);
    h.calls.length = 0;
    assert.equal((await Promise.all(h.collect()))[2], null);
    assert.equal(h.calls.includes(2), false);
});
test("mask tools and presets always read the editor even while RP is inactive", async () => {
    const h = setup();
    for (const js of [null, "", "draw_region", "savepresets"]) {
        h.dependency.js = js;
        h.calls.length = 0;
        assert.equal((await Promise.all(h.collect()))[2].composite, "fresh mask");
        assert.ok(h.calls.includes(2));
    }
});
test("missing, incomplete or asynchronous activation information preserves native reads", async () => {
    for (const kind of ["config", "component", "input", "unknown", "async", "frontend"]) {
        const h = setup();
        if (kind === "config") delete h.window.gradio_config;
        if (kind === "component") h.window.gradio_config.components = h.window.gradio_config.components.filter(c => c.id !== 1);
        if (kind === "input") h.dependency.inputs = [0, 2, 3];
        if (kind === "unknown") h.values.set(1, undefined);
        if (kind === "async") h.values.set(1, Promise.resolve(false));
        if (kind === "frontend") h.dependency.backend_fn = false;
        const result = await Promise.all(h.collect());
        assert.ok(result.some(v => v?.composite === "fresh mask"), kind);
        assert.ok(h.calls.includes(2), kind);
    }
});
test("editor collection errors propagate instead of silently dropping enabled masks", () => {
    const h = setup("t2i", true);
    assert.throws(() => h.window.llmPromptStudioGradioInputs(h.dependency, id => {
        if (id === 2) throw new Error("editor failed");
        return h.values.get(id);
    }), /editor failed/);
});
