// Author: ninjaGurung
// GitHub: https://github.com/ninjaGurung

import { app } from "../../../scripts/app.js";
import { ComfyWidgets } from "../../../scripts/widgets.js";
import { mountTagSuggestions, removeTagSuggestions } from "./tag_suggestions.js";

const generatorTypes = new Set(["GOATUniversalTextGenerator", "UniversalLLM"]);
const previewName = "generated_text";
const inputOrderPending = Symbol("goatInputOrderPending");
const copyControl = Symbol("goatCopyControl");
const titleControl = Symbol("goatTitleControl");
const headerControl = Symbol("goatHeaderControl");
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

function mountCopyButton(widget) {
    const textarea = widget.element;
    const parent = textarea?.parentElement;
    if (!parent) return;
    let header = widget[headerControl];
    if (!header) {
        header = document.createElement("div");
        Object.assign(header.style, {
            position: "absolute", top: "0", left: "0", right: "0", height: "28px",
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "0 8px", boxSizing: "border-box", zIndex: "2", pointerEvents: "none",
        });
        widget[headerControl] = header;
    }
    let title = widget[titleControl];
    if (!title) {
        title = document.createElement("div");
        title.textContent = areaTitles[widget.name];
        title.setAttribute("role", "heading");
        title.setAttribute("aria-level", "3");
        Object.assign(title.style, {
            fontSize: "12px", fontWeight: "600", color: "var(--input-text, #ddd)",
        });
        widget[titleControl] = title;
    }
    if (title.parentElement !== header) header.appendChild(title);
    let button = widget[copyControl];
    if (!button) {
        button = document.createElement("button");
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
        widget[copyControl] = button;
    }
    if (button.parentElement !== header) header.appendChild(button);
    if (header.parentElement !== parent) {
        if (getComputedStyle(parent).position === "static") parent.style.position = "relative";
        textarea.style.marginTop = "28px";
        textarea.style.height = "calc(100% - 28px)";
        parent.appendChild(header);
    }
}

function setupGuideMenu(widget) {
    if (!widget) return;
    const onPointerDown = widget.onPointerDown;
    widget.onPointerDown = function (pointer, node, canvas) {
        if (onPointerDown?.apply(this, arguments)) return true;
        const event = pointer.eDown;
        const x = event.canvasX - node.pos[0];
        const width = this.width || node.size[0];
        if (event.button !== 0 || x < 40 || x > width - 40) return false;
        pointer.onClick = () => {
            const values = this.options.values;
            const sorted = [...values].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
            const leaf = (value, content = value) => ({
                content,
                callback: () => {
                    this.setValue(value, { e: event, node, canvas });
                    node.setDirtyCanvas(true, true);
                },
            });
            const families = ["MiniMax H3", "Seedance"];
            const folders = families.map((family) => ({
                content: `📁 ${family}`,
                has_submenu: true,
                submenu: { options: sorted.filter((value) => value.startsWith(`${family} `))
                    .map((value) => leaf(value, value.slice(family.length).replace(/^ - /, "").trim())) },
            }));
            const rest = sorted.filter((value) => value !== "None" && !families.some((family) => value.startsWith(`${family} `)));
            new LiteGraph.ContextMenu([...folders, leaf("None"), ...rest.map((value) => leaf(value))], {
                event, scale: Math.max(1, canvas.ds.scale), className: "dark", autoopen: true,
            });
        };
        return true;
    };
}

function setMinimumHeight(widget, height) {
    widget.options ??= {};
    widget.options.getMinHeight = () => height;
    const computeSize = widget.computeSize;
    widget.computeSize = function (width) {
        const size = computeSize?.call(this, width) ?? [width, height];
        return size[1] < 0 ? size : [size[0], Math.max(size[1], height)];
    };
    widget.element?.setAttribute("aria-label", widget.name.replaceAll("_", " "));
    if (widget.element) widget.element.style.overflowY = "auto";
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
    setMinimumHeight(widget, 320);
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
                if (widget.name === "prompt_idea") setMinimumHeight(widget, 240);
                if (widget.name === "system_prompt") setMinimumHeight(widget, 160);
            }
            restorePreview(this);
            setupGuideMenu(this.widgets?.find((widget) => widget.name === "prompt_guide"));
            if (nodeData.name === "GOATUniversalTextGenerator") orderGeneratorInputs(this);
            const minimum = this.computeSize();
            this.setSize([Math.max(this.size[0], minimum[0], 560),
                Math.max(this.size[1], minimum[1])]);
            return result;
        };

        const onDrawForeground = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function () {
            const result = onDrawForeground?.apply(this, arguments);
            for (const widget of this.widgets ?? []) {
                if (["prompt_idea", "system_prompt", previewName].includes(widget.name)) mountCopyButton(widget);
                if (["prompt_idea", "system_prompt"].includes(widget.name)) mountTagSuggestions(this, widget);
            }
            return result;
        };

        const onRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            if (this[inputOrderPending] != null) cancelAnimationFrame(this[inputOrderPending]);
            this[inputOrderPending] = null;
            for (const widget of this.widgets ?? []) {
                removeTagSuggestions(widget);
                clearTimeout(widget[copyResetTimer]);
                widget[copyResetTimer] = null;
                widget[copyControl]?.remove();
                widget[titleControl]?.remove();
                widget[headerControl]?.remove();
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
            const onConnectionsChange = nodeType.prototype.onConnectionsChange;
            nodeType.prototype.onConnectionsChange = function () {
                const result = onConnectionsChange?.apply(this, arguments);
                scheduleInputOrder(this);
                return result;
            };
        }
    },
});
