# Frappe Tools

**Utilities App for Frappe Framework**

- **License:** MIT
- **Python:** >=3.10
- **Dependencies:** beautifulsoup4, openpyxl/xlrd, Pillow
- **Role:** Scanner User (fixture)

---

## Table of Contents

1. [Overview](#overview)
2. [Document Scanner Module](#document-scanner-module)
3. [Custom Data Builder Module](#custom-data-builder-module)
4. [Log File Downloader](#log-file-downloader)
5. [Reports](#reports)
6. [Hooks & Scheduled Tasks](#hooks--scheduled-tasks)
7. [API Reference](#api-reference)
8. [Frontend Architecture](#frontend-architecture)
9. [DocType Reference](#doctype-reference)
10. [Integration Points](#integration-points)

---

## Overview

Frappe Tools provides three core utilities for Frappe-based applications:

1. **Document Scanner** — WebRTC-based real-time document scanning from mobile devices with configurable layouts, drag-drop image management, and PDF export.
2. **Custom Data Builder** — Bulk email sender that sources data from Excel files or DocTypes, supports templated emails with PDF attachments, and tracks delivery status.
3. **Log File Downloader** — Admin utility to browse and download server log files as zip archives.

---

## Document Scanner Module

### How It Works

1. **Admin Setup**: Configure allowed DocTypes, scanner layouts, and STUN/TURN servers in **Document Scanner Settings**.
2. **Layout Creation**: Define **Document Scanner Layouts** with named sections and layout types (Front & Back Vertical/Horizontal, Series Vertical, Single Page).
3. **Scanning Workflow**:
   - User opens the scanner page from a supported DocType record via the "View Scanned Documents" button.
   - A QR code with a 4-digit PIN is displayed for mobile device pairing.
   - The mobile device scans the QR code and establishes a WebRTC peer connection using Redis-backed signaling.
   - Images captured on the mobile device are streamed to the web interface in real-time.
   - User organizes images into layout sections via drag-and-drop.
   - On save, images are uploaded (with optional S3 integration) and a **Scanned Document** record is created.
4. **Viewing & Export**: Saved scans can be viewed in a dialog, printed, or exported as PDF.

### DocTypes

| DocType | Type | Purpose |
|---------|------|---------|
| Document Scanner Settings | Single | Global config: allowed doctypes, STUN/TURN servers |
| Document Scanner Settings Items | Child Table | Per-DocType config: fields to show, filters, ordering |
| Document Scanner Server Setting | Child Table | STUN/TURN server credentials (url, username, password) |
| Document Scanner Layout | Submittable | Define layout sections for a target DocType |
| Document Scanner Layout Section | Child Table | Individual section with title and layout type |
| Document Layout Section Title | Document | Reusable section title lookup (quick entry enabled) |
| Scanned Document | Document | Container linking scanned pages to a DocType record |
| Scanned Document Detail | Child Table | Individual page: attachment, page number, layout type, page type (Front/Back), soft-delete flag |

### Layout Types

- **Front And Back Vertical** — Side-by-side front and back images
- **Front And Back Horizontal** — Top-bottom front and back images
- **Series Vertical** — Sequential series of images
- **Single Page** — One image per section

### WebRTC Signaling Flow

```
Mobile Device                    Redis                    Web Browser
     |                             |                          |
     |  ---- resolve_pin(pin) ---> |                          |
     |  <--- room ID ------------- |                          |
     |                             |                          |
     |  ---- send_signal(offer) -> |                          |
     |                             | --- get_signal(room) --> |
     |                             |                          |
     |  <-- send_signal(answer) -- |                          |
     | <--- get_signal(room) ----- |                          |
     |                             |                          |
     |  ======= WebRTC P2P Connected =======                  |
```

- PINs are stored in Redis with a 10-minute TTL.
- Signals use Redis blocking pop (`BLPOP`) with a 25-second timeout.
- Real-time events are dispatched via `frappe.publish_realtime`.

---

## Custom Data Builder Module

### How It Works

1. **Create a Data Builder**: Select data source — Excel file upload or a DocType with filters.
2. **Configure Email**: Set email account, recipient field, subject template, body template, CC/BCC, and optional global constants (key-value pairs).
3. **Attach PDFs** (optional): Choose a Print Format and Letter Head. Specify a naming field for the PDF filename.
4. **Preview**: Preview renders the first row of data into the email template with PDF attachment.
5. **Submit & Send**: On submission, emails are queued. Each row creates a **Data Builder Share Log** entry tracking status (Pending → Processing → Success/Failed).

### Templating

Templates use `{{variable_name}}` syntax. Variables are populated from:
- Row data (Excel columns or DocType fields)
- Global constants defined in the Custom Data Builder

### DocTypes

| DocType | Type | Purpose |
|---------|------|---------|
| Custom Data Builder | Submittable | Main document: data source, email config, attachment settings |
| Custom Data Builder Constant | Child Table | Global key-value pairs for template variables |
| Data Builder Share Log | Document | Per-row email tracking: status, error log, email queue link |

### Naming

- Custom Data Builder: `DB-.#####`
- Data Builder Share Log: `DB-SL-.#####`

### Email Sending Flow

```
Custom Data Builder (Submit)
    │
    ├── Load rows (Excel or DocType)
    │
    ├── For each row:
    │   ├── Create Data Builder Share Log (status: Pending)
    │   ├── Merge row data + global constants
    │   ├── Render subject & body templates
    │   ├── Generate PDF attachment (if enabled)
    │   ├── Convert inline images to base64
    │   └── Call frappe.sendmail()
    │
    └── Scheduled: poll_update_status_processing_data_share()
        └── Update log status from Email Queue (Sent/Error/Not Sent)
```

---

## Log File Downloader

A single-instance utility DocType that:
- Lists all log files in the `../logs/` directory
- Allows downloading individual log file groups as zip archives

Access: System Manager only.

---

## Reports

### Document Upload Status Report

- **Type:** Script Report
- **Reference:** Scanned Document
- **Filters:** Document type, date range
- **Output:** List view fields of the selected DocType with an `is_scanned` column indicating whether a Scanned Document exists for each record.

### Scanned Document Detail Report

- **Type:** Script Report
- **Reference:** Scanned Document
- **Filters:** Document type, date range
- **Roles:** Scanner User, System Manager
- **Output:** Page counts, layout info, creator, creation/modification timestamps for each scanned document detail.

---

## Hooks & Scheduled Tasks

### App Include JS

```
tools_plugin.bundle.js
```

### Fixtures

- `Scanner User` role

### Scheduled Tasks (Cron)

| Schedule | Method | Purpose |
|----------|--------|---------|
| Every 30 min | `custom_data_builder.poll_update_status_processing_data_share` | Sync email delivery status from Email Queue to Share Logs |
| Every 30 min | `custom_data_builder.delete_old_previews` | Delete preview files older than 15 minutes |
| Every hour | `scanned_document_detail.remove_old_deletable_documents` | Hard-delete soft-deleted Scanned Document Detail records |

### Doc Events

| DocType | Event | Handler |
|---------|-------|---------|
| `*` (all) | `on_rename` | Rename handler (updates references) |

### Ignore Links on Delete

- Scanned Document Detail

---

## API Reference

### Document Scanner API (`frappe_tools.api.doc_scanner`)

All signaling endpoints are `allow_guest=True` for mobile device access.

| Method | Auth | Description |
|--------|------|-------------|
| `register_pin(room)` | Guest | Generate 4-digit PIN for device pairing (Redis, 10-min TTL) |
| `resolve_pin(pin)` | Guest | Resolve PIN to session room ID |
| `get_signal(room, timeout=25)` | Guest | Blocking pop from Redis signal queue |
| `send_signal(room, signal_data, device)` | Guest | Push WebRTC signal to queue |
| `get_ice_servers()` | Guest | Return STUN/TURN servers from settings |
| `ping_to_device(device_type, event, room)` | Guest | Send real-time notification to device |
| `add_scanner(room)` | Guest | Notify web that mobile scanner connected |
| `remove_scanner(room)` | Guest | Notify web that mobile scanner disconnected |
| `get_docscanner_allowed_doctypes()` | Logged in | List DocTypes configured for scanning |
| `get_doctype_filtered_values(doctype, txt, ...)` | Logged in | Autocomplete search with custom filters |
| `get_document_details(doctype, docname)` | Logged in | Get configured field values for a document |
| `get_scanned_documents_list(doctype, docname)` | Logged in | List all scans for a document record |
| `load_scanned_document_details(docname)` | Logged in | Load all pages/images for a scan (S3-aware) |
| `upload_image(image_data)` | Logged in | Save base64 image as File (S3-aware, auto-optimized) |
| `make_or_update_main_doc(doctype, layout, docname, is_new, scan_name, documents)` | Logged in | Create/update Scanned Document with pages |
| `delete_scanned_docs(doc)` | Logged in | Delete Scanned Document |

### Custom Data Builder API (`frappe_tools.frappe_tools.doctype.custom_data_builder.custom_data_builder`)

| Method | Description |
|--------|-------------|
| `get_document_uploaded_values(doc_name, limit=10)` | Load Excel preview rows |
| `get_list_details(doctype, filters, limit=10)` | Fetch DocType records with filters |
| `get_preview_content(doc)` | Render email + PDF preview from first row |
| `send_email(doc)` | Trigger bulk email sending |
| `download_excel(name)` | Export data to Excel download |

### Scanned Document API (`frappe_tools.frappe_tools.doctype.scanned_document.scanned_document`)

| Method | Description |
|--------|-------------|
| `get_print_html(doc)` | Render printable HTML for scanned document |
| `get_scan_pdf(name)` | Generate and download PDF of scanned images |

### Log File Downloader API (`frappe_tools.frappe_tools.doctype.log_file_downloader.log_file_downloader`)

| Method | Description |
|--------|-------------|
| `get_logs_namspaces()` | List log files with counts |
| `download_log_zips(file_name)` | Download log files as zip |

---

## Frontend Architecture

### Bundle

The app ships a single JS bundle: `tools_plugin.bundle.js`

### Global Script (`public/js/global_script.js`)

On `app_ready`:
1. Fetches allowed DocTypes from `get_docscanner_allowed_doctypes()`
2. Dynamically registers a **"View Scanned Documents"** button on form views of allowed DocTypes
3. Button opens a dialog with the `DocumentListViewer` Vue component

### Vue Components

```
public/js/vue/Doc/
├── Pages/
│   ├── ImageScanner.vue          # Main scanner interface
│   └── DocumentListViewer.vue    # View saved scanned documents
├── Components/
│   ├── DocScannerController.vue  # Main scanner controls
│   ├── MainLayoutHandler.vue     # Route to layout-specific component
│   ├── QrCode.vue                # QR code for PIN display
│   ├── SessionIndicator.vue      # Connection status indicator
│   ├── UnsedImagesCarrosal.vue   # Unused images carousel
│   └── layouts/
│       ├── FrontBackVertical.vue
│       ├── FrontBackHorizontal.vue
│       ├── SeriesVertical.vue
│       └── SinglePage.vue
└── Store/
    ├── docscanner_store.js           # Session, PIN, device state (Pinia)
    └── doc_scanner_drag_drop_memory.js  # Drag-drop & image management (Pinia)
```

### Pinia Stores

**useSessionStore** (`docscanner_store.js`)
- Session UUID management
- PIN generation/resolution
- Device connection status tracking
- Setup mode toggle

**useGloabalDragMemory** (`doc_scanner_drag_drop_memory.js`)
- Drag-drop state tracking
- Image list management
- Section reordering
- Image-to-dataURL conversion with caching

### Pages

| Page | Route | Purpose |
|------|-------|---------|
| document-scanner | `/app/document-scanner` | Scanner interface (Vue SPA) |

### Workspace

| Workspace | Visibility | Role |
|-----------|-----------|------|
| Scanner | Public | Scanner User |

---

## DocType Reference

### Complete DocType List

| # | DocType | Type | Naming | Module |
|---|---------|------|--------|--------|
| 1 | Custom Data Builder | Submittable | DB-.##### | Frappe Tools |
| 2 | Custom Data Builder Constant | Child Table | — | Frappe Tools |
| 3 | Data Builder Share Log | Document | DB-SL-.##### | Frappe Tools |
| 4 | Scanned Document | Document | DOC_SCAN-.##### | Frappe Tools |
| 5 | Scanned Document Detail | Child Table | — | Frappe Tools |
| 6 | Document Scanner Layout | Submittable | layout_name | Frappe Tools |
| 7 | Document Scanner Layout Section | Child Table | — | Frappe Tools |
| 8 | Document Layout Section Title | Document | title | Frappe Tools |
| 9 | Document Scanner Settings | Single | — | Frappe Tools |
| 10 | Document Scanner Settings Items | Child Table | — | Frappe Tools |
| 11 | Document Scanner Server Setting | Child Table | — | Frappe Tools |
| 12 | Log File Downloader | Single | — | Frappe Tools |

### Permissions Matrix

| DocType | System Manager | Scanner User | All |
|---------|---------------|-------------|-----|
| Custom Data Builder | Full (CRUD + submit) | — | — |
| Data Builder Share Log | Full | — | Read |
| Scanned Document | Full | CRUD | — |
| Scanned Document Detail | Full | Read | — |
| Document Scanner Layout | Full | Read | — |
| Document Layout Section Title | Full | Read | — |
| Document Scanner Settings | Full | — | — |
| Log File Downloader | Full | — | — |

---

## Integration Points

### S3 Integration (`frappe_s3_integration`)

When installed, Frappe Tools automatically:
- Uploads scanned images to S3 instead of local filesystem
- Generates presigned URLs for viewing scanned documents
- Respects S3 auto-optimization settings for image compression

### Redis

Used for WebRTC signaling:
- PIN storage with TTL (`frappe_tools:pin:{pin}`)
- Signal queues with blocking pop (`frappe_tools:signal:{room}`)
- Connection configured from `site_config.json` → `redis_cache`

### Email Queue

Data Builder Share Logs link to Frappe's built-in Email Queue for delivery status tracking.

---

## File Structure

```
frappe_tools/
├── pyproject.toml
├── frappe_tools.md                          # This file
├── api/
│   └── doc_scanner.py                       # Scanner REST API
├── config/
│   └── __init__.py
├── fixtures/
│   └── role.json                            # Scanner User role
├── frappe_tools/
│   ├── hooks.py
│   ├── doctype/
│   │   ├── custom_data_builder/
│   │   ├── custom_data_builder_constant/
│   │   ├── data_builder_share_log/
│   │   ├── scanned_document/
│   │   ├── scanned_document_detail/
│   │   ├── document_scanner_layout/
│   │   ├── document_scanner_layout_section/
│   │   ├── document_layout_section_title/
│   │   ├── document_scanner_settings/
│   │   ├── document_scanner_settings_items/
│   │   ├── document_scanner_server_setting/
│   │   └── log_file_downloader/
│   ├── page/
│   │   └── document_scanner/
│   ├── report/
│   │   ├── document_upload_status_report/
│   │   └── scanned_document_detail_report/
│   └── workspace/
│       └── scanner/
├── public/
│   ├── js/
│   │   ├── global_script.js
│   │   ├── vue_plugins.js
│   │   └── vue/
│   │       └── Doc/
│   │           ├── Pages/
│   │           ├── Components/
│   │           └── Store/
│   └── dist/
├── templates/
│   └── pages/
└── utils/
    └── __init__.py                          # save_file_always_new helper
```
