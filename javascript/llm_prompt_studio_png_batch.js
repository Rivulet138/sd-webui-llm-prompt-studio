(function () {
    "use strict";

    function root() {
        return typeof gradioApp === "function" ? gradioApp() : document;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function status(kind, headline, detail) {
        const safeKind = ["success", "warning", "error"].includes(kind) ? kind : "warning";
        return `<div class="lps-status lps-status--${safeKind}" role="status" aria-live="polite"><strong>${escapeHtml(headline)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}</div>`;
    }

    function setValue(input, value) {
        const prototype = input instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
        if (setter) setter.call(input, value);
        else input.value = value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function componentInput(id) {
        const host = root().querySelector(`#${id}`);
        if (!host) return null;
        return host.matches("textarea, input") ? host : host.querySelector("textarea, input");
    }

    function appendToPrompt(processedPrompt, target, mode) {
        const incoming = String(processedPrompt ?? "").trim();
        if (!incoming) return [status("warning", "当前条没有可写入的结果", "请先完成润色或扩写。"), false];
        if (target !== "txt2img" && target !== "img2img") {
            return [status("warning", "请选择写入目标", "可选择 txt2img 或 img2img。"), false];
        }
        const input = root().querySelector(`#${target}_prompt textarea, #${target}_prompt input`);
        if (!input) return [status("error", "未找到原生 Prompt 输入框", `目标：${target}`), false];

        const current = String(input.value ?? "");
        const next = mode === "replace" || !current.trim()
            ? incoming
            : `${current.replace(/[\s,]+$/, "")}, ${incoming.replace(/^[\s,]+/, "")}`;
        setValue(input, next);
        input.focus({ preventScroll: true });
        const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        input.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
        return [status("success", mode === "replace" ? "已覆盖 Prompt" : "已追加 Prompt", `${target} · ${incoming.length} 字符`), true];
    }

    function appendAllToPrompt(payload, target, mode) {
        let data;
        try {
            data = typeof payload === "string" ? JSON.parse(payload || "{}") : payload;
        } catch (error) {
            return [status("error", "JSON invalid", error?.message || error), false];
        }
        const records = Array.isArray(data?.records) ? data.records : [];
        const prompts = records
            .map((record) => String(record?.prompt?.processed || "").trim())
            .filter(Boolean);
        if (!prompts.length) {
            return [status("warning", "No processed prompts", "Run batch processing first."), false];
        }
        return appendToPrompt(prompts.join(", "), target, mode);
    }

    function appendSelectedToPrompt(payload, selectedIds, mode) {
        let data;
        try {
            data = typeof payload === "string" ? JSON.parse(payload || "{}") : payload;
        } catch (error) {
            return [status("error", "JSON invalid", error?.message || error), false];
        }
        const selected = new Set(Array.isArray(selectedIds) ? selectedIds.map(String) : []);
        const prompts = (Array.isArray(data?.records) ? data.records : [])
            .filter((record) => selected.has(String(record?.record_id || "")) && !record?.appended)
            .map((record) => String(record?.prompt?.processed || "").trim())
            .filter(Boolean);
        if (!prompts.length) {
            return [status("warning", "请先勾选结果", "只有已完成且未写入的结果可以写入。"), false];
        }
        return appendToPrompt(prompts.join(", "), "txt2img", mode);
    }

    function appendScopedToPrompt(payload, currentPrompt, selectedIds, scope, target, mode) {
        if (scope === "current") return appendToPrompt(currentPrompt, target, mode);
        return appendSelectedToPrompt(payload, selectedIds, mode);
    }

    function receiveCollectorBatch(slot) {
        const targetId = `llm_prompt_studio_${slot || "txt2img"}_json_batch_payload`;
        const target = componentInput(targetId);
        const legacy = componentInput("llm_prompt_studio_png_batch_payload");
        if (!target) return status("error", "未找到内嵌 JSON 面板", `目标：#${targetId}`);
        if (!legacy || !String(legacy.value || "").trim()) {
            return status("warning", "PNG Collector 尚无批次", "请先在 PNG Prompt Collector 读取 PNG 或导入 JSON。");
        }
        setValue(target, legacy.value);
        target.focus({ preventScroll: true });
        // Put the user at the next actionable step. Gradio details panels are
        // not guaranteed to be open after a tab switch, so expand them here.
        const studio = window.llmPromptStudioAutoLoop;
        studio?.navigate?.("png_batch");
        const panel = target.closest("details");
        if (panel) panel.open = true;
        const runButton = root().querySelector("#llm_prompt_studio_png_batch_run button");
        runButton?.scrollIntoView?.({ behavior: "smooth", block: "center" });
        return status("success", "已接收 PNG Collector 批次", "批次已写入当前 txt2img JSON 面板。");
    }

    window.llmPromptStudioPngBatch = { appendToPrompt, appendAllToPrompt, appendSelectedToPrompt, appendScopedToPrompt, receiveCollectorBatch };
})();
