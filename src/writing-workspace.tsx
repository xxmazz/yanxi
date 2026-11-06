import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowDownToLine,
  BookOpen,
  FileText,
  Loader2,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { api } from "./api";
import type { ServerDocument } from "./api";

type Source = {
  document_id: string;
  page: number;
  start: number;
  end: number;
  quote: string;
  page_sha256: string;
  url: string;
  preview_url: string;
  method: string;
};
type Reference = {
  number: number;
  label: string;
  document_id: string;
  title: string;
  authors: string;
  year: number | null;
  metadata_origin: string;
  source_url: string | null;
  merged_versions: number;
  evidence: Source;
};
type Draft = {
  version: string;
  generated_at: string;
  idea: string;
  fingerprint: string;
  document_ids: string[];
  markdown: string;
  references: Reference[];
  skipped: { title: string; reason: string }[];
  checks: string[];
  diagram: {
    kind: string;
    nodes: { id: string; label: string }[];
    edges: { source: string; target: string }[];
    mermaid: string;
  };
};
type Saved = {
  idea: string;
  selected: string[];
  draft: string;
  result: Draft | null;
};
const STORAGE = "yanxi-grounded-writing-v1";
function restore(): Saved | null {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE) || "null");
    if (
      !value ||
      typeof value.idea !== "string" ||
      typeof value.draft !== "string" ||
      !Array.isArray(value.selected) ||
      !value.selected.every((id: unknown) => typeof id === "string")
    )
      return null;
    const r = value.result;
    if (
      r &&
      (r.version !== "writing-v1" ||
        typeof r.markdown !== "string" ||
        typeof r.fingerprint !== "string" ||
        !Array.isArray(r.document_ids) ||
        !Array.isArray(r.skipped) ||
        !Array.isArray(r.checks) ||
        !Array.isArray(r.references) ||
        !r.references.every(
          (ref: Reference) =>
            typeof ref.title === "string" &&
            typeof ref.authors === "string" &&
            typeof ref.evidence?.quote === "string",
        ) ||
        !Array.isArray(r.diagram?.nodes) ||
        !r.diagram.nodes.every(
          (n: { label: string }) => typeof n.label === "string",
        ) ||
        typeof r.diagram.mermaid !== "string")
    )
      return null;
    return value;
  } catch {
    return null;
  }
}
function saveFile(
  name: string,
  content: string,
  type = "text/plain;charset=utf-8",
) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function SourceCard({ item }: { item: Reference }) {
  const [preview, setPreview] = useState(false);
  return (
    <details className="writing-source">
      <summary>
        [{item.number}] {item.title} · 第 {item.evidence.page} 页
      </summary>
      <p>
        {item.authors} · {item.year || "年份待核对"}
      </p>
      <small>
        {item.metadata_origin}
        {item.merged_versions > 1
          ? ` · 合并 ${item.merged_versions} 个版本`
          : ""}
      </small>
      <blockquote>{item.evidence.quote}</blockquote>
      <div className="action-row">
        <a href={item.evidence.url} target="_blank" rel="noreferrer">
          打开引用 PDF 原页
        </a>
        {item.source_url && (
          <a href={item.source_url} target="_blank" rel="noreferrer">
            官方来源
          </a>
        )}
        <button onClick={() => setPreview(!preview)}>
          {preview ? "收起原页" : "预览原页"}
        </button>
      </div>
      {preview && (
        <img
          src={item.evidence.preview_url}
          loading="lazy"
          alt={`引用 ${item.number} 的 PDF 第 ${item.evidence.page} 页`}
        />
      )}
      <small className="writing-source-hash">
        原文 SHA-256：{item.evidence.page_sha256}
      </small>
    </details>
  );
}

