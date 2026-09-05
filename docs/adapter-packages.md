# Document adapter process

An adapter owns a document's process: what to extract, instructions, reference
lookups, verification, human input, validation and the resulting document. Frappe
Tools supplies capture, stored review data, source viewing and the shared UI shell.

## 1. Create an adapter package

```text
my_app/document_adapters/purchase_invoice/
├── __init__.py          # exports the registered class
├── plugin.py           # process, schema, prompts, actions, setup and writer
├── review.py           # optional backend helpers
└── ui/
    ├── index.js         # UI entry point
    └── ReviewItems.vue  # optional custom components
```

The package's `__init__.py` imports its class from `plugin.py`. The class extends
`ExtractionPlugin` (or `GenericPlugin`) and uses the existing `@register` decorator.
Register the package in its owning app's `hooks.py`:

```python
doc_extraction_plugins = ["my_app.document_adapters.purchase_invoice"]
```

Keep this hook a literal list. Existing `package.plugin` registrations also work.
Only one adapter may own a `(system, target_doctype)` on a site. The registry uses
that site's installed-app hooks, including when a worker serves multiple sites.

## 2. Define the process in Python

The existing adapter methods remain the process contract:

| Method | Responsibility |
| --- | --- |
| `schema(ctx)` / `instructions(ctx)` | Extracted fields, tables and prompt instructions |
| `reference` / `decision_tools` / `validate_decision` | References, verification and suggestions |
| `workflow(ctx)` | Review sections, user-facing labels and final action |
| `review_actions(ctx)` | Explicit human actions supplied to custom UI |
| `link_queries(ctx, extraction)` | Link filters, search fields and record preview fields |
| `validator` / `writer` / `customize` | Validate reviewed data and prepare the business document |
| `setup(ctx)` / `scanner_layouts()` | Idempotent setup and default capture layouts on migration |

`setup(ctx)` runs after install/migrate for registered adapters whose target
DocType exists. Standard app DocTypes are installed by Frappe normally. Seed only
missing configuration; preserve site changes and role permissions. Schema and
workflow declarations are validated during setup. Custom DocTypes and permissions
belong in the owning app, using Frappe's normal facilities.

## 3. Supply the UI

The UI build reads registered package paths from bench apps' hooks and bundles
their `ui/index.js` entries. The site's API identifies its active adapter. No
DocType-specific switch or import needs to be added to the core frontend.

```js
import ReviewItems from "./ReviewItems.vue";

export default {
  sections: { items: ReviewItems },
};
```

Section keys correspond to `workflow(ctx).review_sections`. Unmodified sections
keep the shared renderer. For a handoff that is not an extracted field/table,
declare `{"key": "allocation", "label": "Choose destination", "custom": True}`
and provide `sections.allocation`. Unassigned extracted fields remain accessible.

For a completely custom review workspace, export `{ Review: MyReview }` instead.
It receives `run`, `saving`, `refining`, `processing`, `adapter` and `context` and
must emit the normal review events. It owns presentation, draft handling and
source viewing; the backend still validates every action and final creation.

Section components receive `run`, `section`, `saving`, `drafts` and `context`.
They emit `focus`, `save-field`, `save-row`, and `resolve-row` with the same
arguments as the shared components. Reuse the supplied `drafts` object when
embedding shared field/table components so unsaved changes survive tab switches.

Import shared components from `@frappe-tools/adapter-ui`: `FieldReview`,
`TableReview`, `DynamicFieldInput`, `RecordLink`, `Button`, `Dialog`, `Message`
and common Vue helpers. Plain JavaScript and Vue single-file components both
work. `TableReview` exposes a `row-actions` slot for handoff controls beside each
record match.

## 4. Configure useful Link controls

```python
def link_queries(self, ctx, extraction):
    return {
        "invoice": {
            "doctype": "Purchase Invoice",
            "fields": ["supplier_name", "bill_no", "bill_date", "grand_total"],
            "search_fields": ["name", "supplier_name", "bill_no"],
            "filters": {"docstatus": ["<", 2]},
        },
    }
```

```vue
<RecordLink v-model="invoice" :extraction="run.name"
  query="invoice" label="Purchase Invoice" :revision="run.modified" />
```

