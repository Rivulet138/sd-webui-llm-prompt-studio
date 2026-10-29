"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../javascript/llm_prompt_studio_auto_loop.js"), "utf8");

// Exercise the browser code in a VM with native-cache controls and transport.
function harness(options = {}) {
    const storage = new Map();
    const timers = [];
    const workerMessages = [];
    const nativeTimer = (callback, ms) => {
        timers.push(ms);
        return setTimeout(callback, 0);
    };
    const window = {
        localStorage: {
            getItem(key) { if (options.failRead) throw new Error("Storage denied"); return storage.get(key) ?? null; },
            setItem(key, value) { if (options.failWrite) throw new Error("QuotaExceededError"); storage.set(key, value); },
            removeItem(key) { storage.delete(key); },
        },
        addEventListener() {},
        setTimeout: nativeTimer,
        clearTimeout,
        setInterval,
        clearInterval,
        requestAnimationFrame: (callback) => nativeTimer(callback, 16),
        cancelAnimationFrame: clearTimeout,
        fetch: options.fetch || (async () => { throw new Error("No network expected"); }),
        getComputedStyle: element => ({ display: element.style?.display || "block" }),
    };
    const originals = Object.fromEntries(["setTimeout", "clearTimeout", "setInterval", "clearInterval", "requestAnimationFrame", "cancelAnimationFrame"].map(k => [k, window[k]]));
    if (options.worker) {
        window.Blob = Blob;
        window.URL = { createObjectURL: () => "blob:local-test", revokeObjectURL() {} };
        window.Worker = class {
            constructor() { options.worker.instance = this; }
            postMessage(message) {
                workerMessages.push(message);
                queueMicrotask(() => this.onmessage({ data: message.id }));
            }
        };
    }
    const nodes = new Map();
    const document = { hidden: Boolean(options.hidden), querySelector: selector => nodes.get(selector.slice(1)) || null, querySelectorAll: () => [] };
    class DomEvent { constructor(type, init = {}) { Object.assign(this, { type, isTrusted: false }, init); } }
    const context = vm.createContext({ window, document, console, setTimeout: nativeTimer, clearTimeout, setInterval, clearInterval, Blob, Event: DomEvent, AbortController, HTMLInputElement: Control, HTMLTextAreaElement: Control });
    const instrumented = source.replace(
        /\}\)\(\);\s*$/,
        "window.__probe = { wait, enablePluginKeepAlive, serverQueueRowsHtml, requestNativeCacheContext, releaseNativeCacheContext, commitInlineCacheCursor, handleNativeGenerate, nativeCacheConfig, startLinkedRun, syncCacheCursor, restoreNativeCacheState: (...args) => restoreNativeCacheState(...args), installNativeCacheState: () => installNativeCacheState(), setForgeRunner: fn => { runForgeGeneration = fn; } };\n})();",
    );
    assert.notEqual(instrumented, source);
    vm.runInContext(instrumented, context, { filename: "llm_prompt_studio_auto_loop.js" });
    return { window, document, nodes, storage, timers, workerMessages, originals, test: window.__probe };
}

class Control {
    constructor(tagName = "INPUT", value = "", type = "text") {
        Object.assign(this, { tagName, value, type, checked: false, dataset: {}, style: {}, listeners: {}, children: [], innerHTML: "", textContent: "" });
    }
    append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); return this; }
    matches(selector) {
        return selector.split(",").some(part => {
            const item = part.trim();
            const tag = item.match(/^[a-z]+/i)?.[0];
            return (!tag || tag.toUpperCase() === this.tagName)
                && (!item.startsWith(".") || (this.className || "").split(" ").includes(item.slice(1)))
                && (!item.includes('[type="') || item.includes('[type="' + this.type + '"]'))
                && (!item.includes(":checked") || this.checked)
                && (!item.includes('[role="') || item.includes('[role="' + this.role + '"]'));
        });
    }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector) || null; }
    focus() {}
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
    removeEventListener(type, listener) { this.listeners[type] = (this.listeners[type] || []).filter(item => item !== listener); }
    dispatchEvent(event) {
        if (!event.target) event.target = this;
        for (const listener of this.listeners[event.type] || []) listener(event);
        if (event.bubbles) this.parent?.dispatchEvent(event);
        return true;
    }
    click(isTrusted = false) {
        this.clicks = (this.clicks || 0) + 1;
        if (this.type === "radio") this.parent.children.forEach(child => { child.checked = child === this; });
        if (this.type === "checkbox") this.checked = !this.checked;
        this.dispatchEvent({ type: "click", bubbles: true, isTrusted, preventDefault() {}, stopImmediatePropagation() {} });
        if (this.type === "radio" || this.type === "checkbox") this.dispatchEvent({ type: "change", bubbles: true, isTrusted });
    }
}

