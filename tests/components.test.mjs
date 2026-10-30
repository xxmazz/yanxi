// These tests exercise real React components in jsdom with explicit API stubs.
// They do not claim browser downloads, GPU inference or live API verification.
import test, { after } from "node:test";
import assert from "node:assert/strict";
import {
  act,
  button,
  change,
  click,
  components,
  disposeEnvironment,
  importFile,
  setup,
  submit,
} from "./helpers/component-dom.mjs";

const csv =
  "文献,E5向量段数\nTransformer v7,47\nTransformer官方扫描件,43\nBERT,72";
after(disposeEnvironment);

async function charts(environment) {
  await environment.render(components.App);
  await click(button("写作工坊", document.querySelector(".sidebar")));
  await click(button("数据可视化"));
}

function assertChartInput(expected = csv) {
  assert.equal(document.querySelector("#csv-data").value, expected);
  assert.equal(button("折线图").classList.contains("active"), true);
  assert.ok(document.querySelector(".chart-svg polyline"));
}

test("chart CSV and line selection survive both writing-tab and main-navigation unmounts", async () => {
  const env = setup();
  try {
    await charts(env);
    await change(document.querySelector("#csv-data"), csv);
    await click(button("折线图"));
    assertChartInput();
    await click(button("论文框架"));
    assert.equal(document.querySelector("#csv-data"), null);
    await click(button("数据可视化"));
    assertChartInput();
    await click(button(/^我的文献\d+$/, document.querySelector(".sidebar")));
    assert.equal(document.querySelector("#csv-data"), null);
    await click(button("写作工坊", document.querySelector(".sidebar")));
    assertChartInput();

    await click(button("导出 SVG"));
    await click(button("导出图注"));
    assert.deepEqual(
      env.downloads.map((file) => file.name),
      ["研析-数据图表.svg", "研析-图表说明.md"],
    );
    const svg = await env.downloads[0].blob.text();
    assert.match(env.downloads[0].blob.type, /image\/svg\+xml/);
    const parsed = new window.DOMParser().parseFromString(svg, "image/svg+xml");
    assert.equal(
      parsed.querySelector("parsererror"),
      null,
      parsed.documentElement.textContent + "\n" + svg.slice(0, 300),
    );
    assert.equal(parsed.documentElement.localName, "svg");
    assert.match(svg, /<polyline/);
    for (const value of ["47", "43", "72"])
      assert.match(svg, new RegExp(`>${value}<`));
    assert.doesNotMatch(svg, /Baseline|68\.4|82\.1/);
    const caption = await env.downloads[1].blob.text();
    assert.ok(caption.includes(csv));
    assert.match(caption, /BERT 的E5向量段数最高（72）/);
    assert.doesNotMatch(caption, /Baseline/);

    await click(button("恢复示例"));
    assert.match(document.querySelector("#csv-data").value, /Baseline,68\.4/);
  } finally {
    await env.cleanup();
  }
});

test("an asynchronous CSV import finishing after a page switch is retained when returning", async () => {
  const env = setup();
  try {
    await charts(env);
    let finish;
    const content = new Promise((resolve) => {
      finish = resolve;
    });
    await importFile(
      document.querySelector('.chart-input input[type="file"]'),
      {
        size: 100,
        text: () => content,
      },
    );
    await click(button("论文框架"));
    await act(async () => finish(csv));
    await click(button("数据可视化"));
    assert.equal(document.querySelector("#csv-data").value, csv);
    await click(button("导出图注"));
    assert.ok((await env.downloads[0].blob.text()).includes(csv));

    await importFile(
      document.querySelector('.chart-input input[type="file"]'),
      {
        size: 40,
        text: async () => "类别,数值\n错误,not-a-number",
      },
    );
    assert.equal(document.querySelector("#csv-data").value, csv);
    assert.match(
      document.querySelector('[role="status"]').textContent,
      /有限数值/,
    );
  } finally {
    await env.cleanup();
  }
});

