import { useEffect, useRef, useState } from "react";
import { ArrowDownToLine, ArrowRight, BookOpen, RefreshCw } from "lucide-react";
import { api } from "./api";
import { FutureCheckPanel } from "./future-check";
import type { ServerDocument, Evidence } from "./api";

type Source = {
  document_id: string;
  page: number;
  start: number;
  end: number;
  quote: string;
  page_sha256: string;
  method: string;
  url: string;
  preview_url: string;
};
type Node = {
  id: string;
  document_id: string;
  title: string;
  authors: string;
  year: number | null;
  documents: {
    id: string;
    title: string;
    version: number;
    ocr_pages: number;
    pages: number;
  }[];
  arxiv_ids: string[];
  in_degree: number;
  out_degree: number;
  pagerank: number;
  role: string;
  x: number;
  y: number;
  reference_pages: number[];
  abstract_evidence: Source | null;
  future_work: Source[];
};
type Edge = {
  source: string;
  target: string;
  match_method: string;
  matched_text: string;
  evidence: Source[];
};
type Graph = {
  fingerprint: string;
  generated_at: string;
  document_ids: string[];
  nodes: Node[];
  edges: Edge[];
  stats: {
    documents: number;
    works: number;
    edges: number;
    components: number;
    isolated: number;
  };
  warnings: string[];
  interpretation: string;
};
type Review = {
  fingerprint: string;
  answer: string;
  citations: Evidence[];
  insufficient_evidence: boolean;
  covered_documents: string[];
};

