import { useEffect, useRef, useState } from "react";
import type { UIEvent } from "react";
import { api } from "./api";

type Direction = "zh" | "en";
type Segment = { source: string; translation: string | null };
type TranslationPage = {
  page: number;
  segments: Segment[];
  complete: boolean;
  method: string;
};
type Job = {
  id: string;
  document_id: string;
  target: Direction;
  pages: number[];
  status: "queued" | "running" | "ready" | "error" | "cancelled";
  completed: number;
  total: number;
  error: string;
  result: string;
  cancel_requested: number;
};
const active = (job: Job | null) =>
  !!job && ["queued", "running"].includes(job.status);
const jobLabel = (job: Job) =>
  ({
    queued: "排队中",
    running: "本地模型翻译中",
    ready: "翻译完成",
    error: "翻译中断",
    cancelled: "已取消",
  })[job.status];

export function TranslationReader({
  documentId,
  number,
}: {
  documentId: string;
  number: number;
}) {
  const [target, setTarget] = useState<Direction>("zh");
  const [page, setPage] = useState<TranslationPage | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [linked, setLinked] = useState(true);
  const [selection, setSelection] = useState("");
  const [text, setText] = useState("");
  const [snippetTarget, setSnippetTarget] = useState<Direction>("zh");
  const [snippetJob, setSnippetJob] = useState<Job | null>(null);
  const [snippetBusy, setSnippetBusy] = useState(false);
  const left = useRef<HTMLDivElement>(null);
  const right = useRef<HTMLDivElement>(null);
  const syncing = useRef<HTMLDivElement | null>(null);
  const scope = `${documentId}/${number}/${target}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;

  useEffect(() => {
    let disposed = false;
    setPage(null);
    setError("");
    setSelection("");
    api<TranslationPage>(
      `/api/documents/${documentId}/translation/pages/${number}?target=${target}`,
    )
      .then((value) => {
        if (!disposed) setPage(value);
      })
      .catch((reason) => {
        if (!disposed) setError(reason.message);
      });
    left.current?.scrollTo(0, 0);
    right.current?.scrollTo(0, 0);
    return () => {
      disposed = true;
    };
  }, [documentId, number, target]);

  useEffect(() => {
    let disposed = false;
    setJob(null);
    api<Job[]>(`/api/documents/${documentId}/translation/jobs`)
      .then((jobs) => {
        if (!disposed)
          setJob(
            jobs.find((item) => item.target === target && active(item)) ||
              jobs.find((item) => item.target === target) ||
              null,
          );
      })
      .catch((reason) => {
        if (!disposed) setError(reason.message);
      });
    return () => {
      disposed = true;
    };
  }, [documentId, target]);

  useEffect(() => {
    if (!job || !active(job)) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await api<Job>(`/api/translation/jobs/${job.id}`);
        const updated = await api<TranslationPage>(
          `/api/documents/${documentId}/translation/pages/${number}?target=${target}`,
        );
        if (!disposed) {
          setJob(value);
          setPage(updated);
        }
      } catch (reason) {
        if (!disposed)
          setError(
            reason instanceof Error ? reason.message : "读取翻译状态失败",
          );
      }
      if (!disposed) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [job?.id, job?.status, documentId, number, target]);

  useEffect(() => {
    if (!snippetJob || !active(snippetJob)) return;
    let disposed = false;
    const timer = setInterval(() => {
      api<Job>(`/api/translation/jobs/${snippetJob.id}`)
        .then((value) => {
          if (!disposed) setSnippetJob(value);
        })
        .catch((reason) => {
          if (!disposed) setError(reason.message);
        });
    }, 1500);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [snippetJob?.id, snippetJob?.status]);

  const start = async (whole: boolean) => {
    setBusy(true);
    setError("");
    const startedScope = scope;
    try {
      const value = await api<Job>(`/api/documents/${documentId}/translation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target, ...(whole ? {} : { page: number }) }),
      });
      if (currentScope.current === startedScope) {
        setJob(value);
        if (value.status === "ready") {
          const updated = await api<TranslationPage>(
            `/api/documents/${documentId}/translation/pages/${number}?target=${target}`,
          );
          if (currentScope.current === startedScope) setPage(updated);
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "翻译启动失败");
    } finally {
      setBusy(false);
    }
  };

  const syncScroll = (
    event: UIEvent<HTMLDivElement>,
    other: HTMLDivElement | null,
  ) => {
    const from = event.currentTarget;
    if (!linked || !other || syncing.current === from) return;
    const originals = Array.from(
      from.querySelectorAll<HTMLElement>("[data-segment]"),
    );
    const targets = Array.from(
      other.querySelectorAll<HTMLElement>("[data-segment]"),
    );
    const index = Math.max(
      0,
      originals.findIndex(
        (item) => item.offsetTop + item.offsetHeight > from.scrollTop + 1,
      ),
    );
    if (!originals[index] || !targets[index]) return;
    const fraction = Math.max(
      0,
      Math.min(
        1,
        (from.scrollTop - originals[index].offsetTop) /
          Math.max(1, originals[index].offsetHeight),
      ),
    );
    const next =
      from.scrollTop + from.clientHeight >= from.scrollHeight - 2
        ? other.scrollHeight - other.clientHeight
        : targets[index].offsetTop + fraction * targets[index].offsetHeight;
    syncing.current = other;
    other.scrollTop = next;
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        syncing.current = null;
      }),
    );
  };

  const exportMarkdown = async () => {
    setError("");
    try {
      const response = await fetch(
        `/api/documents/${documentId}/translation/export?target=${target}`,
      );
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail || "导出失败");
      }
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `研析-对照翻译-${target}.md`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导出失败");
    }
  };

  return (
    <section className="translation-reader" aria-label="本地对照翻译">
      <div className="translation-toolbar">
        <label>
          译文语言{" "}
          <select
            aria-label="译文语言"
            value={target}
            onChange={(e) => setTarget(e.target.value as Direction)}
          >
            <option value="zh">简体中文</option>
            <option value="en">学术英文</option>
          </select>
        </label>
        <button
          disabled={busy || active(job)}
          onClick={() => void start(false)}
        >
          翻译本页
        </button>
        <button disabled={busy || active(job)} onClick={() => void start(true)}>
          翻译整篇
        </button>
        <button onClick={() => void exportMarkdown()}>
          导出整篇对照 Markdown
        </button>
        <label>
          <input
            type="checkbox"
            checked={linked}
            onChange={(e) => setLinked(e.target.checked)}
          />
          双向联动滚动
        </label>
      </div>
      <p className="translation-note">
        逐段对应原文；公式标记、代码与数字原样保留。机器译文请结合原页核对，扫描识别错误可能传入译文。
      </p>
      {error && (
        <p role="alert" className="translation-error">
          {error}
        </p>
      )}
      {job && (
        <div className="translation-progress" role="status">
          <span>
            {job.pages.length > 1
              ? `整篇 ${job.pages.length} 页`
              : `第 ${job.pages[0]} 页`}{" "}
            · {jobLabel(job)} · {job.completed} / {job.total || "待统计"} 段
          </span>
          <progress value={job.completed} max={job.total || 1} />
          {active(job) && (
            <button
              disabled={!!job.cancel_requested}
              onClick={async () => {
                try {
                  setJob(
                    await api<Job>(`/api/translation/jobs/${job.id}/cancel`, {
                      method: "POST",
                    }),
                  );
                } catch (reason) {
                  setError(
                    reason instanceof Error ? reason.message : "取消失败",
                  );
                }
              }}
            >
              {job.cancel_requested ? "当前段完成后停止" : "取消任务"}
            </button>
          )}
          {job.error && (
            <span className="translation-error">
              {job.error}。已完成段落保留，可重新发起。
            </span>
          )}
        </div>
      )}
      <div className="translation-headings">
        <b>论文原文 · 第 {number} 页</b>
        <b>
          {target === "zh" ? "中文" : "英文"}译文
          {page?.complete ? " · 本页已完成" : ""}
        </b>
      </div>
      <div className="translation-columns">
        <div
          ref={left}
          className="translation-pane"
          tabIndex={0}
          aria-label="论文原文滚动区域"
          onScroll={(e) => syncScroll(e, right.current)}
          onMouseUp={() => {
            const selected = window.getSelection();
            if (
              selected &&
              left.current?.contains(selected.anchorNode) &&
              left.current?.contains(selected.focusNode)
            ) {
              const value = selected.toString().trim();
              setSelection(value.length <= 2400 ? value : "");
            }
          }}
        >
          {page ? (
            page.segments.length ? (
              page.segments.map((item, i) => (
                <div data-segment={i} className="translation-segment" key={i}>
                  <small>段落 {i + 1}</small>
                  <p>{item.source}</p>
                </div>
              ))
            ) : (
              <p>本页没有可翻译正文。</p>
            )
          ) : (
            <p>正在加载原文…</p>
          )}
        </div>
        <div
          ref={right}
          className="translation-pane"
          tabIndex={0}
          aria-label="论文译文滚动区域"
          onScroll={(e) => syncScroll(e, left.current)}
        >
          {page?.segments.map((item, i) => (
            <div data-segment={i} className="translation-segment" key={i}>
              <small>段落 {i + 1}</small>
              <p>
                {item.translation ?? "待翻译 · 点击上方“翻译本页”或“翻译整篇”"}
              </p>
            </div>
          ))}
        </div>
      </div>
      <details className="translation-snippet">
        <summary>局部翻译 / 中文摘要译为学术英文</summary>
        <p>可在左侧选中文本后带入，或直接粘贴最多 2400 字的段落。</p>
        <button
          disabled={!selection || active(snippetJob)}
          onClick={() => {
            setText(selection);
            setSnippetJob(null);
          }}
        >
          带入选中文本{selection ? `（${selection.length} 字）` : ""}
        </button>
        <textarea
          aria-label="局部待翻译文本"
          placeholder="粘贴论文段落或中文摘要…"
          maxLength={2400}
          disabled={active(snippetJob)}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setSnippetJob(null);
          }}
        />
        <div className="action-row">
          <label>
            翻译为{" "}
            <select
              aria-label="局部译文语言"
              value={snippetTarget}
              disabled={active(snippetJob)}
              onChange={(e) => {
                setSnippetTarget(e.target.value as Direction);
                setSnippetJob(null);
              }}
            >
              <option value="zh">简体中文</option>
              <option value="en">学术英文</option>
            </select>
          </label>
          <button
            disabled={!text.trim() || snippetBusy || active(snippetJob)}
            onClick={async () => {
              setSnippetBusy(true);
              setError("");
              setSnippetJob(null);
              try {
                setSnippetJob(
                  await api<Job>("/api/translation/text", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ text, target: snippetTarget }),
                  }),
                );
              } catch (reason) {
                setError(
                  reason instanceof Error ? reason.message : "局部翻译失败",
                );
              } finally {
                setSnippetBusy(false);
              }
            }}
          >
            翻译这段文本
          </button>
        </div>
        {snippetJob && (
          <div className="snippet-result" role="status">
            <b>
              {snippetJob.target === "zh" ? "中文译文" : "英文译文"} ·{" "}
              {jobLabel(snippetJob)}
            </b>
            <p>
              {snippetJob.result || snippetJob.error || "本地模型正在处理…"}
            </p>
          </div>
        )}
      </details>
    </section>
  );
}