test("chart SVG export escapes markup-like category labels and remains valid XML", async () => {
  const env = setup();
  try {
    await charts(env);
    await change(
      document.querySelector("#csv-data"),
      "类别,数值\n<script>,1\nA&B<,2",
    );
    await click(button("导出 SVG"));
    const svg = await env.downloads[0].blob.text();
    const parsed = new window.DOMParser().parseFromString(svg, "image/svg+xml");
    assert.equal(parsed.querySelector("parsererror"), null);
    assert.equal(parsed.querySelector("script"), null);
    assert.ok(parsed.documentElement.textContent.includes("<script>"));
    assert.ok(parsed.documentElement.textContent.includes("A&B<"));
    assert.match(svg, /&lt;script&gt;/);
  } finally {
    await env.cleanup();
  }
});

function task(overrides = {}) {
  return {
    id: "task-fixture",
    query: "检索增强生成的自动评估",
    status: "complete",
    stage: "summary",
    error: "",
    attempt: 1,
    search_query: "retrieval augmented generation evaluation",
    selection_method: "e5_topic",
    search_steps: ["组件测试固定候选，不访问arXiv"],
    events: [{ stage: "summary", message: "模拟来源就绪", time: 1 }],
    selected: [
      {
        title: "组件测试来源",
        url: "https://arxiv.org/abs/2403.00820",
        reason: "固定测试候选",
        document_id: "doc-fixture",
      },
    ],
    results: [
      {
        document_id: "doc-fixture",
        title: "组件测试来源",
        pages: 7,
        abstract: "固定摘要数据，不是模型生成结果。",
        pdf_url: "/api/documents/doc-fixture/pdf",
      },
    ],
    summary: {
      answer: "用于界面回归的固定综述。",
      insufficient_evidence: false,
      citations: [
        {
          id: "S1",
          title: "组件测试来源",
          page: 5,
          url: "/api/documents/doc-fixture/pdf#page=5",
          quote: "固定引用段落，仅测试展示与跳转目标。",
        },
      ],
    },
    ...overrides,
  };
}

test("research entry submits the Chinese topic and renders the returned source and physical page", async () => {
  const env = setup();
  try {
    const value = task();
    env.routes.set("POST /api/research/tasks", () => {
      env.routes.set("GET /api/research/tasks", [value]);
      return value;
    });
    await env.render(components.App);
    await click(button("探索发现", document.querySelector(".sidebar")));
    assert.match(
      document.querySelector(".research-task-panel h2").textContent,
      /研究任务/,
    );
    await change(
      document.querySelector('[aria-label="研究任务主题"]'),
      `  ${value.query}  `,
    );
    await change(
      document.querySelector('[aria-label="研究任务论文数量"]'),
      "2",
    );
    await submit(document.querySelector(".research-task-panel form"));
    const request = env.calls.find((item) => item.method === "POST");
    assert.deepEqual(request.body, { query: value.query, limit: 2 });
    assert.equal(request.headers.get("X-Yanxi-Request"), "1");
    const panel = document.querySelector(".research-task-panel");
    assert.match(panel.textContent, /本地 E5 按本次主题重排/);
    assert.match(panel.textContent, /有来源的摘要综述/);
    assert.match(panel.textContent, /第 5 页/);
    assert.match(panel.textContent, /固定引用段落/);
    assert.equal(
      [...panel.querySelectorAll("a")]
        .find((link) => link.textContent === "查看原文")
        .getAttribute("href"),
      "/api/documents/doc-fixture/pdf#page=5",
    );
    assert.equal(panel.querySelector("details").open, true);
  } finally {
    await env.cleanup();
  }
});

