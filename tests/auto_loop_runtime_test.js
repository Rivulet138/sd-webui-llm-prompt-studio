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
    const mutationObservers = new Set();
    function notifyMutation(element) {
        for (const observer of mutationObservers) {
            if (observer.targets.has(element)) observer.callback();
        }
    }
    class FakeMutationObserver {
        constructor(callback) {
            this.callback = callback;
            this.targets = new Set();
        }

        observe(element) {
            this.targets.add(element);
            mutationObservers.add(this);
        }

        disconnect() {
            mutationObservers.delete(this);
            this.targets.clear();
        }
    }
    class FakeWorker {
        postMessage(message) {
            setTimeout(() => this.onmessage?.({ data: message.id }), message.ms);
        }
    }
    class FakeBlob {}
    const localStorage = {
        getItem(key) {
            if (options.throwOnGet) throw new Error("read denied");
            return values.has(key) ? values.get(key) : null;
        },
        setItem(key, value) {
            if (options.throwOnSet) throw new Error("quota exceeded");
            values.set(key, String(value));
            if (key !== QUEUE_KEY) return;
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
        removeItem(key) {
            values.delete(key);
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
    const inlineLoopStatusHost = { innerHTML: "等待开始。" };
    const forgeStatusHost = { textContent: "idle" };
    const gallery = { innerHTML: "old" };
    const generalButton = { disabled: false, matches: () => true };
    let dispatchClicks = 0;
    let generateClicks = 0;
    let forgeBusy = false;
    const promptHistory = [];
    const events = [];
    let interruptClicks = 0;
    const generated = Array.isArray(options.generated) ? [...options.generated] : [options.generated || "new prompt"];
    const inlineGenerated = Array.isArray(options.inlineGenerated) ? [...options.inlineGenerated] : [options.inlineGenerated || "inline prompt"];
    const forgeFailures = Array.isArray(options.forgeFailures) ? [...options.forgeFailures] : [];
    const dispatchButton = {
        disabled: false,
        matches: () => true,
        click() {
            dispatchClicks += 1;
            events.push(`llm-click-${dispatchClicks}`);
            if (!options.dispatchNoDisable) this.disabled = true;
            notifyMutation(this);
            const output = generated.shift() || "new prompt";
            setTimeout(() => {
                if (typeof output === "object" && output.error) {
                    inputs.llm_prompt_studio_output.value = "";
                    studioStatusHost.textContent = output.error;
                } else {
                    inputs.llm_prompt_studio_output.value = output;
                    studioStatusHost.textContent = "completed";
                }
                events.push(`llm-complete-${dispatchClicks}`);
                this.disabled = false;
                notifyMutation(inputs.llm_prompt_studio_output);
                notifyMutation(studioStatusHost);
                notifyMutation(this);
            }, options.llmDelay || 10);
        },
    };
    const inlineGenerate = {
        disabled: false,
        matches: () => true,
        click() {
            if (!options.inlineNoDisable) this.disabled = true;
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
            const generationNumber = generateClicks;
            events.push(`forge-click-${generationNumber}`);
            promptHistory.push(inputs.txt2img_prompt.value);
            localStorage.setItem("txt2img_task_id", `task-${generationNumber}`);
            const markBusy = () => {
                forgeBusy = true;
                if (!options.forgeNoDisable) this.disabled = true;
                notifyMutation(interrupt);
                notifyMutation(this);
            };
            if (options.forgeBusyStartDelay) setTimeout(markBusy, options.forgeBusyStartDelay);
            else markBusy();
            if (options.replaceTaskIdAfter) {
                setTimeout(() => localStorage.setItem("txt2img_task_id", "foreign-task"), options.replaceTaskIdAfter);
            }
            if (options.forgeNeverCompletes) return;
            setTimeout(() => {
                const transientFailure = forgeFailures.shift();
                const failure = transientFailure || (options.forgeFailure ? "Error: generation failed" : "");
                forgeStatusHost.textContent = failure || "completed";
                if (!failure && !options.keepGalleryUnchanged) gallery.innerHTML = `image-${generateClicks}`;
                forgeBusy = false;
                this.disabled = false;
                const removeTaskId = () => localStorage.removeItem("txt2img_task_id");
                if (options.taskIdRemovalDelay) setTimeout(removeTaskId, options.taskIdRemovalDelay);
                else removeTaskId();
                events.push(`forge-complete-${generationNumber}`);
                notifyMutation(forgeStatusHost);
                notifyMutation(interrupt);
                notifyMutation(this);
            }, (options.forgeBusyStartDelay || 0) + (options.forgeDelay || 10));
        },
    };
    const interrupt = {
        offsetParent: options.panelsHidden ? null : {},
        matches: () => true,
        click() {
            interruptClicks += 1;
            forgeBusy = false;
            forgeGenerate.disabled = false;
            localStorage.removeItem("txt2img_task_id");
            notifyMutation(this);
            notifyMutation(forgeGenerate);
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
        llm_prompt_studio_txt2img_inline_loop_status: inlineLoopStatusHost,
        txt2img_generate: forgeGenerate,
        txt2img_interrupt: interrupt,
        txt2img_gallery: gallery,
        txt2img_status: forgeStatusHost,
    };
    for (const [id, field] of Object.entries(inputs)) {
        hosts[id] = { querySelector: () => field };
    }
    const document = {
        hidden: Boolean(options.documentHidden),
        visibilityState: options.documentHidden ? "hidden" : "visible",
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
        setTimeout: options.pollDelayFloor
            ? (callback, ms) => setTimeout(callback, Math.max(ms, options.pollDelayFloor))
            : setTimeout,
        clearTimeout,
        Date,
    };
    if (options.withMutationObserver) context.MutationObserver = FakeMutationObserver;
    if (options.withBackgroundWorker) {
        context.Worker = FakeWorker;
        context.Blob = FakeBlob;
        context.URL = { createObjectURL: () => "blob:test", revokeObjectURL() {} };
    }
    if (options.performanceStep) {
        let performanceNow = 0;
        context.performance = { now: () => { performanceNow += options.performanceStep; return performanceNow; } };
    }
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
        display: element === interrupt ? (forgeBusy ? "block" : "none") : "block",
        visibility: "visible",
    });
    vm.runInNewContext(SCRIPT, context, { filename: "llm_prompt_studio_auto_loop.js" });
    return {
        api: context.llmPromptStudioAutoLoop,
        autoStatusHost,
        inlineLoopStatusHost,
        logHost,
        values,
        get dispatchClicks() { return dispatchClicks; },
        get generateClicks() { return generateClicks; },
        get interruptClicks() { return interruptClicks; },
        events() { return [...events]; },
        promptValue(target = "txt2img") { return inputs[`${target}_prompt`].value; },
        setPromptValue(value, target = "txt2img") { inputs[`${target}_prompt`].value = value; },
        promptHistory() { return [...promptHistory]; },
        queue() {
            const raw = values.get(QUEUE_KEY);
            const stored = raw ? JSON.parse(raw) : [];
            return Array.isArray(stored) ? stored : stored.rows;
        },
    };
}

async function settleWithin(run, cancel, timeoutMs = 3000) {
    const timedOut = Symbol("timed-out");
    let timer;
    const result = await Promise.race([
        run,
        new Promise((resolve) => { timer = setTimeout(() => resolve(timedOut), timeoutMs); }),
    ]);
    if (result === timedOut) {
        cancel();
        await run;
        assert.fail(`runtime did not settle within ${timeoutMs}ms`);
    }
    clearTimeout(timer);
    return result;
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

    const batchWithoutBusySignal = createRuntime([], {
        generated: ["no disabled batch"], dispatchNoDisable: true,
    });
    await batchWithoutBusySignal.api.generateBatch({ request: "start batch" });
    assert.equal(batchWithoutBusySignal.dispatchClicks, 1);
    assert.equal(batchWithoutBusySignal.queue()[0].prompt, "no disabled batch");

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

    const continuousAppendRun = createRuntime([], {
        generated: ["cycle A", "cycle B", "cycle C"],
    });
    const continuousAppendResult = await continuousAppendRun.api.generateAndRun({
        request: "create independent variations",
        target: "txt2img",
        writeMode: "append",
        continuous: true,
        cycles: 3,
    });
    assert.deepEqual(continuousAppendRun.promptHistory(), [
        "base, cycle A",
        "base, cycle B",
        "base, cycle C",
    ]);
    assert.equal(continuousAppendRun.promptValue(), "base, cycle C");
    assert.match(continuousAppendResult, /3/);

    const startFrozenRun = createRuntime([], {
        generated: ["delayed cycle"],
        llmDelay: 60,
    });
    const startFrozenPending = startFrozenRun.api.generateAndRun({
        request: "freeze the starting prompt",
        target: "txt2img",
        writeMode: "append",
        continuous: false,
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    startFrozenRun.setPromptValue("edited while LLM was running");
    await startFrozenPending;
    assert.deepEqual(startFrozenRun.promptHistory(), ["base, delayed cycle"]);

    const hiddenBackgroundRun = createRuntime([], {
        documentHidden: true,
        panelsHidden: true,
        withBackgroundWorker: true,
        withMutationObserver: true,
        dispatchNoDisable: true,
        forgeNoDisable: true,
        pollDelayFloor: 75,
        forgeBusyStartDelay: 10,
        forgeDelay: 20,
        taskIdRemovalDelay: 100,
        generated: ["background one", "background two"],
    });
    const hiddenBackgroundPending = hiddenBackgroundRun.api.generateAndRun({
        request: "background request",
        target: "txt2img",
        writeMode: "replace",
        continuous: true,
        cycles: 2,
    });
    const hiddenBackgroundResult = await settleWithin(
        hiddenBackgroundPending,
        () => hiddenBackgroundRun.api.cancel(),
    );
    assert.match(hiddenBackgroundResult, /2/);
    assert.equal(hiddenBackgroundRun.dispatchClicks, 2);
    assert.equal(hiddenBackgroundRun.generateClicks, 2);
    assert.deepEqual(hiddenBackgroundRun.promptHistory(), ["background one", "background two"]);
    assert.deepEqual(hiddenBackgroundRun.queue().map((row) => row.status), ["completed", "completed"]);
    assert.deepEqual(hiddenBackgroundRun.events(), [
        "llm-click-1", "llm-complete-1", "forge-click-1", "forge-complete-1",
        "llm-click-2", "llm-complete-2", "forge-click-2", "forge-complete-2",
    ]);

    const hiddenBackgroundFailure = createRuntime([], {
        documentHidden: true,
        panelsHidden: true,
        withBackgroundWorker: true,
        withMutationObserver: true,
        dispatchNoDisable: true,
        forgeNoDisable: true,
        pollDelayFloor: 75,
        forgeBusyStartDelay: 10,
        forgeDelay: 20,
        forgeFailures: ["Error: generation failed"],
        generated: ["background failure", "must not run"],
    });
    const hiddenFailurePending = hiddenBackgroundFailure.api.generateAndRun({
        request: "background failure request",
        target: "txt2img",
        writeMode: "replace",
        continuous: true,
        cycles: 2,
    });
    const hiddenFailureResult = await settleWithin(
        hiddenFailurePending,
        () => hiddenBackgroundFailure.api.cancel(),
    );
    assert.match(hiddenFailureResult, /fail|error/i);
    assert.equal(hiddenBackgroundFailure.dispatchClicks, 1);
    assert.equal(hiddenBackgroundFailure.generateClicks, 1);
    assert.equal(hiddenBackgroundFailure.queue()[0].status, "pending");

    const hiddenTimeout = createRuntime([
        { index: 1, id: "hidden-timeout", prompt: "timeout prompt", status: "pending", requestId: "timeout", batchId: "timeout-batch" },
    ], {
        documentHidden: true,
        withBackgroundWorker: true,
        forgeNeverCompletes: true,
        performanceStep: 1800001,
    });
    const hiddenTimeoutResult = await settleWithin(
        hiddenTimeout.api.runStored({ target: "txt2img", writeMode: "replace" }),
        () => hiddenTimeout.api.cancel(),
    );
    assert.match(hiddenTimeoutResult, /timeout/i);
    assert.equal(hiddenTimeout.queue()[0].status, "pending");

    const ownershipLoss = createRuntime([
        { index: 1, id: "ownership-loss", prompt: "ownership prompt", status: "pending", requestId: "ownership", batchId: "ownership-batch" },
    ], {
        documentHidden: true,
        withBackgroundWorker: true,
        forgeNeverCompletes: true,
        replaceTaskIdAfter: 10,
    });
    const ownershipLossResult = await settleWithin(
        ownershipLoss.api.runStored({ target: "txt2img", writeMode: "replace" }),
        () => ownershipLoss.api.cancel(),
    );
    assert.match(ownershipLossResult, /ownership changed/i);
    assert.equal(ownershipLoss.queue()[0].status, "pending");

    const replacedTask = createRuntime([
        { index: 1, id: "replaced", prompt: "owned task", status: "pending", requestId: "owned", batchId: "owned-batch" },
    ], {
        documentHidden: true,
        panelsHidden: true,
        withBackgroundWorker: true,
        forgeNoDisable: true,
        forgeNeverCompletes: true,
        replaceTaskIdAfter: 10,
    });
    const replacedPending = replacedTask.api.runStored({ target: "txt2img", writeMode: "replace" });
    await new Promise((resolve) => setTimeout(resolve, 30));
    replacedTask.api.cancel();
    await settleWithin(replacedPending, () => replacedTask.api.cancel());
    assert.equal(replacedTask.interruptClicks, 0);
    assert.equal(replacedTask.queue()[0].status, "pending");

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
    assert.match(inlineAppendRun.inlineLoopStatusHost.innerHTML, /已完成 2 轮/);

    const inlineProgress = createRuntime([], { inlineGenerated: ["slow details"], inlineDelay: 80 });
    const inlineProgressRun = inlineProgress.api.inlineLoop({
        slot: "txt2img", request: "show progress", source: "llm", cycles: 1,
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.match(inlineProgress.inlineLoopStatusHost.innerHTML, /正在生成 Prompt/);
    await inlineProgressRun;

    const inlineWithoutBusySignal = createRuntime([], {
        inlineGenerated: ["no disabled state"], inlineNoDisable: true,
    });
    const inlineWithoutBusyResult = await inlineWithoutBusySignal.api.inlineLoop({
        slot: "txt2img", request: "start explicitly", source: "llm", cycles: 1,
    });
    assert.match(inlineWithoutBusyResult, /1/);
    assert.equal(inlineWithoutBusySignal.generateClicks, 1);

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
    ], { forgeDelay: 80, forgeNoDisable: true, panelsHidden: true });
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
