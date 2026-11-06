import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, type ParsedPage } from "./api";

type Range = { start: number; end: number; text: string };
type Job = {
  id: string;
  status: "queued" | "running" | "ready" | "error" | "cancelled";
  source_text: string;
  target: string;
  result: string;
  error: string;
};
type Result = { status: "ready" | "error"; result: string; error: string };
type Selection = Range & {
  status: Job["status"];
  result: string;
  error: string;
  cached?: boolean;
};

// 保留 Unicode 字符偏移；公式和代码为不可悬停的原文，避免从中间切开。
export function hoverRanges(text: string, start: number, end: number): Range[] {
  const chars = Array.from(text);
  const source = chars.slice(start, end).join("");
  const ranges: Range[] = [];
  let position = start;
  const protectedPattern =
    /```[\s\S]*?```|\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|`[^`\n]+`|\$[^$\n]+\$/gu;
  const split = (value: string) => {
    const segmenter = new Intl.Segmenter("en", { granularity: "sentence" });
    // PDF软换行不是句号；等长替换后分句，再从原文取区间，保持偏移与原文不变。
    const forSegmentation = value.replace(/(?<!\n)\n(?!\n)/gu, " ");
    for (const sentence of segmenter.segment(forSegmentation)) {
      const remaining = Array.from(
        value.slice(sentence.index, sentence.index + sentence.segment.length),
      );
      while (remaining.length) {
        let size = Math.min(700, remaining.length);
        if (remaining.length > size) {
          for (let i = size; i >= 350; i--)
            if (/\s/u.test(remaining[i - 1])) {
              size = i;
              break;
            }
        }
        const part = remaining.splice(0, size).join("");
        const length = Array.from(part).length;
        if (
          (part.match(/[A-Za-z]/gu)?.length ?? 0) >= 15 ||
          (part.match(/\p{Script=Han}/gu)?.length ?? 0) >= 4
        )
          ranges.push({ start: position, end: position + length, text: part });
        position += length;
      }
    }
  };
  let cursor = 0;
  for (const match of source.matchAll(protectedPattern)) {
    split(source.slice(cursor, match.index));
    position += Array.from(match[0]).length;
    cursor = match.index + match[0].length;
  }
  split(source.slice(cursor));
  return ranges;
}

export function useHoverTranslation(
  documentId: string,
  page: ParsedPage | null,
) {
  const [enabled, setEnabled] = useState(false);
  const [target, setTarget] = useState("zh");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [notice, setNotice] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  const sequence = useRef(0);
  const running = useRef(false);
  const cache = useRef(new Map<string, Result>());
  const scope = `${documentId}\0${page?.page}\0${target}\0${enabled}\0${page?.text}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const clearTimer = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  const dismiss = () => {
    clearTimer();
    sequence.current++;
    setSelection(null);
    setNotice("");
  };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      clearTimer();
    };
  }, []);
  useEffect(() => {
    clearTimer();
    sequence.current++;
    setSelection(null);
    setNotice("");
  }, [scope]);

  const translate = async (range: Range, retry = false) => {
    clearTimer();
    if (!enabled || !page || !mounted.current || scope !== currentScope.current)
      return;
    const key = `${target}\0${range.text}`;
    const previous = cache.current.get(key);
    if (previous && !retry) {
      sequence.current++;
      setSelection({ ...range, ...previous, cached: true });
      setNotice("");
      return;
    }
    if (running.current) {
      setNotice("正在翻译一个选段，请完成后再查看其他句子。");
      return;
    }
    const token = ++sequence.current;
    const captured = scope;
    const visible = () =>
      mounted.current &&
      captured === currentScope.current &&
      token === sequence.current;
    const show = (result: Partial<Selection>) => {
      if (visible()) setSelection((old) => old && { ...old, ...result });
    };
    running.current = true;
    setNotice("");
    setSelection({ ...range, status: "queued", result: "", error: "" });
    try {
      let job = await api<Job>(
        `/api/documents/${documentId}/translation/selection/${page.page}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            start: range.start,
            end: range.end,
            text: range.text,
            target,
          }),
        },
      );
      const deadline = Date.now() + 120000;
      while (true) {
        if (job.source_text !== range.text || job.target !== target)
          throw new Error("翻译任务与当前选段不一致，请重试。");
        if (job.status === "ready") {
          if (!job.result.trim()) throw new Error("本地模型未返回译文。");
          const result: Result = {
            status: "ready",
            result: job.result,
            error: "",
          };
          cache.current.set(key, result);
          show(result);
          break;
        }
        if (job.status === "error" || job.status === "cancelled")
          throw new Error(job.error || "翻译任务已取消，可重新发起。");
        if (!mounted.current) break;
        if (Date.now() > deadline)
          throw new Error("翻译仍在处理，稍后可重新查看此选段。");
        show({ status: job.status });
        await new Promise((resolve) => setTimeout(resolve, 600));
        job = await api<Job>(`/api/translation/jobs/${job.id}`);
      }
    } catch (error) {
      const result: Result = {
        status: "error",
        result: "",
        error: error instanceof Error ? error.message : "翻译失败",
      };
      cache.current.set(key, result);
      show(result);
    } finally {
      running.current = false;
      if (cache.current.size > 60)
        cache.current.delete(cache.current.keys().next().value!);
    }
  };
  const decorate = (
    start: number,
    end: number,
    render: (start: number, end: number) => ReactNode,
  ): ReactNode => {
    if (!enabled || !page) return render(start, end);
    const ranges = hoverRanges(page.text, start, end);
    const pieces: ReactNode[] = [];
    let cursor = start;
    for (const range of ranges) {
      pieces.push(
        <span key={`plain-${cursor}`}>{render(cursor, range.start)}</span>,
      );
      pieces.push(
        <span
          key={`hover-${range.start}`}
          className="hover-translation-source"
          role="button"
          tabIndex={0}
          aria-label={`翻译选段：${range.text.trim().slice(0, 60)}`}
          onMouseEnter={() => {
            clearTimer();
            timer.current = setTimeout(() => void translate(range), 550);
          }}
          onMouseLeave={clearTimer}
          onFocus={() => {
            clearTimer();
            timer.current = setTimeout(() => void translate(range), 550);
          }}
          onBlur={clearTimer}
          onClick={() => void translate(range)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              void translate(range);
            }
          }}
        >
          {render(range.start, range.end)}
        </span>,
      );
      cursor = range.end;
    }
    pieces.push(<span key={`plain-${cursor}`}>{render(cursor, end)}</span>);
    return pieces;
  };
  const controls = (
    <section
      className="hover-translation-controls"
      aria-label="阅读悬停翻译"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          clearTimer();
          sequence.current++;
          setSelection(null);
        }
      }}
    >
      <div className="hover-translation-toolbar">
        <button aria-pressed={enabled} onClick={() => setEnabled(!enabled)}>
          {enabled ? "关闭悬停翻译" : "开启悬停翻译"}
        </button>
        {enabled && (
          <label>
            译文语言{" "}
            <select
              aria-label="悬停译文语言"
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            >
              <option value="zh">中文</option>
              <option value="en">英文</option>
            </select>
          </label>
        )}
      </div>
      {enabled && (
        <small>悬停、点击句子或按 Enter 查看译文；公式和代码保留原文。</small>
      )}
      {notice && <p role="status">{notice}</p>}
      {enabled && selection && (
        <aside
          className="hover-translation-result"
          aria-label="选段译文"
          aria-live="polite"
        >
          <div>
            <strong>
              第 {page?.page} 页 · {target === "zh" ? "中文" : "英文"}译文
              {selection.cached ? " · 会话缓存" : ""}
            </strong>
            <button
              aria-label="关闭选段译文"
              onClick={() => {
                clearTimer();
                sequence.current++;
                setSelection(null);
              }}
            >
              关闭
            </button>
          </div>
          <blockquote>{selection.text}</blockquote>
          {selection.status === "ready" ? (
            <p>{selection.result}</p>
          ) : selection.status === "error" ? (
            <>
              <p role="alert">{selection.error}</p>
              <button onClick={() => void translate(selection, true)}>
                重试选段翻译
              </button>
            </>
          ) : (
            <p role="status">
              {selection.status === "queued"
                ? "已排队，等待本地模型…"
                : "本地模型正在翻译…"}
            </p>
          )}
        </aside>
      )}
    </section>
  );
  return { controls, decorate, dismiss };
}
