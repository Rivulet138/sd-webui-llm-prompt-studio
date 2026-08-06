const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SCRIPT = fs.readFileSync(
    path.join(__dirname, "..", "javascript", "llm_prompt_studio_auto_loop.js"),
    "utf8",
);
const QUEUE_KEY = "llm_prompt_studio_auto_loop_queue_v1";

class FakeInput {
    constructor(value = "") {
        this.value = value;
    }

    dispatchEvent() {}
    focus() {}
}

function createRuntime(initialQueue = [], options = {}) {
    const values = options.values || new Map([[QUEUE_KEY, JSON.stringify(initialQueue)]]);
    let injectedForeignLease = false;
    const localStorage = {
        getItem(key) {
            if (options.throwOnGet) throw new Error("read denied");
            return values.has(key) ? values.get(key) : null;
        },
        setItem(key, value) {
            if (options.throwOnSet) throw new Error("quota exceeded");
            values.set(key, String(value));
            const written = JSON.parse(String(value));
            if (options.foreignLeaseAfterSet && written.activeOwner && !injectedForeignLease) {
                injectedForeignLease = true;
                values.set(key, JSON.stringify({
                    ...written,
                    activeOwner: "foreign-tab",
                    activeUntil: Date.now() + 60_000,
                }));
            }
        },
    };
    const inputs = {
        llm_prompt_studio_request: new FakeInput(),
        llm_prompt_studio_source_tags: new FakeInput(),
        llm_prompt_studio_output: new FakeInput(),
        llm_prompt_studio_txt2img_inline_request: new FakeInput(),
        llm_prompt_studio_txt2img_inline_output: new FakeInput(),
        llm_prompt_studio_txt2img_inline_cache_output: new FakeInput(),
        llm_prompt_studio_txt2img_inline_cache_status: new FakeInput("idle"),
        txt2img_prompt: new FakeInput("base"),
        img2img_prompt: new FakeInput("img base"),
    };
    const logHost = { innerHTML: "" };
    const autoStatusHost = { innerHTML: "" };
    const studioStatusHost = { textContent: "idle" };
    const inlineStatusHost = { textContent: "idle" };
    const forgeStatusHost = { textContent: "idle" };
    const gallery = { innerHTML: "old" };
    const generalButton = { disabled: false, matches: () => true };
    let dispatchClicks = 0;
    let generateClicks = 0;
    const promptHistory = [];
    let interruptClicks = 0;
    const generated = Array.isArray(options.generated) ? [...options.generated] : [options.generated || "new prompt"];
    const inlineGenerated = Array.isArray(options.inlineGenerated) ? [...options.inlineGenerated] : [options.inlineGenerated || "inline prompt"];
    const forgeFailures = Array.isArray(options.forgeFailures) ? [...options.forgeFailures] : [];
    const dispatchButton = {
        disabled: false,
        matches: () => true,
        click() {
            dispatchClicks += 1;
            this.disabled = true;
            const output = generated.shift() || "new prompt";
            setTimeout(() => {
                if (typeof output === "object" && output.error) {
                    inputs.llm_prompt_studio_output.value = "";
                    studioStatusHost.textContent = output.error;
                } else {
                    inputs.llm_prompt_studio_output.value = output;
                    studioStatusHost.textContent = "completed";
                }
                this.disabled = false;
            }, options.llmDelay || 10);
        },
    };
    const inlineGenerate = {
        disabled: false,
        matches: () => true,
        click() {
            this.disabled = true;
            const output = inlineGenerated.shift() || "inline prompt";
            setTimeout(() => {
                if (typeof output === "object" && output.error) {
                    inputs.llm_prompt_studio_txt2img_inline_output.value = "";
                    inlineStatusHost.textContent = output.error;
                } else {
                    inputs.llm_prompt_studio_txt2img_inline_output.value = output;
                    inlineStatusHost.textContent = "completed";
                }
                this.disabled = false;
            }, options.inlineDelay || 10);
        },
    };
    const forgeGenerate = {
        disabled: false,
        matches: () => true,
        click() {
            generateClicks += 1;
            promptHistory.push(inputs.txt2img_prompt.value);
            this.disabled = true;
            setTimeout(() => {
                const transientFailure = forgeFailures.shift();
                const failure = transientFailure || (options.forgeFailure ? "Error: generation failed" : "");
                forgeStatusHost.textContent = failure || "completed";
                if (!failure && !options.keepGalleryUnchanged) gallery.innerHTML = `image-${generateClicks}`;
                this.disabled = false;
            }, options.forgeDelay || 10);
        },
    };
    const interrupt = {
        offsetParent: {},
        matches: () => true,
        click() {
            interruptClicks += 1;
            forgeGenerate.disabled = false;
        },
    };
    const hosts = {
        llm_prompt_studio_auto_loop_log: logHost,
        llm_prompt_studio_auto_loop_status: autoStatusHost,
        llm_prompt_studio_status: studioStatusHost,
        llm_prompt_studio_generate_button: generalButton,
        llm_prompt_studio_auto_loop_dispatch: dispatchButton,
        llm_prompt_studio_txt2img_inline_generate: inlineGenerate,
        llm_prompt_studio_txt2img_inline_status: inlineStatusHost,
        txt2img_generate: forgeGenerate,
        txt2img_interrupt: interrupt,
        txt2img_gallery: gallery,
        txt2img_status: forgeStatusHost,
    };
    for (const [id, field] of Object.entries(inputs)) {
        hosts[id] = { querySelector: () => field };
    }
    const document = {
        querySelector(selector) {
            const prompt = selector.match(/^#(txt2img|img2img)_prompt textarea/);
            if (prompt) return inputs[`${prompt[1]}_prompt`];
            const exactId = selector.match(/^#([A-Za-z0-9_-]+)$/);
            return exactId ? hosts[exactId[1]] || null : null;
        },
    };
    const context = {
        console,
        document,
        Event: class Event {},
        HTMLInputElement: FakeInput,
        HTMLTextAreaElement: FakeInput,
        setTimeout,
        clearTimeout,
        Date,
    };
    context.navigator = options.noWebLocks ? {} : {
        locks: {
            request(_name, _settings, callback) {
                return Promise.resolve(callback({ name: "test-lock" }));
            },
        },
    };
    context.window = context;
    context.localStorage = localStorage;
    context.getComputedStyle = (element) => ({
        display: element === interrupt && !forgeGenerate.disabled ? "none" : "block",
        visibility: "visible",
    });
    vm.runInNewContext(SCRIPT, context, { filename: "llm_prompt_studio_auto_loop.js" });
    return {
        api: context.llmPromptStudioAutoLoop,
        autoStatusHost,
        logHost,
        values,
        get dispatchClicks() { return dispatchClicks; },
        get generateClicks() { return generateClicks; },
        get interruptClicks() { return interruptClicks; },
        promptValue(target = "txt2img") { return inputs[`${target}_prompt`].value; },
        promptHistory() { return [...promptHistory]; },
        queue() {
            const raw = values.get(QUEUE_KEY);
            const stored = raw ? JSON.parse(raw) : [];
            return Array.isArray(stored) ? stored : stored.rows;
        },
    };
}

async function main() {
    const migrated = createRuntime([
        { index: 4, prompt: "  same   prompt ", status: "pending", requestId: "same request" },
        { index: 8, prompt: "same prompt", status: "completed", requestId: "same request" },
        { index: 9, prompt: "   ", status: "pending" },
        { index: 10, prompt: "unique", status: "running" },
    ]);
    assert.deepEqual(migrated.queue().map(({ index, prompt, status, requestId }) => ({ index, prompt, status, requestId })), [
        { index: 1, prompt: "same   prompt", status: "completed", requestId: "same request" },
        { index: 2, prompt: "unique", status: "pending", requestId: "" },
    ]);

    const versionTwoValues = new Map([[QUEUE_KEY, JSON.stringify({
        version: 2,
        rows: [
            { index: 1, id: "cycle-one", prompt: "same generated output", status: "completed", requestId: "cycle" },
            { index: 2, id: "cycle-two", prompt: "same generated output", status: "completed", requestId: "cycle" },
        ],
        requestIds: ["cycle"],
    })]]);
    const versionTwo = createRuntime([], { values: versionTwoValues });
    assert.equal(versionTwo.queue().length, 2);
    assert.deepEqual(versionTwo.queue().map((row) => row.id), ["cycle-one", "cycle-two"]);

    const persistenceFailure = createRuntime([], { generated: ["memory prompt"], throwOnSet: true });
    const persistedMessage = await persistenceFailure.api.generateBatch({ request: "request one" });
    assert.equal(persistenceFailure.dispatchClicks, 1);
    assert.match(persistenceFailure.logHost.innerHTML, /memory prompt/);
    assert.match(persistenceFailure.autoStatusHost.innerHTML, /not persistent/i);
    assert.match(persistedMessage, /1/);

    const localBusy = createRuntime([], { generated: ["owned prompt"], llmDelay: 80 });
    const ownedRun = localBusy.api.generateBatch({ request: "owner request" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    const blockedMessage = await localBusy.api.generateBatch({ request: "other request" });
    assert.equal(localBusy.dispatchClicks, 1);
    assert.match(blockedMessage, /已有队列任务/);
    await ownedRun;

    const staleLeaseValues = new Map([[QUEUE_KEY, JSON.stringify({
        version: 2, rows: [], requestIds: [], activeOwner: "old-tab", activeUntil: Date.now() + 60_000,
    })]]);
    const staleLease = createRuntime([], { values: staleLeaseValues, generated: ["fresh after reload"] });
    assert.doesNotMatch(staleLease.autoStatusHost.innerHTML, /另一标签页/);
    await staleLease.api.generateBatch({ request: "reload request" });
    assert.equal(staleLease.dispatchClicks, 1);

    const sharedValues = new Map([[QUEUE_KEY, "[]"]]);
    const firstRun = createRuntime([], { values: sharedValues, generated: ["first result"] });
    await firstRun.api.generateBatch({ request: "Repeat   Request" });
    const secondRun = createRuntime([], { values: sharedValues, generated: ["second result"] });
    const repeated = await secondRun.api.generateBatch({ request: "repeat request" });
    assert.equal(secondRun.dispatchClicks, 1);
    assert.match(repeated, /1/);

    const explicitSkip = createRuntime([], { values: sharedValues, generated: ["must not run"] });
    const skipped = await explicitSkip.api.generateBatch({ request: "repeat request", allowRepeat: false });
    assert.equal(explicitSkip.dispatchClicks, 0);
    assert.match(skipped, /0/);

    const repeatedLines = createRuntime([], { generated: ["variation one", "variation two"] });
    await repeatedLines.api.generateBatch({ request: "same request\nsame request" });
    assert.equal(repeatedLines.dispatchClicks, 2);
    assert.deepEqual(repeatedLines.queue().map((row) => row.prompt), ["variation one", "variation two"]);

    const exhaustedLlm = createRuntime([], {
        generated: [{ error: "LLM HTTP 503: retries exhausted" }, "must not be requested"],
    });
    const exhaustedLlmResult = await exhaustedLlm.api.generateBatch({ request: "retry request" });
    assert.equal(exhaustedLlm.dispatchClicks, 1);
    assert.equal(exhaustedLlm.queue().length, 0);
    assert.match(exhaustedLlmResult, /503/);

    const onlyNew = createRuntime([
        { index: 1, id: "old", prompt: "historical pending", status: "pending", requestId: "old request", batchId: "old-batch" },
    ], { generated: ["fresh prompt"] });
    await onlyNew.api.generateAndRun({ request: "fresh request", target: "txt2img", writeMode: "replace" });
    assert.equal(onlyNew.generateClicks, 1);
    assert.equal(onlyNew.queue().find((row) => row.prompt === "historical pending").status, "pending");
    const freshRow = onlyNew.queue().find((row) => row.prompt === "fresh prompt");
    assert.equal(freshRow.status, "completed");
    assert.equal(freshRow.requestId, "fresh request");
    assert.match(freshRow.batchId, /^batch-/);

    const forgeFailure = createRuntime([
        { index: 1, id: "failure", prompt: "will fail", status: "pending", requestId: "failure request", batchId: "failure-batch" },
    ], { forgeFailure: true });
    const failed = await forgeFailure.api.runStored({ target: "txt2img", writeMode: "replace" });
    assert.match(failed, /fail|error/i);
    assert.equal(forgeFailure.queue()[0].status, "pending");

    const timedOutForge = createRuntime([
        { index: 1, id: "retry-forge", prompt: "retry forge", status: "pending", requestId: "retry forge", batchId: "retry-batch" },
    ], { forgeFailures: ["Error: generation timeout"], forgeDelay: 10 });
    const timedOutForgeResult = await timedOutForge.api.runStored({ target: "txt2img", writeMode: "replace" });
    assert.match(timedOutForgeResult, /timeout/);
    assert.equal(timedOutForge.generateClicks, 1);
    assert.equal(timedOutForge.queue()[0].status, "pending");

    const appendRun = createRuntime([
        { index: 1, id: "append", prompt: "new details", status: "pending", requestId: "append request", batchId: "append-batch" },
    ]);
    await appendRun.api.runStored({ target: "txt2img", writeMode: "append" });
    assert.equal(appendRun.queue()[0].status, "completed");
    assert.equal(appendRun.promptValue(), "base, new details");
    assert.match(appendRun.logHost.innerHTML, /new details/);

    const multiAppendRun = createRuntime([
        { index: 1, id: "append-one", prompt: "first details", status: "pending", requestId: "append one", batchId: "append-batch" },
        { index: 2, id: "append-two", prompt: "second details", status: "pending", requestId: "append two", batchId: "append-batch" },
    ]);
    await multiAppendRun.api.runStored({ target: "txt2img", writeMode: "append" });
    assert.deepEqual(multiAppendRun.promptHistory(), ["base, first details", "base, second details"]);
    assert.equal(multiAppendRun.promptValue(), "base, second details");

    const continuousRun = createRuntime([], { generated: ["cycle prompt", "cycle prompt"] });
    const continuousResult = await continuousRun.api.generateAndRun({
        request: "repeat this request",
        target: "txt2img",
        writeMode: "replace",
        continuous: true,
        cycles: 2,
    });
    assert.equal(continuousRun.dispatchClicks, 2);
    assert.equal(continuousRun.generateClicks, 2);
    assert.equal(continuousRun.queue().length, 2);
    assert.deepEqual(continuousRun.queue().map((row) => row.status), ["completed", "completed"]);
    assert.match(continuousResult, /2/);

    const inlineAppendRun = createRuntime([], {
        inlineGenerated: ["inline details one", "inline details two"],
    });
    const inlineAppendResult = await inlineAppendRun.api.inlineLoop({
        slot: "txt2img", request: "make a different scene", source: "llm", cycles: 2,
    });
    assert.match(inlineAppendResult, /2/);
    assert.deepEqual(inlineAppendRun.promptHistory(), [
        "base, inline details one",
        "base, inline details two",
    ]);

    const inlineCancelRun = createRuntime([], { forgeDelay: 80, inlineGenerated: ["cancel details"] });
    const pendingInline = inlineCancelRun.api.inlineLoop({
        slot: "txt2img", request: "cancel this loop", source: "llm", cycles: 0,
    });
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.match(inlineCancelRun.api.cancelInline("txt2img"), /正在停止/);
    await pendingInline;
    // Cancellation happened while the inline LLM request was still running;
    // no Forge interrupt is needed until a generation has actually started.
    assert.equal(inlineCancelRun.interruptClicks, 0);
    const afterCancel = await inlineCancelRun.api.inlineOnce({
        slot: "txt2img", request: "run again", source: "llm",
    });
    assert.match(afterCancel, /已生成 Prompt 并写入/);

    const independentInline = createRuntime([], { llmDelay: 80, inlineGenerated: ["independent details"] });
    const mainGeneration = independentInline.api.generateBatch({ request: "main queue" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    const inlineWhileMainBusy = await independentInline.api.inlineOnce({
        slot: "txt2img", request: "inline queue", source: "llm",
    });
    assert.match(inlineWhileMainBusy, /已生成 Prompt 并写入/);
    await mainGeneration;

    const identicalGallery = createRuntime([
        { index: 1, id: "same-gallery", prompt: "fixed seed prompt", status: "pending", requestId: "same gallery", batchId: "same-gallery-batch" },
    ], { keepGalleryUnchanged: true });
    await identicalGallery.api.runStored({ target: "txt2img", writeMode: "replace" });
    assert.equal(identicalGallery.queue()[0].status, "completed");

    const cancelledLlm = createRuntime([], { generated: ["late result"], llmDelay: 60 });
    const pendingGeneration = cancelledLlm.api.generateBatch({ request: "cancel me" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    cancelledLlm.api.cancel();
    await pendingGeneration;
    await new Promise((resolve) => setTimeout(resolve, 80));
    assert.equal(cancelledLlm.queue().length, 0);
    assert.equal(cancelledLlm.interruptClicks, 0);

    const cancelledForge = createRuntime([
        { index: 1, id: "cancel", prompt: "cancel forge", status: "pending", requestId: "cancel request", batchId: "cancel-batch" },
    ], { forgeDelay: 80 });
    const pendingForge = cancelledForge.api.runStored({ target: "txt2img", writeMode: "replace" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    cancelledForge.api.cancel();
    await pendingForge;
    assert.equal(cancelledForge.interruptClicks, 1);
    assert.equal(cancelledForge.queue()[0].status, "pending");
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
