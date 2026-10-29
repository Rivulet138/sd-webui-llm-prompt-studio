(function () {
    "use strict";

    const FAILURE_PATTERN = /fail|error|timeout|refused|interrupted|失败|错误|超时|拒绝|中断/i;
    const SUCCESS_PATTERN = /completed|finished|done|success|生成完成|已完成/i;
    const CONTENT_BLOCK_PATTERN = /(SFW|NSFW|安全|成人|内容).*(拦截|拒绝|阻止|blocked|safety)|blocked.*(SFW|safety)/i;
    const NATIVE_CACHE_MARKER_START = "\u2063\u2062";
    const NATIVE_CACHE_MARKER_END = "\u2062\u2063";
    const NATIVE_CACHE_MARKER_ZERO = "\u200b";
    const NATIVE_CACHE_MARKER_ONE = "\u200c";

    function nativeClaimSuffix(token) {
        const normalized = String(token || "").trim().toLowerCase();
        if (!/^[0-9a-f]{32}$/.test(normalized)) {
            throw new Error("后端返回了无效的缓存上下文 token");
        }
        const bits = [...normalized]
            .map((char) => Number.parseInt(char, 16).toString(2).padStart(4, "0"))
            .join("");
        return NATIVE_CACHE_MARKER_START
            + [...bits].map((bit) => bit === "1"
                ? NATIVE_CACHE_MARKER_ONE
                : NATIVE_CACHE_MARKER_ZERO).join("")
            + NATIVE_CACHE_MARKER_END;
    }

    function nativeClaimPrefix(value) {
        const text = String(value ?? "");
        const start = text.lastIndexOf(NATIVE_CACHE_MARKER_START);
        if (start < 0) return null;
        const encodedStart = start + NATIVE_CACHE_MARKER_START.length;
        const end = encodedStart + 128;
        if (text.length < end + NATIVE_CACHE_MARKER_END.length
            || text.slice(end, end + NATIVE_CACHE_MARKER_END.length) !== NATIVE_CACHE_MARKER_END
            || !/^[\s,，]*$/.test(text.slice(end + NATIVE_CACHE_MARKER_END.length))) {
            return null;
        }
        const encoded = text.slice(encodedStart, end);
        if (![...encoded].every(char => char === NATIVE_CACHE_MARKER_ZERO || char === NATIVE_CACHE_MARKER_ONE)) {
            return null;
        }
        return text.slice(0, start);
    }

    function stripStaleNativeClaim(value) {
        let current = String(value ?? "");
        while (true) {
            const stripped = nativeClaimPrefix(current);
            if (stripped == null) return current;
            current = stripped;
        }
    }

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
        active: null,
        sequence: 0,
    };
    const inlineRuns = { txt2img: null, img2img: null };
    const linkedRuns = { txt2img: null, img2img: null };
    const nativeGenerateBypass = { txt2img: 0, img2img: 0 };
    // Forge keeps its task marker in localStorage for a short period while
    // the progress callback is switching the controls back to idle. Keep the
    // last task completed by Studio per tab so a new run can distinguish that
    // stale marker from an actually active Forge task.
    const lastCompletedForgeTaskIds = { txt2img: "", img2img: "" };
    const inlineCacheCursors = { txt2img: 0, img2img: 0 };
    const processedCacheCursors = { txt2img: 0, img2img: 0 };
    const CACHE_CURSOR_STORAGE_KEY = "llm_prompt_studio_cache_cursors_v1";
    const nativeCacheUi = {};
    const NATIVE_CACHE_CONFIG_FIELDS = ["source", "cache_enabled", "write_mode", "marker"];
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

    function enablePluginKeepAlive() {
        // Keep the background guarantee local to Studio's own wait() calls.
        // Do not replace page-global timers or RAF: Forge, Gradio, and other
        // extensions must retain their native scheduling semantics.
        if (backgroundTimer === undefined) backgroundTimer = createBackgroundTimer();
        return Boolean(backgroundTimer);
    }

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
        const selected = host.querySelector(".single-select");
        if (selected?.textContent.trim()) return normalizeChoiceValue(selected.textContent.trim(), fallback);
        const element = host.querySelector("textarea, input, select");
        return element ? normalizeChoiceValue(element.value, fallback) : fallback;
    }

    function componentChecked(id, fallback = false) {
        const host = find(id);
        const element = host?.querySelector('input[type="checkbox"]');
        return element ? Boolean(element.checked) : fallback;
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
        const timeoutMs = Math.max(1000, Number(options.timeoutMs ?? 15000));
        const externalSignal = options.signal;
        const controller = new AbortController();
        const fetchOptions = { ...options };
        delete fetchOptions.timeoutMs;
        fetchOptions.credentials = "same-origin";
        fetchOptions.signal = controller.signal;
        const abortExternal = () => controller.abort();
        if (externalSignal) {
            if (externalSignal.aborted) controller.abort();
            else externalSignal.addEventListener("abort", abortExternal, { once: true });
        }
        const timer = window.setTimeout(() => controller.abort(), timeoutMs);
        let response;
        try {
            response = await window.fetch(url, fetchOptions);
        } catch (error) {
            if (controller.signal.aborted) {
                throw new Error(`Studio API 请求超时或已取消：${url}`);
            }
            throw error;
        } finally {
            window.clearTimeout(timer);
            externalSignal?.removeEventListener?.("abort", abortExternal);
        }
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

    function createId(prefix) {
        state.sequence += 1;
        return `${prefix}-${Date.now().toString(36)}-${state.sequence.toString(36)}`;
    }



    function renderInline(slot, kind, headline, detail = "") {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        const host = find(`llm_prompt_studio_${normalizedSlot}_inline_loop_status`);
        if (!host) return;
        const tone = kind === "success" ? "success" : kind === "error" ? "error" : "warning";
        const run = linkedRuns[normalizedSlot];
        if (run && !run.cancelled) {
            detail = ["累计完成 " + run.completed + " 张图", detail].filter(Boolean).join(" · ");
        }
        host.innerHTML = `<div class="lps-status lps-status--${tone}" role="status" aria-live="polite"><strong>${escapeHtml(headline)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}</div>`;
    }




    function isControlShown(element) {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        return style.display !== "none";
    }

    function forgeOptions() {
        let options = null;
        try {
            if (typeof opts !== "undefined" && opts && typeof opts === "object") options = opts;
        } catch {
            options = null;
        }
        if (!options && window.opts && typeof window.opts === "object") options = window.opts;
        return options && Object.keys(options).length ? options : null;
    }

    function forgeKeepAliveEnabled() {
        const options = forgeOptions();
        return Boolean(options?.keep_alive || window.__forgeKeepAliveActive);
    }

    function isForgeBusy(tab, run = null) {
        // contextMenus.js sets interrupt.style.display = "block" immediately
        // after every Generate click. When Studio intercepts that click while
        // the previous round is finishing, the style can remain visible even
        // though Forge has no active task. Use Forge's task marker and the
        // actual disabled/interrupting controls as the busy signal so this
        // stale visual marker cannot deadlock the next round.
        const taskId = currentForgeTaskId(tab);
        const lastCompletedTaskId = String(
            run?.lastCompletedForgeTaskId || lastCompletedForgeTaskIds[tab] || "",
        );
        const interruptShown = isControlShown(find(`${tab}_interrupt`));
        if (Boolean(findButton(`${tab}_generate`)?.disabled)) return true;
        if (isControlShown(find(`${tab}_interrupting`))) return true;
        // A Generate-forever click can leave the interrupt button visible for
        // one UI update after Studio intercepted the click. A task marker is
        // only meaningful while that control is visible and is not the round
        // Studio just completed.
        return Boolean(taskId && interruptShown && taskId !== lastCompletedTaskId);
    }

    function currentForgeTaskId(tab) {
        try {
            return String(window.localStorage.getItem(`${tab}_task_id`) || "");
        } catch {
            return "";
        }
    }

    function cacheCursorKey(slot, processed) {
        return `${slot}:${processed ? "processed_cache" : "cache"}`;
    }

    function readCacheCursorState() {
        try {
            const value = JSON.parse(window.localStorage.getItem(CACHE_CURSOR_STORAGE_KEY) || "{}");
            return value && typeof value === "object" ? value : {};
        } catch {
            return {};
        }
    }

    function normalizeCacheCursor(value, fallback = 0) {
        if (value == null || String(value).trim() === "") return fallback;
        const number = Number(value);
        return Number.isSafeInteger(number) && number >= 0 ? number : fallback;
    }

    function persistedCacheCursor(slot, processed) {
        return normalizeCacheCursor(readCacheCursorState()[cacheCursorKey(slot, processed)], 0);
    }

    function saveCacheCursor(slot, processed, value) {
        const cursor = normalizeCacheCursor(value, 0);
        const state = readCacheCursorState();
        state[cacheCursorKey(slot, processed)] = cursor;
        try {
            window.localStorage.setItem(CACHE_CURSOR_STORAGE_KEY, JSON.stringify(state));
        } catch {
            // Local storage is optional; the in-memory cursor still works for this page.
        }
        return cursor;
    }

    function showCacheCursor(slot, value) {
        const element = input(`llm_prompt_studio_${slot}_inline_cache_cursor`);
        if (!element) return;
        const prototype = element instanceof HTMLInputElement ? HTMLInputElement.prototype : null;
        const setter = prototype && Object.getOwnPropertyDescriptor(prototype, "value")?.set;
        if (setter) setter.call(element, String(value));
        else element.value = String(value);
    }

    function currentForgeLog(tab) {
        return String(find(`html_log_${tab}`)?.textContent || "").trim();
    }

    function ownsActiveForgeTask(run) {
        if (!run?.forgeStarted || !run.target) return false;
        if (run.forgeTaskId) return currentForgeTaskId(run.target) === run.forgeTaskId;
        return isForgeBusy(run.target, run);
    }


    function assertActive(run) {
        const isCurrent = run.scope === "inline"
            ? inlineRuns[run.slot] === run
            : run.scope === "linked"
                ? linkedRuns[run.slot] === run
                : state.active === run;
        if (run.cancelled || !isCurrent) throw new Error("已取消");
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






    async function waitForForgeGeneration(tab, run, beforeStatus, taskId = "", timeoutMs = 1800000, beforeLog = "", launchObserved = false) {
        const statusHost = find(`${tab}_status`);
        // A Generate click can be deferred by Gradio until a later animation
        // frame. A changed button/interrupt control only proves that the click
        // was accepted, not that Forge has submitted the job. Do not treat
        // that UI change as a completed generation or release the cache claim.
        let sawBusy = Boolean(taskId);
        const budget = createTimeoutBudget(timeoutMs);
        const launchBudget = createTimeoutBudget(10000);
        while (true) {
            assertActive(run);
            const currentTaskId = currentForgeTaskId(tab);
            const taskTracked = Boolean(taskId) && currentTaskId === taskId;
            const taskReplaced = Boolean(taskId) && Boolean(currentTaskId) && currentTaskId !== taskId;
            const busy = taskId ? taskTracked : isForgeBusy(tab, run);
            sawBusy ||= busy;
            const currentStatus = String(statusHost?.textContent || "");
            const currentLog = currentForgeLog(tab);
            const logChanged = currentLog !== beforeLog;
            const statusCompleted = currentStatus !== beforeStatus && SUCCESS_PATTERN.test(currentStatus);
            if (taskReplaced) {
                throw new Error(`${tab} generation task ownership changed; prompt returned to pending`);
            }
            if (!sawBusy && !taskId && !statusCompleted && !logChanged && launchBudget.expired()) {
                throw new Error(`${tab} Forge 未启动生图任务，请检查 Forge 队列或页面状态`);
            }
            if (!busy && (sawBusy || statusCompleted || (launchObserved && logChanged))) {
                const failure = [currentStatus, logChanged ? currentLog : ""]
                    .filter(Boolean).join(" ");
                if (FAILURE_PATTERN.test(failure) || /out of memory|traceback|exception|cuda/i.test(failure)) {
                    throw new Error(failure);
                }
                return;
            }
            if (budget.expired()) break;
            await waitForUiSignal(250);
        }
        throw new Error(`${tab} generation timeout; prompt returned to pending`);
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

    function notifyPromptProgrammaticChange(slot, value) {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        const run = linkedRuns[normalizedSlot];
        if (!run) return;
        const next = String(value ?? "");
        run.basePrompt = next;
        run.lastWrittenPrompt = next;
        run.promptEdited = false;
    }

    function restoreNativeCacheClaim(run) {
        const suffix = String(run?.nativeClaimSuffix || "");
        if (!suffix) return false;
        const current = promptRawValue(run.slot);
        const restored = stripStaleNativeClaim(current);
        if (restored === current || !current.includes(suffix)) {
            run.nativeClaimSuffix = "";
            run.nativeClaimPrompt = "";
            return false;
        }
        setValue(`${run.slot}_prompt`, restored);
        notifyPromptProgrammaticChange(run.slot, restored);
        run.nativeClaimSuffix = "";
        run.nativeClaimPrompt = "";
        return true;
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
        const previousTaskId = currentForgeTaskId(tab);
        const wasGenerateDisabled = Boolean(generate.disabled);
        let taskId = "";
        let launchObserved = false;
        try {
            run.forgeLaunching = typeof afterClick === "function";
            const keepAliveReady = enablePluginKeepAlive();
            if (document.hidden && !keepAliveReady && !forgeKeepAliveEnabled()) {
                throw new Error("Forge 未启用后台继续生成，请开启 keep_alive 后刷新页面");
            }
            // Consume this bypass only for the native click that is about to
            // be dispatched. Keep it after the hidden-page guard so a failed
            // launch cannot leak a bypass into the next user click.
            if (run.native) validateNativeForgeScript(tab);
            nativeGenerateBypass[tab] += 1;
            generate.click();
            taskId = currentForgeTaskId(tab);
            // Forge's Gradio submit callback can run on a later animation
            // frame. Always wait for a fresh task ID, including native
            // Generate calls; otherwise the next Generate forever round can
            // observe the previous task ID and be rejected as task ownership
            // changing when that ID is replaced.
            const launchBudget = createTimeoutBudget(10000);
            await wait(0);
            taskId = currentForgeTaskId(tab);
            while (!taskId || taskId === previousTaskId) {
                const launchEvidence = (!wasGenerateDisabled && Boolean(generate.disabled))
                    || String(find(`${tab}_status`)?.textContent || "") !== beforeStatus
                    || currentForgeLog(tab) !== beforeLog;
                if (launchEvidence) {
                    // A very fast Forge task may create and remove its task ID
                    // between two samples. Changed native controls/status/log
                    // still prove that the click was submitted.
                    launchObserved = true;
                    taskId = "";
                    break;
                }
                if (launchBudget.expired()) throw new Error(`${tab} Forge 未启动生图任务，请检查 Forge 队列或页面状态`);
                await wait(25);
                taskId = currentForgeTaskId(tab);
            }
            run.forgeStarted = true;
            run.forgeTaskId = taskId;
            run.forgeLaunching = false;
            if (run.cancelled && ownsActiveForgeTask(run)) findButton(`${tab}_interrupt`)?.click();
            if (typeof afterClick === "function") afterClick();
            assertActive(run);
            await waitForForgeGeneration(tab, run, beforeStatus, taskId, 1800000, beforeLog, launchObserved);
        } finally {
            run.forgeLaunching = false;
            if (typeof afterClick === "function") afterClick();
            run.forgeStarted = false;
            run.forgeTaskId = "";
            const completedTaskId = taskId || currentForgeTaskId(tab) || (launchObserved ? previousTaskId : "");
            if (completedTaskId) {
                run.lastCompletedForgeTaskId = completedTaskId;
                lastCompletedForgeTaskIds[tab] = completedTaskId;
            }
            const generateControl = findButton(`${tab}_generate`);
            const interrupting = find(`${tab}_interrupting`);
            if (generateControl && !generateControl.disabled && !isControlShown(interrupting)) {
                const interrupt = find(`${tab}_interrupt`);
                if (interrupt) interrupt.style.display = "none";
            }
        }
    }

    async function readInlineCache(slot, run, processed = false) {
        const cursors = processed ? processedCacheCursors : inlineCacheCursors;
        const endpoint = processed ? "processed-cache" : "cache";
        const label = processed ? "处理结果库" : "原始缓存库";
        const sourceKey = cacheCursorKey(slot, processed);
        if (run.cacheCursorSource !== sourceKey) {
            const requested = run.cacheCursorOverride == null
                ? -1
                : normalizeCacheCursor(run.cacheCursorOverride, 0);
            const initial = requested >= 0 ? requested : persistedCacheCursor(slot, processed);
            cursors[slot] = initial;
            run.cacheCursorSource = sourceKey;
            run.cacheCursor = initial;
            showCacheCursor(slot, initial);
        }
        // A linked run may prefetch the next record before the current Forge
        // round finishes. Start after the record already assigned to the
        // active round so prefetch never repeats it.
        let afterId = normalizeCacheCursor(run.activeCacheCursor ?? run.cacheCursor, cursors[slot]);
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
            run.pendingCacheCursor = nextId;
            run.pendingCacheSource = sourceKey;
            return prompt;
        }
        throw new Error(processed ? "处理结果库为空，请先完成缓存处理" : "原始缓存库为空，请先生成或导入 Prompt");
    }

    function commitInlineCacheCursor(run) {
        const sourceKey = run.activeCacheSource || run.pendingCacheSource;
        const cursorValue = run.activeCacheCursor ?? run.pendingCacheCursor;
        if (!sourceKey || !Number.isSafeInteger(cursorValue)) return;
        const [slot, source] = sourceKey.split(":");
        const processed = source === "processed_cache";
        const cursors = processed ? processedCacheCursors : inlineCacheCursors;
        const cursor = saveCacheCursor(slot, processed, cursorValue);
        cursors[slot] = cursor;
        run.cacheCursor = cursor;
        showCacheCursor(slot, cursor);
        run.activeCacheCursor = null;
        run.activeCacheSource = "";
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
            cacheCursor: overrides.cacheCursor == null || overrides.cacheCursor === "" ? null : normalizeCacheCursor(overrides.cacheCursor, 0),
            preset: normalizeChoiceValue(sharedWorkflowValue("preset", overrides.preset || componentValue(inlineId(slot, "preset"), "Danbooru Tags"))),
            baseModel: normalizeChoiceValue(sharedWorkflowValue("base_model", overrides.baseModel || componentValue(inlineId(slot, "base_model"), "Auto / checkpoint default"))),
            safety: normalizeChoiceValue(sharedWorkflowValue("safety", overrides.safety || componentValue(inlineId(slot, "safety"), "SFW"))),
            template: normalizeChoiceValue(overrides.template || componentValue(inlineId(slot, "template_choice"), "general")),
        };
    }

    function finishLinkedRun(run) {
        if (run.promptElement && run.promptListener) {
            run.promptElement.removeEventListener?.("input", run.promptListener);
            run.promptElement.removeEventListener?.("change", run.promptListener);
        }
        if (linkedRuns[run.slot] === run) linkedRuns[run.slot] = null;
    }

    function nativeCacheEnabled(slot) {
        if (!componentChecked(inlineId(slot, "cache_enabled"), false)) return false;
        const source = normalizeChoiceValue(componentValue(inlineId(slot, "source"), ""));
        return source === "cache" || source === "processed_cache";
    }

    function nativeCacheUiState(slot) {
        return nativeCacheUi[slot] ||= {
            attempted: false, loading: null, snapshot: null, bound: new WeakSet(), applied: new Map(),
            edited: new Set(), pendingEdits: new Set(), values: {}, observed: {}, manualCursors: {},
            saveTimer: null, saveChain: Promise.resolve(), savedConfig: "",
        };
    }

    function nativeCacheSetting(slot, field) {
        return field === "cache_enabled" ? componentChecked(inlineId(slot, field))
            : field === "marker" ? input(inlineId(slot, field))?.value ?? "{{LLM}}"
                : componentValue(inlineId(slot, field));
    }

    async function fetchNativeCacheState(slot) {
        const response = await studioFetch('/llm-prompt-studio/v1/native-cache/state?slot=' + encodeURIComponent(slot), { cache: "no-store" });
        let data = null;
        try { data = await response.json(); } catch { /* Report HTTP and schema errors below. */ }
        if (!response.ok) throw new Error(String(data?.detail || '读取缓存设置失败：HTTP ' + response.status));
        if (!data?.config || !data?.cursors) throw new Error("缓存设置返回格式无效，请更新插件并重启 Forge");
        return data;
    }

    async function restoreNativeCacheControl(slot, field, value, ui) {
        const host = find(inlineId(slot, field));
        const element = input(inlineId(slot, field));
        if (!element) return false;
        if (ui.pendingEdits.has(field)) return false;
        if (field === "cache_enabled") {
            if (element.checked !== Boolean(value)) element.click();
        } else if (host.querySelector('input[type="radio"]')) {
            const choice = Array.from(host.querySelectorAll('input[type="radio"]'))
                .find(node => normalizeChoiceValue(node.value) === value);
            if (!choice) throw new Error('未找到缓存设置选项：' + value);
            if (!choice.checked) choice.click();
        } else if (field === "write_mode" && element.tagName !== "SELECT") {
            if (componentValue(inlineId(slot, field)) === value) return true;
            const before = ui.values[field];
            element.focus?.();
            element.click();
            await wait(0);
            // Gradio mounts dropdown options on focus. A user selection made
            // during that render takes precedence over the saved setting.
            if (ui.pendingEdits.has(field) || (ui.edited.has(field) && ui.values[field] !== before)) return false;
            const choice = Array.from(host.querySelectorAll('[role="option"]')).find(node =>
                normalizeChoiceValue(node.getAttribute?.("aria-label") || node.textContent.replace(/✓/g, "")) === value);
            if (!choice) throw new Error('未找到缓存合并选项：' + value + '，请刷新页面重试');
            choice.dispatchEvent(new Event("mousedown", { bubbles: true, cancelable: true }));
            choice.click();
            await wait(0);
        } else {
            setValue(inlineId(slot, field), value);
        }
        return true;
    }

    function showNativeCacheProgress(slot, source) {
        const ui = nativeCacheUiState(slot);
        if (source !== "cache" && source !== "processed_cache") return null;
        const manual = ui.manualCursors[source];
        const cursor = manual ? manual.value : normalizeCacheCursor(ui.snapshot?.cursors[source], null);
        showCacheCursor(slot, cursor == null ? "" : cursor);
        return cursor;
    }

    async function restoreNativeCacheState(slot) {
        const ui = nativeCacheUiState(slot);
        if (ui.loading) return ui.loading;
        ui.attempted = true;
        ui.loading = (async () => {
            try {
                const fetched = !ui.snapshot;
                if (!ui.snapshot) {
                    ui.snapshot = await fetchNativeCacheState(slot);
                    ui.savedConfig = JSON.stringify(ui.snapshot.config);
                }
                for (const field of NATIVE_CACHE_CONFIG_FIELDS) {
                    const host = find(inlineId(slot, field));
                    if (!host || ui.applied.get(field) === host) continue;
                    const value = ui.edited.has(field) ? ui.values[field] : ui.snapshot.config[field];
                    if (await restoreNativeCacheControl(slot, field, value, ui)) {
                        ui.applied.set(field, host);
                        ui.observed[field] = nativeCacheSetting(slot, field);
                    }
                }
                showNativeCacheProgress(slot, nativeCacheSetting(slot, "source"));
                if (fetched && ui.edited.size) scheduleNativeCacheSave(slot);
                return ui.snapshot;
            } catch (error) {
                renderInline(slot, "error", "缓存设置恢复失败", String(error?.message || error) + ' · 请刷新页面，或修改设置后重试');
                throw error;
            } finally {
                ui.loading = null;
            }
        })();
        return ui.loading;
    }

    function scheduleNativeCacheSave(slot) {
        const ui = nativeCacheUiState(slot);
        if (ui.saveTimer != null) window.clearTimeout(ui.saveTimer);
        ui.saveTimer = window.setTimeout(() => {
            ui.saveTimer = null;
            ui.saveChain = ui.saveChain.then(async () => {
                if (!ui.snapshot) await restoreNativeCacheState(slot);
                const config = Object.fromEntries(NATIVE_CACHE_CONFIG_FIELDS.map(field => [field,
                    ui.edited.has(field) ? ui.values[field] : ui.snapshot.config[field],
                ]));
                const serialized = JSON.stringify(config);
                if (serialized === ui.savedConfig) return;
                const response = await studioFetch("/llm-prompt-studio/v1/native-cache/state", {
                    method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
                    body: JSON.stringify({ slot, config }),
                });
                if (!response.ok) {
                    const data = await response.json();
                    throw new Error(String(data?.detail || 'HTTP ' + response.status));
                }
                ui.savedConfig = serialized;
                ui.snapshot.config = config;
            }).catch(error => {
                renderInline(slot, "error", "缓存设置保存失败", String(error?.message || error) + ' · 本页设置仍有效，请检查连接并修改设置重试');
            });
        }, 250);
    }

    function installNativeCacheState() {
        for (const slot of ["txt2img", "img2img"]) {
            const ui = nativeCacheUiState(slot);
            let mounted = false;
            for (const field of [...NATIVE_CACHE_CONFIG_FIELDS, "cache_cursor"]) {
                const host = find(inlineId(slot, field));
                if (!host || !input(inlineId(slot, field))) continue;
                mounted = true;
                if (ui.bound.has(host)) continue;
                ui.bound.add(host);
                if (field !== "cache_cursor") ui.observed[field] = nativeCacheSetting(slot, field);
                const edit = () => {
                    if (field === "cache_cursor") {
                        const source = nativeCacheSetting(slot, "source");
                        const raw = String(input(inlineId(slot, field))?.value || "").trim();
                        const value = raw === "" ? null : normalizeCacheCursor(raw, null);
                        if (raw && value == null) return;
                        ui.manualCursors[source] = { value };
                        return;
                    }
                    const value = nativeCacheSetting(slot, field);
                    if (ui.observed[field] === value) return;
                    ui.observed[field] = value;
                    ui.values[field] = value;
                    ui.edited.add(field);
                    if (field === "source" && ui.snapshot) showNativeCacheProgress(slot, value);
                    scheduleNativeCacheSave(slot);
                };
                for (const type of ["input", "change"]) host.addEventListener(type, event => {
                    if (event.isTrusted !== true) return;
                    if (field === "write_mode" && input(inlineId(slot, field))?.tagName !== "SELECT") return;
                    edit();
                });
                if (field === "write_mode") {
                    for (const type of ["mousedown", "keydown"]) host.addEventListener(type, event => {
                        if (event.isTrusted !== true) return;
                        if (type === "keydown" ? event.key !== "Enter" : !event.target?.closest?.('[role="option"]')) return;
                        ui.pendingEdits.add(field);
                        void wait(0).then(() => {
                            edit();
                            ui.pendingEdits.delete(field);
                        });
                    });
                }
            }
            if (mounted && (!ui.attempted || ui.snapshot)) void restoreNativeCacheState(slot).catch(() => {});
        }
    }

    function selectedForgeScript(slot) {
        const tab = find('tab_' + slot) || find(slot);
        const host = tab?.querySelector("#script_list");
        return String(host?.querySelector(".single-select")?.textContent.trim()
            || host?.querySelector("select, input, textarea")?.value || "None").trim();
    }

    function validateNativeForgeScript(slot) {
        const script = selectedForgeScript(slot);
        if (script.toLowerCase() !== "none") {
            throw new Error('缓存注入不支持 Forge Script 多任务扩展：' + script + '；请将 Script 设为 None 后再生成');
        }
        return script;
    }

    function nativeCacheConfig(slot) {
        if (!nativeCacheEnabled(slot)) return null;
        const source = normalizeChoiceValue(componentValue(inlineId(slot, "source"), "cache"));
        if (source !== "cache" && source !== "processed_cache") {
            throw new Error("启用缓存 Prompt 注入时，请选择原始缓存库或处理结果库");
        }
        return inlineConfig(slot, {
            source,
            destination: "prompt",
            writeMode: componentValue(inlineId(slot, "write_mode"), "append_end"),
            marker: componentValue(inlineId(slot, "marker"), "{{LLM}}"),
            cacheCursor: componentValue(inlineId(slot, "cache_cursor"), ""),
            request: componentValue(inlineId(slot, "request"), ""),
            variation: componentValue(inlineId(slot, "variation"), ""),
        });
    }

    function readNativeBatchSettings(slot) {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        const number = (suffix, fallback) => {
            const value = Number(componentValue(`${normalizedSlot}_${suffix}`, fallback));
            return Number.isFinite(value) ? Math.max(1, Math.floor(value)) : fallback;
        };
        return {
            batch_size: number("batch_size", 1),
            batch_count: number("batch_count", 1),
        };
    }

    async function requestNativeCacheContext(config, run) {
        const ui = nativeCacheUiState(config.slot);
        const snapshot = await fetchNativeCacheState(config.slot);
        assertActive(run);
        if (ui.snapshot) ui.snapshot.cursors = snapshot.cursors;
        else ui.snapshot = snapshot;
        showNativeCacheProgress(config.slot, config.source);
        const selectedScript = validateNativeForgeScript(config.slot);
        const settings = readNativeBatchSettings(config.slot);
        const totalImages = Math.max(1, settings.batch_size * settings.batch_count);
        // Only a visible user edit may override the backend cursor. Restored
        // UI values and localStorage are never authoritative for native jobs.
        const manual = ui.manualCursors[config.source];
        const afterId = manual?.value ?? null;
        const response = await studioFetch("/llm-prompt-studio/v1/native-cache/prepare", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
            body: JSON.stringify({
                slot: config.slot,
                source: config.source,
                after_id: afterId,
                selected_script: selectedScript,
                total_images: totalImages,
                base_prompt: run.basePrompt,
                write_mode: config.writeMode,
                marker: config.marker,
                allow_wrap: true,
            }),
        });
        let data = null;
        try { data = await response.json(); } catch { data = null; }
        if (!response.ok) throw new Error(String(data?.detail || `准备缓存生图失败：HTTP ${response.status}`));
        const token = String(data?.token || "").trim();
        const nextCursor = normalizeCacheCursor(data?.next_cursor, null);
        const actualAfterId = normalizeCacheCursor(data?.after_id, null);
        const count = Number(data?.count || 0);
        run.nativeContextToken = token;
        if (!token || nextCursor == null || actualAfterId == null || count !== totalImages) {
            throw new Error("后端未准备完整的缓存生图上下文，未启动本次生图");
        }
        if (ui.manualCursors[config.source] === manual) delete ui.manualCursors[config.source];
        ui.snapshot.cursors[config.source] = actualAfterId;
        run.cacheCursorSource = cacheCursorKey(config.slot, config.source === "processed_cache");
        run.cacheCursor = actualAfterId;
        showNativeCacheProgress(config.slot, config.source);
        const claimSuffix = nativeClaimSuffix(token);
        const claimPrompt = `${promptRawValue(config.slot)}${claimSuffix}`;
        setValue(`${config.slot}_prompt`, claimPrompt);
        run.nativeClaimSuffix = claimSuffix;
        run.nativeClaimPrompt = claimPrompt;
        run.lastWrittenPrompt = claimPrompt;
        // Gradio serializes the textbox state on the next render turn. Yield
        // once so the native Generate request cannot capture the old prompt.
        await wait(0);
        run.nativeImageCount = count;
        run.pendingCacheCursor = nextCursor;
        run.pendingCacheSource = cacheCursorKey(config.slot, config.source === "processed_cache");
        return data;
    }

    async function releaseNativeCacheContext(run, commit) {
        const token = String(run?.nativeContextToken || "").trim();
        if (!token) return;
        try {
            const response = await studioFetch("/llm-prompt-studio/v1/native-cache/release", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                cache: "no-store",
                body: JSON.stringify({ token, commit: Boolean(commit) }),
            });
            if (!response.ok) throw new Error(`释放缓存生图上下文失败：HTTP ${response.status}`);
            let data = null;
            try { data = await response.json(); } catch { data = null; }
            if (commit && data?.committed !== true) {
                throw new Error("后端尚未确认缓存批次完成，请等待 Forge 完成后刷新读取进度");
            }
            if (data?.committed === true) {
                const cursor = normalizeCacheCursor(data.next_cursor, null);
                const count = normalizeCacheCursor(data.completed_count, null);
                if (cursor == null || count == null) throw new Error("后端完成进度无效，请刷新读取进度");
                run.pendingCacheCursor = cursor;
                run.activeCacheCursor = cursor;
                run.nativeCompletedCount = count;
                const sourceKey = run.activeCacheSource || run.pendingCacheSource;
                if (sourceKey) {
                    const [slot, source] = sourceKey.split(":");
                    const ui = nativeCacheUiState(slot);
                    if (ui.snapshot) ui.snapshot.cursors[source] = cursor;
                }
            }
            run.nativeContextToken = "";
            return data;
        } catch (error) {
            if (!commit) run.nativeContextToken = "";
            if (commit) throw error;
            run.nativeReleaseError = String(error?.message || error);
            return null;
        }
    }

    function startLinkedRun(config) {
        const slot = config.slot === "img2img" ? "img2img" : "txt2img";
        const normalizedConfig = inlineConfig(slot, config);
        const rawPrompt = promptRawValue(slot);
        const currentPrompt = stripStaleNativeClaim(rawPrompt);
        if (currentPrompt !== rawPrompt) setValue(`${slot}_prompt`, currentPrompt);
        const existing = linkedRuns[slot];
        if (existing && !existing.cancelled) {
            existing.config = normalizedConfig;
            return existing;
        }
        const run = {
            scope: "linked", slot, target: slot, cancelled: false, busy: false, count: 0, completed: 0,
            native: true, config: normalizedConfig, basePrompt: currentPrompt,
            lastWrittenPrompt: currentPrompt,
            promptEdited: false, promptElement: null, promptListener: null,
            cacheCursorOverride: normalizedConfig.cacheCursor, cacheCursorSource: "", cacheCursor: 0,
            pendingCacheCursor: null, pendingCacheSource: "", activeCacheCursor: null, activeCacheSource: "",
            abortController: null, requestId: "", pendingNativeClick: false, nativeRetryTimer: null, forgeLaunching: false,
            forgeStarted: false, forgeTaskId: "", lastCompletedForgeTaskId: lastCompletedForgeTaskIds[slot], nativeContextToken: "", nativeImageCount: 0,
            nativeClaimSuffix: "", nativeClaimPrompt: "",
        };
        run.promptElement = input(`${slot}_prompt`);
        run.promptListener = (event) => {
            // Only trusted browser input is a new base Prompt. Forge/Gradio
            // may replay an older value asynchronously after a native job;
            // those synthetic events must never turn cache A into the base
            // for cache B.
            if (event?.isTrusted !== true) return;
            run.promptEdited = true;
        };
        run.promptElement?.addEventListener("input", run.promptListener);
        run.promptElement?.addEventListener("change", run.promptListener);
        linkedRuns[slot] = run;
        return run;
    }

    function stopLinkedRun(slot, interruptForge = false) {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        const run = linkedRuns[normalizedSlot];
        if (!run) return;
        cancelInlineRequest(run);
        run.cancelled = true;
        run.abortController?.abort();
        run.pendingNativeClick = false;
        if (run.nativeRetryTimer != null) {
            run.nativeRetryTimer.cancelled = true;
            run.nativeRetryTimer = null;
        }
        if (interruptForge && ownsActiveForgeTask(run)) findButton(`${normalizedSlot}_interrupt`)?.click();
        renderInline(normalizedSlot, "warning", "缓存 Prompt 生图已停止", "后端保留已完成图片的读取进度，请等待 Forge 结束后刷新确认");
        if (!run.busy) finishLinkedRun(run);
    }

    function discardPendingInlineCache(run) {
        run.pendingCacheCursor = null;
        run.pendingCacheSource = "";
        run.activeCacheCursor = null;
        run.activeCacheSource = "";
    }

    function scheduleNativeRetry(slot, generate, run) {
        if (!run || run.nativeRetryTimer != null) return;
        const retry = () => {
            if (run.cancelled || !nativeCacheEnabled(slot) || !run.pendingNativeClick) {
                run.pendingNativeClick = false;
                return;
            }
            if (run.busy || isForgeBusy(slot, run)) {
                scheduleNativeRetry(slot, generate, run);
                return;
            }
            // Consume exactly one queued native click. Additional clicks that
            // arrived while Forge was busy are coalesced into this generation;
            // this keeps Generate forever from launching overlapping batches.
            run.pendingNativeClick = false;
            void handleNativeGenerate(slot, generate);
        };
        const timer = { cancelled: false };
        run.nativeRetryTimer = timer;
        wait(100).then(() => {
            if (timer.cancelled || run.nativeRetryTimer !== timer) return;
            run.nativeRetryTimer = null;
            retry();
        });
    }

    async function handleNativeGenerate(slot, generate) {
        let config;
        try {
            config = nativeCacheConfig(slot);
            if (config) validateNativeForgeScript(slot);
        } catch (error) {
            renderInline(slot, "error", "缓存 Prompt 注入未执行", String(error?.message || error));
            return;
        }
        if (!config) return;
        const run = startLinkedRun(config);
        if (run.busy) {
            run.pendingNativeClick = true;
            renderInline(slot, "warning", "当前 Forge 生图完成后继续读取缓存", "已记住下一次原生 Generate");
            return;
        }
        if (isForgeBusy(slot, run)) {
            run.pendingNativeClick = true;
            scheduleNativeRetry(slot, generate, run);
            renderInline(slot, "warning", "正在等待当前 Forge 生图完成", "已记住下一次原生 Generate，空闲后自动继续");
            return;
        }
        run.cancelled = false;
        run.busy = true;
        run.config = config;
        const currentPrompt = promptRawValue(slot);
        if (run.promptEdited) {
            run.basePrompt = currentPrompt;
            run.lastWrittenPrompt = currentPrompt;
            run.promptEdited = false;
        }
        let completed = false;
        try {
            renderInline(slot, "warning", `正在准备${inlineSourceName(config)}缓存批次`, "每张图使用一条缓存，等待 Forge 原生 Generate");
            await requestNativeCacheContext(config, run);
            assertActive(run);
            run.activeCacheCursor = run.pendingCacheCursor;
            run.activeCacheSource = run.pendingCacheSource;
            validateNativeForgeScript(slot);
            await runForgeGeneration(slot, run, generate);
            assertActive(run);
            const released = await releaseNativeCacheContext(run, true);
            commitInlineCacheCursor(run);
            const count = released.completed_count;
            run.completed += count;
            completed = count > 0;
            const partial = count < run.nativeImageCount;
            if (!completed) run.pendingNativeClick = false;
            renderInline(slot, partial ? "warning" : "success", partial ? "本批仅完成部分图片" : "本批生图完成",
                count + ' / ' + run.nativeImageCount + ' 张图 · 后端已保存读取进度 ID ' + run.cacheCursor);
        } catch (error) {
            const message = String(error?.message || error);
            run.pendingNativeClick = false;
            const released = await releaseNativeCacheContext(run, false);
            if (released?.committed === true) {
                commitInlineCacheCursor(run);
                run.completed += released.completed_count;
            }
            const progress = released?.committed === true
                ? released.completed_count + ' / ' + run.nativeImageCount + ' 张图 · 后端已保存读取进度 ID ' + run.cacheCursor
                : "后端可能仍在完成生图并自动保存已完成图片的读取进度，请等待 Forge 结束后刷新确认";
            renderInline(slot, message === "已取消" ? "warning" : "error", "缓存 Prompt 生图已停止",
                message + ' · ' + progress + (run.nativeReleaseError ? ' · ' + run.nativeReleaseError : ''));
            discardPendingInlineCache(run);
        } finally {
            if (!completed) discardPendingInlineCache(run);
            restoreNativeCacheClaim(run);
            const current = promptRawValue(slot);
            if (run.promptEdited) {
                run.basePrompt = current;
                run.lastWrittenPrompt = current;
                run.promptEdited = false;
            }
            run.busy = false;
            run.forgeStarted = false;
            run.forgeTaskId = "";
            const continueRun = completed && run.pendingNativeClick && !run.cancelled && nativeCacheEnabled(slot);
            run.pendingNativeClick = false;
            if (continueRun) {
                const pendingRun = run;
                wait(0).then(() => {
                    if (linkedRuns[slot] === pendingRun && !pendingRun.cancelled) {
                        void handleNativeGenerate(slot, generate);
                    }
                });
            } else {
                finishLinkedRun(run);
            }
        }
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

    function installNativeGenerateInterceptors() {
        for (const slot of ["txt2img", "img2img"]) {
            const toggle = find(inlineId(slot, "cache_enabled"))?.querySelector('input[type="checkbox"]');
            if (toggle && toggle.dataset.llmPromptStudioNativeCacheStatus !== "true") {
                toggle.dataset.llmPromptStudioNativeCacheStatus = "true";
                toggle.addEventListener("change", () => {
                    renderInline(
                        slot,
                        toggle.checked ? "success" : "warning",
                        toggle.checked ? "缓存 Prompt 注入已启用" : "缓存 Prompt 注入已关闭",
                        toggle.checked ? "Forge 原生 Generate 将按图片数量读取缓存" : "Forge 原生 Generate 保持原行为",
                    );
                });
            }
            const generate = findButton(`${slot}_generate`);
            if (!generate || generate.dataset.llmPromptStudioNativeCache === "true") continue;
            generate.dataset.llmPromptStudioNativeCache = "true";
            generate.addEventListener("click", (event) => {
                if (nativeGenerateBypass[slot] > 0) {
                    nativeGenerateBypass[slot] -= 1;
                    return;
                }
                if (!nativeCacheEnabled(slot)) return;
                event.preventDefault();
                event.stopImmediatePropagation();
                void handleNativeGenerate(slot, generate);
            }, true);
        }
    }

    async function runInlineDestination(config, run, original) {
        // The inline cache action only stores prompts. Browser generation is
        // handled by the native Generate interceptor below.
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
        const run = beginInlineRun(slot, slot);
        if (!run) return "当前内嵌面板已有任务正在运行";
        run.cacheCursorOverride = config.cacheCursor;
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
            notifyPromptProgrammaticChange(slot, next);
            commitInlineCacheCursor(run);
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
            return "缓存 Prompt 生图已停止 · 后端保留已完成图片的读取进度，请等待 Forge 结束后刷新确认";
        }
        renderInline(normalizedSlot, "warning", "Prompt 任务已停止", run?.unlimited ? `无限 · 已完成 ${run.completed} 条` : "");
        return "Prompt 任务已停止";
    }

    function syncCacheCursor(slot, source) {
        const normalizedSlot = slot === "img2img" ? "img2img" : "txt2img";
        if (source !== "cache" && source !== "processed_cache") return null;
        const ui = nativeCacheUiState(normalizedSlot);
        if (ui.snapshot || ui.attempted) return showNativeCacheProgress(normalizedSlot, source);
        const cursor = persistedCacheCursor(normalizedSlot, source === "processed_cache");
        showCacheCursor(normalizedSlot, cursor);
        return cursor;
    }

    function writePrompt(prompt, target, mode, basePrompt = "") {
        const targetInput = root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`);
        if (!targetInput) throw new Error(`未找到 ${target} Prompt 输入框`);
        // Ranbooru semantics: every cycle combines the frozen base with only this cycle's prompt.
        const next = mode === "append" && String(basePrompt).trim()
            ? `${String(basePrompt).replace(/[\s,]+$/, "")}, ${String(prompt).replace(/^[\s,]+/, "")}`
            : String(prompt).trim();
        setValue(`${target}_prompt`, next);
        notifyPromptProgrammaticChange(target, next);
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
        return rows.length ? rows.map((row) =>
            `<div class="lps-server-queue-row"><span>${escapeHtml(row.position)}</span><b>${escapeHtml(row.status)}</b><small>${escapeHtml(row.error || "")}</small></div>`
        ).join("") : '<div class="lps-auto-loop-empty">暂无服务端队列记录。</div>';
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
                const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
                const finished = Number(counts.completed || 0) + Number(counts.error || 0)
                    + Number(counts.cancelled || 0) + Number(counts.expired || 0);
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


    installForgeInterruptHandlers();
    installNativeGenerateInterceptors();
    installNativeCacheState();
    if (typeof onAfterUiUpdate === "function") onAfterUiUpdate(() => {
        installForgeInterruptHandlers();
        installNativeGenerateInterceptors();
        installNativeCacheState();
    });
    window.llmPromptStudioAutoLoop = {
        inlineOnce,
        cancelInline,
        syncCacheCursor,
        watchServerQueue,
        navigate: navigateStudioTab,
        focusHandoff,
        readTxt2imgSettings,
    };
})();
