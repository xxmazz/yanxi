import { useEffect, useState } from "react";
import { api, type ServerDocument } from "./api";

type Source = {
  document_id: string;
  title: string;
  page: number;
  quote: string;
  url: string;
};
type Report = {
  topic: string;
  checked_at: string;
  scope: string;
  after_year: number;
  origins: Source[];
  groups: {
    title: string;
    year: number;
    indexed_chunks: number;
    supported: boolean;
    answer: string;
    citations: Source[];
    evidence: {
      label: string;
      title: string;
      text: string;
      page: number;
      url: string;
    }[];
  }[];
};
export function FulltextCheckPanel({
  documentIds,
  fingerprint,
  disabled,
}: {
  documentIds: string[];
  fingerprint: string;
  disabled: boolean;
}) {
  const [documents, setDocuments] = useState<ServerDocument[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Report | null>(null);
  useEffect(() => {
    let alive = true;
    api<ServerDocument[]>("/api/documents")
      .then((items) => {
        if (alive)
          setDocuments(items.filter((item) => item.status === "ready"));
      })
      .catch((reason) => {
        if (alive) setError(reason.message);
      });
    return () => {
      alive = false;
    };
  }, []);
  return (
    <section className="future-check fulltext-check">
      <h3>对照已入库较新论文正文</h3>
      <p>
        选择最多 4
        篇已解析文献，检索正文并查看相关工作的原页证据。新下载的文献请先刷新列表。
      </p>
      <button
        type="button"
        disabled={busy || disabled}
        onClick={async () => {
          try {
            const items = await api<ServerDocument[]>("/api/documents");
            const ready = items.filter((item) => item.status === "ready");
            setDocuments(ready);
            setSelected((old) =>
              old.filter((id) => ready.some((item) => item.id === id)),
            );
            setError("");
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : "刷新文献失败");
          }
        }}
      >
        刷新可选文献
      </button>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || disabled) return;
          setBusy(true);
          setError("");
          setResult(null);
          try {
            setResult(
              await api<Report>("/api/graph/fulltext-check", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  document_ids: documentIds,
                  fingerprint,
                  target_document_ids: selected,
                  topic,
                }),
              }),
            );
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : "正文核对失败");
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          正文核对主题
          <input
            aria-label="正文核对主题"
            value={topic}
            minLength={3}
            maxLength={240}
            required
            disabled={busy || disabled}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="例如：retrieval augmented generation evaluation"
          />
        </label>
        <fieldset disabled={busy || disabled}>
          <legend>较新对照论文（最多4篇）</legend>
          {documents
            .filter((doc) => !documentIds.includes(doc.id))
            .map((doc) => (
              <label className="fulltext-source" key={doc.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(doc.id)}
                  disabled={!selected.includes(doc.id) && selected.length >= 4}
                  onChange={(event) =>
                    setSelected((old) =>
                      event.target.checked
                        ? [...old, doc.id]
                        : old.filter((id) => id !== doc.id),
                    )
                  }
                />
                {doc.title}
              </label>
            ))}
        </fieldset>
        <button
          disabled={
            busy || disabled || !selected.length || topic.trim().length < 3
          }
        >
          {busy ? "正在检索各篇正文并核对…" : "核对所选论文正文"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {result && !disabled && (
        <div className="fulltext-results">
          <p>{result.scope}</p>
          <p>
            核对时间：{new Date(result.checked_at).toLocaleString("zh-CN")} ·
            原方向最近年份：{result.after_year}
          </p>
          {result.groups.map((group, i) => (
            <article key={i}>
              <h4>
                {group.title} · {group.year}
              </h4>
              <p>
                检索正文段数：{group.indexed_chunks} ·{" "}
                {group.supported ? "概括通过来源检查" : "请核对原句"}
              </p>
              <p>{group.answer}</p>
              {group.citations.map((citation, j) => (
                <blockquote key={j}>
                  <a href={citation.url} target="_blank" rel="noreferrer">
                    第{citation.page}页原文
                  </a>
                  <p>{citation.quote}</p>
                </blockquote>
              ))}
              <details>
                <summary>查看本篇全部召回原句</summary>
                {group.evidence.map((item) => (
                  <blockquote key={item.label}>
                    <a href={item.url} target="_blank" rel="noreferrer">
                      [{item.label}] 第{item.page}页
                    </a>
                    <p>{item.text}</p>
                  </blockquote>
                ))}
              </details>
            </article>
          ))}
          <details>
            <summary>原方向出处</summary>
            {result.origins.map((item, i) => (
              <blockquote key={i}>
                <a href={item.url} target="_blank" rel="noreferrer">
                  {item.title} · 第{item.page}页
                </a>
                <p>{item.quote}</p>
              </blockquote>
            ))}
          </details>
          <button
            onClick={() => {
              const url = URL.createObjectURL(
                new Blob([JSON.stringify(result, null, 2)], {
                  type: "application/json",
                }),
              );
              const a = document.createElement("a");
              a.href = url;
              a.download = "研析-研究方向正文核对.json";
              a.click();
              setTimeout(() => URL.revokeObjectURL(url), 1000);
            }}
          >
            导出正文核对结果
          </button>
        </div>
      )}
    </section>
  );
}
