# Document Extraction — Architecture

Current adapter implementation and customization process:
[Adapter packages](adapter-packages.md). The design below is historical;
current review uses extraction staging and adapter-owned UI before the final writer.

Status: **design (approved decisions, pre-implementation)** · 2026-05-25

Scan a document → LLM extracts structured data → build the real (draft) target
document → **verify it side-by-side against the scan** → save. Works for **any**
DocType out of the box (general), with **thin, structured plugins** adding
document-specific behaviour (e.g. Purchase Invoice).

## 1. Principles
- **General-first.** A strong, doctype-agnostic engine handles every DocType with
  zero custom code. Plugins only override specifics. General and per-doctype get
  equal weight.
- **Plugins are structured packages, not single files.** Each doctype plugin is a
  package with one module per concern (schema / resolve / transform / taxes / config).
- **Composition over inheritance**, via four patterns: **Registry** (discover
  plugins), **Capability/Strategy** (swappable per-concern hooks), **Pipeline**
  (fixed stages, plugin-contributed handlers), **Context** (app-aware inputs).
- **Verify the real document, not a field list.** The review screen renders the
  actual Frappe form beside the scan; editing edits the real doc.
- **Save only, never submit** (submission stays a human action in the real form).

## 2. End-to-end flow (decided)
```
List view "Create from Scan"
  → scanner page (WebRTC) captures pages
  → extract_document(target_doctype, images)         # enqueue
  → [worker] pipeline:
        schema → LLM vision → parse → resolve         # fill matches/provenance
        → build DRAFT document (docstatus 0)          # the real Frappe doc
  → open Verify Split View on the draft:
        left  = scan + bbox overlay
        right = the live editable form of the draft
        bidirectional field ↔ region highlight
  → user verifies/edits → form save persists the doc  (submission optional, manual)
```
Decision: the draft is created right after extraction (so the right pane is the
real form). Resolution pre-fills links (supplier, items); the user can change them
in the real form.

## 3. Plugin framework  (`frappe_tools/extractors/`)
```
extractors/
  registry.py    # register(plugin), get_plugin(system, doctype), list_plugins()
  context.py     # ExtractionContext
  base.py        # ExtractionPlugin (capability interface)
  pipeline.py    # the engine: stage runner
  generic/
    plugin.py    # GenericPlugin — full pipeline for ANY doctype
  erpnext/
    purchase_invoice/
      plugin.py    # composes the modules below (thin)
      schema.py    # header fields + child tables/columns
      resolve.py   # supplier + item matching cascade
      transform.py # consolidation → common item
      taxes.py     # validated company/account tax materialization
      config.py    # rule-book / per-supplier settings
```

### 3.1 Registry & discovery
Plugins register against `(system, target_doctype)`. `get_plugin()` returns the
specific plugin or the `GenericPlugin`. Discovery via explicit imports (and later a
`hooks.py` entry so other apps can contribute plugins without touching core).

### 3.2 Context (`ExtractionContext`)
Passed to every capability. Carries: `target_doctype`, `installed_apps`, settings,
the resolved rule book(s), and helpers (`has_app("india_compliance")`, etc.). This
is how app-awareness stays clean and gated.

### 3.3 Capability interface (`ExtractionPlugin`) — all optional
```
schema(ctx)                       -> {header_fields:[...], tables:[{table, columns:[...]}]}
prompt_addendum(ctx)              -> str | None
resolve(ctx, extraction)          -> None      # fill matched values + candidates (in place)
transform(ctx, extraction, build) -> None      # reshape build rows (e.g. consolidate)
customize(ctx, doc, extraction)   -> None      # tweak the built doc pre-save (app-aware)
validate(ctx, extraction)         -> [issues]
provenance_map(extraction)        -> {fieldname: bbox, table: {row_idx: bbox}}  # for UI
```
A doctype plugin implements only what differs; everything else comes from `GenericPlugin`.

### 3.4 Pipeline (`pipeline.py`)
Fixed stages, each delegating to the active plugin (specific → generic):
`schema → call LLM → parse(header + N tables) → resolve → build draft (header + tables 1:1)
→ transform → customize → insert(save)`. The engine OWNS build; plugins contribute handlers.

### 3.5 GenericPlugin
Header scalar fields + every child table declared in the rule book, generic Link
resolution (candidate suggestions), build = header + rows 1:1. A new DocType works
with just a rule book.

### 3.6 DocType plugin package = thin composition
`plugin.py` wires capability modules; no monolith. Adding a system (Odoo/Tally) =
a new package; adding a doctype = a new package. Core untouched.

## 4. Data model (current, retained)
- `Document Rule Book` (+ `Document Rule Book Field`, `Document Rule Book Table`) — per
  DocType; declares instructions, field rules, **child tables to extract**, and PI
  post-processing config (`consolidate_lines`, `common_item`).
