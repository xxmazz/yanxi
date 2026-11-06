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

test("graph defaults to at most 100 PDFs, supports replacing a selection, and explains validation errors", async () => {
  const env = setup();
  const documents = Array.from({ length: 104 }, (_, i) => ({
    id: `graph-doc-${i}`,
    title: `Graph source ${i}`,
    status: "ready",
    is_active: 1,
    trashed_at: null,
    version: 1,
    pages: 1,
    ocr_pages: 0,
  }));
  env.routes.set("POST /api/graph/build", () =>
    Response.json(
      {
        detail: [
          {
            type: "too_long",
            loc: ["body", "document_ids"],
            msg: "List should have at most 100 items",
            ctx: { max_length: 100 },
          },
        ],
      },
      { status: 422 },
    ),
  );
  try {
    await env.render(components.CitationGraph, { documents, open() {} });
    assert.match(document.body.textContent, /当前选择 100 \/ 104 个 PDF/);
    const boxes = () => [
      ...document.querySelectorAll(
        '.graph-document-picker input[type="checkbox"]',
      ),
    ];
    assert.equal(boxes().filter((box) => box.checked).length, 100);
    assert.equal(boxes()[100].disabled, true);
    await click(boxes()[0]);
    assert.equal(boxes()[100].disabled, false);
    await click(boxes()[100]);
    assert.equal(boxes()[0].disabled, true);
    await click(button("构建引用图谱"));
    const request = env.calls.find((call) => call.url === "/api/graph/build");
    assert.equal(request.body.document_ids.length, 100);
    assert.equal(request.body.document_ids.includes("graph-doc-0"), false);
    assert.equal(request.body.document_ids.includes("graph-doc-100"), true);
    assert.match(
      document.querySelector('[role="alert"]').textContent,
      /最多选择 100 个 PDF/,
    );
    assert.doesNotMatch(document.body.textContent, /\[object Object\]/);
    await click(button("清空选择"));
    assert.equal(button("构建引用图谱").disabled, true);
    await click(button("选择前 100 篇"));
    assert.equal(boxes().filter((box) => box.checked).length, 100);
  } finally {
    await env.cleanup();
  }
});

test("graph selects all 34 PDFs and does not enable neighbor-only filtering by default", async () => {
  const env = setup();
  const documents = Array.from({ length: 34 }, (_, i) => ({
    id: `scope-${i}`,
    title: `Scientific source ${i}`,
    status: "ready",
    is_active: 1,
    trashed_at: null,
    version: 1,
    pages: 1,
    ocr_pages: 0,
  }));
  env.routes.set("POST /api/graph/build", (request) => ({
    fingerprint: "a".repeat(64),
    generated_at: "2026-11-07",
    document_ids: request.body.document_ids,
    nodes: [],
    edges: [],
    stats: { documents: 34, works: 0, edges: 0, components: 0, isolated: 0 },
    warnings: [],
    interpretation: "Component fixture",
  }));
  try {
    await env.render(components.CitationGraph, { documents, open() {} });
    assert.match(document.body.textContent, /当前选择 34 \/ 34 个 PDF/);
    await click(button("构建引用图谱"));
    const request = env.calls.find((call) => call.url === "/api/graph/build");
    assert.equal(request.body.document_ids.length, 34);
    const toggle = document.querySelector(".graph-neighbor-toggle input");
    assert.ok(toggle);
    assert.equal(toggle.checked, false);
    await click(toggle);
    assert.equal(toggle.checked, true);
  } finally {
    await env.cleanup();
  }
});

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

