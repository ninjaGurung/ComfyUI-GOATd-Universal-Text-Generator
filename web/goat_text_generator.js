// Author: ninjaGurung
// GitHub: https://github.com/ninjaGurung

import { app } from "../../../scripts/app.js";
import { ComfyWidgets } from "../../../scripts/widgets.js";
import { mountTagSuggestions, removeTagSuggestions } from "./tag_suggestions.js";
import { setupGuideMenu, removeGuideMenu } from "./prompt_guides.js";
import "./settings.js";
import "./context.js";

const generatorTypes = new Set(["GOATUniversalTextGenerator", "UniversalLLM"]);
const previewName = "generated_text";
const inputOrderPending = Symbol("goatInputOrderPending");
const textAreaControl = Symbol("goatTextArea");
const copyResetTimer = Symbol("goatCopyResetTimer");
const areaTitles = { prompt_idea: "Prompt Idea", system_prompt: "System Prompt", generated_text: "Output" };

function trimUnusedMediaInputs(node) {
    for (const group of ["images", "videos", "audios"]) {
        const inputs = node.inputs.filter((input) => input.name.startsWith(`${group}.`));
        const lastConnected = inputs.findLastIndex((input) => {
            const index = node.inputs.indexOf(input);
            return node.getInputLink(index) || [...(node.graph?.floatingLinks?.values() ?? [])]
                .some((link) => link.target_id === node.id && link.target_slot === index);
        });
        for (const input of inputs.slice(lastConnected + 2).reverse()) {
            node.removeInput(node.inputs.indexOf(input));
        }
    }
}

function orderGeneratorInputs(node) {
    const inputs = node.inputs ?? [];
    const ordered = [
        ...inputs.filter((input) => input.widget),
        ...inputs.filter((input) => !input.widget && input.name !== "clip" && input.name !== "settings"),
        ...inputs.filter((input) => !input.widget && input.name === "clip"),
        ...inputs.filter((input) => !input.widget && input.name === "settings"),
    ];
    if (inputs.every((input, index) => input === ordered[index])) return;
    const indices = new Map(inputs.map((input, index) => [index, ordered.indexOf(input)]));
    const links = [...(node.graph?.links.values() ?? [])]
        .filter((link) => link.target_id === node.id && indices.has(link.target_slot))
        .map((link) => ({ link, slot: indices.get(link.target_slot) }))
        .filter(({ link, slot }) => link.target_slot !== slot);

    // Vacate occupied targets before moving links to their reordered slots.
    links.forEach(({ link }, index) => { link.target_slot = inputs.length + index; });
    inputs.splice(0, inputs.length, ...ordered);
    links.forEach(({ link, slot }) => { link.target_slot = slot; });
    for (const link of node.graph?.floatingLinks?.values() ?? []) {
        if (link.target_id === node.id && indices.has(link.target_slot)) {
            link.target_slot = indices.get(link.target_slot);
        }
    }
    node.setDirtyCanvas(true, true);
}

function scheduleInputOrder(node) {
    if (node[inputOrderPending] != null) return;
    node[inputOrderPending] = requestAnimationFrame(() => {
        try {
            trimUnusedMediaInputs(node);
            // Restore notifications skipped when Autogrow shifted slots during loading.
            for (const [group, limit] of [["images", 10], ["videos", 4], ["audios", 6]]) {
                const input = node.inputs.filter((input) => input.name.startsWith(`${group}.`)).at(-1);
                if (!input || Number(input.name.match(/\d+$/)[0]) >= limit) continue;
                const index = node.inputs.indexOf(input);
                const link = node.getInputLink(index);
                if (link) node.onConnectionsChange(LiteGraph.INPUT, index, true, link, input);
            }
            orderGeneratorInputs(node);
        } finally {
            node[inputOrderPending] = null;
        }
    });
}

function syncTextAreas(node) {
    for (const widget of node.widgets ?? []) {
        if (["prompt_idea", "system_prompt"].includes(widget.name)) mountTagSuggestions(node, widget);
    }
}

