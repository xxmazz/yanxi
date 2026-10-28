import { useEffect, useState } from "react";
import { api } from "./api";

type Task = {
  id: string;
  query: string;
  status: string;
  stage: string;
  error: string;
  attempt: number;
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
  complete: "来源汇总完成",
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
      <p>
        输入研究主题，自动检索并选择最多三篇论文，逐篇入库、等待解析、汇总来源。中文主题和摘要综述使用服务器本地模型。
      </p>
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
            {task.query} · {statuses[task.status] || task.status} · 第{" "}
            {task.attempt} 次运行
          </summary>
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
            {["error", "interrupted", "needs_review"].includes(task.status) && (
              <button
                disabled={pending}
                onClick={() => void act(`/api/research/tasks/${task.id}/retry`)}
              >
                从检查点继续
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
                {task.summary.insufficient_evidence
                  ? "综述状态"
                  : "有来源的摘要综述"}
              </h3>
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