function cacheForm(h, slot = "txt2img", dropdown = false) {
    const controls = {};
    const add = (name, elements) => {
        const host = new Control("DIV").append(...elements);
        h.nodes.set('llm_prompt_studio_' + slot + '_inline_' + name, host);
        controls[name] = elements[0];
        return host;
    };
    controls.sourceHost = add("source", ["llm", "cache", "processed_cache"].map(value => new Control("INPUT", value, "radio")));
    controls.source.checked = true;
    add("cache_enabled", [new Control("INPUT", "", "checkbox")]);
    if (dropdown) {
        const field = new Control("INPUT", "");
        field.role = "combobox";
        const host = add("write_mode", [field]);
        const selected = new Control("SPAN");
        selected.className = "single-select";
        selected.textContent = "追加到后面";
        host.append(selected);
        controls.selectedMode = selected;
        field.addEventListener("click", () => {
            if (host.querySelector('[role="option"]')) return;
            ["追加到后面", "追加到前面", "替换当前 Prompt", "插入到标记位置"].forEach(text => {
                const option = new Control("LI");
                option.role = "option";
                option.textContent = text;
                option.addEventListener("mousedown", () => { selected.textContent = text; field.dispatchEvent({ type: "change", bubbles: true }); });
                host.append(option);
            });
        });
    } else {
        const select = new Control("SELECT", "append_end");
        select.append(...["append_end", "append_start", "replace", "marker"].map(value => new Control("OPTION", value)));
        add("write_mode", [select]);
    }
    add("marker", [new Control("TEXTAREA", "{{LLM}}")]);
    add("cache_cursor", [new Control("INPUT", "", "number")]);
    add("loop_status", []);
    h.nodes.set(slot + "_prompt", new Control("DIV").append(new Control("TEXTAREA", "base prompt")));
    h.nodes.set(slot + "_generate", new Control("BUTTON"));
    const script = new Control("SELECT", "None");
    h.nodes.set("tab_" + slot, { querySelector: selector => selector === "#script_list" ? new Control("DIV").append(script) : null });
    controls.script = script;
    return controls;
}

const snapshot = (config = {}, cursors = {}) => ({
    config: { source: "cache", cache_enabled: true, write_mode: "marker", marker: "<cache>", ...config },
    cursors: { cache: 41, processed_cache: 17, ...cursors },
});
const response = data => ({ ok: true, status: 200, json: async () => data });
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

test("native config restores actual controls after mount without saving or generating on UI ticks", async () => {
    const calls = [];
    const h = harness({ fetch: async (url, options) => { calls.push({ url, options }); return response(snapshot()); } });
    h.test.installNativeCacheState();
    assert.equal(calls.length, 0);
    const form = cacheForm(h, "txt2img", true);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    assert.equal(form.sourceHost.children.find(item => item.checked).value, "cache");
    assert.equal(form.cache_enabled.checked, true);
    assert.equal(form.selectedMode.textContent, "插入到标记位置");
    assert.equal(form.marker.value, "<cache>");
    assert.equal(form.cache_cursor.value, "41");
    for (let i = 0; i < 5; i++) h.test.installNativeCacheState();
    await flush();
    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /\/state\?slot=txt2img$/);
    assert.equal(h.nodes.get("txt2img_generate").clicks || 0, 0);
    assert.equal(h.test.syncCacheCursor("txt2img", "processed_cache"), 17);
});