function setupTextArea(node, widget, defaultHeight) {
    if (widget[textAreaControl]) return;
    const textarea = widget.element;
    const panel = document.createElement("div");
    Object.assign(panel.style, {
        position: "relative", display: "flex", flexDirection: "column", width: "100%",
        boxSizing: "border-box", overflow: "hidden", flex: "none",
    });
    const header = document.createElement("div");
    Object.assign(header.style, {
        height: "28px", flex: "0 0 28px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 8px", boxSizing: "border-box",
    });
    const title = document.createElement("div");
    title.textContent = areaTitles[widget.name];
    title.setAttribute("role", "heading");
    title.setAttribute("aria-level", "3");
    Object.assign(title.style, {
        fontSize: "12px", fontWeight: "600", color: "var(--input-text, #ddd)",
    });
    header.appendChild(title);
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "⧉";
    const label = `Copy ${widget.name.replaceAll("_", " ")}`;
    button.title = label;
    button.setAttribute("aria-label", label);
    Object.assign(button.style, {
        width: "24px", height: "24px", padding: "0", cursor: "pointer",
        borderRadius: "4px", border: "1px solid var(--border-color, #555)",
        background: "var(--comfy-input-bg, #222)", color: "var(--input-text, #ddd)",
        pointerEvents: "auto",
    });
    button.onpointerdown = (event) => event.stopPropagation();
    button.onclick = async (event) => {
        event.stopPropagation();
        try {
            await navigator.clipboard.writeText(String(widget.value ?? ""));
            if (!button.parentElement) return;
            clearTimeout(widget[copyResetTimer]);
            button.textContent = "✓";
            button.title = "Copied";
            widget[copyResetTimer] = setTimeout(() => {
                button.textContent = "⧉";
                button.title = label;
                widget[copyResetTimer] = null;
            }, 5000);
        } catch {
            button.textContent = "⧉";
            button.title = "Clipboard unavailable. Select and copy the text.";
        }
    };
    const clear = document.createElement("button");
    clear.type = "button";
    clear.textContent = "×";
    const clearLabel = `Clear ${areaTitles[widget.name]}`;
    clear.title = clearLabel;
    clear.setAttribute("aria-label", clearLabel);
    clear.style.cssText = button.style.cssText;
    Object.assign(clear.style, { marginLeft: "auto", marginRight: "4px" });
    clear.onpointerdown = (event) => event.stopPropagation();
    clear.onclick = (event) => {
        event.stopPropagation();
        widget.value = "";
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        if (widget.name === previewName) {
            node.properties ??= {};
            node.properties.goat_generated_text = "";
        }
        clearTimeout(widget[copyResetTimer]);
        widget[copyResetTimer] = null;
        button.textContent = "⧉";
        button.title = `Copy ${widget.name.replaceAll("_", " ")}`;
        textarea.focus();
        node.setDirtyCanvas(true, true);
    };
    header.append(clear, button);
    const handle = document.createElement("div");
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "horizontal");
    handle.setAttribute("aria-label", `Resize ${areaTitles[widget.name]}`);
    handle.setAttribute("aria-valuemin", "80");
    handle.tabIndex = 0;
    handle.title = "Drag to resize vertically, or use Up/Down arrows";
    Object.assign(handle.style, {
        flex: "0 0 12px", cursor: "ns-resize", touchAction: "none",
        display: "flex", alignItems: "center", justifyContent: "center",
    });
    const grip = document.createElement("span");
    Object.assign(grip.style, { width: "28px", height: "3px", borderRadius: "2px", background: "var(--border-color, #666)", pointerEvents: "none" });
    handle.appendChild(grip);
    Object.assign(textarea.style, {
        flex: "1 1 0", width: "100%", height: "0", minHeight: "0", margin: "0",
        boxSizing: "border-box", resize: "none", overflowY: "auto",
    });
    textarea.setAttribute("aria-label", areaTitles[widget.name]);
    panel.append(header, textarea, handle);
    // A custom DOM type keeps the same controls in classic and Nodes 2.0.
    widget.type = "goat_text_area";
    widget.element = panel;
    widget.options ??= {};
    widget.options.getValue = () => textarea.value;
    widget.options.setValue = (value) => { textarea.value = value; };
    widget.options.margin = 0;
    widget.options.selectOn = ["focusin", "click"];
    let height = defaultHeight;
    widget.options.getMinHeight = widget.options.getMaxHeight = () => height;
    widget.computeSize = (width) => [width, height];
    function setHeight(value, save = false) {
        if (!Number.isFinite(value)) return;
        const previous = height;
        height = Math.max(80, Math.round(value));
        Object.assign(panel.style, { height: `${height}px`, minHeight: `${height}px`, maxHeight: `${height}px` });
        handle.setAttribute("aria-valuenow", String(height));
        if (save) {
            node.properties ??= {};
            node.properties.goat_text_heights ??= {};
            node.properties.goat_text_heights[widget.name] = height;
            const minimum = node.computeSize();
            node.setSize([node.size[0], Math.max(minimum[1], node.size[1] + height - previous)]);
            node.setDirtyCanvas(true, true);
        }
    }
    let drag;
    handle.onpointerdown = (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        drag = { y: event.clientY, height, scale: panel.getBoundingClientRect().height / panel.offsetHeight || 1, id: event.pointerId };
        handle.setPointerCapture(event.pointerId);
    };
    handle.onpointermove = (event) => {
        event.stopPropagation();
        if (drag && event.pointerId === drag.id) setHeight(drag.height + (event.clientY - drag.y) / drag.scale, true);
    };
    handle.onpointerup = handle.onpointercancel = (event) => {
        event.stopPropagation();
        if (!drag || event.pointerId !== drag.id) return;
        drag = null;
        handle.releasePointerCapture(event.pointerId);
    };
    handle.onlostpointercapture = () => { drag = null; };
    handle.onkeydown = (event) => {
        if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        setHeight(height + (event.key === "ArrowDown" ? 20 : -20), true);
    };
    widget[textAreaControl] = { panel, setHeight };
    setHeight(node.properties?.goat_text_heights?.[widget.name] ?? defaultHeight);
    if (!textarea.readOnly) mountTagSuggestions(node, widget);
}

