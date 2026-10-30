import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";

function setup() {
    class Element {
        constructor(tag) { this.tagName = tag.toUpperCase(); this.style = {}; this.children = []; this.attributes = {}; this.offsetHeight = 240; }
        appendChild(child) { child.remove(); this.children.push(child); child.parentElement = this; return child; }
        append(...children) { children.forEach((child) => this.appendChild(child)); }
        remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((child) => child !== this); this.parentElement = null; }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getBoundingClientRect() { return { height: this.offsetHeight / 2 }; }
        setPointerCapture() {}
        releasePointerCapture() {}
        dispatchEvent() {}
        focus() { this.focused = true; }
        querySelector(tag) { return this.children.find((child) => child.tagName === tag.toUpperCase()) ?? null; }
    }
    const document = { createElement: (tag) => new Element(tag) };
    let extension;
    const synced = [];
    const source = readFileSync(new URL("../web/goat_text_generator.js", import.meta.url), "utf8").replace(/^import .*;\r?\n/gm, "");
    const context = { document, Event, setTimeout, clearTimeout,
        app: { registerExtension(value) { extension = value; } },
        ComfyWidgets: { STRING(node, name) { const widget = node.textWidget(name); node.widgets.push(widget); return { widget }; } },
        setupGuideMenu() {}, removeGuideMenu() {}, mountTagSuggestions(node, widget) { synced.push(widget.name); }, removeTagSuggestions() {},
        getComputedStyle: () => ({ position: "static" }), requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    };
    vm.runInNewContext(source, context);
    class Node {
        constructor() {
            this.widgets = [this.textWidget("prompt_idea"), this.textWidget("system_prompt"), { name: "prompt_guide", value: "None" }];
            this.inputs = []; this.size = [560, 900]; this.properties = {};
        }
        textWidget(name) {
            const element = document.createElement("textarea");
            const options = { getValue: () => element.value, setValue: (value) => { element.value = value; } };
            return { name, element, options, type: "customtext", get value() { return this.options.getValue(); }, set value(value) { this.options.setValue(value); } };
        }
        computeSize() { return [560, this.widgets.reduce((sum, widget) => sum + (widget.computeSize?.(560)[1] ?? 24) + 4, 100)]; }
        setSize(size) { this.size = size; }
        setDirtyCanvas() {}
    }
    extension.beforeRegisterNodeDef(Node, { name: "GOATUniversalTextGenerator" });
    const node = new Node(); node.onNodeCreated();
    return { node, synced, widget: (name) => node.widgets.find((widget) => widget.name === name) };
}

const pointer = (clientY, extra = {}) => ({ clientY, pointerId: 1, button: 0, preventDefault() {}, stopPropagation() {}, ...extra });

test("text panels mount before drawing and reserve their own headers and resize handles", () => {
    const { node, widget } = setup();
    for (const [name, height] of [["prompt_idea", 240], ["system_prompt", 160], ["generated_text", 320]]) {
        const area = widget(name);
        assert.equal(area.type, "goat_text_area");
        assert.equal(area.element.tagName, "DIV");
        assert.equal(area.element.children.length, 3);
        assert.equal(area.element.children[1].tagName, "TEXTAREA");
        assert.equal(area.element.children[1].style.resize, "none");
        assert.equal(area.computeSize(560)[1], height);
        assert.equal(area.options.getMinHeight(), height);
        assert.equal(area.options.getMaxHeight(), height);
    }
    assert.equal(widget("generated_text").element.children[1].readOnly, true);
    assert.equal(node.widgets[2].name, "prompt_guide");
});

test("dragging one text area at 50% zoom leaves the other heights and node position alone", () => {
    const { node, widget } = setup();
    const area = widget("prompt_idea");
    const handle = area.element.children[2];
    const initialHeight = node.size[1];
    handle.onpointerdown(pointer(100));
    handle.onpointermove(pointer(140));
    handle.onpointerup(pointer(140));
    assert.equal(area.computeSize(560)[1], 320);
    assert.equal(widget("system_prompt").computeSize(560)[1], 160);
    assert.equal(widget("generated_text").computeSize(560)[1], 320);
    assert.equal(node.size[1], initialHeight + 80);
    assert.equal(node.properties.goat_text_heights.prompt_idea, 320);
});

test("saved heights restore and resize handles support keyboard adjustment", () => {
    const { node, widget } = setup();
    node.properties.goat_text_heights = { prompt_idea: 300, system_prompt: 120, generated_text: 400 };
    node.onConfigure();
    assert.equal(widget("system_prompt").computeSize(560)[1], 120);
    const handle = widget("system_prompt").element.children[2];
    handle.onkeydown(pointer(0, { key: "ArrowDown" }));
    assert.equal(widget("system_prompt").computeSize(560)[1], 140);
    assert.equal(node.properties.goat_text_heights.system_prompt, 140);
    node.onExecuted({ text: ["Generated output"] });
    assert.equal(widget("generated_text").value, "Generated output");
    assert.equal(widget("generated_text").computeSize(560)[1], 400);
});

test("wrapped textarea values and clear controls stay bound to the original input", () => {
    const { widget } = setup();
    const area = widget("prompt_idea");
    const textarea = area.element.children[1];
    textarea.value = "A cute dog";
    assert.equal(area.value, "A cute dog");
    area.element.children[0].children[1].onclick(pointer(0));
    assert.equal(textarea.value, "");
    assert.equal(area.value, "");
    area.value = "Another idea";
    assert.equal(textarea.value, "Another idea");
});

test("connections, configure and re-add refresh reference tags without canvas drawing", () => {
    const { node, synced } = setup();
    for (const refresh of [() => node.onConnectionsChange(), () => node.onConfigure(), () => node.onAdded()]) {
        synced.length = 0;
        refresh();
        assert.deepEqual(synced, ["prompt_idea", "system_prompt"]);
    }
});