test("late restoration preserves user edits and saves merged config only", async () => {
    let resolveState;
    const saves = [];
    const h = harness({ fetch: async (url, options) => {
        if (options.method === "POST") { saves.push(JSON.parse(options.body)); return response({}); }
        return new Promise(resolve => { resolveState = resolve; });
    } });
    const form = cacheForm(h);
    h.test.installNativeCacheState();
    const pending = h.test.restoreNativeCacheState("txt2img");
    form.marker.value = "user marker";
    form.marker.dispatchEvent({ type: "input", bubbles: true, isTrusted: true });
    form.sourceHost.children[2].click(true);
    resolveState(response(snapshot()));
    await pending;
    await new Promise(resolve => setTimeout(resolve, 15));
    await flush();
    assert.equal(form.marker.value, "user marker");
    assert.equal(form.sourceHost.children.find(item => item.checked).value, "processed_cache");
    assert.equal(form.write_mode.value, "marker");
    assert.ok(saves.length > 0);
    assert.deepEqual(saves.at(-1), { slot: "txt2img", config: snapshot({ source: "processed_cache", marker: "user marker" }).config });
    const count = saves.length;
    h.test.installNativeCacheState();
    await flush();
    assert.equal(saves.length, count);
});

test("native prepare fetches durable progress and sends null unless cursor was manually edited", async () => {
    const calls = [];
    let cursor = 41;
    const h = harness({ fetch: async (url, options) => {
        calls.push({ url, body: options.body && JSON.parse(options.body) });
        return response(url.includes("/prepare") ? { token: "a".repeat(32), count: 1, after_id: calls.at(-1).body.after_id ?? cursor, next_cursor: cursor + 1 } : snapshot({}, { cache: cursor }));
    } });
    const form = cacheForm(h);
    h.storage.set("llm_prompt_studio_cache_cursors_v1", JSON.stringify({ "txt2img:cache": 999 }));
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    cursor = 51;
    const prepare = async () => {
        const config = h.test.nativeCacheConfig("txt2img");
        const run = h.test.startLinkedRun(config);
        await h.test.requestNativeCacheContext(config, run);
        return run;
    };
    const run = await prepare();
    assert.equal(calls.at(-1).body.after_id, null);
    assert.equal(calls.at(-1).body.selected_script, "None");
    assert.equal(calls.at(-2).url.endsWith("/state?slot=txt2img"), true);
    assert.equal(run.cacheCursor, 51);
    form.cache_cursor.value = "0";
    form.cache_cursor.dispatchEvent({ type: "input", bubbles: true, isTrusted: true });
    await prepare();
    assert.equal(calls.at(-1).body.after_id, 0);
    await prepare();
    assert.equal(calls.at(-1).body.after_id, null, "manual cursor is consumed once, not reused on forever clicks");
});

test("release acknowledges actual partial cursor before legacy local commit", async () => {
    const h = harness({ fetch: async () => response({ released: true, committed: true, next_cursor: 12, completed_count: 2, state: "partial" }) });
    const form = cacheForm(h);
    const run = { slot: "txt2img", nativeContextToken: "token", pendingCacheCursor: 90, activeCacheCursor: 90, pendingCacheSource: "txt2img:cache", activeCacheSource: "txt2img:cache" };
    const released = await h.test.releaseNativeCacheContext(run, true);
    assert.equal(released.completed_count, 2);
    assert.equal(run.pendingCacheCursor, 12);
    assert.equal(run.activeCacheCursor, 12);
    h.test.commitInlineCacheCursor(run);
    assert.equal(form.cache_cursor.value, "12");
    assert.equal(JSON.parse(h.storage.get("llm_prompt_studio_cache_cursors_v1"))["txt2img:cache"], 12);
});

test("prepare actual cursor stays visible when progress advanced after the state read", async () => {
    const h = harness({ fetch: async url => response(url.includes("/prepare")
        ? { token: "a".repeat(32), count: 1, after_id: 57, next_cursor: 58 } : snapshot()) });
    const form = cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    const config = h.test.nativeCacheConfig("txt2img");
    await h.test.requestNativeCacheContext(config, h.test.startLinkedRun(config));
    h.test.installNativeCacheState();
    await flush();
    assert.equal(form.cache_cursor.value, "57");
    assert.equal(h.test.syncCacheCursor("txt2img", "cache"), 57);
});

