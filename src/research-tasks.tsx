import { useEffect, useState } from "react";
import { api } from "./api";

type Task = {
  id: string;
  query: string;
  status: string;
  stage: string;
  error: string;
  attempt: number;
  search_query?: string;
  selection_method?: string;
  search_steps?: string[];
  events: { stage: string; message: string; time: number }[];
  selected: {
    title: string;
    url: string;
    reason: string;
    document_id: string;
  }[];
  results: {
    document_id: string;
    title: string;
    pages: number;
    abstract: string;
    pdf_url: string;
  }[];
  summary: null | {
    answer: string;
    insufficient_evidence: boolean;
    citations: {
      id: string;
      title: string;
      page: number;
      url: string;
      quote: string;
    }[];
  };
};
const statuses: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  complete: "来源与摘要综述完成",
  needs_summary: "来源就绪，待生成综述",
  error: "需要重试",
  needs_review: "等待版本确认",
  interrupted: "任务已中断",
  cancelled: "已取消",
};

export function ResearchTasks({
  changed,
  open,
}: {
  changed: () => void;
  open: (id: string) => void;
}) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(2);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [expanded, setExpanded] = useState("");
  useEffect(() => {
    let disposed = false,
      previous = "";
    const load = async () => {
      try {
        const result = await api<Task[]>("/api/research/tasks");
        if (disposed) return;
        setTasks(result);
        const signature = JSON.stringify(result.map((t) => [t.id, t.status]));
        if (previous && signature !== previous) changed();
        previous = signature;
      } catch {
        /* 操作时显示错误，避免轮询重复弹窗。 */
      }
    };
    void load();
    const timer = setInterval(load, 2000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, []);
  const act = async (path: string, body?: object) => {
    if (pending) return;
    setPending(true);
    setError("");
    try {
      const task = await api<Task>(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      setExpanded(task.id);
      setTasks(await api<Task[]>("/api/research/tasks"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务操作失败");
    } finally {
      setPending(false);
    }
  };
  return (
    <section className="research-task-panel">
      <h2>研究任务</h2>
      <p>按主题查找最多三篇论文，入库后整理摘要。</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void act("/api/research/tasks", { query: query.trim(), limit });
        }}
      >
        <label>
          研究主题
          <input
            aria-label="研究任务主题"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={240}
            placeholder="例如 retrieval augmented generation，或 arXiv 编号"
            required
            minLength={2}
          />
        </label>
        <label>
          论文数量
          <select
            aria-label="研究任务论文数量"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
          >
            {[1, 2, 3].map((n) => (
              <option key={n} value={n}>
                {n} 篇
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={
            pending ||
            tasks.some((t) => ["queued", "running"].includes(t.status))
          }
          type="submit"
        >
          开始研究任务
        </button>
      </form>
      {error && <p role="status">{error}</p>}
      {tasks.map((task) => (
        <details
          key={task.id}
          open={expanded === task.id}
          onToggle={(e) => {
            if (e.currentTarget.open) setExpanded(task.id);
          }}
        >
          <summary>
            {task.query} ·{" "}
            {task.status === "complete" && task.summary?.insufficient_evidence
              ? statuses.needs_summary
              : statuses[task.status] || task.status}{" "}
            · 第 {task.attempt} 次运行
          </summary>
          {task.search_query && (
            <p>
              实际检索词：{task.search_query} · 候选顺序：
              {task.selection_method === "e5_topic"
                ? "本地 E5 按本次主题重排"
                : "arXiv 官方相关性"}
            </p>
          )}
          {task.selection_method === "e5_topic" && (
            <small>按主题相似度排序，不代表论文质量。</small>
          )}
          {task.search_steps && (
            <details>
              <summary>检索过程</summary>
              <ol>
                {task.search_steps.map((step, i) => (
                  <li key={i}>{step}</li>
                ))}
              </ol>
            </details>
          )}
          <ol>
            {task.events.map((event, i) => (
              <li key={i}>{event.message}</li>
            ))}
          </ol>
          {task.error && <p role="status">{task.error}</p>}
          <div className="action-row">
            {["queued", "running"].includes(task.status) && (
              <button
                disabled={pending}
                onClick={() =>
                  void act(`/api/research/tasks/${task.id}/cancel`)
                }
              >
                取消后续步骤
              </button>
            )}
            {([
              "error",
              "interrupted",
              "needs_review",
              "needs_summary",
            ].includes(task.status) ||
              (task.status === "complete" &&
                task.summary?.insufficient_evidence)) && (
              <button
                disabled={pending}
                onClick={() => void act(`/api/research/tasks/${task.id}/retry`)}
              >
                {task.status === "needs_summary" ||
                (task.status === "complete" &&
                  task.summary?.insufficient_evidence)
                  ? "重试摘要综述"
                  : "从检查点继续"}
              </button>
            )}
          </div>
          {task.selected.map((item) => (
            <p key={item.url}>
              <a href={item.url} target="_blank" rel="noreferrer">
                {item.title}
              </a>{" "}
              · {item.reason}
              {item.document_id && (
                <button onClick={() => open(item.document_id)}>查看文献</button>
              )}
            </p>
          ))}
          {task.results.map((item) => (
            <article key={item.document_id}>
              <h3>{item.title}</h3>
              <small>
                {item.pages} 页 ·{" "}
                <a href={item.pdf_url} target="_blank" rel="noreferrer">
                  原始 PDF
                </a>
              </small>
              <p>{item.abstract || "元数据中未提取到摘要，请查看原文。"}</p>
            </article>
          ))}
          {task.summary && (
            <section>
              <h3>
                {task.summary.insufficient_evidence ? "综述状态" : "摘要综述"}
              </h3>
              {!task.summary.insufficient_evidence && (
                <small>依据论文摘要整理。</small>
              )}
              <p className="task-answer">{task.summary.answer}</p>
              {task.summary.citations.map((item) => (
                <details key={item.id}>
                  <summary>
                    [{item.id}] {item.title} · 第 {item.page} 页
                  </summary>
                  <blockquote>{item.quote}</blockquote>
                  <a href={item.url} target="_blank" rel="noreferrer">
                    查看原文
                  </a>
                </details>
              ))}
            </section>
          )}
        </details>
      ))}
    </section>
  );
}