test("missing-summary retry preserves prior sources on API failure and replaces the result on success", async () => {
  const env = setup();
  try {
    const waiting = task({
      status: "needs_summary",
      summary: {
        answer: "来源不足，请重试。",
        insufficient_evidence: true,
        citations: [],
      },
    });
    env.routes.set("GET /api/research/tasks", [waiting]);
    env.routes.set(
      "POST /api/research/tasks/task-fixture/retry",
      Response.json({ detail: "本地模型繁忙，请稍后重试" }, { status: 503 }),
    );
    await env.render(components.ResearchTasks, {
      changed: () => {},
      open: () => {},
    });
    assert.match(document.body.textContent, /综述状态/);
    assert.doesNotMatch(document.body.textContent, /有来源的摘要综述/);
    await click(button("重试摘要综述"));
    assert.match(document.body.textContent, /本地模型繁忙/);
    assert.match(document.body.textContent, /组件测试来源/);
    assert.equal(button("重试摘要综述").disabled, false);

    const completed = task({ attempt: 2 });
    env.routes.set("POST /api/research/tasks/task-fixture/retry", () => {
      env.routes.set("GET /api/research/tasks", [completed]);
      return completed;
    });
    await click(button("重试摘要综述"));
    assert.match(document.body.textContent, /第 2 次运行/);
    assert.match(document.body.textContent, /有来源的摘要综述/);
    assert.equal(
      [...document.querySelectorAll("button")].some(
        (item) => item.textContent === "重试摘要综述",
      ),
      false,
    );
    assert.equal(
      env.calls.filter((item) => item.url.endsWith("/retry")).length,
      2,
    );
  } finally {
    await env.cleanup();
  }
});

test("a running research task disables new starts and cancellation keeps its saved history", async () => {
  const env = setup();
  try {
    env.routes.set("GET /api/research/tasks", [
      task({ status: "running", summary: null }),
    ]);
    env.routes.set("POST /api/research/tasks/task-fixture/cancel", () => {
      const cancelled = task({ status: "cancelled", summary: null });
      env.routes.set("GET /api/research/tasks", [cancelled]);
      return cancelled;
    });
    await env.render(components.ResearchTasks, {
      changed: () => {},
      open: () => {},
    });
    assert.equal(button("开始研究任务").disabled, true);
    await click(button("取消后续步骤"));
    assert.match(document.body.textContent, /已取消/);
    assert.match(document.body.textContent, /模拟来源就绪/);
    assert.match(document.body.textContent, /组件测试来源/);
    assert.equal(button("开始研究任务").disabled, false);
    assert.equal(
      env.calls.filter((item) => item.url.endsWith("/cancel")).length,
      1,
    );
  } finally {
    await env.cleanup();
  }
});