test("native status uses completed_count, not requested batch count", async () => {
    const h = harness({ fetch: async url => response(url.includes("/prepare")
        ? { token: "a".repeat(32), count: 4, after_id: 41, next_cursor: 90 }
        : url.includes("/release") ? { released: true, committed: true, next_cursor: 12, completed_count: 2, state: "partial" } : snapshot()) });
    cacheForm(h);
    h.nodes.set("txt2img_batch_size", new Control("DIV").append(new Control("INPUT", "4")));
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    h.test.setForgeRunner(async () => {});
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    const status = h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML;
    assert.match(status, /2\s*\/\s*4/);
    assert.match(status, /12/);
    assert.doesNotMatch(status, /已完成 4|完成 4 张/);
});

test("native browser failure never claims backend work was uncommitted", async () => {
    const h = harness({ fetch: async url => response(url.includes("/prepare")
        ? { token: "a".repeat(32), count: 1, after_id: 41, next_cursor: 42 }
        : url.includes("/release") ? { released: false, committed: false, state: "active", completed_count: 0, next_cursor: 41 } : snapshot()) });
    cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    h.test.setForgeRunner(async () => { throw new Error("browser timeout"); });
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    const status = h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML;
    assert.match(status, /browser timeout/);
    assert.doesNotMatch(status, /未提交|可重试/);
    assert.match(status, /后端|刷新/);
});

test("selected Forge script rejects before prepare or native click", async () => {
    const calls = [];
    const h = harness({ fetch: async url => { calls.push(url); return response(snapshot()); } });
    const form = cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    form.script.value = "X/Y/Z plot";
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    assert.equal(calls.some(url => url.includes("/prepare")), false);
    assert.equal(h.nodes.get("txt2img_generate").clicks || 0, 0);
    assert.match(h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML, /Script|脚本/);
});

test("state read and save errors are visible and never trigger generation", async () => {
    const h = harness({ fetch: async () => { throw new Error("offline"); } });
    const form = cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img").catch(() => {});
    assert.match(h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML, /offline/);
    assert.equal(h.nodes.get("txt2img_generate").clicks || 0, 0);
    h.window.fetch = async (url, options) => { if (options.method === "POST") throw new Error("save denied"); return response(snapshot()); };
    await h.test.restoreNativeCacheState("txt2img");
    form.marker.value = "changed";
    form.marker.dispatchEvent({ type: "change", bubbles: true, isTrusted: true });
    await new Promise(resolve => setTimeout(resolve, 15));
    assert.match(h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML, /save denied/);
});

test("trusted Gradio dropdown selection persists, including during async restoration", async () => {
    const saves = [];
    const h = harness({ fetch: async (url, options) => {
        if (options.method === "POST") saves.push(JSON.parse(options.body));
        return response(snapshot());
    } });
    const form = cacheForm(h, "txt2img", true);
    h.test.installNativeCacheState();
    await flush();
    const host = h.nodes.get("llm_prompt_studio_txt2img_inline_write_mode");
    const option = host.children.find(item => item.textContent === "替换当前 Prompt");
    assert.ok(option, "restoration opened real dropdown options");
    option.dispatchEvent({ type: "mousedown", bubbles: true, isTrusted: true });
    await h.test.restoreNativeCacheState("txt2img");
    await new Promise(resolve => setTimeout(resolve, 15));
    assert.equal(form.selectedMode.textContent, "替换当前 Prompt");
    assert.equal(saves.at(-1).config.write_mode, "replace");
    const count = saves.length;
    for (let i = 0; i < 5; i++) h.test.installNativeCacheState();
    await new Promise(resolve => setTimeout(resolve, 15));
    assert.equal(saves.length, count);
});

test("nullable processed progress remains blank and manual cursors stay source scoped", async () => {
    const calls = [];
    const h = harness({ fetch: async (url, options) => {
        const body = options.body && JSON.parse(options.body);
        calls.push({ url, body });
        return response(url.includes("/prepare") ? { token: "a".repeat(32), count: 1, after_id: body.after_id ?? 0, next_cursor: 2 }
            : snapshot({ source: "processed_cache" }, { processed_cache: null }));
    } });
    const form = cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    assert.equal(form.cache_cursor.value, "");
    form.cache_cursor.value = "7";
    form.cache_cursor.dispatchEvent({ type: "change", bubbles: true, isTrusted: true });
    form.sourceHost.children[1].click(true);
    let config = h.test.nativeCacheConfig("txt2img");
    await h.test.requestNativeCacheContext(config, h.test.startLinkedRun(config));
    assert.equal(calls.at(-1).body.after_id, null);
    form.sourceHost.children[2].click(true);
    assert.equal(form.cache_cursor.value, "7");
    config = h.test.nativeCacheConfig("txt2img");
    await h.test.requestNativeCacheContext(config, h.test.startLinkedRun(config));
    assert.equal(calls.at(-1).body.after_id, 7);
});

