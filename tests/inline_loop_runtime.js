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
            assert.ok(url.endsWith("inline-generate") || url.endsWith("auto-loop-generate"), `unexpected endpoint ${url}`);
            // Keep promises pending after abort to exercise late network results as well.
            return new Promise((resolve, reject) => requests.push({
                body, signal: options.signal, reject,
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
    const saved = JSON.parse(h.storage.get("llm_prompt_studio_auto_loop_queue_v1"));
    assert.deepEqual(saved.rows.map((row) => row.prompt), ["first accepted scenery", "last accepted scenery"]);
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
