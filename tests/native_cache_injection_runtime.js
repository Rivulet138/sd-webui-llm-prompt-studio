"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../javascript/llm_prompt_studio_auto_loop.js"), "utf8");

function harness({batchSize = 1, batchCount = 1, asyncTaskId = false} = {}) {
    let now = 0;
    let timerId = 0;
    const timers = new Map();
    const storage = new Map();
    const nodes = new Map();
    const requests = [];
    const submissions = [];

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
        addEventListener(type, callback, _capture = false) {
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
            const taskId = `task-${submissions.length}`;
            if (asyncTaskId) window.setTimeout(() => storage.set(`${slot}_task_id`, taskId), 25);
            else storage.set(`${slot}_task_id`, taskId);
            generate.disabled = true;
            interrupt.style.display = "block";
        };
        interrupt.onClick = () => finish(slot);
        const size = new Element(String(batchSize));
        size.type = "number";
        add(`${slot}_batch_size`, size);
        const count = new Element(String(batchCount));
        count.type = "number";
        add(`${slot}_batch_count`, count);
    }
    const enabled = add("llm_prompt_studio_txt2img_inline_cache_enabled");
    enabled.child = new Element();
    enabled.child.type = "checkbox";
    const sourceChoice = add("llm_prompt_studio_txt2img_inline_source");
    sourceChoice.child = new Element();
    sourceChoice.child.type = "radio";
    sourceChoice.child.value = "cache";
    sourceChoice.child.checked = true;
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
        fetch(url, options = {}) {
            return new Promise((resolve) => requests.push({
                url,
                body: options.body ? JSON.parse(options.body) : null,
                resolve(data, ok = true) {
                    resolve({ ok, status: ok ? 200 : 400, json: async () => data });
                },
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
        for (let index = 0; index < 80; index += 1) await Promise.resolve();
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
    return { nodes, requests, submissions, storage, finish, advance };
}

function prepareRequest(harnessState, token, nextCursor, count) {
    const request = harnessState.requests.filter((item) => item.url.endsWith("/native-cache/prepare")).at(-1);
    assert.ok(request, "native cache preparation request was not sent");
    request.resolve({ token, next_cursor: nextCursor, count, records: [] });
    return request;
}

function releaseRequest(harnessState, commit = true) {
    const request = harnessState.requests.filter((item) => item.url.endsWith("/native-cache/release")).at(-1);
    assert.ok(request, "native cache release request was not sent");
    request.resolve({ released: true, committed: commit, next_cursor: 0 });
}

test("unchecked native Generate remains Forge-only", () => {
    const h = harness();
    h.nodes.get("txt2img_generate").click();
    assert.equal(h.submissions.length, 1);
    assert.equal(h.requests.length, 0);
});

test("a Forge batch prepares one cache record per image without changing the Prompt textbox", async () => {
    const h = harness({batchSize: 2, batchCount: 2});
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].body.total_images, 4);
    assert.equal(h.requests[0].body.allow_wrap, true);
    assert.equal(h.requests[0].body.base_prompt, "fixed subject");
    prepareRequest(h, "batch-1", 4, 4);
    await h.advance();
    assert.deepEqual(h.submissions, [{ slot: "txt2img", prompt: "fixed subject" }]);
    h.finish();
    await h.advance(250);
    releaseRequest(h, true);
    await h.advance();
    assert.equal(h.nodes.get("txt2img_prompt").child.value, "fixed subject");
});

test("delayed synthetic Prompt events never turn cache A into the next base Prompt", async () => {
    const h = harness();
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    assert.equal(h.requests[0].body.base_prompt, "fixed subject");
    prepareRequest(h, "batch-1", 1, 1);
    await h.advance();
    h.finish();
    await h.advance(250);
    releaseRequest(h, true);
    await h.advance();
    const prompt = h.nodes.get("txt2img_prompt").child;
    prompt.value = "fixed subject, cache A";
    prompt.dispatchEvent({ type: "input", isTrusted: false });
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    const prepares = h.requests.filter((item) => item.url.endsWith("/native-cache/prepare"));
    assert.equal(prepares.length, 2);
    assert.equal(prepares[1].body.base_prompt, "fixed subject");
});

test("failed native generation releases the context without advancing the cursor", async () => {
    const h = harness();
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    prepareRequest(h, "batch-1", 7, 1);
    await h.advance();
    h.finish("txt2img", "error: gpu failed", false);
    await h.advance(250);
    releaseRequest(h, false);
    await h.advance();
    assert.equal(h.nodes.get("txt2img_generate").disabled, false);
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    const prepares = h.requests.filter((item) => item.url.endsWith("/native-cache/prepare"));
    assert.equal(prepares.length, 2);
    assert.equal(prepares[1].body.after_id, 0);
});

test("processed cache uses the same native batch contract", async () => {
    const h = harness();
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;
    h.nodes.get("llm_prompt_studio_txt2img_inline_source").child.value = "processed_cache";
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    const request = h.requests.find((item) => item.url.endsWith("/native-cache/prepare"));
    assert.equal(request.body.source, "processed_cache");
});

test("Generate forever clicks continue across repeated Forge rounds", async () => {
    const h = harness();
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;

    for (let round = 1; round <= 4; round += 1) {
        h.nodes.get("txt2img_generate").click();
        await h.advance();
        const prepares = h.requests.filter((item) => item.url.endsWith("/native-cache/prepare"));
        assert.equal(prepares.length, round, `round ${round} should prepare one cache context`);
        prepareRequest(h, `batch-${round}`, round, 1);
        await h.advance();
        assert.equal(h.submissions.length, round, `round ${round} should reach Forge`);

        // Forge's Generate forever menu can issue more than one click while
        // the previous task is switching its interrupt/generate controls.
        h.nodes.get("txt2img_generate").click();
        h.nodes.get("txt2img_generate").click();
        h.finish();
        await h.advance(250);
        releaseRequest(h, true);
        await h.advance();
    }

    assert.equal(h.submissions.length, 4);
    assert.equal(h.requests.filter((item) => item.url.endsWith("/native-cache/release")).length, 4);
});

test("a native click captured during an unrelated Forge task is retried after idle", async () => {
    const h = harness();
    // Start a normal Forge task before enabling cache injection.
    h.nodes.get("txt2img_generate").click();
    assert.equal(h.submissions.length, 1);
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;

    // This is the transition window where the old code discarded the click.
    h.nodes.get("txt2img_generate").click();
    await h.advance(100);
    assert.equal(h.requests.length, 0);

    h.finish();
    await h.advance(100);
    assert.equal(h.requests.filter((item) => item.url.endsWith("/native-cache/prepare")).length, 1);
    prepareRequest(h, "retry-batch", 1, 1);
    await h.advance();
    assert.equal(h.submissions.length, 2);
});

test("native rounds wait for a fresh asynchronous Forge task id", async () => {
    const h = harness({asyncTaskId: true});
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;

    for (let round = 1; round <= 3; round += 1) {
        h.nodes.get("txt2img_generate").click();
        await h.advance();
        prepareRequest(h, `async-batch-${round}`, round, 1);
        await h.advance(25);
        assert.equal(h.submissions.length, round);
        h.finish();
        await h.advance(250);
        releaseRequest(h, true);
        await h.advance();
        if (round < 3) {
            // Keep the previous ID around for the next click. Forge replaces
            // it asynchronously when the next Gradio submit callback runs.
            h.storage.set("txt2img_task_id", `task-${round}`);
        }
    }

    assert.equal(h.submissions.length, 3);
    assert.equal(h.requests.filter((item) => item.url.endsWith("/native-cache/prepare")).length, 3);
});

test("Generate forever does not deadlock when its menu script marks interrupt busy after an intercepted click", async () => {
    const h = harness();
    const generate = h.nodes.get("txt2img_generate");
    const interrupt = h.nodes.get("txt2img_interrupt");
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;

    // First round starts through the plugin and completes normally.
    generate.click();
    await h.advance();
    prepareRequest(h, "batch-1", 1, 1);
    await h.advance();
    h.finish();
    await h.advance(250);
    releaseRequest(h, true);
    await h.advance();

    // Forge's contextMenus.js Generate forever callback does this immediately
    // after generate.click(). The plugin intercepts the click, so no Forge
    // callback resets interrupt.style.display back to "none".
    generate.click();
    interrupt.style.display = "block";
    await h.advance(500);

    const prepares = h.requests.filter((item) => item.url.endsWith("/native-cache/prepare"));
    assert.equal(prepares.length, 2, "the intercepted second click must still prepare the next cache record");
});

test("Generate forever continues through repeated idle transitions without dropping a round", async () => {
    const h = harness();
    const generate = h.nodes.get("txt2img_generate");
    const interrupt = h.nodes.get("txt2img_interrupt");
    h.nodes.get("llm_prompt_studio_txt2img_inline_cache_enabled").child.checked = true;

    // This models contextMenus.js: every time Forge exposes Generate again,
    // its forever loop clicks Generate and immediately marks the interrupt
    // control busy. The click can therefore arrive while the plugin is still
    // releasing the prior native-cache context.
    const foreverTick = () => {
        if (interrupt.style.display === "none") {
            generate.click();
            interrupt.style.display = "block";
        }
    };

    for (let round = 1; round <= 6; round += 1) {
        foreverTick();
        await h.advance();
        const prepares = h.requests.filter((item) => item.url.endsWith("/native-cache/prepare"));
        assert.equal(prepares.length, round, `round ${round} should prepare exactly one context`);
        prepareRequest(h, `forever-${round}`, round, 1);
        await h.advance();
        assert.equal(h.submissions.length, round, `round ${round} should submit exactly one Forge task`);

        // Forge becomes idle before the plugin's release request resolves;
        // the next forever tick must be retained and replayed afterwards.
        h.finish();
        foreverTick();
        await h.advance(250);
        releaseRequest(h, true);
        await h.advance();
    }

    assert.equal(h.submissions.length, 6);
    assert.equal(h.requests.filter((item) => item.url.endsWith("/native-cache/release")).length, 6);
});