test("zero-output and unconfirmed completion stop queued clicks without speculative cursor", async () => {
    for (const committed of [false, true]) {
        let prepares = 0;
        const h = harness({ fetch: async url => {
            if (url.includes("/prepare")) { prepares++; return response({ token: "a".repeat(32), count: 1, after_id: 41, next_cursor: 99 }); }
            return response(url.includes("/release") ? { released: true, committed, next_cursor: 41, completed_count: 0, state: "completed" } : snapshot());
        } });
        const form = cacheForm(h);
        h.test.installNativeCacheState();
        await h.test.restoreNativeCacheState("txt2img");
        h.test.setForgeRunner(async (slot, run) => { run.pendingNativeClick = true; });
        await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
        await new Promise(resolve => setTimeout(resolve, 15));
        assert.equal(prepares, 1);
        assert.equal(form.cache_cursor.value, "41");
        assert.notEqual(JSON.parse(h.storage.get("llm_prompt_studio_cache_cursors_v1") || "{}")["txt2img:cache"], 99);
    }
});

test("cancelled batches retain the backend-produced prefix and truthful stop status", async () => {
    const h = harness({ fetch: async url => response(url.includes("/prepare")
        ? { token: "a".repeat(32), count: 4, after_id: 41, next_cursor: 99 }
        : url.includes("/release") ? { released: true, committed: true, next_cursor: 43, completed_count: 2, state: "partial" } : snapshot()) });
    const form = cacheForm(h);
    h.nodes.set("txt2img_batch_size", new Control("DIV").append(new Control("INPUT", "4")));
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    h.test.setForgeRunner(async () => {
        const message = h.window.llmPromptStudioAutoLoop.cancelInline("txt2img");
        assert.match(message, /后端保留已完成图片的读取进度/);
        assert.doesNotMatch(message, /未提交/);
    });
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    assert.equal(form.cache_cursor.value, "43");
    const status = h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML;
    assert.match(status, /2\s*\/\s*4/);
    assert.doesNotMatch(status, /未提交|可重试/);
});

test("browser failure stops clicks queued during release acknowledgement", async () => {
    let prepares = 0;
    let activeRun;
    const h = harness({ fetch: async url => {
        if (url.includes("/prepare")) { prepares++; return response({ token: "a".repeat(32), count: 1, after_id: 41, next_cursor: 99 }); }
        if (url.includes("/release")) {
            if (prepares === 1) activeRun.pendingNativeClick = true;
            return response({ released: false, committed: false, state: "active" });
        }
        return response(snapshot());
    } });
    cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    h.test.setForgeRunner(async (slot, run) => { activeRun = run; throw new Error("browser timeout"); });
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    await new Promise(resolve => setTimeout(resolve, 15));
    assert.equal(prepares, 1);
});

test("script changes during prepare are rejected before native click", async () => {
    let form;
    let forgeRuns = 0;
    const h = harness({ fetch: async url => {
        if (url.includes("/prepare")) {
            form.script.value = "X/Y/Z plot";
            return response({ token: "a".repeat(32), count: 1, after_id: 41, next_cursor: 42 });
        }
        return response(url.includes("/release") ? { released: true, committed: false, state: "released" } : snapshot());
    } });
    form = cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    h.test.setForgeRunner(async () => { forgeRuns++; });
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    assert.equal(forgeRuns, 0);
    assert.equal(h.nodes.get("txt2img_prompt").children[0].value, "base prompt");
    assert.match(h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML, /Script/);
});

test("invalid completion and release network errors never publish a speculative cursor", async () => {
    const h = harness({ fetch: async () => response({ committed: true, next_cursor: null, completed_count: 0 }) });
    cacheForm(h);
    const run = { slot: "txt2img", nativeContextToken: "token", pendingCacheCursor: 99, activeCacheCursor: 99 };
    await assert.rejects(h.test.releaseNativeCacheContext(run, true), /进度无效/);
    h.window.fetch = async () => { throw new Error("release offline"); };
    await assert.rejects(h.test.releaseNativeCacheContext(run, true), /release offline/);
    assert.equal(await h.test.releaseNativeCacheContext(run, false), null);
    assert.equal(run.nativeReleaseError, "release offline");
    assert.equal(h.storage.size, 0);
});

