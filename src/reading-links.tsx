import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { ParsedPage } from "./api";

type Anchor = {
  page: number;
  start: number;
  end: number;
  quote: string;
  page_sha256: string;
};
type Mention = Anchor & { numbers: number[] };
type Entry = { number: number; evidence: Anchor[] };
type Navigation = {
  document_id: string;
  fingerprint: string;
  entries: Entry[];
  mentions: Mention[];
  limitations: string;
};

export function ReadingLinks({
  documentId,
  page,
  jump,
}: {
  documentId: string;
  page: ParsedPage | null;
  jump: (number: number) => void;
}) {
  const [data, setData] = useState<Navigation | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [origin, setOrigin] = useState<Mention | null>(null);
  const [active, setActive] = useState<Anchor | null>(null);
  const highlight = useRef<HTMLElement | null>(null);
  useEffect(() => {
    let disposed = false;
    setData(null);
    setError("");
    setSelected([]);
    setOrigin(null);
    setActive(null);
    api<Navigation>(`/api/documents/${documentId}/reading-links`)
      .then((value) => {
        if (!disposed) setData(value);
      })
      .catch((reason) => {
        if (!disposed) setError(reason.message);
      });
    return () => {
      disposed = true;
    };
  }, [documentId]);
  useEffect(() => {
    highlight.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [page?.page, active]);
  const chars = Array.from(page?.text || "");
  const validActive =
    active &&
    active.page === page?.page &&
    chars.slice(active.start, active.end).join("") === active.quote;
  const textRange = (start: number, end: number) => {
    if (!validActive || active.end <= start || active.start >= end)
      return chars.slice(start, end).join("");
    const low = Math.max(start, active.start),
      high = Math.min(end, active.end);
    return (
      <>
        {chars.slice(start, low).join("")}
        <mark ref={highlight}>{chars.slice(low, high).join("")}</mark>
        {chars.slice(high, end).join("")}
      </>
    );
  };
  const matches =
    data?.mentions.filter(
      (item) =>
        item.page === page?.page &&
        chars.slice(item.start, item.end).join("") === item.quote,
    ) || [];
  const locate = (anchor: Anchor) => {
    setActive(anchor);
    jump(anchor.page);
  };
  const pieces = [];
  let cursor = 0;
  for (const item of matches) {
    pieces.push(
      <span key={`text-${cursor}`}>{textRange(cursor, item.start)}</span>,
    );
    pieces.push(
      <button
        key={item.start}
        className="inline-reference"
        aria-label={`查看参考文献 ${item.numbers.join("、")}`}
        onClick={() => {
          setOrigin(item);
          setSelected(item.numbers);
          const entry = data?.entries.find(
            (entry) => entry.number === item.numbers[0],
          );
          if (entry?.evidence[0]) locate(entry.evidence[0]);
        }}
      >
        {textRange(item.start, item.end)}
      </button>,
    );
    cursor = item.end;
  }
  pieces.push(
    <span key={`text-${cursor}`}>{textRange(cursor, chars.length)}</span>,
  );
  return (
    <>
      <details
        className="reading-references"
        open={selected.length > 0 || undefined}
      >
        <summary>
          参考文献导航
          {data
            ? ` · ${data.entries.length} 条书目 / ${data.mentions.length} 处正文引用`
            : ""}
        </summary>
        {error && <p role="status">{error}</p>}
        {!data && !error && <p>正在定位参考文献…</p>}
        {data && (
          <>
            <p>
              {data.entries.length
                ? "点击下方正文的引用编号，查看书目并跳到所在物理页。"
                : "没有识别到可跳转的数字制书目，请用原页核对。"}
            </p>
            {origin && (
              <button onClick={() => locate(origin)}>
                返回刚才的正文 · 第 {origin.page} 页
              </button>
            )}
            <label>
              选择书目{" "}
              <select
                aria-label="选择参考文献"
                value={selected.length === 1 ? selected[0] : ""}
                onChange={(event) => {
                  const number = Number(event.target.value);
                  setSelected(number ? [number] : []);
                  const entry = data.entries.find(
                    (entry) => entry.number === number,
                  );
                  if (entry?.evidence[0]) locate(entry.evidence[0]);
                }}
              >
                <option value="">选择编号</option>
                {data.entries.map((entry) => (
                  <option key={entry.number} value={entry.number}>
                    [{entry.number}] {entry.evidence[0]?.quote.slice(0, 90)}
                  </option>
                ))}
              </select>
            </label>
            {data.entries
              .filter((entry) => selected.includes(entry.number))
              .map((entry) => (
                <section key={entry.number}>
                  <h4>参考文献 [{entry.number}]</h4>
                  {entry.evidence.map((anchor) => (
                    <div key={`${anchor.page}-${anchor.start}`}>
                      <blockquote>{anchor.quote}</blockquote>
                      <button onClick={() => locate(anchor)}>
                        定位书目 · 第 {anchor.page} 页
                      </button>{" "}
                      <a
                        href={`/api/documents/${documentId}/pdf#page=${anchor.page}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        核对原始 PDF
                      </a>
                    </div>
                  ))}
                  <p>正文引用位置：</p>
                  <div className="reference-occurrences">
                    {data.mentions
                      .filter((item) => item.numbers.includes(entry.number))
                      .map((item) => (
                        <button
                          key={`${item.page}-${item.start}`}
                          onClick={() => locate(item)}
                        >
                          第 {item.page} 页 · {item.quote} · 字符 {item.start}
                        </button>
                      ))}
                  </div>
                </section>
              ))}
            <small>
              {data.limitations} 导航定位在解析正文中，左侧原页同步到对应页。
            </small>
          </>
        )}
      </details>
      {page && <pre className="reference-page-text">{pieces}</pre>}
      {active && active.page === page?.page && !validActive && (
        <p role="status">
          定位片段与当前正文不一致，请重新打开此文献以刷新导航。
        </p>
      )}
    </>
  );
}
