import assert from "node:assert/strict";
import test from "node:test";

import {
  formatBytes,
  isPdfFile,
  makePageId,
  materializePage,
} from "./images.js";

test("makePageId uses native randomUUID when available", () => {
  const id = makePageId({ randomUUID: () => "native-id" });
  assert.equal(id, "native-id");
});

test("makePageId produces a UUID when only getRandomValues is available", () => {
  const id = makePageId({
    getRandomValues(bytes) {
      bytes.fill(0xab);
      return bytes;
    },
  });
  assert.match(
    id,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
});

test("makePageId has a non-crypto compatibility fallback", () => {
  const id = makePageId(null);
  assert.match(id, /^page-[a-z0-9]+-[a-z0-9]+$/);
});

test("formatBytes never displays zero for a non-empty file", () => {
  assert.equal(formatBytes(1), "1 KB");
});

test("formatBytes rounds kilobytes", () => {
  assert.equal(formatBytes(1536), "2 KB");
});

test("formatBytes displays megabytes with one decimal", () => {
  assert.equal(formatBytes(1572864), "1.5 MB");
});

test("materializePage keeps the original file when no rotation is needed", async () => {
  const file = { name: "invoice.jpg" };
  assert.equal(await materializePage({ file, rotation: 0 }), file);
});

test("isPdfFile recognizes MIME type and filename fallback", () => {
  assert.equal(isPdfFile({ type: "application/pdf", name: "scan" }), true);
  assert.equal(isPdfFile({ type: "", name: "INVOICE.PDF" }), true);
  assert.equal(isPdfFile({ type: "image/png", name: "invoice.png" }), false);
});
