import { useEffect, useState } from "react";
import { api } from "./api";
import type { ServerDocument } from "./api";
import {
  ReferenceMatching,
  type UnresolvedReference,
} from "./reference-matching";

type Candidate = {
  arxiv_id: string;
  reference: string;
  quote: string;
  pages: number[];
  title?: string;
  match_method?: string;
  metadata_url?: string;
  match?: { reasons: string[] };
};
type FeedPaper = {
  base_id: string;
  arxiv_id: string;
  title: string;
  published: string;
  abstract: string;
  url: string;
  code_urls: string[];
  new_in_feed: boolean;
  reason: string;
};
type Subscription = {
  id: string;
  query: string;
  cadence: string;
  enabled: boolean;
  last_run: number | null;
  next_run: number;
  error: string;
  feedback: Record<string, number>;
  items: FeedPaper[];
};
type Batch = {
  id: string;
  source_id: string;
  status: string;
  items: (Candidate & { status: string; error: string; document_id: string })[];
};
type State = { subscriptions: Subscription[]; batches: Batch[] };
const labels: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  complete: "下载入库完成",
  partial: "部分失败，可重试",
  interrupted: "已中断",
  cancelled: "已取消",
  pending: "等待下载",
  downloading: "下载中",
  error: "失败",
};
const when = (seconds: number | null) =>
  seconds ? new Date(seconds * 1000).toLocaleString("zh-CN") : "尚未刷新";
