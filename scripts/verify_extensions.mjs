// 独立无头 Chromium 验证新增功能；只运行于明确隔离文献库，不操作桌面。
import fs from "node:fs/promises";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { parseArgs } from "node:util";
const { values: args } = parseArgs({
  options: {
    config: { type: "string" },
    output: { type: "string" },
    "playwright-module": { type: "string", default: "playwright" },
    executable: { type: "string" },
  },
});
assert(
  args.config && args.output,
  "需要 --config 隔离实例JSON 与 --output 新目录",
);
const config = JSON.parse(await fs.readFile(args.config, "utf8"));
const base = config.base_url.replace(/\/$/, "");
const url = new URL(base);
assert(
  config.isolatedLibrary === true &&
    ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) &&
    !["", "18474"].includes(url.port) &&
    url.pathname === "/" &&
    !url.username &&
    !url.password &&
    !url.search &&
    !url.hash,
  "只允许明确隔离的非生产回环实例",
);
const root = args.output;
await fs.mkdir(root);
const { chromium } = await import(args["playwright-module"]);
const browser = await chromium.launch({
  headless: true,
  ...(args.executable ? { executablePath: args.executable } : {}),
});
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
  acceptDownloads: true,
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
const report = {
  date: "2026-10-30",
  browser: browser.version(),
  api_stubs: false,
  desktop_mouse: false,
  checks: [],
  downloads: [],
};
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const sha = (x) => createHash("sha256").update(x).digest("hex");
async function api(route, body) {
  const r = await page.request.fetch(base + route, {
    method: body ? "POST" : "GET",
    ...(body ? { data: body } : {}),
  });
  assert(r.ok(), `${route}: ${r.status()} ${await r.text()}`);
  return r.json();
}
async function step(name, fn) {
  const start = Date.now();
  try {
    const info = await fn();
    report.checks.push({
      name,
      passed: true,
      seconds: (Date.now() - start) / 1000,
      ...info,
    });
    console.log("PASS", name);
  } catch (e) {
    report.checks.push({ name, passed: false, error: String(e) });
    await page.screenshot({
      path: root + `/failure-${report.checks.length}.png`,
      fullPage: true,
    });
    console.log("FAIL", name, String(e));
  }
  await fs.writeFile(root + "/LIVE.json", JSON.stringify(report, null, 2));
}
try {
  await page.goto(base);
  await page.getByText("本地模型已连接", { exact: true }).waitFor();
  const health = await api("/api/health");
  assert(health.embedding_ready && health.ocr_ready);
  report.health = health;
  const docs = await api("/api/documents");
  const transformer = docs.find(
    (d) =>
      d.id ===
      "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
  );
  assert(transformer);
  await step("original table and numbered formula in actual PDF", async () => {
    const table = await api(
      `/api/documents/${transformer.id}/pages/8/structure`,
    );
    const t = table.items.find((i) => i.kind === "table");
    assert(t);
    assert(
      t.rows.some(
        (r) =>
          r[0]?.includes("Transformer(big)") &&
          r[1] === "28.4" &&
          r[2] === "41.8" &&
          r[3] === "2.3·10^19" &&
          r[4] === null,
      ),
    );
    assert(t.rows.some((r) => r[0]?.includes("ByteNet") && r[2] === ""));
    const eq = await api(`/api/documents/${transformer.id}/pages/4/structure`);
    assert(
      eq.items.some((i) => i.label === "(1)" && i.text.includes("softmax")),
    );
    const response = await page.request.get(
      base + `/api/documents/${transformer.id}/pages/4/structure/0/image`,
    );
    assert(response.ok());
    const content = await response.body();
    assert.equal(content.subarray(0, 4).toString("hex"), "89504e47");
    await fs.writeFile(root + "/equation.png", content);
    return {
      document_sha256: transformer.id,
      table_rows: t.rows.length,
      equation: eq.items[0].text,
    };
  });
  await step(
    "reader displays real extracted cells and downloads CSV",
    async () => {
      await page
        .locator(".sidebar")
        .getByRole("button", { name: "沉浸阅读", exact: true })
        .click();
      await page
        .getByLabel("选择阅读文献", { exact: true })
        .selectOption(transformer.id);
      await page.getByLabel("阅读物理页码", { exact: true }).fill("8");
      await page.waitForFunction(() =>
        document
          .querySelector(".parsed-text")
          ?.textContent?.includes("物理第 8 页"),
      );
      await page
        .getByRole("button", { name: "提取本页表格与编号公式", exact: true })
        .click();
      await page.locator(".structure-table-scroll").waitFor();
      await page.locator(".structure-item img").evaluate(async (img) => {
        await img.decode();
      });
      const download = page.waitForEvent("download");
      await page
        .getByRole("link", { name: "导出表格 CSV", exact: true })
        .click();
      const d = await download;
      assert.equal(await d.failure(), null);
      const path = root + "/" + d.suggestedFilename();
      await d.saveAs(path);
      const c = await fs.readFile(path);
      assert(c.toString().includes("28.4,41.8,2.3·10^19,"));
      report.downloads.push({ name: d.suggestedFilename(), sha256: sha(c) });
      await page.screenshot({
        path: root + "/reader-desktop.png",
        fullPage: true,
      });
      return { csv_sha256: sha(c) };
    },
  );
  await step(
    "two-series charts, radar, SVG and caption downloads",
    async () => {
      await page
        .locator(".sidebar")
        .getByRole("button", { name: "写作工坊", exact: true })
        .click();
      await page
        .getByRole("button", { name: "数据可视化", exact: true })
        .click();
      await page
        .locator("#csv-data")
        .fill("指标,方法甲,方法乙\n准确率,82,85\n召回率,70,75\nF1,76,80");
      await page.getByRole("button", { name: "折线图", exact: true }).click();
      assert.equal(await page.locator(".chart-svg polyline").count(), 2);
      await page.getByRole("button", { name: "雷达图", exact: true }).click();
      assert.equal(
        await page.locator(".chart-svg polygon[data-series]").count(),
        2,
      );
      for (const name of ["导出 SVG", "导出图注"]) {
        const event = page.waitForEvent("download");
        await page.getByRole("button", { name, exact: true }).click();
        const d = await event;
        const path = root + "/" + d.suggestedFilename();
        await d.saveAs(path);
        const bytes = await fs.readFile(path);
        assert(!bytes.toString().includes("NaN"));
        report.downloads.push({
          name: d.suggestedFilename(),
          sha256: sha(bytes),
        });
      }
      await page.screenshot({
        path: root + "/charts-desktop.png",
        fullPage: true,
      });
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth),
        390,
      );
      await page.screenshot({
        path: root + "/charts-mobile.png",
        fullPage: true,
      });
      await page.setViewportSize({ width: 1440, height: 1000 });
      return { series: 2, mobile_width: 390 };
    },
  );
  await step(
    "future work real latest metadata and local Qwen comparison",
    async () => {
      const ids = docs
        .filter((d) => ["2309.15217", "2005.11401"].includes(d.arxiv_id))
        .map((d) => d.id);
      const graph = await api("/api/graph/build", { document_ids: ids });
      assert(graph.nodes.some((n) => n.future_work.length));
      const response = await page.request.post(
        base + "/api/graph/future-check",
        {
          data: {
            document_ids: ids,
            fingerprint: graph.fingerprint,
            topic: "retrieval augmented generation evaluation",
          },
          timeout: 180000,
        },
      );
      assert(response.ok(), `${response.status()} ${await response.text()}`);
      const r = await response.json();
      await fs.writeFile(root + "/FUTURE.json", JSON.stringify(r, null, 2));
      assert(r.candidates.length > 0);
      assert(r.comparisons.some((c) => c.supported));
      assert(
        r.candidates.every(
          (c) => Number(c.published.slice(0, 4)) > r.after_year,
        ),
      );
      return {
        after_year: r.after_year,
        candidate_count: r.candidates.length,
        supported: r.comparisons.filter((c) => c.supported).length,
        cached: r.cached,
        search_query: r.search_query,
        sources: r.candidates.map((c) => ({
          arxiv_id: c.arxiv_id,
          published: c.published,
          url: c.url,
        })),
      };
    },
  );
} catch (e) {
  report.checks.push({ name: "setup", passed: false, error: String(e) });
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
