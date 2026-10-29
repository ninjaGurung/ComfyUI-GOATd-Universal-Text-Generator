import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";

function setup() {
    const app = { nodeOutputs: {} };
    const source = readFileSync(new URL("../web/media_preview.js", import.meta.url), "utf8")
        .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");
    const scope = { app, api: { apiURL: (path) => `/comfy${path}` }, URL, URLSearchParams, TypeError,
        location: { href: "http://localhost:8188/comfy/" } };
    vm.createContext(scope);
    vm.runInContext(source, scope);
    function node(source, kind = "image") {
        return { graph: {}, inputs: [{ name: `${kind}s.${kind}1` }], isInputConnected: () => true,
            getInputNode: () => source, getInputLink: () => ({ origin_slot: 0 }) };
    }
    return { ...scope, node };
}

test("native filenames take precedence over stale previews and preserve annotations", () => {
    const { resolveMediaPreview, node } = setup();
    for (const [type, name, value, kind, folder] of [
        ["LoadImage", "image", "refs/a & b.png", "image", "input"],
        ["LoadImageOutput", "image", "render.png", "image", "output"],
        ["LoadImage", "image", "temp\\frame.png [temp]", "image", "temp"],
        ["LoadVideo", "file", "clip one.mp4", "video", "input"],
    ]) {
        const result = resolveMediaPreview(node({ type, widgets: [{ name, value }], imgs: [{ src: "/stale.png" }] }, kind), `@${kind}1`);
        const url = new URL(result.url, "http://localhost:8188");
        assert.equal(url.pathname, "/comfy/view");
        assert.equal(url.searchParams.get("type"), folder);
        assert.equal(url.searchParams.get("filename"), value.replace(/ \[temp\]$/, "").split(/[\\/]/).at(-1));
        assert.equal(result.mediaType, kind);
    }
});

test("only the tagged connected input resolves, with safe disconnected/audio fallbacks", () => {
    const { resolveMediaPreview, node } = setup();
    const source = { type: "LoadImage", widgets: [{ name: "image", value: "a.png" }] };
    assert.equal(resolveMediaPreview(node(source), "@image2").url, undefined);
    const disconnected = node(source);
    disconnected.graph = null;
    disconnected.getInputNode = () => { throw Error("must not call detached graph"); };
    assert.equal(resolveMediaPreview(disconnected, "@image1").url, undefined);
    assert.equal(resolveMediaPreview(node(source, "audio"), "@audio1").kind, "audio");
    assert.equal(resolveMediaPreview(node(source, "audio"), "@audio1").url, undefined);
});

test("explicit reroutes and subgraph outputs resolve without following transforms or cycles", () => {
    const { resolveMediaPreview, node } = setup();
    const source = { type: "LoadImage", widgets: [{ name: "image", value: "a.png" }] };
    const reroute = { type: "Reroute", getInputNode: () => source, getInputLink: () => ({ origin_slot: 0 }) };
    assert.ok(resolveMediaPreview(node(reroute), "@image1").url);
    const subgraph = { isSubgraphNode: () => true, resolveSubgraphOutputLink: () => ({ outputNode: source, link: {} }) };
    assert.ok(resolveMediaPreview(node(subgraph), "@image1").url);
    const transform = { type: "ImageScale", getInputNode: () => source };
    assert.equal(resolveMediaPreview(node(transform), "@image1").url, undefined);
    reroute.getInputNode = () => reroute;
    assert.equal(resolveMediaPreview(node(reroute), "@image1").url, undefined);
});

test("reuse local previews and execution outputs while rejecting external media URLs", () => {
    const { resolveMediaPreview, node, app } = setup();
    for (const src of ["https://example.com/secret.png", "//example.com/secret.png", "javascript:alert(1)", "http://["]) {
        assert.equal(resolveMediaPreview(node({ imgs: [{ src }] }), "@image1").url, undefined);
    }
    assert.equal(resolveMediaPreview(node({ imgs: [{ src: "/view?filename=a.png" }] }), "@image1").url,
        "http://localhost:8188/view?filename=a.png");
    assert.equal(resolveMediaPreview(node({ imgs: [{ src: "blob:http://localhost:8188/id" }] }), "@image1").mediaType, "image");
    app.nodeOutputs[3] = { images: [{ filename: "preview.mp4", type: "temp", subfolder: "video" }], animated: [true] };
    const result = resolveMediaPreview(node({ id: 3 }, "video"), "@video1");
    assert.equal(result.mediaType, "video");
    assert.match(result.url, /filename=preview.mp4/);
    const vhs = { widgets: [{ name: "videopreview", videoEl: { src: "/vhs/viewvideo?filename=a.mov&force_rate=8" } }] };
    assert.equal(resolveMediaPreview(node(vhs, "video"), "@video1").url,
        "http://localhost:8188/vhs/viewvideo?filename=a.mov&force_rate=8");
});

