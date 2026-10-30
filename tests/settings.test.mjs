import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";

function mockDocument() {
    class Element {
        constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.style = {}; this.listeners = {}; }
        append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
        replaceChildren(...children) { this.children = []; this.append(...children); }
        setAttribute(name, value) { this.attributes[name] = value; }
        removeAttribute(name) { delete this.attributes[name]; }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        showModal() { this.open = true; }
        close() { this.open = false; this.listeners.close?.(); }
        remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
        focus() {}
        scrollIntoView() {}
    }
    return { createElement: (tag) => new Element(tag), body: new Element("body") };
}

function setup(fetchApi = async () => ({ ok: true, json: async () => ({ models: ["z", "a", "z"] }) }), nativeModel = false) {
    let extension;
    const refreshed = [];
    const document = mockDocument();
    const context = { app: { registerExtension(value) { extension = value; } }, goatFetch: fetchApi, keyReference: async (values) => values.api_key, keyValue: (widget) => widget?.value ?? "", refreshContextControls: (node) => refreshed.push(node), AbortController, LiteGraph: {}, document, crypto: {} };
    const source = readFileSync(new URL("../web/settings.js", import.meta.url), "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, context);
    class Node {
        constructor() {
            this.widgets = Object.entries({ provider: "OpenRouter", model: "custom", api_key: "secret-key", api_key_env: "SECRET_ENV", base_url: "", thinking: "auto", reasoning_effort: "auto", seed: 1, control_after_generate: "fixed", json_output: false, context_window: 0, temperature: 0.7, top_p: -1, top_k: -1, min_p: -1, repetition_penalty: -1, frequency_penalty: 0, presence_penalty: 0, max_output_tokens: 1024, timeout_seconds: 120, stop_sequences: "", force_regenerate: false, video_mode: "auto", video_frame_count: 0, remember_context: false })
                .map(([name, value]) => ({ name, value, type: "string", computeSize: () => [300, 20], draw: nativeModel && name === "model" ? undefined : () => {}, drawWidget: nativeModel === true ? () => {} : undefined }));
            this.size = [300, 300];
        }
        addWidget(type, name, value, callback, options) {
            const widget = { type, name, value, callback, options };
            this.widgets.push(widget);
            return widget;
        }
        computeSize() { return [300, this.widgets.reduce((height, widget) => height + (widget.computeSize?.()[1] ?? 20) + 4, 0)]; }
        setSize(size) { this.size = size; }
        setDirtyCanvas() {}
    }
    extension.beforeRegisterNodeDef(Node, { name: "GOATTextGeneratorSettings" });
    const node = new Node();
    const original = [...node.widgets];
    node.onNodeCreated();
    return { node, original, document, refreshed, widget: (name) => node.widgets.find((widget) => widget.name === name) };
}

test("Nodes 2.0 model widgets keep the searchable model editor", () => {
    const { node, widget, document } = setup(undefined, true);
    const model = widget("model");
    assert.equal(typeof model.draw, "function");
    let drawn = false;
    model.drawWidget = () => { drawn = true; };
    model.draw({}, node, 300, 20, 20);
    assert.equal(drawn, true);
    const { dialog } = openEditor(model, document);
    assert.equal(dialog.open, true);
});

test("classic model widgets without drawWidget keep the built-in renderer", () => {
    const { widget } = setup(undefined, "classic");
    assert.equal(widget("model").draw, undefined);
});

test("custom settings widgets follow node width after switching back from Nodes 2.0", () => {
    const { node, widget } = setup(undefined, true);
    node.widgets.at(-1).callback();
    const shapes = [];
    const ctx = new Proxy({ roundRect(x, y, width) { shapes.push(width); } }, { get: (target, key) => target[key] ?? (() => {}) });
    const model = widget("model");
    let modelWidth;
    model.drawWidget = (ctx, options) => { modelWidth = options.width; };
    for (const width of [240, 640]) {
        node.size[0] = width;
        for (const name of ["model", "api_key", "api_key_env"]) {
            const item = widget(name);
            item.width = 480;
            item.draw(ctx, node, item.width, 20, 20);
            assert.equal(item.width, width, name);
        }
        assert.equal(modelWidth, width);
        assert.deepEqual(shapes.splice(0), [width - 30, width - 30]);
    }
});

function openEditor(widget, document) {
    const pointer = { eDown: { button: 0 } };
    widget.onPointerDown(pointer);
    pointer.onClick();
    const dialog = document.body.children.at(-1);
    const form = dialog.children[0];
    return { dialog, form, input: form.children[0].children[0], status: form.children[1], list: form.children[2] };
}

test("secret editors render masked and preserve entered values and widget order", () => {
    const { node, original, widget } = setup();
    node.widgets.at(-1).callback();
    const drawn = [];
    const ctx = new Proxy({ fillText: (text) => drawn.push(text) }, { get: (target, key) => target[key] ?? (() => {}) });
    widget("api_key").draw(ctx, node, 300, 20, 20);
    widget("api_key_env").draw(ctx, node, 300, 40, 20);
    assert.equal(drawn.includes("secret-key"), false);
    assert.equal(drawn.includes("SECRET_ENV"), false);
    assert.equal(drawn.filter((text) => text === "*****...").length, 2);
    assert.equal(widget("api_key").value, "secret-key");
    assert.deepEqual(node.widgets.slice(0, original.length), original);
    assert.ok(node.widgets.slice(original.length).every((widget) => widget.serialize === false && widget.options.serialize === false));
});

test("advanced expansion restores original behavior and survives configuration", () => {
    const { node, widget } = setup();
    const temperature = widget("temperature");
    assert.equal(temperature.hidden, true);
    assert.equal(widget("api_key_env").hidden, true);
    const smallHeight = node.size[1];
    const toggle = node.widgets.at(-1);
    assert.equal(toggle.name, "Show advanced settings");
    toggle.callback();
    assert.equal(toggle.name, "Hide advanced settings");
    assert.equal(temperature.hidden, false);
    assert.equal(widget("api_key_env").hidden, false);
    assert.ok(node.size[1] > smallHeight);
    assert.equal(temperature.computeSize()[1], 20);
    node.onConfigure();
    assert.equal(temperature.hidden, false);
    toggle.callback();
    assert.equal(toggle.name, "Show advanced settings");
    assert.equal(temperature.hidden, true);
    assert.equal(widget("api_key_env").hidden, true);
    assert.equal(widget("seed").hidden, undefined);
    assert.equal(widget("json_output").hidden, undefined);
    assert.equal(widget("json_output").value, false);
});

test("memory edits and connection changes refresh generator controls without drawing", () => {
    const { node, widget, refreshed } = setup();
    const count = refreshed.length;
    widget("remember_context").value = true;
    widget("remember_context").callback();
    assert.equal(widget("context_window").value, 8192);
    assert.equal(refreshed.length, count + 1);
    widget("context_window").value = 2048;
    widget("context_window").callback();
    node.onConnectionsChange();
    assert.equal(refreshed.length, count + 3);
    assert.ok(refreshed.every((value) => value === node));
});

test("provider changes automatically refresh with provider-owned credentials and preserve custom URLs", async () => {
    const requests = [];
    const { node, widget } = setup(async (url, options) => {
        requests.push({ url, ...options });
        return { ok: true, json: async () => ({ models: ["z", "a", "z"] }) };
    });
    assert.equal(widget("base_url").value, "https://openrouter.ai/api/v1");
    widget("provider").value = "Groq";
    widget("provider").callback("Groq");
    await new Promise(setImmediate);
    assert.equal(widget("base_url").value, "https://api.groq.com/openai/v1");
    assert.equal(requests[0].url, "/goat/models");
    assert.equal(JSON.parse(requests[0].body).api_key, "");
    assert.equal(JSON.parse(requests[0].body).api_key_env, "");
    assert.equal(JSON.parse(requests[0].body).provider, "Groq");
    assert.equal(widget("model").value, "");
    widget("model").value = "groq-model";
    assert.match(node.widgets.at(-2).name, /2 available/);
    widget("base_url").value = "http://localhost:8080/v1";
    widget("provider").value = "Cerebras";
    widget("provider").callback("Cerebras");
    assert.equal(widget("base_url").value, "https://api.cerebras.ai/v1");
    assert.equal(requests.length, 2);
    widget("provider").value = "OpenRouter";
    widget("provider").callback("OpenRouter");
    await new Promise(setImmediate);
    assert.equal(JSON.parse(requests[2].body).api_key, "secret-key");
    assert.equal(JSON.parse(requests[2].body).api_key_env, "SECRET_ENV");
    assert.equal(widget("model").value, "custom");
    assert.equal(JSON.stringify(node.properties ?? {}).includes("secret-key"), false);
    widget("provider").value = "Groq";
    widget("provider").callback("Groq");
    assert.equal(widget("base_url").value, "http://localhost:8080/v1");
    assert.equal(widget("model").value, "groq-model");
    assert.equal(JSON.parse(requests[3].body).api_key, "");
    node.onRemoved();
    assert.equal(requests.at(-1).signal.aborted, true);
});

test("credential and endpoint commits refresh models without replacing the selected model", async () => {
    const requests = [];
    const { node, widget } = setup(async (_url, options) => {
        requests.push(options);
        return { ok: true, json: async () => ({ models: ["new-model"] }) };
    });
    widget("api_key").value = "updated-key";
    widget("api_key").callback("updated-key");
    await new Promise(setImmediate);
    assert.equal(JSON.parse(requests[0].body).api_key, "updated-key");
    widget("base_url").value = "http://localhost:8080/v1";
    widget("base_url").callback(widget("base_url").value);
    await new Promise(setImmediate);
    assert.equal(JSON.parse(requests[1].body).base_url, "http://localhost:8080/v1");
    await new Promise(setImmediate);
    assert.equal(widget("model").value, "custom");
    assert.match(node.widgets.at(-2).name, /1 available/);
});

test("loading workflows performs no provider request; explicit refresh handles failure", async () => {
    let count = 0;
    const { node } = setup(async () => { count++; throw new Error("offline"); });
    node.onConfigure();
    assert.equal(count, 0);
    await node.widgets.at(-2).callback();
    assert.equal(count, 1);
    assert.match(node.widgets.at(-2).name, /unavailable/);
});

test("model picker filters visible options, supports keyboard selection and custom IDs", async () => {
    const { node, widget, document } = setup();
    await node.widgets.at(-2).callback();
    let editor = openEditor(widget("model"), document);
    assert.equal(editor.input.value, "custom");
    assert.equal(editor.input.attributes.role, "combobox");
    assert.deepEqual(editor.list.children.map((option) => option.textContent), ["a", "z"]);
    assert.match(editor.status.textContent, /2 of 2/);
    editor.input.value = "z";
    editor.input.oninput();
    assert.deepEqual(editor.list.children.map((option) => option.textContent), ["z"]);
    assert.match(editor.status.textContent, /1 of 2/);
    editor.input.onkeydown({ key: "ArrowDown", preventDefault() {} });
    assert.equal(editor.list.children[0].attributes["aria-selected"], "true");
    editor.input.onkeydown({ key: "Enter", preventDefault() {} });
    assert.equal(widget("model").value, "z");
    assert.equal(document.body.children.length, 0);
    editor = openEditor(widget("model"), document);
    editor.input.value = "my-custom-model";
    editor.form.onsubmit();
    assert.equal(widget("model").value, "my-custom-model");
    editor.dialog.close();
    editor = openEditor(widget("model"), document);
    node.onRemoved();
    assert.equal(document.body.children.length, 0);
});

test("secret edits use password input, refresh once on commit, and redact provider errors", async () => {
    let calls = 0;
    const { node, widget, document } = setup(async () => {
        calls++;
        return { ok: false, status: 502, json: async () => ({ error: "Denied secret-key / SECRET_ENV" }) };
    });
    let editor = openEditor(widget("api_key"), document);
    assert.equal(editor.input.type, "password");
    assert.equal(calls, 0);
    editor.form.onsubmit();
    editor.dialog.close();
    await new Promise(setImmediate);
    assert.equal(calls, 1);
    editor = openEditor(widget("model"), document);
    assert.match(editor.status.textContent, /Denied \[redacted\] \/ \[redacted\]/);
    assert.equal(editor.status.textContent.includes("secret-key"), false);
    editor.dialog.close();
    node.onRemoved();
});

test("late provider responses cannot replace current model suggestions", async () => {
    const pending = [];
    const { node, widget, document } = setup((_url, options) => new Promise((resolve) => pending.push({ resolve, options })));
    widget("provider").value = "Groq";
    widget("provider").callback("Groq");
    widget("provider").value = "Cerebras";
    widget("provider").callback("Cerebras");
    assert.equal(pending[0].options.signal.aborted, true);
    pending[1].resolve({ ok: true, json: async () => ({ models: ["current-a", "current-b"] }) });
    await new Promise(setImmediate);
    pending[0].resolve({ ok: true, json: async () => ({ models: ["stale"] }) });
    await new Promise(setImmediate);
    const editor = openEditor(widget("model"), document);
    assert.deepEqual(editor.list.children.map((option) => option.textContent), ["current-a", "current-b"]);
    editor.dialog.close();
});

test("visible remember context toggle restores custom limits and follows numeric edits", () => {
    const { node, widget } = setup();
    assert.equal(widget("remember_context").hidden, undefined);
    assert.equal(widget("remember_context").value, false);
    widget("remember_context").value = true;
    widget("remember_context").callback(true);
    assert.equal(widget("context_window").value, 8192);
    widget("context_window").value = 16384;
    widget("context_window").callback(16384);
    assert.equal(widget("remember_context").value, true);
    widget("remember_context").value = false;
    widget("remember_context").callback(false);
    assert.equal(widget("context_window").value, 0);
    widget("remember_context").value = true;
    widget("remember_context").callback(true);
    assert.equal(widget("context_window").value, 16384);
    widget("context_window").value = 0;
    widget("context_window").callback(0);
    assert.equal(widget("remember_context").value, false);
    node.onRemoved();
});

test("old positive context workflows infer enabled memory while preserving saved controls", () => {
    const { node, widget } = setup();
    widget("context_window").value = 1024;
    widget("temperature").value = 0.25;
    widget("max_output_tokens").value = 4096;
    node.onConfigure();
    assert.equal(widget("remember_context").value, true);
    assert.equal(widget("context_window").value, 1024);
    assert.equal(widget("temperature").value, 0.25);
    assert.equal(widget("max_output_tokens").value, 4096);
});

test("provider generation controls are independent and first hosted use keeps sampling defaults", () => {
    const { node, widget } = setup();
    widget("temperature").value = 0.4;
    widget("max_output_tokens").value = 3072;
    widget("context_window").value = 16384;
    widget("context_window").callback(16384);
    widget("seed").value = 42;
    widget("json_output").value = true;
    widget("thinking").value = "enabled";
    widget("reasoning_effort").value = "high";
    widget("provider").value = "ComfyUI Local";
    widget("provider").callback("ComfyUI Local");
    assert.equal(widget("temperature").value, 0.7);
    assert.equal(widget("top_p").value, 0.95);
    assert.equal(widget("top_k").value, 40);
    assert.equal(widget("min_p").value, 0.05);
    assert.equal(widget("repetition_penalty").value, 1.1);
    assert.equal(widget("thinking").value, "disabled");
    assert.equal(widget("reasoning_effort").value, "auto");
    assert.equal(widget("remember_context").value, false);
    widget("temperature").value = 0.6;
    widget("max_output_tokens").value = 2048;
    widget("reasoning_effort").value = "low";
    widget("remember_context").value = true;
    widget("remember_context").callback(true);
    widget("provider").value = "Groq";
    widget("provider").callback("Groq");
    for (const name of ["temperature", "top_p", "top_k", "min_p", "repetition_penalty"]) assert.equal(widget(name).value, -1);
    assert.equal(widget("max_output_tokens").value, 1024);
    assert.equal(widget("context_window").value, 0);
    assert.equal(widget("thinking").value, "auto");
    assert.equal(widget("reasoning_effort").value, "auto");
    assert.equal(widget("seed").value, 42);
    assert.equal(widget("json_output").value, true);
    widget("provider").value = "OpenRouter";
    widget("provider").callback("OpenRouter");
    assert.equal(widget("temperature").value, 0.4);
    assert.equal(widget("max_output_tokens").value, 3072);
    assert.equal(widget("thinking").value, "enabled");
    assert.equal(widget("reasoning_effort").value, "high");
    assert.equal(widget("context_window").value, 16384);
    assert.equal(widget("remember_context").value, true);
    widget("provider").value = "ComfyUI Local";
    widget("provider").callback("ComfyUI Local");
    assert.equal(widget("temperature").value, 0.6);
    assert.equal(widget("max_output_tokens").value, 2048);
    assert.equal(widget("thinking").value, "disabled");
    assert.equal(widget("reasoning_effort").value, "low");
    assert.equal(widget("context_window").value, 8192);
    assert.equal(widget("remember_context").value, true);
    node.onRemoved();
});

test("disabled context remembers each provider's last custom limit without persisted extra state", () => {
    const { node, widget } = setup();
    widget("context_window").value = 16384;
    widget("context_window").callback(16384);
    widget("remember_context").value = false;
    widget("remember_context").callback(false);
    widget("provider").value = "Groq";
    widget("provider").callback("Groq");
    widget("context_window").value = 1024;
    widget("context_window").callback(1024);
    widget("remember_context").value = false;
    widget("remember_context").callback(false);
    widget("provider").value = "OpenRouter";
    widget("provider").callback("OpenRouter");
    assert.equal(widget("remember_context").value, false);
    assert.equal(widget("context_window").value, 0);
    widget("remember_context").value = true;
    widget("remember_context").callback(true);
    assert.equal(widget("context_window").value, 16384);
    widget("provider").value = "Groq";
    widget("provider").callback("Groq");
    widget("remember_context").value = true;
    widget("remember_context").callback(true);
    assert.equal(widget("context_window").value, 1024);
    assert.equal(JSON.stringify(node.properties ?? {}), "{}");
    node.onRemoved();
});