test("writing exports preserve edited provenance, source snapshot and escaped diagram labels", async () => {
  const env = setup();
  try {
    const source = {
      document_id: "doc-fixture",
      page: 5,
      start: 0,
      end: 18,
      quote: "组件测试固定摘要，仅检验界面。",
      page_sha256: "a".repeat(64),
      url: "/api/documents/doc-fixture/pdf#page=5",
      preview_url: "/api/documents/doc-fixture/pages/5/image",
      method: "native",
    };
    const reference = {
      number: 1,
      label: "S1",
      document_id: "doc-fixture",
      title: "固定测试文献",
      authors: "Fixture Author",
      year: 2026,
      metadata_origin: "component-fixture",
      source_url: null,
      merged_versions: 1,
      evidence: source,
    };
    const result = {
      version: "writing-v1",
      generated_at: "2026-10-30T00:00:00+00:00",
      idea: "组件测试研究设想",
      fingerprint: "b".repeat(64),
      document_ids: ["doc-fixture"],
      markdown: "# 固定研究计划\n\n拟议方法，尚未开展实验。",
      references: [reference],
      skipped: [],
      checks: ["组件测试固定检查"],
      diagram: {
        kind: "proposed",
        nodes: [
          { id: "a", label: "<script> & 来源" },
          { id: "b", label: "评估计划" },
        ],
        edges: [{ source: "a", target: "b" }],
        mermaid: 'flowchart TD\n  a["来源"] --> b["评估计划"]',
      },
    };
    env.routes.set("POST /api/writing/outline", result);
    await env.render(components.WritingWorkspace, {
      documents: [
        {
          id: "doc-fixture",
          title: reference.title,
          status: "ready",
          is_active: 1,
          trashed_at: null,
          pages: 15,
          ocr_pages: 0,
          version: 1,
        },
      ],
    });
    await change(document.querySelector("#grounded-idea"), result.idea);
    await click(button("全选"));
    await click(button("生成有来源的论文框架"));
    assert.deepEqual(env.calls.find((item) => item.method === "POST").body, {
      idea: result.idea,
      document_ids: ["doc-fixture"],
    });
    assert.match(document.body.textContent, /第 5 页/);
    await click(button("编辑"));
    const edited = result.markdown + "\n\n人工补充：实验仍待开展。";
    await change(document.querySelector('[aria-label="编辑研究计划"]'), edited);
    await click(button("导出 Markdown"));
    await click(button("导出来源记录"));
    await click(button("导出 Mermaid"));
    await click(button("导出 SVG"));
    assert.deepEqual(
      env.downloads.map((file) => file.name),
      [
        "研析-研究计划.md",
        "研析-写作来源.json",
        "研析-方法架构.mmd",
        "研析-方法架构.svg",
      ],
    );
    const markdown = await env.downloads[0].blob.text();
    assert.ok(markdown.startsWith(edited));
    assert.ok(markdown.includes(result.fingerprint));
    assert.match(markdown, /用户已编辑；生成时检查不覆盖新增或修改的文字/);
    const provenance = JSON.parse(await env.downloads[1].blob.text());
    assert.equal(provenance.manually_edited, true);
    assert.equal(provenance.edited_markdown, edited);
    assert.equal(provenance.fingerprint, result.fingerprint);
    assert.deepEqual(provenance.references[0].evidence, source);
    assert.equal(await env.downloads[2].blob.text(), result.diagram.mermaid);
    const svg = new window.DOMParser().parseFromString(
      await env.downloads[3].blob.text(),
      "image/svg+xml",
    );
    assert.equal(svg.querySelector("parsererror"), null);
    assert.equal(svg.querySelector("script"), null);
    assert.ok(svg.documentElement.textContent.includes("<script> & 来源"));
  } finally {
    await env.cleanup();
  }
});

test("chat follow-ups carry only last successful source IDs and reset on new conversation or scope change", async () => {
  const env = setup();
  const doc = {
    id: "context-doc",
    title: "Context source",
    authors: "Test",
    status: "ready",
    created: "2026-10-30T00:00:00",
    pages: 1,
    warnings: [],
    is_active: true,
    trashed_at: null,
    filename: "context.pdf",
    similar_documents: [],
    abstract: "source",
  };
  env.routes.set("GET /api/documents", [doc]);
  env.routes.set("POST /api/chat", {
    answer: "Supported result",
    insufficient_evidence: false,
    steps: [],
    citations: [
      {
        id: "E1",
        document_id: doc.id,
        title: doc.title,
        page: 1,
        quote: "source",
        url: "/api/documents/context-doc/pdf#page=1",
      },
    ],
  });
  try {
    await env.render(components.App);
    await click(button("文献问答AI", document.querySelector(".sidebar")));
    const ask = async (text) => {
      await change(document.querySelector('[aria-label="输入文献问题"]'), text);
      await click(document.querySelector('[aria-label="发送问题"]'));
      return env.calls.filter((call) => call.url === "/api/chat").at(-1).body;
    };
    assert.deepEqual((await ask("第一问")).previous_document_ids, []);
    assert.deepEqual((await ask("其中Adam的参数？")).previous_document_ids, [
      doc.id,
    ]);
    await click(button("新对话"));
    assert.deepEqual((await ask("新问题")).previous_document_ids, []);
    await change(document.querySelector('[aria-label="问答文献范围"]'), doc.id);
    assert.deepEqual(
      (await ask("这篇用了什么GPU？")).previous_document_ids,
      [],
    );
    env.routes.set("POST /api/chat", {
      answer: "Evidence missing",
      insufficient_evidence: true,
      steps: [],
      citations: [],
    });
    await ask("这篇的其他内容？");
    assert.deepEqual((await ask("其中还有什么？")).previous_document_ids, []);
  } finally {
    await env.cleanup();
  }
});

