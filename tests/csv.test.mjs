import test from "node:test";
import assert from "node:assert/strict";
import { parseChartCsv } from "../src/csv.ts";

test("parses quoted names, commas and escaped quotes", () => {
  const data = parseChartCsv(
    '名称,得分\r\n"方法, A",-3\r\n"方法""B",0\r\n其他,12.5',
  );
  assert.deepEqual(data.rows, [
    { label: "方法, A", value: -3 },
    { label: '方法"B', value: 0 },
    { label: "其他", value: 12.5 },
  ]);
});
test("rejects malformed, missing, non-finite and oversized data", () => {
  for (const input of [
    "a,b",
    "a,b\nx,",
    "a,b\nx,Infinity",
    "a,b\nx,hello",
    "a,b\nx,2,3",
    'a,b\n"x,3',
    "a,b\n" + Array.from({ length: 31 }, (_, i) => "x," + i).join("\n"),
  ])
    assert.throws(() => parseChartCsv(input));
});
test("preserves zero and negative data, BOM and final newline", () => {
  assert.deepEqual(
    parseChartCsv("\uFEFF标签,值\nA,0\nB,-3\n").rows.map((x) => x.value),
    [0, -3],
  );
});

test("multi-series CSV retains each metric and rejects missing/duplicate headers", async () => {
  const { parseMultiChartCsv, radarError } = await import("../src/csv.ts");
  const data = parseMultiChartCsv(
    "指标,方法甲,方法乙\n准确率,82,85\n召回率,70,75\nF1,76,80",
  );
  assert.deepEqual(data.series, ["方法甲", "方法乙"]);
  assert.deepEqual(data.rows[1], { label: "召回率", values: [70, 75] });
  assert.equal(radarError(data), "");
  for (const text of [
    "指标,A,A\nx,1,2",
    "指标,A,B\nx,1,",
    "指标,A,B\nx,1",
    "指标,A,B\nx,1,NaN",
  ])
    assert.throws(() => parseMultiChartCsv(text));
  assert.match(
    radarError(parseMultiChartCsv("指标,A\nx,-1\ny,2\nz,3")),
    /负值/,
  );
  assert.match(radarError(parseMultiChartCsv("指标,A\nx,1")), /3–12/);
});
