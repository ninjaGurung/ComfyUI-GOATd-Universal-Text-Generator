import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";

class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.listeners = {}; this.value = ""; }
    append(...children) { children.forEach((child) => { child.parentElement = this; this.children.push(child); }); }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    showModal() { this.open = true; }
    close() { this.open = false; this.listeners.close?.(); }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((child) => child !== this); }
    focus() {}
}
function setup() {
    const document = { createElement: (tag) => new Element(tag), body: new Element("body") };
    let latestMenu;
    const calls = [];
    const initial = { guides: [{ value: "Custom:one", name: "Film", folder: "f1" }], folders: [{ id: "f1", name: "My guides" }], formats: [".md", ".docx", ".pdf"] };
    let response = initial;
    let failure;
    const context = {
        document,
        api: { fetchApi: async (url, options) => { calls.push({ url, options }); return { ok: !failure, json: async () => failure ? { error: failure } : response }; } },
        LiteGraph: { ContextMenu: function (options) { latestMenu = options; } },
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
    const open = async (which = widget) => {
        const pointer = { eDown: { button: 0, canvasX: 200 } };
        which.onPointerDown(pointer, node, { ds: { scale: 1 } });
        await pointer.onClick();
        return latestMenu;
    };
    return { context, document, node, widget, makeWidget, open, calls, initial,
        response(value) { response = value; }, fail(value) { failure = value; } };
}
function formFields(document) {
    const dialog = document.body.children.at(-1);
    const form = dialog.children[0];
    const fields = Object.fromEntries(form.children.filter((child) => child.tag === "label").map((label) => [label.textContent, label.children[0]]));
    return { dialog, form, fields, status: form.children.find((child) => child.role === "status") };
}
const submit = (form) => form.onsubmit({ preventDefault() {} });

test("menu preserves bundled groups and offers custom folder rename without serializing actions", async () => {
    const s = setup();
    const menu = await s.open();
    assert.equal(menu[0].content, "📁 MiniMax H3");
    assert.equal(menu[0].submenu.options[0].content, "Pika");
    assert.equal(menu[0].submenu.options.some((item) => item?.content === "Rename folder…"), false);
    assert.equal(menu[2].content, "📁 My guides");
    assert.equal(menu[2].submenu.options.at(-1).content, "Rename folder…");
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
    menu[2].submenu.options.at(-1).callback();
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
    menu[2].submenu.options.at(-1).callback();
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
    let value = "Custom:one";
    let writes = 0;
    Object.defineProperty(widget, "value", {
        get() { return value; },
        set(next) { writes++; value = next; },
    });
    widget.options.getOptionLabel = (option) => `Original ${option}`;
    s.context.setupGuideMenu(widget, s.node);
    assert.equal(widget.options.getOptionLabel(widget.value), "Film");
    assert.equal(widget.options.getOptionLabel("None"), "Original None");
    assert.equal(widget.options.getOptionLabel("MiniMax H3 - Pika"), "Original MiniMax H3 - Pika");
    assert.equal(widget.value, "Custom:one");
    assert.equal(writes, 0);
});
