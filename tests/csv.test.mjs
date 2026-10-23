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