export function FollowingPanel({
  documents,
  open,
  changed,
}: {
  documents: ServerDocument[];
  open: (id: string) => void;
  changed: () => void;
}) {
  const [state, setState] = useState<State>({ subscriptions: [], batches: [] });
  const [query, setQuery] = useState("");
  const [cadence, setCadence] = useState("daily");
  const [docId, setDocId] = useState("");
  const [candidates, setCandidates] = useState<{
    items: Candidate[];
    references: number;
    unresolved: number;
    fingerprint: string;
    unresolved_entries?: UnresolvedReference[];
    resolved_by_title?: number;
  } | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let disposed = false;
    const load = async () => {
      try {
        const value = await api<State>("/api/following");
        if (!disposed) setState(value);
      } catch {
        /* 操作时显示错误 */
      }
    };
    void load();
    const timer = setInterval(load, 3000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, []);
  const act = async (path: string, body: object = {}) => {
    setBusy(true);
    setError("");
    try {
      await api(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setState(await api<State>("/api/following"));
      changed();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="following-panel panel">
      <h2>论文追踪与参考文献下载</h2>
      <p>
        订阅主题，按日或按周查看新论文；从已入库论文的书目批量获取参考文献。
      </p>
      {error && <p role="alert">{error}</p>}
      <details>
        <summary>主题订阅与新论文推荐（{state.subscriptions.length}）</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void act("/api/following/subscriptions", {
              query: query.trim(),
              cadence,
            });
          }}
          className="action-row"
        >
          <input
            aria-label="订阅研究主题"
            placeholder="主题，例如 retrieval augmented generation"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            required
            minLength={2}
            maxLength={240}
          />
          <select
            aria-label="追踪频率"
            value={cadence}
            onChange={(e) => setCadence(e.target.value)}
          >
            <option value="daily">每天</option>
            <option value="weekly">每周</option>
          </select>
          <button disabled={busy || query.trim().length < 2}>创建订阅</button>
        </form>
        <p>
          服务运行时自动到期刷新；停止期间不占显卡，重启后补一次查询。每次取官方最新8篇；库内摘要相关性说明使用本地E5，不是论文质量评分。推荐保存在这里，不发送站外通知。
        </p>
        {state.subscriptions.map((sub) => (
          <article
            key={sub.id}
            style={{ borderTop: "1px solid #ddd", paddingTop: 12 }}
          >
            <h3>
              {sub.query} · {sub.cadence === "daily" ? "每日" : "每周"} ·{" "}
              {sub.enabled ? "追踪中" : "已暂停"}
            </h3>
            <p>
              上次成功：{when(sub.last_run)} ·{" "}
              {sub.enabled
                ? `下次检查：${when(sub.next_run)}`
                : "自动刷新已暂停"}
            </p>
            {sub.error && <p role="status">{sub.error}（保留上次结果）</p>}
            <div className="action-row">
              <button
                disabled={busy}
                onClick={() => void act(`/api/following/${sub.id}/refresh`)}
              >
                立即刷新
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void act(`/api/following/${sub.id}/enabled`, {
                    enabled: !sub.enabled,
                  })
                }
              >
                {sub.enabled ? "暂停追踪" : "恢复追踪"}
              </button>
            </div>
            {[...sub.items]
              .sort(
                (a, b) =>
                  (sub.feedback[b.base_id] || 0) -
                  (sub.feedback[a.base_id] || 0),
              )
              .map((item) => (
                <details key={item.base_id}>
                  <summary>
                    {item.new_in_feed ? "新发现 · " : ""}
                    {item.title} · {item.published.slice(0, 10)}
                  </summary>
                  <p>{item.reason}</p>
                  <p>{item.abstract}</p>
                  <a href={item.url} target="_blank" rel="noreferrer">
                    arXiv原文
                  </a>
                  {item.code_urls.map((link) => (
                    <p key={link}>
                      <a href={link} target="_blank" rel="noreferrer">
                        作者提供的代码链接
                      </a>
                      （未验证可复现性）
                    </p>
                  ))}
                  <div className="action-row">
                    <button
                      disabled={busy}
                      aria-pressed={sub.feedback[item.base_id] === 1}
                      onClick={() =>
                        void act(`/api/following/${sub.id}/feedback`, {
                          base_id: item.base_id,
                          value: sub.feedback[item.base_id] === 1 ? 0 : 1,
                        })
                      }
                    >
                      感兴趣
                    </button>
                    <button
                      disabled={busy}
                      aria-pressed={sub.feedback[item.base_id] === -1}
                      onClick={() =>
                        void act(`/api/following/${sub.id}/feedback`, {
                          base_id: item.base_id,
                          value: sub.feedback[item.base_id] === -1 ? 0 : -1,
                        })
                      }
                    >
                      不感兴趣
                    </button>
                  </div>
                </details>
              ))}
          </article>
        ))}
      </details>
      <details>
        <summary>从参考文献批量下载</summary>
        <label>
          来源论文{" "}
          <select
            aria-label="参考文献来源论文"
            disabled={busy}
            value={docId}
            onChange={(e) => {
              setDocId(e.target.value);
              setCandidates(null);
              setSelected([]);
            }}
          >
            <option value="">选择已解析论文</option>
            {documents
              .filter(
                (d) => d.status === "ready" && d.is_active && !d.trashed_at,
              )
              .map((d) => (
                <option value={d.id} key={d.id}>
                  {d.title}
                </option>
              ))}
          </select>
        </label>
        <button
          disabled={busy || !docId}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              const result = await api<NonNullable<typeof candidates>>(
                `/api/documents/${docId}/reference-candidates`,
              );
              setCandidates(result);
              setSelected([]);
            } catch (reason) {
              setError(reason instanceof Error ? reason.message : "读取失败");
            } finally {
              setBusy(false);
            }
          }}
        >
          读取参考文献
        </button>
        {candidates && (
          <>
            <p>
              识别到{candidates.references}条书目，其中{candidates.items.length}
              个不同arXiv编号可勾选下载（其中{candidates.resolved_by_title || 0}
              个由题名核对）；{candidates.unresolved}
              条待按题名匹配。每批最多30篇。上传后仍需等待解析及版本确认。
            </p>
            <button
              onClick={() =>
                setSelected(
                  candidates.items.slice(0, 30).map((c) => c.arxiv_id),
                )
              }
            >
              选择前30篇
            </button>
            <button onClick={() => setSelected([])}>清空下载选择</button>
            {candidates.items.map((item) => (
              <label
                key={item.arxiv_id}
                style={{ display: "block", margin: "8px 0" }}
              >
                <input
                  type="checkbox"
                  checked={selected.includes(item.arxiv_id)}
                  disabled={
                    !selected.includes(item.arxiv_id) && selected.length >= 30
                  }
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, item.arxiv_id]
                        : selected.filter((id) => id !== item.arxiv_id),
                    )
                  }
                />
                {item.reference} · {item.title ? `${item.title} · ` : ""}arXiv:
                {item.arxiv_id} · 第{item.pages.join("、")}页
                <details>
                  <summary>核对书目</summary>
                  <p>{item.quote}</p>
                  {item.match_method === "title-authors-year" && (
                    <>
                      <p>题名匹配：{item.match?.reasons.join("；")}</p>
                      <a
                        href={item.metadata_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        核对官方元数据
                      </a>
                    </>
                  )}
                </details>
              </label>
            ))}
            <button
              disabled={busy || !selected.length}
              onClick={() =>
                void act(`/api/documents/${docId}/reference-batches`, {
                  fingerprint: candidates.fingerprint,
                  arxiv_ids: selected,
                })
              }
            >
              下载所选参考文献（{selected.length}）
            </button>
            <ReferenceMatching
              key={`${docId}-${candidates.fingerprint}`}
              documentId={docId}
              fingerprint={candidates.fingerprint}
              entries={candidates.unresolved_entries || []}
              disabled={busy}
              pending={setBusy}
              resolved={async () => {
                const latest = await api<NonNullable<typeof candidates>>(
                  `/api/documents/${docId}/reference-candidates`,
                );
                setCandidates(latest);
              }}
            />
          </>
        )}
      </details>
      {state.batches.map((batch) => (
        <details key={batch.id}>
          <summary>
            参考文献批次 · {labels[batch.status] || batch.status} ·{" "}
            {batch.items.filter((i) => i.status === "complete").length}/
            {batch.items.length}
          </summary>
          <p>
            完成指原文件已入库，解析与相似版本决策请到文献库查看。取消只停止后续步骤，当前已提交的单篇保留。
          </p>
          {["running", "queued"].includes(batch.status) ? (
            <button
              disabled={busy}
              onClick={() => void act(`/api/following/${batch.id}/cancel`)}
            >
              取消批量下载
            </button>
          ) : (
            batch.status !== "complete" && (
              <button
                disabled={busy}
                onClick={() => void act(`/api/following/${batch.id}/retry`)}
              >
                继续未完成下载
              </button>
            )
          )}
          {batch.items.map((item) => (
            <p key={item.arxiv_id}>
              arXiv:{item.arxiv_id} · {labels[item.status]} {item.error}
              {item.document_id && (
                <button onClick={() => open(item.document_id)}>
                  查看已入库文献
                </button>
              )}
            </p>
          ))}
        </details>
      ))}
    </section>
  );
}