test("immersive reading opens the latest real PDF and keeps explicit paper selection", async () => {
  const env = setup();
  const first = {
    id: "reader-first",
    title: "A real paper",
    authors: "Author",
    pages: 3,
    status: "ready",
    is_active: 1,
    trashed_at: null,
    version: 1,
    created: "2026-11-07T00:00:00",
    filename: "first.pdf",
    warnings: [],
    similar_documents: [],
    abstract: "Real abstract",
  };
  const second = {
    ...first,
    id: "reader-attention",
    title: "Attention Is All You Need",
    created: "2026-11-06T00:00:00",
  };
  env.routes.set("GET /api/documents", [first, second]);
  for (const doc of [first, second]) {
    env.routes.set(
      `GET /api/documents/${doc.id}/pdf`,
      () =>
        new Response("%PDF-1.4 fixture", {
          headers: { "Content-Type": "application/pdf" },
        }),
    );
  }
  env.routes.set(`GET /api/documents/${first.id}/sources`, []);
  env.routes.set(`GET /api/documents/${first.id}/pages/1`, {
    page: 1,
    text: "Real first page text.",
    method: "native",
    blocks: [],
  });
  env.routes.set(`GET /api/documents/${first.id}/reading-links`, {
    document_id: first.id,
    fingerprint: "fixture",
    entries: [],
    mentions: [],
    outline: [],
    limitations: "",
  });
  try {
    await env.render(components.App);
    await click(button("沉浸阅读", document.querySelector(".sidebar")));
    const picker = document.querySelector('[aria-label="选择阅读文献"]');
    assert.equal(picker.value, first.id);
    assert.deepEqual(
      [...picker.options].map((option) => option.value),
      [first.id, second.id],
    );
    assert.equal(
      document.querySelector(".local-preview iframe").dataset.pdfSource,
      `/api/documents/${first.id}/pdf`,
    );
    assert.doesNotMatch(
      document.body.textContent,
      /固定阅读样例|READING SAMPLE|演示释义/,
    );
    assert.equal(document.querySelector(".document-paper"), null);
    await change(picker, second.id);
    assert.equal(
      document.querySelector(".local-preview iframe").title,
      second.title,
    );
    await change(picker, first.id);
    assert.equal(
      document.querySelector(".local-preview iframe").title,
      first.title,
    );
    await click(button("逐页阅读与翻译"));
    assert.match(
      document.querySelector(".parsed-reader").textContent,
      /Real first page text/,
    );
    await click(button("探索发现", document.querySelector(".sidebar")));
    await click(button("沉浸阅读", document.querySelector(".sidebar")));
    assert.equal(
      document.querySelector(".local-preview iframe").dataset.pdfSource,
      `/api/documents/${first.id}/pdf`,
    );
    await click(button("问这篇论文"));
    assert.equal(
      document.querySelector('[aria-label="问答文献范围"]').value,
      first.id,
    );
  } finally {
    await env.cleanup();
  }
});

test("immersive reading with no imported PDF asks for a file instead of showing fixed text", async () => {
  const env = setup();
  try {
    await env.render(components.App);
    await click(button("沉浸阅读", document.querySelector(".sidebar")));
    assert.match(document.body.textContent, /导入 PDF 后即可阅读/);
    assert.equal(document.querySelector("iframe"), null);
    assert.equal(document.querySelector(".document-paper"), null);
    assert.doesNotMatch(
      document.body.textContent,
      /固定阅读样例|READING SAMPLE/,
    );
  } finally {
    await env.cleanup();
  }
});

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

