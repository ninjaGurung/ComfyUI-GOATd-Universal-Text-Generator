import { app } from "../../../scripts/app.js";
import { goatFetch, keyReference, keyValue } from "./security.js";
import { refreshContextControls } from "./context.js";

const defaults = {
    OpenRouter: "https://openrouter.ai/api/v1",
    Cerebras: "https://api.cerebras.ai/v1",
    "LM Studio": "http://127.0.0.1:1234",
    Ollama: "http://127.0.0.1:11434",
    "OpenAI-compatible": "https://api.openai.com/v1",
    Groq: "https://api.groq.com/openai/v1",
    OpenCode: "https://opencode.ai/inference/openai/v1",
    "ComfyUI Local": "",
};
const primary = new Set(["provider", "model", "api_key", "thinking", "reasoning_effort", "seed", "control_after_generate", "json_output", "remember_context"]);
const generationDefaults = {
    model: "",
    temperature: -1, top_p: -1, top_k: -1, min_p: -1, repetition_penalty: -1,
    frequency_penalty: 0, presence_penalty: 0, max_output_tokens: 1024,
    context_window: 0, remember_context: false, timeout_seconds: 120, stop_sequences: "",
    force_regenerate: false, video_mode: "auto", video_frame_count: 0,
    thinking: "auto", reasoning_effort: "auto",
};
const localDefaults = { temperature: 0.7, top_p: 0.95, top_k: 40, min_p: 0.05, repetition_penalty: 1.1, thinking: "disabled" };
const stateKey = Symbol("goatSettings");
let nextModelListId = 0;

function syncContext(node) {
    const state = node[stateKey];
    const limit = Number(state.widgets.context_window.value);
    if (state.widgets.remember_context) state.widgets.remember_context.value = limit > 0;
    if (limit > 0) state.contextWindows.set(state.provider, limit);
    refreshContextControls(node);
    node.setDirtyCanvas(true, true);
}

function commit(node, widget, value) {
    if (widget.setValue && widget.name !== "api_key") widget.setValue(value, { node, canvas: app.canvas });
    else {
        widget.value = value;
        widget.callback?.(value, app.canvas, node);
    }
    node.setDirtyCanvas(true, true);
}