function createPreview(node) {
    const existing = node.widgets?.find((widget) => widget.name === previewName);
    if (existing) return existing;
    const widget = ComfyWidgets.STRING(node, previewName,
        ["STRING", { multiline: true, default: "" }], app).widget;
    widget.options.serialize = false;
    widget.serialize = false;
    widget.element.readOnly = true;
    widget.element.placeholder = "Generated text appears here after execution.";
    widget.element.setAttribute("aria-label", "Generated text");
    widget.element.style.overflowY = "auto";
    setupTextArea(node, widget, 320);
    return widget;
}

function restorePreview(node) {
    const widget = createPreview(node);
    const saved = node.properties?.goat_generated_text;
    widget.value = typeof saved === "string" ? saved : "";
}

app.registerExtension({
    name: "ninjaGurung.GOATTextGenerator",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!generatorTypes.has(nodeData.name)) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            for (const widget of this.widgets ?? []) {
                if (widget.name === "prompt_idea") setupTextArea(this, widget, 240);
                if (widget.name === "system_prompt") setupTextArea(this, widget, 160);
            }
            restorePreview(this);
            setupGuideMenu(this.widgets?.find((widget) => widget.name === "prompt_guide"), this);
            if (nodeData.name === "GOATUniversalTextGenerator") orderGeneratorInputs(this);
            const minimum = this.computeSize();
            this.setSize([Math.max(this.size[0], minimum[0], 560),
                Math.max(this.size[1], minimum[1])]);
            return result;
        };

        const onDrawForeground = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function () {
            const result = onDrawForeground?.apply(this, arguments);
            syncTextAreas(this);
            return result;
        };

        const onAdded = nodeType.prototype.onAdded;
        nodeType.prototype.onAdded = function () {
            const result = onAdded?.apply(this, arguments);
            syncTextAreas(this);
            setupGuideMenu(this.widgets?.find((widget) => widget.name === "prompt_guide"), this);
            return result;
        };

        const onRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            if (this[inputOrderPending] != null) cancelAnimationFrame(this[inputOrderPending]);
            this[inputOrderPending] = null;
            for (const widget of this.widgets ?? []) {
                removeTagSuggestions(widget);
                removeGuideMenu(widget);
                clearTimeout(widget[copyResetTimer]);
                widget[copyResetTimer] = null;
                widget[textAreaControl]?.panel.remove();
            }
            return onRemoved?.apply(this, arguments);
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = onExecuted?.apply(this, arguments);
            if (message && Object.hasOwn(message, "text")) {
                const text = Array.isArray(message.text)
                    ? message.text.filter((value) => value != null).join("\n\n")
                    : String(message.text ?? "");
                this.properties ??= {};
                this.properties.goat_generated_text = text;
                createPreview(this).value = text;
                this.setDirtyCanvas(true, true);
            }
            return result;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = onConfigure?.apply(this, arguments);
            restorePreview(this);
            for (const widget of this.widgets ?? []) {
                const height = this.properties?.goat_text_heights?.[widget.name];
                if (height != null) widget[textAreaControl]?.setHeight(height);
            }
            syncTextAreas(this);
            if (nodeData.name === "GOATUniversalTextGenerator") scheduleInputOrder(this);
            return result;
        };

        const onConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function () {
            const result = onConnectionsChange?.apply(this, arguments);
            syncTextAreas(this);
            if (nodeData.name === "GOATUniversalTextGenerator") scheduleInputOrder(this);
            return result;
        };

        if (nodeData.name === "GOATUniversalTextGenerator") {
            const onInputAdded = nodeType.prototype.onInputAdded;
            nodeType.prototype.onInputAdded = function () {
                const result = onInputAdded?.apply(this, arguments);
                scheduleInputOrder(this);
                return result;
            };
        }
    },
});
