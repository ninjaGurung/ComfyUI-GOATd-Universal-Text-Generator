// Author: ninjaGurung
// GitHub: https://github.com/ninjaGurung

import { resolveMediaPreview, createMediaPreview } from "./media_preview.js";

const suggestionsState = Symbol("goatTagSuggestions");
let nextListId = 0;

function connectedTags(node) {
    const tags = [];
    for (const [index, input] of (node.inputs ?? []).entries()) {
        const match = /^(?:images\.|videos\.|audios\.)?(image|video|audio)(\d+)$/.exec(input.name);
        if (match && node.isInputConnected(index)) {
            tags.push({ tag: `@${match[1]}${match[2]}`, kind: match[1], number: Number(match[2]) });
        }
    }
    const kinds = { image: 0, video: 1, audio: 2 };
    return tags.sort((a, b) => kinds[a.kind] - kinds[b.kind] || a.number - b.number)
        .map((item) => item.tag);
}

function prefixAtCaret(textarea) {
    if (textarea.selectionStart !== textarea.selectionEnd) return null;
    const end = textarea.selectionStart;
    const match = /(?:^|[\s([{,])(@[A-Za-z0-9]*)$/.exec(textarea.value.slice(0, end));
    return match ? { text: match[1], start: end - match[1].length, end } : null;
}

export function mountTagSuggestions(node, widget) {
    const textarea = widget.element?.querySelector("textarea") ?? widget.element;
    if (!textarea || textarea.readOnly) return;
    let state = widget[suggestionsState];
    if (state && state.textarea !== textarea) {
        removeTagSuggestions(widget);
        state = null;
    }
    if (state) {
        state.sync();
        return;
    }

    const doc = textarea.ownerDocument;
    const view = doc.defaultView;
    const tooltip = doc.createElement("div");
    tooltip.id = `goat-tag-preview-${nextListId + 1}`;
    tooltip.setAttribute("role", "tooltip");
    Object.assign(tooltip.style, {
        position: "fixed", zIndex: "10000", display: "none", pointerEvents: "none",
        padding: "8px", borderRadius: "6px", border: "1px solid var(--border-color, #666)",
        background: "var(--comfy-input-bg, #222)", color: "var(--input-text, #ddd)",
        font: "12px/1.4 sans-serif", boxShadow: "0 4px 16px #0006",
    });
    const popup = doc.createElement("div");
    popup.id = `goat-tag-suggestions-${++nextListId}`;
    popup.setAttribute("role", "listbox");
    popup.setAttribute("aria-label", "Connected media tags");
    Object.assign(popup.style, {
        position: "absolute", zIndex: "10", display: "none", overflowY: "auto",
        maxHeight: "160px", boxSizing: "border-box",
        background: "var(--comfy-input-bg, #222)", color: "var(--input-text, #ddd)",
        border: "1px solid var(--border-color, #666)", borderRadius: "4px",
        fontSize: "12px", pointerEvents: "auto",
    });
    const mirror = doc.createElement("div");
    Object.assign(mirror.style, {
        position: "absolute", visibility: "hidden", pointerEvents: "none",
        whiteSpace: "pre-wrap", overflowWrap: "break-word", boxSizing: "border-box",
        top: "0", left: "0", height: "auto",
    });
    const decorations = doc.createElement("div");
    decorations.setAttribute("aria-label", "Media references");
    Object.assign(decorations.style, {
        position: "absolute", zIndex: "3", overflow: "hidden", pointerEvents: "none",
    });
    const text = doc.createElement("div");
    Object.assign(text.style, {
        position: "relative", boxSizing: "border-box", color: "transparent", userSelect: "none",
    });
    decorations.appendChild(text);
    const styles = new Map(["lineHeight", "paddingTop", "paddingRight"].map((name) => [name, textarea.style[name]]));
    const attributes = new Map(["aria-autocomplete", "aria-controls", "aria-expanded", "aria-activedescendant"]
        .map((name) => [name, textarea.getAttribute(name)]));
    textarea.setAttribute("aria-autocomplete", "list");
    textarea.setAttribute("aria-controls", popup.id);
    textarea.setAttribute("aria-expanded", "false");

    state = { textarea, popup, mirror, decorations, styles, items: [], active: 0, prefix: null, target: null, visible: false, key: "", suppressedPrefix: "", listeners: [], composing: false };
    widget[suggestionsState] = state;

    let references = [];
    let thumbnails = [];
    let hovered = null;
    let preview = null;
    let previewKey = "";

    function hidePreview() {
        hovered?.label.removeAttribute("aria-describedby");
        hovered = null;
        preview?.dispose();
        preview = null;
        previewKey = "";
        tooltip.replaceChildren();
        tooltip.style.display = "none";
    }

    function positionPreview() {
        if (!hovered) return;
        const rect = hovered.label.getBoundingClientRect();
        const removeRect = hovered.remove.getBoundingClientRect();
        const anchorTop = Math.min(rect.top, removeRect.top);
        const anchorBottom = Math.max(rect.bottom, removeRect.bottom);
        const bounds = textarea.getBoundingClientRect();
        if (!hovered.label.isConnected || rect.bottom <= bounds.top || rect.top >= bounds.bottom || rect.right <= bounds.left || rect.left >= bounds.right) {
            hidePreview();
            return;
        }
        const width = tooltip.offsetWidth;
        const height = tooltip.offsetHeight;
        tooltip.style.left = `${Math.max(4, Math.min(rect.left, view.innerWidth - width - 4))}px`;
        const top = anchorBottom + 8 + height <= view.innerHeight ? anchorBottom + 8 : anchorTop - height - 8;
        tooltip.style.top = `${Math.max(4, Math.min(top, view.innerHeight - height - 4))}px`;
    }

    function showPreview(label, tag, remove) {
        const media = resolveMediaPreview(node, tag);
        const key = JSON.stringify(media);
        if (hovered?.label !== label || key !== previewKey) {
            hidePreview();
            hovered = { label, tag, remove };
            previewKey = key;
            preview = createMediaPreview(doc, media, 160);
            const caption = doc.createElement("div");
            caption.textContent = media.title;
            Object.assign(caption.style, { width: "160px", marginTop: "6px", overflowWrap: "anywhere" });
            tooltip.append(preview.element, caption);
            doc.body.appendChild(tooltip);
            label.setAttribute("aria-describedby", tooltip.id);
            tooltip.style.display = "block";
        }
        positionPreview();
    }

    function clearThumbnails() {
        for (const thumbnail of thumbnails) thumbnail.dispose();
        thumbnails = [];
    }

    function layout() {
        const parent = textarea.parentElement;
        if (!parent) return;
        const style = getComputedStyle(textarea);
        // Keep the raised remove controls clear of adjacent lines and the clipped edges.
        if (!(Number.parseFloat(style.lineHeight) >= Number.parseFloat(style.fontSize) * 1.8)) textarea.style.lineHeight = "1.8";
        if (Number.parseFloat(style.paddingTop) < 7) textarea.style.paddingTop = "7px";
        if (Number.parseFloat(style.paddingRight) < 5) textarea.style.paddingRight = "5px";
        const names = ["fontFamily", "fontSize", "fontWeight", "fontStyle", "fontVariant", "fontStretch", "fontKerning", "fontFeatureSettings", "lineHeight", "letterSpacing", "wordSpacing", "textAlign", "textIndent", "textTransform", "direction", "whiteSpace", "overflowWrap", "wordBreak", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "tabSize"];
        const key = [textarea.offsetLeft, textarea.offsetTop, textarea.clientLeft, textarea.clientTop, textarea.clientWidth, textarea.clientHeight, ...names.map((name) => style[name])].join(";");
        if (parent === state.parent && key === state.layoutKey) return;
        state.parent = parent;
        state.layoutKey = key;
        for (const element of [popup, mirror, decorations]) {
            if (element.parentElement !== parent) parent.appendChild(element);
        }
        for (const name of names) {
            mirror.style[name] = text.style[name] = style[name];
        }
        mirror.style.width = text.style.width = `${textarea.clientWidth}px`;
        Object.assign(decorations.style, {
            left: `${textarea.offsetLeft + textarea.clientLeft}px`, top: `${textarea.offsetTop + textarea.clientTop}px`,
            width: `${textarea.clientWidth}px`, height: `${textarea.clientHeight}px`, borderRadius: style.borderRadius,
        });
    }

    function replace(range, replacement) {
        if (textarea.value !== state.value || textarea.value.slice(range.start, range.end) !== range.text) {
            sync();
            hide();
            return;
        }
        hide();
        textarea.focus();
        textarea.setSelectionRange(range.start, range.end);
        // Native insertion keeps replacements and removals in the textarea's undo history.
        if (!doc.execCommand("insertText", false, replacement)) {
            textarea.setRangeText(replacement, range.start, range.end, "end");
            textarea.dispatchEvent(new Event("input", { bubbles: true }));
        }
        sync();
        hide();
    }

    function decorate(tags) {
        hidePreview();
        text.replaceChildren();
        references = [];
        let end = 0;
        for (const match of textarea.value.matchAll(/(?<![\p{L}\p{N}_@])@(image|video|audio)(\p{Nd}+)(?![\p{L}\p{N}_])/gu)) {
            text.appendChild(doc.createTextNode(textarea.value.slice(end, match.index)));
            const range = { text: match[0], start: match.index, end: match.index + match[0].length };
            const present = tags.includes(range.text);
            const color = present ? "#60a5fa" : "#f87171";
            const background = `color-mix(in srgb, var(--comfy-input-bg, #222) 85%, ${color})`;
            const tag = doc.createElement("span");
            Object.assign(tag.style, {
                position: "relative", color, background, borderRadius: "3px",
                boxShadow: `0 0 0 1px ${color}55`,
            });
            const label = doc.createElement("span");
            label.textContent = range.text;
            label.setAttribute("role", "button");
            label.tabIndex = 0;
            label.setAttribute("aria-label", `${range.text}, ${present ? "connected" : "not connected"}. Replace reference`);
            Object.assign(label.style, { cursor: "pointer", pointerEvents: "auto" });
            function open() {
                hidePreview();
                textarea.focus();
                textarea.setSelectionRange(range.end, range.end);
                state.target = range;
                state.suppressedPrefix = "";
                refresh();
            }
            label.onpointerdown = (event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                event.stopPropagation();
                open();
            };
            label.onclick = (event) => event.stopPropagation();
            label.onpointerenter = () => showPreview(label, range.text, remove);
            label.onpointerleave = () => { if (doc.activeElement !== label) hidePreview(); };
            label.onfocus = () => showPreview(label, range.text, remove);
            label.onblur = hidePreview;
            label.onkeydown = (event) => {
                if (event.key === "Escape") hidePreview();
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                event.stopPropagation();
                open();
            };
            const remove = doc.createElement("button");
            remove.type = "button";
            remove.textContent = "×";
            remove.title = `Remove ${range.text}`;
            remove.setAttribute("aria-label", remove.title);
            Object.assign(remove.style, {
                position: "absolute", right: "-4px", top: "-7px", width: "9px", height: "9px",
                padding: "0", margin: "0", border: "0", borderRadius: "50%", color, background,
                font: "10px/9px sans-serif", textAlign: "center", cursor: "pointer", pointerEvents: "auto",
                boxShadow: `0 0 0 1px ${color}55`,
            });
            remove.onpointerdown = (event) => {
                event.preventDefault();
                event.stopPropagation();
            };
            remove.onclick = (event) => {
                event.stopPropagation();
                replace(range, "");
            };
            tag.append(label, remove);
            text.appendChild(tag);
            references.push({ range, tag, background });
            end = range.end;
        }
        text.appendChild(doc.createTextNode(textarea.value.slice(end) + "\u200b"));
    }

    function sync() {
        layout();
        const tags = connectedTags(node);
        const signature = tags.join(",");
        if (state.value !== textarea.value || state.tagSignature !== signature) {
            if (state.value !== textarea.value) state.target = null;
            state.value = textarea.value;
            state.tagSignature = signature;
            decorate(tags);
        }
        text.style.transform = `translate(${-textarea.scrollLeft}px, ${-textarea.scrollTop}px)`;
        decorations.style.visibility = textarea.selectionStart === textarea.selectionEnd ? "visible" : "hidden";
        for (const { range, tag, background } of references) {
            const editing = doc.activeElement === textarea && textarea.selectionStart >= range.start && textarea.selectionStart <= range.end;
            tag.style.background = editing ? "transparent" : background;
        }
        if (state.visible) refresh();
        if (hovered) showPreview(hovered.label, hovered.tag, hovered.remove);
    }

    function hide() {
        const prefix = prefixAtCaret(textarea);
        state.suppressedPrefix = prefix ? `${prefix.start}:${prefix.end}:${prefix.text}` : "";
        state.visible = false;
        state.target = null;
        state.key = "";
        popup.style.display = "none";
        clearThumbnails();
        popup.replaceChildren();
        textarea.setAttribute("aria-expanded", "false");
        textarea.removeAttribute("aria-activedescendant");
    }

    function position() {
        if (!textarea.parentElement || !state.prefix || !state.visible) return;
        const style = getComputedStyle(textarea);
        mirror.textContent = textarea.value.slice(0, state.prefix.end);
        const marker = doc.createElement("span");
        marker.textContent = "\u200b";
        mirror.appendChild(marker);
        const lineHeight = Number.parseFloat(style.lineHeight) || Number.parseFloat(style.fontSize) * 1.4 || 18;
        const width = Math.min(220, textarea.clientWidth);
        popup.style.width = `${width}px`;
        popup.style.maxHeight = `${Math.min(160, textarea.clientHeight)}px`;
        const left = textarea.offsetLeft + textarea.clientLeft;
        const top = textarea.offsetTop + textarea.clientTop;
        const caretY = marker.offsetTop - textarea.scrollTop;
        const height = popup.offsetHeight;
        const below = caretY + lineHeight;
        const y = below + height <= textarea.clientHeight ? below : caretY - height;
        popup.style.left = `${left + Math.max(0, Math.min(marker.offsetLeft - textarea.scrollLeft, textarea.clientWidth - width))}px`;
        popup.style.top = `${top + Math.max(0, Math.min(y, textarea.clientHeight - height))}px`;
    }

    function highlight() {
        for (const [index, option] of [...popup.children].entries()) {
            const selected = index === state.active;
            option.setAttribute("aria-selected", String(selected));
            option.style.background = selected ? "var(--comfy-menu-bg, #444)" : "transparent";
        }
        const selected = popup.children[state.active];
        if (selected) {
            textarea.setAttribute("aria-activedescendant", selected.id);
            if (selected.offsetTop < popup.scrollTop) popup.scrollTop = selected.offsetTop;
            else if (selected.offsetTop + selected.offsetHeight > popup.scrollTop + popup.clientHeight) {
                popup.scrollTop = selected.offsetTop + selected.offsetHeight - popup.clientHeight;
            }
        }
    }

    function choose(index) {
        const prefix = state.target ?? prefixAtCaret(textarea);
        const tag = state.items[index];
        if (!prefix || !tag || !connectedTags(node).includes(tag)) {
            hide();
            return;
        }
        replace(prefix, tag);
    }

    function refresh() {
        if (doc.activeElement !== textarea || state.composing) {
            hide();
            return;
        }
        const prefix = state.target ?? prefixAtCaret(textarea);
        if (!state.target && prefix && `${prefix.start}:${prefix.end}:${prefix.text}` === state.suppressedPrefix) return;
        const tags = connectedTags(node);
        const items = prefix ? tags.filter((tag) => state.target ? tag !== prefix.text : tag.startsWith(prefix.text.toLowerCase())) : [];
        if (!prefix || !items.length) {
            hide();
            return;
        }
        const media = items.map((tag) => resolveMediaPreview(node, tag));
        const key = `${prefix.start}:${prefix.end}:${items.join(",")}:${JSON.stringify(media)}`;
        state.prefix = prefix;
        if (key !== state.key) {
            state.key = key;
            state.items = items;
            state.active = 0;
            clearThumbnails();
            popup.replaceChildren();
            for (const [index, tag] of items.entries()) {
                const option = doc.createElement("div");
                option.id = `${popup.id}-${index}`;
                const thumbnail = createMediaPreview(doc, media[index]);
                thumbnails.push(thumbnail);
                thumbnail.element.setAttribute("aria-hidden", "true");
                const label = doc.createElement("span");
                label.textContent = tag;
                option.append(thumbnail.element, label);
                option.title = media[index].title;
                option.setAttribute("role", "option");
                Object.assign(option.style, { padding: "5px 8px", cursor: "pointer", display: "flex", alignItems: "center", gap: "8px" });
                option.onpointerdown = (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    choose(index);
                };
                popup.appendChild(option);
            }
            highlight();
        }
        state.visible = true;
        popup.style.display = "block";
        textarea.setAttribute("aria-expanded", "true");
        position();
    }

    function keydown(event) {
        if (event.isComposing || !state.visible) return;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            event.stopPropagation();
            state.active = (state.active + (event.key === "ArrowDown" ? 1 : -1) + state.items.length) % state.items.length;
            highlight();
        } else if (event.key === "Enter" || event.key === "Tab") {
            event.preventDefault();
            event.stopPropagation();
            choose(state.active);
        } else if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            hide();
        } else {
            hide();
        }
    }

    function userRefresh() {
        state.target = null;
        state.suppressedPrefix = "";
        sync();
        refresh();
    }
    function compositionStart() {
        state.composing = true;
        hide();
    }
    function compositionEnd() {
        state.composing = false;
        userRefresh();
    }
    for (const [type, listener] of [["input", userRefresh], ["click", userRefresh], ["keyup", () => { sync(); refresh(); }], ["keydown", keydown], ["scroll", sync], ["select", sync], ["focus", sync], ["blur", () => { hide(); sync(); }], ["compositionstart", compositionStart], ["compositionend", compositionEnd]]) {
        textarea.addEventListener(type, listener);
        state.listeners.push([type, listener]);
    }
    state.sync = sync;
    const dismissPreview = () => hidePreview();
    view.addEventListener("blur", dismissPreview);
    doc.addEventListener("scroll", dismissPreview, true);
    state.cleanupPreviews = () => {
        hidePreview();
        clearThumbnails();
        tooltip.remove();
        view.removeEventListener("blur", dismissPreview);
        doc.removeEventListener("scroll", dismissPreview, true);
    };
    state.attributes = attributes;
    state.resizeObserver = new ResizeObserver(sync);
    state.resizeObserver.observe(textarea);
    sync();
    refresh();
}

export function removeTagSuggestions(widget) {
    const state = widget[suggestionsState];
    if (!state) return;
    for (const [type, listener] of state.listeners) state.textarea.removeEventListener(type, listener);
    for (const [name, value] of state.attributes) {
        if (value === null) state.textarea.removeAttribute(name);
        else state.textarea.setAttribute(name, value);
    }
    state.resizeObserver.disconnect();
    state.cleanupPreviews();
    for (const [name, value] of state.styles) state.textarea.style[name] = value;
    state.popup.remove();
    state.mirror.remove();
    state.decorations.remove();
    delete widget[suggestionsState];
}
