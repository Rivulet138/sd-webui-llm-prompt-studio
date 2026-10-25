"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../javascript/llm_prompt_studio_auto_loop.js"), "utf8");

// The studio prompt queue was removed (it had no UI entry and no callers), so this
// suite now covers what is still live: keep-alive timers, the Worker-based wait
// scheduler, and the server-queue renderer.
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
        fetch: async () => { throw new Error("No network expected"); },
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
    const instrumented = source.replace(
        /\}\)\(\);\s*$/,
        "window.__probe = { wait, enablePluginKeepAlive, serverQueueRowsHtml };\n})();",
    );
    assert.notEqual(instrumented, source);
    vm.runInContext(instrumented, context, { filename: "llm_prompt_studio_auto_loop.js" });
    return { window, document, storage, timers, workerMessages, originals, test: window.__probe };
}

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
