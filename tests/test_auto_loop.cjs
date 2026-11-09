"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../javascript/llm_prompt_studio_auto_loop.js"), "utf8");
const forgeUiSource = fs.readFileSync(path.join(__dirname, "../../../javascript/ui.js"), "utf8");

// Exercise the browser code in a VM with native-cache controls and transport.
function harness(options = {}) {
    const storage = new Map();
    const timers = [];
    const workerMessages = [];
    const scheduledTimers = new Map();
    let clock = 0;
    let nextTimerId = 0;
    const nativeTimer = (callback, ms) => {
        timers.push(ms);
        if (options.manualTimers) {
            const id = ++nextTimerId;
            scheduledTimers.set(id, { callback, due: clock + ms });
            return id;
        }
        return options.suspendNativeTimers ? 1 : setTimeout(callback, 0);
    };
    const window = {
        localStorage: {
            getItem(key) { if (options.failRead) throw new Error("Storage denied"); return storage.get(key) ?? null; },
            setItem(key, value) { if (options.failWrite) throw new Error("QuotaExceededError"); storage.set(key, value); },
            removeItem(key) { storage.delete(key); },
        },
        addEventListener() {},
        setTimeout: nativeTimer,
        clearTimeout: options.manualTimers ? id => scheduledTimers.delete(id) : clearTimeout,
        AbortController,
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
        "window.__probe = { wait, enablePluginKeepAlive, serverQueueRowsHtml, watchServerQueue, studioFetch, setStudioFetch: fn => { studioFetch = fn; }, beginInlineRun, requestPromptApi, requestNativeCacheContext, commitInlineCacheCursor, handleNativeGenerate, nativeCacheConfig, startLinkedRun, syncCacheCursor, restoreNativeCacheState: (...args) => restoreNativeCacheState(...args), installNativeCacheState: () => installNativeCacheState(), setForgeRunner: fn => { runForgeGeneration = fn; } };\n})();",
    );
    assert.notEqual(instrumented, source);
    vm.runInContext(instrumented, context, { filename: "llm_prompt_studio_auto_loop.js" });
    const advanceTime = async ms => {
        const target = clock + ms;
        while (true) {
            const entry = [...scheduledTimers].filter(([, timer]) => timer.due <= target)
                .sort((a, b) => a[1].due - b[1].due)[0];
            if (!entry) break;
            const [id, timer] = entry;
            clock = timer.due;
            scheduledTimers.delete(id);
            timer.callback();
            await flush();
        }
        clock = target;
        await flush();
    };
    return { window, document, nodes, storage, timers, scheduledTimers, advanceTime, workerMessages, originals, test: window.__probe };
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

test("Forge progress callbacks only update controls while they own the task ID", () => {
    for (const tab of ["txt2img", "img2img"]) {
        const taskKey = `${tab}_task_id`;
        const submit = forgeUiSource.match(new RegExp(`function ${tab === "txt2img" ? "submit" : "submit_img2img"}\\(\\)\\s*\\{([\\s\\S]*?)\\n\\}`));
        const restore = forgeUiSource.match(new RegExp(`function restoreProgress${tab === "txt2img" ? "Txt2img" : "Img2img"}\\(\\)\\s*\\{([\\s\\S]*?)\\n\\}`));
        assert.ok(submit, `${tab} submit callback should remain discoverable`);
        assert.ok(restore, `${tab} restore callback should remain discoverable`);
        assert.match(submit[1], new RegExp(`localGet\\("${taskKey}"\\) !== id\\) return;[\\s\\S]*localRemove\\("${taskKey}"\\)`));
        assert.match(restore[1], new RegExp(`localGet\\("${taskKey}"\\) !== id\\) return;`));
    }
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

test("native injection advances the cursor before Forge completes without polling", async () => {
    const calls = [];
    const h = harness({ fetch: async url => {
        calls.push(url);
        if (url.includes("/prepare")) return response({ token: "a".repeat(32), count: 1, after_id: 41, next_cursor: 90 });
        assert.match(url, /\/state\?slot=txt2img$/);
        return response(snapshot());
    } });
    const form = cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    calls.length = 0;
    let finishForge;
    let started;
    const injecting = new Promise(resolve => { started = resolve; });
    h.test.setForgeRunner(async (slot, run, generate, afterClick) => {
        assert.equal(form.cache_cursor.value, "41");
        assert.notEqual(h.nodes.get("txt2img_prompt").children[0].value, "base prompt", "prepare attached the token claim");
        afterClick();
        assert.equal(form.cache_cursor.value, "90");
        assert.equal(run.cacheCursor, 90);
        assert.equal(h.nodes.get("txt2img_prompt").children[0].value, "base prompt", "claim is removed after injection");
        started();
        await new Promise(resolve => { finishForge = resolve; });
    });
    const pending = h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    await Promise.race([injecting, pending.then(() => { throw new Error("Forge did not reach injection"); })]);
    assert.equal(JSON.parse(h.storage.get("llm_prompt_studio_cache_cursors_v1"))["txt2img:cache"], 90);
    assert.equal(calls.length, 2, "state and prepare are the only requests while Forge is running");
    finishForge();
    await pending;
    assert.equal(calls.length, 2, "completion does not release or poll native cache");
    assert.equal(form.cache_cursor.value, "90");
});

test("native zero-output completion retains the cursor advanced at injection", async () => {
    const calls = [];
    const h = harness({ fetch: async url => {
        calls.push(url);
        return response(url.includes("/prepare")
            ? { token: "a".repeat(32), count: 1, after_id: 41, next_cursor: 42 } : snapshot());
    } });
    const form = cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    calls.length = 0;
    h.test.setForgeRunner(async (slot, run, generate, afterClick) => { afterClick(); });
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    assert.equal(form.cache_cursor.value, "42");
    assert.equal(calls.length, 2);
    assert.equal(calls.some(url => /release|progress/.test(url)), false);
    assert.match(h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML, /缓存 Prompt 已注入/);
});

test("native stop after injection preserves the full injected cursor and removes claims", async () => {
    const calls = [];
    const h = harness({ fetch: async url => {
        calls.push(url);
        return response(url.includes("/prepare")
            ? { token: "a".repeat(32), count: 1, after_id: 41, next_cursor: 90 } : snapshot());
    } });
    const form = cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    calls.length = 0;
    let activeRun;
    h.test.setForgeRunner(async (slot, run, generate, afterClick) => {
        activeRun = run;
        afterClick();
        h.window.llmPromptStudioAutoLoop.cancelInline(slot);
    });
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    assert.equal(activeRun.cancelled, true);
    assert.equal(form.cache_cursor.value, "90");
    assert.equal(calls.length, 2, "native stop does not issue LLM cancel or cache release");
    assert.equal(h.nodes.get("txt2img_prompt").children[0].value, "base prompt");
    assert.equal(h.nodes.get("txt2img_prompt").children[0].listeners.input.length, 0);
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

test("native status reports injection without waiting for image completion counts", async () => {
    const calls = [];
    const h = harness({ fetch: async url => {
        calls.push(url);
        return response(url.includes("/prepare")
            ? { token: "a".repeat(32), count: 4, after_id: 41, next_cursor: 90 } : snapshot());
    } });
    const form = cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    calls.length = 0;
    h.nodes.set("txt2img_batch_size", new Control("DIV").append(new Control("INPUT", "4")));
    h.test.setForgeRunner(async (slot, run, generate, afterClick) => { afterClick(); });
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    const status = h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML;
    assert.match(status, /缓存 Prompt 已注入/);
    assert.match(status, /插件游标已推进/);
    assert.equal(form.cache_cursor.value, "90");
    assert.equal(calls.length, 2);
    assert.doesNotMatch(status, /已完成 4|完成 4 张|4\s*\/\s*4/);
});

test("native browser failure never claims backend work was uncommitted", async () => {
    const h = harness({ fetch: async url => response(url.includes("/prepare")
        ? { token: "a".repeat(32), count: 1, after_id: 41, next_cursor: 42 }
        : snapshot()) });
    cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    h.test.setForgeRunner(async () => { throw new Error("browser timeout"); });
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    const status = h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML;
    assert.match(status, /browser timeout/);
    assert.doesNotMatch(status, /未提交|可重试/);
    assert.match(status, /Forge|未完成/);
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
    assert.equal(calls.findLast(item => item.url.includes("/prepare")).body.after_id, null);
    form.sourceHost.children[2].click(true);
    assert.equal(form.cache_cursor.value, "7");
    config = h.test.nativeCacheConfig("txt2img");
    await h.test.requestNativeCacheContext(config, h.test.startLinkedRun(config));
    assert.equal(calls.findLast(item => item.url.includes("/prepare")).body.after_id, 7);
});

test("native completion or browser failure discards queued clicks without rolling back injection", async () => {
    for (const fail of [false, true]) {
        const calls = [];
        const h = harness({ fetch: async url => {
            calls.push(url);
            return response(url.includes("/prepare")
                ? { token: "a".repeat(32), count: 1, after_id: 41, next_cursor: 99 } : snapshot());
        } });
        const form = cacheForm(h);
        h.test.installNativeCacheState();
        await h.test.restoreNativeCacheState("txt2img");
        calls.length = 0;
        let activeRun;
        h.test.setForgeRunner(async (slot, run, generate, afterClick) => {
            activeRun = run;
            afterClick();
            run.pendingNativeClick = true;
            if (fail) throw new Error("browser timeout");
        });
        await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
        assert.equal(activeRun.pendingNativeClick, false);
        assert.equal(activeRun.busy, false);
        assert.equal(form.cache_cursor.value, "99");
        assert.equal(JSON.parse(h.storage.get("llm_prompt_studio_cache_cursors_v1"))["txt2img:cache"], 99);
        assert.equal(calls.length, 2, "no completion acknowledgement or extra prepare");
        if (fail) assert.match(h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML, /browser timeout.*插件游标已推进/);
    }
});

test("cancelled native batches keep the full injection cursor without completion accounting", async () => {
    const calls = [];
    const h = harness({ fetch: async url => {
        calls.push(url);
        return response(url.includes("/prepare")
            ? { token: "a".repeat(32), count: 4, after_id: 41, next_cursor: 99 } : snapshot());
    } });
    const form = cacheForm(h);
    h.test.installNativeCacheState();
    await h.test.restoreNativeCacheState("txt2img");
    calls.length = 0;
    h.nodes.set("txt2img_batch_size", new Control("DIV").append(new Control("INPUT", "4")));
    h.test.setForgeRunner(async (slot, run, generate, afterClick) => {
        afterClick();
        const message = h.window.llmPromptStudioAutoLoop.cancelInline("txt2img");
        assert.match(message, /已停止/);
    });
    await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
    assert.equal(form.cache_cursor.value, "99");
    assert.equal(calls.length, 2);
    const status = h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML;
    assert.match(status, /缓存生图已停止/);
    assert.doesNotMatch(status, /2\s*\/\s*4|未提交|可重试/);
});

test("script changes during prepare are rejected before native click", async () => {
    let form;
    let forgeRuns = 0;
    const h = harness({ fetch: async url => {
        if (url.includes("/prepare")) {
            form.script.value = "X/Y/Z plot";
            return response({ token: "a".repeat(32), count: 1, after_id: 41, next_cursor: 42 });
        }
        return response(snapshot());
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

test("invalid prepare or transport failure never publishes an injection cursor or launches Forge", async () => {
    for (const prepare of [
        { token: "", count: 1, after_id: 41, next_cursor: 99 },
        { token: "a".repeat(32), count: 1, after_id: 41, next_cursor: null },
        { token: "a".repeat(32), count: 0, after_id: 41, next_cursor: 99 },
        new Error("prepare offline"),
    ]) {
        const h = harness({ fetch: async url => {
            if (!url.includes("/prepare")) return response(snapshot());
            if (prepare instanceof Error) throw prepare;
            return response(prepare);
        } });
        const form = cacheForm(h);
        h.test.installNativeCacheState();
        await h.test.restoreNativeCacheState("txt2img");
        let forgeRuns = 0;
        h.test.setForgeRunner(async () => { forgeRuns++; });
        await h.test.handleNativeGenerate("txt2img", h.nodes.get("txt2img_generate"));
        assert.equal(forgeRuns, 0);
        assert.equal(form.cache_cursor.value, "41");
        assert.equal(h.storage.has("llm_prompt_studio_cache_cursors_v1"), false);
        assert.equal(h.nodes.get("txt2img_prompt").children[0].value, "base prompt");
        assert.match(h.nodes.get("llm_prompt_studio_txt2img_inline_loop_status").innerHTML, /注入未执行/);
    }
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

test("Worker-unsupported waits retain native scheduling in either visibility state", async () => {
    const visible = harness();
    const count = visible.timers.length;
    await visible.test.wait(2);
    assert.equal(visible.timers.length, count + 1);
    const hidden = harness({ hidden: true });
    const hiddenCount = hidden.timers.length;
    await hidden.test.wait(2);
    assert.equal(hidden.timers.length, hiddenCount + 1);
});

test("waits started before hiding already use the Worker scheduler", async () => {
    const h = harness({ worker: {}, suspendNativeTimers: true });
    let settled = false;
    const pending = h.test.wait(25).then(() => { settled = true; });
    h.document.hidden = true;
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(settled, true, "foreground waits must survive suspension of page timers");
    await pending;
    assert.equal(h.workerMessages.length, 1);
    assert.equal(h.workerMessages[0].ms, 25);
    assert.equal(h.timers.length, 0);
});

test("server queue renders status without accessing legacy image payloads", () => {
    const h = harness();
    const job = { position: 1, status: "completed", prompt: "one cat <tree>", error: "" };
    Object.defineProperty(job, "images", { get() { throw new Error("Do not load queue images"); } });
    const markup = h.test.serverQueueRowsHtml([job]);
    assert.match(markup, /completed/);
    assert.doesNotMatch(markup, /one cat|&lt;tree&gt;/);
    assert.doesNotMatch(markup, /<img|base64/);
});

test("server queue watcher stops immediately on permanent HTTP errors", async () => {
    let requests = 0;
    const h = harness({ fetch: async () => {
        requests++;
        return { ok: false, status: 404 };
    } });
    const status = { innerHTML: "", get textContent() { return this.innerHTML; } };
    h.nodes.set("llm_prompt_studio_server_queue_status", status);

    await h.test.watchServerQueue("missing-batch");

    assert.equal(requests, 1);
    assert.match(status.innerHTML, /轮询已停止：服务端队列 HTTP 404/);
});

test("server queue watcher bounds transient failures", async () => {
    let requests = 0;
    const h = harness({ fetch: async () => {
        requests++;
        throw new Error("backend offline");
    } });
    const status = { innerHTML: "", get textContent() { return this.innerHTML; } };
    h.nodes.set("llm_prompt_studio_server_queue_status", status);

    await h.test.watchServerQueue("offline-batch");

    assert.equal(requests, 5);
    assert.deepEqual(h.timers.filter(ms => ms <= 8000), [1000, 2000, 4000, 8000]);
    assert.match(status.innerHTML, /轮询已停止：backend offline/);
});

test("server queue watcher does not retry authentication failures", async () => {
    let requests = 0;
    const h = harness({ fetch: async () => {
        requests++;
        return { status: 401 };
    } });
    const status = { innerHTML: "", get textContent() { return this.innerHTML; } };
    h.nodes.set("llm_prompt_studio_server_queue_status", status);

    await h.test.watchServerQueue("protected-batch");

    assert.equal(requests, 1);
    assert.match(status.innerHTML, /轮询已停止：Studio API 认证失败/);
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
    assert.match(source, /function clearRunRegistry\(run\)/);
    assert.match(source, /finishLinkedRun\(run\)\s*\{[\s\S]*?clearRunRegistry\(run\)/);
});

test("native generation restores claims and finishes linked runs", () => {
    const handleNativeGenerate = source.match(/async function handleNativeGenerate\(slot, generate\)\s*\{([\s\S]*?)\n    \}\s+function installForgeInterruptHandlers/);
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


// Virtual time keeps provider budgets and cancellation races deterministic.
const abortableFetch = signal => new Promise((resolve, reject) => {
    const abort = () => reject(new Error("transport aborted"));
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
});

test("generation defers all deadlines to the backend, including retries and fallback", async () => {
    for (const endpoint of ["inline-generate", "auto-loop-generate"]) {
        let finish;
        const h = harness({ manualTimers: true, fetch: (url, options) => new Promise((resolve, reject) => {
            finish = resolve;
            options.signal.addEventListener("abort", () => reject(new Error("transport aborted")), { once: true });
        }) });
        const run = h.test.beginInlineRun("txt2img", "txt2img");
        const pending = h.test.requestPromptApi(run, endpoint, { slot: "txt2img" });
        const result = pending.catch(error => error);
        await h.advanceTime(24 * 60 * 60 * 1000);
        assert.deepEqual(h.timers, [], endpoint + " must not impose a competing browser deadline");
        assert.ok(run.abortController, "the request remains cancellable after extended backend retries");
        finish(response({ prompt: "one cat" }));
        assert.equal((await result).prompt, "one cat");
        assert.equal(h.scheduledTimers.size, 0);
    }
});

test("legal 90 to 600 second inline generation keeps its request ID and succeeds", async () => {
    for (const slot of ["txt2img", "img2img"]) {
        let resolveGeneration;
        const calls = [];
        const h = harness({ manualTimers: true, fetch: (url, options) => {
            calls.push({ url, options });
            if (url.endsWith("/inline-generate")) return new Promise((resolve, reject) => {
                resolveGeneration = resolve;
                options.signal.addEventListener("abort", () => reject(new Error("transport aborted")), { once: true });
            });
            return Promise.resolve(response({ cancelled: true }));
        } });
        const run = h.test.beginInlineRun(slot, slot);
        const pending = h.test.requestPromptApi(run, "inline-generate", { slot });
        const result = pending.catch(error => error);
        const id = run.requestId;
        assert.ok(id);
        for (const elapsed of [90000, 510000]) {
            await h.advanceTime(elapsed);
            assert.equal(calls[0].options.signal.aborted, false, "legal backend generation is still running");
            assert.equal(run.requestId, id);
        }
        assert.equal(calls[0].options.credentials, "same-origin");
        assert.equal("timeoutMs" in calls[0].options, false, "internal budget is not forwarded to fetch");
        assert.equal(JSON.parse(calls[0].options.body).request_id, id);
        resolveGeneration(response({ prompt: "one cat" }));
        assert.equal((await result).prompt, "one cat");
        assert.equal(calls.length, 1, "success never sends cancellation");
        assert.equal(run.requestId, "");
        assert.equal(run.abortController, null);
        assert.equal(h.scheduledTimers.size, 0);
    }
});

test("ordinary state requests still time out at exactly fifteen seconds", async () => {
    const calls = [];
    const h = harness({ manualTimers: true, fetch: (url, options) => {
        calls.push({ url, options });
        return abortableFetch(options.signal);
    } });
    const pending = h.test.studioFetch("/llm-prompt-studio/v1/native-cache/state?slot=txt2img").catch(error => error);
    await h.advanceTime(14999);
    assert.equal(calls[0].options.signal.aborted, false);
    await h.advanceTime(1);
    assert.equal(calls[0].options.signal.aborted, true);
    assert.match((await pending).message, /超时/);
    assert.deepEqual(h.timers, [15000]);
    assert.equal(calls.length, 1);
    assert.equal(h.scheduledTimers.size, 0);
});

// Force a deadline only in cancellation regression tests, without changing the
// production generation budget or the request/cancel implementation.
function forceGenerationDeadline(h) {
    const fetch = h.test.studioFetch;
    h.test.setStudioFetch((url, options, consumeResponse) => fetch(url, {
        ...options,
        ...(url.endsWith("/inline-generate") ? { timeoutMs: 15000 } : {}),
    }, consumeResponse));
}

test("inline timeout cancels the exact slot and ID before clearing it, once per request", async () => {
    for (const slot of ["txt2img", "img2img"]) {
        const cancels = [];
        let run;
        const h = harness({ manualTimers: true, fetch: (url, options) => {
            if (url.endsWith("/inline-generate")) return abortableFetch(options.signal);
            cancels.push({ body: JSON.parse(options.body), liveId: run.requestId, signal: options.signal });
            return Promise.resolve(response({ cancelled: true }));
        } });
        forceGenerationDeadline(h);
        run = h.test.beginInlineRun(slot, slot);
        const ids = [];
        for (let attempt = 0; attempt < 2; attempt++) {
            const pending = h.test.requestPromptApi(run, "inline-generate", { slot }).catch(error => error);
            ids.push(run.requestId);
            const budget = h.timers.at(-1);
            await h.advanceTime(budget);
            assert.match((await pending).message, /超时/);
            assert.equal(cancels.length, attempt + 1);
            assert.deepEqual(cancels[attempt].body, { slot, request_id: ids[attempt] });
            assert.equal(cancels[attempt].liveId, ids[attempt], "cancel is sent while the request ID is still owned");
            assert.equal(cancels[attempt].signal.aborted, false, "cancel has an independent signal");
            assert.equal(run.requestId, "");
            assert.equal(run.abortController, null);
            assert.equal(h.timers.at(-1), 15000, "cancel itself uses the ordinary request budget");
            assert.equal(h.scheduledTimers.size, 0);
        }
        assert.notEqual(ids[0], ids[1], "a new request can be cancelled independently");
    }
});

test("timeout cancellation failure preserves the timeout error and clears local state", async () => {
    let cancels = 0;
    const h = harness({ manualTimers: true, fetch: (url, options) => {
        if (url.endsWith("/inline-generate")) return abortableFetch(options.signal);
        cancels++;
        return Promise.reject(new Error("cancel offline"));
    } });
    forceGenerationDeadline(h);
    const run = h.test.beginInlineRun("txt2img", "txt2img");
    const pending = h.test.requestPromptApi(run, "inline-generate", { slot: "txt2img" }).catch(error => error);
    await h.advanceTime(h.timers[0]);
    assert.equal(cancels, 1);
    assert.match((await pending).message, /超时/);
    assert.equal(run.requestId, "");
    assert.equal(run.abortController, null);
    assert.equal(h.scheduledTimers.size, 0);
});

test("repeated user stop sends one inline cancel and aborts the generation transport", async () => {
    for (const slot of ["txt2img", "img2img"]) {
        const calls = [];
        const h = harness({ manualTimers: true, fetch: (url, options) => {
            calls.push({ url, body: JSON.parse(options.body), signal: options.signal });
            return url.endsWith("/inline-generate") ? abortableFetch(options.signal) : Promise.resolve(response({ cancelled: true }));
        } });
        const run = h.test.beginInlineRun(slot, slot);
        const pending = h.test.requestPromptApi(run, "inline-generate", { slot }).catch(error => error);
        const id = run.requestId;
        h.window.llmPromptStudioAutoLoop.cancelInline(slot);
        h.window.llmPromptStudioAutoLoop.cancelInline(slot);
        assert.equal(calls[0].signal.aborted, true);
        assert.equal((await pending).message, "已取消");
        await flush();
        assert.equal(calls.length, 2, "two stops and the rejected fetch share one backend cancellation");
        assert.deepEqual(calls[1].body, { slot, request_id: id });
        assert.equal(calls[1].signal.aborted, false);
        assert.equal(run.requestId, "");
        assert.equal(run.abortController, null);
        assert.equal(h.scheduledTimers.size, 0);
    }
});

test("user stop racing the timeout does not duplicate backend cancellation", async () => {
    let cancels = 0;
    const h = harness({ manualTimers: true, fetch: (url, options) => {
        if (url.endsWith("/inline-generate")) return abortableFetch(options.signal);
        cancels++;
        return Promise.resolve(response({ cancelled: true }));
    } });
    forceGenerationDeadline(h);
    const run = h.test.beginInlineRun("txt2img", "txt2img");
    const pending = h.test.requestPromptApi(run, "inline-generate", { slot: "txt2img" }).catch(error => error);
    const timeout = [...h.scheduledTimers.values()][0].callback;
    timeout();
    h.window.llmPromptStudioAutoLoop.cancelInline("txt2img");
    h.window.llmPromptStudioAutoLoop.cancelInline("txt2img");
    assert.equal((await pending).message, "已取消");
    await flush();
    assert.equal(cancels, 1);
    assert.equal(run.requestId, "");
    assert.equal(h.scheduledTimers.size, 0);
});

test("HTTP and network failures clear inline state without sending timeout cancellation", async () => {
    for (const failure of [new Error("backend offline"), { ok: false, status: 500, json: async () => ({ detail: "generation failed" }) }]) {
        const calls = [];
        const h = harness({ manualTimers: true, fetch: async url => {
            calls.push(url);
            if (failure instanceof Error) throw failure;
            return failure;
        } });
        const run = h.test.beginInlineRun("txt2img", "txt2img");
        await assert.rejects(h.test.requestPromptApi(run, "inline-generate", { slot: "txt2img" }), /backend offline|generation failed/);
        assert.equal(calls.length, 1);
        assert.equal(run.requestId, "");
        assert.equal(run.abortController, null);
        assert.equal(h.scheduledTimers.size, 0);
    }
});


test("inline JSON body keeps its ID and abort signal until parsing completes", async () => {
    for (const slot of ["txt2img", "img2img"]) {
        let finishBody;
        let reading = false;
        let signal;
        const calls = [];
        const h = harness({ manualTimers: true, fetch: async (url, options) => {
            calls.push(url);
            signal = options.signal;
            return { ok: true, status: 200, json: () => {
                reading = true;
                return new Promise(resolve => { finishBody = resolve; });
            } };
        } });
        const run = h.test.beginInlineRun(slot, slot);
        const pending = h.test.requestPromptApi(run, "inline-generate", { slot });
        await flush();
        assert.equal(reading, true);
        const id = run.requestId;
        const controller = run.abortController;
        await h.advanceTime(600000);
        assert.equal(run.requestId, id);
        assert.ok(id);
        assert.equal(run.abortController, controller);
        assert.equal(signal.aborted, false);
        assert.deepEqual(h.timers, []);
        finishBody({ prompt: "one cat" });
        assert.equal((await pending).prompt, "one cat");
        assert.equal(run.requestId, "");
        assert.equal(run.abortController, null);
        assert.equal(calls.length, 1);
    }
});

test("stop during inline JSON parsing aborts the body and cancels the exact request once", async () => {
    for (const slot of ["txt2img", "img2img"]) {
        const cancels = [];
        let bodySignal;
        let reading = false;
        let run;
        const h = harness({ manualTimers: true, fetch: async (url, options) => {
            if (url.endsWith("/inline-cancel")) {
                cancels.push({ body: JSON.parse(options.body), liveId: run.requestId });
                return response({ cancelled: true });
            }
            bodySignal = options.signal;
            return { ok: true, status: 200, json: () => {
                reading = true;
                return abortableFetch(bodySignal);
            } };
        } });
        run = h.test.beginInlineRun(slot, slot);
        const pending = h.test.requestPromptApi(run, "inline-generate", { slot }).catch(error => error);
        await flush();
        assert.equal(reading, true);
        const id = run.requestId;
        await h.advanceTime(600000);
        h.window.llmPromptStudioAutoLoop.cancelInline(slot);
        h.window.llmPromptStudioAutoLoop.cancelInline(slot);
        assert.equal(bodySignal.aborted, true, "external abort forwarding survives response headers");
        assert.equal((await pending).message, "已取消");
        await flush();
        assert.deepEqual(cancels, [{ body: { slot, request_id: id }, liveId: id }]);
        assert.equal(run.requestId, "");
        assert.equal(run.abortController, null);
        assert.equal(h.scheduledTimers.size, 0);
    }
});

test("an explicit finite request budget includes response JSON reading", async () => {
    let reading = false;
    const h = harness({ manualTimers: true, fetch: async (url, options) => ({
        ok: true, status: 200, json: () => { reading = true; return abortableFetch(options.signal); },
    }) });
    const pending = h.test.studioFetch("/status", {}, response => response.json()).catch(error => error);
    await flush();
    assert.equal(reading, true);
    assert.equal(h.scheduledTimers.size, 1, "headers do not clear the body deadline");
    await h.advanceTime(15000);
    assert.equal((await pending).name, "TimeoutError");
    assert.equal(h.scheduledTimers.size, 0);
});

test("an explicit inline deadline during JSON parsing cancels before ID cleanup", async () => {
    const cancels = [];
    let reading = false;
    let run;
    const h = harness({ manualTimers: true, fetch: async (url, options) => {
        if (url.endsWith("/inline-cancel")) {
            cancels.push({ body: JSON.parse(options.body), liveId: run.requestId });
            return response({ cancelled: true });
        }
        return { ok: true, status: 200, json: () => {
            reading = true;
            return abortableFetch(options.signal);
        } };
    } });
    forceGenerationDeadline(h);
    run = h.test.beginInlineRun("txt2img", "txt2img");
    const pending = h.test.requestPromptApi(run, "inline-generate", { slot: "txt2img" }).catch(error => error);
    await flush();
    assert.equal(reading, true);
    const id = run.requestId;
    await h.advanceTime(14999);
    assert.equal(run.requestId, id);
    assert.equal(cancels.length, 0);
    await h.advanceTime(1);
    assert.equal((await pending).name, "TimeoutError");
    assert.deepEqual(cancels, [{ body: { slot: "txt2img", request_id: id }, liveId: id }]);
    assert.equal(run.requestId, "");
    assert.equal(run.abortController, null);
    assert.equal(h.scheduledTimers.size, 0);
});
