export type ChartData = {
  title: string;
  label: string;
  rows: { label: string; value: number }[];
};

export type MultiChartData = {
  title: string;
  label: string;
  series: string[];
  rows: { label: string; values: number[] }[];
};

/** Parse quoted CSV locally; each numeric column is an independent series. */
export function parseMultiChartCsv(input: string): MultiChartData {
  if (input.length > 1024 * 1024) throw new Error("CSV 请控制在 1 MB 以内。");
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
  const width = rows[0].length;
  if (width < 2 || width > 7 || rows.some((r) => r.length !== width))
    throw new Error("CSV 第一列为类别，后续支持 1–6 列数值，每行列数须一致。");
  if (rows.length > 31) throw new Error("当前最多展示 30 行数据，请先筛选。");
  if (
    rows[0].some((cell) => !cell) ||
    new Set(rows[0].slice(1)).size !== width - 1
  )
    throw new Error("表头不能为空，数值列名称不能重复。");
  const data = rows.slice(1).map((r, i) => {
    const values = r.slice(1).map(Number);
    if (
      !r[0] ||
      r.slice(1).some((cell) => cell === "") ||
      values.some((v) => !Number.isFinite(v))
    )
      throw new Error("第 " + (i + 2) + " 行需要有效的类别和有限数值。");
    return { label: r[0], values };
  });
  return {
    title: rows[0][0],
    label: rows[0].slice(1).join(" / "),
    series: rows[0].slice(1),
    rows: data,
  };
}

/** Compatibility parser for consumers requiring exactly one numeric series. */
export function parseChartCsv(input: string): ChartData {
  const data = parseMultiChartCsv(input);
  if (data.series.length !== 1) throw new Error("此处需要两列 CSV。");
  return {
    title: data.title,
    label: data.label,
    rows: data.rows.map((row) => ({ label: row.label, value: row.values[0] })),
  };
}

export function radarError(data: MultiChartData): string {
  if (data.rows.length < 3 || data.rows.length > 12)
    return "雷达图需要 3–12 个指标行。";
  if (data.rows.some((row) => row.values.some((value) => value < 0)))
    return "雷达图不支持负值，请使用柱状图或折线图。";
  return "";
}
