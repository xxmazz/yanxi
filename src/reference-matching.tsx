import { useEffect, useRef, useState } from "react";
import { api } from "./api";

export type ReferenceResolution = {
  reference: string;
  status: "matched" | "ambiguous" | "no_match";
  search_title: string;
  checked_at: string;
  note: string;
  candidates: {
    title: string;
    arxiv_id: string;
    authors: string[];
    published: string;
    url: string;
    match: { verified: boolean; reasons: string[] };
  }[];
};
export type UnresolvedReference = {
  reference: string;
  title: string;
  authors: string;
  year: number | null;
  quote: string;
  pages: number[];
  resolution: ReferenceResolution | null;
};
export function ReferenceMatching({
  documentId,
  fingerprint,
  entries,
  disabled,
  pending,
  resolved,
}: {
  documentId: string;
  fingerprint: string;
  entries: UnresolvedReference[];
  disabled: boolean;
  pending: (value: boolean) => void;
  resolved: () => Promise<void>;
}) {
  const [running, setRunning] = useState("");
  const [results, setResults] = useState<Record<string, ReferenceResolution>>(
    {},
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const alive = useRef(true);
  const cancelled = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      cancelled.current = true;
    };
  }, []);
  const match = async (selected: UnresolvedReference[]) => {
    if (running || disabled) return;
    cancelled.current = false;
    pending(true);
    try {
      for (const entry of selected) {
        if (!alive.current || cancelled.current) break;
        setRunning(entry.reference);
        setErrors((previous) => ({ ...previous, [entry.reference]: "" }));
        try {
          const result = await api<ReferenceResolution>(
            `/api/documents/${documentId}/reference-resolutions`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ fingerprint, reference: entry.reference }),
            },
          );
          if (alive.current)
            setResults((previous) => ({
              ...previous,
              [entry.reference]: result,
            }));
        } catch (e) {
          if (alive.current)
            setErrors((previous) => ({
              ...previous,
              [entry.reference]: e instanceof Error ? e.message : "匹配失败",
            }));
          // 网络或原文失败时停止本批，不连续重试造成请求风暴。
          break;
        }
      }
      if (alive.current) await resolved();
    } catch (e) {
      if (alive.current)
        setErrors((previous) => ({
          ...previous,
          refresh:
            e instanceof Error ? e.message : "刷新候选失败，请重新读取书目",
        }));
    } finally {
      if (alive.current) {
        setRunning("");
        pending(false);
      }
    }
  };
  const missing = entries.filter(
    (entry) => entry.title && !entry.resolution && !results[entry.reference],
  );
  return (
    <section className="reference-matching" aria-label="按题名匹配参考文献">
      <h3>无编号书目：按题名匹配</h3>
      {errors.refresh && <p role="alert">{errors.refresh}</p>}
      <p>在 arXiv 核对题名、作者和年份；唯一匹配后可勾选下载。</p>
      <button
        disabled={disabled || !!running || !missing.length}
        onClick={() => void match(missing.slice(0, 5))}
      >
        匹配前5条无编号书目
      </button>
      {!!running && (
        <>
          <p role="status">正在匹配 {running}，官方请求有间隔…</p>
          <button
            onClick={() => {
              cancelled.current = true;
            }}
          >
            停止后续匹配
          </button>
        </>
      )}
      {entries.map((entry) => {
        const result = results[entry.reference] || entry.resolution;
        return (
          <details key={entry.reference} className="reference-match-entry">
            <summary>
              {entry.reference} · {entry.title || "题名无法可靠分离"}{" "}
              {result
                ? result.status === "matched"
                  ? "· 已核对"
                  : "· 待人工核对"
                : ""}
            </summary>
            <p>
              {entry.authors} · {entry.year || "年份缺失"}
            </p>
            <p>{entry.quote}</p>
            <button
              disabled={disabled || !!running || !entry.title}
              onClick={() => void match([entry])}
            >
              {result ? "重新匹配此书目" : "匹配此书目"}
            </button>
            {errors[entry.reference] && (
              <p role="alert">{errors[entry.reference]}</p>
            )}
            {result && (
              <div>
                <p>
                  {result.status === "matched"
                    ? "唯一匹配已保存，已加入可勾选的下载列表。"
                    : result.status === "ambiguous"
                      ? "有多个身份同时符合，暂不加入下载。"
                      : "没有找到通过全部核对的候选，暂不加入下载。"}
                </p>
                <p>{result.note}</p>
                {result.candidates.map((candidate) => (
                  <article key={candidate.arxiv_id}>
                    <a href={candidate.url} target="_blank" rel="noreferrer">
                      {candidate.title} · arXiv:{candidate.arxiv_id}
                    </a>
                    <p>
                      {candidate.authors.join("; ")} ·{" "}
                      {candidate.published.slice(0, 10)}
                    </p>
                    <p>{candidate.match.reasons.join("；")}</p>
                  </article>
                ))}
              </div>
            )}
          </details>
        );
      })}
      {!entries.length && <p>当前没有待匹配的无编号书目。</p>}
    </section>
  );
}
