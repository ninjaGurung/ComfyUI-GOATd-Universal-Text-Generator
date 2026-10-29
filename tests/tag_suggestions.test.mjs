import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";

function setup(value = "", active = true) {
    const previews = [];
    const document = { activeElement: null, defaultView: { innerWidth: 1024, innerHeight: 768, addEventListener() {}, removeEventListener() {} } };
    class Element {
        constructor(tag) {
            this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.style = {}; this.listeners = new Map();
            this.ownerDocument = document; this.parentElement = null; this._text = "";
            this.clientWidth = 500; this.clientHeight = 240; this.clientLeft = this.clientTop = 1;
            this.offsetLeft = this.offsetTop = this.scrollLeft = this.scrollTop = 0; this.offsetHeight = 40; this.offsetWidth = 176;
        }
        get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
        set textContent(value) { this.replaceChildren(); this._text = String(value); }
        get isConnected() { return this === document.body || Boolean(this.parentElement?.isConnected); }
        append(...children) { children.forEach((child) => this.appendChild(child)); }
        appendChild(child) { child.remove(); child.parentElement = this; this.children.push(child); return child; }
        replaceChildren(...children) { this.children.forEach((child) => { child.parentElement = null; }); this.children = []; this._text = ""; this.append(...children); }
        remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((child) => child !== this); this.parentElement = null; }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return this.attributes[name] ?? null; }
        removeAttribute(name) { delete this.attributes[name]; }
        addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(listener); }
        removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
        dispatchEvent(event) { for (const listener of this.listeners.get(event.type) ?? []) listener(event); this[`on${event.type}`]?.(event); }
        focus() { document.activeElement = this; }
        contains(element) { return element === this || this.children.some((child) => child.contains(element)); }
        getBoundingClientRect() { return { left: 100, top: 100, right: 600, bottom: 340, width: 500, height: 240 }; }
        setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
        setRangeText(text, start, end) { this.value = this.value.slice(0, start) + text + this.value.slice(end); this.setSelectionRange(start + text.length, start + text.length); }
    }
    document.createElement = (tag) => new Element(tag);
    document.createTextNode = (text) => { const node = new Element("#text"); node.textContent = text; return node; };
    document.body = new Element("body");
    document.documentElement = document.body;
    document.documentElement.clientWidth = 1024; document.documentElement.clientHeight = 768;
    document.addEventListener = () => {};
    document.removeEventListener = () => {};
    document.execCommand = () => false;
    const textarea = new Element("textarea");
    const parent = new Element("div"); document.body.appendChild(parent); parent.appendChild(textarea);
    textarea.value = value; textarea.setSelectionRange(value.length, value.length);
    if (active) textarea.focus();
    const connected = new Set(["images.image1", "images.image2", "videos.video1", "audios.audio1"]);
    const node = {
        inputs: [...connected].map((name) => ({ name })),
        isInputConnected(index) { return connected.has(this.inputs[index].name); },
    };
    const descriptors = new Map();
    let disconnected = false;
    const context = {
        Event, window: document.defaultView, document,
        getComputedStyle: () => ({ fontSize: "14px", lineHeight: "26px", paddingTop: "8px", paddingRight: "8px" }),
        ResizeObserver: class { observe() {} disconnect() { disconnected = true; } },
        resolveMediaPreview: (_node, tag) => descriptors.get(tag) ?? { kind: tag.match(/^@(image|video|audio)/)[1], title: tag, url: `/view?filename=${tag.slice(1)}` },
        createMediaPreview: (_doc, descriptor, size) => {
            const element = document.createElement("span");
            element.setAttribute("data-preview", descriptor.kind);
            const preview = { element, descriptor, size, disposed: false, dispose() { this.disposed = true; } };
            previews.push(preview);
            return preview;
        },
    };
    const source = readFileSync(new URL("../web/tag_suggestions.js", import.meta.url), "utf8")
        .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");
    vm.runInNewContext(source, context);
    const widget = { element: textarea };
    const sync = () => context.mountTagSuggestions(node, widget);
    sync();
    const all = (element = document.body) => [element, ...element.children.flatMap((child) => all(child))];
    const role = (name) => all().filter((element) => element.getAttribute("role") === name);
    const fire = (element, type, options = {}) => element.dispatchEvent({ type, button: 0, preventDefault() {}, stopPropagation() {}, ...options });
    const type = (text) => { textarea.value = text; textarea.setSelectionRange(text.length, text.length); textarea.focus(); fire(textarea, "input"); };
    return { document, textarea, widget, node, connected, descriptors, previews, sync, role, fire, type,
        remove: () => context.removeTagSuggestions(widget), observerDisconnected: () => disconnected };
}

test("autocomplete filters image, video and audio prefixes and renders matching thumbnails", () => {
    const ui = setup();
    for (const [prefix, kind, tags] of [["@im", "image", ["@image1", "@image2"]], ["@vid", "video", ["@video1"]], ["@aud", "audio", ["@audio1"]]]) {
        ui.type(prefix);
        assert.deepEqual(ui.role("option").map((option) => option.textContent), tags);
        assert.equal(ui.textarea.getAttribute("aria-expanded"), "true");
        assert.ok(ui.role("option").every((option) => option.children.some((child) => child.getAttribute("data-preview") === kind)));
    }
    assert.ok(ui.previews.slice(0, 3).every((preview) => preview.disposed));
    ui.remove();
});