test("following panel persists subscription preference and submits only explicitly selected reference IDs", async () => {
  const env = setup();
  const state = { subscriptions: [], batches: [] };
  env.routes.set("GET /api/following", () => state);
  env.routes.set("POST /api/following/subscriptions", (request) => {
    state.subscriptions.push({
      id: "subscription",
      query: request.body.query,
      cadence: request.body.cadence,
      enabled: true,
      last_run: null,
      next_run: 1,
      error: "",
      feedback: {},
      items: [],
    });
    return state.subscriptions[0];
  });
  env.routes.set("GET /api/documents/source/reference-candidates", {
    fingerprint: "f".repeat(64),
    references: 2,
    unresolved: 1,
    items: [
      {
        arxiv_id: "1706.03762v7",
        reference: "[1]",
        quote: "Source arXiv:1706.03762v7",
        pages: [10],
      },
    ],
  });
  env.routes.set("POST /api/documents/source/reference-batches", {
    id: "batch",
  });
  try {
    await env.render(components.FollowingPanel, {
      documents: [
        {
          id: "source",
          title: "Source",
          status: "ready",
          is_active: true,
          trashed_at: null,
        },
      ],
      open: () => {},
      changed: () => {},
    });
    await change(
      document.querySelector('[aria-label="订阅研究主题"]'),
      "retrieval augmented generation",
    );
    await change(document.querySelector('[aria-label="追踪频率"]'), "weekly");
    await submit(document.querySelector("form"));
    assert.deepEqual(
      env.calls.find((c) => c.url === "/api/following/subscriptions").body,
      { query: "retrieval augmented generation", cadence: "weekly" },
    );
    await change(
      document.querySelector('[aria-label="参考文献来源论文"]'),
      "source",
    );
    await click(button("读取参考文献"));
    assert.equal(button("下载所选参考文献（0）").disabled, true);
    await click(button("选择前30篇"));
    await click(button("下载所选参考文献（1）"));
    assert.deepEqual(
      env.calls.find((c) => c.url.endsWith("reference-batches")).body,
      { fingerprint: "f".repeat(64), arxiv_ids: ["1706.03762v7"] },
    );
  } finally {
    await env.cleanup();
  }
});

test("page structures show provenance, merged cells and original-image/CSV targets", async () => {
  const env = setup();
  try {
    env.routes.set("GET /api/documents/doc-a/pages/8/structure", {
      page: 8,
      note: "自动分列候选",
      items: [
        {
          id: "0",
          kind: "table",
          label: "Table 2",
          caption: "测试表题",
          text: "原文表格",
          bbox: [0.1, 0.2, 0.8, 0.5],
          warning: "导出前核验",
          rows: [
            ["Model", "BLEU"],
            ["A&B", "28.4"],
            ["跨列", null],
          ],
        },
        {
          id: "1",
          kind: "equation",
          label: "(1)",
          text: "f(x)=x",
          bbox: [0.2, 0.6, 0.8, 0.7],
          warning: "核对上下标",
        },
      ],
    });
    await env.render(components.PageStructure, {
      documentId: "doc-a",
      number: 8,
    });
    assert.equal(env.calls.length, 0, "analysis is on demand");
    await click(button("提取本页表格与编号公式"));
    assert.equal(document.querySelectorAll(".structure-item").length, 2);
    assert.ok(document.body.textContent.includes("28.4"));
    assert.ok(document.querySelector('span[title="原布局合并单元格"]'));
    assert.equal(
      document.querySelector(".structure-item img").getAttribute("src"),
      "/api/documents/doc-a/pages/8/structure/0/image",
    );
    assert.equal(
      document.querySelector("a[download]").getAttribute("href"),
      "/api/documents/doc-a/pages/8/structure/0/csv",
    );
    assert.equal(
      document.querySelector('a[target="_blank"]').getAttribute("href"),
      "/api/documents/doc-a/pdf#page=8",
    );
  } finally {
    await env.cleanup();
  }
});

