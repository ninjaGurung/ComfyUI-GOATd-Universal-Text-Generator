// Author: ninjaGurung
// GitHub: https://github.com/ninjaGurung

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
    const textarea = widget.element;
    if (!textarea || textarea.readOnly) return;
    let state = widget[suggestionsState];
    if (state && state.textarea !== textarea) {
        removeTagSuggestions(widget);
        state = null;
    }
    if (state) {
        if (state.visible && connectedTags(node).join(",") !== state.tagSignature) state.refresh();
        else if (state.visible && textarea.parentElement !== state.parent) state.position();
        return;
    }

    const doc = textarea.ownerDocument;
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
    const attributes = new Map(["aria-autocomplete", "aria-controls", "aria-expanded", "aria-activedescendant"]
        .map((name) => [name, textarea.getAttribute(name)]));
    textarea.setAttribute("aria-autocomplete", "list");
    textarea.setAttribute("aria-controls", popup.id);
    textarea.setAttribute("aria-expanded", "false");

    state = { textarea, popup, mirror, items: [], active: 0, prefix: null, visible: false, key: "", suppressedPrefix: "", listeners: [] };
    widget[suggestionsState] = state;

    function hide() {
        const prefix = prefixAtCaret(textarea);
        state.suppressedPrefix = prefix ? `${prefix.start}:${prefix.end}:${prefix.text}` : "";
        state.visible = false;
        state.key = "";
        popup.style.display = "none";
        textarea.setAttribute("aria-expanded", "false");
        textarea.removeAttribute("aria-activedescendant");
    }

    function position() {
        const parent = textarea.parentElement;
        if (!parent || !state.prefix || !state.visible) return;
        state.parent = parent;
        if (popup.parentElement !== parent) parent.appendChild(popup);
        if (mirror.parentElement !== parent) parent.appendChild(mirror);
        const style = getComputedStyle(textarea);
        for (const name of ["fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight", "letterSpacing", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "tabSize"]) {
            mirror.style[name] = style[name];
        }
        mirror.style.width = `${textarea.clientWidth}px`;
        mirror.textContent = textarea.value.slice(0, state.prefix.end);
        const marker = doc.createElement("span");
        marker.textContent = "\u200b";
        mirror.appendChild(marker);
        const lineHeight = Number.parseFloat(style.lineHeight) || Number.parseFloat(style.fontSize) * 1.4 || 18;
        const width = Math.min(180, textarea.clientWidth);
        popup.style.width = `${width}px`;
        popup.style.maxHeight = `${Math.min(160, textarea.clientHeight)}px`;
        const left = textarea.offsetLeft;
        const top = textarea.offsetTop;
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
        const prefix = prefixAtCaret(textarea);
        const tag = state.items[index];
        if (!prefix || !tag || !connectedTags(node).includes(tag)) {
            hide();
            return;
        }
        textarea.setRangeText(tag, prefix.start, prefix.end, "end");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        textarea.focus();
        hide();
    }

    function refresh() {
        if (doc.activeElement !== textarea) {
            hide();
            return;
        }
        const prefix = prefixAtCaret(textarea);
        if (prefix && `${prefix.start}:${prefix.end}:${prefix.text}` === state.suppressedPrefix) return;
        const tags = connectedTags(node);
        state.tagSignature = tags.join(",");
        const items = prefix ? tags.filter((tag) => tag.startsWith(prefix.text.toLowerCase())) : [];
        if (!prefix || !items.length) {
            hide();
            return;
        }
        const key = `${prefix.start}:${prefix.end}:${items.join(",")}`;
        state.prefix = prefix;
        if (key !== state.key) {
            state.key = key;
            state.items = items;
            state.active = 0;
            popup.replaceChildren();
            for (const [index, tag] of items.entries()) {
                const option = doc.createElement("div");
                option.id = `${popup.id}-${index}`;
                option.textContent = tag;
                option.setAttribute("role", "option");
                Object.assign(option.style, { padding: "5px 8px", cursor: "pointer" });
                option.onpointerdown = (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    choose(index);
                };
                popup.appendChild(option);
            }
        }
        state.visible = true;
        popup.style.display = "block";
        textarea.setAttribute("aria-expanded", "true");
        position();
        highlight();
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
        }
    }

    function userRefresh() {
        state.suppressedPrefix = "";
        refresh();
    }
    for (const [type, listener] of [["input", userRefresh], ["click", userRefresh], ["keyup", refresh], ["keydown", keydown], ["scroll", position], ["blur", hide]]) {
        textarea.addEventListener(type, listener);
        state.listeners.push([type, listener]);
    }
    state.refresh = refresh;
    state.position = position;
    state.attributes = attributes;
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
    state.popup.remove();
    state.mirror.remove();
    delete widget[suggestionsState];
}