test("hover and keyboard focus show tag previews and release them on exit", () => {
    const ui = setup("Describe @image1", false);
    const label = ui.role("button")[0];
    for (const [enter, leave] of [["pointerenter", "pointerleave"], ["focus", "blur"]]) {
        ui.fire(label, enter);
        assert.equal(ui.role("tooltip").filter((element) => element.style.display !== "none").length, 1);
        assert.equal(ui.previews.at(-1).descriptor.kind, "image");
        ui.fire(label, leave);
        assert.equal(ui.previews.at(-1).disposed, true);
        assert.equal(ui.role("tooltip").filter((element) => element.style.display !== "none").length, 0);
    }
    ui.remove();
});

test("keyboard selection inserts a reference and closes and disposes suggestions", () => {
    const ui = setup("Use @im");
    ui.fire(ui.textarea, "keydown", { key: "ArrowDown" });
    assert.equal(ui.role("option")[1].getAttribute("aria-selected"), "true");
    ui.fire(ui.textarea, "keydown", { key: "Enter" });
    assert.equal(ui.textarea.value, "Use @image2");
    assert.equal(ui.textarea.getAttribute("aria-expanded"), "false");
    assert.ok(ui.previews.every((preview) => preview.disposed));
    ui.remove();
});

test("clicking an existing tag replaces only that occurrence and remove still works", () => {
    const ui = setup("Use @image1 and @image1.");
    ui.fire(ui.role("button")[0], "pointerdown");
    const video = ui.role("option").find((option) => option.textContent === "@video1");
    ui.fire(video, "pointerdown");
    assert.equal(ui.textarea.value, "Use @video1 and @image1.");
    const remove = ui.role("button")[0].parentElement.children.find((child) => child.tagName === "BUTTON");
    ui.fire(remove, "click");
    assert.equal(ui.textarea.value, "Use  and @image1.");
    ui.remove();
});

test("open suggestions refresh changed media and connections without retaining old previews", () => {
    const ui = setup("@im");
    const initial = [...ui.previews];
    ui.descriptors.set("@image1", { kind: "image", title: "new.png", url: "/view?filename=new.png" });
    ui.sync();
    assert.ok(initial.every((preview) => preview.disposed));
    assert.ok(ui.previews.some((preview) => !preview.disposed && preview.descriptor.title === "new.png"));
    ui.connected.delete("images.image2");
    ui.sync();
    assert.deepEqual(ui.role("option").map((option) => option.textContent), ["@image1"]);
    ui.remove();
});

test("hover preview follows media changes and disappears when its tag leaves the textarea", () => {
    const ui = setup("Describe @video1", false);
    const label = ui.role("button")[0];
    ui.fire(label, "pointerenter");
    const initial = ui.previews.at(-1);
    ui.descriptors.set("@video1", { kind: "video", title: "replacement.mp4", url: "/view?filename=replacement.mp4" });
    ui.sync();
    assert.equal(initial.disposed, true);
    assert.equal(ui.previews.at(-1).descriptor.title, "replacement.mp4");
    label.getBoundingClientRect = () => ({ left: 100, top: 400, right: 180, bottom: 426 });
    ui.sync();
    assert.equal(ui.previews.at(-1).disposed, true);
    assert.equal(label.getAttribute("aria-describedby"), null);
    assert.equal(ui.role("tooltip")[0].style.display, "none");
    ui.remove();
});

test("preview positioned above a tag clears its raised remove control at different zoom levels", () => {
    for (const scale of [0.5, 1, 2, 4]) {
        const ui = setup("Describe @image1", false);
        const label = ui.role("button")[0];
        const remove = label.parentElement.children.find((child) => child.tagName === "BUTTON");
        ui.textarea.getBoundingClientRect = () => ({ left: 100, top: 100, right: 600, bottom: 760 });
        label.getBoundingClientRect = () => ({ left: 100, top: 700, right: 180, bottom: 740 });
        const removeTop = 700 - 7 * scale;
        remove.getBoundingClientRect = () => ({ left: 176, top: removeTop, right: 176 + 9 * scale, bottom: removeTop + 9 * scale });
        ui.fire(label, "pointerenter");
        const tooltip = ui.role("tooltip")[0];
        const bottom = Number.parseFloat(tooltip.style.top) + tooltip.offsetHeight;
        assert.ok(bottom <= removeTop - 8, `preview overlaps remove control at ${scale}× zoom`);
        ui.remove();
    }
});

test("composition suppresses the menu until committed and remount does not duplicate it", () => {
    const ui = setup("@im");
    ui.fire(ui.textarea, "compositionstart");
    assert.equal(ui.textarea.getAttribute("aria-expanded"), "false");
    ui.fire(ui.textarea, "compositionend");
    assert.equal(ui.textarea.getAttribute("aria-expanded"), "true");
    const count = ui.previews.length;
    ui.sync();
    ui.sync();
    assert.equal(ui.role("listbox").length, 1);
    assert.equal(ui.previews.length, count);
    ui.remove();
});

test("escape and unmount dispose media and restore textarea attributes and listeners", () => {
    const ui = setup("@vid");
    ui.fire(ui.textarea, "keydown", { key: "Escape" });
    assert.equal(ui.textarea.getAttribute("aria-expanded"), "false");
    assert.ok(ui.previews.every((preview) => preview.disposed));
    ui.type("@image1");
    ui.fire(ui.role("button")[0], "pointerenter");
    ui.remove();
    assert.ok(ui.previews.every((preview) => preview.disposed));
    assert.equal(ui.observerDisconnected(), true);
    assert.equal(ui.textarea.getAttribute("aria-autocomplete"), null);
    assert.ok([...ui.textarea.listeners.values()].every((listeners) => listeners.size === 0));
    assert.equal(ui.role("listbox").length, 0);
    assert.equal(ui.role("tooltip").length, 0);
});