- `Document Extraction` (+ `Document Extraction Field` [header], `Document Extraction Line`
  [child rows, tagged by `table`], `Document Extraction Page`). Carries provenance
  (bbox/confidence/raw) per field and per line row, and `created_document`.
- `Document AI Call Log`, `Supplier Item Map` (learning store — deferred), `Item Embedding`.

## 5. Verification Split View (UI)
### 5.1 Layout
- **Left**: scanned page(s). Image + an SVG overlay
  (`viewBox="0 0 100 100" preserveAspectRatio="none"`), bbox rects scaled ×100,
  confidence-banded colours, zoom-on-select. (Pattern proven in essdee `lr_review.js`;
  here it becomes a small reusable component, not a 574-line monolith.)
- **Right**: the live document — `new frappe.ui.form.Form(target_doctype, container)`
  on the draft, toolbar hidden via CSS. Full fields/links/validation; the form's own
  save persists the doc.

### 5.2 Bidirectional highlight (grounded in research)
- **Region → field**: each SVG rect has `data-fieldname` (and `data-table`/`data-row`
  for child cells). Click → `frm.scroll_to_field(fieldname)` (Frappe-native scroll +
  highlight). Child rows → open that grid row.
- **Field → region**: hook `frm.fields_dict[fieldname].$input` focus/click → look up
  the field's bbox in the plugin's `provenance_map` → highlight + zoom the matching rect.
- The page receives `pages[]`, `provenance_map`, and `created_document` from the engine
  and wires the two panes. Doctype-agnostic.

### 5.3 Child tables
Row-level mapping first: a line's region ↔ its grid row (open/scroll). Cell-level
(per-column) highlight is a later refinement.

## 6. Purchase Invoice plugin (first concrete plugin)
- `schema.py`: header (supplier, bill_no, bill_date, posting_date, supplier_gstin),
  `items` table columns (description, supplier_code, hsn, qty, uom, rate, amount).
- `resolve.py`: supplier (GSTIN→exact→fuzzy) + item cascade (learned memory →
  supplier part-no → barcode → exact → HSN-fuzzy → fuzzy → semantic). Resolve-in-UI
  via the real form's Link fields + candidate hints.
- `transform.py`: if `consolidate_lines`, collapse all item rows into one `common_item`
  row (config default; overridable in review). General path keeps rows 1:1.
- `taxes.py`: see §7.
- `config.py`: reads rule-book / per-supplier consolidation + common item.

## 7. App-awareness — validated tax boundary
`taxes.py` always prevents ERPNext from silently applying header tax defaults. When
India Compliance is installed it also clears item tax templates. A tax row is added
only after the bounded decision is validated and explicitly applied to review staging:
the Account must be an enabled leaf owned by the selected Company, and the CGST/SGST/
IGST amount must equal printed evidence within INR 0.02. Invalid or stale accounting
choices abort draft creation. Nil/exempt/per-line tax treatment remains an explicit
future schema extension and cannot be inferred by this v1 adapter.

## 8. Migration from current code
- `adapters/` → `extractors/` (registry + base + context + pipeline + generic).
- `api/doc_extract.py` build logic → `pipeline.py`; endpoints stay thin wrappers.
- `api/doc_resolve.py` matching primitives → `erpnext/purchase_invoice/resolve.py`
  (+ shared helpers in the framework); review endpoints adjust to the form-based UI.
- `adapters/erpnext/purchase_invoice.py` → the `purchase_invoice/` package (split).
- `ExtractReviewPanel.vue` → the new Verify Split View (real form + overlay).
- The in-progress multi-table engine work folds into `pipeline.py` + `generic/`.

## 9. Phases
1. Plugin framework (registry/context/base/pipeline/GenericPlugin) — migrate current code.
2. PI plugin as a structured package.
3. Verify Split View (real form + scan overlay + two-way highlight) — replace ExtractReviewPanel.
4. Draft-creation flow + validated tax materialization + tests.
5. *(Deferred)* Learning-by-doing (Supplier Item Map auto-apply + embeddings).

## 10. Open / deferred
- Nil/exempt/per-line India Compliance tax-treatment schema and labeled fixtures.
- Learning-by-doing — deferred per decision.
## Reusable backend entry point

Code-owned processes should use the extraction service instead of duplicating
File, child-page, or queue logic:

```python
from frappe_tools.extractors.service import enqueue_document_paths

result = enqueue_document_paths(
    "Purchase Invoice",
    ["/absolute/local/path/invoice-page-1.jpg"],
)
```

The function creates the `Document Extraction`, copies every source into the
site's private local file store, attaches ordered pages, and enqueues exactly
one background job. It returns the extraction name and stable queue metadata.
For multi-step callers, use `create_extraction`, `stage_local_paths` (or
`stage_data_urls`), and `enqueue` separately.

Path ingestion is intentionally Python-only and rejects HTTP, HTTPS, and S3
URLs. Browser callers must upload private Files first, preventing a whitelisted
API from becoming an arbitrary server-file or production-object reader.
