import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Download, Search, BookOpen } from "lucide-react";
import { api } from "./api";
import type { ServerDocument } from "./api";

type Candidate = {
  arxiv_id: string;
  title: string;
  abstract: string;
  authors: string[];
  published: string;
  updated: string;
  categories: string[];
  url: string;
  code_urls: string[];
  related_document?: { id: string; title: string; similarity: number };
  imported?: { document_id: string; status: string; archived: boolean } | null;
};
type Results = {
  query: string;
  search_query: string;
  total: number;
  start: number;
  page_size: number;
  items: Candidate[];
  cached: boolean;
  stale?: boolean;
  warning?: string;
  fetched_at: string;
  steps: string[];
  sort: string;
  library_count: number;
  library_ranked: boolean;
};
type ImportJob = {
  id: string;
  arxiv_id: string;
  status: string;
  document_id: string;
  error: string;
};

export function Discovery({
  documents,
  changed,
  open,
  notify,
}: {
  documents: ServerDocument[];
  changed: () => void;
  open: (id: string) => void;
  notify: (text: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("relevance");
  const [results, setResults] = useState<Results | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [jobs, setJobs] = useState<ImportJob[]>([]);
  const [submitting, setSubmitting] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    let previous = "";
    const load = async () => {
      try {
        const value = await api<ImportJob[]>("/api/discovery/jobs");
        if (!mounted.current) return;
        setJobs(value);
        const signature = JSON.stringify(value);
        if (previous && previous !== signature) changed();
        previous = signature;
      } catch {
        /* 搜索表单单独报告连接问题，避免轮询反复弹提示。 */
      }
    };
    void load();
    const timer = setInterval(load, 2500);
    return () => {
      mounted.current = false;
      clearInterval(timer);
    };
  }, []);
  const search = async (text = query, start = 0, selectedSort = sort) => {
    if (busy || text.trim().length < 2) return;
    setQuery(text);
    setBusy(true);
    setError("");
    try {
      const value = await api<Results>(
        `/api/discovery/search?${new URLSearchParams({ q: text.trim(), start: String(start), sort: selectedSort })}`,
      );
      if (mounted.current) setResults(value);
    } catch (reason) {
      if (mounted.current)
        setError(
          reason instanceof Error ? reason.message : "检索失败，请稍后重试",
        );
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  const importPaper = async (item: Candidate) => {
    if (submitting) return;
    setSubmitting(item.arxiv_id);
    try {
      const job = await api<ImportJob>("/api/discovery/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ arxiv_id: item.arxiv_id }),
      });
      if (mounted.current)
        setJobs((values) => [
          job,
          ...values.filter((value) => value.id !== job.id),
        ]);
      notify("已加入后台下载队列，完成后自动解析并进入文献库");
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : "导入失败");
    } finally {
      if (mounted.current) setSubmitting(null);
    }
  };
  const stateFor = (item: Candidate) => {
    const job = jobs.find((value) => value.arxiv_id === item.arxiv_id);
    const sourceId = job?.document_id || item.imported?.document_id;
    const document = documents.find((doc) => doc.id === sourceId);
    return { job, document };
  };
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">FOLLOW YOUR CURIOSITY</div>
          <h1>探索发现</h1>
          <p>检索真实论文，把下一条研究线索带回文献库。</p>
        </div>
        <span className="subtle-label">arXiv 官方来源 · 本地模型辅助</span>
      </div>
      <section className="discovery-search panel">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void search();
          }}
        >
          <label htmlFor="discovery-query">
            研究主题、论文名称或 arXiv 编号
          </label>
          <div className="discovery-query-row">
            <input
              id="discovery-query"
              maxLength={240}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="例如：检索增强生成，或 Attention Is All You Need"
            />
            <select
              aria-label="论文检索排序"
              value={sort}
              onChange={(event) => setSort(event.target.value)}
            >
              <option value="relevance">主题相关</option>
              <option value="newest">最新发布</option>
              <option value="library">本页与文献库最相关</option>
            </select>
            <button
              className="button primary"
              disabled={busy || query.trim().length < 2}
              type="submit"
            >
              <Search size={16} />
              {busy ? "正在检索…" : "检索论文"}
            </button>
          </div>
        </form>
        <div className="discovery-suggestions">
          试一试：
          {["Attention Is All You Need", "检索增强生成", "参数高效微调"].map(
            (text) => (
              <button
                key={text}
                disabled={busy}
                onClick={() => void search(text)}
              >
                {text}
              </button>
            ),
          )}
        </div>
        <p>
          中文主题通过本地术语词表与 Qwen 转成英文检索词；搜索与下载连接
          arXiv，论文问答仍在本地完成。
        </p>
        {error && (
          <p className="chart-error" role="alert">
            {error}
          </p>
        )}
        {busy && (
          <p role="status">正在查询官方论文元数据，网络较慢时可能需要几十秒…</p>
        )}
      </section>
      {!!jobs.length && (
        <details
          className="archive-panel"
          open={jobs.some((job) =>
            ["queued", "downloading"].includes(job.status),
          )}
        >
          <summary>论文导入记录</summary>
          {jobs.slice(0, 6).map((job) => {
            const document = documents.find(
              (doc) => doc.id === job.document_id,
            );
            return (
              <div className="archive-item" key={job.id}>
                <span>
                  arXiv {job.arxiv_id} ·{" "}
                  {job.status === "error"
                    ? job.error
                    : job.status === "downloading"
                      ? "正在核对来源并下载 PDF"
                      : job.status === "queued"
                        ? "等待下载"
                        : document?.status === "ready"
                          ? "已入库，可问答"
                          : document?.status === "review"
                            ? "相似版本待确认，请到文献库处理"
                            : document?.status === "error"
                              ? `解析失败：${document.error}`
                              : document
                                ? `已下载，解析 ${document.processed_pages}/${document.pages} 页`
                                : "下载完成，查看文献库或归档记录"}
                </span>
                {document && (
                  <button onClick={() => open(document.id)}>查看文献</button>
                )}
              </div>
            );
          })}
        </details>
      )}
      {results && (
        <>
          <div className="discovery-result-summary" role="status">
            <b>
              找到 {results.total.toLocaleString()} 篇 · 当前{" "}
              {results.items.length ? results.start + 1 : 0}—
              {results.start + results.items.length} 篇
            </b>
            <p>
              检索词：{results.search_query} ·{" "}
              {results.cached ? "缓存结果" : "官方接口结果"} · 获取于{" "}
              {new Date(results.fetched_at).toLocaleString("zh-CN")}
            </p>
            <p>{results.steps.join(" → ")}</p>
            {results.warning && (
              <p className="chart-error">{results.warning}</p>
            )}
            {results.sort === "library" && results.library_ranked && (
              <p>
                仅对本页最多 8 篇按本地 E5 相似度排序，依据库中最近 40
                篇已解析论文；相似度表示主题接近程度。
              </p>
            )}
          </div>
          {results.sort === "library" && !results.library_ranked && (
            <p className="notice">
              暂未进行文献库相似度排序：请先导入文献并确认本地向量模型已就绪。当前显示官方相关性顺序。
            </p>
          )}
          {!results.items.length && (
            <div className="empty-state">
              <h3>没有找到匹配的论文</h3>
              <p>减少关键词，或试试英文标题、arXiv 编号。</p>
            </div>
          )}
          <div className="discovery-list">
            {results.items.map((item, index) => {
              const { job, document } = stateFor(item);
              const downloading =
                job && ["queued", "downloading"].includes(job.status);
              return (
                <article key={item.arxiv_id}>
                  <div className="discovery-number sage">
                    {String(results.start + index + 1).padStart(2, "0")}
                  </div>
                  <div>
                    <span className="discovery-meta">
                      arXiv:{item.arxiv_id} · {item.published.slice(0, 10)} ·{" "}
                      {item.categories.slice(0, 3).join(" / ")}
                    </span>
                    <h3>
                      <a href={item.url} target="_blank" rel="noreferrer">
                        {item.title}
                      </a>
                    </h3>
                    <p className="discovery-authors">
                      {item.authors.join(" · ")}
                    </p>
                    <details className="discovery-abstract">
                      <summary>查看论文摘要</summary>
                      <p>{item.abstract}</p>
                    </details>
                    {item.related_document && (
                      <p className="recommend-reason">
                        与你的《{item.related_document.title}》主题接近 · E5
                        余弦 {item.related_document.similarity.toFixed(3)}
                      </p>
                    )}
                    {!!item.code_urls.length && (
                      <div className="discovery-code">
                        摘要／作者备注提供代码链接：
                        {item.code_urls.map((url) => (
                          <a
                            key={url}
                            href={url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {url.replace("https://github.com/", "")}{" "}
                            <ArrowUpRight size={12} />
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="discovery-actions">
                    {document ? (
                      <button
                        className="button secondary small"
                        onClick={() => open(document.id)}
                      >
                        <BookOpen size={14} />
                        {document.status === "ready"
                          ? "打开已入库文献"
                          : document.status === "review"
                            ? "查看待确认版本"
                            : "查看解析状态"}
                      </button>
                    ) : (
                      <button
                        className="button secondary small"
                        disabled={
                          !!submitting ||
                          !!downloading ||
                          !!item.imported?.archived
                        }
                        onClick={() => void importPaper(item)}
                      >
                        <Download size={14} />
                        {downloading || submitting === item.arxiv_id
                          ? "下载排队中…"
                          : item.imported?.archived
                            ? "已归档，请到文献库恢复"
                            : "下载并加入文献库"}
                      </button>
                    )}
                    <a href={item.url} target="_blank" rel="noreferrer">
                      arXiv 原始页面 <ArrowUpRight size={14} />
                    </a>
                  </div>
                </article>
              );
            })}
          </div>
          <div className="reader-pagination">
            <button
              disabled={busy || results.start === 0}
              onClick={() =>
                void search(
                  results.query,
                  Math.max(0, results.start - 8),
                  results.sort,
                )
              }
            >
              上一页
            </button>
            <span>第 {Math.floor(results.start / 8) + 1} 页</span>
            <button
              disabled={
                busy ||
                results.start + results.items.length >= results.total ||
                results.start >= 992
              }
              onClick={() =>
                void search(results.query, results.start + 8, results.sort)
              }
            >
              下一页
            </button>
          </div>
        </>
      )}
      {!results && !busy && (
        <div className="empty-state">
          <Search size={30} />
          <h3>从一个主题开始</h3>
          <p>搜索后选择论文导入，后台会自动解析正文并检查相似版本。</p>
        </div>
      )}
    </>
  );
}
