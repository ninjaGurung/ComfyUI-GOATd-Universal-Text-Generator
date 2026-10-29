// Author: ninjaGurung
// GitHub: https://github.com/ninjaGurung

import { api } from "../../../scripts/api.js";
import { app } from "../../../scripts/app.js";

function viewURL(file, type = "input") {
    const annotated = / \[(input|output|temp)\]$/.exec(file);
    if (annotated) {
        type = annotated[1];
        file = file.slice(0, annotated.index);
    }
    const path = file.replaceAll("\\", "/");
    const slash = path.lastIndexOf("/");
    return api.apiURL(`/view?${new URLSearchParams({
        filename: path.slice(slash + 1), subfolder: path.slice(0, Math.max(0, slash)), type,
    })}`);
}

function localURL(value) {
    if (typeof value !== "string" || !value) return null;
    const base = new URL(globalThis.location.href);
    // Reuse browser-owned previews without introducing requests to external servers.
    if (/^data:image\/(?:png|jpeg|webp|gif|avif|bmp);/i.test(value)) return value;
    if (value.startsWith(`blob:${base.origin}/`)) return value;
    try {
        const url = new URL(value, base);
        return url.origin === base.origin && /^(https?:)$/.test(url.protocol) ? url.href : null;
    } catch (error) {
        if (error instanceof TypeError) return null;
        throw error;
    }
}

function mediaType(value) {
    const url = new URL(value, globalThis.location.href);
    return /\.(?:mp4|webm|mov|m4v|ogv|mkv)$/i.test(url.searchParams.get("filename") || url.pathname) ? "video" : "image";
}

export function resolveMediaPreview(node, tag) {
    const match = /^@(image|video|audio)(\d+)$/.exec(tag);
    const kind = match?.[1] ?? "image";
    const result = { kind, title: `${tag}: preview unavailable` };
    const index = match ? node.inputs?.findIndex((input) =>
        input.name === `${kind}${match[2]}` || input.name === `${kind}s.${kind}${match[2]}`) ?? -1 : -1;
    if (index < 0 || !node.graph || !node.isInputConnected(index)) return result;
    let source = node.getInputNode(index);
    let link = node.getInputLink(index);
    const seen = new Set();
    let nested = false;
    while (source && !seen.has(source) && seen.size < 64) {
        seen.add(source);
        if (source.type === "Reroute") {
            link = source.getInputLink(0);
            source = source.getInputNode(0);
        } else if (source.isSubgraphNode?.() && link) {
            nested = true;
            const resolved = source.resolveSubgraphOutputLink(link.origin_slot);
            source = resolved?.outputNode;
            link = resolved?.link;
        } else break;
    }
    if (!source || source.type === "Reroute" || source.isSubgraphNode?.()) return result;
    result.title = `${tag}: ${source.title || source.type || kind}`;
    if (kind === "audio") return result;

    const vhs = source.widgets?.find((widget) => widget.name === "videopreview");
    const video = !vhs?.videoEl?.hidden && localURL(vhs?.videoEl?.currentSrc || vhs?.videoEl?.src);
    if (video) return { ...result, url: video, mediaType: "video" };
    const vhsImage = !vhs?.imgEl?.hidden && localURL(vhs?.imgEl?.currentSrc || vhs?.imgEl?.src);
    if (vhsImage) return { ...result, url: vhsImage, mediaType: "image" };
    const loader = {
        LoadImage: ["image", "input", "image"],
        LoadImageOutput: ["image", "output", "image"],
        LoadVideo: ["file", "input", "video"],
        VHS_LoadVideo: ["video", "input", "video"],
    }[source.type];
    const file = loader && source.widgets?.find((widget) => widget.name === loader[0])?.value;
    const fileInput = loader ? source.inputs?.findIndex((input) => input.name === loader[0]) ?? -1 : -1;
    if (typeof file === "string" && file && !(fileInput >= 0 && source.isInputConnected(fileInput))) {
        return { ...result, title: `${result.title}: ${file}`, url: viewURL(file, loader[1]), mediaType: loader[2] };
    }
    const image = localURL(source.imgs?.[0]?.currentSrc || source.imgs?.[0]?.src);
    if (image) return { ...result, url: image, mediaType: mediaType(image) };
    if (nested || (source.graph && source.graph !== node.graph)) return result;
    const output = app.nodeOutputs?.[source.id]?.images?.[0];
    if (output?.filename && ["input", "output", "temp"].includes(output.type)) {
        const url = viewURL(`${output.subfolder ? `${output.subfolder}/` : ""}${output.filename}`, output.type);
        return { ...result, url, mediaType: mediaType(url) };
    }
    return result;
}

export function createMediaPreview(doc, preview, size = 40) {
    const element = doc.createElement("span");
    element.setAttribute("role", "img");
    element.setAttribute("aria-label", preview.title);
    Object.assign(element.style, {
        display: "inline-flex", position: "relative", flexShrink: "0", alignItems: "center", justifyContent: "center",
        width: `${size}px`, height: `${size}px`, overflow: "hidden", borderRadius: "4px",
        background: "var(--comfy-menu-bg, #333)", color: "var(--input-text, #ddd)", pointerEvents: "none",
    });
    const symbol = doc.createElement("span");
    symbol.textContent = preview.kind === "audio" ? "▂▅▇▃▆▂" : preview.kind === "video" ? "▶" : "▧";
    symbol.setAttribute("aria-hidden", "true");
    Object.assign(symbol.style, { font: `${Math.min(size / 2, 24)}px sans-serif`, letterSpacing: "-1px" });
    element.appendChild(symbol);
    let media;
    let observer;
    let disposed = false;
    if (preview.url && preview.kind !== "audio") {
        const video = preview.mediaType === "video";
        media = doc.createElement(video ? "video" : "img");
        media.setAttribute("aria-hidden", "true");
        Object.assign(media.style, { position: "absolute", width: "100%", height: "100%", objectFit: "contain", visibility: "hidden" });
        const show = () => { if (!disposed) { media.style.visibility = "visible"; symbol.style.visibility = "hidden"; } };
        media.onerror = () => { if (!disposed) { media.style.visibility = "hidden"; symbol.style.visibility = "visible"; } };
        element.appendChild(media);
        if (video) {
            media.muted = true;
            media.playsInline = true;
            media.preload = "metadata";
            media.onloadedmetadata = () => { if (!disposed) media.currentTime = 0; };
            media.onloadeddata = show;
            const Observer = doc.defaultView?.IntersectionObserver;
            if (Observer) {
                observer = new Observer((entries) => {
                    if (!disposed && entries.some((entry) => entry.isIntersecting)) {
                        media.src = preview.url;
                        observer.disconnect();
                    }
                });
                observer.observe(element);
            } else media.src = preview.url;
        } else {
            media.loading = "lazy";
            media.decoding = "async";
            media.onload = show;
            media.src = preview.url;
        }
    }
    return { element, dispose() {
        disposed = true;
        observer?.disconnect();
        if (!media) return;
        media.onload = media.onerror = media.onloadedmetadata = media.onloadeddata = null;
        if (preview.mediaType === "video") media.pause();
        media.removeAttribute("src");
        if (preview.mediaType === "video") media.load();
    } };
}
