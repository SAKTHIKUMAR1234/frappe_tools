import assert from "node:assert/strict";
import { test } from "node:test";

import { refreshSavedDetails, storedPages } from "./scan_pages.js";

const row = (detail, page_no, attachment, page_type = "Front") => ({
  detail, page_no, page_type, attachment, title: "Invoice", layout_type: "Series Vertical",
});

test("attached pages at one position are all kept as separate pages", () => {
  const pages = storedPages([row("D1", 1, "a"), row("D2", 1, "b"), row("D3", 2, "c")], "Invoice", "Series Vertical");
  assert.deepEqual(pages.map((page) => [page.detail, page.page_no]), [["D1", 1], ["D2", 3], ["D3", 2]]);
});

test("an empty slot at a taken position and a repeated detail are dropped", () => {
  const rows = [row("D1", 1, "a"), row("D1", 1, "a"), row("E1", 1, null), row("E2", 2, null)];
  const pages = storedPages(rows, "Invoice", "Series Vertical");
  assert.deepEqual(pages.map((page) => [page.detail, page.page_no]), [["D1", 1], [null, 2]]);
});

test("front and back of one page are both kept", () => {
  const pages = storedPages([row("D1", 1, "a"), row("D2", 1, "b", "Back")], "Invoice", "Series Vertical");
  assert.deepEqual(pages.map((page) => page.page_no), [1, 1]);
});

test("only the sections rows are returned", () => {
  const other = { ...row("D9", 1, "z"), title: "Receipt" };
  assert.equal(storedPages([other], "Invoice", "Series Vertical").length, 0);
});

test("a save points saved images at new details and clears retired ones", () => {
  const kept = { attachment: "a", detail: "D1" };
  const scanned = { attachment: "new", detail: null };
  const tray = { attachment: "t", detail: "D_TRAY" };
  const saved = [
    { image: kept, attachment: "a", sent_detail: "D1", detail: "N1" },
    { image: scanned, attachment: "new", sent_detail: null, detail: "N2" },
  ];
  refreshSavedDetails(saved, [kept, scanned, tray]);
  assert.deepEqual([kept.detail, scanned.detail, tray.detail], ["N1", "N2", null]);
});

test("an image moved during the save follows its new detail", () => {
  const source = { attachment: null, detail: null };
  const target = { attachment: "a", detail: "D1" };
  const saved = [
    { image: source, attachment: "a", sent_detail: "D1", detail: "N1" },
    { image: target, attachment: null, sent_detail: null, detail: "N2" },
  ];
  refreshSavedDetails(saved, [source, target]);
  assert.deepEqual([source.detail, target.detail], [null, "N1"]);
});
