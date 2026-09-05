# LR Entry and Purchase Invoice testing

Both adapters are in Essdee. Install `frappe_tools` on the intended site to enable
automation; Essdee remains usable without it.

```bash
bench --site <site-hostname> migrate
bench build --app frappe_tools
```

Migration discovers the hook registrations and creates missing backend defaults
without overwriting existing model, layout or rule-book settings. Build discovers
the adapters' adjacent `ui/index.js` files. Model credentials and role assignments
remain site configuration. Normal background workers must be running for uploads.
Use the explicit site hostname; never configure a bench default site.

## Purchase Invoice

1. Open `/ocr`, select Purchase Invoice and upload its pages.
2. Confirm supplier, its address and your company against the printed identities.
3. Confirm bill number and dates. The invoice lookup can show an existing invoice
   for comparison; duplicate supplier bill identities block creation.
4. Map each row to an existing Item, or use Create item with its code, name, group,
   UOM and optional HSN. Verify quantities, rates and amounts.
5. Review printed taxes and totals, select the correct company tax accounts and
   confirm. Configured Item/Item Group/Company expense defaults are used. Missing
   defaults are reported for correction. Other charges need an adapter mapping.
6. Create the Purchase Invoice draft. Check actual Item code/name, dates, taxes
   and totals in Accounts before following the normal submission workflow.

## LR Entry

1. Select LR Entry and upload one receipt.
2. Confirm receipt number, date and the printed LR details.
3. Refresh invoice suggestions if a printed reference was corrected. Check the
   invoice's customer, date, E-Way Bill and value; confirm each match.
4. Save the reviewed LR entry. This action does not update Sales Invoices.
5. Open the entry to create a review batch, then use its separate confirmation
   workflow when ready to apply LR details.

Each phase records who confirmed it and when. Reloading resumes the active phase.
Changing an earlier input reopens that phase and later phases, while preserving
completed extraction and existing item mappings. Unsaved edits and stale browser
revisions cannot advance a phase.

## Automated validation

Run on a disposable local/test database using Frappe's normal test runner:

- `frappe_tools.api.test_adapter_ui`, `frappe_tools.api.test_ocr_agent`
- `frappe_tools.extractors.test_adapter_packages`, `test_workflow`, `test_v1_setup`
- `essdee.essdee.doc_extraction.test_phases`, `test_optional_integration`
- `essdee.essdee.doc_extraction.erpnext.purchase_invoice.test_plugin`,
  `test_review_ui`, `test_accounting_safety`
- `essdee.essdee.doc_extraction.lr.test_plugin`

The phase tests need configured local Company defaults, a Supplier and an available
submitted Sales Invoice. Fixtures are rolled back. They test prepared review data
through real document insertion, without a live model call or file upload.

```bash
yarn --cwd apps/frappe_tools/ocr_ui test
```

Browser checks live in `ocr_ui/e2e`. Use a disposable local fixture and an
explicit-hostname proxy. Never use production storage objects as writable fixtures.
