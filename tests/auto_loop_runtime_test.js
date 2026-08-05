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

function createRuntime(initialQueue, generatedOutput = "", options = {}) {
    const values = new Map([[QUEUE_KEY, JSON.stringify(initialQueue)]]);
    const localStorage = {
        getItem(key) {
            if (options.throwOnGet) throw new Error("read denied");
            return values.has(key) ? values.get(key) : null;
        },
        setItem(key, value) {
            if (options.throwOnSet) throw new Error("quota exceeded");
            values.set(key, String(value));
        },
    };
    const inputs = {
        llm_prompt_studio_request: new FakeInput(),
        llm_prompt_studio_source_tags: new FakeInput(),
        llm_prompt_studio_output: new FakeInput(),
    };
    const logHost = { innerHTML: "" };
    const autoStatusHost = { innerHTML: "" };
    const studioStatusHost = { textContent: "等待" };
    const generalButton = { disabled: false, matches: () => true };
    let dispatchClicks = 0;
    const dispatchButton = {
        disabled: false,
        matches: () => true,
        click() {
            dispatchClicks += 1;
            this.disabled = true;
            setTimeout(() => {
                inputs.llm_prompt_studio_output.value = generatedOutput;
                studioStatusHost.textContent = "生成完成";
                this.disabled = false;
            }, 10);
        },
    };
    const hosts = {
        llm_prompt_studio_auto_loop_log: logHost,
        llm_prompt_studio_auto_loop_status: autoStatusHost,
        llm_prompt_studio_status: studioStatusHost,
        llm_prompt_studio_generate_button: generalButton,
        llm_prompt_studio_auto_loop_dispatch: dispatchButton,
    };
    for (const [id, field] of Object.entries(inputs)) {
        hosts[id] = { querySelector: () => field };
    }
    const document = {
        querySelector(selector) {
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
    };
    context.window = context;
    context.localStorage = localStorage;
    context.getComputedStyle = () => ({ display: "block", visibility: "visible" });
    vm.runInNewContext(SCRIPT, context, { filename: "llm_prompt_studio_auto_loop.js" });
    return {
        api: context.llmPromptStudioAutoLoop,
        autoStatusHost,
        logHost,
        get dispatchClicks() {
            return dispatchClicks;
        },
        queue() {
            return JSON.parse(localStorage.getItem(QUEUE_KEY));
        },
    };
}

async function main() {
    const migrated = createRuntime([
        { index: 4, prompt: "  same   prompt ", status: "待生图" },
        { index: 8, prompt: "same prompt", status: "已完成（队列状态未保存）" },
        { index: 9, prompt: "   ", status: "待生图" },
        { index: 10, prompt: "unique", status: "生图中" },
    ]);
    assert.deepEqual(migrated.queue(), [
        { index: 1, prompt: "same   prompt", status: "已完成" },
        { index: 2, prompt: "unique", status: "待生图" },
    ]);
    assert.match(migrated.autoStatusHost.innerHTML, /已移除重复记录 1 条/);
    assert.match(migrated.autoStatusHost.innerHTML, /已移除空记录 1 条/);

    const persistenceFailure = createRuntime(
        [
            { index: 4, prompt: "restored prompt", status: "待生图" },
            { index: 5, prompt: "restored   prompt", status: "已完成" },
        ],
        "",
        { throwOnSet: true },
    );
    assert.match(persistenceFailure.logHost.innerHTML, /restored prompt/);
    assert.match(persistenceFailure.logHost.innerHTML, /已完成/);
    assert.match(persistenceFailure.autoStatusHost.innerHTML, /已恢复，但整理结果保存失败/);
    assert.doesNotMatch(persistenceFailure.autoStatusHost.innerHTML, /已整理历史 Prompt 队列/);

    const readFailure = createRuntime([], "", { throwOnGet: true });
    assert.match(readFailure.logHost.innerHTML, /暂无已保存 Prompt/);
    assert.match(readFailure.autoStatusHost.innerHTML, /历史 Prompt 队列恢复失败/);

    const duplicateOutput = createRuntime(
        [{ index: 7, prompt: "Same   prompt", status: "待生图" }],
        "Same prompt",
    );
    const message = await duplicateOutput.api.generateBatch({
        request: "request one\nrequest   one",
    });
    assert.equal(duplicateOutput.dispatchClicks, 1);
    assert.equal(duplicateOutput.queue().length, 1);
    assert.match(message, /新增 0 条/);
    assert.match(duplicateOutput.autoStatusHost.innerHTML, /重复输入 1 条/);
    assert.match(duplicateOutput.autoStatusHost.innerHTML, /重复生成结果 1 条/);
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
