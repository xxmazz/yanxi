// 真实Chromium、官方arXiv和隔离GPU库；不使用API替身或桌面自动化。
import fs from "node:fs/promises";
import assert from "node:assert/strict";
import { parseArgs } from "node:util";
const { values: args } = parseArgs({
  options: {
    config: { type: "string" },
    output: { type: "string" },
    "playwright-module": { type: "string", default: "playwright" },
    executable: { type: "string" },
  },
});
assert(args.config && args.output, "需要 --config 私有JSON 与 --output 新目录");
const config = JSON.parse(await fs.readFile(args.config, "utf8"));
const base = config.base_url.replace(/\/$/, "");
const url = new URL(base);
assert(
  config.isolatedLibrary === true &&
    ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) &&
    !["", "18474"].includes(url.port) &&
    url.pathname === "/" &&
    !url.username &&
    !url.password &&
    !url.search &&
    !url.hash,
  "只允许明确隔离的非生产回环实例",
);
await fs.mkdir(args.output);
const root = args.output;
const { chromium } = await import(args["playwright-module"]);
const browser = await chromium.launch({
  headless: true,
  ...(args.executable ? { executablePath: args.executable } : {}),
});
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
  ...(config.username
    ? {
        httpCredentials: {
          username: config.username,
          password: config.password,
        },
      }
    : {}),
});
page.setDefaultTimeout(30000);
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const report = {
  date: "2026-10-30",
  browser: browser.version(),
  api_stubs: false,
  desktop_mouse: false,
  checks: [],
};
const source =
  "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697";
