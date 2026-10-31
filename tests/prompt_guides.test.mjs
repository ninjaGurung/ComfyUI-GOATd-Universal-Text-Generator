import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";

class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.style = { setProperty(name, value) { this[name] = value; }, removeProperty(name) { delete this[name]; } }; this.listeners = {}; this.captureListeners = {}; this.value = ""; this.offsetWidth = 300; this.offsetHeight = 200; }
    append(...children) { children.forEach((child) => { child.parentElement = this; this.children.push(child); }); }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    setAttribute(name, value) { this[name] = value; }
    getAttribute(name) { return this[name] ?? null; }
    contains(node) { return this === node || this.children.some((child) => child.contains(node)); }
    addEventListener(name, callback, capture) { const listeners = capture === true ? this.captureListeners : this.listeners; (listeners[name] ??= []).push(callback); }
    matches(selector) { return selector === ".litemenu-entry" && this.className?.split(" ").includes("litemenu-entry"); }
    click() { this.dispatch("click"); }
    scrollIntoView() {}
    dispatch(type, options = {}) {
        const event = { type, target: this, clientX: 200, clientY: 500, ...options,
            stopPropagation() { this.stopped = true; }, preventDefault() { this.prevented = true; } };
        const visit = (element) => {
            for (const callback of element.listeners[type] ?? []) callback(event);
            if (!event.stopped && element.parentElement) visit(element.parentElement);
        };
        const path = [];
        for (let element = this; element; element = element.parentElement) path.unshift(element);
        for (const element of path) {
            for (const callback of element.captureListeners[type] ?? []) callback(event);
            if (event.stopped) break;
        }
        if (!event.stopped) visit(this);
        // Native button keyboard activation synthesizes a click.
        if (this.tag === "button" && !event.prevented &&
            (type === "keydown" && event.key === "Enter" || type === "keyup" && event.key === " ")) this.dispatch("click");
        return event;
    }
    get offsetHeight() { return Math.min(this._height, parseFloat(this.style.maxHeight) || Infinity); }
    set offsetHeight(value) { this._height = value; }
    getBoundingClientRect() {
        if (this.rect) return this.rect;
        const scale = Number(this.style.transform?.match(/scale\(([^)]+)\)/)?.[1]) || this.scale || 1;
        const left = parseFloat(this.style.left) || 0;
        const top = parseFloat(this.style.top) || 0;
        const width = Math.min(this.offsetWidth, parseFloat(this.style.maxWidth) || Infinity) * scale;
        const height = Math.min(this.offsetHeight, parseFloat(this.style.maxHeight) || Infinity) * scale;
        return { left, top, right: left + width, bottom: top + height, width, height };
    }
    showModal() { this.open = true; }
    close() { this.open = false; for (const listener of this.listeners.close ?? []) listener(); }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((child) => child !== this); }
    focus() { this.focused = true; }
}
function setup({ roles = true } = {}) {
    const document = { createElement: (tag) => new Element(tag), createElementNS: (ns, tag) => new Element(tag), head: new Element("head"), body: new Element("body"), documentElement: { clientWidth: 1200, clientHeight: 800 } };
    let latestMenu, latestInstance;
    const instances = [];
    class NativeMenu {
        constructor(values, options = {}) {
            this.values = values;
            this.options = options;
            this.root = new Element("menu");
            this.root.style.left = `${options.event?.clientX ?? 100}px`;
            this.root.style.top = `${(options.event?.clientY ?? 100) - 10}px`;
            this.root.scale = options.scale ?? 1;
            this.rows = [];
            if (options.className === "dark" && values.length > 4) this.root.append(new Element("input"));
            if (options.parentMenu) options.parentMenu.current_submenu = this;
            values.forEach((value) => this.addItem(value?.content ?? "", value, options));
            latestMenu = values;
            latestInstance = this;
            instances.push(this);
        }
        addItem(name, value, options) {
            const row = new Element("row");
            row.className = "litemenu-entry submenu";
            row.value = value;
            if (roles && value != null) row.setAttribute("role", "menuitem");
            row.textContent = name;
            const click = (event) => {
                if (value?.submenu) new this.constructor(value.submenu.options, { parentMenu: this, event, autoopen: options.autoopen });
                else { value?.callback?.(); this.getTopMenu().close(); }
            };
            row.addEventListener("click", click);
            row.addEventListener("pointerenter", (event) => { if (value?.submenu && options.autoopen) click(event); });
            this.rows.push(row);
            this.root.append(row);
            return row;
        }
        getTopMenu() { return this.options.parentMenu?.getTopMenu() ?? this; }
        containsNode(node) { return this.root.contains(node) || Boolean(this.current_submenu?.containsNode(node)); }
        close(event) { this.closeEvent = event; this.closed = true; this.root.remove(); this.current_submenu?.close(); }
    }
    const calls = [];
    const initial = { guides: [{ value: "Custom:one", name: "Film", folder: "f1" }], folders: [{ id: "f1", name: "My guides" }], formats: [".md", ".docx", ".pdf"] };
    let response = initial;
    let failure;
    const timers = new Map();
    let timerId = 0;
    const context = {
        document,
        requestAnimationFrame(callback) { callback(); },
        setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
        clearTimeout(id) { timers.delete(id); },
        app: { canvas: { graph_mouse: [0, 0] } },
        api: { fetchApi: async (url, options) => { calls.push({ url, options }); return { ok: !failure, json: async () => failure ? { error: failure } : response }; } },
        // ComfyUI wraps construction and returns a raw native instance, ignoring newTarget.
        LiteGraph: { ContextMenu: new Proxy(NativeMenu, { construct(target, args) { return new target(...args); } }), context_menu_scaling: true },
        FormData: class { constructor() { this.values = {}; } append(name, value) { this.values[name] = value; } },
    };
    const source = readFileSync(new URL("../web/prompt_guides.js", import.meta.url), "utf8")
        .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");
    context.goatFetch = (...args) => context.api.fetchApi(...args);
    vm.runInNewContext(source, context);
    const node = { pos: [0, 0], size: [560, 800], setDirtyCanvas() {} };
    const makeWidget = () => ({ value: "None", options: { values: ["None", "MiniMax H3 - Pika", "Seedance 2.5", "Fibo 2"] }, setValue(value) { this.value = value; }, draw() { return this.value; } });
    const widget = makeWidget();
    context.setupGuideMenu(widget, node);
    const open = async (which = widget, options = {}) => {
        const pointer = { eDown: { button: 0, canvasX: 200, ...options.event } };
        which.onPointerDown(pointer, node, { ds: { scale: options.scale ?? 1 } });
        await pointer.onClick();
        return latestMenu;
    };
    return { context, document, node, widget, makeWidget, open, calls, initial, instances, menu: () => latestInstance,
        runTimers() { const pending = [...timers.values()]; timers.clear(); pending.forEach((callback) => callback()); },
        response(value) { response = value; }, fail(value) { failure = value; } };
}
function formFields(document) {
    const dialog = document.body.children.at(-1);
    const form = dialog.children[0];
    const fields = Object.fromEntries(form.children.filter((child) => child.tag === "label").map((label) => [label.textContent, label.children[0]]));
    return { dialog, form, fields, status: form.children.find((child) => child.role === "status") };
}
const submit = (form) => form.onsubmit({ preventDefault() {} });

