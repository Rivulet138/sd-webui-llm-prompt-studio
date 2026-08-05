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

    function setChecked(id, checked) {
        const element = find(id)?.querySelector("input[type=checkbox]");
        if (!element) return null;
        const previous = Boolean(element.checked);
        if (previous !== Boolean(checked)) {
            element.checked = Boolean(checked);
            element.dispatchEvent(new Event("input", { bubbles: true }));
            element.dispatchEvent(new Event("change", { bubbles: true }));
        }
        return previous;
    }

    function wait(ms) {
        return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>\"']/g, (char) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
        }[char]));
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
        try {
            window.localStorage.setItem(QUEUE_KEY, JSON.stringify(state.queue));
        } catch (error) {
            console.warn("LLM Prompt Studio queue persistence failed", error);
        }
    }

    function loadQueue() {
        try {
            const raw = window.localStorage.getItem(QUEUE_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            state.queue = Array.isArray(parsed)
                ? parsed.filter((row) => row && typeof row.prompt === "string").map((row, index) => ({
                    index: Number(row.index) || index + 1,
                    prompt: row.prompt.trim(),
                    status: row.status === "已完成" ? "已完成" : "待生图",
                }))
                : [];
        } catch (error) {
            state.queue = [];
        }
        renderQueue();
    }

    async function waitForStudioGeneration(runId, beforeOutput, beforeStatus, timeoutMs = 300000) {
        const button = findButton("llm_prompt_studio_generate_button");
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
            if (currentOutput && sawBusy && !busy) {
                await wait(100);
                return String(output?.value || "").trim() || currentOutput;
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

    function writePrompt(prompt, target, mode) {
        const targetInput = root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`);
        if (!targetInput) throw new Error(`未找到 ${target} Prompt 输入框`);
        const current = String(targetInput.value || "");
        const next = mode === "append" && current.trim()
            ? `${current.replace(/[\s,]+$/, "")}, ${String(prompt).replace(/^[\s,]+/, "")}`
            : String(prompt).trim();
        setValue(`${target}_prompt`, next);
        targetInput.focus({ preventScroll: true });
    }

    function ensureLlmIdle() {
        const llmButton = findButton("llm_prompt_studio_generate_button");
        if (!llmButton || llmButton.disabled) throw new Error("LLM Studio 当前已有生成任务，请等待完成后再启动");
        return llmButton;
    }

    function ensureForgeIdle(target) {
        if (isBusy(target)) throw new Error(`${target} 当前已有生图任务，请等待完成后再启动`);
    }

    async function generateBatch(config) {
        if (state.running) return "已有队列任务正在运行";
        const continuous = Boolean(config.continuous);
        const count = Math.floor(Number(config.count));
        if (!continuous && (!Number.isFinite(count) || count < 1)) {
            render("warning", "批量数量无效", "请输入大于等于 1 的整数，或勾选持续生成");
            return "批量数量无效";
        }
        const request = String(config.request || "").trim();
        if (!request) {
            render("warning", "缺少每轮创作要求", "请先填写批量生成要求");
            return "缺少每轮创作要求";
        }
        state.running = true;
        state.cancelled = false;
        state.phase = "llm";
        const runId = ++state.runId;
        const maxRounds = continuous ? Infinity : count;
        const cachePrevious = setChecked("llm_prompt_studio_cache_result", false);
        const scorePrevious = setChecked("llm_prompt_studio_auto_score", false);
        let added = 0;
        try {
            await wait(150);
            for (let index = 1; index <= maxRounds; index += 1) {
                if (state.cancelled || state.runId !== runId) break;
                const button = ensureLlmIdle();
                render("warning", `正在批量生成 Prompt：第 ${index}${continuous ? "" : `/${count}`} 条`, "本阶段不会评分，也不会写入 Prompt 缓存");
                const promptRequest = continuous || count > 1
                    ? `${request}\n这是第 ${index} 条，请生成不同的画面方案。`
                    : request;
                setValue("llm_prompt_studio_request", promptRequest);
                setValue("llm_prompt_studio_source_tags", "");
                const output = input("llm_prompt_studio_output");
                const statusHost = find("llm_prompt_studio_status");
                const beforeOutput = String(output?.value || "");
                const beforeStatus = String(statusHost?.textContent || "");
                button.click();
                const prompt = await waitForStudioGeneration(runId, beforeOutput, beforeStatus);
                state.queue.push({ index: state.queue.length + 1, prompt, status: "待生图" });
                added += 1;
                saveQueue();
                renderQueue();
            }
            const message = state.cancelled ? `已取消 Prompt 批量生成，新增 ${added} 条` : `Prompt 批量生成完成，新增 ${added} 条，已保存待生图队列`;
            render(state.cancelled ? "warning" : "success", message, "请检查队列后再点击“投入队列生图”");
            return message;
        } catch (error) {
            const message = String(error?.message || error);
            render(message === "已取消" ? "warning" : "error", "Prompt 批量生成已停止", message);
            return message;
        } finally {
            if (cachePrevious !== null) setChecked("llm_prompt_studio_cache_result", cachePrevious);
            if (scorePrevious !== null) setChecked("llm_prompt_studio_auto_score", scorePrevious);
            state.running = false;
            state.phase = "idle";
        }
    }

    async function runStored(config) {
        if (state.running) return "已有队列任务正在运行";
        const pending = state.queue.filter((row) => row.status !== "已完成");
        if (!pending.length) {
            render("warning", "没有待生图 Prompt", "请先批量生成或清空后重新生成");
            return "没有待生图 Prompt";
        }
        const target = config.target === "img2img" ? "img2img" : "txt2img";
        const mode = config.writeMode === "append" ? "append" : "replace";
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
                writePrompt(row.prompt, target, mode);
                const generate = root().querySelector(`#${target}_generate`);
                if (!generate) throw new Error(`未找到 ${target} 生图按钮`);
                const gallery = root().querySelector(`#${target}_gallery`);
                const beforeGallery = gallery?.innerHTML || "";
                generate.click();
                await waitForForgeGeneration(target, runId, beforeGallery);
                row.status = "已完成";
                currentRow = null;
                completed += 1;
                saveQueue();
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
                saveQueue();
                renderQueue();
            }
            state.running = false;
            state.phase = "idle";
        }
    }

    function clearQueue() {
        if (state.running) return "运行中不能清空队列";
        state.queue = [];
        saveQueue();
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

    loadQueue();
    window.setTimeout(renderQueue, 1000);
    window.llmPromptStudioAutoLoop = {
        generateBatch,
        runStored,
        clearQueue,
        cancel,
        start: generateBatch,
    };
})();