export function WritingWorkspace({
  documents,
}: {
  documents: ServerDocument[];
}) {
  const [saved] = useState(restore);
  const [idea, setIdea] = useState(
    saved?.idea || "面向科研文献的可验证问答与证据溯源",
  );
  const [selected, setSelected] = useState<string[]>(saved?.selected || []);
  const [draft, setDraft] = useState(saved?.draft || "");
  const [result, setResult] = useState<Draft | null>(saved?.result || null);
  const [previous, setPrevious] = useState<{
    result: Draft | null;
    draft: string;
  } | null>(null);
  const [mode, setMode] = useState("preview");
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const [checkNote, setCheckNote] = useState("");
  const [storageError, setStorageError] = useState(false);
  const generation = useRef(0);
  const svg = useRef<SVGSVGElement>(null);
  const ready = documents.filter(
    (doc) => doc.status === "ready" && doc.is_active && !doc.trashed_at,
  );
  const readyIds = new Set(ready.map((doc) => doc.id));
  const unavailable = selected.filter((id) => !readyIds.has(id));
  const signature = JSON.stringify(
    ready
      .map((doc) => [
        doc.id,
        doc.title,
        doc.authors,
        doc.version,
        doc.ocr_pages,
      ])
      .sort(),
  );
  const scope = JSON.stringify([idea.trim(), [...selected].sort(), signature]);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const edited = !!result && draft !== result.markdown;
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  useEffect(() => {
    try {
      localStorage.setItem(
        STORAGE,
        JSON.stringify({ idea, selected, draft, result }),
      );
      setStorageError(false);
    } catch {
      setStorageError(true);
    }
  }, [idea, selected, draft, result]);
  const generate = async () => {
    if (busy || checking || !selected.length || unavailable.length) return;
    const version = ++generation.current,
      input = currentScope.current;
    setBusy(true);
    setError("");
    setCheckNote("");
    try {
      const value = await api<Draft>("/api/writing/outline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idea: idea.trim(), document_ids: selected }),
      });
      if (generation.current !== version) return;
      if (currentScope.current !== input) {
        setError("生成期间文献范围发生变化，已保留原草稿，请重新选择后生成。");
        return;
      }
      setPrevious({ result, draft });
      setResult(value);
      setDraft(value.markdown);
      setMode("preview");
      setCheckNote("生成前后已核对文献原文及书目指纹。");
    } catch (reason) {
      if (generation.current === version)
        setError(
          reason instanceof Error ? reason.message : "生成失败，原稿仍保留",
        );
    } finally {
      if (generation.current === version) setBusy(false);
    }
  };
  const check = async () => {
    if (!result || busy || checking) return;
    const version = generation.current;
    setChecking(true);
    setError("");
    setCheckNote("");
    try {
      await api("/api/writing/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          document_ids: result.document_ids,
          fingerprint: result.fingerprint,
        }),
      });
      if (version === generation.current)
        setCheckNote(
          "来源原文及书目信息与生成时一致。" +
            (edited ? "手动编辑的文字需另行核对。" : ""),
        );
    } catch (reason) {
      if (version === generation.current)
        setError(reason instanceof Error ? reason.message : "来源复核失败");
    } finally {
      if (version === generation.current) setChecking(false);
    }
  };
  const provenance = result
    ? `\n\n---\n生成时间：${result.generated_at}\n\n来源快照：${result.fingerprint}\n\n稿件状态：${edited ? "用户已编辑；生成时检查不覆盖新增或修改的文字" : "模型研究计划草稿；来源和数值检查不代替语义复核"}\n`
    : "";
  return (
    <>
      <div className="writing-layout grounded-writing-layout">
        <section className="writing-input panel">
          <h2>研究计划</h2>
          <p>写下研究设想，选择参考文献。</p>
          <label htmlFor="grounded-idea">研究设想</label>
          <textarea
            id="grounded-idea"
            maxLength={1200}
            value={idea}
            disabled={busy}
            onChange={(e) => setIdea(e.target.value)}
          />
          <button
            className="button primary"
            disabled={
              busy ||
              checking ||
              idea.trim().length < 3 ||
              !selected.length ||
              !!unavailable.length
            }
            onClick={() => void generate()}
          >
            {busy ? (
              <Loader2 className="spin" size={16} />
            ) : (
              <Sparkles size={16} />
            )}
            {busy ? "正在生成…" : "生成研究计划"}
          </button>
          <fieldset className="writing-picker" disabled={busy}>
            <legend>参考文献 · 最多六篇作品</legend>
            <div className="action-row">
              <button onClick={() => setSelected(ready.map((doc) => doc.id))}>
                全选
              </button>
              <button onClick={() => setSelected([])}>清空</button>
            </div>
            <div
              className="writing-picker-list"
              role="region"
              aria-label="可滚动参考文献列表"
              tabIndex={0}
            >
              {ready.map((doc) => (
                <label key={doc.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(doc.id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, doc.id]
                          : selected.filter((id) => id !== doc.id),
                      )
                    }
                  />
                  <span>
                    {doc.title}
                    <small>
                      {doc.pages} 页{doc.ocr_pages ? " · OCR" : ""}
                      {doc.version ? ` · v${doc.version}` : ""}
                    </small>
                  </span>
                </label>
              ))}
              {!ready.length && <p>请先导入论文，等待解析与版本确认完成。</p>}
            </div>
          </fieldset>
          {!!unavailable.length && (
            <p role="alert" className="writing-error">
              有 {unavailable.length} 篇已选文献当前不可用。
              <button
                onClick={() =>
                  setSelected(selected.filter((id) => readyIds.has(id)))
                }
              >
                移除不可用选择
              </button>
            </p>
          )}
          <div className="info-note">
            <ShieldCheck size={18} />
            <p>依据所选摘要生成草稿；方法与实验部分为研究计划。</p>
          </div>

          {error && (
            <p role="alert" className="writing-error">
              {error}
            </p>
          )}
          {checkNote && (
            <p role="status" className="writing-check-note">
              {checkNote}
            </p>
          )}
          {storageError && (
            <p role="alert" className="writing-error">
              浏览器存储不足，草稿暂未自动保存，请导出备份。
            </p>
          )}
        </section>
        <section className="writing-output panel">
          <div>
            <h3>
              <FileText size={17} />
              研究计划草稿
            </h3>
            <button
              className="button secondary small"
              disabled={!draft}
              onClick={() =>
                saveFile(
                  "研析-研究计划.md",
                  draft + provenance,
                  "text/markdown;charset=utf-8",
                )
              }
            >
              <ArrowDownToLine size={14} />
              导出 Markdown
            </button>
          </div>
          {!!draft && (
            <div
              className="draft-mode"
              role="group"
              aria-label="研究计划显示方式"
            >
              <button
                className={`button small ${mode === "edit" ? "primary" : "secondary"}`}
                aria-pressed={mode === "edit"}
                onClick={() => setMode("edit")}
              >
                编辑
              </button>
              <button
                className={`button small ${mode === "preview" ? "primary" : "secondary"}`}
                aria-pressed={mode === "preview"}
                onClick={() => setMode("preview")}
              >
                预览
              </button>
              {previous && (
                <button
                  className="button small secondary"
                  disabled={busy || checking}
                  onClick={() => {
                    setResult(previous.result);
                    setDraft(previous.draft);
                    setPrevious(null);
                    setCheckNote("");
                  }}
                >
                  恢复上一稿
                </button>
              )}
            </div>
          )}
          {edited && (
            <p className="writing-edit-note">
              已手动编辑。生成时的来源与数值检查不覆盖新增文字，导出时会标注。
            </p>
          )}
          {draft ? (
            mode === "edit" ? (
              <textarea
                aria-label="编辑研究计划"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
            ) : (
              <article className="markdown-preview" aria-label="研究计划预览">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  skipHtml
                  components={{ img: () => null }}
                >
                  {draft}
                </ReactMarkdown>
              </article>
            )
          ) : (
            <div className="empty-state">
              <BookOpen size={34} />
              <h3>还没有草稿</h3>
              <p>选好文献后生成研究计划，可编辑并导出。</p>
            </div>
          )}
        </section>
      </div>
      {result && (
        <>
          <section className="panel writing-evidence">
            <div className="action-row">
              <h2>引用与来源核对</h2>
              <button
                className="button secondary small"
                disabled={busy || checking}
                onClick={() => void check()}
              >
                {checking ? "正在核对…" : "重新核对来源"}
              </button>
              <button
                className="button secondary small"
                onClick={() =>
                  saveFile(
                    "研析-写作来源.json",
                    JSON.stringify(
                      {
                        ...result,
                        edited_markdown: draft,
                        manually_edited: edited,
                      },
                      null,
                      2,
                    ),
                    "application/json",
                  )
                }
              >
                导出来源记录
              </button>
            </div>
            <details className="usage-details">
              <summary>生成依据 · {result.references.length} 篇摘要</summary>
              <p>已检查：{result.checks.join("、")}。内容仍需对照原文复核。</p>
            </details>
            {result.references.map((item) => (
              <SourceCard key={item.label + result.fingerprint} item={item} />
            ))}
            {result.skipped.map((item) => (
              <p key={item.title}>
                未纳入：{item.title}（{item.reason}）
              </p>
            ))}
          </section>
          <section className="panel writing-diagram">
            <div className="action-row">
              <h2>拟议方法架构图</h2>
              <button
                className="button secondary small"
                onClick={() =>
                  saveFile("研析-方法架构.mmd", result.diagram.mermaid)
                }
              >
                导出 Mermaid
              </button>
              <button
                className="button secondary small"
                onClick={() => {
                  if (svg.current)
                    saveFile(
                      "研析-方法架构.svg",
                      svg.current.outerHTML,
                      "image/svg+xml;charset=utf-8",
                    );
                }}
              >
                导出 SVG
              </button>
            </div>
            <p>
              依据本稿生成时的方法步骤绘制；编辑正文后，请核对图中步骤是否仍与草稿一致。
            </p>
            <div className="writing-diagram-grid">
              <div className="writing-flow-preview">
                <svg
                  ref={svg}
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox={`0 0 600 ${result.diagram.nodes.length * 110 + 20}`}
                  role="img"
                  aria-label="拟议研究方法架构图"
                >
                  <defs>
                    <marker
                      id="writing-arrow"
                      viewBox="0 0 10 10"
                      refX="8"
                      refY="5"
                      markerWidth="6"
                      markerHeight="6"
                      orient="auto"
                    >
                      <path d="M0 0 L10 5 L0 10Z" fill="#75906d" />
                    </marker>
                  </defs>
                  {result.diagram.nodes.map((node, i) => (
                    <g key={node.id}>
                      {i > 0 && (
                        <line
                          x1="300"
                          y1={i * 110 - 12}
                          x2="300"
                          y2={i * 110 + 14}
                          stroke="#75906d"
                          strokeWidth="2"
                          markerEnd="url(#writing-arrow)"
                        />
                      )}
                      <rect
                        x="40"
                        y={i * 110 + 20}
                        width="520"
                        height="78"
                        rx="12"
                        fill={i === 0 ? "#e5efde" : "#f7faf3"}
                        stroke="#9bb08b"
                      />
                      <text
                        x="300"
                        y={i * 110 + 49}
                        textAnchor="middle"
                        fontFamily="sans-serif"
                        fontSize="13"
                        fill="#426047"
                      >
                        {Array.from(node.label)
                          .join("")
                          .match(/.{1,30}/gu)
                          ?.map((line, n) => (
                            <tspan key={n} x="300" dy={n ? 18 : 0}>
                              {line}
                            </tspan>
                          ))}
                      </text>
                    </g>
                  ))}
                </svg>
              </div>
              <div>
                <label htmlFor="writing-mermaid">Mermaid 架构图脚本</label>
                <textarea
                  id="writing-mermaid"
                  readOnly
                  value={result.diagram.mermaid}
                  spellCheck={false}
                />
              </div>
            </div>
          </section>
        </>
      )}
    </>
  );
}