Search suggestions and the selected record preview display the adapter's chosen
fields. Link results enforce Frappe record and field permissions. Password and
child-table fields are not included. Exact previews retain the same filters as
searches. A shared schema field can opt in using `link_query`; a table resolver
can do the same.

## 5. Define human actions

```python
def review_actions(self, ctx):
    return {
        "create_item": {
            "label": "Create item and use it",
            "handler": create_item,
            "permissions": [("Item", "create")],
        },
    }

def create_item(ctx, extraction, values):
    # Validate input, use normal document.insert()/save(), then map the result.
    ...
```

Use `context.can("create_item")` to render an available action, then call
`await context.action("create_item", values)`. The shared host supplies the
extraction ID and modification timestamp, blocks calls during unsaved review
changes, runs the action and refreshes the review. The backend locks the review,
checks document/target permissions and declared action permissions, rejects stale
or processing documents, and records the action and reviewer. Handlers must use
normal Frappe permissions and keep writes in the current transaction; do not
commit or enqueue irreversible work in a handoff handler.

## 6. Install, migrate and build

Install the owning app on the intended site, then use its explicit site name:

```bash
bench --site <site-hostname> migrate
bench build --app frappe_tools
```

The `frappe_tools` package build script installs the locked UI dependencies and
builds all adapter components discovered from bench app hooks. Each site's
registry exposes only adapters from its installed apps. Compiled assets are
local build output; the website reads the Vite manifest, so app-specific UI is
not copied into the shared framework's source.

## Essdee adapters

Both packages live in Essdee and are registered as literal module paths:

```python
doc_extraction_plugins = [
    "essdee.essdee.doc_extraction.lr",
    "essdee.essdee.doc_extraction.erpnext.purchase_invoice",
]
```

Essdee's guarded install/migrate hook invokes setup only when `frappe_tools` is
installed on that site. Normal Essdee imports, migration and manual LR entry do
not require the optional runtime. LR extraction IDs are stored without a schema
Link dependency on a DocType owned by the optional app.

Purchase Invoice owns supplier/company/address previews, existing invoice lookup,
Item suggestions and a minimal Item-creation form. Its writer preserves the
reviewed Item code and name, posting date, line amounts and validated tax choices.
Item, Item Group and Company defaults provide expense accounts; missing defaults
produce a handoff. Other charges currently require an adapter charge mapping.

LR Entry owns receipt review and Sales Invoice matching with invoice/customer/date/
value previews. A unique invoice-number suffix or E-Way reference is required;
fuzzy candidates remain suggestions. Confirmation checks current permissions and
availability. Creation saves a ready LR entry. Its separate batch confirmation
workflow applies LR details to Sales Invoices.

## Durable review phases

`review_phases(ctx)` returns ordered phase definitions with `key`, `label`,
`sections`, `automation` and `human_input`. Optional `automate`, `validate` and
`complete` callbacks receive `(ctx, extraction)` and belong to the adapter.
Every workflow section must belong to a phase.

The shared UI calls `advance_phase(extraction, phase, operation, modified)`.
Only the active phase can advance. The server locks the extraction, enforces
write permission and its modification timestamp, validates source evidence and
saved inputs, and stores a protected checkpoint with reviewer/time information.
Changing inputs invalidates the affected phase and its successors. Earlier
completed phases survive, and refreshing suggestions does not repeat extraction.
Direct target creation also enforces phase completion.

Purchase Invoice has four phases: supplier/company, invoice details, items,
taxes/totals. LR has two: receipt facts and invoice references. Human confirmation
passes the normal adapter validation and binds the decision to the current review;
it does not require another model call. Both writers remain idempotent and create
unsubmitted staging/draft documents.

Mark schema fields whose values come from local configuration with
`input_source: "local"`. They use normal Link validation and permissions without
requiring a source-image highlight for a value not printed in the source.

## Validation

See [the testing guide](lr-purchase-invoice-v1-test-guide.md) for setup, review
steps and automated tests. The transaction tests create real local draft/entry
records and roll back their fixtures. They use prepared review data, so they
verify handoff and creation independently of live model availability.
