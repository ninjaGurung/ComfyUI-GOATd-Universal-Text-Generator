import { app } from "../../../scripts/app.js";

const stateKey = Symbol("goatContext");

function createSession() {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function contextLimit(node) {
    if (!node.graph) return 0;
    const slot = node.inputs?.findIndex((input) => input.name === "settings") ?? -1;
    let settings = slot < 0 ? null : node.getInputNode(slot);
    const seen = new Set();
    while (settings?.type === "Reroute" && !seen.has(settings)) {
        seen.add(settings);
        settings = settings.getInputNode(0);
    }
    return Number(settings?.widgets?.find((widget) => widget.name === "context_window")?.value ?? 0);
}

function ensureSession(node) {
    const session = node[stateKey].session;
    if (!session.value || node.graph?._nodes?.some((other) => other !== node && other[stateKey]?.session.value === session.value)) {
        session.value = createSession();
    }
}

function updateLayout(node, resize = true) {
    const state = node[stateKey];
    if (node.widgets.at(-1) !== state.footer) {
        node.widgets.splice(node.widgets.indexOf(state.footer), 1);
        node.widgets.push(state.footer);
    }
    const limit = contextLimit(node);
    if (state.limit === limit) return;
    const wasEnabled = state.limit > 0;
    state.limit = limit;
    if (!(limit > 0)) state.used = 0;
    for (const widget of [state.resetSpacing, state.resetButton, state.footer]) widget.hidden = !(limit > 0);
    if (wasEnabled !== (limit > 0)) {
        const minimum = node.computeSize();
        const height = resize ? node.size[1] + (limit > 0 ? 60 : -60) : node.size[1];
        node.setSize([Math.max(node.size[0], minimum[0]), Math.max(minimum[1], height)]);
    }
    state.footer.callback?.();
    node.setDirtyCanvas(true, true);
}

export function refreshContextControls(node) {
    for (const generator of node.graph?._nodes ?? []) {
        if (generator[stateKey]) updateLayout(generator);
    }
}

function drawUsage(ctx, node, width, y, height) {
    const state = node[stateKey];
    if (!(state.limit > 0)) return;
    y += height / 2;
    ctx.save();
    ctx.lineWidth = 3;
    ctx.strokeStyle = "#555";
    ctx.beginPath();
    ctx.arc(22, y, 8, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeStyle = state.used >= state.limit ? "#f59e0b" : "#60a5fa";
    ctx.beginPath();
    ctx.arc(22, y, 8, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, state.used / state.limit));
    ctx.stroke();
    ctx.fillStyle = "#bbb";
    ctx.font = "12px sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(`Context ~${state.used.toLocaleString()} / ${state.limit.toLocaleString()} tokens`, 38, y);
    ctx.restore();
}

function newContext(node) {
    const state = node[stateKey];
    state.reset.value = Number(state.reset.value || 0) + 1;
    for (const name of ["prompt_idea", "generated_text"]) {
        const widget = node.widgets.find((item) => item.name === name);
        if (!widget) continue;
        widget.value = "";
        (widget.element?.querySelector("textarea") ?? widget.element)?.dispatchEvent(new Event("input", { bubbles: true }));
    }
    node.properties ??= {};
    node.properties.goat_generated_text = "";
    state.used = 0;
    state.footer.callback?.();
    node.setDirtyCanvas(true, true);
}

app.registerExtension({
    name: "ninjaGurung.GOATTextGeneratorContext",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "GOATUniversalTextGenerator") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            const session = this.widgets.find((widget) => widget.name === "context_session");
            const reset = this.widgets.find((widget) => widget.name === "context_reset");
            session.value ||= createSession();
            for (const widget of [session, reset]) {
                widget.hidden = true;
                widget.computeSize = () => [0, -4];
                widget.draw = () => {};
                widget.onPointerDown = () => false;
            }
            const state = this[stateKey] = { session, reset, limit: 0, used: 0 };
            state.resetSpacing = this.addCustomWidget({
                name: "context_spacing", type: "goat_context_spacing", value: "", serialize: false,
                options: { serialize: false }, hidden: true,
                computeSize: () => [0, state.limit > 0 ? 0 : -4],
                draw: () => {},
            });
            state.resetButton = this.addWidget("button", "New context", null, () => newContext(this), { serialize: false });
            state.resetButton.serialize = false;
            state.resetButton.tooltip = "Start a new context: clear prior turns, Prompt Idea, and Output; keep System Prompt and Prompt Guide. Available when context_window is above 0.";
            state.resetButton.computeSize = () => [0, state.limit > 0 ? 24 : -4];
            state.resetButton.hidden = true;
            state.footer = this.addCustomWidget({
                name: "context_usage", type: "goat_context_usage", value: "", serialize: false,
                options: { serialize: false }, hidden: true,
                computeSize: () => [0, state.limit > 0 ? 24 : -4],
                draw: drawUsage,
            });
            // Nonserialized controls can precede the prompt without moving saved values.
            for (const widget of [state.resetSpacing, state.resetButton]) this.widgets.splice(this.widgets.indexOf(widget), 1);
            this.widgets.splice(this.widgets.findIndex((widget) => widget.name === "prompt_idea"), 0, state.resetSpacing, state.resetButton);
            return result;
        };
        const configured = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = configured?.apply(this, arguments);
            const state = this[stateKey];
            if (state) {
                ensureSession(this);
                updateLayout(this, false);
            }
            return result;
        };
        const added = nodeType.prototype.onAdded;
        nodeType.prototype.onAdded = function () {
            const result = added?.apply(this, arguments);
            ensureSession(this);
            updateLayout(this);
            return result;
        };
        const connectionsChanged = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function () {
            const result = connectionsChanged?.apply(this, arguments);
            updateLayout(this);
            return result;
        };
        const executed = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = executed?.apply(this, arguments);
            const usage = message?.context_usage?.[0];
            if (usage) {
                this[stateKey].used = Number(usage.used) || 0;
                this[stateKey].footer.callback?.();
            }
            updateLayout(this);
            this.setDirtyCanvas(true, true);
            return result;
        };
    },
});
