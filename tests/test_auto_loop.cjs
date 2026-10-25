"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../javascript/llm_prompt_studio_auto_loop.js"), "utf8");
const KEY = "llm_prompt_studio_auto_loop_queue_v1";

function harness(stored, options = {}) {
    const storage = new Map(stored === undefined ? [] : [[KEY, stored]]);
    const events = new Map();
    const timers = [];
    const workerMessages = [];
    let fetchCount = 0;
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
        addEventListener(name, callback) { events.set(name, callback); },
        setTimeout: nativeTimer,
        clearTimeout,
        setInterval,
        clearInterval,
        requestAnimationFrame: (callback) => nativeTimer(callback, 16),
        cancelAnimationFrame: clearTimeout,
        fetch: async () => { fetchCount++; throw new Error("No network expected during restore"); },
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
    const document = { hidden: Boolean(options.hidden), querySelector: () => null, querySelectorAll: () => [] };
    const context = vm.createContext({ window, document, console, setTimeout: nativeTimer, clearTimeout, setInterval, clearInterval, Blob, Event, AbortController });
    const instrumented = source.replace(/\}\)\(\);\s*$/, "window.__queueTest = { state, saveQueue, loadQueue, migrateQueue, wait, enablePluginKeepAlive, serverQueueRowsHtml };\n})();");
    assert.notEqual(instrumented, source);
    vm.runInContext(instrumented, context, { filename: "llm_prompt_studio_auto_loop.js" });
    return { window, document, storage, events, timers, workerMessages, originals, test: window.__queueTest, fetchCount: () => fetchCount };
}

const row = (overrides = {}) => ({ id: "r1", index: 1, prompt: "one cat", status: "pending", selected: true, requestId: "request 1", ...overrides });

test("save and refresh restore a versioned queue without auto-start", () => {
    const first = harness();
    first.test.state.queue = [row({ status: "running" }), row({ id: "r2", status: "completed" })];
    first.test.state.requestIds.add("request 1");
    assert.equal(first.test.saveQueue(), true);
    const saved = JSON.parse(first.storage.get(KEY));
    assert.equal(saved.version, 2);
    assert.equal(saved.rows.length, 2);
    const second = harness(first.storage.get(KEY));
    assert.equal(second.test.state.queue.length, 2);
    assert.equal(second.test.state.queue[0].status, "pending");
    assert.equal(second.test.state.queue[1].status, "completed");
    assert.equal(second.test.state.requestIds.has("request 1"), true);
    assert.equal(second.test.state.active, null);
    assert.equal(second.fetchCount(), 0);
});

test("legacy array migration merges duplicates and keeps completed state", () => {
    const h = harness(JSON.stringify([row({ prompt: " cat " }), row({ id: "r2", prompt: "CAT", status: "completed" }), row({ id: "r3", prompt: "" })]));
    assert.equal(h.test.state.queue.length, 1);
    assert.equal(h.test.state.queue[0].status, "completed");
    assert.equal(h.test.state.persistent, true);
});

test("version 1 envelope remains supported", () => {
    const h = harness(JSON.stringify({ version: 1, rows: [row({ status: "running" })], requestIds: ["before"] }));
    assert.equal(h.test.state.queue[0].status, "pending");
    assert.equal(h.test.state.requestIds.has("before"), true);
});

test("version 2 preserves intentionally repeated prompts and selection", () => {
    const h = harness(JSON.stringify({ version: 2, rows: [row(), row({ id: "r2", selected: false })] }));
    assert.equal(h.test.state.queue.length, 2);
    assert.equal(h.test.state.queue[1].selected, false);
});

test("invalid JSON is reported and the original storage is not deleted", () => {
    const h = harness("{broken");
    assert.equal(h.test.state.persistent, false);
    assert.equal(h.test.state.lastSaveFailure, "invalid");
    assert.equal(h.storage.get(KEY), "{broken");
});

test("invalid envelope is not silently reported as a restored empty queue", () => {
    const stored = JSON.stringify({ version: 2, rows: "invalid" });
    const h = harness(stored);
    assert.equal(h.test.state.persistent, false);
    assert.equal(h.storage.get(KEY), stored);
});

test("storage read and write failures remain explicit", () => {
    const unreadable = harness(undefined, { failRead: true });
    assert.equal(unreadable.test.state.persistent, false);
    const h = harness(undefined, { failWrite: true });
    h.test.state.queue = [row()];
    assert.equal(h.test.saveQueue(), false);
    assert.equal(h.test.state.queue.length, 1);
    assert.equal(h.test.state.persistent, false);
    assert.match(h.window.llmPromptStudioAutoLoop.clearQueue(), /未持久化/);
});

test("clear persists an empty queue and refuses while active", () => {
    const h = harness(JSON.stringify([row()]));
    h.test.state.active = { id: "active" };
    assert.match(h.window.llmPromptStudioAutoLoop.clearQueue(), /运行中/);
    assert.equal(h.test.state.queue.length, 1);
    h.test.state.active = null;
    assert.match(h.window.llmPromptStudioAutoLoop.clearQueue(), /已清空/);
    assert.equal(JSON.parse(h.storage.get(KEY)).rows.length, 0);
});

test("cross-tab storage refresh does not erase persisted data or interrupt an active run", () => {
    const h = harness();
    const saved = JSON.stringify({ version: 2, rows: [row()] });
    h.storage.set(KEY, saved);
    h.events.get("storage")({ key: KEY });
    assert.equal(h.test.state.queue.length, 1);
    assert.equal(h.storage.get(KEY), saved);
    h.test.state.active = { id: "active" };
    h.storage.set(KEY, JSON.stringify([]));
    h.events.get("storage")({ key: KEY });
    assert.equal(h.test.state.queue.length, 1);
});

test("plugin never replaces page-global timers or RAF", () => {
    const h = harness();
    for (const [name, native] of Object.entries(h.originals)) assert.equal(h.window[name], native, name);
});

test("hidden-page waits use only the plugin Worker scheduler", async () => {
    const worker = {};
    const h = harness(undefined, { hidden: true, worker });
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
    const hidden = harness(undefined, { hidden: true });
    await hidden.test.wait(2);
    assert.equal(hidden.timers.length, 2);
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
