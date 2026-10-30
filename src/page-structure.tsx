import { useState } from "react";
import { api } from "./api";

type StructureItem = {
  id: string;
  kind: "table" | "equation";
  label: string;
  caption?: string;
  text: string;
  warning: string;
  bbox: number[];
  rows?: (string | null)[][];
};
type Structure = {
  page: number;
  items: StructureItem[];
  note: string;
};

export function PageStructure({
  documentId,
  number,
}: {
  documentId: string;
  number: number;
}) {
  const [result, setResult] = useState<Structure | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const base = `/api/documents/${documentId}/pages/${number}/structure`;
  return (
    <section className="page-structure" aria-label="本页表格与公式">
      <h4>本页表格与公式</h4>
      <button
        disabled={pending}
        onClick={async () => {
          setPending(true);
          setError("");
          try {
            setResult(await api<Structure>(base));
          } catch (e) {
            setError(e instanceof Error ? e.message : "提取失败");
          } finally {
            setPending(false);
          }
        }}
      >
        {pending
          ? "正在提取…"
          : result
            ? "重新查看本页结构"
            : "提取本页表格与编号公式"}
      </button>
      {error && <p role="status">{error}</p>}
      {result && (
        <>
          <p>{result.note}</p>
          {!result.items.length && <p>本页未检出可展示项，请查看原页。</p>}
          {result.items.map((item) => (
            <details key={item.id} className="structure-item" open>
              <summary>
                {item.kind === "table" ? "表格" : "公式"} · {item.label}
              </summary>
              {item.caption && <p>{item.caption}</p>}
              <img
                src={`${base}/${item.id}/image`}
                alt={`第 ${number} 页 ${item.label} 原图`}
                loading="lazy"
              />
              <p>{item.warning}</p>
              {item.rows && (
                <>
                  <div className="structure-table-scroll">
                    <table>
                      <tbody>
                        {item.rows.map((row, i) => (
                          <tr key={i}>
                            {row.map((cell, j) => (
                              <td key={j}>
                                {cell === null ? (
                                  <span title="原布局合并单元格">—</span>
                                ) : (
                                  cell
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <a href={`${base}/${item.id}/csv`} download>
                    导出表格 CSV
                  </a>
                </>
              )}
              <details>
                <summary>查看文本层转写与坐标</summary>
                <pre>{item.text}</pre>
                <small>
                  归一化坐标（左、上、右、下）：{item.bbox.join(", ")}
                </small>
              </details>
              <a
                href={`/api/documents/${documentId}/pdf#page=${number}`}
                target="_blank"
                rel="noreferrer"
              >
                核对原 PDF · 第 {number} 页
              </a>
            </details>
          ))}
        </>
      )}
    </section>
  );
}
