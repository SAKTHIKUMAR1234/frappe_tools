# Frappe Capture

Standalone Vue 3 + PrimeVue document-intelligence workspace served by Frappe at `/ocr`.

## Product flow

1. Choose any create-permitted DocType with an enabled Document Rule Book.
2. Add private JPG, PNG, or WebP pages from files, drag-and-drop, or the browser camera.
3. Reorder, rotate, remove, and preview pages before processing.
4. Run one vision extraction; Frappe performs schema, Link, required-field, arithmetic, and plugin validation.
5. Review dynamic fields and child tables beside their exact page/bounding-box evidence.
6. Create one idempotent draft. Submission remains a normal Frappe action.

The SPA polls persisted extraction state, so it has no Socket.IO dependency. The existing extraction plugin registry and Rule Books remain the configuration boundary; no DocType-specific Vue code is required.

## Development

```bash
yarn install
yarn test
yarn format:check
yarn build
```

`yarn build` writes versioned assets to `frappe_tools/public/ocr_ui` and copies the Frappe/Jinja entry page to `frappe_tools/www/ocr.html`.

## Runtime boundary

- Frappe permissions protect the app, extraction records, target DocTypes, Link searches, and private Files.
- Uploaded pages must belong to the extraction, be private, decode as an accepted image, and stay within the page/size limits.
- The model never creates or submits ERP documents directly. Deterministic validation and the user's review gate control draft creation.
- Draft creation is row-locked and idempotent across retries.