function editValue(node, widget, secret) {
    const state = node[stateKey];
    state.dialog?.close();
    const dialog = document.createElement("dialog");
    dialog.style.cssText = "max-width:calc(100vw - 32px);width:440px;background:var(--comfy-menu-bg,#222);color:var(--input-text,#ddd);border:1px solid var(--border-color,#555);border-radius:8px;padding:20px";
    const form = document.createElement("form");
    form.method = "dialog";
    const label = document.createElement("label");
    label.textContent = widget.name.replaceAll("_", " ");
    label.style.display = "block";
    const input = document.createElement("input");
    input.type = secret ? "password" : "text";
    input.value = String(widget.name === "api_key" ? keyValue(widget) : widget.value ?? "");
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("aria-label", label.textContent);
    input.style.cssText = "box-sizing:border-box;width:100%;margin:10px 0 16px;padding:8px";
    label.append(input);
    form.append(label);
    if (!secret) {
        const status = document.createElement("p");
        status.setAttribute("role", "status");
        status.style.cssText = "font-size:12px;margin:0 0 8px;overflow-wrap:anywhere";
        const list = document.createElement("div");
        list.id = `goat-models-${++nextModelListId}`;
        list.setAttribute("role", "listbox");
        list.setAttribute("aria-label", "Available models");
        list.style.cssText = "max-height:280px;overflow:auto;border:1px solid var(--border-color,#555);margin-bottom:12px";
        input.setAttribute("role", "combobox");
        input.setAttribute("aria-autocomplete", "list");
        input.setAttribute("aria-controls", list.id);
        input.setAttribute("aria-expanded", "true");
        input.placeholder = "Search models or enter a model ID";
        let filter = "", active = -1, filtered = [];
        const render = () => {
            filtered = state.models.filter((model) => model.toLowerCase().includes(filter.toLowerCase()));
            active = -1;
            input.removeAttribute("aria-activedescendant");
            status.textContent = state.status || `${filtered.length} of ${state.models.length} models · Custom model IDs are also accepted.`;
            list.replaceChildren();
            for (const [index, model] of filtered.entries()) {
                const option = document.createElement("div");
                option.id = `${list.id}-${index}`;
                option.setAttribute("role", "option");
                option.setAttribute("aria-selected", "false");
                option.textContent = model;
                option.style.cssText = "padding:8px;cursor:pointer;overflow-wrap:anywhere";
                if (index > 0) option.style.borderTop = "1px solid var(--border-color,#555)";
                option.onpointerdown = (event) => event.preventDefault();
                option.onclick = () => {
                    commit(node, widget, model);
                    dialog.close();
                };
                list.append(option);
            }
            if (!filtered.length) {
                const empty = document.createElement("p");
                empty.textContent = state.request ? "Loading models…" : "No matching models. Enter a custom model ID above.";
                empty.style.padding = "0 8px";
                list.append(empty);
            }
        };
        input.oninput = () => { filter = input.value; render(); };
        input.onkeydown = (event) => {
            if ((event.key === "ArrowDown" || event.key === "ArrowUp") && filtered.length) {
                event.preventDefault();
                active = active < 0 ? (event.key === "ArrowDown" ? 0 : filtered.length - 1)
                    : (active + (event.key === "ArrowDown" ? 1 : -1) + filtered.length) % filtered.length;
                for (const [index, option] of [...list.children].entries()) {
                    option.setAttribute("aria-selected", String(index === active));
                    option.style.background = index === active ? "var(--comfy-input-bg,#444)" : "";
                }
                const option = list.children[active];
                input.setAttribute("aria-activedescendant", option.id);
                option.scrollIntoView({ block: "nearest" });
            } else if (event.key === "Enter" && active >= 0) {
                event.preventDefault();
                commit(node, widget, filtered[active]);
                dialog.close();
            }
        };
        const refresh = document.createElement("button");
        refresh.type = "button";
        refresh.textContent = "Refresh models";
        refresh.style.cssText = "display:block;margin-bottom:12px";
        refresh.onclick = () => refreshModels(node);
        form.append(status, list, refresh);
        state.renderModels = render;
        render();
    }
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.onclick = () => dialog.close();
    const ok = document.createElement("button");
    ok.type = "submit";
    ok.textContent = "OK";
    ok.style.marginLeft = "8px";
    form.append(cancel, ok);
    form.onsubmit = () => commit(node, widget, input.value);
    dialog.append(form);
    dialog.addEventListener("close", () => {
        dialog.remove();
        if (state.dialog === dialog) {
            state.dialog = null;
            state.renderModels = null;
        }
    }, { once: true });
    state.dialog = dialog;
    document.body.append(dialog);
    dialog.showModal();
    input.focus();
}

function setupEditor(node, widget, secret) {
    if (secret) {
        widget.draw = function (ctx, node, width, y, height) {
            ctx.fillStyle = LiteGraph.WIDGET_BGCOLOR || "#222";
            ctx.beginPath();
            ctx.roundRect(15, y, width - 30, height, 6);
            ctx.fill();
            ctx.save();
            ctx.beginPath();
            ctx.rect(20, y, width - 40, height);
            ctx.clip();
            ctx.textBaseline = "middle";
            ctx.fillStyle = LiteGraph.WIDGET_SECONDARY_TEXT_COLOR || "#aaa";
            ctx.textAlign = "left";
            ctx.fillText(this.name, 25, y + height / 2);
            ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR || "#ddd";
            ctx.textAlign = "right";
            ctx.fillText(keyValue(this) ? "*****..." : "", width - 25, y + height / 2);
            ctx.restore();
        };
    } else if (!widget.draw && widget.drawWidget) {
        widget.draw = function (ctx, node, width, y) {
            this.y = y;
            this.drawWidget(ctx, { width, showText: true });
        };
    }
    widget.onPointerDown = function (pointer) {
        if (pointer.eDown.button !== 0) return false;
        pointer.onClick = () => editValue(node, this, secret);
        return true;
    };
}

