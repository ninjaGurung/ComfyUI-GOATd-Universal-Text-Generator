import { goatFetch } from "./security.js";

const states = new Map();
let catalog = { guides: [], folders: [], formats: [".md", ".txt", ".text", ".docx", ".pdf"] };
let refreshRequest;
let revision = 0;
const families = ["MiniMax H3", "Seedance"];
const order = (a, b) => a.localeCompare(b, undefined, { sensitivity: "base" });
const menuText = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");

async function request(options) {
    const response = await goatFetch("/goat/prompt-guides" + (options?.method === "PATCH" ? "/folders" : ""), options);
    if (response.status === 401 || response.status === 403) throw new Error("GOAT access denied. Open ComfyUI locally or configure GOAT_ROUTE_TOKEN for remote access.");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Prompt guide request failed (HTTP ${response.status}).`);
    return result;
}

function updateCatalog(result) {
    catalog = result;
    revision++;
    for (const [widget, state] of states) {
        widget.options.values = [...state.builtins, ...catalog.guides.map((guide) => guide.value)];
        state.node.setDirtyCanvas(true, true);
    }
}

function refreshCatalog() {
    if (!refreshRequest) {
        const current = revision;
        refreshRequest = request().then((result) => {
            if (current === revision) updateCatalog(result);
        }).finally(() => { refreshRequest = null; });
    }
    return refreshRequest;
}

function selectGuide(widget, state, value, event) {
    if (widget.setValue) widget.setValue(value, { e: event, node: state.node, canvas: state.canvas });
    else {
        widget.value = value;
        widget.callback?.(value);
    }
    state.node.setDirtyCanvas(true, true);
}

function editor(widget, state, folder) {
    state.dialog?.close();
    const dialog = document.createElement("dialog");
    state.dialog = dialog;
    dialog.style.cssText = "box-sizing:border-box;width:460px;max-width:calc(100vw - 24px);max-height:calc(100vh - 24px);overflow:auto;background:var(--comfy-menu-bg,#222);color:var(--input-text,#ddd);border:1px solid var(--border-color,#555);border-radius:8px;padding:20px";
    const form = document.createElement("form");
    const title = document.createElement("h3");
    title.textContent = folder ? "Rename folder" : "Add a prompt guide";
    form.append(title);
    const field = (labelText, input) => {
        const label = document.createElement("label");
        label.textContent = labelText;
        label.style.cssText = "display:block;margin:12px 0";
        input.setAttribute("aria-label", labelText);
        input.style.cssText = "box-sizing:border-box;width:100%;padding:8px;margin-top:4px";
        label.append(input);
        form.append(label);
        return input;
    };
    let file, folderSelect, newFolder;
    if (!folder) {
        file = field("Guide file", document.createElement("input"));
        file.type = "file";
        file.accept = catalog.formats.join(",");
        file.required = true;
    }
    const name = field(folder ? "Folder name" : "Guide name", document.createElement("input"));
    name.type = "text";
    name.required = true;
    name.maxLength = 120;
    name.value = folder?.name ?? "";
    if (!folder) {
        file.onchange = () => {
            if (!name.value.trim() && file.files?.[0]) name.value = file.files[0].name.replace(/\.[^.]+$/, "");
        };
        folderSelect = field("Folder", document.createElement("select"));
        for (const [value, text] of [["", "No folder"], ...catalog.folders.map((item) => [item.id, item.name]), ["__new__", "+ New folder…"]]) {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = text;
            folderSelect.append(option);
        }
        folderSelect.value = "";
        newFolder = field("New folder name", document.createElement("input"));
        newFolder.type = "text";
        newFolder.maxLength = 120;
        newFolder.parentElement.style.display = "none";
        folderSelect.onchange = () => {
            const creating = folderSelect.value === "__new__";
            newFolder.parentElement.style.display = creating ? "block" : "none";
            newFolder.required = creating;
            if (creating) newFolder.focus();
        };
        const hint = document.createElement("p");
        hint.textContent = "Guides are saved locally for future workflows. Word files must be .docx; PDFs must contain selectable text.";
        hint.style.cssText = "font-size:12px;line-height:1.5";
        form.append(hint);
    }
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.style.cssText = "overflow-wrap:anywhere";
    form.append(status);
    const buttons = document.createElement("div");
    buttons.style.cssText = "display:flex;justify-content:flex-end;gap:8px";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.onclick = () => dialog.close();
    const save = document.createElement("button");
    save.type = "submit";
    save.textContent = folder ? "Rename" : "Add guide";
    buttons.append(cancel, save);
    form.append(buttons);
    let saving = false;
    form.onsubmit = async (event) => {
        event.preventDefault();
        if (saving) return;
        saving = true;
        save.disabled = true;
        status.textContent = folder ? "Saving…" : "Reading guide…";
        try {
            let options;
            if (folder) options = { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: folder.id, name: name.value.trim() }) };
            else {
                if (!file.files?.[0]) throw new Error("Choose a guide file.");
                const body = new FormData();
                body.append("file", file.files[0]);
                body.append("name", name.value.trim());
                body.append("folder", folderSelect.value === "__new__" ? "" : folderSelect.value);
                body.append("new_folder", folderSelect.value === "__new__" ? newFolder.value.trim() : "");
                options = { method: "POST", body };
            }
            const result = await request(options);
            updateCatalog(result);
            if (dialog.open && states.has(widget) && !folder) selectGuide(widget, state, result.value);
            dialog.close();
        } catch (error) {
            status.textContent = error.message || "Could not save the guide. Try again.";
        } finally {
            saving = false;
            save.disabled = false;
        }
    };
    dialog.addEventListener("close", () => {
        dialog.remove();
        if (state.dialog === dialog) state.dialog = null;
    });
    dialog.append(form);
    document.body.append(dialog);
    dialog.showModal();
    (file ?? name).focus();
}

export function setupGuideMenu(widget, node) {
    if (!widget || states.has(widget)) return;
    const state = { node, builtins: [...widget.options.values].filter((value) => !value.startsWith("Custom:")), dialog: null };
    states.set(widget, state);
    widget.options.values = [...state.builtins, ...catalog.guides.map((guide) => guide.value)];
    refreshCatalog().catch(() => {});
    const getOptionLabel = widget.options.getOptionLabel;
    widget.options.getOptionLabel = (value) => catalog.guides.find((guide) => guide.value === value)?.name
        ?? getOptionLabel?.(value) ?? String(value ?? "");
    const onPointerDown = widget.onPointerDown;
    widget.onPointerDown = function (pointer, node, canvas) {
        const event = pointer.eDown;
        const x = event.canvasX - node.pos[0];
        const width = this.width || node.size[0];
        if (event.button !== 0 || x < 40 || x > width - 40) return onPointerDown?.apply(this, arguments) ?? false;
        state.canvas = canvas;
        pointer.onClick = async () => {
            let error = "";
            try { await refreshCatalog(); }
            catch (cause) { error = cause.message || "Could not load custom guides."; }
            if (!states.has(widget)) return;
            const leaf = (value, content = value) => ({ content: menuText(content), callback: () => selectGuide(widget, state, value, event) });
            const sorted = [...state.builtins].sort(order);
            const folders = families.map((family) => ({
                content: `📁 ${family}`, has_submenu: true,
                submenu: { options: sorted.filter((value) => value.startsWith(`${family} `))
                    .map((value) => leaf(value, value.slice(family.length).replace(/^ - /, "").trim())) },
            }));
            for (const folder of [...catalog.folders].sort((a, b) => order(a.name, b.name))) {
                folders.push({ content: menuText(`📁 ${folder.name}`), has_submenu: true, submenu: { options: [
                    ...catalog.guides.filter((guide) => guide.folder === folder.id).sort((a, b) => order(a.name, b.name)).map((guide) => leaf(guide.value, guide.name)),
                    null, { content: "Rename folder…", callback: () => editor(widget, state, folder) },
                ] } });
            }
            const rest = sorted.filter((value) => value !== "None" && !families.some((family) => value.startsWith(`${family} `)));
            const options = [...folders, leaf("None"), ...rest.map((value) => leaf(value)),
                ...catalog.guides.filter((guide) => !guide.folder).sort((a, b) => order(a.name, b.name)).map((guide) => leaf(guide.value, guide.name)),
                null, { content: "+ Add a prompt guide", callback: () => editor(widget, state) }];
            if (error) options.push({ content: menuText(error), disabled: true });
            new LiteGraph.ContextMenu(options, { event, scale: Math.max(1, canvas.ds.scale), className: "dark", autoopen: true });
        };
        return true;
    };
}

export function removeGuideMenu(widget) {
    states.get(widget)?.dialog?.close();
    states.delete(widget);
}