test("discovery results appear directly below search and before tracking and research tasks", async () => {
  const env = setup();
  env.routes.set("GET /api/discovery/search?q=demo&start=0&sort=relevance", {
    query: "demo",
    search_query: "demo",
    total: 1,
    start: 0,
    page_size: 8,
    cached: false,
    fetched_at: "2026-11-07T00:00:00Z",
    steps: ["检索官方论文"],
    sort: "relevance",
    library_count: 0,
    library_ranked: false,
    items: [
      {
        arxiv_id: "2401.00001",
        title: "Visible search result",
        abstract: "Example abstract",
        authors: ["Example Author"],
        published: "2024-01-01",
        updated: "2024-01-01",
        categories: ["cs.CL"],
        url: "https://arxiv.org/abs/2401.00001",
        code_urls: [],
      },
    ],
  });
  try {
    await env.render(components.App);
    await click(button("探索发现", document.querySelector(".sidebar")));
    await change(document.querySelector("#discovery-query"), "demo");
    await submit(document.querySelector(".discovery-search form"));
    const search = document.querySelector(".discovery-search");
    const summary = document.querySelector(".discovery-result-summary");
    const list = document.querySelector(".discovery-list");
    const following = document.querySelector(".following-panel");
    assert.equal(search.nextElementSibling, summary);
    assert.equal(summary.nextElementSibling, list);
    assert.match(list.textContent, /Visible search result/);
    assert.ok(
      list.compareDocumentPosition(following) &
        window.Node.DOCUMENT_POSITION_FOLLOWING,
    );
    assert.ok(
      following.compareDocumentPosition(
        document.querySelector(".research-task-panel"),
      ) & window.Node.DOCUMENT_POSITION_FOLLOWING,
    );
  } finally {
    await env.cleanup();
  }
});

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
    assert.match(panel.textContent, /摘要综述/);
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
    assert.equal(
      [...document.querySelectorAll(".research-task-panel h3")].some(
        (heading) => heading.textContent === "摘要综述",
      ),
      false,
    );
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
    assert.match(document.body.textContent, /摘要综述/);
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
    await click(button("生成研究计划"));
    assert.deepEqual(env.calls.find((item) => item.method === "POST").body, {
      idea: result.idea,
      document_ids: ["doc-fixture"],
    });
    assert.match(document.body.textContent, /第 5 页/);
    assert.equal(
      document.querySelector(".writing-diagram").nextElementSibling,
      document.querySelector(".writing-evidence"),
    );
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
    await click(button("文献问答", document.querySelector(".sidebar")));
    const ask = async (text) => {
      await change(document.querySelector('[aria-label="输入文献问题"]'), text);
      await click(document.querySelector('[aria-label="发送问题"]'));
      return env.calls.filter((call) => call.url === "/api/chat").at(-1).body;
    };
    assert.deepEqual(
      [...document.querySelectorAll(".suggestion-grid button")].map((item) =>
        item.textContent.replace(/^0\d/, "").trim(),
      ),
      [
        "所选文献的主要研究问题是什么？",
        "所选文献使用了哪些实验设置？",
        "所选文献提出了哪些局限？",
      ],
    );
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
      answer_mode: "model_explanation",
      steps: [],
      citations: [],
    });
    await ask("这篇的其他内容？");
    assert.match(document.body.textContent, /补充分析 · 暂无已核验的原文引用/);
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

const unresolvedReference = {
  reference: "[11]",
  title: "Deep residual learning for image recognition",
  authors: "Kaiming He, Xiangyu Zhang",
  year: 2016,
  quote:
    "[11] Kaiming He, Xiangyu Zhang. Deep residual learning for image recognition. 2016.",
  pages: [10],
  resolution: null,
};
const resolvedReference = {
  reference: "[11]",
  status: "matched",
  search_title: unresolvedReference.title,
  checked_at: "2026-10-30T00:00:00Z",
  note: "核对标题作者与年份",
  candidates: [
    {
      title: "Deep Residual Learning for Image Recognition",
      arxiv_id: "1512.03385v1",
      authors: ["Kaiming He", "Xiangyu Zhang"],
      published: "2015-12-10",
      url: "https://arxiv.org/abs/1512.03385v1",
      match: { verified: true, reasons: ["完整标题一致", "作者及年份核对"] },
    },
  ],
};

test("reference title matching refreshes candidates but never starts a download without selection", async () => {
  const env = setup();
  let matched = false;
  env.routes.set("GET /api/documents/source/reference-candidates", () => ({
    fingerprint: "f".repeat(64),
    references: 1,
    unresolved: matched ? 0 : 1,
    resolved_by_title: matched ? 1 : 0,
    unresolved_entries: matched ? [] : [unresolvedReference],
    items: matched
      ? [
          {
            arxiv_id: "1512.03385v1",
            reference: "[11]",
            quote: unresolvedReference.quote,
            pages: [10],
            title: resolvedReference.candidates[0].title,
            match_method: "title-authors-year",
            match: resolvedReference.candidates[0].match,
            metadata_url: resolvedReference.candidates[0].url,
          },
        ]
      : [],
  }));
  env.routes.set(
    "POST /api/documents/source/reference-resolutions",
    (request) => {
      assert.deepEqual(request.body, {
        reference: "[11]",
        fingerprint: "f".repeat(64),
      });
      matched = true;
      return resolvedReference;
    },
  );
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
      document.querySelector('[aria-label="参考文献来源论文"]'),
      "source",
    );
    await click(button("读取参考文献"));
    assert.equal(button("下载所选参考文献（0）").disabled, true);
    await click(button("匹配前5条无编号书目"));
    assert.ok(document.body.textContent.includes("由题名核对"));
    assert.equal(
      env.calls.filter((call) => call.url.endsWith("reference-batches")).length,
      0,
    );
    assert.equal(button("下载所选参考文献（0）").disabled, true);
    await click(button("选择前30篇"));
    await click(button("下载所选参考文献（1）"));
    assert.deepEqual(
      env.calls.find((call) => call.url.endsWith("reference-batches")).body,
      { fingerprint: "f".repeat(64), arxiv_ids: ["1512.03385v1"] },
    );
  } finally {
    await env.cleanup();
  }
});