test("multi-series charts export two lines and radar with shared scale and reject negatives", async () => {
  const env = setup();
  try {
    await charts(env);
    await change(
      document.querySelector("#csv-data"),
      "指标,方法甲,方法乙\n准确率,82,85\n召回率,70,75\nF1,76,80",
    );
    await click(button("折线图"));
    assert.equal(document.querySelectorAll(".chart-svg polyline").length, 2);
    await click(button("雷达图"));
    assert.equal(
      document.querySelectorAll(".chart-svg polygon[data-series]").length,
      2,
    );
    await click(button("导出 SVG"));
    const svg = await env.downloads[0].blob.text();
    assert.match(svg, /共同刻度 0–85/);
    assert.doesNotMatch(svg, /NaN|Infinity/);
    await click(button("导出图注"));
    const caption = await env.downloads[1].blob.text();
    assert.match(caption, /方法甲最高（82）/);
    assert.match(caption, /方法乙最高（85）/);
    await change(
      document.querySelector("#csv-data"),
      "指标,方法甲\n准确率,-1\n召回率,70\nF1,76",
    );
    assert.match(document.querySelector('[role="alert"]').textContent, /负值/);
    assert.equal(button("导出 SVG").disabled, true);
    await click(button("柱状图"));
    assert.ok(document.querySelector(".chart-svg"));
    await change(
      document.querySelector("#csv-data"),
      "指标,A,B\nx,-1e308,1e308\ny,1e308,-1e308",
    );
    assert.doesNotMatch(
      document.querySelector(".chart-svg").outerHTML,
      /NaN|Infinity/,
    );
  } finally {
    await env.cleanup();
  }
});

test("future research check submits exact scope and renders real-source fields without inventing novelty", async () => {
  const env = setup();
  try {
    env.routes.set("POST /api/graph/future-check", (request) => {
      assert.deepEqual(request.body, {
        topic: "retrieval evaluation",
        document_ids: ["doc-a"],
        fingerprint: "a".repeat(64),
      });
      return {
        topic: request.body.topic,
        checked_at: "2026-10-30T08:00:00Z",
        after_year: 2023,
        search_query: "retrieval evaluation",
        scope: "不是全网查新",
        origins: [],
        candidates: [
          {
            label: "E1",
            title: "New research",
            published: "2025-01-01",
            text: "Original abstract for exact-source review.",
            url: "https://arxiv.org/abs/2501.00001",
          },
        ],
        comparisons: [
          { source: "E1", answer: "来源受限的摘要概括。[E1]", supported: true },
        ],
      };
    });
    const props = {
      documentIds: ["doc-a"],
      fingerprint: "a".repeat(64),
      disabled: false,
    };
    await env.render(components.FutureCheckPanel, props);
    await change(
      document.querySelector("#future-topic"),
      "retrieval evaluation",
    );
    await submit(document.querySelector(".future-check form"));
    assert.match(document.body.textContent, /不是全网查新/);
    assert.equal(
      document.querySelector(".future-check-result article a").href,
      "https://arxiv.org/abs/2501.00001",
    );
    await click(button("导出核对结果"));
    const result = JSON.parse(await env.downloads[0].blob.text());
    assert.equal(result.candidates[0].published, "2025-01-01");
    await env.render(components.FutureCheckPanel, { ...props, disabled: true });
    assert.equal(document.querySelector(".future-check-result"), null);
    assert.equal(button("核对更新研究").disabled, true);
  } finally {
    await env.cleanup();
  }
});
