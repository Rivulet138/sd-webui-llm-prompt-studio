(function () {
    "use strict";

    const QUEUE_KEY = "llm_prompt_studio_auto_loop_queue_v1";
    const MAX_LOG_ROWS = 100;
    const STATUS = Object.freeze({ pending: "pending", running: "running", completed: "completed" });
    const FAILURE_PATTERN = /fail|error|timeout|refused|interrupted|失败|错误|超时|拒绝|中断/i;
    const SUCCESS_PATTERN = /completed|finished|done|success|生成完成|已完成/i;
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

    async function waitForUiSignal(ms, elements) {
        if (typeof window.MutationObserver !== "function") {
            await wait(ms);
            return;
        }
        let wake;
        const mutation = new Promise((resolve) => { wake = resolve; });
        const observer = new window.MutationObserver(wake);
        for (const element of elements.filter(Boolean)) {
            observer.observe(element, {
                attributes: true,
                childList: true,
                characterData: true,
                subtree: true,
            });
        }
        try {
            await Promise.race([wait(ms), mutation]);
        } finally {
            observer.disconnect();
        }
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
        if (inlineRuns[slot]) return null;
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
        const key = canonicalPrompt(value);
        if (state.queue.some((row) => canonicalPrompt(row.prompt) === key && row.status !== STATUS.completed)) return false;
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

    async function waitForForgeGeneration(tab, run, beforeStatus, taskId = "", timeoutMs = 1800000) {
        const statusHost = find(`${tab}_status`);
        const interrupt = find(`${tab}_interrupt`);
        const interrupting = find(`${tab}_interrupting`);
        const generate = findButton(`${tab}_generate`);
        let sawBusy = Boolean(taskId);
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
            const statusCompleted = currentStatus !== beforeStatus && SUCCESS_PATTERN.test(currentStatus);
            if (taskReplaced) {
                throw new Error(`${tab} generation task ownership changed; prompt returned to pending`);
            }
            if (!sawBusy && !taskId && !statusCompleted && launchBudget.expired()) {
                throw new Error(`${tab} Forge 未启动生图任务，请检查 Forge 队列或页面状态`);
            }
            if (!busy && (sawBusy || statusCompleted)) {
                if (FAILURE_PATTERN.test(currentStatus)) throw new Error(currentStatus);
                return;
            }
            if (budget.expired()) break;
            await waitForUiSignal(250, [statusHost, interrupt, interrupting, generate]);
        }
        throw new Error(`${tab} generation timeout; prompt returned to pending`);
    }

    function ensureForgeIdle(target) {
        if (isForgeBusy(target)) throw new Error(`${target} 当前已有生图任务`);
    }

    function inlineId(slot, suffix) {
        return `llm_prompt_studio_${slot}_inline_${suffix}`;
    }

    function promptValue(slot) {
        return String(input(`${slot}_prompt`)?.value || "").trim();
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
        const base = String(basePrompt || "").trim();
        const generatedText = String(generated || "").trim();
        if (mode === "replace") return generatedText;
        const delta = removePromptOverlap(base, generated);
        if (!delta) return base;
        const join = (left, right) => [String(left || "").trim().replace(/[\s,]+$/, ""), String(right || "").trim().replace(/^[\s,]+/, "")].filter(Boolean).join(", ");
        if (mode === "append_start") return join(delta, base);
        if (mode === "marker") {
            const token = String(marker || "{{LLM}}").trim() || "{{LLM}}";
            if (base.includes(token)) return base.replace(token, delta);
            return join(base, delta);
        }
        return join(base, delta);
    }

    async function readInlineCache(slot, run) {
        assertActive(run);
        if (typeof window.fetch !== "function") throw new Error("浏览器不支持 Fetch，无法读取 Prompt 缓存");
        if (run.abortController) throw new Error("LLM Studio 当前已有生成任务");
        const controller = typeof window.AbortController === "function" ? new window.AbortController() : null;
        run.abortController = controller;
        try {
            const response = await studioFetch("/llm-prompt-studio/v1/cache?limit=1000", {
                cache: "no-store",
                ...(controller ? { signal: controller.signal } : {}),
            });
            let data = null;
            try { data = await response.json(); } catch { data = null; }
            if (!response.ok) throw new Error(String(data?.detail || `读取缓存失败：HTTP ${response.status}`));
            const records = Array.isArray(data?.records)
                ? data.records.filter((record) => String(record?.prompt || "").trim())
                : [];
            if (!records.length) throw new Error("缓存为空，请先生成或导入 Prompt");
            const position = inlineCacheCursors[slot] % records.length;
            inlineCacheCursors[slot] = position + 1;
            const prompt = String(records[position].prompt || "").trim();
            assertActive(run);
            if (!prompt) throw new Error("缓存记录没有可用 Prompt");
            return prompt;
        } catch (error) {
            if (controller?.signal.aborted || run.cancelled) throw new Error("已取消");
            throw error;
        } finally {
            if (run.abortController === controller) run.abortController = null;
        }
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
        const data = await requestPromptApi(run, "inline-generate", {
            slot,
            request: String(config.request || ""),
            source_tags: promptValue(slot),
            variation: String(config.variation || ""),
            preset: normalizeChoiceValue(config.preset || componentValue(inlineId(slot, "preset"), "Danbooru Tags")),
            base_model: normalizeChoiceValue(config.baseModel || componentValue(inlineId(slot, "base_model"), "Auto / checkpoint default")),
            safety: normalizeChoiceValue(config.safety || componentValue(inlineId(slot, "safety"), "SFW")),
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
        let taskId = "";
        try {
            generate.click();
            taskId = currentForgeTaskId(tab);
        } finally {
            if (typeof afterClick === "function") afterClick();
        }
        run.forgeStarted = true;
        run.forgeTaskId = taskId;
        try {
            await waitForForgeGeneration(tab, run, beforeStatus, taskId);
        } finally {
            run.forgeStarted = false;
            run.forgeTaskId = "";
        }
    }

    async function getInlinePrompt(config, run) {
        return config.source === "cache"
            ? readInlineCache(config.slot, run)
            : generateInlinePrompt(config, run);
    }

    async function getInlinePromptWithRetry(config, run) {
        for (let attempt = 0; attempt < 2; attempt += 1) {
            try {
                return await getInlinePrompt(config, run);
            } catch (error) {
                if (!/assistant text|finish_reason\s*[=:]\s*length/i.test(String(error?.message || error)) || attempt === 1) throw error;
                assertActive(run);
                renderInline(config.slot, "warning", "Prompt 响应为空，自动重试", "不会结束当前连续流程");
                await wait(500);
            }
        }
        throw new Error("Prompt 生成重试失败");
    }

    const infiniteModes = { txt2img: null, img2img: null };
    const generateClickBypass = { txt2img: false, img2img: false };

    function inlineConfig(slot, overrides = {}) {
        return {
            slot,
            enabled: overrides.enabled !== false,
            writeMode: normalizeChoiceValue(overrides.writeMode, "append_end"),
            marker: String(overrides.marker || "{{LLM}}"),
            request: String(overrides.request || ""),
            variation: String(overrides.variation || ""),
            source: normalizeChoiceValue(overrides.source, "llm"),
            preset: normalizeChoiceValue(overrides.preset || componentValue(inlineId(slot, "preset"), "Danbooru Tags")),
            baseModel: normalizeChoiceValue(overrides.baseModel || componentValue(inlineId(slot, "base_model"), "Auto / checkpoint default")),
            safety: normalizeChoiceValue(overrides.safety || componentValue(inlineId(slot, "safety"), "SFW")),
        };
    }

    function finishLinkedRun(run) {
        if (linkedRuns[run.slot] === run) linkedRuns[run.slot] = null;
    }

    async function prepareLinkedPrompt(run) {
        assertActive(run);
        renderInline(run.slot, "warning", `正在准备第 ${run.count + 1} 条 LLM Prompt`, "Forge 会等待本轮 Prompt 准备完成");
        for (let attempt = 0; attempt < 3; attempt += 1) {
            const current = promptValue(run.slot);
            if (current !== run.lastWrittenPrompt) run.basePrompt = current;
            const generated = await getInlinePromptWithRetry(run.config, run);
            assertActive(run);
            if (promptValue(run.slot) !== current) {
                run.preparedPrompt = "";
                run.preparedSource = "";
                renderInline(run.slot, "warning", "正面 Prompt 已变化，正在重新准备", "旧请求结果已丢弃");
                continue;
            }
            const next = composePrompt(run.basePrompt, generated, run.config.writeMode, run.config.marker);
            const duplicate = !next
                || canonicalPrompt(next) === canonicalPrompt(current)
                || canonicalPrompt(next) === canonicalPrompt(run.lastUsedPrompt);
            if (!duplicate) {
                run.preparedPrompt = next;
                run.preparedSource = current;
                run.lastWrittenPrompt = current;
                renderInline(run.slot, "success", `第 ${run.count + 1} 条 LLM Prompt 已就绪`, "点击生成或继续 Forge 无限生成；正面 Prompt 框保持不变");
                return next;
            }
            if (attempt < 2) {
                renderInline(run.slot, "warning", "检测到重复 Prompt，正在重新生成", `第 ${attempt + 2}/3 次尝试`);
            }
        }
        throw new Error("LLM 连续返回重复内容，本轮未提交给 Forge");
    }

    function startLinkedRun(config) {
        const slot = config.slot === "img2img" ? "img2img" : "txt2img";
        if (linkedRuns[slot]) {
            linkedRuns[slot].config = inlineConfig(slot, config);
            return linkedRuns[slot];
        }
        const run = {
            scope: "linked", slot, target: slot, cancelled: false, count: 0,
            config: inlineConfig(slot, config), basePrompt: promptValue(slot),
            lastWrittenPrompt: promptValue(slot), lastUsedPrompt: "", preparedPrompt: "", preparedSource: "",
            nextPromise: null, abortController: null, requestId: "", generationLoopPromise: null,
        };
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

    function stopLinkedRun(slot) {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        const run = linkedRuns[normalizedSlot];
        if (!run) return;
        cancelInlineRequest(run);
        run.cancelled = true;
        run.abortController?.abort();
        finishLinkedRun(run);
        renderInline(normalizedSlot, "warning", "LLM 无限生成已停止", "Forge 当前图片会完成，之后不会再由 LLM 更新 Prompt");
    }

    function failLinkedRun(slot, error) {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        renderInline(normalizedSlot, "error", "LLM 无限生成已暂停", String(error?.message || error));
    }

    function setInfiniteMode(config) {
        const slot = config.slot === "img2img" ? "img2img" : "txt2img";
        if (!config.enabled) {
            infiniteModes[slot] = null;
            stopLinkedRun(slot);
            return "已关闭 LLM 无限生成";
        }
        const nextConfig = inlineConfig(slot, config);
        const previousConfig = infiniteModes[slot];
        const configChanged = previousConfig && ["writeMode", "marker", "request", "variation", "source", "preset", "baseModel", "safety"]
            .some((key) => previousConfig[key] !== nextConfig[key]);
        if (configChanged) stopLinkedRun(slot);
        infiniteModes[slot] = nextConfig;
        startLinkedRun(infiniteModes[slot]);
        // Do not start an LLM request from the checkbox change handler.  The
        // request can take several seconds (or hit the provider timeout), and
        // Gradio may keep the originating change event pending while that
        // request is in flight.  The Forge generate interceptor prepares the
        // prompt immediately before the actual generation instead.
        renderInline(slot, "success", "LLM 无限生成已启用", "点击 Forge 生成后再准备本轮 Prompt");
        return "LLM 无限生成已启用，点击 Forge 生成后准备 Prompt";
    }

    function scheduleNextLinkedPrompt(run) {
        window.setTimeout(() => {
            if (!infiniteModes[run.slot]?.enabled || linkedRuns[run.slot] !== run) return;
            ensureLinkedPrompt(run).catch((error) => failLinkedRun(run.slot, error));
        }, 0);
    }

    function consumeLinkedPrompt(tab) {
        const slot = tab === "img2img" ? "img2img" : "txt2img";
        const run = linkedRuns[slot];
        if (!infiniteModes[slot]?.enabled || !run?.preparedPrompt) return null;
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
        renderInline(slot, "success", `第 ${run.count} 条 LLM Prompt 已提交给 Forge`, "正面 Prompt 框未修改；正在准备下一条");
        return prompt;
    }

    async function submitLinkedForgeGeneration(run) {
        const slot = run.slot;
        const generate = findButton(`${slot}_generate`);
        if (!generate) throw new Error(`未找到 ${slot} 生成按钮`);
        const original = promptValue(slot);
        await ensureLinkedPrompt(run);
        assertActive(run);
        const override = consumeLinkedPrompt(slot);
        if (!override) throw new Error("准备好的 LLM Prompt 已过期，请重新生成");
        let restored = false;
        const restore = () => {
            if (restored) return;
            restored = true;
            setValue(`${slot}_prompt`, original, { emitChange: false });
            scheduleNextLinkedPrompt(run);
        };
        setValue(`${slot}_prompt`, override, { emitChange: false });
        generateClickBypass[slot] = true;
        try {
            await runForgeGeneration(slot, run, generate, restore);
        } finally {
            generateClickBypass[slot] = false;
            restore();
        }
    }

    function startLinkedGenerationLoop(run) {
        if (run.generationLoopPromise) return run.generationLoopPromise;
        run.generationLoopPromise = (async () => {
            while (infiniteModes[run.slot]?.enabled && linkedRuns[run.slot] === run) {
                assertActive(run);
                await submitLinkedForgeGeneration(run);
            }
        })().catch((error) => {
            if (String(error?.message || error) !== "已取消") failLinkedRun(run.slot, error);
        }).finally(() => {
            run.generationLoopPromise = null;
        });
        return run.generationLoopPromise;
    }

    function interceptForgeGenerate(event, slot) {
        if (generateClickBypass[slot]) {
            generateClickBypass[slot] = false;
            return;
        }
        const config = infiniteModes[slot];
        if (!config?.enabled) return;
        const run = startLinkedRun(config);
        event.preventDefault();
        event.stopImmediatePropagation();
        if (run.generationLoopPromise) return;
        renderInline(slot, "warning", "正在等待 LLM Prompt", "准备完成后会自动开始连续生成");
        startLinkedGenerationLoop(run);
    }

    function installForgeGenerateInterceptors() {
        for (const slot of ["txt2img", "img2img"]) {
            const generate = findButton(`${slot}_generate`);
            if (generate && generate.dataset.llmPromptStudioIntercepted !== "true") {
                generate.dataset.llmPromptStudioIntercepted = "true";
                generate.addEventListener("click", (event) => interceptForgeGenerate(event, slot), true);
            }
            const prompt = input(`${slot}_prompt`);
            if (prompt && prompt.dataset.llmPromptStudioIntercepted !== "true") {
                prompt.dataset.llmPromptStudioIntercepted = "true";
                prompt.addEventListener("keydown", (event) => {
                    if (event.key !== "Enter" || (!event.ctrlKey && !event.metaKey) || event.altKey) return;
                    if (!infiniteModes[slot]?.enabled) return;
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    generate.click();
                }, true);
            }
            const interrupt = findButton(`${slot}_interrupt`);
            if (interrupt && interrupt.dataset.llmPromptStudioIntercepted !== "true") {
                interrupt.dataset.llmPromptStudioIntercepted = "true";
                interrupt.addEventListener("click", () => {
                    if (!infiniteModes[slot]?.enabled) return;
                    infiniteModes[slot] = null;
                    stopLinkedRun(slot);
                    const checkbox = input(inlineId(slot, "infinite"));
                    if (checkbox && checkbox.checked) {
                        checkbox.checked = false;
                        checkbox.dispatchEvent(new Event("input", { bubbles: true }));
                        checkbox.dispatchEvent(new Event("change", { bubbles: true }));
                    }
                });
            }
        }
    }

    async function inlineOnce(config) {
        const slot = config.slot === "img2img" ? "img2img" : "txt2img";
        const target = slot;
        const run = beginInlineRun(slot, target);
        if (!run) return "当前内嵌面板已有任务正在运行";
        try {
            renderInline(slot, "warning", config.source === "cache" ? "正在读取缓存 Prompt" : "正在生成 Prompt", "当前请求处理中");
            const prompt = await getInlinePromptWithRetry(config, run);
            assertActive(run);
            if (!enqueuePrompt(prompt, config.request)) {
                throw new Error("LLM 返回了队列中已有的重复 Prompt，未加入队列");
            }
            const message = config.source === "cache" ? "已取缓存 Prompt 并加入队列" : "已生成 Prompt 并加入队列";
            renderInline(slot, "success", message, "请在队列中勾选后写入固定正面 Prompt");
            return message;
        } catch (error) {
            const message = String(error?.message || error);
            renderInline(slot, message === "已取消" ? "warning" : "error", "内嵌 Prompt 操作已停止", message);
            return message;
        } finally {
            finishInlineRun(run);
        }
    }

    function cancelInline(slot) {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        const run = inlineRuns[normalizedSlot];
        const linked = linkedRuns[normalizedSlot];
        if (!run && !linked) return "当前没有该面板的 LLM 任务";
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
            cancelInlineRequest(linked);
            linked.cancelled = true;
            linked.abortController?.abort();
            finishLinkedRun(linked);
        }
        renderInline(normalizedSlot, "warning", "已停止当前 LLM 请求", "无限模式仍保持勾选；下次 Forge 生成时会重新准备");
        return "已停止当前 LLM 请求";
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
                let prompt = "";
                let promptKey = "";
                let accepted = false;
                for (let attempt = 0; attempt < 3; attempt += 1) {
                    const attemptRequest = parsed.generatedInspiration && attempt
                        ? randomInspirationRequest(request)
                        : request;
                    prompt = await generateAutoLoopPrompt({ ...config, request: attemptRequest }, run);
                    assertActive(run);
                    promptKey = canonicalPrompt(prompt);
                    if (allowDuplicateOutput || !queuedPrompts.has(promptKey)) {
                        accepted = true;
                        break;
                    }
                    duplicateOutputCount += 1;
                    render("warning", "检测到重复 Prompt，正在重新抽样", "当前连续任务会继续运行");
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
        const selectedIds = rowIds ? new Set(rowIds) : new Set(selectedRowIds());
        const pending = state.queue.filter((row) => row.status !== STATUS.completed && selectedIds.has(row.id));
        if (!pending.length) return "请先勾选待使用的 Prompt";
        const target = "txt2img";
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
                await runForgeGeneration(target, run, generate);
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
        // Zero used to mean an unbounded loop, which made accidental clicks grow the queue rapidly.
        // Require an explicit bounded value in the browser; the Forge infinite mode remains separate.
        const cycleLimit = continuous
            ? (Number.isFinite(parsedCycles) ? Math.min(100, Math.max(1, Math.floor(parsedCycles))) : 1)
            : 1;
        let completedCycles = 0;
        try {
            while (completedCycles < cycleLimit) {
                assertActive(run);
                const generated = await generateBatch({
                    ...config,
                    allowRepeat: true,
                    allowDuplicateOutput: false,
                }, run);
                if (!String(generated).startsWith("Prompt 批量生成完成")) return generated;
                const rowIds = state.lastBatchRowIds.slice();
                if (!rowIds.length) return "本轮没有新增 Prompt";
                if (continuous && !config.promptOnly) {
                    const forgeResult = await runStored(
                        { target: config.target || "txt2img", writeMode: config.writeMode || "append" },
                        rowIds,
                        run,
                    );
                    if (!String(forgeResult).startsWith("队列生图完成")) return forgeResult;
                }
                completedCycles += 1;
                if (continuous) render("success", `持续 Prompt 生成已完成 ${completedCycles} 轮`, `计划 ${cycleLimit} 轮`);
            }
            return continuous ? `持续 Prompt 生成完成，共 ${completedCycles} 轮` : `Prompt 生成完成，共 ${completedCycles} 轮`;
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
            openAccordionByLabel("Ranbooru 缓存联动");
            openAccordionByLabel("Ranbooru 实时交接箱");
            findButtonByText("刷新交接箱")?.click();
            find("llm_prompt_studio_handoff_table")?.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 0);
        return true;
    }

    function syncFooterNavigation() {
        const footer = root().querySelector("#llm_prompt_studio_footer_nav");
        if (!footer) return;
        const tabs = root().querySelectorAll('#llm_prompt_studio_main_tabs [role="tab"]');
        footer.querySelectorAll("[data-lps-tab]").forEach((link) => {
            const target = String(link.dataset.lpsTab || "");
            const panel = root().querySelector(`#${target}`) || root().querySelector(`#tab_${target}`);
            const panelId = panel?.id || `tab_${target}`;
            const active = Array.from(tabs).some((tab) => tab.getAttribute("aria-selected") === "true" && tab.getAttribute("aria-controls") === panelId);
            link.toggleAttribute("aria-current", active);
            if (active) link.classList.add("is-active");
            else link.classList.remove("is-active");
        });
    }

    if (typeof MutationObserver === "function") {
        const observer = new MutationObserver(syncFooterNavigation);
        const observeTabs = () => {
            const tabs = root().querySelector("#llm_prompt_studio_main_tabs");
            if (tabs) observer.observe(tabs, { subtree: true, attributes: true, attributeFilter: ["aria-selected", "class"] });
            syncFooterNavigation();
        };
        if (typeof onAfterUiUpdate === "function") onAfterUiUpdate(observeTabs);
        else window.setTimeout(observeTabs, 500);
    }

    function renderServerQueue(snapshot) {
        const statusHost = find("llm_prompt_studio_server_queue_status");
        const logHost = find("llm_prompt_studio_server_queue_log");
        if (statusHost) statusHost.innerHTML = escapeHtml(snapshot?.status || "服务端队列状态未知");
        if (!logHost) return;
        const rows = Array.isArray(snapshot?.jobs) ? snapshot.jobs : [];
        logHost.innerHTML = rows.length
            ? rows.map((row) => `<div class="lps-server-queue-row"><span>${escapeHtml(row.position)}</span><b>${escapeHtml(row.status)}</b><code>${escapeHtml(row.prompt || row.request || "")}</code><small>${escapeHtml(row.error || "")}</small></div>`).join("")
            : '<div class="lps-auto-loop-empty">暂无服务端队列记录。</div>';
    }

    async function watchServerQueue(batchId) {
        const id = String(batchId || "").trim();
        if (!id) return "尚未提交服务端任务";
        const prior = serverQueueWatchers.get(id);
        if (prior) prior.cancelled = true;
        const watcher = { cancelled: false };
        serverQueueWatchers.set(id, watcher);
        while (!watcher.cancelled) {
            try {
                const response = await studioFetch(`/llm-prompt-studio/v1/queue/${encodeURIComponent(id)}`, { cache: "no-store" });
                if (!response.ok) throw new Error(`服务端队列 HTTP ${response.status}`);
                const snapshot = await response.json();
                renderServerQueue(snapshot);
                const counts = snapshot.counts || {};
                const total = Number(snapshot.jobs?.length || 0);
                const finished = Number(counts.completed || 0) + Number(counts.error || 0) + Number(counts.cancelled || 0);
                if (total && finished >= total) break;
            } catch (error) {
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
    installForgeGenerateInterceptors();
    if (typeof onAfterUiUpdate === "function") onAfterUiUpdate(installForgeGenerateInterceptors);
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
        setInfiniteMode,
        navigate: navigateStudioTab,
        focusHandoff,
        cancel,
        start: generateBatch,
    };
})();
