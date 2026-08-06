(function () {
    "use strict";

    const QUEUE_KEY = "llm_prompt_studio_auto_loop_queue_v1";
    const MAX_LOG_ROWS = 100;
    const LLM_WAIT_TIMEOUT_MS = 1900000;
    const STATUS = Object.freeze({ pending: "pending", running: "running", completed: "completed" });
    const FAILURE_PATTERN = /fail|error|timeout|refused|interrupted|失败|错误|超时|拒绝|中断/i;
    const state = {
        queue: [],
        requestIds: new Set(),
        active: null,
        sequence: 0,
        persistent: true,
        lastSaveFailure: "",
        lastBatchRowIds: [],
    };
    const inlineRuns = { txt2img: null, img2img: null };

    function root() {
        return typeof gradioApp === "function" ? gradioApp() : document;
    }

    function find(id) {
        return root().querySelector(`#${id}`);
    }

    function findButton(id) {
        const host = find(id);
        return host?.matches("button") ? host : host?.querySelector("button");
    }

    function input(id) {
        return find(id)?.querySelector("textarea, input, select");
    }

    function setValue(id, value) {
        const element = input(id);
        if (!element) throw new Error(`未找到控件: ${id}`);
        const prototype = element instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
        if (setter) setter.call(element, String(value ?? ""));
        else element.value = String(value ?? "");
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function wait(ms) {
        return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>\"']/g, (character) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
        }[character]));
    }

    function canonicalPrompt(value) {
        return String(value ?? "").normalize("NFKC").replace(/\s+/g, " ").trim().toLowerCase();
    }

    function normalizeStatus(value) {
        return /completed|已完成/i.test(String(value || "")) ? STATUS.completed : STATUS.pending;
    }

    function createId(prefix) {
        state.sequence += 1;
        return `${prefix}-${Date.now().toString(36)}-${state.sequence.toString(36)}`;
    }

    function migrateQueue(stored) {
        const sourceRows = Array.isArray(stored) ? stored : stored?.rows;
        const preserveDistinctRows = !Array.isArray(stored) && Number(stored?.version || 0) >= 2;
        const requestIds = new Set(Array.isArray(stored?.requestIds) ? stored.requestIds.map(canonicalPrompt).filter(Boolean) : []);
        const rows = [];
        const positions = new Map();
        let duplicateCount = 0;
        let emptyCount = 0;
        for (const source of Array.isArray(sourceRows) ? sourceRows : []) {
            const prompt = typeof source?.prompt === "string" ? source.prompt.trim() : "";
            const promptKey = canonicalPrompt(prompt);
            if (!promptKey) {
                emptyCount += 1;
                continue;
            }
            const requestId = canonicalPrompt(source.requestId);
            if (requestId) requestIds.add(requestId);
            const status = normalizeStatus(source.status);
            if (!preserveDistinctRows && positions.has(promptKey)) {
                duplicateCount += 1;
                const existing = rows[positions.get(promptKey)];
                if (status === STATUS.completed) existing.status = STATUS.completed;
                if (!existing.requestId && requestId) existing.requestId = requestId;
                continue;
            }
            if (!preserveDistinctRows) positions.set(promptKey, rows.length);
            rows.push({
                index: rows.length + 1,
                id: String(source.id || createId("row")),
                batchId: String(source.batchId || ""),
                requestId,
                prompt,
                status,
            });
        }
        return { rows, requestIds, duplicateCount, emptyCount };
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
            : '<div class="lps-auto-loop-empty">暂无已保存 Prompt。</div>';
    }

    function saveQueue() {
        try {
            state.lastSaveFailure = "";
            window.localStorage.setItem(QUEUE_KEY, JSON.stringify({
                version: 2,
                rows: state.queue,
                requestIds: Array.from(state.requestIds),
            }));
            state.persistent = true;
            return true;
        } catch (error) {
            state.persistent = false;
            state.lastSaveFailure = "storage";
            render("warning", "队列仅保存在内存中 (not persistent)", String(error?.message || error));
            return false;
        }
    }

    function loadQueue() {
        try {
            const raw = window.localStorage.getItem(QUEUE_KEY);
            const migration = migrateQueue(raw ? JSON.parse(raw) : []);
            state.queue = migration.rows;
            state.requestIds = migration.requestIds;
            renderQueue();
            if (!saveQueue()) return;
            if (migration.duplicateCount || migration.emptyCount) {
                render("warning", "已整理历史 Prompt 队列", `移除重复 ${migration.duplicateCount} 条，空记录 ${migration.emptyCount} 条`);
            }
        } catch (error) {
            state.queue = [];
            state.requestIds = new Set();
            state.persistent = false;
            renderQueue();
            render("error", "历史 Prompt 队列恢复失败", String(error?.message || error));
        }
    }

    function isVisible(element) {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden" && element.offsetParent !== null;
    }

    function isForgeBusy(tab) {
        return isVisible(find(`${tab}_interrupt`)) || Boolean(findButton(`${tab}_generate`)?.disabled);
    }

    async function beginRun(phase, target = null) {
        if (state.active) return null;
        const run = { id: createId("run"), phase, target, cancelled: false };
        state.active = run;
        saveQueue();
        return run;
    }

    function assertActive(run) {
        const isCurrent = run.scope === "inline"
            ? inlineRuns[run.slot] === run
            : state.active === run;
        if (run.cancelled || !isCurrent) throw new Error("已取消");
    }

    function finishRun(run) {
        if (state.active === run) {
            state.active = null;
            saveQueue();
        }
    }

    function beginInlineRun(slot, target) {
        if (inlineRuns[slot]) return null;
        const run = {
            id: createId("inline"), scope: "inline", slot, phase: "inline", target, cancelled: false,
        };
        inlineRuns[slot] = run;
        return run;
    }

    function finishInlineRun(run) {
        if (inlineRuns[run.slot] === run) inlineRuns[run.slot] = null;
    }

    async function waitForStudioGeneration(run, beforeStatus, timeoutMs = 300000, controls = null) {
        const button = controls?.button || findButton("llm_prompt_studio_auto_loop_dispatch");
        const output = controls?.output || input("llm_prompt_studio_output");
        const statusHost = controls?.statusHost || find("llm_prompt_studio_status");
        const beforeOutput = String(controls?.beforeOutput ?? output?.value ?? "");
        let sawBusy = false;
        const started = Date.now();
        while (Date.now() - started < timeoutMs) {
            assertActive(run);
            const busy = Boolean(button?.disabled);
            sawBusy ||= busy;
            const currentStatus = String(statusHost?.textContent || "");
            if (currentStatus !== beforeStatus && FAILURE_PATTERN.test(currentStatus)) {
                throw new Error(currentStatus || "LLM Prompt 生成失败");
            }
            if (sawBusy && !busy) {
                await wait(50);
                assertActive(run);
                const result = String(output?.value || "").trim();
                if (result && (result !== beforeOutput || currentStatus !== beforeStatus)
                    && !FAILURE_PATTERN.test(String(statusHost?.textContent || ""))) return result;
                throw new Error(currentStatus || "LLM Prompt 生成失败：未返回结果");
            }
            await wait(25);
        }
        throw new Error("LLM Prompt 生成超时（服务端重试已耗尽）");
    }

    async function waitForForgeGeneration(tab, run, beforeStatus, timeoutMs = 1800000) {
        const statusHost = find(`${tab}_status`);
        let sawBusy = false;
        const started = Date.now();
        while (Date.now() - started < timeoutMs) {
            assertActive(run);
            const busy = isForgeBusy(tab);
            sawBusy ||= busy;
            const currentStatus = String(statusHost?.textContent || "");
            if (sawBusy && !busy) {
                if (FAILURE_PATTERN.test(currentStatus)) throw new Error(currentStatus);
                return;
            }
            await wait(25);
        }
        throw new Error(`${tab} generation timeout; prompt returned to pending`);
    }

    function ensureLlmIdle() {
        const general = findButton("llm_prompt_studio_generate_button");
        const dispatch = findButton("llm_prompt_studio_auto_loop_dispatch");
        if (!general || !dispatch || general.disabled || dispatch.disabled) throw new Error("LLM Studio 当前已有生成任务");
        return dispatch;
    }

    function ensureForgeIdle(target) {
        if (isForgeBusy(target)) throw new Error(`${target} 当前已有生图任务`);
    }

    function inlineId(slot, suffix) {
        return `llm_prompt_studio_${slot}_inline_${suffix}`;
    }

    async function readInlineCache(slot, run) {
        const button = findButton(inlineId(slot, "cache_fetch"));
        const output = input(inlineId(slot, "cache_output"));
        const status = input(inlineId(slot, "cache_status"));
        if (!button || !output || !status) throw new Error("未找到缓存读取控件");
        const beforeStatus = String(status.value || "");
        button.click();
        const started = Date.now();
        while (Date.now() - started < 30000) {
            assertActive(run);
            const busy = Boolean(button.disabled);
            const currentStatus = String(status.value || "");
            const prompt = String(output.value || "").trim();
            if (!busy && currentStatus !== beforeStatus) {
                if (!prompt) throw new Error(currentStatus || "缓存为空");
                return prompt;
            }
            await wait(25);
        }
        throw new Error("读取缓存超时");
    }

    async function generateInlinePrompt(slot, request, run) {
        const inlineButton = findButton(inlineId(slot, "generate"));
        const mainButton = findButton("llm_prompt_studio_generate_button");
        const isInline = Boolean(inlineButton);
        const button = inlineButton || mainButton;
        const requestInput = input(isInline ? inlineId(slot, "request") : "llm_prompt_studio_request");
        const output = input(isInline ? inlineId(slot, "output") : "llm_prompt_studio_output");
        const status = isInline ? find(inlineId(slot, "status")) : find("llm_prompt_studio_status");
        if (!button || !requestInput || !output || !status) throw new Error("未找到主生成面板");
        if (button.disabled) throw new Error("LLM Studio 当前已有生成任务");
        setValue(isInline ? inlineId(slot, "request") : "llm_prompt_studio_request", request);
        if (!isInline && input("llm_prompt_studio_source_tags")) setValue("llm_prompt_studio_source_tags", "");
        const beforeStatus = String(status.textContent || "");
        const beforeOutput = String(output.value || "");
        button.click();
        return waitForStudioGeneration(run, beforeStatus, LLM_WAIT_TIMEOUT_MS, {
            button, output, statusHost: status, beforeOutput,
        });
    }

    async function runForgeGeneration(tab, run, generate) {
        const beforeStatus = String(find(`${tab}_status`)?.textContent || "");
        generate.click();
        await waitForForgeGeneration(tab, run, beforeStatus);
    }

    async function runStudioGeneration(run, request, button) {
        assertActive(run);
        setValue("llm_prompt_studio_request", request);
        setValue("llm_prompt_studio_source_tags", "");
        setValue("llm_prompt_studio_output", "");
        const beforeStatus = String(find("llm_prompt_studio_status")?.textContent || "");
        const output = input("llm_prompt_studio_output");
        button.click();
        return waitForStudioGeneration(run, beforeStatus, LLM_WAIT_TIMEOUT_MS, {
            button, output, statusHost: find("llm_prompt_studio_status"), beforeOutput: "",
        });
    }

    async function getInlinePrompt(config, run) {
        return config.source === "cache"
            ? readInlineCache(config.slot, run)
            : generateInlinePrompt(config.slot, config.request, run);
    }

    async function inlineOnce(config) {
        const slot = config.slot === "img2img" ? "img2img" : "txt2img";
        const target = slot;
        const run = beginInlineRun(slot, target);
        if (!run) return "当前内嵌面板已有任务正在运行";
        const basePrompt = String(root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`)?.value || "");
        try {
            const prompt = await getInlinePrompt(config, run);
            assertActive(run);
            writePrompt(prompt, target, "append", basePrompt);
            const message = config.source === "cache" ? "已取缓存 Prompt 并写入" : "已生成 Prompt 并写入";
            render("success", message, "当前只更新 Prompt，未启动生图");
            return message;
        } catch (error) {
            const message = String(error?.message || error);
            render(message === "已取消" ? "warning" : "error", "内嵌 Prompt 操作已停止", message);
            return message;
        } finally {
            finishInlineRun(run);
        }
    }

    async function inlineLoop(config) {
        const slot = config.slot === "img2img" ? "img2img" : "txt2img";
        const target = slot;
        const run = beginInlineRun(slot, target);
        if (!run) return "当前内嵌面板已有任务正在运行";
        run.phase = "forge";
        const parsedCycles = Number(config.cycles);
        const cycleLimit = Number.isFinite(parsedCycles) ? Math.max(0, Math.floor(parsedCycles)) : 0;
        let completed = 0;
        const basePrompt = String(root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`)?.value || "");
        try {
            while (cycleLimit === 0 || completed < cycleLimit) {
                assertActive(run);
                ensureForgeIdle(target);
                const prompt = await getInlinePrompt(config, run);
                assertActive(run);
                writePrompt(prompt, target, "append", basePrompt);
                const generate = findButton(`${target}_generate`);
                if (!generate) throw new Error(`未找到 ${target} 生图按钮`);
                await runForgeGeneration(target, run, generate);
                completed += 1;
                render("success", `内嵌连续生成已完成 ${completed} 轮`, cycleLimit ? `计划 ${cycleLimit} 轮` : "持续运行到停止");
            }
            return `内嵌连续生成完成，共 ${completed} 轮`;
        } catch (error) {
            const message = String(error?.message || error);
            render(message === "已取消" ? "warning" : "error", "内嵌连续生成已停止", message);
            return message;
        } finally {
            finishInlineRun(run);
        }
    }

    function cancelInline(slot) {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        const run = inlineRuns[normalizedSlot];
        if (!run) return "当前没有该面板的连续任务";
        run.cancelled = true;
        if (run.target) {
            const interrupt = find(`${run.target}_interrupt`);
            if (isVisible(interrupt)) interrupt.click();
        }
        render("warning", "正在停止内嵌连续生成", "当前请求结束后停止");
        return "正在停止内嵌连续生成";
    }

    function writePrompt(prompt, target, mode, basePrompt = "") {
        const targetInput = root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`);
        if (!targetInput) throw new Error(`未找到 ${target} Prompt 输入框`);
        // Ranbooru semantics: every cycle combines the frozen base with only this cycle's prompt.
        const next = mode === "append" && String(basePrompt).trim()
            ? `${String(basePrompt).replace(/[\s,]+$/, "")}, ${String(prompt).replace(/^[\s,]+/, "")}`
            : String(prompt).trim();
        setValue(`${target}_prompt`, next);
        targetInput.focus({ preventScroll: true });
        return next;
    }

    function parseRequests(value) {
        const seen = new Set();
        let duplicateCount = 0;
        const requests = [];
        for (const rawItem of String(value || "").split(/\r?\n/)) {
            const item = rawItem.trim();
            if (!item || item.startsWith("#")) continue;
            const id = canonicalPrompt(item);
            if (seen.has(id)) duplicateCount += 1;
            seen.add(id);
            requests.push(item);
        }
        return { requests, duplicateCount };
    }

    async function generateBatch(config, parentRun = null) {
        if (state.active && state.active !== parentRun) return "已有队列任务正在运行";
        const parsed = parseRequests(config.request);
        if (!parsed.requests.length) return "缺少每轮创作要求";
        const run = parentRun || await beginRun("llm");
        if (!run) return "已有队列任务正在运行";
        run.phase = "llm";
        run.target = null;
        const batchId = createId("batch");
        const queuedPrompts = new Set(state.queue.map((row) => canonicalPrompt(row.prompt)));
        const allowRepeat = config.allowRepeat !== false;
        const allowDuplicateOutput = Boolean(config.allowDuplicateOutput);
        state.lastBatchRowIds = [];
        let duplicateOutputCount = 0;
        let skippedRequestCount = 0;
        try {
            for (const request of parsed.requests) {
                assertActive(run);
                const requestId = canonicalPrompt(request);
                if (!allowRepeat && state.requestIds.has(requestId)) {
                    skippedRequestCount += 1;
                    continue;
                }
                const button = ensureLlmIdle();
                const prompt = await runStudioGeneration(run, request, button);
                assertActive(run);
                state.requestIds.add(requestId);
                const promptKey = canonicalPrompt(prompt);
                if (!allowDuplicateOutput && queuedPrompts.has(promptKey)) {
                    duplicateOutputCount += 1;
                    saveQueue();
                    continue;
                }
                const row = {
                    index: state.queue.length + 1,
                    id: createId("row"),
                    batchId,
                    requestId,
                    prompt: prompt.trim(),
                    status: STATUS.pending,
                };
                state.queue.push(row);
                state.lastBatchRowIds.push(row.id);
                queuedPrompts.add(promptKey);
                saveQueue();
                renderQueue();
            }
            const added = state.lastBatchRowIds.length;
            const persistence = state.persistent ? "" : "；当前队列未持久化 (not persistent)";
            const message = `Prompt 批量生成完成，新增 ${added} 条${persistence}`;
            render("success", message, `重复要求作为独立任务 ${parsed.duplicateCount} 条，历史请求跳过 ${skippedRequestCount} 条，重复结果 ${duplicateOutputCount} 条`);
            return message;
        } catch (error) {
            const message = String(error?.message || error);
            render(message === "已取消" ? "warning" : "error", "Prompt 批量生成已停止", message);
            return message;
        } finally {
            if (!parentRun) finishRun(run);
        }
    }

    async function runStored(config, rowIds = null, parentRun = null) {
        if (state.active && state.active !== parentRun) return "已有队列任务正在运行";
        const selectedIds = rowIds ? new Set(rowIds) : null;
        const pending = state.queue.filter((row) => row.status !== STATUS.completed && (!selectedIds || selectedIds.has(row.id)));
        if (!pending.length) return "没有待生图 Prompt";
        const target = config.target === "img2img" ? "img2img" : "txt2img";
        const mode = config.writeMode === "append" ? "append" : "replace";
        const targetInput = root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`);
        if (!targetInput) throw new Error(`未找到 ${target} Prompt 输入框`);
        const basePrompt = String(targetInput.value || "");
        const run = parentRun || await beginRun("forge", target);
        if (!run) return "已有队列任务正在运行";
        run.phase = "forge";
        run.target = target;
        let currentRow = null;
        let completed = 0;
        try {
            for (const row of pending) {
                assertActive(run);
                ensureForgeIdle(target);
                currentRow = row;
                row.status = STATUS.running;
                renderQueue();
                writePrompt(row.prompt, target, mode, basePrompt);
                const generate = findButton(`${target}_generate`);
                if (!generate) throw new Error(`未找到 ${target} 生图按钮`);
                await runForgeGeneration(target, run, generate);
                assertActive(run);
                row.status = STATUS.completed;
                currentRow = null;
                completed += 1;
                saveQueue();
                renderQueue();
            }
            const message = `队列生图完成，共 ${completed} 条${state.persistent ? "" : "；状态未持久化 (not persistent)"}`;
            render("success", message);
            return message;
        } catch (error) {
            const message = String(error?.message || error);
            render(message === "已取消" ? "warning" : "error", "队列生图已停止", message);
            return message;
        } finally {
            if (currentRow?.status === STATUS.running) {
                currentRow.status = STATUS.pending;
                saveQueue();
                renderQueue();
            }
            if (!parentRun) finishRun(run);
        }
    }

    async function generateAndRun(config) {
        if (state.active) return "已有队列任务正在运行";
        const run = await beginRun("llm");
        if (!run) return "已有队列任务正在运行";
        const continuous = Boolean(config.continuous);
        const parsedCycles = Number(config.cycles);
        const cycleLimit = continuous
            ? (Number.isFinite(parsedCycles) ? Math.max(0, Math.floor(parsedCycles)) : 1)
            : 1;
        let completedCycles = 0;
        try {
            while (cycleLimit === 0 || completedCycles < cycleLimit) {
                assertActive(run);
                const generated = await generateBatch({
                    ...config,
                    allowRepeat: true,
                    allowDuplicateOutput: continuous,
                }, run);
                if (!String(generated).startsWith("Prompt 批量生成完成")) return generated;
                const rowIds = state.lastBatchRowIds.slice();
                if (!rowIds.length) return "本轮没有新增 Prompt";
                const generatedImages = await runStored(config, rowIds, run);
                if (!String(generatedImages).startsWith("队列生图完成")) return generatedImages;
                completedCycles += 1;
                if (continuous) {
                    render("success", `持续自动生图已完成 ${completedCycles} 轮`, cycleLimit ? `计划 ${cycleLimit} 轮` : "将持续运行到取消");
                }
            }
            return continuous ? `持续自动生图完成，共 ${completedCycles} 轮` : `生成并生图完成，共 ${completedCycles} 轮`;
        } finally {
            finishRun(run);
        }
    }

    function clearQueue() {
        if (state.active) return "运行中不能清空队列";
        state.queue = [];
        state.requestIds.clear();
        state.lastBatchRowIds = [];
        const persisted = saveQueue();
        renderQueue();
        const message = persisted ? "已清空待生图队列" : "已清空内存队列；未持久化 (not persistent)";
        render(persisted ? "success" : "warning", message);
        return message;
    }

    function cancel() {
        const run = state.active;
        if (!run) return "当前没有运行中的任务";
        run.cancelled = true;
        if (run.phase === "forge" && run.target) {
            const interrupt = find(`${run.target}_interrupt`);
            if (isVisible(interrupt)) interrupt.click();
        }
        render("warning", "正在取消当前阶段", "迟到结果不会写入队列");
        return "正在取消当前阶段";
    }

    if (typeof window.addEventListener === "function") {
        window.addEventListener("storage", (event) => {
            if (event.key !== QUEUE_KEY) return;
            try {
                if (event.newValue) JSON.parse(event.newValue);
            } catch {
                state.persistent = false;
                state.lastSaveFailure = "invalid";
                render("warning", "自动队列状态无法读取", "检测到损坏的队列数据，当前页继续使用内存队列");
                return;
            }
            if (!state.active) loadQueue();
        });
    }
    loadQueue();
    window.setTimeout(renderQueue, 1000);
    window.llmPromptStudioAutoLoop = {
        generateBatch,
        generateAndRun,
        runStored,
        inlineOnce,
        inlineLoop,
        cancelInline,
        clearQueue,
        cancel,
        start: generateBatch,
    };
})();
