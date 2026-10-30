// 独立 Chromium + 实际隔离后端，不使用桌面鼠标或模拟 API。
// Playwright 为验收机的独立工具，不加入 GPU 服务或产品依赖。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";
const { values: args } = parseArgs({
  options: {
    config: { type: "string" },
    output: { type: "string" },
    fixtures: { type: "string" },
    "playwright-module": { type: "string", default: "playwright" },
    executable: { type: "string" },
  },
});
if (!args.config || !args.output || !args.fixtures)
  throw Error(
    "需要 --config 私有认证JSON、--output 全新目录和 --fixtures 官方资料目录",
  );
const config = JSON.parse(await fs.readFile(args.config, "utf8"));
const url = new URL(config.base_url);
assert(
  config.isolatedLibrary === true &&
    ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) &&
    !["", "18474"].includes(url.port) &&
    !url.username &&
    !url.password &&
    url.pathname === "/" &&
    !url.search &&
    !url.hash,
  "只允许显式确认的非生产端口回环隔离库",
);
await fs.mkdir(args.output); // 已存在时不覆盖旧失败记录
const output = path.resolve(args.output);
const { chromium } = await import(args["playwright-module"]);
const browser = await chromium.launch({
  headless: true,
  ...(args.executable ? { executablePath: args.executable } : {}),
});
const context = await browser.newContext({
  httpCredentials: { username: config.username, password: config.password },
  acceptDownloads: true,
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(20000);
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const sha = (value) => createHash("sha256").update(value).digest("hex");
const report = {
  date: "2026-10-30",
  browser: browser.version(),
  engineering_baseline: "437ab76",
  candidate: true,
  api_stubs_used: false,
  desktop_mouse_used: false,
  authenticated_loopback: true,
  checks: [],
  downloads: [],
  scope:
    "真实无头Chromium + 已有四卡隔离文献库；不等于公网、手动密码弹窗、其他浏览器或全部赛题扩展方向验收",
};
const base = config.base_url;
async function api(route, options = {}) {
  const response = await context.request.fetch(base + route, options);
  assert(response.ok(), `${route}: ${response.status()}`);
  return response.json();
}
const docs = await api("/api/documents");
const fixture = JSON.parse(
  await fs.readFile("docs/GRAPH20_CASES.json", "utf8"),
);
const native = docs.find(
  (d) =>
    d.id ===
    fixture.documents.find((d) => d.filename === "1706.03762v7.pdf").sha256,
);
const bert = docs.find(
  (d) =>
    d.id ===
    fixture.documents.find((d) => d.filename === "N19-1423.pdf").sha256,
);
const ragas = docs.find((d) => d.arxiv_id === "2309.15217" && d.is_active);
const rag = docs.find((d) => d.arxiv_id === "2005.11401" && d.is_active);
assert(native && bert && ragas && rag, "隔离库缺少演示样本");
async function nav(label) {
  await page
    .locator(".sidebar")
    .getByRole("button", {
      name: label,
      exact: !["我的文献", "文献问答"].includes(label),
    })
    .click();
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
    report.checks.push({
      name,
      passed: false,
      error: String(error).slice(0, 800),
    });
    console.log("FAIL", name, String(error).slice(0, 350));
    await page
      .screenshot({
        path: path.join(output, `failure-${report.checks.length}.png`),
        fullPage: true,
      })
      .catch(() => {});
  }
  await fs.writeFile(
    path.join(output, "REPORT.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
}
async function file(button, kind, check) {
  const event = page.waitForEvent("download");
  await button.click();
  const download = await event;
  assert.equal(await download.failure(), null);
  const target = path.join(
    output,
    `${report.downloads.length + 1}-${download.suggestedFilename()}`,
  );
  await download.saveAs(target);
  const content = await fs.readFile(target);
  await check(content);
  report.downloads.push({
    kind,
    name: download.suggestedFilename(),
    bytes: content.length,
    sha256: sha(content),
    saved_to_disk: true,
  });
  return content;
}
async function reader(doc, number = 1) {
  await nav("沉浸阅读");
  await page.getByLabel("选择阅读文献", { exact: true }).selectOption(doc.id);
  await page.waitForFunction(
    (id) =>
      document
        .querySelector(".page-image img")
        ?.getAttribute("src")
        ?.includes(id),
    doc.id,
  );
  await page.getByLabel("阅读物理页码", { exact: true }).fill(String(number));
  await page
    .getByRole("button", { name: "原页与解析正文", exact: true })
    .click();
  await page.locator(".page-image img").waitFor();
  await page.waitForFunction(() => {
    const img = document.querySelector(".page-image img");
    return img?.complete && img.naturalWidth > 0;
  });
  await page.waitForFunction(
    (number) =>
      document.querySelector('[aria-label="阅读物理页码"]').value ===
        String(number) &&
      document
        .querySelector(".parsed-text")
        ?.textContent?.includes(`物理第 ${number} 页`),
    number,
  );
}
try {
  await step("浏览器认证与页面加载", async () => {
    const anonymous = await browser.newContext();
    assert.equal((await anonymous.request.get(base + "/")).status(), 401);
    assert.equal(
      (
        await anonymous.request.get(base + `/api/documents/${native.id}/pdf`)
      ).status(),
      401,
    );
    await anonymous.close();
    await page.goto(base);
    await page.getByText("本地模型已连接", { exact: true }).waitFor();
    const health = await api("/api/health");
    assert(health.model_ready && health.embedding_ready && health.ocr_ready);
    await page.screenshot({
      path: path.join(output, "library.png"),
      fullPage: false,
    });
    return {
      anonymous_rejected: true,
      credentials_supplied_by_browser_context: true,
      native_password_dialog_manually_tested: false,
      models_ready: true,
    };
  });
  await step("目录下载与原PDF文件", async () => {
    await file(
      page.getByRole("button", { name: "导出目录", exact: true }),
      "library-json",
      (content) => {
        assert(JSON.parse(content).some((d) => d.id === native.id));
      },
    );
    const pdf = await context.request.get(
      base + `/api/documents/${native.id}/pdf`,
    );
    assert(pdf.ok());
    assert.equal(sha(await pdf.body()), native.id);
    return { pdf_sha256_matches: true };
  });
  await step("原页、三级章节和数字引用双向跳转", async () => {
    await reader(native, 2);
    await page
      .getByRole("button", { name: "查看参考文献 [13]", exact: true })
      .first()
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector('[aria-label="阅读物理页码"]').value === "11",
    );
    await page
      .getByRole("button", { name: "返回刚才的正文 · 第 2 页", exact: true })
      .click();
    await page.waitForFunction(
      () => document.querySelector('[aria-label="阅读物理页码"]').value === "2",
    );
    await page.locator(".reading-outline > summary").click();
    await page.getByRole("button", { name: /跳到章节 3\.2\.2/ }).click();
    await page.locator(".parsed-text mark").first().waitFor();
    assert(
      (await page.locator(".parsed-text mark").first().innerText()).includes(
        "Multi-Head",
      ),
    );
    return {
      numeric_reference: "[13]: physical 2 → 11 → 2",
      chapter: "3.2.2 Multi-Head Attention",
    };
  });
  await step("作者年份引用双向跳转", async () => {
    await reader(bert, 1);
    await page
      .getByRole("button", { name: /查看参考文献.*Peters.*2018a/ })
      .first()
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector('[aria-label="阅读物理页码"]').value === "11",
    );
    await page
      .getByRole("button", { name: "返回刚才的正文 · 第 1 页", exact: true })
      .click();
    await page.waitForFunction(
      () => document.querySelector('[aria-label="阅读物理页码"]').value === "1",
    );
    return { author_year: "Peters et al., 2018a: physical 1 → 11 → 1" };
  });
  await step("真实本地问答与连续追问", async () => {
    await nav("文献问答");
    await page
      .getByLabel("问答文献范围", { exact: true })
      .selectOption(native.id);
    const answers = [];
    for (const question of [
      "论文的第6作者是谁，在哪个机构工作？",
      "其中Adam优化器的β1和β2分别是多少？",
    ]) {
      await page.getByLabel("输入文献问题", { exact: true }).fill(question);
      const event = page.waitForResponse(
        (r) => r.url().endsWith("/api/chat") && r.request().method() === "POST",
        { timeout: 180000 },
      );
      await page.getByRole("button", { name: "发送问题", exact: true }).click();
      const response = await event;
      assert(response.ok());
      const result = await response.json();
      assert(!result.insufficient_evidence && result.citations.length);
      assert(result.citations.every((e) => e.document_id === native.id));
      answers.push({
        question,
        answer: result.answer,
        pages: result.citations.map((e) => e.page),
      });
      await page
        .getByRole("button", { name: "发送问题", exact: true })
        .waitFor({ state: "visible" });
    }
    assert(answers[0].answer.includes("Aidan"));
    assert(
      answers[1].answer.includes("0.9") && answers[1].answer.includes("0.98"),
    );
    return { answers, scope: "显式单篇；每问真实检索生成" };
  });
  await step("历史研究任务、真实来源和原页", async () => {
    await nav("探索发现");
    const tasks = await api("/api/research/tasks");
    const task = tasks.find(
      (t) => t.query === "检索增强生成的自动评估" && t.status === "complete",
    );
    assert(task);
    const panel = page
      .locator(".research-task-panel > details")
      .filter({
        has: page
          .locator("summary")
          .filter({ hasText: "检索增强生成的自动评估" }),
      })
      .first();
    await panel.locator(":scope > summary").click();
    await panel
      .getByRole("heading", { name: "有来源的摘要综述", exact: true })
      .waitFor();
    const source = panel.locator("section > details").first();
    await source.locator("summary").click();
    assert((await source.locator("blockquote").innerText()).length > 20);
    return {
      task_id: task.id,
      pages: task.summary.citations.map((e) => e.page),
      history_marked: true,
    };
  });
  await step("八页对照翻译、双向滚动及Markdown落盘", async () => {
    await reader(ragas, 1);
    await page
      .getByRole("button", { name: "中英对照翻译", exact: true })
      .click();
    await page
      .locator('[aria-label="论文译文滚动区域"] .translation-segment p')
      .first()
      .waitFor();
    const content = await file(
      page.getByRole("button", { name: "导出整篇对照 Markdown", exact: true }),
      "translation-markdown",
      (content) => {
        assert(content.length > 10000);
        assert(content.toString().includes("Ragas"));
        assert(/[\u4e00-\u9fff]/.test(content.toString()));
      },
    );
    const sync = await page.evaluate(async () => {
      const left = document.querySelector('[aria-label="论文原文滚动区域"]'),
        right = document.querySelector('[aria-label="论文译文滚动区域"]');
      left.scrollTop = left.scrollHeight;
      left.dispatchEvent(new Event("scroll"));
      await new Promise((r) => setTimeout(r, 120));
      const a = right.scrollTop > 0;
      right.scrollTop = 0;
      right.dispatchEvent(new Event("scroll"));
      await new Promise((r) => setTimeout(r, 120));
      return { toChinese: a, toEnglish: left.scrollTop < 2 };
    });
    assert(sync.toChinese && sync.toEnglish);
    return {
      existing_translation_cache: true,
      bytes: content.length,
      scroll: sync,
    };
  });
  await step("20篇100边引用图谱及JSON落盘", async () => {
    await nav("引用图谱");
    await page.locator(".graph-controls details > summary").click();
    await page.getByRole("button", { name: "清空选择", exact: true }).click();
    const labels = page.locator(".graph-document-picker label");
    const ids = new Set(fixture.documents.map((d) => d.sha256));
    const available = docs.filter(
      (d) => d.status === "ready" && d.is_active && !d.trashed_at,
    );
    assert.equal(await labels.count(), available.length);
    for (let i = 0; i < available.length; i++)
      if (ids.has(available[i].id))
        await labels.nth(i).locator("input").check();
    const build = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/graph/build") && r.request().method() === "POST",
      { timeout: 120000 },
    );
    build.catch(() => {});
    await page
      .getByRole("button", { name: /^(构建真实引用图谱|重新构建图谱)$/ })
      .click();
    assert((await build).ok());
    await page.getByLabel("真实文献引用图谱", { exact: true }).waitFor();
    const content = await file(
      page.getByRole("button", { name: "导出图谱与来源", exact: true }),
      "graph-json",
      (content) => {
        const graph = JSON.parse(content);
        assert.equal(graph.nodes.length, 20);
        assert.equal(graph.edges.length, 100);
        assert(graph.edges.every((e) => e.evidence?.length));
      },
    );
    return { nodes: 20, edges: 100, bytes: content.length };
  });
  await step("三篇论文计划生成、原页、编辑、刷新与四种导出", async () => {
    await nav("写作工坊");
    await page.getByRole("button", { name: "论文框架", exact: true }).click();
    const labels = page.locator(".writing-input fieldset label");
    await labels.first().waitFor();
    const ready = docs.filter(
      (d) => d.status === "ready" && d.is_active && !d.trashed_at,
    );
    assert.equal(await labels.count(), ready.length);
    const ids = new Set([native.id, rag.id, ragas.id]);
    for (let i = 0; i < ready.length; i++)
      await labels.nth(i).locator("input").setChecked(ids.has(ready[i].id));
    const event = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/writing/outline") &&
        r.request().method() === "POST",
      { timeout: 180000 },
    );
    event.catch(() => {});
    await page
      .getByRole("button", { name: "生成有来源的论文框架", exact: true })
      .click();
    const response = await event;
    assert(response.ok());
    const draft = await response.json();
    assert.equal(draft.references.length, 3);
    await page.getByLabel("研究计划预览", { exact: true }).waitFor();
    const source = page.locator(".writing-source").first();
    await source.locator("summary").click();
    await source.getByRole("button", { name: "预览原页", exact: true }).click();
    await page.waitForFunction(() => {
      const img = document.querySelector(".writing-source img");
      return img?.complete && img.naturalWidth > 0;
    });
    await file(
      page.getByRole("button", { name: "导出 Markdown", exact: true }),
      "writing-markdown",
      (c) => {
        assert(c.toString().includes("References"));
        assert(c.toString().includes("研究计划"));
      },
    );
    await file(
      page.getByRole("button", { name: "导出来源记录", exact: true }),
      "writing-sources",
      (c) => {
        assert.equal(JSON.parse(c).references.length, 3);
      },
    );
    await file(
      page.getByRole("button", { name: "导出 Mermaid", exact: true }),
      "writing-mermaid",
      (c) => {
        assert(c.toString().includes("flowchart TD"));
      },
    );
    await file(
      page.getByRole("button", { name: "导出 SVG", exact: true }),
      "writing-svg",
      async (c) => {
        assert(
          await page.evaluate(
            (text) =>
              !new DOMParser()
                .parseFromString(text, "image/svg+xml")
                .querySelector("parsererror"),
            c.toString(),
          ),
        );
      },
    );
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    const editor = page.getByLabel("编辑研究计划", { exact: true });
    const marker = "\n\n手动核对记录：浏览器验收。";
    await editor.fill((await editor.inputValue()) + marker);
    await page.reload();
    await nav("写作工坊");
    await page.getByRole("button", { name: "论文框架", exact: true }).click();
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    assert(
      (
        await page.getByLabel("编辑研究计划", { exact: true }).inputValue()
      ).endsWith(marker),
    );
    return {
      references: 3,
      real_model_generation: true,
      source_png_loaded: true,
      edited_draft_persisted_after_reload: true,
    };
  });
  await step("CSV导入、两类切页与SVG图注落盘", async () => {
    await nav("写作工坊");
    await page.getByRole("button", { name: "数据可视化", exact: true }).click();
    const csv =
      "文献,E5向量段数\nTransformer v7,47\nTransformer官方扫描件,43\nBERT,72";
    const filename = path.join(output, "input.csv");
    await fs.writeFile(filename, csv);
    await page.locator(".chart-input input[type=file]").setInputFiles(filename);
    await page.getByRole("button", { name: "折线图", exact: true }).click();
    await page.getByRole("button", { name: "论文框架", exact: true }).click();
    await page.getByRole("button", { name: "数据可视化", exact: true }).click();
    assert.equal(await page.locator("#csv-data").inputValue(), csv);
    await nav("我的文献");
    await nav("写作工坊");
    assert.equal(await page.locator("#csv-data").inputValue(), csv);
    assert.equal(await page.locator(".chart-svg polyline").count(), 1);
    await file(
      page.getByRole("button", { name: "导出 SVG", exact: true }),
      "chart-svg",
      async (c) => {
        assert(
          await page.evaluate((text) => {
            const doc = new DOMParser().parseFromString(text, "image/svg+xml");
            return (
              !doc.querySelector("parsererror") &&
              !!doc.querySelector("polyline")
            );
          }, c.toString()),
        );
        for (const v of ["47", "43", "72"])
          assert(c.toString().includes(">" + v + "<"));
      },
    );
    await file(
      page.getByRole("button", { name: "导出图注", exact: true }),
      "chart-caption",
      (c) => {
        assert(c.toString().includes(csv));
        assert(!c.toString().includes("Baseline"));
      },
    );
    return {
      file_import: true,
      line_chart_preserved: true,
      values: [47, 43, 72],
    };
  });
  await step("同字节改名PDF上传去重", async () => {
    await nav("我的文献");
    await page
      .getByRole("button", { name: "导入文献", exact: true })
      .first()
      .click();
    const pdf = await fs.readFile(
      path.join(args.fixtures, "pdf", "1706.03762v7.pdf"),
    );
    const event = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/documents") && r.request().method() === "POST",
    );
    event.catch(() => {});
    await page.locator('input[type="file"][accept*="pdf"]').setInputFiles({
      name: "浏览器改名重复上传.pdf",
      mimeType: "application/pdf",
      buffer: pdf,
    });
    const response = await event;
    assert(response.ok());
    const result = await response.json();
    assert(result.duplicate && result.id === native.id);
    return { duplicate: true, pdf_unchanged: true };
  });
  await step("窄屏导航与布局", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "展开导航", exact: true }).click();
    await page
      .locator(".sidebar")
      .getByRole("button", { name: "写作工坊", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "写作工坊", exact: true })
      .waitFor();
    const overflow = await page.evaluate(() => ({
      width: window.innerWidth,
      scroll: document.documentElement.scrollWidth,
    }));
    assert(overflow.scroll <= overflow.width + 2, JSON.stringify(overflow));
    await page.screenshot({
      path: path.join(output, "mobile.png"),
      fullPage: false,
    });
    return overflow;
  });
} finally {
  report.javascript_errors = errors;
  report.passed = report.checks.every((c) => c.passed) && errors.length === 0;
  await fs.writeFile(
    path.join(output, "REPORT.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  await browser.close();
}
if (!report.passed) process.exitCode = 1;
