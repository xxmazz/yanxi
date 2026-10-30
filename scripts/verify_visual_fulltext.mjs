// 真实Chromium、隔离库及本地视觉/语言模型；不使用API替身或桌面自动化。
import fs from "node:fs/promises";
import assert from "node:assert/strict";
import { parseArgs } from "node:util";
const { values: args } = parseArgs({
  options: {
    config: { type: "string" },
    output: { type: "string" },
    "playwright-module": { type: "string", default: "playwright" },
    executable: { type: "string" },
    crosspage: { type: "string" },
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
  date: "2026-10-31",
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
const downloads = [];
async function post(path, data) {
  const response = await page.request.post(base + path, {
    headers: { "X-Yanxi-Request": "1" },
    data,
  });
  assert(
    response.ok(),
    `${path}: ${response.status()} ${await response.text()}`,
  );
  return response.json();
}
async function reader(id, number) {
  const menu = page.getByRole("button", { name: "展开导航", exact: true });
  if (await menu.isVisible()) await menu.click();
  await page
    .locator(".sidebar")
    .getByRole("button", { name: "沉浸阅读", exact: true })
    .click();
  await page.getByLabel("选择阅读文献", { exact: true }).selectOption(id);
  await page.locator(".reference-page-text").waitFor();
  if (number !== 1) {
    const loaded = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/pages/${number}`) && r.request().method() === "GET",
    );
    await page.getByLabel("阅读物理页码", { exact: true }).fill(String(number));
    await loaded;
    await page.locator(".reference-page-text").waitFor();
  }
  await page
    .getByRole("button", { name: "扫描表格 / 公式 LaTeX 识别", exact: true })
    .click();
}
async function draw(bbox) {
  const picker = page.locator(".visual-region-picker");
  await picker.scrollIntoViewIfNeeded();
  await picker.locator("img").evaluate((image) => image.decode());
  await picker.evaluate((element, region) => {
    let parent = element.parentElement;
    while (parent && !/(auto|scroll)/.test(getComputedStyle(parent).overflowY))
      parent = parent.parentElement;
    const rect = element.getBoundingClientRect();
    const middle = rect.y + ((region[1] + region[3]) / 2) * rect.height;
    if (parent) {
      const box = parent.getBoundingClientRect();
      parent.scrollTop += middle - (box.top + box.height / 2);
    } else window.scrollBy(0, middle - innerHeight / 2);
  }, bbox);
  const box = await picker.boundingBox();
  const x = (value) => box.x + value * box.width,
    y = (value) => box.y + value * box.height;
  await page.mouse.move(x(bbox[0]), y(bbox[1]));
  await page.mouse.down();
  await page.mouse.move(x(bbox[2]), y(bbox[3]), { steps: 10 });
  await page.mouse.up();
}
async function recognize() {
  const event = page.waitForResponse(
    (r) =>
      r.url().endsWith("/visual-structure") && r.request().method() === "POST",
    { timeout: 175000 },
  );
  await page.getByRole("button", { name: "识别所选区域", exact: true }).click();
  const response = await event;
  assert(response.ok(), await response.text());
  return response.json();
}
async function file(button, verify) {
  const event = page.waitForEvent("download");
  await button.click();
  const download = await event;
  const target = root + "/" + download.suggestedFilename();
  await download.saveAs(target);
  const content = await fs.readFile(target, "utf8");
  verify(content);
  downloads.push({
    name: download.suggestedFilename(),
    bytes: Buffer.byteLength(content),
  });
}
try {
  await page.goto(base);
  await page.getByText("本地模型已连接", { exact: true }).waitFor();
  report.health = await get("/api/health");
  assert(
    report.health.vision_ready &&
      report.health.ocr_ready &&
      report.health.embedding_ready &&
      report.health.model_ready,
  );
  const docs = await get("/api/documents");
  const scan = docs.find((d) => d.id.startsWith("64094fe169dd"));
  const rag = docs.find((d) => d.arxiv_id === "2005.11401"),
    ragas = docs.find((d) => d.arxiv_id === "2309.15217");
  assert(scan && rag && ragas);
  await step(
    "official scanned Transformer table retains five leaf columns, blanks and source",
    async () => {
      await reader(scan.id, 8);
      await page.getByLabel("表格预期列数", { exact: true }).fill("5");
      await draw([0.17, 0.115, 0.82, 0.313]);
      const result = await recognize();
      await page
        .getByRole("button", { name: "导出识别表格 CSV", exact: true })
        .waitFor();
      const rows = result.items[0].rows;
      assert(rows.every((row) => row.length === 5));
      assert.equal(rows.length, 11);
      assert.match(result.model, /Qwen3-VL-4B/);
      const big = rows.find((row) => /Transformer.*big/u.test(row[0]));
      assert(big, JSON.stringify(rows));
      assert.equal(big[1], "28.4");
      assert.equal(big[2], "41.0");
      const baseRow = rows.find((row) => /Transformer.*base/u.test(row[0]));
      assert.equal(baseRow[1], "27.3");
      assert.equal(baseRow[2], "38.1");
      const byte = rows.find((row) => /ByteNet/u.test(row[0]));
      assert.equal(byte[2], "");
      assert.equal(byte[3], "");
      assert.equal(byte[4], "");
      for (const [pattern, score, cost] of [
        [/^Deep-Att.*Ensemble/, "40.4", "8.0"],
        [/^Deep-Att(?!.*Ensemble)/, "39.2", "1.0"],
      ]) {
        const row = rows.find((item) => pattern.test(item[0]));
        assert.equal(row[1], "");
        assert.equal(row[2], score);
        assert.equal(row[3], "");
        assert(row[4].startsWith(cost));
      }
      await file(
        page.getByRole("button", { name: "导出识别表格 CSV", exact: true }),
        (text) => {
          assert.match(text, /28\.4/);
          assert.match(text, /41\.0/);
        },
      );
      await page
        .locator(".visual-structure")
        .screenshot({ path: root + "/scan-table.png" });
      return {
        document_sha256: scan.id,
        physical_page: 8,
        columns: 5,
        rows: rows.length,
        big,
        base: baseRow,
        empty_cells_preserved: true,
        model: result.model,
        cached: result.cached,
        source_hash: result.source_hash,
      };
    },
  );
  await step(
    "native and official scan formula produce exportable LaTeX with fractions and indices",
    async () => {
      const results = [];
      for (const id of [source, scan.id]) {
        const number = id === source ? 4 : 3;
        await reader(id, number);
        await page
          .getByLabel("图片识别类型", { exact: true })
          .selectOption("equation");
        await draw(
          id === source ? [0.33, 0.56, 0.84, 0.64] : [0.34, 0.88, 0.84, 0.925],
        );
        const result = await recognize();
        const latex = result.items[0].latex;
        assert.match(latex, /\\frac/);
        assert.match(latex, /\\sqrt/);
        assert.match(latex.replace(/\s/g, ""), /softmax/);
        assert.match(latex.replace(/\s/g, ""), /d_\{?k/);
        await page
          .getByRole("button", { name: "导出公式 LaTeX", exact: true })
          .waitFor();
        await file(
          page.getByRole("button", { name: "导出公式 LaTeX", exact: true }),
          (text) => {
            assert(text.includes(`Physical page: ${number}`));
            assert.match(text, /\\frac/);
          },
        );
        results.push({
          document_sha256: id,
          page: number,
          latex,
          model: result.model,
          source_hash: result.source_hash,
          cached: result.cached,
        });
      }
      await page
        .locator(".visual-structure")
        .screenshot({ path: root + "/scan-formula.png" });
      return { results };
    },
  );
  await step(
    "two-page synthetic scan selection merges only repeated header and exports all rows",
    async () => {
      assert(args.crosspage, "需要 --crosspage 两页扫描回归文件");
      const response = await page.request.post(base + "/api/documents", {
        headers: { "X-Yanxi-Request": "1" },
        multipart: {
          file: {
            name: "cross-page-scan.pdf",
            mimeType: "application/pdf",
            buffer: await fs.readFile(args.crosspage),
          },
        },
      });
      assert(response.ok(), await response.text());
      let document = await response.json();
      const deadline = Date.now() + 180000;
      while (document.status !== "ready" && Date.now() < deadline) {
        assert(
          !["error", "review"].includes(document.status),
          JSON.stringify(document),
        );
        await new Promise((resolve) => setTimeout(resolve, 1000));
        document = (await get("/api/documents")).find(
          (item) => item.id === document.id,
        );
      }
      assert.equal(document.status, "ready");
      assert.equal(document.ocr_pages, 2);
      await reader(document.id, 1);
      await page.getByLabel("表格预期列数", { exact: true }).fill("3");
      await draw([0.085, 0.12, 0.91, 0.285]);
      await page
        .getByRole("button", { name: "保留选区并选择下一页续表", exact: true })
        .click();
      await draw([0.085, 0.12, 0.91, 0.285]);
      await recognize();
      await page
        .getByRole("button", { name: "导出识别表格 CSV", exact: true })
        .waitFor({ timeout: 175000 });
      await file(
        page.getByRole("button", { name: "导出识别表格 CSV", exact: true }),
        (text) => {
          for (const value of ["Alpha", "Beta", "Gamma", "Delta", "50", "60"])
            assert(text.includes(value));
          assert.equal(text.match(/Model/g).length, 1);
        },
      );
      assert.equal(await page.locator(".visual-structure tr").count(), 5);
      await file(
        page.getByRole("button", { name: "导出识别结果与出处", exact: true }),
        (text) => {
          const results = JSON.parse(text);
          assert.deepEqual(
            results.map((item) => item.page),
            [1, 2],
          );
          assert(
            results.every(
              (item) => item.document_id === document.id && item.source_hash,
            ),
          );
        },
      );
      return {
        synthetic_fixture: true,
        document_sha256: document.id,
        ocr_pages: 2,
        merged_rows: 5,
        columns: 3,
      };
    },
  );
  await step(
    "real Ragas body evidence beyond abstract, page hashes and browser export",
    async () => {
      await page
        .locator(".sidebar")
        .getByRole("button", { name: "引用图谱", exact: true })
        .click();
      const picker = page.locator(".graph-document-picker");
      await page.locator(".graph-controls details > summary").click();
      await picker
        .getByRole("button", { name: "清空选择", exact: true })
        .click();
      await picker
        .locator("label")
        .filter({ hasText: rag.title })
        .getByRole("checkbox")
        .check();
      await page
        .getByRole("button", { name: /构建真实引用图谱|重新构建图谱/u })
        .click();
      await page.getByLabel("正文核对主题", { exact: true }).waitFor();
      await page
        .getByLabel("正文核对主题", { exact: true })
        .fill("retrieval augmented generation evaluation");
      await page
        .locator(".fulltext-source")
        .filter({ hasText: ragas.title })
        .getByRole("checkbox")
        .check();
      const event = page.waitForResponse(
        (r) =>
          r.url().endsWith("/api/graph/fulltext-check") &&
          r.request().method() === "POST",
        { timeout: 175000 },
      );
      await page
        .getByRole("button", { name: "核对所选论文正文", exact: true })
        .click();
      const response = await event;
      assert(response.ok(), await response.text());
      const result = await response.json();
      const group = result.groups[0];
      assert(group.supported, group.answer);
      assert(group.citations.length);
      assert(
        group.evidence.some((item) => item.page > 1),
        "应从摘要之外的正文页面提供证据",
      );
      for (const item of group.evidence) {
        const raw = await get(
          `/api/documents/${item.document_id}/pages/${item.page}`,
        );
        assert.equal(
          Array.from(raw.text).slice(item.start, item.end).join(""),
          item.text,
        );
      }
      await file(
        page.getByRole("button", { name: "导出正文核对结果", exact: true }),
        (text) => {
          const saved = JSON.parse(text);
          assert.equal(saved.target_fingerprint, result.target_fingerprint);
          assert(saved.groups[0].citations.length);
        },
      );
      await page
        .locator(".fulltext-check")
        .screenshot({ path: root + "/fulltext.png" });
      return {
        origin_document: rag.id,
        target_document: ragas.id,
        source_year: result.after_year,
        target_year: group.year,
        indexed_body_chunks: group.indexed_chunks,
        answer: group.answer,
        evidence_pages: group.evidence.map((item) => item.page),
        citation_pages: group.citations.map((item) => item.page),
        offsets_verified: true,
      };
    },
  );
  await step(
    "narrow-screen visual controls and fulltext results remain usable",
    async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      );
      await page
        .locator(".fulltext-check")
        .screenshot({ path: root + "/fulltext-mobile.png" });
      await reader(scan.id, 8);
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      );
      return { width: 390 };
    },
  );
} finally {
  report.downloads = downloads;
  report.javascript_errors = errors;
  report.passed =
    report.checks.length === 5 &&
    report.checks.every((item) => item.passed) &&
    !errors.length;
  await fs.writeFile(root + "/LIVE.json", JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify(report, null, 2));
  if (!report.passed) process.exitCode = 1;
}