function save(name: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function label(title: string) {
  const known = [
    ["attention is all you need", "Transformer"],
    ["retrieval-augmented generation for", "RAG"],
    ["ragas:", "Ragas"],
    ["bert:", "BERT"],
    ["dense passage retrieval", "DPR"],
    ["layer normalization", "LayerNorm"],
    ["improving language understanding", "GPT-1"],
    ["language models are unsupervised", "GPT-2"],
    ["language models are few-shot", "GPT-3"],
    ["scaling laws for neural", "Scaling Laws"],
    ["exploring the limits of transfer", "T5"],
    ["lora:", "LoRA"],
    ["evaluating large language models trained on code", "Codex"],
    ["chain-of-thought prompting", "CoT"],
    ["training language models to follow instructions", "InstructGPT"],
    ["emergent abilities of", "Emergent Abilities"],
    ["flashattention", "FlashAttention"],
    ["bloom:", "BLOOM"],
    ["llama:", "LLaMA"],
    ["llama 2:", "Llama 2"],
    ["qwen2.5", "Qwen2.5"],
    ["deepseek-v3", "DeepSeek-V3"],
    ["deepseek-r1", "DeepSeek-R1"],
  ];
  return (
    known.find(([prefix]) => title.toLowerCase().startsWith(prefix))?.[1] ||
    (title.length > 28 ? title.slice(0, 26) + "…" : title)
  );
}

function badgeLines(title: string) {
  const name = label(title);
  const split: Record<string, string[]> = {
    "Scaling Laws": ["Scaling", "Laws"],
    "Emergent Abilities": ["Emergent", "Abilities"],
    FlashAttention: ["Flash", "Attention"],
    "DeepSeek-V3": ["DeepSeek", "V3"],
    "DeepSeek-R1": ["DeepSeek", "R1"],
  };
  if (split[name]) return split[name];
  return name.length <= 12
    ? [name]
    : [name.slice(0, 11), name.slice(11, 21) + (name.length > 21 ? "…" : "")];
}

function SourceDisclosure({ source, name }: { source: Source; name: string }) {
  const [preview, setPreview] = useState(false);
  return (
    <details className="graph-source">
      <summary>
        {name} · 物理第 {source.page} 页
        {source.method === "ocr" ? " · OCR" : ""}
      </summary>
      <blockquote>{source.quote}</blockquote>
      <div className="action-row">
        <button onClick={() => setPreview((value) => !value)}>
          {preview ? "收起原页" : "查看原页"}
        </button>
        <a href={source.url} target="_blank" rel="noreferrer">
          原始 PDF · 第 {source.page} 页
        </a>
      </div>
      {preview && (
        <img
          loading="lazy"
          alt={`引用出处原页 · 第 ${source.page} 页`}
          src={source.preview_url}
        />
      )}
    </details>
  );
}

export function CitationGraph({
  documents,
  open,
}: {
  documents: ServerDocument[];
  open: (id: string) => void;
}) {
  const ready = documents.filter(
    (doc) => doc.status === "ready" && doc.is_active && !doc.trashed_at,
  );
  const signature = JSON.stringify(
    ready
      .map((doc) => ({
        id: doc.id,
        title: doc.title,
        version: doc.version,
        ocr: doc.ocr_pages,
      }))
      .sort((a, b) => a.id.localeCompare(b.id)),
  );
  const [selected, setSelected] = useState<string[] | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [focus, setFocus] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [connectedOnly, setConnectedOnly] = useState(false);
  const [adjacentOnly, setAdjacentOnly] = useState(true);
  const [ranking, setRanking] = useState("pagerank");
  const generation = useRef(0);
  const pendingScope = selected ?? ready.map((doc) => doc.id);
  const current = useRef("");
  current.current = JSON.stringify([...pendingScope].sort());
  const lastLibrary = useRef(signature);
  const stale =
    !!graph &&
    (current.current !== JSON.stringify([...graph.document_ids].sort()) ||
      lastLibrary.current !== signature);
  const node = graph?.nodes.find((item) => item.id === focus) || null;
  const graphNodes =
    graph?.nodes.filter(
      (item) => !connectedOnly || item.in_degree + item.out_degree > 0,
    ) || [];
  const visible = new Set(graphNodes.map((item) => item.id));
  const edges =
    graph?.edges.filter(
      (edge) => visible.has(edge.source) && visible.has(edge.target),
    ) || [];
  const ranked = [...(graph?.nodes || [])].sort((a, b) =>
    ranking === "degree"
      ? b.in_degree - a.in_degree || b.pagerank - a.pagerank
      : b.pagerank - a.pagerank || b.in_degree - a.in_degree,
  );
  const connectedEdges =
    graph?.edges.filter(
      (edge) => edge.source === focus || edge.target === focus,
    ) || [];

  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  const build = async () => {
    if (busy || reviewBusy) return;
    if (!pendingScope.length) {
      setError("请选择至少一篇已解析文献");
      return;
    }
    const version = ++generation.current;
    const scope = current.current;
    setBusy(true);
    setError("");
    setReview(null);
    try {
      const result = await api<Graph>("/api/graph/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_ids: pendingScope }),
      });
      if (generation.current !== version || current.current !== scope) return;
      setGraph(result);
      lastLibrary.current = signature;
      setFocus(
        [...result.nodes].sort((a, b) => b.pagerank - a.pagerank)[0]?.id || "",
      );
    } catch (reason) {
      if (generation.current === version)
        setError(reason instanceof Error ? reason.message : "构图失败");
    } finally {
      if (generation.current === version) setBusy(false);
    }
  };

  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">FOLLOW THE REFERENCES</div>
          <h1>引用图谱</h1>
          <p>沿着论文的真实引用，发现当前文献库中的研究脉络。</p>
        </div>
        <button
          className="button secondary"
          disabled={!graph || stale}
          onClick={() => save("研析-局域引用图谱.json", { ...graph, review })}
        >
          <ArrowDownToLine size={16} />
          导出图谱与来源
        </button>
      </div>
      <section className="graph-controls">
        <div className="action-row">
          <strong>选择构图文献</strong>
          <span>最多 30 个 PDF；同一作品的版本合并计数。</span>
        </div>
        <details>
          <summary>
            当前选择 {pendingScope.length} / {ready.length} 个 PDF
          </summary>
          <div className="graph-document-picker">
            <button onClick={() => setSelected(ready.map((doc) => doc.id))}>
              全选
            </button>
            <button onClick={() => setSelected([])}>清空选择</button>
            {ready.map((doc) => (
              <label key={doc.id}>
                <input
                  type="checkbox"
                  checked={pendingScope.includes(doc.id)}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...pendingScope, doc.id]
                        : pendingScope.filter((id) => id !== doc.id),
                    )
                  }
                />
                {doc.title}
                {doc.ocr_pages ? " · OCR" : ""}
                {doc.version ? ` · v${doc.version}` : ""}
              </label>
            ))}
            {!ready.length && <p>请先导入论文并完成解析与版本确认。</p>}
          </div>
        </details>
        <div className="action-row">
          <button
            className="button primary"
            disabled={busy || reviewBusy || !pendingScope.length}
            onClick={() => void build()}
          >
            <RefreshCw size={15} />
            {busy
              ? "正在核对参考文献…"
              : graph
                ? "重新构建图谱"
                : "构建真实引用图谱"}
          </button>
          <label>
            <input
              type="checkbox"
              checked={connectedOnly}
              onChange={(e) => setConnectedOnly(e.target.checked)}
            />
            仅显示有连边的论文
          </label>
        </div>
        <p>
          箭头：引用论文 <ArrowRight size={13} /> 被引论文。完整标题或标识符在
          References 中匹配才连边，主题相近不会自动连边。
        </p>
        {error && (
          <p role="alert" className="graph-error">
            {error}
          </p>
        )}
        {stale && (
          <p role="status" className="graph-error">
            文献范围已变化，请重新构建后再生成综述或导出。
          </p>
        )}
      </section>
      {!graph ? (
        <div className="graph-empty">
          <BookOpen size={28} />
          <h2>从已入库文献开始</h2>
          <p>构图后点击节点，可核对引用片段、物理页码和论文原页。</p>
        </div>
      ) : (
        <>
          <div className="graph-statistics">
            <span>
              <b>{graph.stats.works}</b> 篇作品 / {graph.stats.documents} 个 PDF
            </span>
            <span>
              <b>{graph.stats.edges}</b> 条引用边
            </span>
            <span>
              <b>{graph.stats.components}</b> 个连通分量
            </span>
            <span>
              <b>{graph.stats.isolated}</b> 篇未连边作品
            </span>
          </div>
          {!!graph.warnings.length && (
            <details className="graph-warnings">
              <summary>解析范围说明（{graph.warnings.length}）</summary>
              {graph.warnings.map((text) => (
                <p key={text}>{text}</p>
              ))}
            </details>
          )}
          <div className="real-graph-layout">
            <section
              className={`graph-canvas real-graph-canvas ${graph.nodes.length >= 12 ? "dense-graph" : ""}`}
            >
              <div className="graph-top">
                <span>当前文献库的有向引用网络</span>
                <small>
                  {graph.nodes.length >= 12
                    ? "点击节点查看题名与相邻引用"
                    : "节点大小随局部 PageRank 变化"}
                </small>
              </div>
              <svg
                viewBox="0 0 800 590"
                role="group"
                aria-label="真实文献引用图谱"
              >
                <defs>
                  <pattern
                    id="real-graph-dots"
                    width="22"
                    height="22"
                    patternUnits="userSpaceOnUse"
                  >
                    <circle cx="1" cy="1" r="1" fill="#dfe7df" />
                  </pattern>
                  <marker
                    id="citation-arrow"
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="7"
                    markerHeight="7"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 0 L 10 5 L 0 10 z" fill="#77937e" />
                  </marker>
                </defs>
                <rect width="800" height="590" fill="url(#real-graph-dots)" />
                {edges
                  .filter(
                    (edge) =>
                      !adjacentOnly ||
                      edge.source === focus ||
                      edge.target === focus,
                  )
                  .map((edge) => {
                    const a = graph.nodes.find((n) => n.id === edge.source)!,
                      b = graph.nodes.find((n) => n.id === edge.target)!;
                    const dx = b.x - a.x,
                      dy = b.y - a.y,
                      length = Math.max(1, Math.hypot(dx, dy));
                    const radius = (n: Node) => 20 + 35 * Math.sqrt(n.pagerank);
                    const focused =
                      edge.source === focus || edge.target === focus;
                    return (
                      <line
                        key={edge.source + edge.target}
                        x1={a.x + (dx / length) * (radius(a) + 3)}
                        y1={a.y + (dy / length) * (radius(a) + 3)}
                        x2={b.x - (dx / length) * (radius(b) + 5)}
                        y2={b.y - (dy / length) * (radius(b) + 5)}
                        stroke={
                          focused
                            ? edge.target === focus
                              ? "#52785d"
                              : "#b17b3e"
                            : "#b7c7bb"
                        }
                        strokeWidth={focused ? 2.5 : 1.6}
                        opacity={graph.nodes.length >= 12 && !focused ? 0.2 : 1}
                        markerEnd="url(#citation-arrow)"
                      >
                        <title>
                          {label(a.title)} 引用 {label(b.title)}
                        </title>
                      </line>
                    );
                  })}
                {graphNodes.map((item) => {
                  const radius = 20 + 35 * Math.sqrt(item.pagerank);
                  const lines = badgeLines(item.title);
                  return (
                    <g
                      key={item.id}
                      role="button"
                      aria-label={`查看 ${item.title}`}
                      tabIndex={0}
                      className={`graph-node ${focus === item.id ? "focused" : ""}`}
                      onClick={() => setFocus(item.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setFocus(item.id);
                        }
                      }}
                    >
                      <title>
                        {item.title} · 局部入度 {item.in_degree} · PageRank{" "}
                        {item.pagerank.toFixed(4)}
                      </title>
                      <circle
                        className="node-halo"
                        cx={item.x}
                        cy={item.y}
                        r={radius + 9}
                      />
                      <circle
                        cx={item.x}
                        cy={item.y}
                        r={radius}
                        fill={item.in_degree ? "#52785d" : "#b5c7b8"}
                        stroke="white"
                        strokeWidth="3"
                      />
                      {lines.map((line, index) => (
                        <text
                          key={index}
                          x={item.x}
                          y={
                            item.y + (lines.length === 1 ? 4 : -3 + index * 14)
                          }
                          textAnchor="middle"
                          className={`paper-badge ${item.in_degree ? "light-label" : ""}`}
                          textLength={Math.min(
                            radius * 2 - 10,
                            line.length * 6.4,
                          )}
                          lengthAdjust="spacingAndGlyphs"
                        >
                          {line}
                        </text>
                      ))}
                      <text
                        x={graph.nodes.length >= 12 ? 400 : item.x}
                        y={
                          graph.nodes.length >= 12 ? 280 : item.y + radius + 23
                        }
                        textAnchor="middle"
                        className="graph-label"
                      >
                        {label(item.title)}
                      </text>
                      <text
                        x={graph.nodes.length >= 12 ? 400 : item.x}
                        y={
                          graph.nodes.length >= 12 ? 305 : item.y + radius + 40
                        }
                        textAnchor="middle"
                        className="graph-sub"
                      >
                        入度 {item.in_degree} · 出度 {item.out_degree}
                      </text>
                    </g>
                  );
                })}
              </svg>
              {!edges.length && (
                <p className="graph-empty-note">
                  当前显示范围未发现可核对的引用边。可补充同一方向的被引论文，或取消“仅显示有连边”。
                </p>
              )}
              <div className="graph-legend">
                <label className="graph-neighbor-toggle">
                  <input
                    type="checkbox"
                    checked={adjacentOnly}
                    onChange={(event) => setAdjacentOnly(event.target.checked)}
                  />
                  只看选中论文的引用
                </label>
                <span className="graph-incoming-key">引用了它</span>
                <span className="graph-outgoing-key">它引用了</span>
              </div>
              {node && (
                <p className="graph-focus-caption">
                  <strong>{label(node.title)}</strong> 引用了 {node.out_degree}{" "}
                  篇；当前所选文献中有 {node.in_degree}{" "}
                  篇引用它。点击节点可切换，箭头始终指向被引论文。
                </p>
              )}
            </section>
            <aside key={focus} className="graph-details real-graph-details">
              {node && (
                <>
                  <span className="eyebrow">EVIDENCE & CONTEXT</span>
                  <h2>{node.title}</h2>
                  <p>{node.authors || "作者信息待核对"}</p>
                  <span className="tag sage">{node.role}</span>
                  <div className="graph-node-metrics">
                    <span>
                      局部入度 <b>{node.in_degree}</b>
                    </span>
                    <span>
                      局部出度 <b>{node.out_degree}</b>
                    </span>
                    <span>
                      PageRank <b>{node.pagerank.toFixed(4)}</b>
                    </span>
                  </div>
                  {node.documents.length > 1 && (
                    <p>
                      合并了 {node.documents.length}{" "}
                      个相同作品版本，优先使用较新、较少 OCR 的 PDF 核对引用。
                    </p>
                  )}
                  <button
                    className="button secondary"
                    onClick={() => open(node.document_id)}
                  >
                    <BookOpen size={15} />
                    打开文献阅读
                  </button>
                  <h4>相邻引用与原文</h4>
                  {!connectedEdges.length && (
                    <p>当前范围没有与该作品相连的已匹配引用。</p>
                  )}
                  {connectedEdges.map((edge) => (
                    <div
                      className="graph-edge-detail"
                      key={edge.source + edge.target}
                    >
                      <b>
                        {label(
                          graph.nodes.find((n) => n.id === edge.source)!.title,
                        )}{" "}
                        <ArrowRight size={12} />{" "}
                        {label(
                          graph.nodes.find((n) => n.id === edge.target)!.title,
                        )}
                      </b>
                      <small>
                        匹配依据：
                        {
                          (
                            {
                              arxiv_id: "arXiv 编号",
                              doi: "DOI",
                              full_title: "完整标题",
                            } as Record<string, string>
                          )[edge.match_method]
                        }
                      </small>
                      {edge.evidence.map((source) => (
                        <SourceDisclosure
                          key={`${source.document_id}/${source.page}/${source.start}`}
                          source={source}
                          name="引用出处"
                        />
                      ))}
                    </div>
                  ))}
                </>
              )}
            </aside>
          </div>
          <div className="graph-analysis-grid">
            <section className="graph-analysis-card">
              <div className="action-row">
                <h2>局部影响力排序</h2>
                <select
                  aria-label="引用图谱排序指标"
                  value={ranking}
                  onChange={(e) => setRanking(e.target.value)}
                >
                  <option value="pagerank">PageRank</option>
                  <option value="degree">局部入度</option>
                </select>
              </div>
              <p>{graph.interpretation}</p>
              <div className="graph-ranking">
                {ranked.map((item, i) => (
                  <button
                    key={item.id}
                    className={focus === item.id ? "selected" : ""}
                    onClick={() => setFocus(item.id)}
                  >
                    <span>
                      {i + 1}. {label(item.title)}
                    </span>
                    <small>
                      入度 {item.in_degree} · PR {item.pagerank.toFixed(4)}
                    </small>
                  </button>
                ))}
              </div>
            </section>
            <section className="graph-analysis-card">
              <h2>有来源的研究综述</h2>
              <p>
                本地 Qwen 根据本图中最多 6 篇论文的摘要生成，优先覆盖局部
                PageRank 较高的作品。引用与数值校验不能代替完整语义核对。
              </p>
              <button
                className="button primary"
                disabled={stale || busy || reviewBusy || !graph.nodes.length}
                onClick={async () => {
                  if (busy || reviewBusy) return;
                  const scope = current.current;
                  const fingerprint = graph.fingerprint;
                  const version = ++generation.current;
                  setReviewBusy(true);
                  setError("");
                  try {
                    const result = await api<Review>("/api/graph/review", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({
                        document_ids: graph.document_ids,
                        fingerprint,
                      }),
                    });
                    if (
                      generation.current === version &&
                      current.current === scope
                    )
                      setReview(result);
                  } catch (reason) {
                    if (generation.current === version)
                      setError(
                        reason instanceof Error
                          ? reason.message
                          : "综述生成失败",
                      );
                  } finally {
                    if (generation.current === version) setReviewBusy(false);
                  }
                }}
              >
                {reviewBusy ? "本地模型正在阅读摘要…" : "生成简短综述"}
              </button>
              {review && !stale && (
                <div className="graph-review" role="status">
                  <p>{review.answer}</p>
                  <small>
                    本轮读取 {review.covered_documents.length} 篇摘要
                    {review.insufficient_evidence ? " · 证据或校验不足" : ""}
                  </small>
                  {review.citations.map((item) => (
                    <details className="graph-source" key={item.id}>
                      <summary>
                        [{item.id}] {item.title} · 第 {item.page} 页
                      </summary>
                      <blockquote>{item.quote}</blockquote>
                      <a href={item.url} target="_blank" rel="noreferrer">
                        打开引用原页
                      </a>
                    </details>
                  ))}
                </div>
              )}
            </section>
          </div>
          <section className="graph-analysis-card graph-future">
            <h2>后续研究线索</h2>
            <p>
              提取原文 Future Work
              等表述，按已知发表年份排列；是否仍未解决，需结合更新研究核验。
            </p>
            <FutureCheckPanel
              key={graph.fingerprint}
              documentIds={graph.document_ids}
              fingerprint={graph.fingerprint}
              disabled={
                stale || !graph.nodes.some((item) => item.future_work.length)
              }
            />
            {graph.nodes
              .filter((item) => item.future_work.length)
              .sort((a, b) => (b.year || 0) - (a.year || 0))
              .map((item) => (
                <div key={item.id}>
                  <h3>
                    {item.year || "年份待核对"} · {item.title}
                  </h3>
                  {item.future_work.map((source) => (
                    <SourceDisclosure
                      key={`${source.page}/${source.start}`}
                      source={source}
                      name="后续研究原文"
                    />
                  ))}
                </div>
              ))}
            {!graph.nodes.some((item) => item.future_work.length) && (
              <p>
                当前范围未找到明确的 Future Work
                表述，不生成未被原文支持的方向。
              </p>
            )}
          </section>
        </>
      )}
    </>
  );
}
