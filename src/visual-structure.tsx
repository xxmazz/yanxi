import { useRef, useState } from "react";
import { api } from "./api";

type Item = {
  kind: "table" | "equation";
  label: string;
  bbox: number[];
  rows?: (string | null)[][];
  latex?: string;
  warning: string;
};
type Result = {
  document_id: string;
  page: number;
  source_hash: string;
  model: string;
  checked_at: string;
  cached: boolean;
  items: Item[];
};
type Slice = { page: number; bbox: number[] };
function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function visualCSV(rows: (string | null)[][]) {
  return (
    "\ufeff" +
    rows
      .map((row) =>
        row
          .map((value) => {
            let text = value ?? "";
            if (
              /^[=+\-@]/u.test(text.trimStart()) &&
              !/^[+-]?\d+(?:\.\d+)?$/u.test(text.trim())
            )
              text = "'" + text;
            return '"' + text.replaceAll('"', '""') + '"';
          })
          .join(","),
      )
      .join("\r\n") +
    "\r\n"
  );
}
export function joinVisualTables(results: Result[]) {
  if (!results.length || results.some((result) => !result.items[0]?.rows))
    throw new Error("请先识别各页表格区域");
  const rows = results[0].items[0].rows!.map((row) => [...row]);
  const columns = rows[0].length;
  for (const result of results.slice(1)) {
    const next = result.items[0].rows!;
    if (next.some((row) => row.length !== columns))
      throw new Error("两页列数不同，请先核对选区，不能自动拼接");
    const repeated = JSON.stringify(next[0]) === JSON.stringify(rows[0]);
    rows.push(...next.slice(repeated ? 1 : 0));
  }
  return rows;
}
export function VisualStructure({
  documentId,
  number,
  pages,
}: {
  documentId: string;
  number: number;
  pages: number;
}) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<"table" | "equation">("table");
  const [columns, setColumns] = useState(0);
  const [slices, setSlices] = useState<Slice[]>([]);
  const [bbox, setBox] = useState<number[] | null>(null);
  const [draw, setDraw] = useState<{ start: number[]; end: number[] } | null>(
    null,
  );
  const [pickPage, setPickPage] = useState(number);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<Result[]>([]);
  const canvas = useRef<HTMLDivElement>(null);
  const position = (event: React.PointerEvent) => {
    const rect = canvas.current!.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    ];
  };
  const reset = () => {
    setResults([]);
    setSlices([]);
    setBox(null);
    setError("");
    setPickPage(number);
  };
  const rows =
    results.length && kind === "table"
      ? (() => {
          try {
            return joinVisualTables(results);
          } catch {
            return null;
          }
        })()
      : null;
  const rectangle = draw
    ? [
        Math.min(draw.start[0], draw.end[0]),
        Math.min(draw.start[1], draw.end[1]),
        Math.max(draw.start[0], draw.end[0]),
        Math.max(draw.start[1], draw.end[1]),
      ]
    : bbox;
  return (
    <section className="visual-structure" aria-label="扫描表格与公式识别">
      <button onClick={() => setOpen(!open)}>
        {open ? "收起图片识别" : "扫描表格 / 公式 LaTeX 识别"}
      </button>
      {open && (
        <>
          <p>
            在原页拖框选取一个表格或公式，使用服务器本地视觉模型识别。跨页表格可先保存本页选区，再选择下一页的续表；原页和出处随结果保留。
          </p>
          <label>
            识别内容{" "}
            <select
              aria-label="图片识别类型"
              value={kind}
              disabled={busy || !!slices.length}
              onChange={(event) => {
                setKind(event.target.value as "table" | "equation");
                reset();
              }}
            >
              <option value="table">表格单元格</option>
              <option value="equation">数学公式 LaTeX</option>
            </select>
          </label>
          {kind === "table" && (
            <label>
              表格列数（含名称列，0为自动）{" "}
              <input
                aria-label="表格预期列数"
                type="number"
                min={0}
                max={24}
                value={columns}
                disabled={busy || !!slices.length}
                onChange={(event) => {
                  setColumns(
                    Math.max(0, Math.min(24, Number(event.target.value) || 0)),
                  );
                  setResults([]);
                }}
              />
            </label>
          )}
          <p>选区物理页：{pickPage} · 按住并拖动，选取完整边界。</p>
          <div
            ref={canvas}
            className="visual-region-picker"
            onPointerDown={(event) => {
              if (busy) return;
              event.currentTarget.setPointerCapture(event.pointerId);
              const point = position(event);
              setDraw({ start: point, end: point });
            }}
            onPointerMove={(event) => {
              if (draw) setDraw({ ...draw, end: position(event) });
            }}
            onPointerUp={(event) => {
              if (!draw) return;
              const end = position(event);
              const chosen = [
                Math.min(draw.start[0], end[0]),
                Math.min(draw.start[1], end[1]),
                Math.max(draw.start[0], end[0]),
                Math.max(draw.start[1], end[1]),
              ];
              setDraw(null);
              setBox(chosen);
              setResults([]);
            }}
            onPointerCancel={() => setDraw(null)}
          >
            <img
              src={`/api/documents/${documentId}/render/${pickPage}`}
              alt={`图片识别选区原页 ${pickPage}`}
              draggable={false}
            />
            {rectangle && (
              <div
                className="visual-region-box"
                style={{
                  left: `${rectangle[0] * 100}%`,
                  top: `${rectangle[1] * 100}%`,
                  width: `${(rectangle[2] - rectangle[0]) * 100}%`,
                  height: `${(rectangle[3] - rectangle[1]) * 100}%`,
                }}
              />
            )}
          </div>
          {bbox && (
            <p>
              选区：
              {bbox.map((value) => (value * 100).toFixed(1) + "%").join("，")}
            </p>
          )}
          {!!slices.length && (
            <p>
              已保留第{slices.map((slice) => slice.page).join("、")}
              页选区；请再框选第{pickPage}页的续表。
            </p>
          )}
          <div className="visual-actions">
            {kind === "table" && !slices.length && pickPage < pages && (
              <button
                disabled={busy || !bbox}
                onClick={() => {
                  if (!bbox) return;
                  setSlices([{ page: pickPage, bbox }]);
                  setBox(null);
                  setPickPage(pickPage + 1);
                  setResults([]);
                }}
              >
                保留选区并选择下一页续表
              </button>
            )}
            <button
              disabled={busy || !bbox}
              onClick={async () => {
                if (!bbox) return;
                setBusy(true);
                setError("");
                setResults([]);
                try {
                  const complete: Result[] = [];
                  for (const slice of [...slices, { page: pickPage, bbox }])
                    complete.push(
                      await api<Result>(
                        `/api/documents/${documentId}/pages/${slice.page}/visual-structure`,
                        {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({
                            kind,
                            bbox: slice.bbox,
                            expected_columns: kind === "table" ? columns : 0,
                          }),
                        },
                      ),
                    );
                  if (kind === "table") joinVisualTables(complete);
                  setResults(complete);
                } catch (e) {
                  setError(e instanceof Error ? e.message : "识别失败");
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "本地GPU正在识别…" : "识别所选区域"}
            </button>
            <button disabled={busy} onClick={reset}>
              清空选区
            </button>
          </div>
          {error && <p role="alert">{error}</p>}
          {results.map((result) => (
            <article key={result.page}>
              <h5>
                第{result.page}页识别结果{result.cached ? " · 已保存结果" : ""}
              </h5>
              <small>
                {result.model} ·{" "}
                {new Date(result.checked_at).toLocaleString("zh-CN")}
              </small>
              <p>
                <a
                  href={`/api/documents/${documentId}/pdf#page=${result.page}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  核对第{result.page}页原PDF
                </a>
              </p>
              {result.items.map((item, i) => (
                <div key={i}>
                  <p>{item.warning}</p>
                  {item.latex && (
                    <>
                      <pre>{item.latex}</pre>
                      <button
                        onClick={() =>
                          download(
                            `equation-p${result.page}.tex`,
                            `% Source document SHA-256: ${documentId}\n% Physical page: ${result.page}; bbox: ${item.bbox.join(",")}\n\\[\n${item.latex}\n\\]\n`,
                            "text/plain;charset=utf-8",
                          )
                        }
                      >
                        导出公式 LaTeX
                      </button>
                    </>
                  )}
                </div>
              ))}
            </article>
          ))}
          {rows && (
            <>
              <div className="structure-table-scroll">
                <table>
                  <tbody>
                    {rows.map((row, i) => (
                      <tr key={i}>
                        {row.map((cell, j) => (
                          <td key={j}>{cell ?? "—"}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>
                共{rows.length}行，{rows[0].length}
                列。续表仅移除与首页完全相同的一行表头，其余行保留。
              </p>
              <button
                onClick={() =>
                  download(
                    `visual-table-p${results.map((r) => r.page).join("-")}.csv`,
                    visualCSV(rows),
                    "text/csv;charset=utf-8",
                  )
                }
              >
                导出识别表格 CSV
              </button>
            </>
          )}
          {!!results.length && (
            <button
              onClick={() =>
                download(
                  "研析-图片结构及来源.json",
                  JSON.stringify(results, null, 2),
                  "application/json",
                )
              }
            >
              导出识别结果与出处
            </button>
          )}
        </>
      )}
    </section>
  );
}