test("custom icons are compact and translucent with vertical breathing room", async () => {
    const s = setup();
    await s.open();
    const row = s.menu().rows[2];
    const button = row.children[1].children[0];
    assert.match(button.style.cssText, /width:24px;height:22px/);
    assert.match(button.style.cssText, /margin:2px 0/);
    assert.match(button.style.cssText, /background:transparent/);
    button.dispatch("pointerenter");
    assert.equal(button.style.background, "rgb(255 255 255 / 0.1)");
    button.dispatch("pointerleave");
    assert.equal(button.style.background, "transparent");
    assert.equal(row.style.background, undefined);
    assert.match(row.className, /goat-custom-guide-folder/);
    assert.match(s.document.head.children[0].textContent, /goat-custom-guide-folder\.has_submenu::after\{top:50%;transform:translateY\(-50%\)/);
    assert.match(s.document.head.children[0].textContent, /translateY\(-50%\) rotate\(45deg\);rotate:0deg/);
    assert.match(s.document.head.children[0].textContent, /border-width:1.5px 1.5px 0 0/);
    assert.equal(s.menu().rows[0].className, "litemenu-entry submenu");
    row.dispatch("pointerenter");
    assert.equal(s.document.head.children.length, 1);
});

test("native menu rows without accessibility roles still react to hover and arrow keys", async () => {
    const s = setup({ roles: false });
    await s.open();
    const menu = s.menu();
    const filter = menu.root.children[0];
    menu.rows[2].dispatch("pointerenter");
    assert.equal(menu.rows[2]["data-goat-selected"], "true");
    assert.equal(menu.rows[0]["data-goat-selected"], "false");
    filter.dispatch("keydown", { key: "ArrowDown" });
    assert.equal(menu.rows[3]["data-goat-selected"], "true");
    menu.rows[4].dispatch("pointerenter");
    filter.dispatch("keydown", { key: "ArrowUp" });
    assert.equal(menu.rows[3]["data-goat-selected"], "true");
});

test("hover and arrow keys share one selection and retain the last hovered row", async () => {
    const s = setup();
    await s.open();
    const menu = s.menu();
    const nativeFilter = menu.root.children[0];
    assert.equal(nativeFilter.tag, "input");
    nativeFilter.dispatch("keydown", { key: "ArrowUp" });
    assert.equal(menu.rows[6]["data-goat-selected"], "true");
    nativeFilter.dispatch("keydown", { key: "ArrowDown" });
    assert.equal(menu.rows[0]["data-goat-selected"], "true");
    assert.equal(nativeFilter["data-goat-selected"], undefined);
    const filter = new Element("input");
    menu.root.append(filter);
    let nativeKeys = 0;
    filter.addEventListener("keydown", () => nativeKeys++);
    menu.rows[2].dispatch("pointerenter");
    assert.equal(menu.rows[0]["data-goat-selected"], "false");
    assert.equal(menu.rows[2]["data-goat-selected"], "true");
    menu.rows[2].dispatch("pointerleave", { relatedTarget: filter });
    s.runTimers();
    filter.dispatch("keydown", { key: "ArrowDown" });
    assert.equal(menu.rows[3]["data-goat-selected"], "true");
    assert.equal(menu.rows[2]["data-goat-selected"], "false");
    assert.equal(nativeKeys, 0);
    filter.dispatch("keydown", { key: "ArrowUp" });
    assert.equal(menu.rows[2]["data-goat-selected"], "true");
    menu.rows[4].dispatch("pointerenter");
    filter.dispatch("keydown", { key: "ArrowDown" });
    assert.equal(menu.rows[6]["data-goat-selected"], "true");
    menu.rows[4].style.display = "none";
    menu.root.dispatch("input");
    filter.dispatch("keydown", { key: "ArrowUp" });
    assert.equal(menu.rows[3]["data-goat-selected"], "true");
    assert.equal(menu.rows.filter((row) => row["data-goat-selected"] === "true").length, 1);
    assert.equal(menu.rows[2].style.display, "flex");
});

test("keyboard navigation enters submenus, returns to the parent, and selects the hovered guide", async () => {
    const s = setup();
    await s.open();
    const parent = s.menu();
    parent.rows[2].dispatch("pointerenter");
    parent.root.dispatch("keydown", { key: "ArrowRight" });
    const child = parent.current_submenu;
    assert.equal(child.root.focused, true);
    child.root.dispatch("keydown", { key: "ArrowDown" });
    assert.equal(child.rows[0]["data-goat-selected"], "true");
    child.root.dispatch("keydown", { key: "ArrowLeft" });
    assert.equal(parent.current_submenu, undefined);
    assert.equal(parent.closed, undefined);
    assert.equal(parent.root.focused, true);
    parent.rows[2].dispatch("pointerenter");
    parent.current_submenu.rows[0].dispatch("pointerenter");
    parent.current_submenu.root.dispatch("keydown", { key: "Enter" });
    assert.equal(s.widget.value, "Custom:one");
});

test("submenus retract after leaving the folder and submenu but allow crossing between them", async () => {
    const s = setup();
    await s.open();
    const parent = s.menu();
    const row = parent.rows[2];
    row.dispatch("pointerenter");
    let child = parent.current_submenu;
    row.dispatch("pointerleave", { relatedTarget: child.root });
    parent.root.dispatch("pointerleave", { relatedTarget: child.root });
    s.runTimers();
    assert.equal(child.closed, undefined);
    child.root.dispatch("pointerleave", { relatedTarget: row });
    s.runTimers();
    assert.equal(child.closed, undefined);
    row.dispatch("pointerleave", { relatedTarget: null });
    child.root.dispatch("pointerenter");
    s.runTimers();
    assert.equal(child.closed, undefined);
    child.root.dispatch("pointerleave", { relatedTarget: new Element("outside") });
    s.runTimers();
    assert.equal(child.closed, true);
    assert.equal(child.closeEvent.type, "pointerleave");
    assert.equal(parent.current_submenu, undefined);
    assert.equal(parent.closed, undefined);
    assert.equal(row["aria-expanded"], "false");
    row.dispatch("pointerenter");
    child = parent.current_submenu;
    row.dispatch("pointerleave", { relatedTarget: parent.rows[3] });
    s.runTimers();
    assert.equal(child.closed, true);
});

test("leaving an old folder cannot retract a newly opened folder and focused submenu controls remain usable", async () => {
    const s = setup();
    await s.open();
    const parent = s.menu();
    parent.rows[0].dispatch("pointerenter");
    parent.rows[0].dispatch("pointerleave", { relatedTarget: null });
    parent.rows[2].dispatch("pointerenter");
    const child = parent.current_submenu;
    s.runTimers();
    assert.equal(child.closed, undefined);
    s.document.activeElement = child.rows[0].children[1].children[0];
    child.root.dispatch("pointerleave", { relatedTarget: null });
    s.runTimers();
    assert.equal(child.closed, undefined);
});

test("menu preserves bundled groups and offers custom folder rename without serializing actions", async () => {
    const s = setup();
    const menu = await s.open();
    assert.equal(menu[0].content, "📁 MiniMax H3");
    assert.equal(menu[0].submenu.options[0].content, "Pika");
    assert.equal(menu[0].submenu.options.some((item) => item?.content === "Rename folder…"), false);
    assert.equal(menu[2].content, "📁 My guides");
    assert.equal(menu[2].submenu.options.some((item) => item?.content === "Rename folder…"), false);
    assert.equal(menu[2].guideActions.length, 2);
    assert.equal(menu[0].guideActions, undefined);
    assert.equal(menu.at(-1).content, "+ Add a prompt guide");
    assert.equal(s.widget.options.values.includes("+ Add a prompt guide"), false);
    menu[2].submenu.options[0].callback();
    assert.equal(s.widget.value, "Custom:one");
    assert.equal(s.widget.options.getOptionLabel(s.widget.value), "Film");
    assert.equal(s.widget.value, "Custom:one");
});

test("import sends local multipart file/name/folder and updates every live widget", async () => {
    const s = setup();
    const menu = await s.open();
    const second = s.makeWidget();
    s.context.setupGuideMenu(second, s.node);
    menu.at(-1).callback();
    const { form, fields, dialog } = formFields(s.document);
    fields["Guide file"].files = [{ name: "Cinema.md" }];
    fields["Guide file"].onchange();
    assert.equal(fields["Guide name"].value, "Cinema");
    fields.Folder.value = "__new__";
    fields.Folder.onchange();
    assert.equal(fields["New folder name"].required, true);
    fields["New folder name"].value = "Movies";
    s.response({ ...s.initial, guides: [...s.initial.guides, { value: "Custom:two", name: "Cinema", folder: "f2" }], folders: [...s.initial.folders, { id: "f2", name: "Movies" }], value: "Custom:two" });
    await submit(form);
    const post = s.calls.find((call) => call.options?.method === "POST");
    assert.equal(post.options.body.values.new_folder, "Movies");
    assert.equal(post.options.body.values.folder, "");
    assert.equal(post.options.body.values.name, "Cinema");
    assert.equal(s.widget.value, "Custom:two");
    assert.ok(second.options.values.includes("Custom:two"));
    assert.equal(dialog.open, false);
});

test("folder rename preserves workflow ids and reports errors inside the open dialog", async () => {
    const s = setup();
    const menu = await s.open();
    s.widget.value = "Custom:one";
    s.menu().rows[2].children[1].children[0].dispatch("click");
    const { form, fields, status, dialog } = formFields(s.document);
    fields["Folder name"].value = "Renamed";
    s.fail("A folder already has that name.");
    await submit(form);
    assert.equal(dialog.open, true);
    assert.equal(status.textContent, "A folder already has that name.");
    assert.equal(s.widget.value, "Custom:one");
    s.fail("");
    s.response({ ...s.initial, folders: [{ id: "f1", name: "Renamed" }] });
    await submit(form);
    const patch = s.calls.find((call) => call.options?.method === "PATCH");
    assert.equal(patch.url, "/goat/prompt-guides/folders");
    assert.deepEqual(JSON.parse(patch.options.body), { id: "f1", name: "Renamed" });
    assert.equal(s.widget.value, "Custom:one");
    assert.equal((await s.open())[2].content, "📁 Renamed");
});

test("cancel and node removal close dialogs without changing the selected guide", async () => {
    const s = setup();
    const menu = await s.open();
    menu.at(-1).callback();
    let { form } = formFields(s.document);
    form.children.at(-1).children[0].onclick();
    assert.equal(s.document.body.children.length, 0);
    assert.equal(s.widget.value, "None");
    menu.at(-1).callback();
    s.context.removeGuideMenu(s.widget);
    assert.equal(s.document.body.children.length, 0);
});

test("custom names are escaped for LiteGraph menus and stay literal in form fields", async () => {
    const s = setup();
    s.response({ ...s.initial, guides: [{ value: "Custom:one", name: "<img src=x onerror=run()>", folder: "f1" }], folders: [{ id: "f1", name: "<b>Movies</b>" }] });
    const menu = await s.open();
    assert.equal(menu[2].content, "📁 &lt;b&gt;Movies&lt;/b&gt;");
    assert.equal(menu[2].submenu.options[0].content, "&lt;img src=x onerror=run()&gt;");
    s.menu().rows[2].children[1].children[0].dispatch("click");
    assert.equal(formFields(s.document).fields["Folder name"].value, "<b>Movies</b>");
});

test("cancel during import keeps selection while a late GET cannot erase the saved guide", async () => {
    const s = setup();
    const menu = await s.open();
    menu.at(-1).callback();
    const { dialog, form, fields } = formFields(s.document);
    fields["Guide file"].files = [{ name: "New.md" }];
    fields["Guide name"].value = "New";
    let resolveGet, resolvePost;
    s.context.api.fetchApi = async (url, options) => {
        const result = await new Promise((resolve) => {
            if (options?.method === "POST") resolvePost = resolve;
            else resolveGet = resolve;
        });
        return { ok: true, json: async () => result };
    };
    const second = s.makeWidget();
    s.context.setupGuideMenu(second, s.node);
    const saving = submit(form);
    dialog.close();
    await Promise.resolve();
    resolvePost({ ...s.initial, guides: [...s.initial.guides, { value: "Custom:two", name: "New", folder: "" }], value: "Custom:two" });
    await saving;
    resolveGet(s.initial);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(s.widget.value, "None");
    assert.ok(s.widget.options.values.includes("Custom:two"));
    assert.ok(second.options.values.includes("Custom:two"));
});


test("new folder name is displayed only for New folder and name limits match the backend", async () => {
    const s = setup();
    const menu = await s.open();
    menu.at(-1).callback();
    const { fields } = formFields(s.document);
    const name = fields["New folder name"];
    assert.equal(name.parentElement.style.display, "none");
    assert.equal(fields["Guide name"].maxLength, 120);
    assert.equal(name.maxLength, 120);
    fields.Folder.value = "__new__";
    fields.Folder.onchange();
    assert.equal(name.parentElement.style.display, "block");
    assert.equal(name.required, true);
    fields.Folder.value = "f1";
    fields.Folder.onchange();
    assert.equal(name.parentElement.style.display, "none");
    assert.equal(name.required, false);
    fields.Folder.value = "";
    fields.Folder.onchange();
    assert.equal(name.parentElement.style.display, "none");
});


test("native combos without draw render guide labels without writing their stable value", async () => {
    const s = setup();
    await s.open();
    const widget = s.makeWidget();
    delete widget.draw;
    let drawn;
    widget.drawWidget = function (ctx, options) { drawn = { value: this.value, y: this.y, ...options }; };
    let value = "Custom:one";
    let writes = 0;
    Object.defineProperty(widget, "value", {
        get() { return value; },
        set(next) { writes++; value = next; },
    });
    widget.options.getOptionLabel = (option) => `Original ${option}`;
    s.context.setupGuideMenu(widget, s.node);
    assert.equal(typeof widget.draw, "function");
    s.context.LiteGraph.vueNodesMode = true;
    widget.draw({}, s.node, 400, 1, 20);
    assert.deepEqual(drawn, { value: "Custom:one", y: 1, width: 400, showText: true });
    const menu = await s.open(widget);
    assert.equal(menu.at(-1).content, "+ Add a prompt guide");
    assert.equal(widget.options.getOptionLabel(widget.value), "Film");
    assert.equal(widget.options.getOptionLabel("None"), "Original None");
    assert.equal(widget.options.getOptionLabel("MiniMax H3 - Pika"), "Original MiniMax H3 - Pika");
    assert.equal(widget.value, "Custom:one");
    assert.equal(writes, 0);
});

test("guide width follows the node after a classic to Nodes 2.0 round trip", () => {
    const s = setup();
    const widget = s.makeWidget();
    delete widget.draw;
    let drawnWidth;
    widget.drawWidget = (ctx, options) => { drawnWidth = options.width; };
    s.context.setupGuideMenu(widget, s.node);
    s.context.LiteGraph.vueNodesMode = true;
    widget.width = 480;
    widget.draw({}, s.node, 480, 1, 20);
    assert.equal(drawnWidth, 480);
    s.context.LiteGraph.vueNodesMode = false;
    for (const width of [300, 720]) {
        s.node.size[0] = width;
        widget.draw({}, s.node, widget.width, 1, 20);
        assert.equal(drawnWidth, width);
        assert.equal(widget.width, width);
    }
});

test("custom folder and nested guide rows have separate SVG actions without selecting the row", async () => {
    const s = setup();
    await s.open();
    const root = s.menu();
    for (const index of [0, 1, 3, 4]) assert.equal(root.rows[index].children.length, 0);
    const folderRow = root.rows[2];
    assert.equal(Object.getPrototypeOf(root).constructor.name, "NativeMenu");
    assert.equal(folderRow.children[0].textContent, "📁 My guides");
    assert.equal(folderRow.children[1].children[0]["aria-label"], "Rename folder");
    assert.equal(folderRow.children[1].children[1]["aria-label"], "Delete folder");
    assert.equal(folderRow.children[1].children[0].style.cssText.includes("width:24px"), true);
    folderRow.dispatch("pointerenter");
    const child = root.current_submenu;
    const row = child.rows[0];
    const rename = row.children[1].children[0];
    const remove = row.children[1].children[1];
    assert.equal(rename["aria-label"], "Rename guide");
    assert.equal(remove["aria-label"], "Delete guide");
    assert.notEqual(rename.children[0].children[0].d, remove.children[0].children[0].d);
    assert.equal(rename.dispatch("pointerdown").stopped, true);
    const event = rename.dispatch("click");
    assert.equal(event.stopped, true);
    assert.equal(event.prevented, true);
    assert.equal(root.closed, true);
    assert.equal(child.closed, true);
    assert.equal(s.widget.value, "None");
    const { form, fields } = formFields(s.document);
    fields["Guide name"].value = "New Film";
    s.response({ ...s.initial, guides: [{ ...s.initial.guides[0], name: "New Film" }] });
    await submit(form);
    const patch = s.calls.find((call) => call.options?.method === "PATCH");
    assert.equal(patch.url, "/goat/prompt-guides");
    assert.deepEqual(JSON.parse(patch.options.body), { value: "Custom:one", name: "New Film" });
    assert.equal(s.widget.options.getOptionLabel("Custom:one"), "New Film");
});

test("deleting a guide asks first, preserves selection on cancel/error, and resets only matching live widgets", async () => {
    const s = setup();
    const guide = { ...s.initial.guides[0], folder: "" };
    const data = { ...s.initial, guides: [guide] };
    s.response(data);
    await s.open();
    const second = s.makeWidget();
    const third = s.makeWidget();
    s.context.setupGuideMenu(second, s.node);
    s.context.setupGuideMenu(third, s.node);
    s.widget.value = "Custom:one";
    second.value = "Custom:one";
    let resetCanvas;
    second.setValue = function (value, options) {
        this.value = value;
        resetCanvas = options.canvas;
        assert.deepEqual(options.canvas.graph_mouse, [0, 0]);
    };
    third.value = "Fibo 2";
    const openDelete = async () => {
        await s.open();
        const row = s.menu().rows.find((row) => row.value?.label === "Film");
        row.children[1].children[1].dispatch("click");
        return formFields(s.document);
    };
    let { form, dialog, status } = await openDelete();
    assert.match(form.children[1].textContent, /removes its saved guidance/);
    assert.equal(form.children.at(-1).children[0].focused, true);
    assert.equal(form.children.at(-1).children[1].textContent, "Delete");
    assert.match(form.children.at(-1).children[1].style.cssText, /background:#a52630/);
    assert.equal(s.calls.some((call) => call.options?.method === "DELETE"), false);
    dialog.close(); // Escape has the same native close lifecycle.
    assert.equal(s.widget.value, "Custom:one");
    ({ form, dialog, status } = await openDelete());
    s.fail("The guide could not be deleted.");
    await submit(form);
    assert.equal(dialog.open, true);
    assert.equal(status.textContent, "The guide could not be deleted.");
    assert.equal(s.widget.value, "Custom:one");
    assert.equal(second.value, "Custom:one");
    s.fail("");
    s.response({ ...s.initial, guides: [] });
    await submit(form);
    const request = s.calls.find((call) => call.options?.method === "DELETE");
    assert.equal(request.url, "/goat/prompt-guides");
    assert.deepEqual(JSON.parse(request.options.body), { value: "Custom:one" });
    assert.equal(s.widget.value, "None");
    assert.equal(second.value, "None");
    assert.equal(resetCanvas, s.context.app.canvas);
    assert.equal(third.value, "Fibo 2");
    assert.equal(second.options.values.includes("Custom:one"), false);
    assert.equal(dialog.open, false);
});

test("folder deletion confirms that guides are kept and moves their stable IDs to the main list", async () => {
    const s = setup();
    await s.open();
    s.widget.value = "Custom:one";
    s.menu().rows[2].children[1].children[1].dispatch("click");
    const { form } = formFields(s.document);
    assert.match(form.children[1].textContent, /guides will be kept in the main list/);
    s.response({ ...s.initial, folders: [], guides: [{ ...s.initial.guides[0], folder: "" }] });
    await submit(form);
    const request = s.calls.find((call) => call.options?.method === "DELETE");
    assert.equal(request.url, "/goat/prompt-guides/folders");
    assert.deepEqual(JSON.parse(request.options.body), { id: "f1" });
    assert.equal(s.widget.value, "Custom:one");
    const menu = await s.open();
    assert.equal(menu.some((item) => item?.label === "📁 My guides"), false);
    assert.equal(menu.find((item) => item?.label === "Film").guideActions.length, 2);
});

test("a stale GET cannot restore a deleted guide or its selected value", async () => {
    const s = setup();
    s.response({ ...s.initial, guides: [{ ...s.initial.guides[0], folder: "" }] });
    await s.open();
    s.widget.value = "Custom:one";
    s.menu().rows.find((row) => row.value?.label === "Film").children[1].children[1].dispatch("click");
    const { form } = formFields(s.document);
    let resolveGet;
    s.context.api.fetchApi = async (url, options) => {
        const result = options?.method === "DELETE" ? { ...s.initial, guides: [] }
            : await new Promise((resolve) => { resolveGet = resolve; });
        return { ok: true, json: async () => result };
    };
    const second = s.makeWidget();
    s.context.setupGuideMenu(second, s.node);
    await submit(form);
    resolveGet(s.initial);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(s.widget.value, "None");
    assert.equal(s.widget.options.values.includes("Custom:one"), false);
    assert.equal(second.options.values.includes("Custom:one"), false);
});

test("submenus align to the parent row rather than cursor height, inherit zoom, and flip at viewport edges", async () => {
    const s = setup();
    await s.open();
    const parent = s.menu();
    parent.options.scale = 1.5;
    parent.root.rect = { left: 100, right: 400, top: 100, width: 300, height: 300 };
    const row = parent.rows[2];
    row.rect = { left: 100, right: 400, top: 260, width: 300, height: 42 };
    row.dispatch("pointerenter", { clientY: 299 });
    let child = parent.current_submenu;
    assert.equal(child.options.scale, 1.5);
    assert.equal(child.root.style.top, "260px");
    assert.equal(child.root.style.left, "400px");
    assert.equal(child.root.style.maxHeight, `${(800 - 260 - 8) / 1.5}px`);
    assert.equal(child.root.style.overflowY, "auto");
    parent.root.rect = { left: 700, right: 1000, top: 100, width: 300, height: 300 };
    row.rect.top = 750;
    row.dispatch("click", { clientY: 790 });
    child = parent.current_submenu;
    assert.equal(child.root.style.top, "750px");
    assert.equal(child.root.style.left, "250px");
    assert.equal(child.root.style.maxHeight, "28px");
    s.document.documentElement.clientWidth = 320;
    parent.root.rect = { left: 100, right: 300, top: 0, width: 200, height: 100 };
    row.rect.top = -5;
    row.dispatch("pointerenter", { clientY: 30 });
    assert.equal(parent.current_submenu.root.style.top, "8px");
    assert.equal(parent.current_submenu.root.style.left, "8px");
    assert.ok(parent.current_submenu.root.getBoundingClientRect().right <= 312);
    assert.ok(parent.current_submenu.root.getBoundingClientRect().bottom <= 792);
    assert.equal(parent.current_submenu.rows[0].style.minWidth, "0");
});


test("custom action buttons activate with Enter and Space without invoking the row", async () => {
    const s = setup();
    await s.open();
    let parent = s.menu();
    const rename = parent.rows[2].children[1].children[0];
    rename.dispatch("keydown", { key: "Enter" });
    assert.equal(parent.current_submenu, undefined);
    assert.equal(parent.closed, true);
    assert.equal(formFields(s.document).fields["Folder name"].value, "My guides");
    formFields(s.document).dialog.close();
    await s.open();
    parent = s.menu();
    const remove = parent.rows[2].children[1].children[1];
    remove.dispatch("keydown", { key: " " });
    assert.equal(s.document.body.children.length, 0);
    remove.dispatch("keyup", { key: " " });
    assert.equal(parent.current_submenu, undefined);
    assert.equal(parent.closed, true);
    assert.equal(formFields(s.document).form.children[0].textContent, "Delete folder?");
    assert.equal(s.calls.some((call) => call.options?.method === "DELETE"), false);
    assert.equal(s.widget.value, "None");
});


test("concurrent node mutations are serialized so an earlier rename cannot restore a deleted guide", async () => {
    const s = setup();
    const initial = { ...s.initial, guides: [{ ...s.initial.guides[0], folder: "" }] };
    s.response(initial);
    await s.open();
    s.menu().rows.find((row) => row.value?.label === "Film").children[1].children[0].dispatch("click");
    const rename = formFields(s.document);
    rename.fields["Guide name"].value = "Renamed";
    const second = s.makeWidget();
    s.context.setupGuideMenu(second, s.node);
    await s.open(second);
    s.menu().rows.find((row) => row.value?.label === "Film").children[1].children[1].dispatch("click");
    const removal = formFields(s.document);
    const methods = [];
    let resolveRename;
    s.context.api.fetchApi = async (url, options) => {
        methods.push(options.method);
        const result = options.method === "PATCH" ? await new Promise((resolve) => { resolveRename = resolve; })
            : { ...initial, guides: [] };
        return { ok: true, json: async () => result };
    };
    const renaming = submit(rename.form);
    const deleting = submit(removal.form);
    await Promise.resolve();
    assert.deepEqual(methods, ["PATCH"]);
    resolveRename({ ...initial, guides: [{ ...initial.guides[0], name: "Renamed" }] });
    await Promise.all([renaming, deleting]);
    assert.deepEqual(methods, ["PATCH", "DELETE"]);
    assert.equal(s.widget.options.values.includes("Custom:one"), false);
    assert.equal(second.options.values.includes("Custom:one"), false);
});


test("decorated root menus stay inside the viewport after icon expansion at the right edge and high zoom", async () => {
    const s = setup();
    s.document.documentElement.clientWidth = 420;
    s.document.documentElement.clientHeight = 300;
    await s.open(s.widget, { scale: 2, event: { clientX: 400, clientY: 290 } });
    const menu = s.menu();
    assert.equal(Object.getPrototypeOf(menu).constructor.name, "NativeMenu");
    const rect = menu.root.getBoundingClientRect();
    assert.ok(rect.left >= 8);
    assert.ok(rect.right <= 412);
    assert.ok(rect.top >= 8);
    assert.ok(rect.bottom <= 292);
    assert.equal(menu.rows[2].style.minWidth, "0");
    assert.equal(menu.rows[2].children[1].children.length, 2);
    assert.equal(menu.root.style.transformOrigin, "top left");
});

test("scrolling a parent reanchors only its current submenu without redecorating or adding listeners", async () => {
    const s = setup();
    await s.open();
    const parent = s.menu();
    parent.root.rect = { left: 100, right: 400, top: 100, width: 300, height: 300 };
    const first = parent.rows[0];
    const custom = parent.rows[2];
    first.rect = { top: 130 };
    custom.rect = { top: 230 };
    first.dispatch("pointerenter");
    assert.equal(parent.current_submenu.root.style.top, "130px");
    custom.dispatch("pointerenter");
    const child = parent.current_submenu;
    assert.equal(child.root.style.top, "230px");
    custom.rect.top = 170;
    first.rect.top = 90;
    parent.root.dispatch("scroll");
    assert.equal(parent.current_submenu, child);
    assert.equal(child.root.style.top, "170px");
    assert.equal(child.rows[0].children[1].children.length, 2);
    assert.equal(child.root.listeners.scroll.length, 1);
    assert.equal(child.rows[0].listeners.click.length, 1);
    parent.current_submenu = undefined;
    parent.root.dispatch("scroll");
    assert.equal(parent.current_submenu, undefined);
});


test("confirmed deletion disables cancellation and Escape while pending and prevents duplicate requests", async () => {
    const s = setup();
    await s.open();
    s.menu().rows[2].children[1].children[1].dispatch("click");
    const { form, dialog, status } = formFields(s.document);
    const [cancel, remove] = form.children.at(-1).children;
    assert.equal(dialog.dispatch("cancel").prevented, undefined);
    let resolveDelete;
    let requests = 0;
    s.context.api.fetchApi = async () => {
        requests++;
        return await new Promise((resolve) => { resolveDelete = resolve; });
    };
    const pending = submit(form);
    await submit(form);
    await Promise.resolve();
    assert.equal(requests, 1);
    assert.equal(cancel.disabled, true);
    assert.equal(remove.disabled, true);
    assert.equal(dialog.dispatch("cancel").prevented, true);
    assert.equal(dialog.open, true);
    resolveDelete({ ok: false, json: async () => ({ error: "Could not delete the folder." }) });
    await pending;
    assert.equal(cancel.disabled, false);
    assert.equal(remove.disabled, false);
    assert.equal(dialog.open, true);
    assert.equal(status.textContent, "Could not delete the folder.");
    assert.equal(dialog.dispatch("cancel").prevented, undefined);
    cancel.onclick();
    assert.equal(dialog.open, false);
});
