import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const extensions = [];
const requests = [];
const context = vm.createContext({
    AbortController,
    location: { protocol: "http:", hostname: "127.0.0.1" },
    app: { registerExtension: (extension) => extensions.push(extension) },
    api: { fetchApi: async (path, options) => {
        requests.push({ path, options });
        return { ok: true, status: 200, json: async () => ({ reference: "goat-session:dummy-reference" }) };
    } },
});
const source = await readFile(new URL("../web/security.js", import.meta.url), "utf8");
const module = new vm.SourceTextModule(source, { context });
await module.link((specifier) => new vm.SourceTextModule(
    specifier.endsWith("app.js") ? "export const app = globalThis.app" : "export const api = globalThis.api", { context },
));
await module.evaluate();

for (const name of ["GOATTextGeneratorSettings", "UniversalLLM"]) {
    class Node {
        constructor() {
            this.widgets = [
                { name: "provider", value: "OpenRouter" },
                { name: "api_key", value: "legacy-imported-secret" },
                { name: "base_url", value: "" },
                { name: "model", value: "test/model" },
            ];
        }
    }
    extensions[0].beforeRegisterNodeDef(Node, { name });
    const node = new Node();
    node.onNodeCreated();
    const key = node.widgets[1];
    assert.equal(module.namespace.keyValue(key), "legacy-imported-secret");
    assert.equal(key.value, "");
    assert.equal(JSON.stringify(node).includes("legacy-imported-secret"), false);
    assert.equal(await key.serializeValue(), "goat-session:dummy-reference");
    const apiWorkflow = Object.fromEntries(await Promise.all(node.widgets.map(async (widget) => [widget.name, widget.serializeValue ? await widget.serializeValue() : widget.value])));
    assert.equal(JSON.stringify(apiWorkflow).includes("legacy-imported-secret"), false);
    assert.equal(apiWorkflow.api_key, "goat-session:dummy-reference");
    key.value = "fresh-key";
    assert.equal(module.namespace.keyValue(key), "fresh-key");
    assert.equal(key.value, "");
    key.value = "";
    assert.equal(await key.serializeValue(), "");
}
assert.equal(requests.every(({ path }) => path === "/goat/credentials"), true);
assert.equal(requests.every(({ options }) => options.headers["X-GOAT-Request"] === "1"), true);

const settings = new vm.SourceTextModule(await readFile(new URL("../web/settings.js", import.meta.url), "utf8"), { context });
await settings.link((specifier) => specifier === "./security.js" ? module : new vm.SourceTextModule(
    specifier === "./context.js" ? "export function refreshContextControls() {}" : "export const app = globalThis.app", { context },
));
await settings.evaluate();
class SettingsNode {
    constructor() {
        this.widgets = Object.entries({ provider: "OpenRouter", model: "test", api_key: "first-provider-key", api_key_env: "", base_url: "", context_window: 0, remember_context: false }).map(([name, value]) => ({ name, value, computeSize: () => [100, 20] }));
        this.size = [560, 400];
    }
    addWidget(type, name, value, callback) {
        const widget = { type, name, value, callback, options: {}, computeSize: () => [100, 20] };
        this.widgets.push(widget);
        return widget;
    }
    computeSize() { return [560, 400]; }
    setSize(size) { this.size = size; }
    setDirtyCanvas() {}
}
for (const extension of extensions) extension.beforeRegisterNodeDef(SettingsNode, { name: "GOATTextGeneratorSettings" });
const node = new SettingsNode();
node.onNodeCreated();
const provider = node.widgets.find((widget) => widget.name === "provider");
const key = node.widgets.find((widget) => widget.name === "api_key");
provider.value = "Ollama";
provider.callback();
assert.equal(module.namespace.keyValue(key), "");
key.value = "local-provider-key";
provider.value = "OpenRouter";
provider.callback();
assert.equal(module.namespace.keyValue(key), "first-provider-key");
assert.equal(JSON.stringify(node).includes("first-provider-key"), false);
await new Promise((resolve) => setImmediate(resolve));
for (const { options } of requests.filter(({ path }) => path === "/goat/models")) {
    const body = JSON.parse(options.body);
    assert.equal(["first-provider-key", "local-provider-key"].includes(body.api_key), false);
}
console.log("Key-free exports, execution references, and provider switching passed.");
context.location.hostname = "remote.example";
const requestCount = requests.length;
await assert.rejects(module.namespace.keyReference({ provider: "OpenRouter", api_key: "must-not-cross-http" }), /HTTPS/);
assert.equal(requests.length, requestCount);
console.log("Remote HTTP is blocked before credentials leave the browser.");
