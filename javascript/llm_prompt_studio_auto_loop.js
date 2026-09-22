(function () {
    "use strict";

    const QUEUE_KEY = "llm_prompt_studio_auto_loop_queue_v1";
    const MAX_LOG_ROWS = 100;
    const STATUS = Object.freeze({ pending: "pending", running: "running", completed: "completed" });
    const FAILURE_PATTERN = /fail|error|timeout|refused|interrupted|失败|错误|超时|拒绝|中断/i;
    const SUCCESS_PATTERN = /completed|finished|done|success|生成完成|已完成/i;
    const CONTENT_BLOCK_PATTERN = /(SFW|NSFW|安全|成人|内容).*(拦截|拒绝|阻止|blocked|safety)|blocked.*(SFW|safety)/i;

    function isContentBlocked(value) {
        return CONTENT_BLOCK_PATTERN.test(String(value?.message || value || ""));
    }

    function isRejectedPrompt(value) {
        const message = String(value?.message || value || "");
        return isContentBlocked(message)
            || /assistant text|finish_reason\s*[=:]\s*length|LLM 未返回可用 Prompt|输出为空|不是英文|输出包含非英文|not English/i.test(message);
    }

    function promptRetryDelay(attempt) {
        return Math.min(500 * (2 ** Math.min(attempt, 4)), 5000);
    }
    const state = {
        queue: [],
        requestIds: new Set(),
        active: null,
        sequence: 0,
        persistent: true,
        lastSaveFailure: "",
        lastBatchRowIds: [],
        selectedRowIds: new Set(),
        dispatchBusy: false,
    };
    const inlineRuns = { txt2img: null, img2img: null };
    const linkedRuns = { txt2img: null, img2img: null };
    const inlineCacheCursors = { txt2img: 0, img2img: 0 };
    const processedCacheCursors = { txt2img: 0, img2img: 0 };
    const serverQueueWatchers = new Map();
    const CHOICE_ALIASES = Object.freeze({
        "Danbooru 标签": "Danbooru Tags",
        "Danbooru 标签 + 自然语言": "Danbooru + Natural",
        "自然语言": "Natural Language",
        "NoobAI 标签": "NoobAI Tags",
        "Anima 标签": "Anima Tags",
        "Krea 2 自然语言": "Krea 2 Natural",
        "自动 / 使用底模默认规则": "Auto / checkpoint default",
        "LLM 自动生成": "llm",
        "缓存顺序读取": "cache",
        "原始缓存顺序读取": "cache",
        "原始缓存库": "cache",
        "处理结果库": "processed_cache",
        "处理结果库顺序读取": "processed_cache",
        "处理结果顺序读取": "processed_cache",
        "追加到后面": "append_end",
        "追加到前面": "append_start",
        "替换当前 Prompt": "replace",
        "插入到标记位置": "marker",
        "追加": "append",
        "覆盖": "replace",
        "正面 Prompt": "txt2img",
    });
    const inspirationRequests = [
        "日常生活场景", "校园时光场景", "节庆活动场景", "奇幻冒险场景",
        "科幻探索场景", "自然观察场景", "城市夜景场景", "旅行见闻场景",
        "运动瞬间场景", "职业工作场景", "传统文化场景", "海边度假场景",
        "森林秘境场景", "未来都市场景", "温馨居家场景",
    ];
    let backgroundTimer = undefined;

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

    function componentValue(id, fallback = "") {
        const host = find(id);
        if (!host) return fallback;
        const checked = host.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked');
        if (checked && checked.type !== "checkbox") return normalizeChoiceValue(checked.value, fallback);
        const element = host.querySelector("textarea, input, select");
        return element ? normalizeChoiceValue(element.value, fallback) : fallback;
    }

    function readTxt2imgSettings() {
        const raw = (id, fallback = "") => componentValue(`txt2img_${id}`, fallback);
        const number = (id, fallback) => {
            const value = Number(raw(id, fallback));
            return Number.isFinite(value) ? value : fallback;
        };
        const checked = (id, fallback = false) => {
            const host = find(`txt2img_${id}`);
            const element = host?.querySelector('input[type="checkbox"]');
            return element ? Boolean(element.checked) : fallback;
        };
        return JSON.stringify({
            "negative_prompt": String(componentValue("txt2img_neg_prompt", "") || ""),
            steps: number("steps", 20), width: number("width", 1024), height: number("height", 1024),
            cfg_scale: number("cfg_scale", 6), distilled_cfg_scale: number("distilled_cfg_scale", 3),
            "sampler_name": raw("sampling", "Euler"), seed: number("seed", -1),
            batch_size: number("batch_size", 1), batch_count: number("batch_count", 1),
            enable_hr: checked("hr", false), hr_scale: number("hr_scale", 2),
            hr_resize_x: number("hr_resize_x", 0), hr_resize_y: number("hr_resize_y", 0),
            denoising_strength: number("denoising_strength", 0.6),
            hr_second_pass_steps: number("hires_steps", 0), hr_upscaler: raw("hr_upscaler", ""),
            hr_cfg: number("hr_cfg", 6), hr_distilled_cfg: number("hr_distilled_cfg", 3),
        });
    }

    function normalizeChoiceValue(value, fallback = "") {
        const text = String(value ?? fallback).trim();
        return CHOICE_ALIASES[text] || text;
    }

    async function studioFetch(url, options = {}) {
        const response = await window.fetch(url, {
            credentials: "same-origin",
            ...options,
        });
        if (response.status === 401 || response.status === 403) {
            throw new Error(`Studio API 认证失败 (HTTP ${response.status})，请先在当前页面完成登录或检查 --api-auth 配置`);
        }
        return response;
    }

    function setValue(id, value, options = {}) {
        const element = input(id);
        if (!element) throw new Error(`未找到控件: ${id}`);
        const prototype = element instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
        if (setter) setter.call(element, String(value ?? ""));
        else element.value = String(value ?? "");
        element.dispatchEvent(new Event("input", { bubbles: true }));
        if (options.emitChange !== false) element.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function createBackgroundTimer() {
        if (
            typeof window.Worker !== "function"
            || typeof window.Blob !== "function"
            || typeof window.URL?.createObjectURL !== "function"
        ) return null;
        try {
            const source = "self.onmessage=function(event){setTimeout(function(){self.postMessage(event.data.id);},event.data.ms);};";
            const url = window.URL.createObjectURL(new window.Blob([source], { type: "text/javascript" }));
            const worker = new window.Worker(url);
            window.URL.revokeObjectURL(url);
            const pending = new Map();
            let sequence = 0;
            worker.onmessage = (event) => {
                const resolve = pending.get(event.data);
                if (!resolve) return;
                pending.delete(event.data);
                resolve();
            };
            worker.onerror = () => {
                for (const resolve of pending.values()) resolve();
                pending.clear();
                backgroundTimer = null;
            };
            return (ms) => new Promise((resolve) => {
                sequence += 1;
                pending.set(sequence, resolve);
                worker.postMessage({ id: sequence, ms });
            });
        } catch {
            return null;
        }
    }

    function wait(ms) {
        if (Boolean(document.hidden)) {
            if (backgroundTimer === undefined) backgroundTimer = createBackgroundTimer();
            if (backgroundTimer) return backgroundTimer(ms);
        }
        return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    async function waitForUiSignal(ms) {
        // This used to race a MutationObserver against the timer so a status change could wake the
        // waiting loop early. A running Forge job mutates the status, interrupt and generate nodes on
        // every progress frame, so the mutation always won the race and the generation loop
        // re-entered at mutation rate instead of once per interval, burning the main thread and
        // making the page feel unresponsive. The interval is now a hard floor.
        await wait(ms);
    }

    function createTimeoutBudget(timeoutMs) {
        const now = () => typeof window.performance?.now === "function"
            ? window.performance.now()
            : Date.now();
        const deadline = now() + timeoutMs;
        return {
            expired() {
                return now() >= deadline;
            },
        };
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
                selected: source.selected !== false,
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

    function renderInline(slot, kind, headline, detail = "") {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        const host = find(`llm_prompt_studio_${normalizedSlot}_inline_loop_status`);
        if (!host) return;
        const tone = kind === "success" ? "success" : kind === "error" ? "error" : "warning";
        const run = linkedRuns[normalizedSlot];
        if (run && !run.cancelled) {
            detail = [`${run.limit ? `计划 ${run.limit} 轮` : "无限"} · 已完成 ${run.completed} 轮`, detail].filter(Boolean).join(" · ");
        }
        host.innerHTML = `<div class="lps-status lps-status--${tone}" role="status" aria-live="polite"><strong>${escapeHtml(headline)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}</div>`;
    }

    function renderQueue() {
        const host = find("llm_prompt_studio_auto_loop_log");
        if (!host) return;
        const rows = state.queue.slice(-MAX_LOG_ROWS);
        const selected = state.queue.filter((row) => row.selected !== false).length;
        const pending = state.queue.filter((row) => row.status !== STATUS.completed).length;
        const summary = `<div class="lps-queue-summary" role="status" aria-live="polite"><strong>队列 ${state.queue.length} 条</strong><span>已选 ${selected} · 待生图 ${pending}</span></div>`;
        host.innerHTML = rows.length
            ? summary + rows.map((row) => `<label class="lps-auto-loop-row"><input type="checkbox" class="lps-auto-loop-select" data-row-id="${escapeHtml(row.id)}" ${row.selected !== false ? "checked" : ""}><span>${row.index}</span><span>${escapeHtml(row.status)}</span><code>${escapeHtml(row.prompt)}</code></label>`).join("")
            : summary + '<div class="lps-auto-loop-empty">暂无已保存 Prompt。</div>';
        host.querySelectorAll(".lps-auto-loop-select").forEach((checkbox) => {
            checkbox.addEventListener("change", () => {
                const row = state.queue.find((item) => item.id === checkbox.dataset.rowId);
                if (row) row.selected = Boolean(checkbox.checked);
                saveQueue();
            });
        });
    }

    function saveQueue() {
        state.persistent = false;
        state.lastSaveFailure = "";
        return true;
    }

    function loadQueue() {
        try {
            window.localStorage.removeItem(QUEUE_KEY);
            state.queue = [];
            state.requestIds = new Set();
            renderQueue();
        } catch (error) {
            state.queue = [];
            state.requestIds = new Set();
            state.persistent = false;
            renderQueue();
            render("error", "历史 Prompt 队列恢复失败", String(error?.message || error));
        }
    }

    function isControlShown(element) {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        return style.display !== "none";
    }

    function isForgeBusy(tab) {
        return isControlShown(find(`${tab}_interrupt`))
            || isControlShown(find(`${tab}_interrupting`))
            || Boolean(findButton(`${tab}_generate`)?.disabled);
    }

    function currentForgeTaskId(tab) {
        try {
            return String(window.localStorage.getItem(`${tab}_task_id`) || "");
        } catch {
            return "";
        }
    }

    function currentForgeLog(tab) {
        return String(find(`html_log_${tab}`)?.textContent || "").trim();
    }

    function currentForgeOutput(tab) {
        const ids = [`${tab}_gallery`, `generation_info_${tab}`, `html_info_${tab}`];
        const hosts = ids.map((id) => find(id)).filter(Boolean);
        return hosts.length ? hosts.map((host) => {
            const field = host.querySelector?.("textarea, input");
            return [host.innerHTML, field?.value, host.textContent, host.value].map((value) => String(value || "")).join("|");
        }).join("\n") : "";
    }

    function ownsActiveForgeTask(run) {
        if (!run?.forgeStarted || !run.target) return false;
        if (run.forgeTaskId) return currentForgeTaskId(run.target) === run.forgeTaskId;
        return isForgeBusy(run.target);
    }

    async function beginRun(phase, target = null) {
        if (state.active) return null;
        const run = {
            id: createId("run"), phase, target, cancelled: false, forgeStarted: false, forgeTaskId: "",
            basePrompts: Object.create(null), abortController: null,
        };
        state.active = run;
        saveQueue();
        return run;
    }

    function assertActive(run) {
        const isCurrent = run.scope === "inline"
            ? inlineRuns[run.slot] === run
            : run.scope === "linked"
                ? linkedRuns[run.slot] === run
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
        if (inlineRuns[slot] || linkedRuns[slot]) return null;
        const run = {
            id: createId("inline"), scope: "inline", slot, phase: "inline", target, cancelled: false,
            forgeStarted: false, forgeTaskId: "", abortController: null,
        };
        inlineRuns[slot] = run;
        return run;
    }

    function finishInlineRun(run) {
        if (inlineRuns[run.slot] === run) inlineRuns[run.slot] = null;
    }

    function freezeBasePrompt(run, target, targetInput) {
        if (!Object.prototype.hasOwnProperty.call(run.basePrompts, target)) {
            run.basePrompts[target] = String(targetInput?.value || "");
        }
        return run.basePrompts[target];
    }

    function selectedRowIds() {
        return state.queue.filter((row) => row.selected !== false).map((row) => row.id);
    }

    function selectAllRows() {
        state.queue.forEach((row) => { row.selected = true; });
        saveQueue();
        renderQueue();
        render("success", "已勾选全部 Prompt", "现在可以写入固定正面 Prompt 或投入队列生图");
        return "已勾选全部 Prompt";
    }

    function clearSelectedRows() {
        state.queue.forEach((row) => { row.selected = false; });
        saveQueue();
        renderQueue();
        render("warning", "已清空 Prompt 勾选", "队列内容仍保留");
        return "已清空 Prompt 勾选";
    }

    function writeSelectedToPositive() {
        const ids = selectedRowIds();
        if (!ids.length) return "请先勾选 Prompt";
        const rows = state.queue.filter((row) => ids.includes(row.id) && row.status !== STATUS.completed);
        if (!rows.length) return "所选 Prompt 已使用";
        const targetInput = root().querySelector("#txt2img_prompt textarea, #txt2img_prompt input");
        if (!targetInput) return "未找到固定正面 Prompt 框";
        const text = rows.map((row) => row.prompt).join(", ");
        setValue("txt2img_prompt", text);
        // Writing a prompt is separate from running Forge, so keep the row
        // pending and only clear the explicit selection.
        rows.forEach((row) => { row.selected = false; });
        saveQueue();
        renderQueue();
        return `已写入 ${rows.length} 条到正面 Prompt`;
    }

    function enqueuePrompt(prompt, request = "") {
        const value = String(prompt || "").trim();
        if (!value) throw new Error("LLM 未返回可用 Prompt");
        const row = {
            index: state.queue.length + 1,
            id: createId("row"),
            batchId: createId("batch"),
            requestId: canonicalPrompt(request),
            prompt: value,
            status: STATUS.pending,
            selected: true,
        };
        state.queue.push(row);
        state.lastBatchRowIds.push(row.id);
        saveQueue();
        renderQueue();
        return true;
    }

    async function waitForForgeGeneration(tab, run, beforeStatus, taskId = "", timeoutMs = 1800000, beforeLog = "", beforeOutput = "") {
        const statusHost = find(`${tab}_status`);
        let sawBusy = Boolean(taskId);
        let noOutputSince = 0;
        const budget = createTimeoutBudget(timeoutMs);
        const launchBudget = createTimeoutBudget(10000);
        while (true) {
            assertActive(run);
            const currentTaskId = currentForgeTaskId(tab);
            const taskTracked = Boolean(taskId) && currentTaskId === taskId;
            const taskReplaced = Boolean(taskId) && Boolean(currentTaskId) && currentTaskId !== taskId;
            const busy = taskId ? taskTracked : isForgeBusy(tab);
            sawBusy ||= busy;
            const currentStatus = String(statusHost?.textContent || "");
            const currentLog = currentForgeLog(tab);
            const currentOutput = currentForgeOutput(tab);
            const statusCompleted = currentStatus !== beforeStatus && SUCCESS_PATTERN.test(currentStatus);
            if (taskReplaced) {
                throw new Error(`${tab} generation task ownership changed; prompt returned to pending`);
            }
            if (!sawBusy && !taskId && !statusCompleted && launchBudget.expired()) {
                throw new Error(`${tab} Forge 未启动生图任务，请检查 Forge 队列或页面状态`);
            }
            if (!busy && (sawBusy || statusCompleted)) {
                const failure = [currentStatus, currentLog !== beforeLog ? currentLog : ""]
                    .filter(Boolean).join(" ");
                if (FAILURE_PATTERN.test(failure) || /out of memory|traceback|exception|cuda/i.test(failure)) {
                    throw new Error(failure);
                }
                if (taskId && beforeOutput && currentOutput === beforeOutput && !statusCompleted && currentLog === beforeLog) {
                    const now = typeof window.performance?.now === "function" ? window.performance.now() : Date.now();
                    if (!noOutputSince) noOutputSince = now;
                    if (now - noOutputSince < 1500) {
                        await waitForUiSignal(250);
                        continue;
                    }
                    throw new Error("Forge 任务结束但没有收到输出，可能是进度连接中断");
                }
                noOutputSince = 0;
                return;
            }
            if (budget.expired()) break;
            await waitForUiSignal(250);
        }
        throw new Error(`${tab} generation timeout; prompt returned to pending`);
    }

    function ensureForgeIdle(target) {
        if (isForgeBusy(target)) throw new Error(`${target} 当前已有生图任务`);
    }

    function inlineId(slot, suffix) {
        return `llm_prompt_studio_${slot}_inline_${suffix}`;
    }

    function sharedWorkflowValue(suffix, fallback = "") {
        const value = componentValue(`llm_prompt_studio_${suffix}`, "");
        return value || fallback;
    }

    function promptValue(slot) {
        return String(input(`${slot}_prompt`)?.value || "").trim();
    }

    function promptRawValue(slot) {
        return String(input(`${slot}_prompt`)?.value || "");
    }

    function splitPromptParts(value) {
        return String(value || "")
            .split(/\s*,\s*|\r?\n/)
            .map((part) => part.trim())
            .filter(Boolean);
    }

    function promptPartKey(value) {
        return canonicalPrompt(value).replace(/[()]/g, "");
    }

    function uniquePromptParts(value) {
        const seen = new Set();
        return splitPromptParts(value).filter((part) => {
            const key = promptPartKey(part);
            if (!key || seen.has(key)) return false;
            seen.add(key);
            return true;
        });
    }

    function removePromptOverlap(source, generated) {
        const sourceText = String(source || "").trim();
        const generatedText = String(generated || "").trim();
        if (!generatedText) return "";
        if (!sourceText || canonicalPrompt(sourceText) === canonicalPrompt(generatedText)) return sourceText ? "" : generatedText;
        const sourceKey = canonicalPrompt(sourceText);
        const generatedKey = canonicalPrompt(generatedText);
        if (sourceKey.length >= 24 && generatedKey.includes(sourceKey)) {
            const escaped = sourceText.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
            const overlap = generatedText.replace(new RegExp(escaped, "i"), "");
            if (canonicalPrompt(overlap)) return overlap.replace(/^[\s,;:]+|[\s,;:]+$/g, "").trim();
        }
        if (generatedKey.startsWith(sourceKey)) {
            return generatedText.slice(sourceText.length).replace(/^[\s,;:]+/, "").trim();
        }
        const sourceParts = new Set(uniquePromptParts(sourceText).map(promptPartKey));
        const generatedParts = uniquePromptParts(generatedText).filter((part) => !sourceParts.has(promptPartKey(part)));
        return generatedParts.join(", ").trim();
    }

    function composePrompt(basePrompt, generated, mode, marker) {
        const rawBase = String(basePrompt || "");
        const base = rawBase.trim();
        const generatedText = String(generated || "").trim();
        if (mode === "replace") return generatedText;
        if (mode === "marker" && !base.includes(String(marker || "{{LLM}}").trim() || "{{LLM}}")) {
            throw new Error("当前 Prompt 中没有插入标记，请添加标记或更换写入方式");
        }
        const delta = removePromptOverlap(base, generated);
        if (!delta) return base;
        const join = (left, right) => [String(left || "").trim().replace(/[\s,]+$/, ""), String(right || "").trim().replace(/^[\s,]+/, "")].filter(Boolean).join(", ");
        if (mode === "append_start") return join(delta, base);
        if (mode === "marker") {
            const token = String(marker || "{{LLM}}").trim() || "{{LLM}}";
            if (base.includes(token)) return base.replace(token, delta);
        }
        return join(base, delta);
    }

    function immutableTechnicalTokens(source) {
        const text = String(source || "");
        const loras = text.match(/<lora:[^>]+>/gi) || [];
        const parts = uniquePromptParts(text).filter((part) => !/^<lora:[^>]+>$/i.test(part));
        return [...loras, ...parts.filter((part) => /^[A-Za-z0-9_:.\-]+$/.test(part) && !/[\s]/.test(part))];
    }

    function preservesImmutableTechnicalTokens(source, candidate) {
        const target = String(candidate || "");
        return immutableTechnicalTokens(source).every((token) => target.includes(token));
    }

    function composeInlinePrompt(base, generated, config) {
        const mode = config.writeMode || "append_end";
        const next = composePrompt(base, generated, mode, config.marker);
        if (mode !== "replace" && !preservesImmutableTechnicalTokens(base, next)) {
            throw new Error("固定 Prompt 完整性校验失败，已拒绝写入 Forge");
        }
        return next;
    }

    function inlineSourceName(config) {
        return config.source === "cache" ? "原始缓存库" : config.source === "processed_cache" ? "处理结果库" : "LLM";
    }

    async function requestPromptApi(run, path, payload) {
        assertActive(run);
        if (typeof window.fetch !== "function") throw new Error("浏览器不支持 Fetch，无法调用 LLM 服务");
        if (run.abortController) throw new Error("LLM Studio 当前已有生成任务");
        const controller = typeof window.AbortController === "function" ? new window.AbortController() : null;
        const requestId = path === "inline-generate" ? createId("inline-request") : "";
        const requestPayload = requestId ? { ...(payload || {}), request_id: requestId } : (payload || {});
        run.abortController = controller;
        if (requestId) run.requestId = requestId;
        try {
            const response = await studioFetch(`/llm-prompt-studio/v1/${path}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                cache: "no-store",
                body: JSON.stringify(requestPayload),
                ...(controller ? { signal: controller.signal } : {}),
            });
            let data = null;
            try { data = await response.json(); } catch { data = null; }
            if (!response.ok) {
                const detail = data?.detail || data?.error || `HTTP ${response.status}`;
                throw new Error(String(detail));
            }
            const prompt = String(data?.prompt || "").trim();
            if (!prompt) throw new Error(String(data?.status || "LLM 未返回可用 Prompt"));
            assertActive(run);
            return data;
        } catch (error) {
            if (controller?.signal.aborted || run.cancelled) throw new Error("已取消");
            throw error;
        } finally {
            if (run.abortController === controller) run.abortController = null;
            if (run.requestId === requestId) run.requestId = "";
        }
    }

    function cancelInlineRequest(run) {
        const slot = run?.slot === "img2img" ? "img2img" : "txt2img";
        const requestId = String(run?.requestId || "").trim();
        if (!requestId || typeof window.fetch !== "function") return;
        // Compatibility marker for existing contract checks: the request is
        // sent through studioFetch, which delegates to window.fetch.
        // window.fetch("/llm-prompt-studio/v1/inline-cancel"
        void studioFetch("/llm-prompt-studio/v1/inline-cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
            body: JSON.stringify({ slot, request_id: requestId }),
        }).catch(() => {});
    }

    async function generateInlinePrompt(config, run) {
        const slot = config.slot === "img2img" ? "img2img" : "txt2img";
        const sharedConfig = {
            ...config,
            preset: sharedWorkflowValue("preset", config.preset || componentValue(inlineId(slot, "preset"), "Danbooru Tags")),
            baseModel: sharedWorkflowValue("base_model", config.baseModel || componentValue(inlineId(slot, "base_model"), "Auto / checkpoint default")),
            safety: sharedWorkflowValue("safety", config.safety || componentValue(inlineId(slot, "safety"), "SFW")),
        };
        const data = await requestPromptApi(run, "inline-generate", {
            slot,
            request: String(sharedConfig.request || ""),
            source_tags: config.fixedPrompt ?? promptValue(slot),
            ...(config.destination === "cache" || config.destination === "queue" ? { cache_result: true } : {}),
            variation: String(sharedConfig.variation || ""),
            preset: normalizeChoiceValue(sharedConfig.preset),
            base_model: normalizeChoiceValue(sharedConfig.baseModel),
            safety: normalizeChoiceValue(sharedConfig.safety),
            template: normalizeChoiceValue(sharedConfig.template || componentValue(inlineId(slot, "template_choice"), "general")),
        });
        return String(data.prompt).trim();
    }

    async function generateAutoLoopPrompt(config, run) {
        const data = await requestPromptApi(run, "auto-loop-generate", {
            request: String(config.request || ""),
            preset: normalizeChoiceValue(config.preset, "Danbooru Tags"),
            base_model: normalizeChoiceValue(config.baseModel, "Auto / checkpoint default"),
            safety: normalizeChoiceValue(config.safety, "SFW"),
            cache_result: Boolean(config.cacheResult),
        });
        return String(data.prompt).trim();
    }

    async function runForgeGeneration(tab, run, generate, afterClick = null) {
        const beforeStatus = String(find(`${tab}_status`)?.textContent || "");
        const beforeLog = currentForgeLog(tab);
        const beforeOutput = currentForgeOutput(tab);
        const previousTaskId = currentForgeTaskId(tab);
        let taskId = "";
        try {
            run.forgeLaunching = typeof afterClick === "function";
            if (document.hidden && window.opts && window.opts.keep_alive !== true) {
                throw new Error("Forge 未启用后台继续生成，请开启 keep_alive 后刷新页面");
            }
            generate.click();
            taskId = currentForgeTaskId(tab);
            if (typeof afterClick === "function") {
                // Gradio snapshots inputs on a later animation frame; submit() then assigns the task ID.
                const launchBudget = createTimeoutBudget(10000);
                while (!taskId || taskId === previousTaskId) {
                    if (launchBudget.expired()) throw new Error(`${tab} Forge 未启动生图任务，请检查 Forge 队列或页面状态`);
                    await wait(25);
                    taskId = currentForgeTaskId(tab);
                }
            }
            run.forgeStarted = true;
            run.forgeTaskId = taskId;
            run.forgeLaunching = false;
            if (run.cancelled && ownsActiveForgeTask(run)) findButton(`${tab}_interrupt`)?.click();
            if (typeof afterClick === "function") afterClick();
            assertActive(run);
            await waitForForgeGeneration(tab, run, beforeStatus, taskId, 1800000, beforeLog, beforeOutput);
        } finally {
            run.forgeLaunching = false;
            if (typeof afterClick === "function") afterClick();
            run.forgeStarted = false;
            run.forgeTaskId = "";
        }
    }

    async function readInlineCache(slot, run, processed = false) {
        const cursors = processed ? processedCacheCursors : inlineCacheCursors;
        const endpoint = processed ? "processed-cache" : "cache";
        const label = processed ? "处理结果库" : "原始缓存库";
        let afterId = cursors[slot];
        // Read by stable ID so large libraries, deletions and new records remain
        // reachable without loading every Prompt into the browser on each round.
        for (let attempt = 0; attempt < 2; attempt += 1) {
            assertActive(run);
            const response = await studioFetch(`/llm-prompt-studio/v1/${endpoint}?limit=1&after_id=${afterId}`, { cache: "no-store" });
            let data = null;
            try { data = await response.json(); } catch { data = null; }
            assertActive(run);
            if (!response.ok) throw new Error(String(data?.detail || `读取${label}失败：HTTP ${response.status}`));
            if (!Array.isArray(data?.records)) throw new Error(`${label}返回格式无效，请更新插件并重启 Forge`);
            if (!data.records.length) {
                if (afterId === 0) break;
                afterId = 0;
                continue;
            }
            const record = data.records[0];
            const nextId = Number(record?.id);
            const prompt = String(record?.prompt || "").trim();
            if (!Number.isSafeInteger(nextId) || nextId <= afterId || !prompt) {
                throw new Error(`${label}返回无效记录，请更新插件并重启 Forge`);
            }
            cursors[slot] = nextId;
            return prompt;
        }
        throw new Error(processed ? "处理结果库为空，请先完成缓存处理" : "原始缓存库为空，请先生成或导入 Prompt");
    }

    async function getInlinePrompt(config, run) {
        if (config.source === "cache") return readInlineCache(config.slot, run);
        if (config.source === "processed_cache") return readInlineCache(config.slot, run, true);
        return generateInlinePrompt(config, run);
    }

    async function getInlinePromptWithRetry(config, run) {
        for (let attempt = 0; ; attempt += 1) {
            assertActive(run);
            try {
                return await getInlinePrompt(config, run);
            } catch (error) {
                assertActive(run);
                if (!isRejectedPrompt(error) || (run.scope !== "linked" && attempt >= 1)) throw error;
                const delay = promptRetryDelay(attempt);
                renderInline(config.slot, "warning", "当前 Prompt 未通过校验，已丢弃并自动生成下一条",
                    `${String(error?.message || error)}；${delay / 1000} 秒后重试（第 ${attempt + 1} 次）`);
                // First-round generation and prefetch share this promise, including its retry delay.
                await wait(delay);
            }
        }
    }

    function inlineConfig(slot, overrides = {}) {
        const rawCount = overrides.count;
        const explicitCount = typeof rawCount === "number" || (typeof rawCount === "string" && rawCount.trim() !== "");
        const numericCount = Number(rawCount);
        const count = explicitCount && numericCount === 0 ? 0
            : (Number.isFinite(numericCount) ? Math.max(1, Math.min(200, Math.floor(numericCount))) : 1);
        return {
            slot,
            destination: normalizeChoiceValue(overrides.destination, "prompt"),
            count,
            writeMode: normalizeChoiceValue(overrides.writeMode, "append_end"),
            marker: String(overrides.marker || "{{LLM}}"),
            request: String(overrides.request || ""),
            variation: String(overrides.variation || ""),
            source: normalizeChoiceValue(overrides.source, "llm"),
            preset: normalizeChoiceValue(sharedWorkflowValue("preset", overrides.preset || componentValue(inlineId(slot, "preset"), "Danbooru Tags"))),
            baseModel: normalizeChoiceValue(sharedWorkflowValue("base_model", overrides.baseModel || componentValue(inlineId(slot, "base_model"), "Auto / checkpoint default"))),
            safety: normalizeChoiceValue(sharedWorkflowValue("safety", overrides.safety || componentValue(inlineId(slot, "safety"), "SFW"))),
            template: normalizeChoiceValue(overrides.template || componentValue(inlineId(slot, "template_choice"), "general")),
        };
    }

    function finishLinkedRun(run) {
        if (linkedRuns[run.slot] !== run) return;
        if (run.promptElement && run.promptListener) {
            run.promptElement.removeEventListener?.("input", run.promptListener);
            run.promptElement.removeEventListener?.("change", run.promptListener);
        }
        linkedRuns[run.slot] = null;
        setInlineLoopButtons(run.slot, false);
    }

    function setInlineLoopButtons(slot, running) {
        for (const suffix of ["start", "once"]) {
            const button = findButton(inlineId(slot, suffix));
            if (button) button.disabled = running;
        }
    }

    async function prepareLinkedPrompt(run) {
        assertActive(run);
        renderInline(run.slot, "warning", `正在准备第 ${run.count + 1} 条 ${inlineSourceName(run.config)} Prompt`, "Forge 会等待本轮 Prompt 准备完成");
        for (let attempt = 0; ; attempt += 1) {
            const current = promptValue(run.slot);
            // A user edit is a new fixed base. The previous generated override
            // is temporary and must never become the next round's base.
            if (run.promptEdited || (current !== run.lastWrittenPrompt && current !== run.lastUsedPrompt)) {
                run.basePrompt = promptRawValue(run.slot);
                run.lastWrittenPrompt = current;
                run.promptEdited = false;
            }
            const generated = await getInlinePromptWithRetry(run.config, run);
            assertActive(run);
            if (promptValue(run.slot) !== current) {
                run.preparedPrompt = "";
                run.preparedSource = "";
                renderInline(run.slot, "warning", "正面 Prompt 已变化，正在重新准备", "旧请求结果已丢弃");
                continue;
            }
            const next = composeInlinePrompt(run.basePrompt, generated, run.config);
            if (!next) continue;
            run.preparedPrompt = next;
            run.preparedSource = current;
            run.lastWrittenPrompt = current;
            renderInline(run.slot, "success", `第 ${run.count + 1} 条 ${inlineSourceName(run.config)} Prompt 已就绪`, "等待本轮生图；正面 Prompt 框保持不变");
            return next;
        }
    }

    function startLinkedRun(config) {
        const slot = config.slot === "img2img" ? "img2img" : "txt2img";
        const normalizedConfig = inlineConfig(slot, config);
        if (linkedRuns[slot]) {
            linkedRuns[slot].config = normalizedConfig;
            return linkedRuns[slot];
        }
        const run = {
            scope: "linked", slot, target: slot, cancelled: false, count: 0, completed: 0,
            config: normalizedConfig, basePrompt: promptRawValue(slot),
            lastWrittenPrompt: promptValue(slot), lastUsedPrompt: "", preparedPrompt: "", preparedSource: "",
            promptEdited: false, promptElement: null, promptListener: null, internalPromptWrite: false,
            nextPromise: null, abortController: null, requestId: "", generationLoopPromise: null,
            // For the inline queue destination, generate through the native
            // Forge button. Zero means unlimited until the user presses Stop.
            limit: normalizedConfig.destination === "queue" ? Number(normalizedConfig.count || 0) : 0,
        };
        run.promptElement = input(`${slot}_prompt`);
        run.promptListener = () => {
            if (!run.internalPromptWrite) run.promptEdited = true;
        };
        run.promptElement?.addEventListener("input", run.promptListener);
        run.promptElement?.addEventListener("change", run.promptListener);
        linkedRuns[slot] = run;
        return run;
    }

    async function ensureLinkedPrompt(run) {
        assertActive(run);
        if (run.preparedPrompt && run.preparedSource === promptValue(run.slot)) return run.preparedPrompt;
        run.preparedPrompt = "";
        run.preparedSource = "";
        if (!run.nextPromise) {
            run.nextPromise = prepareLinkedPrompt(run).finally(() => { run.nextPromise = null; });
        }
        return run.nextPromise;
    }

    function stopLinkedRun(slot, interruptForge = false) {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        const run = linkedRuns[normalizedSlot];
        if (!run) return;
        cancelInlineRequest(run);
        run.cancelled = true;
        run.abortController?.abort();
        if (!run.forgeLaunching) finishLinkedRun(run);
        if (interruptForge && ownsActiveForgeTask(run)) findButton(`${normalizedSlot}_interrupt`)?.click();
        renderInline(normalizedSlot, "warning", "前端连续生图已停止", `已完成 ${run.completed} 轮 · 已提交 ${run.count} 轮`);
    }

    function failLinkedRun(run, error) {
        if (linkedRuns[run.slot] !== run) return;
        stopLinkedRun(run.slot);
        renderInline(run.slot, "error", "前端连续生图已停止", `已完成 ${run.completed} 轮 · ${String(error?.message || error)}`);
    }

    function startInlineLoop(config) {
        const slot = config.slot === "img2img" ? "img2img" : "txt2img";
        if (linkedRuns[slot] || inlineRuns[slot]) return "当前内嵌面板已有任务正在运行";
        if (config.destination === "cache") return "仅保存到缓存请使用生成到缓存按钮";
        try {
            ensureForgeIdle(slot);
            if (!findButton(`${slot}_generate`) || !input(`${slot}_prompt`)) {
                throw new Error(`未找到 ${slot} 生图控件`);
            }
        } catch (error) {
            return String(error?.message || error);
        }
        const run = startLinkedRun(config);
        setInlineLoopButtons(slot, true);
        const log = find(inlineId(slot, "queue_log"));
        if (log) log.innerHTML = "";
        // Let Gradio finish the button event before starting the long-running loop.
        window.setTimeout(() => {
            if (linkedRuns[slot] === run) startLinkedGenerationLoop(run);
        }, 0);
        return `前端连续生图已开始（${run.limit ? `${run.limit} 轮` : "无限"}），使用当前 ${slot} 参数`;
    }

    function scheduleNextLinkedPrompt(run) {
        window.setTimeout(() => {
            if (run.cancelled || linkedRuns[run.slot] !== run || (run.limit > 0 && run.count >= run.limit)) return;
            ensureLinkedPrompt(run).catch((error) => failLinkedRun(run, error));
        }, 0);
    }

    function consumeLinkedPrompt(tab) {
        const slot = tab === "img2img" ? "img2img" : "txt2img";
        const run = linkedRuns[slot];
        if (!run?.preparedPrompt) return null;
        if (run.preparedSource !== promptValue(slot)) {
            run.preparedPrompt = "";
            run.preparedSource = "";
            return null;
        }
        const prompt = run.preparedPrompt;
        run.preparedPrompt = "";
        run.preparedSource = "";
        run.lastUsedPrompt = prompt;
        run.count += 1;
        renderInline(slot, "warning", `前端生图：正在提交第 ${run.count} 轮`, "使用当前 Forge 参数");
        return prompt;
    }

    async function submitLinkedForgeGeneration(run) {
        const slot = run.slot;
        const generate = findButton(`${slot}_generate`);
        if (!generate) throw new Error(`未找到 ${slot} 生成按钮`);
        await ensureLinkedPrompt(run);
        assertActive(run);
        ensureForgeIdle(slot);
        const original = run.basePrompt;
        const override = consumeLinkedPrompt(slot);
        if (!override) throw new Error("准备好的 Prompt 已过期，请重新生成");
        let restored = false;
        const writeTemporaryPrompt = (value) => {
            run.internalPromptWrite = true;
            try {
                setValue(`${slot}_prompt`, value, { emitChange: false });
            } finally {
                run.internalPromptWrite = false;
            }
        };
        const restore = () => {
            if (restored) return;
            restored = true;
            // Restore the fixed base unless an actual user input event happened
            // during launch/rendering. This prevents generated text from
            // becoming the next round's base while preserving LoRA edits.
            const current = promptValue(run.slot);
            const userEdited = run.promptEdited
                || (current !== run.lastWrittenPrompt && current !== run.lastUsedPrompt);
            if (userEdited) {
                run.basePrompt = promptRawValue(run.slot);
                run.lastWrittenPrompt = current;
                run.promptEdited = false;
            } else {
                writeTemporaryPrompt(run.basePrompt);
                run.lastWrittenPrompt = promptValue(run.slot);
            }
            scheduleNextLinkedPrompt(run);
        };
        writeTemporaryPrompt(override);
        try {
            await runForgeGeneration(slot, run, generate, restore);
        } finally {
            restore();
        }
    }

    function startLinkedGenerationLoop(run) {
        if (run.generationLoopPromise) return run.generationLoopPromise;
        run.generationLoopPromise = (async () => {
            while (linkedRuns[run.slot] === run) {
                assertActive(run);
                await submitLinkedForgeGeneration(run);
                assertActive(run);
                run.completed += 1;
                if (run.limit > 0 && run.count >= run.limit) {
                    finishLinkedRun(run);
                    renderInline(run.slot, "success", `前端生图完成，共 ${run.completed} 轮`, "结果显示在 Forge 图库");
                    break;
                }
                renderInline(run.slot, "success", "本轮前端生图已完成", "正在准备下一轮");
            }
        })().catch((error) => {
            if (String(error?.message || error) !== "已取消") {
                failLinkedRun(run, error);
            }
        }).finally(() => {
            run.generationLoopPromise = null;
            if (run.cancelled) finishLinkedRun(run);
        });
        return run.generationLoopPromise;
    }

    function installForgeInterruptHandlers() {
        for (const slot of ["txt2img", "img2img"]) {
            const interrupt = findButton(`${slot}_interrupt`);
            if (interrupt && interrupt.dataset.llmPromptStudioIntercepted !== "true") {
                interrupt.dataset.llmPromptStudioIntercepted = "true";
                interrupt.addEventListener("click", () => {
                    if (linkedRuns[slot]) stopLinkedRun(slot);
                });
            }
        }
    }

    async function runInlineDestination(config, run, original) {
        // The inline cache action only stores prompts.  Browser generation is
        // handled by startLinkedGenerationLoop, which clicks Forge's native
        // button and therefore receives the complete current txt2img state.
        if (config.destination !== "cache") throw new Error("未知的 Prompt 去向");
        if (config.source !== "llm") throw new Error("仅存缓存请使用 LLM 自动生成来源");
        const prompts = [];
        config.fixedPrompt = original;
        for (let index = 0; index < config.count; index += 1) {
            assertActive(run);
            renderInline(run.slot, "warning", run.unlimited ? "无限 · 正在准备下一条 Prompt" : `正在准备 Prompt ${index + 1} / ${config.count}`,
                `已完成 ${run.unlimited ? run.completed : prompts.length} 条`);
            const prompt = await getInlinePromptWithRetry(config, run);
            assertActive(run);
            prompts.push(prompt);
        }
        const message = `已完成 ${prompts.length} 条 Prompt，已保存到缓存；未启动生图`;
        renderInline(run.slot, "success", message);
        return message;
    }

    async function inlineOnce(config) {
        const slot = config.slot === "img2img" ? "img2img" : "txt2img";
        config = inlineConfig(slot, config);
        // Forge snapshots all current native controls; do not serialize a
        // partial settings list or create a Studio server queue job here.
        if (config.destination === "queue") return startInlineLoop(config);
        const run = beginInlineRun(slot, slot);
        if (!run) return "当前内嵌面板已有任务正在运行";
        try {
            const original = promptValue(slot);
            if (config.destination !== "prompt") {
                if (config.count !== 0) return await runInlineDestination(config, run, original);
                run.unlimited = true;
                run.completed = 0;
                while (true) {
                    assertActive(run);
                    await runInlineDestination({ ...config, count: 1 }, run, original);
                    assertActive(run);
                    run.completed += 1;
                }
            }
            const sourceName = inlineSourceName(config);
            renderInline(slot, "warning", `正在${config.source === "llm" ? "生成" : "读取"} ${sourceName} Prompt`, "");
            const prompt = await getInlinePromptWithRetry(config, run);
            assertActive(run);
            if (promptValue(slot) !== original) {
                throw new Error("正面 Prompt 已被修改，本次结果未写入，请重试");
            }
            const next = composeInlinePrompt(original, prompt, config);
            setValue(`${slot}_prompt`, next);
            const message = `${sourceName} Prompt 已写入正面提示词`;
            renderInline(slot, "success", message, "");
            return message;
        } catch (error) {
            const message = String(error?.message || error);
            renderInline(slot, message === "已取消" ? "warning" : "error", "内嵌 Prompt 操作已停止",
                run.unlimited ? `无限 · 已完成 ${run.completed} 条 · ${message}` : message);
            return message;
        } finally {
            finishInlineRun(run);
        }
    }

    function cancelInline(slot) {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        const run = inlineRuns[normalizedSlot];
        const linked = linkedRuns[normalizedSlot];
        if (!run && !linked) return "当前没有该面板的 Prompt 任务";
        if (run) {
            cancelInlineRequest(run);
            run.cancelled = true;
            run.abortController?.abort();
            if (run.target) {
                const interrupt = find(`${run.target}_interrupt`);
                if (interrupt && ownsActiveForgeTask(run)) interrupt.click();
            }
        }
        if (linked) {
            stopLinkedRun(normalizedSlot, true);
            return `前端连续生图已停止 · 已完成 ${linked.completed} 轮 · 已提交 ${linked.count} 轮`;
        }
        renderInline(normalizedSlot, "warning", "Prompt 任务已停止", run?.unlimited ? `无限 · 已完成 ${run.completed} 条` : "");
        return "Prompt 任务已停止";
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

    function randomInspirationRequest(previous = "") {
        const candidates = inspirationRequests.filter((item) => item !== previous);
        const pool = candidates.length ? candidates : inspirationRequests;
        const topic = pool[Math.floor(Math.random() * pool.length)];
        const nonce = Math.random().toString(36).slice(2, 8);
        return `请围绕${topic}创作一条完整、可直接生图的单图 Prompt；主动补足动作、表情、服装、道具、环境、构图、镜头、时间天气和光线；本轮变化标识 ${nonce}`;
    }

    async function generateBatch(config, parentRun = null) {
        if (state.active && state.active !== parentRun) return "已有队列任务正在运行";
            const parsed = parseRequests(config.request);
            if (!parsed.requests.length) {
                parsed.requests.push(randomInspirationRequest());
                parsed.generatedInspiration = true;
            }
        const run = parentRun || await beginRun("llm");
        if (!run) return "已有队列任务正在运行";
        run.phase = "llm";
        run.target = null;
        const batchId = createId("batch");
        state.lastBatchRowIds = [];
        try {
            for (const request of parsed.requests) {
                assertActive(run);
                const requestId = canonicalPrompt(request);
                let prompt = "";
                let accepted = false;
                for (let attempt = 0; attempt < 3; attempt += 1) {
                    const attemptRequest = parsed.generatedInspiration && attempt
                        ? randomInspirationRequest(request)
                        : request;
                    try {
                        prompt = await generateAutoLoopPrompt({ ...config, request: attemptRequest }, run);
                    } catch (error) {
                        assertActive(run);
                        if (!config.continuous || !isRejectedPrompt(error)) throw error;
                        render("warning", attempt < 2 ? "当前 Prompt 未通过校验，正在自动重试" : "当前请求已跳过，继续下一条",
                            String(error?.message || error));
                        await wait(promptRetryDelay(attempt));
                        continue;
                    }
                    assertActive(run);
                    accepted = true;
                    break;
                }
                if (!accepted) continue;
                state.requestIds.add(requestId);
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
                saveQueue();
                renderQueue();
            }
            const added = state.lastBatchRowIds.length;
            const persistence = state.persistent ? "" : "；当前队列未持久化 (not persistent)";
            const message = `Prompt 批量生成完成，新增 ${added} 条${persistence}`;
            render("success", message, "所有非空结果均已保留");
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
        const selectedIds = rowIds ? new Set(rowIds) : new Set(selectedRowIds());
        const pending = state.queue.filter((row) => row.status !== STATUS.completed && selectedIds.has(row.id));
        if (!pending.length) return "请先勾选待使用的 Prompt";
        const target = config?.target === "img2img" ? "img2img" : "txt2img";
        const mode = config.writeMode === "append" ? "append" : "replace";
        const targetInput = root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`);
        if (!targetInput) throw new Error(`未找到 ${target} Prompt 输入框`);
        const run = parentRun || await beginRun("forge", target);
        if (!run) return "已有队列任务正在运行";
        run.phase = "forge";
        run.target = target;
        const basePrompt = freezeBasePrompt(run, target, targetInput);
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
                let forgeError = null;
                for (let attempt = 1; attempt <= 3; attempt += 1) {
                    try { await runForgeGeneration(target, run, generate); forgeError = null; break; }
                    catch (error) {
                        forgeError = error;
                        const message = String(error?.message || error);
                        const blocked = isContentBlocked(message);
                        if (!blocked || attempt >= 3) break;
                        render("warning", `内容拦截，自动重试第 ${attempt + 1} 次`, message);
                        await new Promise((resolve) => setTimeout(resolve, 500));
                        assertActive(run);
                    }
                }
                if (forgeError) {
                    const message = String(forgeError?.message || forgeError);
                    const blocked = isContentBlocked(message);
                    if (blocked) {
                        row.status = STATUS.completed; row.selected = false; currentRow = null;
                        saveQueue(); renderQueue(); render("warning", "内容被拦截，已跳过并继续", message); continue;
                    }
                    throw forgeError;
                }
                assertActive(run);
                row.status = STATUS.completed;
                row.selected = false;
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
        if (state.active || state.dispatchBusy) return "已有队列任务正在运行";
        state.dispatchBusy = true;
        const run = await beginRun("llm");
        if (!run) { state.dispatchBusy = false; return "已有队列任务正在运行"; }
        const continuous = Boolean(config.continuous);
        const parsedCycles = Number(config.cycles);
        // Continuous mode accepts a finite 1-100 cycle count or an empty/zero value for
        // unattended generation until the user presses Stop.
        const cycleLimit = continuous
            ? (Number.isFinite(parsedCycles) && parsedCycles > 0 ? Math.min(100, Math.floor(parsedCycles)) : null)
            : 1;
        let completedCycles = 0;
        try {
            while (cycleLimit === null || completedCycles < cycleLimit) {
                assertActive(run);
                const generated = await generateBatch({
                    ...config,
                    allowRepeat: true,
                    allowDuplicateOutput: false,
                }, run);
                if (!String(generated).startsWith("Prompt 批量生成完成")) {
                    return generated;
                }
                const rowIds = state.lastBatchRowIds.slice();
                if (!rowIds.length) {
                    if (!continuous) return "本轮没有新增 Prompt";
                    render("warning", "本轮没有可用 Prompt，已跳过", "继续下一轮");
                    await wait(500);
                    assertActive(run);
                }
                if (rowIds.length && continuous && !config.promptOnly) {
                    const forgeResult = await runStored(
                        { target: config.target || "txt2img", writeMode: config.writeMode || "append" },
                        rowIds,
                        run,
                    );
                    if (!String(forgeResult).startsWith("队列生图完成")) return forgeResult;
                }
                completedCycles += 1;
                if (continuous) render("success", `持续 Prompt 生成已完成 ${completedCycles} 轮`, cycleLimit === null ? "点击停止结束" : `计划 ${cycleLimit} 轮`);
            }
            return continuous ? `持续 Prompt 生成完成，共 ${completedCycles} 轮` : `Prompt 生成完成，共 ${completedCycles} 轮`;
        } catch (error) {
            const message = String(error?.message || error);
            render(message === "已取消" ? "warning" : "error", "持续 Prompt 生成已停止", message);
            return message;
        } finally {
            finishRun(run);
            state.dispatchBusy = false;
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

    function navigateStudioTab(tabId) {
        const scope = root();
        const host = scope.querySelector(`#${tabId}`) || scope.querySelector(`#tab_${tabId}`);
        const panelId = host?.id || `tab_${tabId}`;
        const button = scope.querySelector(`[role="tab"][aria-controls="${panelId}"]`)
            || scope.querySelector(`[role="tab"][aria-controls="${tabId}"]`)
            || scope.querySelector(`#${tabId}_button`)
            || scope.querySelector(`#tab_${tabId}-button`);
        if (button) {
            button.click();
            return true;
        }
        return false;
    }

    function findButtonByText(text) {
        const wanted = String(text || "").trim();
        if (!wanted) return null;
        return Array.from(root().querySelectorAll("button")).find((button) => button.textContent.trim() === wanted) || null;
    }

    function openAccordionByLabel(label) {
        const wanted = String(label || "").trim();
        const summary = Array.from(root().querySelectorAll("details > summary")).find((node) => node.textContent.includes(wanted));
        if (!summary) return false;
        const details = summary.closest("details");
        if (details && !details.open) summary.click();
        return true;
    }

    function focusHandoff() {
        navigateStudioTab("llm_prompt_studio_library_tab");
        window.setTimeout(() => {
            openAccordionByLabel("Ranbooru 实时交接箱");
            findButtonByText("刷新交接箱")?.click();
            find("llm_prompt_studio_handoff_table")?.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 0);
        return true;
    }

    function serverQueueRowsHtml(rows) {
        return rows.length ? rows.map((row) => {
            const images = (Array.isArray(row.images) ? row.images : []).map((value) => {
                const raw = String(value || "");
                const source = raw.startsWith("data:image/") ? raw : /^[A-Za-z0-9+/=\s]+$/.test(raw) ? `data:image/png;base64,${raw}` : "";
                return source ? `<img class="lps-queue-image" src="${escapeHtml(source)}" alt="生成结果" loading="lazy">` : "";
            }).join("");
            return `<div class="lps-server-queue-row"><span>${escapeHtml(row.position)}</span><b>${escapeHtml(row.status)}</b><code>${escapeHtml(row.prompt || row.request || "")}</code><small>${escapeHtml(row.error || "")}</small><div class="lps-queue-images">${images}</div></div>`;
        }).join("") : '<div class="lps-auto-loop-empty">暂无服务端队列记录。</div>';
    }

    function renderServerQueue(snapshot) {
        const statusHost = find("llm_prompt_studio_server_queue_status");
        const logHost = find("llm_prompt_studio_server_queue_log");
        if (statusHost) statusHost.innerHTML = escapeHtml(snapshot?.status || "服务端队列状态未知");
        if (logHost) logHost.innerHTML = serverQueueRowsHtml(Array.isArray(snapshot?.jobs) ? snapshot.jobs : []);
    }

    async function watchServerQueue(batchId) {
        const id = String(batchId || "").trim();
        if (!id) return "尚未提交服务端任务";
        // These watchers share one Studio log; replacing the view does not cancel queued jobs.
        for (const prior of serverQueueWatchers.values()) prior.cancelled = true;
        serverQueueWatchers.clear();
        const watcher = { cancelled: false };
        serverQueueWatchers.set(id, watcher);
        while (!watcher.cancelled) {
            try {
                const response = await studioFetch(`/llm-prompt-studio/v1/queue/${encodeURIComponent(id)}`, { cache: "no-store" });
                if (!response.ok) throw new Error(`服务端队列 HTTP ${response.status}`);
                const snapshot = await response.json();
                if (watcher.cancelled) break;
                renderServerQueue(snapshot);
                const counts = snapshot.counts || {};
                const total = Number(snapshot.jobs?.length || 0);
                const finished = Number(counts.completed || 0) + Number(counts.error || 0) + Number(counts.cancelled || 0);
                if (total && finished >= total) break;
            } catch (error) {
                if (watcher.cancelled) break;
                const statusHost = find("llm_prompt_studio_server_queue_status");
                if (statusHost) statusHost.innerHTML = escapeHtml(`服务端队列轮询失败：${error?.message || error}`);
            }
            await wait(1000);
        }
        if (serverQueueWatchers.get(id) === watcher) serverQueueWatchers.delete(id);
        return String(find("llm_prompt_studio_server_queue_status")?.textContent || "服务端队列已结束");
    }

    function cancel() {
        const run = state.active;
        if (!run) return "当前没有运行中的任务";
        run.cancelled = true;
        run.abortController?.abort();
        if (run.phase === "forge" && run.target) {
            const interrupt = find(`${run.target}_interrupt`);
            if (interrupt && ownsActiveForgeTask(run)) interrupt.click();
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
    installForgeInterruptHandlers();
    if (typeof onAfterUiUpdate === "function") onAfterUiUpdate(installForgeInterruptHandlers);
    loadQueue();
    window.setTimeout(renderQueue, 1000);
    window.llmPromptStudioAutoLoop = {
        generateBatch,
        generateAndRun,
        runStored,
        inlineOnce,
        cancelInline,
        watchServerQueue,
        clearQueue,
        selectAllRows,
        clearSelectedRows,
        writeSelectedToPositive,
        startInlineLoop,
        navigate: navigateStudioTab,
        focusHandoff,
        cancel,
        start: generateBatch,
        readTxt2imgSettings,
    };
})();