test("ambiguous bibliographic matches remain review-only and show official candidate links", async () => {
  const env = setup();
  let refreshed = 0;
  env.routes.set("POST /api/documents/source/reference-resolutions", {
    ...resolvedReference,
    status: "ambiguous",
  });
  try {
    await env.render(components.ReferenceMatching, {
      documentId: "source",
      fingerprint: "f".repeat(64),
      entries: [unresolvedReference],
      disabled: false,
      pending: () => {},
      resolved: async () => {
        refreshed++;
      },
    });
    await click(button("匹配此书目"));
    assert.match(document.body.textContent, /有多个身份同时符合，暂不加入下载/);
    assert.equal(
      document.querySelector(".reference-match-entry a").href,
      "https://arxiv.org/abs/1512.03385v1",
    );
    assert.equal(refreshed, 1);
    assert.equal(env.calls.length, 1);
  } finally {
    await env.cleanup();
  }
});

const hoverText =
  "🙂 Preface. The model uses 32 GPUs and a learning rate of 0.0003. Another sentence explains attention mechanisms.";
const hoverPage = { page: 1, text: hoverText, method: "native", blocks: [] };
function hoverRoutes(env) {
  env.routes.set("GET /api/documents/hover-paper/reading-links", {
    document_id: "hover-paper",
    fingerprint: "a".repeat(64),
    entries: [],
    mentions: [],
    limitations: "",
  });
}
async function activateHover(element, key = "click") {
  assert(element, "hover source exists");
  await act(async () =>
    element.dispatchEvent(
      key === "click"
        ? new MouseEvent("click", { bubbles: true })
        : new window.KeyboardEvent("keydown", {
            key,
            bubbles: true,
            cancelable: true,
          }),
    ),
  );
}
function hoverJob(request, overrides = {}) {
  return {
    id: "hover-job",
    status: "ready",
    source_text: request.body.text,
    target: request.body.target,
    result: "该模型使用32块GPU，学习率为0.0003。",
    error: "",
    ...overrides,
  };
}