function applyAdvanced(node) {
    const state = node[stateKey];
    const expanded = Boolean(node.properties?.goat_advanced_settings);
    for (const [widget, original] of state.advanced) {
        widget.hidden = !expanded;
        widget.computeSize = expanded ? original.computeSize : () => [0, -4];
        widget.draw = expanded ? original.draw : () => {};
        widget.onPointerDown = expanded ? original.onPointerDown : () => false;
        if (widget.element) widget.element.hidden = !expanded;
    }
    state.toggle.name = expanded ? "Hide advanced settings" : "Show advanced settings";
    const size = node.computeSize();
    node.setSize([Math.max(node.size[0], size[0]), size[1]]);
    node.setDirtyCanvas(true, true);
}

async function refreshModels(node) {
    const state = node[stateKey];
    state.request?.abort();
    const provider = state.widgets.provider.value;
    state.models = [];
    if (provider === "ComfyUI Local") {
        state.request = null;
        state.status = "Models are supplied by the connected CLIP model.";
        state.refresh.name = "Models supplied by connected CLIP";
        state.renderModels?.();
        node.setDirtyCanvas(true, true);
        return;
    }
    const request = new AbortController();
    state.request = request;
    state.status = "Loading models…";
    state.refresh.name = "Loading models…";
    state.renderModels?.();
    node.setDirtyCanvas(true, true);
    let failureMessage = "Could not load models. Check the base URL and server, then refresh.";
    try {
        const values = Object.fromEntries(["provider", "base_url", "api_key", "api_key_env"]
            .map((name) => [name, state.widgets[name]?.value ?? ""]));
        values.api_key = keyValue(state.widgets.api_key);
        values.api_key = values.api_key ? await keyReference(values) : "";
        if (request.signal.aborted) return;
        const response = await goatFetch("/goat/models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            signal: request.signal,
            body: JSON.stringify(values),
        });
        if (!response.ok) {
            const hints = {
                400: "Check the provider and base URL.",
                401: "GOAT server access denied. Enter the configured GOAT_ROUTE_TOKEN.",
                403: "GOAT access denied. Open ComfyUI locally or configure secure remote access.",
                404: "The models endpoint was not found. Check the base URL.",
                429: "The provider rate limit was reached. Try again later.",
                502: "The provider could not be reached. Check the base URL and server.",
                504: "The provider timed out. Try again.",
            };
            failureMessage = hints[response.status] ?? "The provider could not list models. Check the provider and base URL.";
            const detail = await response.json().catch(() => null);
            if (typeof detail?.error === "string") {
                let message = detail.error;
                for (const name of ["api_key", "api_key_env"]) {
                    const secret = String(keyValue(state.widgets[name]));
                    if (secret) message = message.replaceAll(secret, "[redacted]");
                }
                failureMessage = message.slice(0, 600);
            }
            throw new Error("Model request failed");
        }
        const data = await response.json();
        if (request.signal.aborted || state.request !== request) return;
        state.models = [...new Set((data.models ?? []).filter((value) => typeof value === "string"))].sort();
        state.status = state.models.length ? "" : "No models returned. Enter a custom model ID or refresh.";
        state.refresh.name = `Refresh models (${state.models.length} available)`;
    } catch {
        if (request.signal.aborted || state.request !== request) return;
        state.status = failureMessage;
        state.refresh.name = "Models unavailable · click to retry";
    } finally {
        if (state.request === request) {
            state.request = null;
            state.renderModels?.();
            node.setDirtyCanvas(true, true);
        }
    }
}

