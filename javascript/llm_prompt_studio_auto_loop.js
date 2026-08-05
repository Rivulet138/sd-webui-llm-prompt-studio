(function () {
    "use strict";

    const QUEUE_KEY = "llm_prompt_studio_auto_loop_queue_v1";
    const MAX_LOG_ROWS = 100;
    const state = {
        running: false,
        cancelled: false,
        runId: 0,
        phase: "idle",
        target: "txt2img",
        queue: [],
    };

    function root() {
        return typeof gradioApp === "function" ? gradioApp() : document;
    }

    function find(id, selector = "") {
        const host = root().querySelector(`#${id}`);
        return host ? (selector ? host.querySelector(selector) : host) : null;
    }

    function findButton(id) {
        const host = find(id);
        return host?.matches("button") ? host : host?.querySelector("button");
    }

    function input(id) {
        return find(id)?.querySelector("textarea, input, select");
    }

    function setValue(id, next) {
        const element = input(id);
        if (!element) throw new Error(`未找到控件：${id}`);
        const prototype = element instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
        if (setter) setter.call(element, String(next ?? ""));
        else element.value = String(next ?? "");
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function wait(ms) {
        return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>\"']/g, (char) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
        }[char]));
    }

    function canonicalPrompt(value) {
        return String(value ?? "").normalize("NFKC").replace(/\s+/g, " ").trim();
    }

    function normalizeQueueStatus(value) {
        return String(value || "").startsWith("已完成") ? "已完成" : "待生图";
    }

    function migrateQueue(rows) {
        const queue = [];
        const positions = new Map();
        let duplicateCount = 0;
        let emptyCount = 0;
        for (const row of Array.isArray(rows) ? rows : []) {
            const prompt = row && typeof row.prompt === "string" ? row.prompt.trim() : "";
            const key = canonicalPrompt(prompt);
            if (!key) {
                emptyCount += 1;
                continue;
            }
            const status = normalizeQueueStatus(row.status);
            if (positions.has(key)) {
                duplicateCount += 1;
                const existing = queue[positions.get(key)];
                if (status === "已完成") existing.status = "已完成";
                continue;
            }
            positions.set(key, queue.length);
            queue.push({ index: queue.length + 1, prompt, status });
        }
        return { queue, duplicateCount, emptyCount };
    }

    function isVisible(element) {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden" && element.offsetParent !== null;
    }

    function isBusy(tab) {
        const interrupt = root().querySelector(`#${tab}_interrupt`);
        const generate = root().querySelector(`#${tab}_generate`);
        return isVisible(interrupt) || Boolean(generate?.disabled);
    }

    function render(kind, headline, detail = "") {
        const host = find("llm_prompt_studio_auto_loop_status");
        if (!host) return;
        const tone = kind === "success" ? "success" : kind === "error" ? "error" : "warning";
        host.innerHTML = `<div class="lps-status lps-status--${tone}" role="status" aria-live="polite"><strong>${escapeHtml(headline)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}</div>`;
    }

    function renderQueue() {
        const host = find("llm_prompt_studio_auto_loop_log");
        if (!host) return;
        const rows = state.queue.slice(-MAX_LOG_ROWS);
        host.innerHTML = rows.length
            ? rows.map((row) => `<div class="lps-auto-loop-row"><span>${row.index}</span><span>${escapeHtml(row.status)}</span><code>${escapeHtml(row.prompt)}</code></div>`).join("")
            : `<div class="lps-auto-loop-empty">暂无已保存 Prompt。先批量生成，再决定是否投入生图。</div>`;
    }

    function saveQueue() {
        window.localStorage.setItem(QUEUE_KEY, JSON.stringify(state.queue));
    }

    function renderQueueMigrationNotice(migration) {
        if (migration?.error) {
            render("error", migration.error.headline, migration.error.detail);
            return;
        }
        if (!migration?.duplicateCount && !migration?.emptyCount) return;
        const details = [];
        if (migration.duplicateCount) details.push(`已移除重复记录 ${migration.duplicateCount} 条`);
        if (migration.emptyCount) details.push(`已移除空记录 ${migration.emptyCount} 条`);
        details.push(`保留 ${state.queue.length} 条唯一 Prompt，并已重新编号`);
        render("warning", "已整理历史 Prompt 队列", details.join("；"));
    }

    function loadQueue() {
        let migration = { queue: [], duplicateCount: 0, emptyCount: 0 };
        try {
            const raw = window.localStorage.getItem(QUEUE_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            migration = migrateQueue(parsed);
            state.queue = migration.queue;
        } catch (error) {
            state.queue = [];
            migration.error = {
                headline: "历史 Prompt 队列恢复失败",
                detail: String(error?.message || error),
            };
            renderQueue();
            renderQueueMigrationNotice(migration);
            return migration;
        }
        try {
            saveQueue();
        } catch (error) {
            migration.error = {
                headline: "历史 Prompt 队列已恢复，但整理结果保存失败",
                detail: String(error?.message || error),
            };
        }
        renderQueue();
        renderQueueMigrationNotice(migration);
        return migration;
    }

    async function waitForStudioGeneration(runId, beforeOutput, beforeStatus, timeoutMs = 300000) {
        const button = findButton("llm_prompt_studio_auto_loop_dispatch");
        const output = input("llm_prompt_studio_output");
        const statusHost = find("llm_prompt_studio_status");
        let sawBusy = false;
        const started = Date.now();
        while (Date.now() - started < timeoutMs) {
            if (state.cancelled || state.runId !== runId) throw new Error("已取消");
            const busy = Boolean(button?.disabled);
            sawBusy ||= busy;
            const currentOutput = String(output?.value || "").trim();
            const currentStatus = String(statusHost?.textContent || "");
            const changed = currentStatus !== beforeStatus || currentOutput !== beforeOutput;
            if (changed && /失败|错误|超时|拒绝|拦截/.test(currentStatus)) {
                throw new Error(currentStatus || "LLM Prompt 生成失败");
            }
            if (sawBusy && !busy) {
                await wait(100);
                const finishedOutput = String(output?.value || "").trim() || currentOutput;
                if (finishedOutput) return finishedOutput;
                throw new Error(currentStatus || "LLM Prompt 生成失败：未返回结果");
            }
            if (changed && currentOutput && (!button || (!busy && Date.now() - started > 500))) return currentOutput;
            await wait(250);
        }
        throw new Error("LLM Prompt 生成超时，请检查 API 设置");
    }

    async function waitForForgeGeneration(tab, runId, beforeGallery, timeoutMs = 1800000) {
        const started = Date.now();
        let sawBusy = false;
        const gallery = root().querySelector(`#${tab}_gallery`);
        while (Date.now() - started < timeoutMs) {
            if (state.cancelled || state.runId !== runId) throw new Error("已取消");
            const busy = isBusy(tab);
            sawBusy ||= busy;
            if (sawBusy && !busy) return;
            if (!busy && gallery && gallery.innerHTML !== beforeGallery && Date.now() - started > 500) return;
            await wait(400);
        }
        throw new Error(`${tab} 生图超时，请检查 Forge 队列或手动停止任务`);
    }

    function writePrompt(prompt, target, mode, basePrompt = "") {
        const targetInput = root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`);
        if (!targetInput) throw new Error(`未找到 ${target} Prompt 输入框`);
        const base = String(basePrompt || "");
        const next = mode === "append" && base.trim()
            ? `${base.replace(/[\s,]+$/, "")}, ${String(prompt).replace(/^[\s,]+/, "")}`
            : String(prompt).trim();
        setValue(`${target}_prompt`, next);
        targetInput.focus({ preventScroll: true });
    }

    function ensureLlmIdle() {
        const generalButton = findButton("llm_prompt_studio_generate_button");
        const dispatchButton = findButton("llm_prompt_studio_auto_loop_dispatch");
        if (!generalButton || !dispatchButton || generalButton.disabled || dispatchButton.disabled) {
            throw new Error("LLM Studio 当前已有生成任务，请等待完成后再启动");
        }
        return dispatchButton;
    }

    function ensureForgeIdle(target) {
        if (isBusy(target)) throw new Error(`${target} 当前已有生图任务，请等待完成后再启动`);
    }

    async function generateBatch(config) {
        if (state.running) return "已有队列任务正在运行";
        const seen = new Set();
        let duplicateInputCount = 0;
        const requests = String(config.request || "").split(/\r?\n/).map((value) => value.trim()).filter((value) => {
            if (!value || value.startsWith("#")) return false;
            const key = canonicalPrompt(value);
            if (seen.has(key)) {
                duplicateInputCount += 1;
                return false;
            }
            seen.add(key);
            return true;
        });
        if (!requests.length) {
            render("warning", "缺少批量创作要求", "请按每行一条填写至少一个 Prompt");
            return "缺少每轮创作要求";
        }
        state.running = true;
        state.cancelled = false;
        state.phase = "llm";
        const runId = ++state.runId;
        let added = 0;
        let duplicateOutputCount = 0;
        const queuedPrompts = new Set(state.queue.map((row) => canonicalPrompt(row.prompt)).filter(Boolean));
        try {
            await wait(150);
            for (let index = 1; index <= requests.length; index += 1) {
                if (state.cancelled || state.runId !== runId) break;
                const button = ensureLlmIdle();
                render("warning", `正在批量生成 Prompt：第 ${index}/${requests.length} 条`, "每条唯一输入仅请求一次，不评分、不写入 Prompt 缓存");
                const promptRequest = requests[index - 1];
                setValue("llm_prompt_studio_request", promptRequest);
                setValue("llm_prompt_studio_source_tags", "");
                setValue("llm_prompt_studio_output", "");
                const output = input("llm_prompt_studio_output");
                const statusHost = find("llm_prompt_studio_status");
                const beforeOutput = String(output?.value || "");
                const beforeStatus = String(statusHost?.textContent || "");
                button.click();
                const prompt = await waitForStudioGeneration(runId, beforeOutput, beforeStatus);
                const promptKey = canonicalPrompt(prompt);
                if (queuedPrompts.has(promptKey)) {
                    duplicateOutputCount += 1;
                    renderQueue();
                    continue;
                }
                state.queue.push({ index: state.queue.length + 1, prompt: prompt.trim(), status: "待生图" });
                queuedPrompts.add(promptKey);
                try {
                    saveQueue();
                } catch (error) {
                    state.queue.pop();
                    queuedPrompts.delete(promptKey);
                    throw new Error(`Prompt 队列保存失败：${String(error?.message || error)}`);
                }
                added += 1;
                renderQueue();
            }
            const message = state.cancelled ? `已取消 Prompt 批量生成，新增 ${added} 条` : `Prompt 批量生成完成，新增 ${added} 条，已保存待生图队列`;
            const details = [];
            if (duplicateInputCount) details.push(`已忽略重复输入 ${duplicateInputCount} 条`);
            if (duplicateOutputCount) details.push(`已忽略重复生成结果 ${duplicateOutputCount} 条`);
            details.push("请检查队列后再点击“投入队列生图”");
            const detail = details.join("；");
            render(state.cancelled ? "warning" : "success", message, detail);
            return message;
        } catch (error) {
            const message = String(error?.message || error);
            render(message === "已取消" ? "warning" : "error", "Prompt 批量生成已停止", message);
            return message;
        } finally {
            state.running = false;
            state.phase = "idle";
        }
    }

    async function runStored(config) {
        if (state.running) return "已有队列任务正在运行";
        const pending = state.queue.filter((row) => !String(row.status || "").startsWith("已完成"));
        if (!pending.length) {
            render("warning", "没有待生图 Prompt", "请先批量生成或清空后重新生成");
            return "没有待生图 Prompt";
        }
        const target = config.target === "img2img" ? "img2img" : "txt2img";
        const mode = config.writeMode === "append" ? "append" : "replace";
        const targetInput = root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`);
        if (!targetInput) throw new Error(`未找到 ${target} Prompt 输入框`);
        const basePrompt = String(targetInput.value || "");
        state.running = true;
        state.cancelled = false;
        state.phase = "forge";
        state.target = target;
        const runId = ++state.runId;
        let completed = 0;
        let currentRow = null;
        try {
            for (const row of pending) {
                if (state.cancelled || state.runId !== runId) break;
                ensureForgeIdle(target);
                currentRow = row;
                row.status = "生图中";
                renderQueue();
                writePrompt(row.prompt, target, mode, basePrompt);
                const generate = root().querySelector(`#${target}_generate`);
                if (!generate) throw new Error(`未找到 ${target} 生图按钮`);
                const gallery = root().querySelector(`#${target}_gallery`);
                const beforeGallery = gallery?.innerHTML || "";
                generate.click();
                await waitForForgeGeneration(target, runId, beforeGallery);
                row.status = "已完成";
                try {
                    saveQueue();
                } catch (error) {
                    row.status = "已完成（队列状态未保存）";
                    currentRow = null;
                    renderQueue();
                    throw new Error(`Prompt 队列保存失败：${String(error?.message || error)}`);
                }
                currentRow = null;
                completed += 1;
                renderQueue();
                render("success", `队列生图进度：已完成 ${completed}/${pending.length}`, "");
            }
            const message = state.cancelled ? `已取消队列生图，完成 ${completed} 条` : `队列生图完成，共 ${completed} 条`;
            render(state.cancelled ? "warning" : "success", message, "");
            return message;
        } catch (error) {
            const message = String(error?.message || error);
            render(message === "已取消" ? "warning" : "error", "队列生图已停止", message);
            return message;
        } finally {
            if (currentRow?.status === "生图中") {
                currentRow.status = "待生图";
                try {
                    saveQueue();
                } catch (error) {
                    render("error", "Prompt 队列状态保存失败", String(error?.message || error));
                }
                renderQueue();
            }
            state.running = false;
            state.phase = "idle";
        }
    }

    async function generateAndRun(config) {
        if (state.running) return "已有队列任务正在运行";
        const generated = await generateBatch(config);
        if (!String(generated).startsWith("Prompt 批量生成完成")) return generated;
        return runStored(config);
    }

    function clearQueue() {
        if (state.running) return "运行中不能清空队列";
        const previous = state.queue;
        state.queue = [];
        try {
            saveQueue();
        } catch (error) {
            state.queue = previous;
            render("error", "清空队列失败", String(error?.message || error));
            return "清空队列失败";
        }
        renderQueue();
        render("success", "已清空待生图队列", "");
        return "已清空待生图队列";
    }

    function cancel() {
        if (!state.running) return "当前没有运行中的任务";
        state.cancelled = true;
        state.runId += 1;
        if (state.phase === "forge") {
            const interrupt = root().querySelector(`#${state.target}_interrupt`);
            if (isVisible(interrupt)) interrupt.click();
        }
        render("warning", "正在取消当前阶段", "已生成或已完成的队列记录会保留");
        return "正在取消当前阶段";
    }

    const queueMigration = loadQueue();
    window.setTimeout(() => {
        renderQueue();
        renderQueueMigrationNotice(queueMigration);
    }, 1000);
    window.llmPromptStudioAutoLoop = {
        generateBatch,
        generateAndRun,
        runStored,
        clearQueue,
        cancel,
        start: generateBatch,
    };
})();
