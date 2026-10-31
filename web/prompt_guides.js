import { app } from "../../../scripts/app.js";
import { goatFetch } from "./security.js";

const states = new Map();
let catalog = { guides: [], folders: [], formats: [".md", ".txt", ".text", ".docx", ".pdf"] };
let refreshRequest;
let mutationQueue = Promise.resolve();
let revision = 0;
const families = ["MiniMax H3", "Seedance"];
const order = (a, b) => a.localeCompare(b, undefined, { sensitivity: "base" });
const menuText = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");

async function request(options, folder = false) {
    const response = await goatFetch("/goat/prompt-guides" + (folder ? "/folders" : ""), options);
    if (response.status === 401 || response.status === 403) throw new Error("GOAT access denied. Open ComfyUI locally or configure GOAT_ROUTE_TOKEN for remote access.");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Prompt guide request failed (HTTP ${response.status}).`);
    return result;
}

function mutate(options, folder = false, deletedValue) {
    const operation = mutationQueue.then(async () => {
        const result = await request(options, folder);
        updateCatalog(result, deletedValue);
        return result;
    });
    mutationQueue = operation.catch(() => {});
    return operation;
}

function updateCatalog(result, deletedValue) {
    catalog = result;
    revision++;
    for (const [widget, state] of states) {
        widget.options.values = [...state.builtins, ...catalog.guides.map((guide) => guide.value)];
        if (deletedValue && widget.value === deletedValue) selectGuide(widget, state, "None");
        widget.triggerDraw?.();
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
    if (widget.setValue) widget.setValue(value, { e: event, node: state.node, canvas: state.canvas ?? app.canvas });
    else {
        widget.value = value;
        widget.callback?.(value);
    }
    state.node.setDirtyCanvas(true, true);
}

function editor(widget, state, target) {
    const folder = target?.kind === "folder";
    const renaming = Boolean(target);
    state.dialog?.close();
    const dialog = document.createElement("dialog");
    state.dialog = dialog;
    dialog.style.cssText = "box-sizing:border-box;width:460px;max-width:calc(100vw - 24px);max-height:calc(100vh - 24px);overflow:auto;background:var(--comfy-menu-bg,#222);color:var(--input-text,#ddd);border:1px solid var(--border-color,#555);border-radius:8px;padding:20px";
    const form = document.createElement("form");
    const title = document.createElement("h3");
    title.textContent = renaming ? `Rename ${target.kind}` : "Add a prompt guide";
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
    if (!renaming) {
        file = field("Guide file", document.createElement("input"));
        file.type = "file";
        file.accept = catalog.formats.join(",");
        file.required = true;
    }
    const name = field(folder ? "Folder name" : "Guide name", document.createElement("input"));
    name.type = "text";
    name.required = true;
    name.maxLength = 120;
    name.value = target?.item.name ?? "";
    if (!renaming) {
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
    save.textContent = renaming ? "Rename" : "Add guide";
    buttons.append(cancel, save);
    form.append(buttons);
    let saving = false;
    form.onsubmit = async (event) => {
        event.preventDefault();
        if (saving) return;
        saving = true;
        save.disabled = true;
        status.textContent = renaming ? "Saving…" : "Reading guide…";
        try {
            let options;
            if (renaming) options = { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...(folder ? { id: target.item.id } : { value: target.item.value }), name: name.value.trim() }) };
            else {
                if (!file.files?.[0]) throw new Error("Choose a guide file.");
                const body = new FormData();
                body.append("file", file.files[0]);
                body.append("name", name.value.trim());
                body.append("folder", folderSelect.value === "__new__" ? "" : folderSelect.value);
                body.append("new_folder", folderSelect.value === "__new__" ? newFolder.value.trim() : "");
                options = { method: "POST", body };
            }
            const result = await mutate(options, folder);
            if (dialog.open && states.has(widget) && !renaming) selectGuide(widget, state, result.value);
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

function confirmDelete(state, target) {
    state.dialog?.close();
    const dialog = document.createElement("dialog");
    state.dialog = dialog;
    dialog.style.cssText = "box-sizing:border-box;width:440px;max-width:calc(100vw - 24px);max-height:calc(100vh - 24px);overflow:auto;background:var(--comfy-menu-bg,#222);color:var(--input-text,#ddd);border:1px solid var(--border-color,#555);border-radius:8px;padding:20px";
    const form = document.createElement("form");
    const title = document.createElement("h3");
    title.textContent = `Delete ${target.kind}?`;
    const description = document.createElement("p");
    description.textContent = target.kind === "folder"
        ? `Delete the folder “${target.item.name}”? Its guides will be kept in the main list.`
        : `Delete the guide “${target.item.name}”? This removes its saved guidance. Nodes using it will switch to None.`;
    description.style.cssText = "line-height:1.5;overflow-wrap:anywhere";
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.style.cssText = "overflow-wrap:anywhere";
    const buttons = document.createElement("div");
    buttons.style.cssText = "display:flex;justify-content:flex-end;gap:12px";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.onclick = () => dialog.close();
    const remove = document.createElement("button");
    remove.type = "submit";
    remove.textContent = "Delete";
    remove.style.cssText = "background:#a52630;color:#fff;border:1px solid #cf4550;border-radius:4px;padding:6px 14px";
    buttons.append(cancel, remove);
    form.append(title, description, status, buttons);
    let saving = false;
    dialog.addEventListener("cancel", (event) => { if (saving) event.preventDefault(); });
    form.onsubmit = async (event) => {
        event.preventDefault();
        if (saving) return;
        saving = true;
        cancel.disabled = true;
        remove.disabled = true;
        status.textContent = "Deleting…";
        try {
            const folder = target.kind === "folder";
            await mutate({ method: "DELETE", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(folder ? { id: target.item.id } : { value: target.item.value }) }, folder, folder ? undefined : target.item.value);
            dialog.close();
        } catch (error) {
            status.textContent = error.message || "Could not delete. Try again.";
        } finally {
            saving = false;
            cancel.disabled = false;
            remove.disabled = false;
        }
    };
    dialog.addEventListener("close", () => {
        dialog.remove();
        if (state.dialog === dialog) state.dialog = null;
    });
    dialog.append(form);
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
}

function actions(widget, state, target) {
    return [
        { label: `Rename ${target.kind}`, icon: "rename", callback: () => editor(widget, state, target) },
        { label: `Delete ${target.kind}`, icon: "delete", callback: () => confirmDelete(state, target) },
    ];
}

const decoratedMenus = new WeakSet();
let folderArrowStyle;

function constrainMenu(menu, top) {
    const root = menu.root;
    const scale = root.getBoundingClientRect().height / root.offsetHeight || 1;
    root.style.maxHeight = `${Math.max(40, document.documentElement.clientHeight - top - 8) / scale}px`;
    root.style.maxWidth = `${(document.documentElement.clientWidth - 16) / scale}px`;
    root.style.boxSizing = "border-box";
    root.style.overflowY = "auto";
    root.style.overflowX = "hidden";
    for (const row of root.children) {
        if (row.value?.guideActions) Object.assign(row.style, { minWidth: "0", maxWidth: "100%" });
    }
    return root.getBoundingClientRect();
}

function decorateGuideMenu(menu) {
    if (decoratedMenus.has(menu)) return;
    decoratedMenus.add(menu);
    if (!folderArrowStyle) {
        folderArrowStyle = document.createElement("style");
        folderArrowStyle.textContent = ".litegraph.litecontextmenu .litemenu-entry.goat-custom-guide-folder.has_submenu::after{top:50%;transform:translateY(-50%) rotate(45deg);rotate:0deg;right:4px;content:'';width:5px;height:5px;border:solid currentColor;border-width:1.5px 1.5px 0 0;pointer-events:none}" +
            ".litegraph.litecontextmenu.goat-guide-menu .litemenu-entry[data-goat-selected=false]:hover{background-color:var(--comfy-menu-bg)!important;color:var(--fg-color)!important}";
        document.head.append(folderArrowStyle);
    }
    menu.root.className += " goat-guide-menu";
    const rows = [...menu.root.children].filter((row) => row.matches(".litemenu-entry") && row.value != null && !row.value.disabled);
    let selectedRow;
    const visibleRows = () => rows.filter((row) => row.style.display !== "none");
    const selectRow = (selected) => {
        selectedRow = selected;
        for (const row of rows) {
            row.setAttribute("data-goat-selected", String(row === selected));
            row.style.removeProperty("background-color");
            row.style.removeProperty("color");
        }
        selected?.style.setProperty("background-color", "var(--content-hover-bg, #345)", "important");
        selected?.style.setProperty("color", "var(--content-hover-fg, #fff)", "important");
    };
    for (const row of rows) row.addEventListener("pointerenter", () => selectRow(row));
    menu.root.addEventListener("keydown", (event) => {
        if (rows.some((row) => row.value.guideActions && row.children[1].contains(event.target))) return;
        if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter", "Escape"].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        if (event.key === "Escape") { menu.getTopMenu().close(); return; }
        if (event.key === "ArrowRight") {
            if (selectedRow?.value.submenu) {
                selectedRow.click();
                menu.current_submenu.root.tabIndex = -1;
                menu.current_submenu.root.focus();
            }
            return;
        }
        if (event.key === "ArrowLeft") {
            const parent = menu.options.parentMenu;
            if (parent) {
                menu.close(event, true);
                parent.current_submenu = undefined;
                parent.lock = false;
                for (const row of parent.root.children) {
                    if (row.value?.submenu) row.setAttribute("aria-expanded", "false");
                }
                const filter = [...parent.root.children].find((element) => element.tagName === "INPUT");
                (filter ?? parent.root).focus();
            }
            return;
        }
        const visible = visibleRows();
        if (event.key === "Enter") { (visible.includes(selectedRow) ? selectedRow : visible[0])?.click(); return; }
        if (!visible.length) return;
        const index = visible.indexOf(selectedRow);
        const next = index < 0 ? (event.key === "ArrowDown" ? 0 : visible.length - 1)
            : (index + (event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length;
        if (menu.current_submenu && visible[next] !== activeRow) {
            menu.current_submenu.close(event, true);
            menu.current_submenu = undefined;
            menu.lock = false;
            activeRow?.setAttribute("aria-expanded", "false");
        }
        selectRow(visible[next]);
        selectedRow.scrollIntoView({ block: "nearest" });
    }, true);
    menu.root.addEventListener("input", () => {
        for (const row of rows) {
            if (row.value.guideActions && row.style.display !== "none") row.style.display = "flex";
        }
        selectRow(visibleRows().includes(selectedRow) ? selectedRow : undefined);
    });
    // The native filter initializes its first-row highlight on the next frame.
    requestAnimationFrame(() => selectRow(selectedRow ?? visibleRows().find((row) => row.matches(":hover"))));
    if (menu.options.parentMenu) {
        menu.options.scale = menu.options.parentMenu.options.scale;
        if (LiteGraph.context_menu_scaling && menu.options.scale) {
            menu.root.style.transform = `scale(${Math.round(menu.options.scale * 4) * 0.25})`;
        }
    }
    let alignCurrent;
    let activeRow;
    let closeTimer;
    const cancelClose = () => clearTimeout(closeTimer);
    const scheduleClose = (event) => {
        const submenu = menu.current_submenu;
        const row = activeRow;
        if (!submenu || !row) return;
        if (event.relatedTarget && (row.contains(event.relatedTarget) || submenu.containsNode(event.relatedTarget))) return;
        cancelClose();
        closeTimer = setTimeout(() => {
            if (menu.current_submenu !== submenu || submenu.root.contains(document.activeElement)) return;
            submenu.close(event, true);
            menu.current_submenu = undefined;
            menu.lock = false;
            row.setAttribute("aria-expanded", "false");
        }, 120);
    };
    menu.root.addEventListener("pointerleave", scheduleClose);
    menu.root.addEventListener("scroll", () => alignCurrent?.());
    for (const row of menu.root.children) {
        const value = row.value;
        if (value?.guideActions) {
            const label = document.createElement("span");
            label.textContent = value.label;
            label.title = value.label;
            label.style.cssText = "flex:1;min-width:0;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
            const controls = document.createElement("span");
            controls.style.cssText = "display:flex;gap:6px;flex-shrink:0;margin-left:auto";
            for (const action of value.guideActions) {
                const button = document.createElement("button");
                button.type = "button";
                button.title = action.label;
                button.setAttribute("aria-label", action.label);
                button.style.cssText = `display:flex;align-items:center;justify-content:center;box-sizing:border-box;width:24px;height:22px;padding:3px;margin:2px 0;flex-shrink:0;border:1px solid rgb(255 255 255 / 0.14);border-radius:4px;background:transparent;color:${action.icon === "delete" ? "#ff8a92" : "#a8ceff"};cursor:pointer`;
                button.addEventListener("pointerenter", () => { button.style.background = "rgb(255 255 255 / 0.1)"; });
                button.addEventListener("pointerleave", () => { button.style.background = "transparent"; });
                const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
                svg.setAttribute("viewBox", "0 0 24 24");
                svg.setAttribute("width", "15");
                svg.setAttribute("height", "15");
                svg.setAttribute("fill", "none");
                svg.setAttribute("stroke", "currentColor");
                svg.setAttribute("stroke-width", "1.8");
                svg.setAttribute("stroke-linecap", "round");
                svg.setAttribute("stroke-linejoin", "round");
                svg.setAttribute("aria-hidden", "true");
                const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                path.setAttribute("d", action.icon === "delete"
                    ? "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"
                    : "m16 3 5 5-12 12-6 1 1-6L16 3Zm-1 2 5 5M4 15l5 5");
                svg.append(path);
                button.append(svg);
                button.addEventListener("pointerdown", (event) => event.stopPropagation());
                button.addEventListener("click", (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    menu.getTopMenu().close();
                    action.callback();
                });
                controls.append(button);
            }
            row.replaceChildren(label, controls);
            Object.assign(row.style, { display: "flex", alignItems: "center", gap: "10px", padding: "3px 8px", minHeight: "32px", minWidth: "240px", boxSizing: "border-box" });
            if (value.submenu) {
                row.style.paddingRight = "24px";
                row.className += " goat-custom-guide-folder";
            }
        }
        if (value?.submenu) {
            const align = () => {
                const submenu = menu.current_submenu;
                if (!submenu) return;
                decorateGuideMenu(submenu);
                const root = submenu.root;
                root.style.transformOrigin = "top left";
                const parent = menu.root.getBoundingClientRect();
                const entry = row.getBoundingClientRect();
                const viewportWidth = document.documentElement.clientWidth;
                const top = Math.max(8, Math.min(entry.top, document.documentElement.clientHeight - 48));
                const width = constrainMenu(submenu, top).width;
                const left = parent.right + width <= viewportWidth - 8 ? parent.right : parent.left - width;
                root.style.left = `${Math.max(8, left)}px`;
                root.style.top = `${top}px`;
            };
            const activate = () => {
                cancelClose();
                activeRow = row;
                alignCurrent = align;
                align();
                const submenu = menu.current_submenu;
                submenu.root.addEventListener("pointerenter", cancelClose);
                submenu.root.addEventListener("pointerleave", scheduleClose);
            };
            row.addEventListener("pointerenter", activate);
            row.addEventListener("click", activate);
            row.addEventListener("pointerleave", scheduleClose);
        }
    }
    if (!menu.options.parentMenu) {
        const root = menu.root;
        root.style.transformOrigin = "top left";
        const top = Math.max(8, Math.min(root.getBoundingClientRect().top, document.documentElement.clientHeight - 48));
        const rect = constrainMenu(menu, top);
        root.style.left = `${Math.max(8, Math.min(rect.left, document.documentElement.clientWidth - rect.width - 8))}px`;
        root.style.top = `${top}px`;
    }
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
    // Nodes 2.0 must use the custom widget adapter to preserve the guide menu.
    if (!widget.draw && widget.drawWidget) widget.draw = function (ctx, node, width, y) {
        if (!LiteGraph.vueNodesMode) this.width = width = node.size[0];
        this.y = y;
        this.drawWidget(ctx, { width, showText: true });
    };
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
            const customLeaf = (guide) => ({ ...leaf(guide.value, guide.name), label: guide.name,
                guideActions: actions(widget, state, { kind: "guide", item: guide }) });
            for (const folder of [...catalog.folders].sort((a, b) => order(a.name, b.name))) {
                folders.push({ content: menuText(`📁 ${folder.name}`), label: `📁 ${folder.name}`,
                    guideActions: actions(widget, state, { kind: "folder", item: folder }), has_submenu: true,
                    submenu: { options: catalog.guides.filter((guide) => guide.folder === folder.id)
                        .sort((a, b) => order(a.name, b.name)).map(customLeaf) } });
            }
            const rest = sorted.filter((value) => value !== "None" && !families.some((family) => value.startsWith(`${family} `)));
            const options = [...folders, leaf("None"), ...rest.map((value) => leaf(value)),
                ...catalog.guides.filter((guide) => !guide.folder).sort((a, b) => order(a.name, b.name)).map(customLeaf),
                null, { content: "+ Add a prompt guide", callback: () => editor(widget, state) }];
            if (error) options.push({ content: menuText(error), disabled: true });
            decorateGuideMenu(new LiteGraph.ContextMenu(options, { event, scale: Math.max(1, canvas.ds.scale), className: "dark", autoopen: true }));
        };
        return true;
    };
}

export function removeGuideMenu(widget) {
    states.get(widget)?.dialog?.close();
    states.delete(widget);
}