test("plugin never replaces page-global timers or RAF", () => {
    const h = harness();
    for (const [name, native] of Object.entries(h.originals)) assert.equal(h.window[name], native, name);
});

test("hidden-page waits use only the plugin Worker scheduler", async () => {
    const worker = {};
    const h = harness({ hidden: true, worker });
    const count = h.timers.length;
    await h.test.wait(25);
    assert.equal(h.workerMessages.length, 1);
    assert.equal(h.workerMessages[0].ms, 25);
    assert.equal(h.timers.length, count);
    for (const [name, native] of Object.entries(h.originals)) assert.equal(h.window[name], native, name);
    worker.instance.onerror();
    await h.test.wait(5);
    assert.equal(h.timers.length, count + 1);
});

test("visible or Worker-unsupported waits retain native scheduling", async () => {
    const visible = harness();
    const count = visible.timers.length;
    await visible.test.wait(2);
    assert.equal(visible.timers.length, count + 1);
    const hidden = harness({ hidden: true });
    const hiddenCount = hidden.timers.length;
    await hidden.test.wait(2);
    assert.equal(hidden.timers.length, hiddenCount + 1);
});

test("server queue renders status without accessing legacy image payloads", () => {
    const h = harness();
    const job = { position: 1, status: "completed", prompt: "one cat <tree>", error: "" };
    Object.defineProperty(job, "images", { get() { throw new Error("Do not load queue images"); } });
    const markup = h.test.serverQueueRowsHtml([job]);
    assert.match(markup, /completed/);
    assert.match(markup, /one cat &lt;tree&gt;/);
    assert.doesNotMatch(markup, /<img|base64/);
});

test("native cache claims remain token-bound and recoverable", () => {
    assert.match(source, /const NATIVE_CACHE_MARKER_START/);
    assert.match(source, /const NATIVE_CACHE_MARKER_END/);
    assert.match(source, /function nativeClaimSuffix\(token\)/);
    assert.match(source, /function restoreNativeCacheClaim\(run\)/);
    assert.match(source, /run\.nativeClaimSuffix = claimSuffix/);
    assert.match(source, /run\.nativeClaimPrompt = claimPrompt/);
});

test("linked runs always remove their own prompt listeners", () => {
    assert.match(source, /function finishLinkedRun\(run\)\s*\{[\s\S]*?run\.promptElement\.removeEventListener/);
    assert.doesNotMatch(source, /if \(linkedRuns\[run\.slot\] !== run\) return/);
    assert.match(source, /if \(linkedRuns\[run\.slot\] === run\) linkedRuns\[run\.slot\] = null/);
});

test("native generation restores claims and finishes linked runs", () => {
    const handleNativeGenerate = source.match(/async function handleNativeGenerate\(slot, generate\)\s*\{([\s\S]*?)\n    \}\n\n\n    function installForgeInterruptHandlers/);
    assert.ok(handleNativeGenerate, "handleNativeGenerate function should remain discoverable");
    assert.match(handleNativeGenerate[1], /finally\s*\{[\s\S]*restoreNativeCacheClaim\(run\)/);
    assert.match(handleNativeGenerate[1], /finally\s*\{[\s\S]*finishLinkedRun\(run\)/);
});

test("programmatic prompt writes synchronize linked-run state", () => {
    assert.match(source, /function notifyPromptProgrammaticChange\(slot, value\)/);
    assert.match(source, /function writePrompt\(prompt, target, mode, basePrompt = ""\)\s*\{[\s\S]*setValue\(`\$\{target\}_prompt`, next\);\s*notifyPromptProgrammaticChange\(target, next\);/);
    assert.match(source, /setValue\(`\$\{slot\}_prompt`, next\);\s*notifyPromptProgrammaticChange\(slot, next\);/);
});

test("inline controls retain their public cancellation API", () => {
    const h = harness();
    assert.equal(typeof h.window.llmPromptStudioAutoLoop.inlineOnce, "function");
    assert.equal(typeof h.window.llmPromptStudioAutoLoop.cancelInline, "function");
});
