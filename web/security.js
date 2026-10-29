import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

let accessToken = "";
let tokenPrompt;
const sessionValues = new WeakMap();

export function keyValue(widget) {
    return sessionValues.get(widget) ?? widget?.value ?? "";
}

function askToken() {
    if (tokenPrompt) return tokenPrompt;
    tokenPrompt = new Promise((resolve) => {
        const dialog = document.createElement("dialog");
        const form = document.createElement("form");
        form.method = "dialog";
        const label = document.createElement("label");
        label.textContent = "GOAT server access token";
        const input = document.createElement("input");
        input.type = "password";
        input.autocomplete = "off";
        input.style.cssText = "display:block;margin:12px 0";
        label.append(input);
        const submit = document.createElement("button");
        submit.textContent = "Connect";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.textContent = "Cancel";
        cancel.onclick = () => dialog.close();
        form.append(label, submit, cancel);
        form.onsubmit = () => { accessToken = input.value; };
        dialog.append(form);
        dialog.addEventListener("close", () => {
            dialog.remove();
            resolve(accessToken);
        }, { once: true });
        document.body.append(dialog);
        dialog.showModal();
        input.focus();
    }).finally(() => { tokenPrompt = null; });
    return tokenPrompt;
}

export async function goatFetch(path, options = {}) {
    if (location.protocol !== "https:" && !["localhost", "[::1]"].includes(location.hostname) && !/^127(?:\.\d{1,3}){3}$/.test(location.hostname)) {
        throw new Error("Remote GOAT access requires HTTPS. Use a TLS proxy or a local SSH tunnel.");
    }
    const send = () => api.fetchApi(path, {
        ...options,
        headers: { ...options.headers, "X-GOAT-Request": "1", ...(accessToken ? { "X-GOAT-Token": accessToken } : {}) },
    });
    let response = await send();
    if (response.status === 401) {
        accessToken = "";
        if (await askToken()) response = await send();
    }
    return response;
}

export async function keyReference(values) {
    const key = String(values.api_key ?? "").trim();
    if (!key || key.startsWith("goat-session:")) return key;
    const response = await goatFetch("/goat/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: values.provider, base_url: values.base_url ?? "", api_key: key }),
    });
    if (!response.ok) throw new Error("Could not register the session-only API key. Check GOAT access and the endpoint URL.");
    return (await response.json()).reference;
}

app.registerExtension({
    name: "ninjaGurung.GOATCredentials",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!["GOATTextGeneratorSettings", "UniversalLLM"].includes(nodeData.name)) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            const widget = this.widgets.find((item) => item.name === "api_key");
            const initial = widget.value;
            Object.defineProperty(widget, "value", {
                configurable: true,
                enumerable: true,
                get: () => "",
                set: (value) => sessionValues.set(widget, String(value ?? "")),
            });
            widget.value = initial;
            widget.serializeValue = () => keyReference({
                ...Object.fromEntries(this.widgets.map((item) => [item.name, item.value])),
                api_key: keyValue(widget),
            });
            return result;
        };
    },
});
