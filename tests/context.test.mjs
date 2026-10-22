import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";
import { webcrypto } from "node:crypto";

function setup() {
    let extension;
    const source = readFileSync(new URL("../web/context.js", import.meta.url), "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, { app: { registerExtension(value) { extension = value; } }, crypto: webcrypto, Event });
    const settings = { widgets: [{ name: "context_window", value: 0 }] };
    class Node {
        constructor() {
            this.widgets = Object.entries({ prompt_idea: "Idea", system_prompt: "Keep", prompt_guide: "Guide", context_session: "", context_reset: 0 })
                .map(([name, value]) => ({ name, value }));
            this.inputs = [{ name: "settings" }];
            this.size = [560, 800];
            this.properties = { goat_generated_text: "Output" };
            this.graph = { _nodes: [this] };
        }
        addWidget(type, name, value, callback, options) {
            return this.addCustomWidget({ type, name, value, callback, options });
        }
        addCustomWidget(widget) { this.widgets.push(widget); return widget; }
        getInputNode() { return settings; }
        computeSize() { return [560, this.widgets.reduce((height, widget) => height + (widget.computeSize?.()[1] ?? 24) + 4, 0)]; }
        setSize(size) { this.size = size; }
        setDirtyCanvas() {}
    }
    extension.beforeRegisterNodeDef(Node, { name: "GOATUniversalTextGenerator" });
    const node = new Node();
    node.onNodeCreated();
    node.widgets.push({ name: "generated_text", value: "Output", serialize: false });
    return { node, Node, settings, widget: (name) => node.widgets.find((widget) => widget.name === name) };
}

test("context controls only show with a positive limit and reserve footer after output", () => {
    const { node, settings, widget } = setup();
    node.onConfigure();
    assert.equal(widget("New context").hidden, true);
    assert.match(widget("New context").tooltip, /clear prior turns, Prompt Idea, and Output; keep System Prompt and Prompt Guide/);
    assert.equal(widget("context_session").hidden, true);
    assert.ok(widget("context_session").value);
    settings.widgets[0].value = 1024;
    node.onConfigure();
    assert.equal(widget("New context").hidden, false);
    assert.equal(node.widgets.at(-1).name, "context_usage");
    assert.ok(node.widgets.indexOf(widget("New context")) < node.widgets.indexOf(widget("prompt_idea")));
    const strings = [];
    const ctx = new Proxy({ fillText(text) { strings.push(text); } }, { get: (target, key) => target[key] ?? (() => {}) });
    node.onExecuted({ context_usage: [{ used: 320, limit: 1024 }] });
    node.onDrawForeground(ctx);
    assert.match(strings[0], /320.*1,024/);
    settings.widgets[0].value = 0;
    node.onConfigure();
    assert.equal(widget("New context").hidden, true);
});

test("new context clears idea/output but preserves system and guide", () => {
    const { node, widget } = setup();
    widget("New context").callback();
    assert.equal(widget("context_reset").value, 1);
    assert.equal(widget("prompt_idea").value, "");
    assert.equal(widget("generated_text").value, "");
    assert.equal(node.properties.goat_generated_text, "");
    assert.equal(widget("system_prompt").value, "Keep");
    assert.equal(widget("prompt_guide").value, "Guide");
});

test("clones configured before graph attachment get separate sessions on addition", () => {
    const { node, Node, widget } = setup();
    const session = widget("context_session").value;
    node.onConfigure();
    assert.equal(widget("context_session").value, session);
    const clone = new Node();
    clone.onNodeCreated();
    clone.widgets.find((item) => item.name === "context_session").value = session;
    clone.graph = null;
    clone.onConfigure();
    clone.graph = node.graph;
    node.graph._nodes.push(clone);
    clone.onAdded();
    assert.notEqual(clone.widgets.find((item) => item.name === "context_session").value, session);
});

test("context controls resolve settings through reroute nodes", () => {
    const { node, settings, widget } = setup();
    settings.widgets[0].value = 16384;
    node.getInputNode = () => ({ type: "Reroute", getInputNode: () => settings });
    node.onConfigure();
    assert.equal(widget("New context").hidden, false);
});