async function get(path) {
  const response = await page.request.get(base + path);
  assert(response.ok(), `${path}: ${response.status()}`);
  return response.json();
}
async function step(name, fn) {
  const start = Date.now();
  try {
    const details = await fn();
    report.checks.push({
      name,
      passed: true,
      seconds: (Date.now() - start) / 1000,
      ...details,
    });
    console.log("PASS", name);
  } catch (error) {
    report.checks.push({ name, passed: false, error: String(error) });
    await page.screenshot({
      path: root + `/failure-${report.checks.length}.png`,
      fullPage: true,
    });
    console.log("FAIL", name, String(error));
  }
  await fs.writeFile(root + "/LIVE.json", JSON.stringify(report, null, 2));
}
async function openReferences() {
  await page
    .locator(".sidebar")
    .getByRole("button", { name: "探索发现", exact: true })
    .click();
  await page.getByText("从参考文献批量下载", { exact: true }).click();
  await page
    .getByLabel("参考文献来源论文", { exact: true })
    .selectOption(source);
  await page.getByRole("button", { name: "读取参考文献", exact: true }).click();
  await page
    .getByRole("button", { name: "匹配前5条无编号书目", exact: true })
    .waitFor();
}
try {
  await page.goto(base);
  await page.getByText("本地模型已连接", { exact: true }).waitFor();
  report.health = await get("/api/health");
  assert(
    report.health.model_ready &&
      report.health.embedding_ready &&
      report.health.ocr_ready,
  );
  const before = await get("/api/documents");
  report.library_count_before = before.length;
  await openReferences();
  await step(
    "resolve two identifier-free bibliographies using real title metadata",
    async () => {
      const results = [];
      for (const [label, identifier] of [
        ["[11]", "1512.03385"],
        ["[20]", "1412.6980"],
      ]) {
        const existing = page
          .locator(".following-panel label")
          .filter({ hasText: `arXiv:${identifier}` })
          .locator('input[type="checkbox"]');
        if (await existing.count()) {
          results.push({ reference: label, previously_matched: true });
          continue;
        }
        const entry = page
          .locator(".reference-match-entry")
          .filter({
            has: page.locator("summary", {
              hasText: new RegExp(`^\\${label} ·`),
            }),
          });
        await entry.locator("summary").click();
        const event = page.waitForResponse(
          (r) =>
            r.url().endsWith("/reference-resolutions") &&
            r.request().method() === "POST",
          { timeout: 60000 },
        );
        await entry
          .getByRole("button", { name: "匹配此书目", exact: true })
          .click();
        const response = await event;
        assert(response.ok(), await response.text());
        const data = await response.json();
        assert.equal(data.status, "matched");
        assert.equal(data.selected.base_id, identifier);
        assert(data.selected.match.verified);
        await existing.waitFor();
        results.push({
          reference: label,
          arxiv_id: data.selected.arxiv_id,
          title: data.selected.title,
          authors: data.selected.match.matched_authors,
          reference_year: data.selected.match.reference_year,
          published_year: data.selected.match.published_year,
          cached: data.cached,
        });
      }
      await page.screenshot({
        path: root + "/matched-desktop.png",
        fullPage: true,
      });
      return { results };
    },
  );
  await step(
    "unmatched reference is never added to downloadable IDs",
    async () => {
      const entry = page
        .locator(".reference-match-entry")
        .filter({ has: page.locator("summary", { hasText: /^\[13\] ·/ }) });
      await entry.locator("summary").click();
      const event = page.waitForResponse(
        (r) => r.url().endsWith("/reference-resolutions"),
        { timeout: 60000 },
      );
      await entry.getByRole("button", { name: /匹配此书目/ }).click();
      const data = await (await event).json();
      assert.notEqual(data.status, "matched");
      const candidates = await get(
        `/api/documents/${source}/reference-candidates`,
      );
      assert(!candidates.items.some((i) => i.reference === "[13]"));
      return {
        reference: "[13]",
        status: data.status,
        candidates: data.candidates.length,
      };
    },
  );
  await step(
    "reload persists verified provenance and real user-selected batch import",
    async () => {
      await page.reload();
      await page.getByText("本地模型已连接", { exact: true }).waitFor();
      await openReferences();
      const candidates = await get(
        `/api/documents/${source}/reference-candidates`,
      );
      assert(
        candidates.items.some(
          (i) =>
            i.reference === "[11]" && i.match_method === "title-authors-year",
        ),
      );
      const ids = [];
      for (const baseId of ["1512.03385", "1412.6980"]) {
        const item = candidates.items.find(
          (i) => i.arxiv_id.replace(/v\d+$/, "") === baseId,
        );
        assert(item);
        ids.push(item.arxiv_id);
        await page
          .locator(".following-panel label")
          .filter({ hasText: `arXiv:${item.arxiv_id}` })
          .locator('input[type="checkbox"]')
          .check();
      }
      const event = page.waitForResponse((r) =>
        r.url().endsWith("/reference-batches"),
      );
      await page
        .getByRole("button", { name: "下载所选参考文献（2）", exact: true })
        .click();
      const response = await event;
      assert(response.ok(), await response.text());
      const batch = await response.json();
      let current;
      const deadline = Date.now() + 420000;
      do {
        const state = await get("/api/following");
        current = state.batches.find((b) => b.id === batch.id);
        if (!["queued", "running"].includes(current.status)) break;
        await new Promise((r) => setTimeout(r, 2000));
      } while (Date.now() < deadline);
      assert.equal(current.status, "complete", JSON.stringify(current));
      let parsed = [];
      const parseDeadline = Date.now() + 240000;
      do {
        const docs = await get("/api/documents");
        parsed = current.items.map((i) =>
          docs.find((d) => d.id === i.document_id),
        );
        if (
          parsed.every(
            (d) => d && ["ready", "review", "error"].includes(d.status),
          )
        )
          break;
        await new Promise((r) => setTimeout(r, 2000));
      } while (Date.now() < parseDeadline);
      assert(
        parsed.every((d) => d && ["ready", "review"].includes(d.status)),
        JSON.stringify(parsed),
      );
      report.library_count_after = (await get("/api/documents")).length;
      return {
        batch_id: batch.id,
        status: current.status,
        items: current.items.map((item, i) => ({
          arxiv_id: item.arxiv_id,
          document_sha256: item.document_id,
          match_method: item.match_method,
          title: parsed[i].title,
          pages: parsed[i].pages,
          document_status: parsed[i].status,
        })),
      };
    },
  );
  await step(
    "narrow-screen reference controls and metadata remain on page",
    async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth),
        390,
      );
      await page.screenshot({
        path: root + "/matched-mobile.png",
        fullPage: true,
      });
      return { width: 390 };
    },
  );
} catch (error) {
  report.checks.push({ name: "setup", passed: false, error: String(error) });
} finally {
  report.javascript_errors = errors;
  report.passed =
    report.checks.length === 4 &&
    report.checks.every((c) => c.passed) &&
    !errors.length;
  await fs.writeFile(root + "/LIVE.json", JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify(report, null, 2));
  if (!report.passed) process.exitCode = 1;
}
