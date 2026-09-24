"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../javascript/llm_prompt_studio_auto_loop.js"), "utf8");

function harness() {
    let now = 0;
    let timerId = 0;
    const timers = new Map();
    const storage = new Map();
    const nodes = new Map();
    const requests = [];
    const submissions = [];
    const interrupts = [];

    class Element {
        constructor(value = "") {
            this.value = value;
            this.checked = false;
            this.type = "text";
            this.button = false;
            this.child = null;
            this.dataset = {};
            this.listeners = new Map();
            this.style = { display: "block" };
            this.disabled = false;
            this.innerHTML = "";
            this.textContent = "";
        }
        matches(selector) { return selector === "button" && this.button; }
        querySelector(selector) {
            if (selector.includes(":checked")) return this.child?.checked ? this.child : null;
            if (selector.includes("checkbox")) return this.child?.type === "checkbox" ? this.child : null;
            return this.child;
        }
        addEventListener(type, callback) {
            const callbacks = this.listeners.get(type) || [];
            callbacks.push(callback);
            this.listeners.set(type, callbacks);
        }
        dispatchEvent(event) {
            for (const callback of this.listeners.get(event.type) || []) callback(event);
        }
        click() {
            const event = {
                type: "click",
                prevented: false,
                preventDefault() { this.prevented = true; },
                stopImmediatePropagation() { this.immediateStopped = true; },
            };
            this.dispatchEvent(event);
            if (!event.prevented) this.onClick?.();
        }
    }

    function add(id, child = null) {
        const node = new Element();
        node.id = id;
        node.child = child;
        nodes.set(id, node);
        return node;
    }

    for (const id of [
        "llm_prompt_studio_txt2img_inline_loop_status", "llm_prompt_studio_img2img_inline_loop_status",
        "html_log_txt2img", "html_log_img2img", "txt2img_status", "img2img_status",
        "txt2img_gallery", "img2img_gallery", "txt2img_interrupt", "img2img_interrupt",
    ]) add(id);
    for (const slot of ["txt2img", "img2img"]) {
        const prompt = new Element("fixed subject");
        prompt.type = "textarea";
        add(`${slot}_prompt`, prompt);
        const generate = add(`${slot}_generate`);
        generate.button = true;
        const interrupt = nodes.get(`${slot}_interrupt`);
        interrupt.button = true;
        interrupt.style.display = "none";
        generate.onClick = () => {
            submissions.push({ slot, prompt: prompt.value });
            storage.set(`${slot}_task_id`, `task-${submissions.length}`);
            generate.disabled = true;
            interrupt.style.display = "block";
        };
        interrupt.onClick = () => {
            interrupts.push(slot);
            finish(slot);
        };
    }
    const enabled = add("llm_prompt_studio_txt2img_inline_cache_enabled");
    enabled.child = new Element();
    enabled.child.type = "checkbox";
    const sourceHost = add("llm_prompt_studio_txt2img_inline_source");
    sourceHost.child = new Element();
    sourceHost.child.type = "radio";
    sourceHost.child.value = "cache";
    sourceHost.child.checked = true;
    const cursor = new Element();
    cursor.type = "number";
    add("llm_prompt_studio_txt2img_inline_cache_cursor", cursor);
    const merge = new Element();
    merge.child = new Element("append_end");
    add("llm_prompt_studio_txt2img_inline_write_mode", merge);
    const marker = new Element("{{LLM}}");
    add("llm_prompt_studio_txt2img_inline_marker", marker);

    function finish(slot = "txt2img", log = "", output = true) {
        storage.delete(`${slot}_task_id`);
        nodes.get(`${slot}_generate`).disabled = false;
        nodes.get(`${slot}_interrupt`).style.display = "none";
        if (log) nodes.get(`html_log_${slot}`).textContent = log;
        if (output) nodes.get(`${slot}_gallery`).innerHTML = `<img data-round="${submissions.length}">`;
    }
    nodes.get("txt2img_gallery").innerHTML = "<div></div>";
    nodes.get("img2img_gallery").innerHTML = "<div></div>";

    const root = {
        querySelector(selector) { return nodes.get(selector.replace(/^#/, "")) || null; },
        querySelectorAll() { return []; },
    };
    const window = {
        AbortController,
        addEventListener() {},
        performance: { now: () => now },
        getComputedStyle: (element) => element.style,
        opts: { keep_alive: true },
        localStorage: {
            getItem: (key) => storage.get(key) ?? null,
            setItem: (key, value) => storage.set(key, String(value)),
            removeItem: (key) => storage.delete(key),
        },
        setTimeout(callback, delay = 0) {
            const id = ++timerId;
            timers.set(id, { callback, time: now + delay });
            return id;
        },
        clearTimeout: (id) => timers.delete(id),
        fetch(url) {
            return new Promise((resolve) => requests.push({
                url,
                resolve(records) { resolve({ ok: true, status: 200, json: async () => ({ records }) }); },
            }));
        },
    };
    const context = vm.createContext({
        window,
        document: { ...root, hidden: false },
        gradioApp: () => root,
        HTMLTextAreaElement: Element,
        HTMLInputElement: Element,
        Event,
        console,
        Date,
        Set,
        Map,
    });
    vm.runInContext(source, context);

    async function flush() {
        for (let index = 0; index < 60; index += 1) await Promise.resolve();
    }
    async function advance(milliseconds = 0) {
        await flush();
        const end = now + milliseconds;
        for (;;) {
            const entry = [...timers.entries()].filter(([, timer]) => timer.time <= end)
                .sort((a, b) => a[1].time - b[1].time)[0];
            if (!entry) { now = end; await flush(); return; }
            const [id, timer] = entry;
            timers.delete(id);
            now = timer.time;
            timer.callback();
            await flush();
        }
    }
    return { api: window.llmPromptStudioAutoLoop, nodes, requests, submissions, interrupts, storage, finish, advance };
}

test("unchecked native Generate remains Forge-only", () => {
    const h = harness();
    h.nodes.get("txt2img_generate").click();
    assert.equal(h.submissions.length, 1);
    assert.equal(h.requests.length, 0);
});

test("each native Generate consumes one cache record and restores the fixed base", async () => {
    const h = harness();
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;
    assert.equal(h.nodes.get("txt2img_generate").listeners.get("click")?.length, 1);
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    assert.equal(h.requests.length, 1);
    h.requests[0].resolve([{ id: 1, prompt: "cache A" }]);
    await h.advance();
    assert.deepEqual(h.submissions, [{ slot: "txt2img", prompt: "fixed subject, cache A" }]);
    h.finish();
    await h.advance(250);
    assert.equal(h.nodes.get("txt2img_prompt").child.value, "fixed subject");

    h.nodes.get("txt2img_generate").click();
    await h.advance();
    assert.equal(h.requests.length, 2);
    h.requests[1].resolve([{ id: 2, prompt: "cache B" }]);
    await h.advance();
    assert.deepEqual(h.submissions[1], { slot: "txt2img", prompt: "fixed subject, cache B" });
    assert.equal(h.submissions.some((item) => item.prompt.includes("cache A, cache B")), false);
});

test("failed native generation leaves the same cache record for retry", async () => {
    const h = harness();
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    h.requests[0].resolve([{ id: 7, prompt: "retry me" }]);
    await h.advance();
    h.finish("txt2img", "error: gpu failed", false);
    await h.advance(250);
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    assert.equal(h.requests.length, 2);
    h.requests[1].resolve([{ id: 7, prompt: "retry me" }]);
    await h.advance();
    assert.equal(h.submissions[1].prompt, "fixed subject, retry me");
});

test("a native click arriving at Forge completion is replayed after restoration", async () => {
    const h = harness();
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    h.requests[0].resolve([{ id: 1, prompt: "cache A" }]);
    await h.advance();
    assert.equal(h.submissions.length, 1);
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    assert.equal(h.requests.length, 1);
    h.finish();
    await h.advance(250);
    assert.equal(h.requests.length, 2);
    h.requests[1].resolve([{ id: 2, prompt: "cache B" }]);
    await h.advance();
    assert.deepEqual(h.submissions[1], { slot: "txt2img", prompt: "fixed subject, cache B" });
});

test("the processed cache source uses the same native click contract", async () => {
    const h = harness();
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;
    h.nodes.get("llm_prompt_studio_txt2img_inline_source").child.value = "processed_cache";
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    assert.match(h.requests[0].url, /processed-cache/);
    h.requests[0].resolve([{ id: 3, prompt: "processed C" }]);
    await h.advance();
    assert.deepEqual(h.submissions[0], { slot: "txt2img", prompt: "fixed subject, processed C" });
});
