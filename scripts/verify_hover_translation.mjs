// 真实Chromium、隔离库及本地Qwen悬停翻译；不使用API替身或桌面自动化。
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
let submissions = 0;
page.on("request", (request) => {
  if (
    request.method() === "POST" &&
    request.url().includes("/translation/selection/")
  )
    submissions++;
});
async function pageNumber(number) {
  const event = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/pages/${number}`) &&
      response.request().method() === "GET",
  );
  await page.getByLabel("阅读物理页码", { exact: true }).fill(String(number));
  await event;
  await page.locator(".reference-page-text").waitFor();
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
  await page
    .locator(".sidebar")
    .getByRole("button", { name: "沉浸阅读", exact: true })
    .click();
  await page.getByLabel("选择阅读文献", { exact: true }).selectOption(source);
  await page
    .getByRole("button", { name: "开启悬停翻译", exact: true })
    .waitFor();
  await pageNumber(3);
  const original = await get(`/api/documents/${source}/pages/3`);
  await step(
    "opt-in real sentence hover runs local Qwen and preserves source offsets",
    async () => {
      assert.equal(submissions, 0);
      assert.equal(await page.locator(".hover-translation-source").count(), 0);
      await page
        .getByRole("button", { name: "开启悬停翻译", exact: true })
        .click();
      const sentence = page.locator(".hover-translation-source").filter({
        hasText: "The Transformer follows this overall architecture",
      });
      assert.equal(await sentence.count(), 1);
      const responseEvent = page.waitForResponse(
        (response) =>
          response.url().includes("/translation/selection/3") &&
          response.request().method() === "POST",
      );
      await sentence.hover();
      const response = await responseEvent;
      assert.equal(response.status(), 202, await response.text());
      const job = await response.json();
      const request = response.request().postDataJSON();
      assert.match(request.text, /connected layers/);
      assert.match(request.text, /respectively\./);
      assert.equal(
        Array.from(original.text).slice(request.start, request.end).join(""),
        request.text,
      );
      assert.equal(job.source.document_id, source);
      assert.equal(job.source.page, 3);
      assert.equal(job.source.text, request.text);
      await page.waitForFunction(
        () => {
          const panel = document.querySelector(".hover-translation-result");
          return (
            panel &&
            [...panel.querySelectorAll("p")].some(
              (p) =>
                /[\u4e00-\u9fff]/u.test(p.textContent) &&
                !/排队|正在翻译/u.test(p.textContent),
            )
          );
        },
        null,
        { timeout: 150000 },
      );
      const latest = await get(`/api/translation/jobs/${job.id}`);
      assert.equal(latest.status, "ready", latest.error);
      assert.match(latest.result, /[\u4e00-\u9fff]/u);
      assert.match(latest.result, /编码器|解码器|自注意力/u);
      assert.equal(
        await page.locator(".reference-page-text").textContent(),
        original.text,
      );
      await page.screenshot({
        path: root + "/hover-desktop.png",
        fullPage: true,
      });
      return {
        document_sha256: source,
        physical_page: 3,
        start: request.start,
        end: request.end,
        source: request.text,
        translation: latest.result,
        source_hash: job.source.source_hash,
        target: request.target,
        submissions,
      };
    },
  );
  await step(
    "same sentence cache, keyboard access, and page changes isolate displayed results",
    async () => {
      const before = submissions;
      await page
        .getByRole("button", { name: "关闭选段译文", exact: true })
        .click();
      const sentence = page.locator(".hover-translation-source").filter({
        hasText: "The Transformer follows this overall architecture",
      });
      await sentence.focus();
      await sentence.press("Enter");
      await page.getByText(/会话缓存/u).waitFor();
      assert.equal(submissions, before);
      await sentence.press("Escape");
      assert.equal(await page.locator(".hover-translation-result").count(), 0);
      await sentence.press("Enter");
      await page.getByText(/会话缓存/u).waitFor();
      await pageNumber(4);
      assert.equal(await page.locator(".hover-translation-result").count(), 0);
      await pageNumber(3);
      assert.equal(await page.locator(".hover-translation-result").count(), 0);
      await page
        .getByRole("button", { name: "查看参考文献 [11]", exact: true })
        .first()
        .click();
      await page.waitForFunction(
        () =>
          document.querySelector('[aria-label="阅读物理页码"]').value === "11",
      );
      await page
        .getByRole("button", { name: "返回刚才的正文 · 第 3 页", exact: true })
        .click();
      await page.waitForFunction(
        () =>
          document.querySelector('[aria-label="阅读物理页码"]').value === "3",
      );
      await page
        .locator(".reference-page-text")
        .filter({ hasText: "The Transformer follows" })
        .waitFor();
      return {
        cache_reused_without_submission: true,
        keyboard: "Enter / Escape",
        reference_navigation: "3 → 11 → 3",
        page_change_clears_translation: true,
      };
    },
  );
  await step(
    "forged source selection is rejected before creating a translation",
    async () => {
      const response = await page.request.post(
        base + `/api/documents/${source}/translation/selection/3`,
        {
          headers: { "X-Yanxi-Request": "1" },
          data: { start: 0, end: 5, text: "wrong", target: "zh" },
        },
      );
      assert.equal(response.status(), 409);
      return {
        status: response.status(),
        detail: (await response.json()).detail,
      };
    },
  );
  await step("390px touch selection stays within viewport", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    const sentence = page
      .locator(".hover-translation-source")
      .filter({ hasText: "The Transformer follows this overall architecture" });
    await sentence.click();
    await page.getByText(/会话缓存/u).waitFor();
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    );
    await page.locator(".hover-translation-result").scrollIntoViewIfNeeded();
    await page.screenshot({ path: root + "/hover-mobile.png", fullPage: true });
    await page
      .getByRole("button", { name: "关闭悬停翻译", exact: true })
      .click();
    assert.equal(await page.locator(".hover-translation-source").count(), 0);
    assert.equal(
      await page.locator(".reference-page-text").textContent(),
      original.text,
    );
    return { width: 390, cached_touch: true, disabling_preserves_source: true };
  });
} finally {
  report.javascript_errors = errors;
  report.passed =
    report.checks.length === 4 &&
    report.checks.every((check) => check.passed) &&
    !errors.length;
  await fs.writeFile(root + "/LIVE.json", JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify(report, null, 2));
  if (!report.passed) process.exitCode = 1;
}