test("hover sentence offsets preserve Unicode and exclude complete formula/code spans", () => {
  const wrapped =
    "Figure 1: Architecture.\nThe Transformer uses fully\nconnected layers in both\nencoder and decoder, respectively.\n3.1 Encoder";
  const sentence = components
    .hoverRanges(wrapped, 0, Array.from(wrapped).length)
    .find((range) => range.text.includes("The Transformer"));
  assert.match(sentence.text, /fully\nconnected/);
  assert.match(sentence.text, /respectively\./);
  assert.doesNotMatch(sentence.text, /3\.1 Encoder/);
  const text =
    "🙂 The method is described here. $x=2$ Another explanation follows here.\n```python\nx = 3\n```\n" +
    "Long technical sentence ".repeat(70);
  const chars = Array.from(text);
  const ranges = components.hoverRanges(text, 0, chars.length);
  assert(ranges.length > 3);
  for (const range of ranges) {
    assert.equal(chars.slice(range.start, range.end).join(""), range.text);
    assert(Array.from(range.text).length <= 700);
    assert.doesNotMatch(range.text, /\$x=2\$|```|x = 3/);
  }
  assert(ranges.some((range) => range.text.includes("Another explanation")));
  assert(ranges.some((range) => range.text.includes("Long technical")));
});

test("hover translation is opt-in, verifies exact source selection, caches per language and preserves body", async () => {
  const env = setup();
  hoverRoutes(env);
  env.routes.set(
    "POST /api/documents/hover-paper/translation/selection/1",
    (request) => hoverJob(request),
  );
  try {
    await env.render(components.ReadingLinks, {
      documentId: "hover-paper",
      page: hoverPage,
      jump() {},
    });
    assert.equal(document.querySelector(".hover-translation-source"), null);
    assert.equal(
      document.querySelector(".reference-page-text").textContent,
      hoverText,
    );
    await click(button("开启悬停翻译"));
    const source = [
      ...document.querySelectorAll(".hover-translation-source"),
    ].find((node) => node.textContent.includes("32 GPUs"));
    await activateHover(source);
    const requests = () => env.calls.filter((call) => call.method === "POST");
    assert.equal(requests().length, 1);
    const selected = requests()[0].body;
    assert.equal(
      Array.from(hoverText).slice(selected.start, selected.end).join(""),
      selected.text,
    );
    assert.equal(selected.target, "zh");
    assert.match(
      document.querySelector(".hover-translation-result").textContent,
      /0\.0003/,
    );
    await click(
      button("关闭", document.querySelector(".hover-translation-result")),
    );
    await activateHover(source, "Enter");
    assert.equal(requests().length, 1);
    assert.match(
      document.querySelector(".hover-translation-result").textContent,
      /会话缓存/,
    );
    assert.equal(
      document.querySelector(".reference-page-text").textContent,
      hoverText,
    );
    await change(document.querySelector('[aria-label="悬停译文语言"]'), "en");
    assert.equal(document.querySelector(".hover-translation-result"), null);
    await activateHover(
      document.querySelectorAll(".hover-translation-source")[1],
    );
    assert.equal(requests().length, 2);
    assert.equal(requests()[1].body.target, "en");
    await activateHover(
      document.querySelectorAll(".hover-translation-source")[1],
      "Escape",
    );
    assert.equal(document.querySelector(".hover-translation-result"), null);
  } finally {
    await env.cleanup();
  }
});

test("leaving a sentence before the hover delay makes no model request", async () => {
  const env = setup();
  hoverRoutes(env);
  try {
    await env.render(components.ReadingLinks, {
      documentId: "hover-paper",
      page: hoverPage,
      jump() {},
    });
    await click(button("开启悬停翻译"));
    const source = document.querySelector(".hover-translation-source");
    await act(async () => {
      source.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
      source.dispatchEvent(new MouseEvent("mouseout", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 650));
    });
    assert.equal(env.calls.filter((call) => call.method === "POST").length, 0);
  } finally {
    await env.cleanup();
  }
});

test("late hover results cannot appear on another page and only one source request is in flight", async () => {
  const env = setup();
  hoverRoutes(env);
  let finish;
  env.routes.set(
    "POST /api/documents/hover-paper/translation/selection/1",
    (request) =>
      new Promise((resolve) => {
        finish = () => resolve(hoverJob(request));
      }),
  );
  try {
    const props = { documentId: "hover-paper", page: hoverPage, jump() {} };
    await env.render(components.ReadingLinks, props);
    await click(button("开启悬停翻译"));
    const sources = document.querySelectorAll(".hover-translation-source");
    await activateHover(sources[0]);
    await activateHover(sources[1]);
    assert.equal(env.calls.filter((call) => call.method === "POST").length, 1);
    assert.match(
      document.querySelector(".hover-translation-controls").textContent,
      /请完成后/,
    );
    await env.render(components.ReadingLinks, {
      ...props,
      page: {
        ...hoverPage,
        page: 2,
        text: "A completely different page with new evidence.",
      },
    });
    await act(async () => finish());
    assert.equal(document.querySelector(".hover-translation-result"), null);
    assert.match(
      document.querySelector(".reference-page-text").textContent,
      /different page/,
    );
    assert.doesNotMatch(
      document.querySelector(".reference-page-text").textContent,
      /32 GPUs/,
    );
  } finally {
    await env.cleanup();
  }
});

test("invalid hover job identity shows an error and retries only on explicit request", async () => {
  const env = setup();
  hoverRoutes(env);
  let wrong = true;
  env.routes.set(
    "POST /api/documents/hover-paper/translation/selection/1",
    (request) =>
      hoverJob(request, wrong ? { source_text: "Other source" } : {}),
  );
  try {
    await env.render(components.ReadingLinks, {
      documentId: "hover-paper",
      page: hoverPage,
      jump() {},
    });
    await click(button("开启悬停翻译"));
    const source = document.querySelectorAll(".hover-translation-source")[1];
    await activateHover(source);
    assert.match(
      document.querySelector('[role="alert"]').textContent,
      /不一致/,
    );
    await activateHover(source);
    assert.equal(env.calls.filter((call) => call.method === "POST").length, 1);
    wrong = false;
    await click(button("重试选段翻译"));
    assert.equal(env.calls.filter((call) => call.method === "POST").length, 2);
    assert.match(
      document.querySelector(".hover-translation-result").textContent,
      /0\.0003/,
    );
  } finally {
    await env.cleanup();
  }
});

async function drawRegion(canvas, low = [0.1, 0.1], high = [0.9, 0.5]) {
  canvas.getBoundingClientRect = () => ({
    left: 0,
    top: 0,
    width: 1000,
    height: 1000,
  });
  canvas.setPointerCapture = () => {};
  await act(async () =>
    canvas.dispatchEvent(
      new MouseEvent("pointerdown", {
        bubbles: true,
        clientX: low[0] * 1000,
        clientY: low[1] * 1000,
      }),
    ),
  );
  await act(async () =>
    canvas.dispatchEvent(
      new MouseEvent("pointerup", {
        bubbles: true,
        clientX: high[0] * 1000,
        clientY: high[1] * 1000,
      }),
    ),
  );
}
function visualResult(page, kind, rows) {
  return {
    document_id: "visual-paper",
    page,
    source_hash: "f".repeat(64),
    model: "local-vision",
    checked_at: "2026-10-31T00:00:00+08:00",
    cached: false,
    items: [
      {
        kind,
        label: "table",
        bbox: [0.1, 0.1, 0.9, 0.5],
        rows,
        latex: kind === "equation" ? "\\frac{x_1}{y^2}=1" : undefined,
        warning: "核对原图",
      },
    ],
  };
}

test("cross-page table joining removes only an identical header and refuses incompatible columns", () => {
  const a = visualResult(1, "table", [
    ["Model", "Score"],
    ["Alpha", "10"],
  ]);
  const b = visualResult(2, "table", [
    ["Model", "Score"],
    ["Beta", "20"],
  ]);
  assert.deepEqual(components.joinVisualTables([a, b]), [
    ["Model", "Score"],
    ["Alpha", "10"],
    ["Beta", "20"],
  ]);
  assert.equal(
    components.joinVisualTables([
      a,
      visualResult(2, "table", [
        ["Other", "Score"],
        ["Beta", "20"],
      ]),
    ]).length,
    4,
  );
  assert.throws(
    () =>
      components.joinVisualTables([
        a,
        visualResult(2, "table", [
          ["x", "y", "z"],
          ["1", "2", "3"],
        ]),
      ]),
    /列数不同/,
  );
  const csv = components.visualCSV([["=HYPERLINK(x)", "-1.5", null, 'a"b']]);
  assert.match(csv, /'=HYPERLINK/);
  assert.match(csv, /"-1\.5"/);
  assert.match(csv, /a""b/);
});

test("visual picker submits only the drawn formula region and exports source-bound LaTeX", async () => {
  const env = setup();
  env.routes.set(
    "POST /api/documents/visual-paper/pages/1/visual-structure",
    (request) => {
      assert.deepEqual(request.body.bbox, [0.1, 0.1, 0.9, 0.5]);
      assert.equal(request.body.kind, "equation");
      return visualResult(1, "equation");
    },
  );
  try {
    await env.render(components.VisualStructure, {
      documentId: "visual-paper",
      number: 1,
      pages: 2,
    });
    await click(button("扫描表格 / 公式 LaTeX 识别"));
    assert.equal(button("识别所选区域").disabled, true);
    await change(
      document.querySelector('[aria-label="图片识别类型"]'),
      "equation",
    );
    await drawRegion(document.querySelector(".visual-region-picker"));
    await click(button("识别所选区域"));
    assert.match(
      document.querySelector(".visual-structure pre").textContent,
      /\\frac/,
    );
    await click(button("导出公式 LaTeX"));
    assert.match(await env.downloads[0].blob.text(), /Physical page: 1/);
    assert.match(await env.downloads[0].blob.text(), /\\frac\{x_1\}/);
    await click(button("导出识别结果与出处"));
    assert.equal(
      JSON.parse(await env.downloads[1].blob.text())[0].source_hash,
      "f".repeat(64),
    );
  } finally {
    await env.cleanup();
  }
});

test("visual cross-page selection preserves page-specific regions before sequential inference", async () => {
  const env = setup();
  env.routes.set(
    "POST /api/documents/visual-paper/pages/1/visual-structure",
    () =>
      visualResult(1, "table", [
        ["Model", "Score"],
        ["Alpha", "10"],
      ]),
  );
  env.routes.set(
    "POST /api/documents/visual-paper/pages/2/visual-structure",
    () =>
      visualResult(2, "table", [
        ["Model", "Score"],
        ["Beta", "20"],
      ]),
  );
  try {
    await env.render(components.VisualStructure, {
      documentId: "visual-paper",
      number: 1,
      pages: 2,
    });
    await click(button("扫描表格 / 公式 LaTeX 识别"));
    await drawRegion(document.querySelector(".visual-region-picker"));
    await click(button("保留选区并选择下一页续表"));
    assert.match(
      document.querySelector(".visual-region-picker img").src,
      /render\/2$/,
    );
    await drawRegion(
      document.querySelector(".visual-region-picker"),
      [0.2, 0.2],
      [0.8, 0.6],
    );
    await click(button("识别所选区域"));
    const calls = env.calls.filter((call) => call.method === "POST");
    assert.deepEqual(
      calls.map((call) => call.body.bbox),
      [
        [0.1, 0.1, 0.9, 0.5],
        [0.2, 0.2, 0.8, 0.6],
      ],
    );
    assert.equal(document.querySelectorAll(".visual-structure tr").length, 3);
    await click(button("导出识别表格 CSV"));
    const csv = await env.downloads[0].blob.text();
    assert.match(csv, /Alpha/);
    assert.match(csv, /Beta/);
    assert.equal(csv.match(/Model/g).length, 1);
    await click(button("清空选区"));
    assert.equal(document.querySelectorAll(".visual-structure tr").length, 0);
  } finally {
    await env.cleanup();
  }
});

test("fulltext check sends explicit comparison scope and exports page evidence", async () => {
  const env = setup();
  env.routes.set("GET /api/documents", [
    { id: "older", title: "Original", status: "ready" },
    { id: "newer", title: "Newer paper", status: "ready" },
  ]);
  env.routes.set("POST /api/graph/fulltext-check", {
    topic: "attention evaluation",
    checked_at: "2026-10-31",
    scope: "正文核对范围",
    after_year: 2020,
    origins: [],
    groups: [
      {
        title: "Newer paper",
        year: 2023,
        indexed_chunks: 17,
        supported: true,
        answer: "本篇研究注意力评估。",
        citations: [
          {
            document_id: "newer",
            page: 3,
            quote: "We evaluate attention.",
            url: "/api/documents/newer/pdf#page=3",
          },
        ],
        evidence: [
          {
            label: "E1",
            title: "Newer paper",
            text: "We evaluate attention.",
            page: 3,
            url: "/api/documents/newer/pdf#page=3",
          },
        ],
      },
    ],
  });
  try {
    await env.render(components.FulltextCheckPanel, {
      documentIds: ["older"],
      fingerprint: "a".repeat(64),
      disabled: false,
    });
    assert.equal(document.querySelectorAll(".fulltext-source input").length, 1);
    await change(
      document.querySelector('[aria-label="正文核对主题"]'),
      "attention evaluation",
    );
    await click(document.querySelector(".fulltext-source input"));
    await submit(document.querySelector(".fulltext-check form"));
    const request = env.calls.find((call) => call.method === "POST");
    assert.deepEqual(request.body, {
      document_ids: ["older"],
      fingerprint: "a".repeat(64),
      target_document_ids: ["newer"],
      topic: "attention evaluation",
    });
    assert.equal(
      document
        .querySelector(".fulltext-results blockquote a")
        .getAttribute("href"),
      "/api/documents/newer/pdf#page=3",
    );
    await click(button("导出正文核对结果"));
    assert.equal(
      JSON.parse(await env.downloads[0].blob.text()).groups[0].citations[0]
        .quote,
      "We evaluate attention.",
    );
  } finally {
    await env.cleanup();
  }
});
