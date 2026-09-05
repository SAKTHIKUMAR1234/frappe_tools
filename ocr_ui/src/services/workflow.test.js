import test from "node:test";
import assert from "node:assert/strict";
import {
  reviewSections,
  fieldEvidence,
  reviewIssueTarget,
} from "./workflow.js";
import { mergeDraft, hasUnsavedDrafts } from "./reviewDrafts.js";

test("a future adapter groups its own fields without a DocType switch", () => {
  const sections = reviewSections({
    fields: [{ fieldname: "destination" }, { fieldname: "required_extra" }],
    tables: [{ fieldname: "styles", label: "Style codes" }],
    workflow: {
      review_sections: [
        {
          key: "dispatch",
          label: "Dispatch",
          kind: "fields",
          fields: ["destination"],
        },
        { key: "styles", label: "Styles", kind: "table", table: "styles" },
      ],
    },
  });
  assert.deepEqual(
    sections.map((section) => section.label),
    ["Dispatch", "Styles", "Other details"],
  );
  assert.equal(sections[2].fields[0].fieldname, "required_extra");
});

test("older and incomplete manifests keep every fact accessible", () => {
  const run = {
    fields: [{ fieldname: "id" }],
    tables: [{ fieldname: "items", label: "Items" }],
  };
  assert.equal(reviewSections(run).length, 2);
  run.workflow = {
    review_sections: [{ key: "checks", kind: "fields", fields: ["id"] }],
  };
  assert.equal(reviewSections(run).length, 2);
});

test("a refresh retains the selected cell rather than changing to row evidence", () => {
  const bbox = { x: 0.1, y: 0.2, w: 0.2, h: 0.1, page: 2 };
  const run = {
    tables: [
      {
        fieldname: "items",
        rows: [{ row_no: 1, source_page: 1, cell_evidence: { qty: bbox } }],
      },
    ],
  };
  const selected = fieldEvidence(run, {
    table: "items",
    row_no: 1,
    column: "qty",
  });
  assert.equal(selected.source_page, 2);
  assert.equal(selected.column, "qty");
  assert.deepEqual(selected.bbox, bbox);
});

test("refresh preserves dirty values and accepts independently changed server fields", () => {
  const store = {};
  mergeDraft(store, "header", { supplier: "old", company: "A" });
  store.header.values.supplier = "reviewer edit";
  mergeDraft(store, "header", { supplier: "old", company: "B" });
  assert.deepEqual(store.header.values, {
    supplier: "reviewer edit",
    company: "B",
  });
  assert.equal(hasUnsavedDrafts(store), true);
  mergeDraft(store, "header", { supplier: "reviewer edit", company: "B" });
  assert.equal(hasUnsavedDrafts(store), false);
});

const issueSections = [
  { key: "parties", fields: [{ fieldname: "supplier" }], tables: [] },
  {
    key: "lines",
    fields: [],
    tables: [
      { fieldname: "items", rows: [{ row_no: 2 }], columns: [{ key: "qty" }] },
    ],
  },
];

test("a field check opens the adapter section and its input", () => {
  assert.deepEqual(
    reviewIssueTarget(issueSections, { path: "field:supplier" }),
    {
      section: "parties",
      inputId: "field-supplier",
    },
  );
});

test("a cell check targets the exact row and column", () => {
  assert.deepEqual(
    reviewIssueTarget(issueSections, { path: "table:items:2:qty" }),
    {
      section: "lines",
      inputId: "cell-items-2-qty",
    },
  );
});

test("row checks and stale cells open their section without focusing another input", () => {
  for (const path of [
    "table:items:2:review",
    "table:items:99:qty",
    "table:items",
  ]) {
    assert.deepEqual(reviewIssueTarget(issueSections, { path }), {
      section: "lines",
      inputId: null,
    });
  }
});

test("cross-field and unknown checks remain in the checks view", () => {
  for (const path of [
    "validation:total",
    "field:unknown",
    "table:unknown:1:qty",
    "",
  ]) {
    assert.equal(reviewIssueTarget(issueSections, { path }), null);
  }
});
