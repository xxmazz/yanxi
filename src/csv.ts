export type ChartData = {
  title: string;
  label: string;
  rows: { label: string; value: number }[];
};

/** Parse a two-column CSV including quoted fields, escaped quotes and CRLF. */
export function parseChartCsv(input: string): ChartData {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  const csv = input.replace(/^﻿/, "");
  for (let i = 0; i < csv.length; i++) {
    const char = csv[i];
    if (char === '"') {
      if (quoted && csv[i + 1] === '"') {
        field += '"';
        i++;
      } else quoted = !quoted;
    } else if (char === "," && !quoted) {
      row.push(field.trim());
      field = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && csv[i + 1] === "\n") i++;
      row.push(field.trim());
      if (row.some(Boolean)) rows.push(row);
      row = [];
      field = "";
    } else field += char;
  }
  if (quoted) throw new Error("CSV 的引号没有闭合。");
  row.push(field.trim());
  if (row.some(Boolean)) rows.push(row);
  if (rows.length < 2)
    throw new Error("请提供表头和至少一行数据，例如：模型,得分。");
  if (rows[0].length !== 2 || rows.some((r) => r.length !== 2))
    throw new Error("当前支持两列 CSV：第一列类别，第二列数值。");
  if (rows.length > 31) throw new Error("当前最多展示 30 行数据，请先筛选。");
  const data = rows.slice(1).map((r, i) => {
    const value = Number(r[1]);
    if (!r[0] || r[1] === "" || !Number.isFinite(value))
      throw new Error("第 " + (i + 2) + " 行需要有效的类别和有限数值。");
    return { label: r[0], value };
  });
  return { title: rows[0][0], label: rows[0][1], rows: data };
}
