import { useState } from "react";
import { api } from "./api";
import { FulltextCheckPanel } from "./fulltext-check";

type Report = {
  topic: string;
  checked_at: string;
  after_year: number | null;
  search_query: string;
  scope: string;
  origins: {
    title: string;
    year: number | null;
    page: number;
    quote: string;
    url: string;
  }[];
  candidates: {
    label: string;
    title: string;
    published: string;
    text: string;
    url: string;
  }[];
  comparisons: { source: string; answer: string; supported: boolean }[];
};
export function FutureCheckPanel({
  documentIds,
  fingerprint,
  disabled,
}: {
  documentIds: string[];
  fingerprint: string;
  disabled: boolean;
}) {
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Report | null>(null);
  const [error, setError] = useState("");
  return (
    <>
      <section className="future-check">
        <h3>对照更新研究，核对拟议方向</h3>
        <p>
          输入准备研究的主题，检索较新 arXiv
          论文，并由本地模型逐篇概括摘要，和上方 Future Work 原文一起核对。
        </p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (busy || disabled) return;
            setBusy(true);
            setError("");
            setResult(null);
            try {
              setResult(
                await api<Report>("/api/graph/future-check", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    topic,
                    document_ids: documentIds,
                    fingerprint,
                  }),
                }),
              );
            } catch (e) {
              setError(e instanceof Error ? e.message : "核对失败");
            } finally {
              setBusy(false);
            }
          }}
        >
          <label htmlFor="future-topic">拟议研究主题（中文或英文）</label>
          <input
            id="future-topic"
            value={topic}
            minLength={3}
            maxLength={240}
            required
            disabled={busy || disabled}
            placeholder="例如：retrieval augmented generation evaluation"
            onChange={(e) => setTopic(e.target.value)}
          />
          <button disabled={busy || disabled || topic.trim().length < 3}>
            {busy ? "正在检索并逐篇核对…" : "核对更新研究"}
          </button>
        </form>
        {error && <p role="alert">{error}</p>}
        {result && !disabled && (
          <div className="future-check-result">
            <p>
              核对时间：
              {new Date(result.checked_at).toLocaleString("zh-CN", {
                hour12: false,
              })}{" "}
              · 检索词：
              {result.search_query} ·{" "}
              {result.after_year
                ? `限所选原文最近年份 ${result.after_year} 之后发表的候选`
                : "原文年份未知，未过滤年份"}
            </p>
            <p>{result.scope}</p>
            {!result.candidates.length && (
              <p>本轮没有满足时间条件的候选；这不代表该方向尚未被研究。</p>
            )}
            {result.candidates.map((item) => (
              <article key={item.label}>
                <h4>
                  [{item.label}]{" "}
                  <a href={item.url} target="_blank" rel="noreferrer">
                    {item.title}
                  </a>
                </h4>
                <small>
                  首次发表日期：{item.published.slice(0, 10)} · 来源：arXiv 摘要
                </small>
                <p>
                  {
                    result.comparisons.find((c) => c.source === item.label)
                      ?.answer
                  }
                </p>
                <details>
                  <summary>核对摘要原文</summary>
                  <p>{item.text}</p>
                </details>
              </article>
            ))}
            <details>
              <summary>本次对照的 Future Work 原文</summary>
              {result.origins.map((item, i) => (
                <p key={i}>
                  <a href={item.url} target="_blank" rel="noreferrer">
                    {item.title} · 第 {item.page} 页
                  </a>
                  <br />
                  {item.quote}
                </p>
              ))}
            </details>
            <button
              onClick={() => {
                const url = URL.createObjectURL(
                  new Blob([JSON.stringify(result, null, 2)], {
                    type: "application/json",
                  }),
                );
                const link = document.createElement("a");
                link.href = url;
                link.download = "研析-更新研究核对.json";
                link.click();
                setTimeout(() => URL.revokeObjectURL(url), 1000);
              }}
            >
              导出核对结果
            </button>
          </div>
        )}
      </section>
      <FulltextCheckPanel
        key={fingerprint}
        documentIds={documentIds}
        fingerprint={fingerprint}
        disabled={disabled}
      />
    </>
  );
}
