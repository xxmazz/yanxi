import test from "node:test";
import assert from "node:assert/strict";
import { hasPdfHeader } from "../src/pdf-validation.ts";

test("accepts a PDF header at the start or within the first 1024 bytes", () => {
  assert.equal(hasPdfHeader(new TextEncoder().encode("%PDF-1.7")), true);
  const prefixed = new Uint8Array(1029);
  prefixed.set(new TextEncoder().encode("%PDF-1.4"), 1021);
  assert.equal(hasPdfHeader(prefixed), true);
});

test("rejects renamed non-PDF files and incomplete headers", () => {
  assert.equal(hasPdfHeader(new TextEncoder().encode("not a PDF")), false);
  assert.equal(hasPdfHeader(new TextEncoder().encode("%PDF")), false);
  const tooLate = new Uint8Array(1032);
  tooLate.set(new TextEncoder().encode("%PDF-1.7"), 1024);
  assert.equal(hasPdfHeader(tooLate), false);
});
