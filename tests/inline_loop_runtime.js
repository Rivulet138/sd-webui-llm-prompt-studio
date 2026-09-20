"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../javascript/llm_prompt_studio_auto_loop.js"), "utf8");
const config = {
    slot: "txt2img", writeMode: "append_end", marker: "{{LLM}}",
    request: "change the scenery", variation: "", source: "llm",
    preset: "Danbooru Tags", baseModel: "Auto / checkpoint default", safety: "SFW",
};

function harness({ submitDelay = 0 } = {}) {
    let now = 0;
    let sequence = 0;
    const timers = new Map();
    const storage = new Map();
    const nodes = new Map();
    const requests = [];
    const cancellations = [];
    const submissions = [];
    const interrupts = [];
    class Element {
        constructor(value = "") {
            this.value = value;
            this.dataset = {};
            this.listeners = new Map();
            this.style = { display: "block" };
            this.disabled = false;
            this.innerHTML = "";
            this.textContent = "";
        }
        matches(selector) { return selector === "button" && this.button; }
        querySelector() { return this.child || null; }
        querySelectorAll() { return []; }
        addEventListener(type, callback) {
            const callbacks = this.listeners.get(type) || [];
            callbacks.push(callback);
            this.listeners.set(type, callbacks);
        }
        dispatchEvent(event) {
            for (const callback of this.listeners.get(event.type) || []) callback(event);
        }
        click() {
            const event = { type: "click", prevented: false, preventDefault() { this.prevented = true; }, stopImmediatePropagation() {} };
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
    function finish(slot = "txt2img") {
        storage.delete(`${slot}_task_id`);
        nodes.get(`${slot}_generate`).disabled = false;
        nodes.get(`${slot}_interrupt`).style.display = "none";
        nodes.get(`${slot}_status`).textContent = `completed ${submissions.length}`;
    }
    add("llm_prompt_studio_auto_loop_log");
    add("llm_prompt_studio_server_queue_status");
    add("llm_prompt_studio_server_queue_log");
    for (const slot of ["txt2img", "img2img"]) {
        add(`${slot}_prompt`, new Element("fixed subject"));
        add(`${slot}_status`);
        add(`llm_prompt_studio_${slot}_inline_loop_status`);
        const start = add(`llm_prompt_studio_${slot}_inline_loop_start`);
        const stop = add(`llm_prompt_studio_${slot}_inline_loop_stop`);
        start.button = stop.button = true;
        const generate = add(`${slot}_generate`);
        generate.button = true;
        const submit = () => {
            submissions.push({ slot, prompt: nodes.get(`${slot}_prompt`).child.value });
            storage.set(`${slot}_task_id`, `task-${submissions.length}`);
            generate.disabled = true;
            nodes.get(`${slot}_interrupt`).style.display = "block";
        };
        generate.onClick = () => {
            if (submitDelay === null) return;
            if (submitDelay) window.setTimeout(submit, submitDelay);
            else submit();
        };
        const interrupt = add(`${slot}_interrupt`);
        interrupt.button = true;
        interrupt.style.display = "none";
        interrupt.onClick = () => { interrupts.push(slot); finish(slot); };
    }
    const root = {
        querySelector(selector) { return nodes.get(selector.replace(/^#/, "")) || null; },
        querySelectorAll() { return []; },
    };
    const window = {
        AbortController, addEventListener() {},
        performance: { now: () => now },
        getComputedStyle: (element) => element.style,
        localStorage: {
            getItem: (key) => storage.get(key) ?? null,
            setItem: (key, value) => storage.set(key, String(value)),
            removeItem: (key) => storage.delete(key),
        },
        setTimeout(callback, delay = 0) {
            const id = ++sequence;
            timers.set(id, { callback, time: now + delay });
            return id;
        },
        clearTimeout: (id) => timers.delete(id),
        fetch(url, options = {}) {
            const body = options.body ? JSON.parse(options.body) : {};
            if (url.endsWith("inline-cancel")) {
                cancellations.push(body);
                return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
            }
            assert.ok(url.includes("/v1/queue") || url.endsWith("inline-generate") || url.endsWith("auto-loop-generate") || /\/(?:processed-)?cache\?/.test(url), `unexpected endpoint ${url}`);
            // Keep promises pending after abort to exercise late network results as well.
            return new Promise((resolve, reject) => requests.push({
                url, body, signal: options.signal, reject,
                json(data) { resolve({ ok: true, status: 200, json: async () => data }); },
                records(records) {
                    const params = new URL(url, "http://localhost").searchParams;
                    const after = Number(params.get("after_id") || 0);
                    const limit = Number(params.get("limit") || 100);
                    const page = records.map((record, index) => ({ id: index + 1, ...record }))
                        .filter(record => record.id > after && String(record.prompt || "").trim()).slice(0, limit);
                    resolve({ ok: true, status: 200, json: async () => ({ records: page }) });
                },
                resolve(prompt) { resolve({ ok: true, status: 200, json: async () => ({ prompt }) }); },
                fail(detail) { resolve({ ok: false, status: 500, json: async () => ({ detail }) }); },
            }));
        },
    };
    const context = vm.createContext({
        window, document: { ...root, hidden: false }, gradioApp: () => root,
        HTMLTextAreaElement: Element, HTMLInputElement: Element, Event,
        console, Date, Set, Map,
    });
    vm.runInContext(source, context);
    async function flush() {
        for (let i = 0; i < 40; i += 1) await Promise.resolve();
    }
    async function advance(milliseconds = 0) {
        await flush();
        const end = now + milliseconds;
        for (let iteration = 0; iteration < 1000; iteration += 1) {
            const entry = [...timers.entries()].filter(([, timer]) => timer.time <= end)
                .sort((a, b) => a[1].time - b[1].time)[0];
            if (!entry) { now = end; await flush(); return; }
            const [id, timer] = entry;
            timers.delete(id);
            now = timer.time;
            timer.callback();
            await flush();
        }
        throw new Error("timer loop did not settle");
    }
    return { api: window.llmPromptStudioAutoLoop, requests, cancellations, submissions, interrupts,
        nodes, storage, advance, flush, finish,
        prompt: (slot = "txt2img") => nodes.get(`${slot}_prompt`).child.value,
        status: (slot = "txt2img") => nodes.get(`llm_prompt_studio_${slot}_inline_loop_status`).innerHTML,
    };
}

test("start returns immediately and duplicate start cannot launch another request", async () => {
    const h = harness();
    const result = h.api.startInlineLoop(config);
    assert.equal(typeof result?.then, "undefined", "start must not hold a Gradio event open");
    h.api.startInlineLoop(config);
    await h.advance();
    assert.equal(h.requests.length, 1);
    assert.equal(h.submissions.length, 0);
    h.api.cancelInline("txt2img");
});

test("each Forge round uses a fresh prompt and waits for completion while visible text stays fixed", async () => {
    const h = harness();
    h.api.startInlineLoop(config);
    await h.advance();
    h.requests[0].resolve("mountain scenery");
    await h.advance();
    assert.deepEqual(h.submissions, [{ slot: "txt2img", prompt: "fixed subject, mountain scenery" }]);
    assert.equal(h.prompt(), "fixed subject");
    assert.equal(h.requests.length, 2, "next prompt can be prefetched during Forge work");
    h.requests[1].resolve("seaside scenery");
    await h.advance(750);
    assert.equal(h.submissions.length, 1, "prepared prompt must wait for GPU completion");
    h.finish();
    await h.advance(250);
    assert.equal(h.submissions.length, 2);
    assert.equal(h.submissions[1].prompt, "fixed subject, seaside scenery");
    assert.equal(h.prompt(), "fixed subject");
    h.api.cancelInline("txt2img");
});

test("stop aborts the matching pending request and ignores its late result after restart", async () => {
    const h = harness();
    h.api.startInlineLoop(config);
    await h.advance();
    const old = h.requests[0];
    h.api.cancelInline("txt2img");
    assert.equal(old.signal.aborted, true);
    assert.equal(h.cancellations[0].request_id, old.body.request_id);
    assert.equal(h.cancellations[0].slot, "txt2img");
    assert.equal(h.interrupts.length, 0);
    h.api.startInlineLoop(config);
    await h.advance();
    old.resolve("obsolete scenery");
    await h.advance();
    assert.equal(h.submissions.length, 0);
    h.requests[1].resolve("new scenery");
    await h.advance();
    assert.equal(h.submissions.length, 1);
    assert.equal(h.submissions[0].prompt, "fixed subject, new scenery");
    h.api.cancelInline("txt2img");
});

test("stop interrupts owned Forge work and native Generate afterward does not invoke LLM", async () => {
    const h = harness();
    h.api.startInlineLoop(config);
    await h.advance();
    h.requests[0].resolve("forest scenery");
    await h.advance();
    h.api.cancelInline("txt2img");
    assert.deepEqual(h.interrupts, ["txt2img"]);
    const count = h.requests.length;
    await h.advance(1000);
    h.nodes.get("txt2img_generate").click();
    await h.advance();
    assert.equal(h.requests.length, count);
    assert.equal(h.submissions.at(-1).prompt, "fixed subject");
});

test("stop never interrupts a Forge task belonging to another owner", async () => {
    const h = harness();
    h.api.startInlineLoop(config);
    await h.advance();
    h.requests[0].resolve("forest scenery");
    await h.advance();
    h.storage.set("txt2img_task_id", "external-task");
    h.api.cancelInline("txt2img");
    await h.advance(250);
    assert.equal(h.interrupts.length, 0);
});

test("API failure stops repetition and a new button start can recover", async () => {
    const h = harness();
    h.api.startInlineLoop(config);
    await h.advance();
    h.requests[0].fail("provider unavailable");
    await h.advance(1000);
    assert.match(h.status(), /provider unavailable/);
    assert.equal(h.requests.length, 1);
    assert.equal(h.submissions.length, 0);
    h.api.startInlineLoop(config);
    await h.advance();
    assert.equal(h.requests.length, 2);
    h.requests[1].resolve("recovered scenery");
    await h.advance();
    assert.equal(h.submissions.length, 1);
    h.api.cancelInline("txt2img");
});

const rejectedCandidates = [
    ["non-English output", "LLM 输出不是英文。请检查模型的语言指令或更换模型后重试。"],
    ["SFW rejection", "SFW 校验拦截了成人内容。请修改要求，或明确切换为 NSFW 模式。"],
];

for (const [reason, detail] of rejectedCandidates) {
    test(`${reason} discards consecutive candidates and keeps retrying with a delay`, async () => {
        const h = harness();
        h.api.startInlineLoop(config);
        await h.advance();
        const requestIds = new Set();
        for (let rejected = 0; rejected < 5; rejected += 1) {
            const pending = h.requests.at(-1);
            assert.equal(requestIds.has(pending.body.request_id), false, "each candidate needs a fresh request");
            requestIds.add(pending.body.request_id);
            assert.equal(pending.body.safety, "SFW", "recovery must preserve the selected safety mode");
            const requestCount = h.requests.length;
            pending.fail(detail);
            await h.advance(499);
            assert.equal(h.requests.length, requestCount, "invalid output must not trigger a tight request loop");
            assert.equal(h.submissions.length, 0, "a rejected candidate cannot reach Forge");
            await h.advance(60000);
            assert.equal(h.requests.length, requestCount + 1, "candidate rejection must automatically request a replacement");
            h.api.startInlineLoop(config);
            await h.advance();
            assert.equal(h.requests.length, requestCount + 1, "retrying must retain ownership and reject duplicate starts");
        }
        h.requests.at(-1).resolve("valid replacement scenery");
        await h.advance();
        assert.deepEqual(h.submissions, [{ slot: "txt2img", prompt: "fixed subject, valid replacement scenery" }]);
        assert.equal(h.prompt(), "fixed subject");
        h.api.cancelInline("txt2img");
    });

    test(`${reason} during prefetch preserves Forge work and continues with a replacement`, async () => {
        const h = harness();
        h.api.startInlineLoop(config);
        await h.advance();
        h.requests[0].resolve("first valid scenery");
        await h.advance();
        assert.equal(h.submissions.length, 1);
        const failedPrefetch = h.requests.at(-1);
        failedPrefetch.fail(detail);
        await h.advance(499);
        assert.equal(h.requests.length, 2, "prefetch recovery must also wait before retrying");
        assert.deepEqual(h.interrupts, [], "a rejected future candidate must not interrupt current Forge work");
        h.finish();
        await h.advance(60000);
        assert.equal(h.requests.length, 3, "failed prefetch must be replaced automatically");
        assert.notEqual(h.requests.at(-1).body.request_id, failedPrefetch.body.request_id);
        h.requests.at(-1).resolve("second valid scenery");
        await h.advance(500);
        assert.equal(h.submissions.length, 2);
        assert.equal(h.submissions[1].prompt, "fixed subject, second valid scenery");
        assert.equal(h.prompt(), "fixed subject");
        h.api.cancelInline("txt2img");
    });
}

test("Stop during candidate retry backoff prevents further requests and allows restart", async () => {
    const h = harness();
    h.api.startInlineLoop(config);
    await h.advance();
    h.requests[0].fail(rejectedCandidates[0][1]);
    await h.advance(100);
    h.api.cancelInline("txt2img");
    await h.advance(60000);
    assert.equal(h.requests.length, 1, "a cancelled backoff must never launch another request");
    assert.equal(h.submissions.length, 0);
    h.api.startInlineLoop(config);
    await h.advance();
    assert.equal(h.requests.length, 2, "restart must acquire the released loop slot");
    h.requests[1].resolve("scenery after restart");
    await h.advance();
    assert.equal(h.submissions.length, 1);
    assert.equal(h.submissions[0].prompt, "fixed subject, scenery after restart");
    h.api.cancelInline("txt2img");
});

test("continuous batch skips a rejected request while preserving earlier and later valid rows", async () => {
    const h = harness();
    const completed = h.api.generateAndRun({ ...config, continuous: true, cycles: 1, promptOnly: true,
        request: "first request\nrejected request\nlast request" });
    await h.advance();
    h.requests[0].resolve("first accepted scenery");
    await h.advance();
    let rejected = 0;
    while (h.requests.at(-1).body.request === "rejected request" && rejected < 10) {
        h.requests.at(-1).fail(rejectedCandidates[0][1]);
        await h.advance(60000);
        rejected += 1;
    }
    assert.ok(rejected > 1, "the failed request should be retried before moving on");
    assert.equal(h.requests.at(-1).body.request, "last request", "one failed request cannot block the rest of its batch");
    h.requests.at(-1).resolve("last accepted scenery");
    await h.advance();
    assert.match(await completed, /持续 Prompt 生成完成/);
    // This queue now lives in memory; assert the user-visible retained results.
    const queue = h.nodes.get("llm_prompt_studio_auto_loop_log").innerHTML;
    assert.match(queue, /<code>first accepted scenery<\/code>/);
    assert.match(queue, /<code>last accepted scenery<\/code>/);
    assert.match(queue, /队列 2 条/);
});

test("empty continuous batches advance to fresh rounds until Stop cancels retry backoff", async () => {
    const h = harness();
    const completed = h.api.generateAndRun({ ...config, continuous: true, cycles: 0, promptOnly: true });
    await h.advance();
    for (let rejected = 0; rejected < 7; rejected += 1) {
        const count = h.requests.length;
        h.requests.at(-1).fail(rejectedCandidates[0][1]);
        await h.advance(499);
        assert.equal(h.requests.length, count, "empty rounds must retain a retry delay");
        await h.advance(60000);
        assert.equal(h.requests.length, count + 1, "an empty round cannot terminate continuous generation");
    }
    const count = h.requests.length;
    h.requests.at(-1).fail(rejectedCandidates[0][1]);
    await h.advance(100);
    h.api.cancel();
    await h.advance(60000);
    await completed;
    assert.equal(h.requests.length, count, "Stop must prevent another round after the pending delay");
    assert.equal(h.submissions.length, 0);
});

test("Stop between empty rounds resolves cancellation and releases the batch runner", async () => {
    const h = harness();
    const completed = h.api.generateAndRun({ ...config, continuous: true, cycles: 0, promptOnly: true });
    await h.advance();
    for (const delay of [500, 1000, 2000]) {
        h.requests.at(-1).fail(rejectedCandidates[0][1]);
        await h.advance(delay);
    }
    const count = h.requests.length;
    h.api.cancel();
    await h.advance(500);
    assert.equal(await completed, "已取消");
    assert.equal(h.requests.length, count);
    const restarted = h.api.generateAndRun({ ...config, continuous: true, cycles: 1, promptOnly: true });
    await h.advance();
    assert.equal(h.requests.length, count + 1);
    h.requests.at(-1).resolve("scenery after batch restart");
    await h.advance();
    assert.match(await restarted, /持续 Prompt 生成完成/);
});

test("a late error from a stopped run cannot cancel the restarted run", async () => {
    const h = harness();
    h.api.startInlineLoop(config);
    await h.advance();
    h.api.cancelInline("txt2img");
    h.api.startInlineLoop(config);
    await h.advance();
    h.requests[0].reject(new Error("old network failure"));
    await h.advance();
    assert.equal(h.requests[1].signal.aborted, false);
    h.requests[1].resolve("current scenery");
    await h.advance();
    assert.equal(h.submissions.length, 1);
    h.api.cancelInline("txt2img");
});

test("native Forge Generate has no extension interceptor installed", () => {
    const h = harness();
    for (const slot of ["txt2img", "img2img"]) {
        assert.equal(h.nodes.get(`${slot}_generate`).listeners.get("click")?.length || 0, 0);
        h.nodes.get(`${slot}_generate`).click();
    }
    assert.equal(h.requests.length, 0);
    assert.equal(h.submissions.length, 2);
});

test("stopping txt2img leaves the img2img loop running", async () => {
    const h = harness();
    h.api.startInlineLoop(config);
    h.api.startInlineLoop({ ...config, slot: "img2img" });
    await h.advance();
    assert.equal(h.requests.length, 2);
    const txtRequest = h.requests.find((request) => request.body.slot === "txt2img");
    const imgRequest = h.requests.find((request) => request.body.slot === "img2img");
    h.api.cancelInline("txt2img");
    assert.equal(txtRequest.signal.aborted, true);
    assert.equal(imgRequest.signal.aborted, false);
    imgRequest.resolve("separate scenery");
    await h.advance();
    assert.deepEqual(h.submissions, [{ slot: "img2img", prompt: "fixed subject, separate scenery" }]);
    assert.equal(h.prompt("img2img"), "fixed subject");
    h.api.cancelInline("img2img");
});

for (const slot of ["txt2img", "img2img"]) {
    test(`${slot} discards stale LLM output and restores the latest exact textarea contents`, async () => {
        const h = harness();
        h.api.startInlineLoop({ ...config, slot });
        await h.advance();
        const edited = "  revised subject,\n  detailed background  \n";
        h.nodes.get(`${slot}_prompt`).child.value = edited;
        h.requests[0].resolve("obsolete scenery");
        await h.advance();
        assert.equal(h.submissions.length, 0, "an old source must never be submitted");
        assert.equal(h.requests.length, 2);
        assert.equal(h.requests[1].body.source_tags, edited.trim());
        h.requests[1].resolve("current scenery");
        await h.advance();
        assert.equal(h.submissions.length, 1);
        assert.equal(h.submissions[0].slot, slot);
        assert.match(h.submissions[0].prompt, /revised subject/);
        assert.match(h.submissions[0].prompt, /current scenery/);
        assert.doesNotMatch(h.submissions[0].prompt, /obsolete scenery|fixed subject/);
        assert.equal(h.prompt(slot), edited, "restoring the prompt must retain exact whitespace");
        h.api.cancelInline(slot);
    });
}

test("native Forge Interrupt cancels prefetch and stops future rounds", async () => {
    const h = harness();
    h.api.startInlineLoop(config);
    await h.advance();
    h.requests[0].resolve("initial scenery");
    await h.advance();
    assert.equal(h.requests.length, 2);
    const pending = h.requests[1];
    h.nodes.get("txt2img_interrupt").click();
    assert.deepEqual(h.interrupts, ["txt2img"]);
    assert.equal(pending.signal.aborted, true);
    assert.equal(h.cancellations.at(-1).request_id, pending.body.request_id);
    pending.resolve("late scenery");
    await h.advance(1000);
    assert.equal(h.submissions.length, 1);
    assert.equal(h.requests.length, 2);
    assert.equal(h.prompt(), "fixed subject");
    h.api.startInlineLoop(config);
    await h.advance();
    assert.equal(h.requests.length, 3, "native interrupt must permit a new explicit start");
    h.api.cancelInline("txt2img");
});

test("deferred Gradio submission captures the generated prompt before restoration", async () => {
    const h = harness({ submitDelay: 16 });
    h.api.startInlineLoop(config);
    await h.advance();
    h.requests[0].resolve("deferred scenery");
    await h.advance();
    assert.equal(h.submissions.length, 0);
    await h.advance(25);
    assert.deepEqual(h.submissions, [{ slot: "txt2img", prompt: "fixed subject, deferred scenery" }]);
    assert.equal(h.prompt(), "fixed subject");
    assert.equal(h.requests.length, 2);
    h.api.cancelInline("txt2img");
    assert.deepEqual(h.interrupts, ["txt2img"]);
});

for (const [submitDelay, cancelAt] of [[16, 10], [16, 20], [60, 10]]) {
    test(`stop at ${cancelAt}ms interrupts a Forge task submitted at ${submitDelay}ms`, async () => {
        const h = harness({ submitDelay });
        h.api.startInlineLoop(config);
        await h.advance();
        h.requests[0].resolve("startup scenery");
        await h.advance(cancelAt);
        h.api.cancelInline("txt2img");
        await h.advance(100);
        assert.equal(h.prompt(), "fixed subject");
        assert.equal(h.requests.length, 1, "a stopped startup must not prefetch");
        assert.equal(h.submissions.length, 1, "the already dispatched Gradio click settles once");
        assert.deepEqual(h.interrupts, ["txt2img"], "stop must catch the task launched by its pending click");
        assert.equal(h.storage.get("txt2img_task_id"), undefined);
    });
}

for (const cancelDuringStartup of [false, true]) {
    test(`Forge launch timeout releases the panel${cancelDuringStartup ? " after stop" : ""}`, async () => {
        const h = harness({ submitDelay: null });
        h.api.startInlineLoop(config);
        await h.advance();
        h.requests[0].resolve("unsubmitted scenery");
        await h.advance(10);
        if (cancelDuringStartup) h.api.cancelInline("txt2img");
        h.api.startInlineLoop(config);
        await h.advance();
        assert.equal(h.requests.length, 1, "pending Forge dispatch must retain the panel slot");
        await h.advance(10000);
        assert.equal(h.prompt(), "fixed subject");
        assert.equal(h.submissions.length, 0);
        assert.equal(h.interrupts.length, 0);
        assert.equal(h.requests.length, 1, "launch failure must not restart itself");
        h.api.startInlineLoop(config);
        await h.advance();
        assert.equal(h.requests.length, 2, "watchdog expiry must permit a new explicit start");
        h.api.cancelInline("txt2img");
    });
}

for (const [sourceName, endpoint, label] of [
    ["原始缓存库", "/cache?", "原始缓存库"],
    ["处理结果库", "/processed-cache?", "处理结果库"],
]) {
    test(`${sourceName} writes one prompt without image generation or hidden queue`, async () => {
        const h = harness();
        const pending = h.api.inlineOnce({ ...config, source: sourceName });
        assert.ok(h.requests[0].url.includes(endpoint));
        h.requests[0].records([{ prompt: "first scenery" }, { prompt: "second scenery" }]);
        assert.match(await pending, new RegExp(label));
        assert.equal(h.prompt(), "fixed subject, first scenery");
        assert.equal(h.submissions.length, 0);
        assert.equal(h.requests.length, 1);
        assert.equal(h.storage.has("llm_prompt_studio_auto_loop_queue_v1"), false);
    });
    test(`${sourceName} late cancelled reads do not advance the cursor`, async () => {
        const h = harness();
        const pending = h.api.inlineOnce({ ...config, source: sourceName });
        h.api.cancelInline("txt2img");
        h.requests[0].records([{ prompt: "first scenery" }, { prompt: "second scenery" }]);
        assert.equal(await pending, "已取消");
        assert.equal(h.prompt(), "fixed subject");
        const retry = h.api.inlineOnce({ ...config, source: sourceName });
        h.requests[1].records([{ prompt: "first scenery" }, { prompt: "second scenery" }]);
        await retry;
        assert.equal(h.prompt(), "fixed subject, first scenery");
    });
    test(`${sourceName} empty library returns its own error`, async () => {
        const h = harness();
        const pending = h.api.inlineOnce({ ...config, source: sourceName });
        h.requests[0].records([]);
        assert.match(await pending, new RegExp(`${label}为空`));
        assert.equal(h.prompt(), "fixed subject");
        assert.equal(h.submissions.length, 0);
    });
}

test("cache libraries maintain independent sequential cursors", async () => {
    const h = harness();
    for (const [sourceName, expected] of [["cache", "raw one"], ["processed_cache", "processed one"], ["cache", "raw two"]]) {
        const pending = h.api.inlineOnce({ ...config, source: sourceName, writeMode: "replace" });
        h.requests.at(-1).records(sourceName === "cache" ? [{ prompt: "raw one" }, { prompt: "raw two" }] : [{ prompt: "processed one" }]);
        await pending;
        assert.equal(h.prompt(), expected);
    }
});

for (const sourceName of ["cache", "processed_cache"]) {
    test(`${sourceName} traverses more than 1000 rows with bounded cursor reads and wraps at the end`, async () => {
        const h = harness();
        const rows = Array.from({ length: 1205 }, (_, index) => ({ id: index * 3 + 2, prompt: `scene ${index}` }));
        let lastId = 0;
        for (const row of rows) {
            const pending = h.api.inlineOnce({ ...config, source: sourceName, writeMode: "replace" });
            const request = h.requests.at(-1);
            const params = new URL(request.url, "http://localhost").searchParams;
            assert.equal(params.get("after_id"), String(lastId));
            assert.equal(params.get("limit"), "1", "do not load the whole database for a single image");
            request.records(rows);
            await pending;
            assert.equal(h.prompt(), row.prompt);
            lastId = row.id;
        }
        const wrap = h.api.inlineOnce({ ...config, source: sourceName, writeMode: "replace" });
        h.requests.at(-1).records(rows);
        await h.flush();
        assert.equal(new URL(h.requests.at(-1).url, "http://localhost").searchParams.get("after_id"), "0");
        h.requests.at(-1).records(rows);
        await wrap;
        assert.equal(h.prompt(), rows[0].prompt);
        assert.equal(h.requests.length, rows.length + 2);
    });

    test(`${sourceName} skips deleted IDs, sees appended rows and handles an emptied library`, async () => {
        const h = harness();
        let rows = [{ id: 2, prompt: "first" }, { id: 4, prompt: "deleted next" }, { id: 7, prompt: "third" }];
        async function read(expected) {
            const pending = h.api.inlineOnce({ ...config, source: sourceName, writeMode: "replace" });
            h.requests.at(-1).records(rows);
            await pending;
            assert.equal(h.prompt(), expected);
        }
        await read("first");
        rows = rows.filter(row => row.id !== 4);
        await read("third");
        rows.push({ id: 12, prompt: "appended" });
        await read("appended");
        const empty = h.api.inlineOnce({ ...config, source: sourceName, writeMode: "replace" });
        h.requests.at(-1).records([]);
        await h.flush();
        h.requests.at(-1).records([]);
        assert.match(await empty, /为空/);
        assert.equal(h.prompt(), "appended");
    });

    test(`${sourceName} failed reads and cancelled wraparound do not advance the cursor`, async () => {
        const h = harness();
        const options = { ...config, source: sourceName, writeMode: "replace" };
        const first = h.api.inlineOnce(options);
        h.requests.at(-1).records([{ id: 5, prompt: "first" }]);
        await first;
        const failed = h.api.inlineOnce(options);
        h.requests.at(-1).fail("temporary read failure");
        assert.match(await failed, /temporary read failure/);
        const wrap = h.api.inlineOnce(options);
        assert.equal(new URL(h.requests.at(-1).url, "http://localhost").searchParams.get("after_id"), "5");
        h.requests.at(-1).records([]);
        await h.flush();
        h.api.cancelInline("txt2img");
        h.requests.at(-1).records([{ id: 2, prompt: "late wrapped result" }]);
        assert.equal(await wrap, "已取消");
        const retry = h.api.inlineOnce(options);
        assert.equal(new URL(h.requests.at(-1).url, "http://localhost").searchParams.get("after_id"), "5");
        h.requests.at(-1).records([{ id: 8, prompt: "fresh result" }]);
        await retry;
        assert.equal(h.prompt(), "fresh result");
    });
}

for (const sourceName of ["llm", "cache", "processed_cache"]) {
    test(`${sourceName} single write never overwrites user edits during a pending request`, async () => {
        const h = harness();
        const pending = h.api.inlineOnce({ ...config, source: sourceName });
        h.nodes.get("txt2img_prompt").child.value = "user edited subject";
        if (sourceName === "llm") h.requests[0].resolve("new scenery");
        else h.requests[0].records([{ prompt: "new scenery" }]);
        assert.match(await pending, /已被修改/);
        assert.equal(h.prompt(), "user edited subject");
        assert.equal(h.submissions.length, 0);
    });
}

for (const [mode, base, expected] of [
    ["append_end", "subject_token, <lora:sample:1>", "subject_token, <lora:sample:1>, new scenery"],
    ["append_start", "subject_token, <lora:sample:1>", "new scenery, subject_token, <lora:sample:1>"],
    ["marker", "subject_token, {{LLM}}, <lora:sample:1>", "subject_token, new scenery, <lora:sample:1>"],
    ["replace", "subject_token, <lora:sample:1>", "new scenery"],
]) {
    for (const continuous of [false, true]) {
        test(`${continuous ? "continuous" : "single"} write respects ${mode}`, async () => {
            const h = harness();
            h.nodes.get("txt2img_prompt").child.value = base;
            const pending = continuous ? h.api.startInlineLoop({ ...config, writeMode: mode }) : h.api.inlineOnce({ ...config, writeMode: mode });
            await h.advance();
            h.requests[0].resolve("new scenery");
            await h.advance();
            if (continuous) {
                assert.equal(h.submissions[0].prompt, expected);
                assert.equal(h.prompt(), base);
                h.api.cancelInline("txt2img");
            } else {
                await pending;
                assert.equal(h.prompt(), expected);
                assert.equal(h.submissions.length, 0);
            }
        });
    }
}

for (const continuous of [false, true]) {
    test(`${continuous ? "continuous" : "single"} marker mode rejects absent markers`, async () => {
        const h = harness();
        const pending = continuous ? h.api.startInlineLoop({ ...config, writeMode: "marker" }) : h.api.inlineOnce({ ...config, writeMode: "marker" });
        await h.advance();
        h.requests[0].resolve("new scenery");
        await h.advance();
        if (!continuous) assert.match(await pending, /没有插入标记/);
        assert.match(h.status(), /没有插入标记/);
        assert.equal(h.prompt(), "fixed subject");
        assert.equal(h.submissions.length, 0);
    });
}

for (const sourceName of ["cache", "processed_cache"]) {
    test(`${sourceName} continuous mode reads sequential rows and preserves visible prompt`, async () => {
        const h = harness();
        h.api.startInlineLoop({ ...config, source: sourceName });
        await h.advance();
        const rows = [{ prompt: "first scenery" }, { prompt: "second scenery" }];
        h.requests[0].records(rows);
        await h.advance();
        assert.equal(h.submissions[0].prompt, "fixed subject, first scenery");
        assert.equal(h.prompt(), "fixed subject");
        h.requests[1].records(rows);
        await h.advance(750);
        assert.equal(h.submissions.length, 1);
        h.finish();
        await h.advance(250);
        assert.equal(h.submissions[1].prompt, "fixed subject, second scenery");
        assert.equal(h.prompt(), "fixed subject");
        assert.ok(h.requests.every(request => !request.url.includes("inline-generate")));
        h.api.cancelInline("txt2img");
    });
}


test("cache destination saves every generated result without changing native prompt or starting images", async () => {
    const h = harness();
    const pending = h.api.inlineOnce({ ...config, destination: "cache", count: 2 });
    assert.equal(h.requests[0].body.cache_result, true);
    h.requests[0].resolve("forest");
    await h.flush();
    assert.equal(h.requests[1].body.cache_result, true);
    assert.equal(h.requests[1].body.source_tags, "fixed subject");
    h.requests[1].resolve("beach");
    assert.match(await pending, /2.*未启动生图/);
    assert.equal(h.prompt(), "fixed subject");
    assert.equal(h.submissions.length, 0);
    assert.equal(h.requests.length, 2);
});

test("zero count caches indefinitely until cancel with visible completed count", async () => {
    const h = harness();
    const pending = h.api.inlineOnce({ ...config, destination: "cache", count: 0 });
    for (let i = 0; i < 3; i += 1) {
        h.requests[i].resolve(`scene ${i}`);
        await h.flush();
    }
    assert.equal(h.requests.length, 4);
    assert.match(h.status(), /无限/);
    assert.match(h.status(), /已完成 3/);
    h.api.cancelInline("txt2img");
    h.requests[3].resolve("late");
    assert.equal(await pending, "已取消");
    assert.match(h.status(), /已完成 3/);
    assert.equal(h.submissions.length, 0);
    assert.equal(h.prompt(), "fixed subject");
});

for (const count of [-1, "", null, "invalid", Infinity, 0.5]) {
    test(`invalid count ${String(count)} does not become unlimited`, async () => {
        const h = harness();
        const pending = h.api.inlineOnce({ ...config, destination: "cache", count });
        h.requests[0].resolve("scene");
        await pending;
        assert.equal(h.requests.length, 1);
    });
}

for (const sourceName of ["llm", "cache", "processed_cache"]) {
    test(`zero count queues ${sourceName} sequentially without unbounded pending jobs`, async () => {
        const h = harness();
        const pending = h.api.inlineOnce({ ...config, source: sourceName, destination: "queue", count: "0" });
        if (sourceName === "llm") h.requests[0].resolve("scene");
        else h.requests[0].records([{ prompt: "scene" }]);
        await h.flush();
        assert.deepEqual(h.requests[1].body.requests, ["fixed subject, scene"]);
        h.requests[1].json({ batch_id: "first", counts: { pending: 1 }, jobs: [{ status: "pending" }] });
        await h.flush();
        assert.equal(h.requests.length, 2);
        assert.match(h.status(), /无限/);
        await h.advance(1000);
        h.requests[2].json({ counts: { completed: 1 }, jobs: [{ status: "completed" }] });
        await h.flush();
        assert.equal(h.requests.length, 4);
        assert.match(h.status(), /已完成 1/);
        h.api.cancelInline("txt2img");
        if (sourceName === "llm") h.requests[3].resolve("late");
        else h.requests[3].records([{ prompt: "late" }]);
        assert.equal(await pending, "已取消");
        assert.equal(h.requests.length, 4, "completed queue is not cancelled again");
        assert.equal(h.submissions.length, 0);
    });
}

for (const sourceName of ["cache", "processed_cache"]) {
    test(`unlimited ${sourceName} stops when source becomes empty`, async () => {
        const h = harness();
        const pending = h.api.inlineOnce({ ...config, source: sourceName, destination: "queue", count: 0 });
        h.requests[0].records([]);
        assert.match(await pending, /为空/);
        assert.equal(h.requests.length, 1);
    });
}

test("unlimited queue cancellation targets the current cycle only", async () => {
    const h = harness();
    const pending = h.api.inlineOnce({ ...config, destination: "queue", count: 0 });
    h.requests[0].resolve("first");
    await h.flush();
    h.requests[1].json({ batch_id: "done", counts: { completed: 1 }, jobs: [{ status: "completed" }] });
    await h.flush();
    h.requests[2].resolve("second");
    await h.flush();
    h.api.cancelInline("txt2img");
    h.requests[3].json({ batch_id: "current", counts: { pending: 1 }, jobs: [{ status: "pending" }] });
    await h.flush();
    assert.ok(h.requests[4].url.endsWith("/queue/current/cancel"));
    h.requests[4].json({});
    assert.equal(await pending, "已取消");
    assert.equal(h.requests.length, 5);
    assert.match(h.status(), /已完成 1/);
});

for (const outcome of ["error", "cancelled", "missing"]) {
    test(`unlimited queue stops after ${outcome} without counting it as completed`, async () => {
        const h = harness();
        const pending = h.api.inlineOnce({ ...config, destination: "queue", count: 0 });
        h.requests[0].resolve("scene");
        await h.flush();
        h.requests[1].json({ batch_id: "failed-cycle", counts: { pending: 1 }, jobs: [{ status: "pending" }] });
        await h.advance(1000);
        h.requests[2].json({
            counts: outcome === "missing" ? {} : { [outcome]: 1 },
            jobs: outcome === "missing" ? [] : [{ status: outcome }],
        });
        await h.flush();
        assert.match(h.status(), /操作已停止/);
        assert.match(h.status(), /已完成 0/);
        assert.match(await pending, outcome === "error" ? /失败/ : outcome === "cancelled" ? /取消/ : /不存在|消失/);
        assert.equal(h.requests.length, 3, "failed cycle must not request another prompt");
    });
}

test("queue destination submits composed prompts and renders images beside inline controls", async () => {
    const h = harness();
    const pending = h.api.inlineOnce({ ...config, destination: "queue", count: 2 });
    h.requests[0].resolve("forest");
    await h.flush();
    h.requests[1].resolve("beach");
    await h.flush();
    const enqueue = h.requests[2];
    assert.ok(enqueue.url.endsWith("/v1/queue"));
    assert.deepEqual(enqueue.body.requests, ["fixed subject, forest", "fixed subject, beach"]);
    assert.equal(enqueue.body.config.direct_prompt, true);
    assert.equal(enqueue.body.config.cache_result, false, "LLM results were cached already");
    assert.equal(enqueue.body.target, "txt2img");
    assert.ok(enqueue.body.config.generation_settings);
    enqueue.json({ batch_id: "own-batch", status: "已完成 2", counts: { completed: 2 }, jobs: [
        { position: 1, status: "completed", prompt: "fixed subject, forest", images: ["aGVsbG8="] },
        { position: 2, status: "completed", prompt: "fixed subject, beach" },
    ] });
    await pending;
    assert.equal(h.prompt(), "fixed subject");
    assert.equal(h.submissions.length, 0);
    assert.match(h.status(), /fixed subject, forest/);
    assert.match(h.status(), /data:image\/png;base64,aGVsbG8=/);
});

test("cancel during queue submission cancels only the returned batch and never changes native prompt", async () => {
    const h = harness();
    const pending = h.api.inlineOnce({ ...config, destination: "queue" });
    h.requests[0].resolve("forest");
    await h.flush();
    h.api.cancelInline("txt2img");
    h.requests[1].json({ batch_id: "late-owned-batch", jobs: [] });
    await h.flush();
    assert.ok(h.requests[2].url.endsWith("/queue/late-owned-batch/cancel"));
    h.requests[2].json({});
    assert.equal(await pending, "已取消");
    assert.equal(h.prompt(), "fixed subject");
    assert.equal(h.interrupts.length, 0);
});

test("cancel cache generation preserves completed cache entries and does not enqueue late results", async () => {
    const h = harness();
    const pending = h.api.inlineOnce({ ...config, destination: "cache", count: 3 });
    h.requests[0].resolve("forest");
    await h.flush();
    h.api.cancelInline("txt2img");
    assert.equal(h.requests[1].signal.aborted, true);
    h.requests[1].resolve("late beach");
    assert.equal(await pending, "已取消");
    assert.equal(h.requests.length, 2);
    assert.equal(h.prompt(), "fixed subject");
});


test("queue cancellation while polling targets only its batch and ignores late progress", async () => {
    const h = harness();
    const pending = h.api.inlineOnce({ ...config, destination: "queue" });
    h.requests[0].resolve("forest");
    await h.flush();
    h.requests[1].json({ batch_id: "only-owned", status: "处理中", counts: { pending: 1 }, jobs: [{ status: "pending" }] });
    await h.advance(1000);
    const poll = h.requests[2];
    assert.ok(poll.url.endsWith("/queue/only-owned"));
    h.api.cancelInline("txt2img");
    assert.ok(h.requests[3].url.endsWith("/queue/only-owned/cancel"));
    h.requests[3].json({});
    poll.json({ status: "obsolete progress", counts: { completed: 1 }, jobs: [{ status: "completed" }] });
    assert.equal(await pending, "已取消");
    assert.doesNotMatch(h.status(), /obsolete progress/);
    assert.equal(h.interrupts.length, 0);
});

test("cache-only ignores prompt insertion markers and rejects non-LLM cache copying", async () => {
    const h = harness();
    const pending = h.api.inlineOnce({ ...config, destination: "cache", writeMode: "marker" });
    h.requests[0].resolve("forest");
    assert.match(await pending, /已保存到缓存/);
    assert.match(await h.api.inlineOnce({ ...config, source: "processed_cache", destination: "cache" }), /仅存缓存请使用/);
    assert.equal(h.requests.length, 1);
});


for (const lateFailure of [false, true]) {
    test(`Studio queue watcher ignores superseded ${lateFailure ? "errors" : "snapshots"} without cancelling jobs`, async () => {
        const h = harness();
        const old = h.api.watchServerQueue("old-batch");
        const current = h.api.watchServerQueue("new-batch");
        h.requests[1].json({ status: "new complete", counts: { completed: 1 }, jobs: [{ prompt: "new result" }] });
        await current;
        if (lateFailure) h.requests[0].fail("obsolete error");
        else h.requests[0].json({ status: "old complete", counts: { completed: 1 }, jobs: [{ prompt: "old result" }] });
        await old;
        assert.equal(h.nodes.get("llm_prompt_studio_server_queue_status").innerHTML, "new complete");
        assert.match(h.nodes.get("llm_prompt_studio_server_queue_log").innerHTML, /new result/);
        assert.equal(h.requests.length, 2);
        assert.equal(h.cancellations.length, 0);
    });
}