app.registerExtension({
    name: "ninjaGurung.GOATTextGeneratorSettings",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "GOATTextGeneratorSettings") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            const widgets = Object.fromEntries(this.widgets.map((widget) => [widget.name, widget]));
            const state = this[stateKey] = { widgets, advanced: [], models: [], request: null, dialog: null,
                provider: widgets.provider.value, credentials: new Map(), urls: new Map(),
                controls: new Map(), contextWindows: new Map(),
                status: "Refresh models to load available model IDs." };
            for (const name of ["api_key", "api_key_env"]) if (widgets[name]) setupEditor(this, widgets[name], true);
            setupEditor(this, widgets.model, false);
            state.advanced = this.widgets.filter((widget) => !primary.has(widget.name)).map((widget) =>
                [widget, { computeSize: widget.computeSize, draw: widget.draw, onPointerDown: widget.onPointerDown }]);
            state.refresh = this.addWidget("button", "Refresh models", null, () => refreshModels(this), { serialize: false });
            state.refresh.serialize = false;
            state.toggle = this.addWidget("button", "Show advanced settings", null, () => {
                this.properties ??= {};
                this.properties.goat_advanced_settings = !this.properties.goat_advanced_settings;
                applyAdvanced(this);
            }, { serialize: false });
            state.toggle.serialize = false;
            const providerChanged = widgets.provider.callback;
            widgets.provider.callback = (...args) => {
                providerChanged?.apply(widgets.provider, args);
                const provider = widgets.provider.value;
                if (provider !== state.provider) {
                    state.credentials.set(state.provider, { api_key: keyValue(widgets.api_key), api_key_env: widgets.api_key_env.value });
                    state.urls.set(state.provider, widgets.base_url.value);
                    state.controls.set(state.provider, Object.fromEntries(Object.keys(generationDefaults)
                        .filter((name) => widgets[name]).map((name) => [name, widgets[name].value])));
                    const credentials = state.credentials.get(provider);
                    for (const name of ["api_key", "api_key_env"]) widgets[name].value = credentials?.[name] ?? "";
                    widgets.base_url.value = state.urls.get(provider) ?? defaults[provider] ?? "";
                    const controls = state.controls.get(provider) ?? {
                        ...generationDefaults, ...(provider === "ComfyUI Local" ? localDefaults : {}),
                    };
                    for (const [name, value] of Object.entries(controls)) if (widgets[name]) widgets[name].value = value;
                    state.provider = provider;
                    syncContext(this);
                }
                const url = widgets.base_url;
                if (!url.value) url.value = defaults[provider] ?? "";
                refreshModels(this);
            };
            for (const name of ["api_key", "api_key_env", "base_url"]) {
                const changed = widgets[name].callback;
                widgets[name].callback = (...args) => {
                    changed?.apply(widgets[name], args);
                    refreshModels(this);
                };
            }
            const contextChanged = widgets.context_window.callback;
            widgets.context_window.callback = (...args) => {
                contextChanged?.apply(widgets.context_window, args);
                syncContext(this);
            };
            if (widgets.remember_context) {
                const rememberChanged = widgets.remember_context.callback;
                widgets.remember_context.callback = (...args) => {
                    rememberChanged?.apply(widgets.remember_context, args);
                    const limit = Number(widgets.context_window.value);
                    if (widgets.remember_context.value && !(limit > 0)) {
                        widgets.context_window.value = state.contextWindows.get(state.provider) ?? 8192;
                    } else if (!widgets.remember_context.value && limit > 0) {
                        state.contextWindows.set(state.provider, limit);
                        widgets.context_window.value = 0;
                    }
                    syncContext(this);
                };
            }
            if (!widgets.base_url.value) widgets.base_url.value = defaults[widgets.provider.value] ?? "";
            syncContext(this);
            applyAdvanced(this);
            return result;
        };
        const configure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = configure?.apply(this, arguments);
            const state = this[stateKey];
            if (state) {
                state.dialog?.close();
                state.request?.abort();
                state.request = null;
                state.models = [];
                state.provider = state.widgets.provider.value;
                state.credentials.clear();
                state.urls.clear();
                state.controls.clear();
                state.contextWindows.clear();
                state.status = "Refresh models to load available model IDs.";
                state.refresh.name = "Refresh models";
                if (!state.widgets.base_url.value) state.widgets.base_url.value = defaults[state.widgets.provider.value] ?? "";
                syncContext(this);
                applyAdvanced(this);
            }
            return result;
        };
        const connectionsChanged = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function () {
            const result = connectionsChanged?.apply(this, arguments);
            refreshContextControls(this);
            return result;
        };
        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            const state = this[stateKey];
            if (state) {
                state.request?.abort();
                state.request = null;
                state.dialog?.close();
                state.credentials.clear();
                state.urls.clear();
                state.controls.clear();
                state.contextWindows.clear();
            }
            return removed?.apply(this, arguments);
        };
    },
});