function documentMock() {
    class Element {
        constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.attributes = {}; }
        setAttribute(name, value) { this.attributes[name] = value; }
        removeAttribute(name) { delete this[name]; }
        appendChild(child) { this.children.push(child); }
        pause() { this.paused = true; }
        load() { this.released = true; }
    }
    const observers = [];
    class IntersectionObserver {
        constructor(callback) { this.callback = callback; observers.push(this); }
        observe(element) { this.element = element; }
        disconnect() { this.disconnected = true; }
    }
    return { createElement: (tag) => new Element(tag), defaultView: { IntersectionObserver }, observers };
}

test("video first frame loads only when visible, never autoplays, and releases resources", () => {
    const { createMediaPreview } = setup();
    const doc = documentMock();
    const preview = createMediaPreview(doc, { kind: "video", mediaType: "video", url: "/view?filename=a.mp4", title: "@video1" });
    const video = preview.element.children[1];
    assert.equal(video.src, undefined);
    assert.equal(video.muted, true);
    assert.equal(video.autoplay, undefined);
    doc.observers[0].callback([{ isIntersecting: true }]);
    assert.equal(video.src, "/view?filename=a.mp4");
    video.onloadedmetadata();
    assert.equal(video.currentTime, 0);
    video.onloadeddata();
    assert.equal(video.style.visibility, "visible");
    const lateCallback = video.onloadeddata;
    preview.dispose();
    assert.equal(video.src, undefined);
    assert.equal(video.released, true);
    assert.equal(video.paused, true);
    assert.equal(doc.observers[0].disconnected, true);
    assert.equal(video.onloadeddata, null);
    lateCallback();
});

test("audio is a wave symbol without media requests; image errors retain a fallback", () => {
    const { createMediaPreview } = setup();
    const doc = documentMock();
    const audio = createMediaPreview(doc, { kind: "audio", url: "/unneeded.mp3", title: "@audio1" });
    assert.equal(audio.element.children.length, 1);
    assert.equal(audio.element.children[0].textContent, "▂▅▇▃▆▂");
    audio.dispose();
    const image = createMediaPreview(doc, { kind: "image", mediaType: "image", url: "/view?filename=a.png", title: "@image1" });
    const img = image.element.children[1];
    img.onload();
    assert.equal(img.style.visibility, "visible");
    img.onerror();
    assert.equal(img.style.visibility, "hidden");
    assert.equal(image.element.children[0].style.visibility, "visible");
    image.dispose();
    assert.equal(img.onload, null);
});


test("animated image outputs remain images and image URL previews can identify video files", () => {
    const { resolveMediaPreview, node, app } = setup();
    for (const filename of ["preview.gif", "preview.webp", "preview.apng"]) {
        app.nodeOutputs[7] = { images: [{ filename, type: "temp" }], animated: [true] };
        assert.equal(resolveMediaPreview(node({ id: 7 }), "@image1").mediaType, "image");
    }
    const result = resolveMediaPreview(node({ imgs: [{ src: "/view?filename=first.mp4&type=temp" }] }, "video"), "@video1");
    assert.equal(result.mediaType, "video");
});

test("VHS preview honors the active format and takes precedence over raw loader files", () => {
    const { resolveMediaPreview, node } = setup();
    const source = { type: "VHS_LoadVideo", widgets: [
        { name: "video", value: "raw.mp4" },
        { name: "videopreview", videoEl: { hidden: false, src: "/vhs/viewvideo?filename=raw.mp4&skip_first_frames=8" },
            imgEl: { hidden: true, src: "/view?filename=old.webp" } },
    ] };
    assert.match(resolveMediaPreview(node(source, "video"), "@video1").url, /skip_first_frames=8/);
    source.widgets[1].videoEl.hidden = true;
    source.widgets[1].imgEl.hidden = false;
    const result = resolveMediaPreview(node(source, "video"), "@video1");
    assert.match(result.url, /old.webp/);
    assert.equal(result.mediaType, "image");
});

test("nested local node ids never reuse unrelated root execution previews", () => {
    const { resolveMediaPreview, node, app } = setup();
    app.nodeOutputs[4] = { images: [{ filename: "unrelated.png", type: "temp" }] };
    const child = { id: 4, type: "ImageScale" };
    const subgraph = { isSubgraphNode: () => true, resolveSubgraphOutputLink: () => ({ outputNode: child, link: {} }) };
    assert.equal(resolveMediaPreview(node(subgraph), "@image1").url, undefined);
    child.graph = {};
    assert.equal(resolveMediaPreview(node(child), "@image1").url, undefined);
});

test("converted loader filename inputs do not expose the stale widget file", () => {
    const { resolveMediaPreview, node } = setup();
    const source = { type: "LoadVideo", inputs: [{ name: "file" }], isInputConnected: () => true,
        widgets: [{ name: "file", value: "stale.mp4" }] };
    assert.equal(resolveMediaPreview(node(source, "video"), "@video1").url, undefined);
});

test("execution video filenames containing URL punctuation retain video classification", () => {
    const { resolveMediaPreview, node, app } = setup();
    for (const filename of ["clip#1.mp4", "clip?take.mp4"]) {
        app.nodeOutputs[8] = { images: [{ filename, type: "temp" }] };
        assert.equal(resolveMediaPreview(node({ id: 8 }, "video"), "@video1").mediaType, "video");
    }
});
