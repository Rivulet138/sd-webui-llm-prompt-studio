(function () {
    "use strict";

    const state = {
        running: false,
        cancelled: false,
        runId: 0,
        phase: "idle",
        target: "txt2img",
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

    function setValue(id, next) {
        const host = find(id);
        const input = host?.querySelector("textarea, input, select");
        if (!input) throw new Error(`未找到控件：${id}`);
        const prototype = input instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
        if (setter) setter.call(input, String(next ?? ""));
        else input.value = String(next ?? "");
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
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
        host.innerHTML = `<div class="lps-status lps-status--${kind === "success" ? "success" : kind === "error" ? "error" : "warning"}" role="status" aria-live="polite"><strong>${escapeHtml(headline)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}</div>`;
    }

    function renderLog(rows) {
        const host = find("llm_prompt_studio_auto_loop_log");
        if (!host) return;
        host.innerHTML = rows.map((row) => `<div class="lps-auto-loop-row"><span>${row.index}</span><span>${escapeHtml(row.status)}</span><code>${escapeHtml(row.prompt)}</code></div>`).join("");
    }

    async function waitForStudioGeneration(runId, beforeOutput, beforeStatus, timeoutMs = 300000) {
        const button = findButton("llm_prompt_studio_generate_button");
        const output = find("llm_prompt_studio_output")?.querySelector("textarea, input");
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
        const input = root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`);
        if (!input) throw new Error(`未找到 ${target} Prompt 输入框`);
        const current = String(input.value || "");
        const next = mode === "append" && current.trim()
            ? `${current.replace(/[\s,]+$/, "")}, ${String(prompt).replace(/^[\s,]+/, "")}`
            : String(prompt).trim();
        setValue(`${target}_prompt`, next);
        input.focus({ preventScroll: true });
    }

    async function start(config) {
        if (state.running) {
            render("warning", "自动循环已在运行", "请先取消当前任务");
            return "自动循环已在运行";
        }
        const target = config.target === "img2img" ? "img2img" : "txt2img";
        const continuous = Boolean(config.continuous);
        const count = Math.floor(Number(config.count));
        if (!continuous && (!Number.isFinite(count) || count < 1)) {
            render("warning", "循环次数无效", "请输入大于等于 1 的整数，或勾选持续运行");
            return "循环次数无效";
        }
        const request = String(config.request || "").trim();
        if (!request) {
            render("warning", "缺少每轮创作要求", "请填写 LLM 每轮要生成的创作要求");
            return "缺少每轮创作要求";
        }
        state.running = true;
        state.cancelled = false;
        const runId = ++state.runId;
        const rows = [];
        let completedCount = 0;
        const maxRounds = continuous ? Infinity : count;
        const varyRounds = continuous || count > 1;
        state.target = target;
        render("warning", "自动循环准备中", continuous ? "持续运行，点击取消可停止" : `计划执行 ${count} 轮`);
        try {
            for (let index = 1; index <= maxRounds; index += 1) {
                if (state.cancelled || state.runId !== runId) break;
                const llmButton = findButton("llm_prompt_studio_generate_button");
                if (!llmButton || llmButton.disabled) throw new Error("LLM Studio 当前已有生成任务，请等待完成后再启动自动循环");
                if (isBusy(target)) throw new Error(`${target} 当前已有生图任务，请等待完成后再启动自动循环`);
                render("warning", `第 ${index}${continuous ? "" : `/${count}`} 轮：生成 Prompt`, "");
                const promptRequest = varyRounds ? `${request}\n这是第 ${index} 轮，请生成与前几轮不同的画面方案。` : request;
                setValue("llm_prompt_studio_request", promptRequest);
                setValue("llm_prompt_studio_source_tags", "");
                const output = find("llm_prompt_studio_output")?.querySelector("textarea, input");
                const studioStatus = find("llm_prompt_studio_status");
                const beforeOutput = String(output?.value || "");
                const beforeStatus = String(studioStatus?.textContent || "");
                state.phase = "llm";
                llmButton.click();
                const prompt = await waitForStudioGeneration(runId, beforeOutput, beforeStatus);
                if (state.cancelled || state.runId !== runId) break;
                writePrompt(prompt, target, config.writeMode === "append" ? "append" : "replace");
                rows.push({ index, status: "Prompt 已写入", prompt: prompt.slice(0, 300) });
                if (rows.length > 100) rows.shift();
                renderLog(rows);
                render("warning", `第 ${index}${continuous ? "" : `/${count}`} 轮：正在生图`, `${target} 已开始`);
                const generate = root().querySelector(`#${target}_generate`);
                if (!generate) throw new Error(`未找到 ${target} 生图按钮`);
                if (isBusy(target)) throw new Error(`${target} 在本轮开始前已被其他任务占用，自动循环已停止`);
                const gallery = root().querySelector(`#${target}_gallery`);
                const beforeGallery = gallery?.innerHTML || "";
                state.phase = "forge";
                generate.click();
                await waitForForgeGeneration(target, runId, beforeGallery);
                rows[rows.length - 1].status = "已完成";
                completedCount += 1;
                renderLog(rows);
                render("success", `第 ${index}${continuous ? "" : `/${count}`} 轮完成`, "");
            }
            const message = state.cancelled ? `已取消，保留 ${completedCount} 轮结果` : `自动循环完成，共 ${completedCount} 轮`;
            render(state.cancelled ? "warning" : "success", message, "");
            return message;
        } catch (error) {
            const message = String(error?.message || error);
            if (message === "已取消") {
                const cancelledMessage = `已取消，保留 ${completedCount} 轮结果`;
                render("warning", cancelledMessage, "");
                return cancelledMessage;
            }
            render("error", "自动循环已停止", message);
            return message;
        } finally {
            state.running = false;
            state.phase = "idle";
        }
    }

    function cancel() {
        if (!state.running) {
            render("warning", "当前没有运行中的自动循环", "");
            return "当前没有运行中的自动循环";
        }
        state.cancelled = true;
        state.runId += 1;
        if (state.phase === "forge") {
            const interrupt = root().querySelector(`#${state.target}_interrupt`);
            if (isVisible(interrupt)) interrupt.click();
        }
        render("warning", "正在取消自动循环", "当前生图结束后将停止下一轮");
        return "正在取消自动循环";
    }

    window.llmPromptStudioAutoLoop = { start, cancel };
})();
