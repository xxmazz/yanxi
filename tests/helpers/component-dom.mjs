// 隔离 DOM 组件回归：不启动浏览器、不访问服务器、不操作鼠标或系统界面。
import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";

const project = fileURLToPath(new URL("../../", import.meta.url));
const temporary = path.join(project, "runtime", "component-tests");
await mkdir(temporary, { recursive: true });
const directory = await mkdtemp(path.join(temporary, "run-"));
const bundle = await build({
  stdin: {
    contents: `export { default as App } from "./src/App.tsx";
export { ResearchTasks } from "./src/research-tasks.tsx";
export { FutureCheckPanel } from "./src/future-check.tsx";
export { PageStructure } from "./src/page-structure.tsx";
export { ReferenceMatching } from "./src/reference-matching.tsx";
export { ReadingLinks } from "./src/reading-links.tsx";
export { hoverRanges } from "./src/hover-translation.tsx";
export { FollowingPanel } from "./src/following.tsx";
export { WritingWorkspace } from "./src/writing-workspace.tsx";`,
    resolveDir: project,
  },
  bundle: true,
  platform: "node",
  format: "esm",
  packages: "external",
  jsx: "automatic",
  write: false,
});
const filename = path.join(directory, "components.mjs");
await writeFile(filename, bundle.outputFiles[0].contents);

// ReactDOM checks DOM event support at import time; set up the DOM first.
const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://yanxi-component.test/",
});
for (const name of [
  "window",
  "document",
  "localStorage",
  "HTMLElement",
  "HTMLInputElement",
  "HTMLTextAreaElement",
  "HTMLSelectElement",
  "Event",
  "MouseEvent",
  "XMLSerializer",
]) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    value: dom.window[name],
  });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
dom.window.HTMLElement.prototype.scrollTo = function () {};
const { createRoot } = await import("react-dom/client");
export const components = await import(pathToFileURL(filename).href);

export async function disposeEnvironment() {
  dom.window.close();
  await rm(directory, { recursive: true, force: true });
}

export function setup() {
  document.body.replaceChildren();
  localStorage.clear();
  const calls = [];
  const unexpected = [];
  const routes = new Map();
  const downloads = [];
  const blobs = new Map();
  const originalFetch = globalThis.fetch;
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const originalClick = dom.window.HTMLAnchorElement.prototype.click;
  let objectId = 0;
  routes.set("GET /api/health", { status: "ok", model_ready: true });
  routes.set("GET /api/documents", []);
  routes.set("GET /api/documents?include_archived=true", []);
  routes.set("GET /api/discovery/jobs", []);
  routes.set("GET /api/research/tasks", []);
  routes.set("GET /api/following", { subscriptions: [], batches: [] });
  globalThis.fetch = async (url, init = {}) => {
    // No fallback to real fetch: an unstubbed request is a test failure.
    const request = {
      url: String(url),
      method: init.method || "GET",
      headers: new Headers(init.headers),
      body: init.body ? JSON.parse(init.body) : null,
    };
    calls.push(request);
    const route = routes.get(`${request.method} ${request.url}`);
    if (!route) {
      unexpected.push(`${request.method} ${request.url}`);
      throw new Error(
        "Unstubbed request; network disabled for component tests",
      );
    }
    const result = typeof route === "function" ? await route(request) : route;
    return result instanceof Response ? result : Response.json(result);
  };
  URL.createObjectURL = (blob) => {
    const id = `blob:component-test-${++objectId}`;
    blobs.set(id, blob);
    return id;
  };
  URL.revokeObjectURL = (id) => blobs.delete(id);
  dom.window.HTMLAnchorElement.prototype.click = function () {
    downloads.push({ name: this.download, blob: blobs.get(this.href) });
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  return {
    calls,
    routes,
    downloads,
    container,
    async render(Component, props = {}) {
      await act(async () => root.render(createElement(Component, props)));
    },
    async cleanup() {
      await act(async () => root.unmount());
      globalThis.fetch = originalFetch;
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
      dom.window.HTMLAnchorElement.prototype.click = originalClick;
      assert.deepEqual(unexpected, [], "component made unstubbed requests");
    },
  };
}

export function button(text, scope = document) {
  const matches = [...scope.querySelectorAll("button")].filter((item) =>
    text instanceof RegExp
      ? text.test(item.textContent.trim())
      : item.textContent.trim() === text,
  );
  assert.equal(matches.length, 1, `expected one button: ${text}`);
  return matches[0];
}

export async function click(element) {
  assert.ok(element, "click target exists");
  assert.equal(element.disabled, false, "click target is enabled");
  await act(async () => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

export async function change(element, value) {
  assert.ok(element, "input exists");
  const setter = Object.getOwnPropertyDescriptor(
    Object.getPrototypeOf(element),
    "value",
  ).set;
  await act(async () => {
    setter.call(element, value);
    element.dispatchEvent(
      new Event(element.tagName === "SELECT" ? "change" : "input", {
        bubbles: true,
      }),
    );
  });
}

export async function submit(form) {
  assert.ok(form, "form exists");
  await act(async () => {
    form.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
  });
}

export async function importFile(input, file) {
  Object.defineProperty(input, "files", {
    configurable: true,
    value: [file],
  });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

export { act };
