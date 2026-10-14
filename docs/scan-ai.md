# Scan AI Guide

This guide covers the PDF preview, the page data on files, the nightly AI categorization, the Scanned Document AI Verifier and the AI history for scans.

## Requirements

- An OpenRouter API key in Document Extraction Settings.
- The Python packages `opencv-python-headless`, `numpy` and `pypdfium2`. They are in `pyproject.toml` and install with the app.
- Document Scanner Layouts with sections for the target DocType. AI categorization needs them.

## Scanned document PDF preview

The PDF button on a scanned document opens a large dialog in the same page. The dialog shows the PDF. Use the Download button in the dialog to save the file. The file name is `Document_Scan_<name>.pdf`.

## Page data on files

A vision model reads each file once and stores the page data as JSON on the File, in the AI Data section (AI Page Data). The data has the form `{"pages": [...]}`. The categorization and the verifier use this stored data. They do not send page images again.

- An image file has 1 page. The app renders each page of a PDF file as an image. It reads at most 20 pages of one PDF. The data notes the other pages in `omitted_pages`, and AI Page Count shows all pages of the file.
- One vision call reads one page. The page data has the document fields, parties, references, items, the printed item table (all columns and rows), taxes, totals, other labelled fields, handwritten notes, signatures and stamps, and the full page text.
- The values are copied as printed. Missing values are null.
- When another File with the same content already has data, the app copies that data and makes no vision call.
- A new or replaced attachment is a different File, so the app reads it again.
- When a file cannot be read, or the response is cut off, the error is saved in AI Page Error and no page data is saved. The hourly job tries a failed file again 6 hours after the last failure, up to 3 failed attempts in total. A successful read resets the count. Read pages now and the verifier try again at once, also after 3 attempts.
- The response can use up to 16,000 output tokens, or more when Max Tokens in Document Extraction Settings is higher.
- AI Page Model shows the model that read the file.

### Which files belong to a document

| Document | Files |
|---|---|
| Scanned Document | The attachment file of each active Scanned Document Detail row, in page order. Each page gets the section title of its row. |
| Any other document | The image and PDF files attached to the document, also through Attach fields. The order is the attach field, then the upload time. |

### Hourly page reading

The scheduler runs the job every hour. It reads only the files of scanned documents. It queues one job for each scan that has unread pages, oldest scan first, until the page limit is reached. The app reads the files that are attached directly to other documents only when the verifier needs them.

| Setting (Document Scanner Settings) | Default | Meaning |
|---|---|---|
| Enable Page Extraction | Off | Switch for the hourly job. |
| Page Extraction Batch Limit | 500 | Maximum pages to read each hour. |
| AI Process Since | Blank | Only scans created on or after this date. |

Read pages now (in the AI group of a Scanned Document) queues the page reading of that scan at once, also for pages with an error. It needs the same permission as Run AI check.

## Nightly AI categorization

### What it does

1. The job uses the stored page data. It makes no vision call. A scan with an unread page is skipped and stays without an AI status, so a later night picks it up. A skipped scan does not count against the batch limit.
2. The model set for the Scan Decision task (see AI providers) decides the layout and the section of each page. The app sends a JSON schema that lists only the allowed layouts and sections, and rejects an answer that skips a page or gives a confidence outside 0 to 1. Page text is sent as data, not as instructions.
3. OpenCV fixes the rotation (from the stored page data) and the tilt of each page.
4. The app applies the result only when the layout confidence and every page confidence are at or above the threshold. Otherwise the scan gets the status Needs Review.

The app keeps the original page images. A corrected page is saved as a new file.

### When it runs

The scheduler runs the job every day at 03:00. The job queues the scans that are not reviewed and have no AI status, or are stuck in Queued. It takes the oldest scans first.

### How to switch it on

1. Set up an AI provider and a model for the Scan Decision task (see AI providers).
2. Open Document Scanner Settings, go to the AI Categorization section and check Enable AI Categorization.

| Setting | Default | Meaning |
|---|---|---|
| Enable AI Categorization | Off | Switch for the nightly job and for Run AI check. |
| AI Confidence Threshold | 0.8 | Minimum layout confidence (0 to 1) to apply the result. |
| AI Batch Limit | 200 | Maximum scans for each night. |
| AI Process Since | Blank | Only scans created on or after this date. Blank includes all unchecked scans. |
| AI Max Pages | 30 | Scans with more pages are not processed and get Needs Review. |

### Buttons on a Scanned Document

The buttons are in the AI group. Only a System Manager, or a user who can write to the linked document, sees Run AI check and Mark reviewed.

| Button | Action |
|---|---|
| Read pages now | Queues the page reading of the scan now. |
| Run AI check | Queues the scan now. It needs Enable AI Categorization and stored data for all pages. |
| Mark reviewed | Sets the status to Done and stops all later AI checks of this scan. |
| AI History | Opens the AI call logs of this scan. |

### Statuses

| Status | Meaning |
|---|---|
| Queued | The scan waits for a worker. A scan that stays Queued for 6 hours can be queued again. |
| Done | The result was applied, or a user marked the scan as reviewed. |
| Needs Review | The app did not apply the result. For example, the confidence is below the threshold or the scan has too many pages. A user must check the scan. |
| Failed | An error stopped the check. The error is in the AI Error field. |

## Scanned Document AI Verifier

Use the verifier to compare the scans or the attached files of many documents with the document data and get the result in an Excel file. For each document the verifier uses the pages of its Scanned Documents. When the document has no Scanned Document, it uses the image and PDF files attached to the document. The verifier sends the stored page data, not the page images. It reads only the files that have no stored data yet. The model takes scan values only from the page data and gives null when a value is not there.

### How to use it

1. Create a Scanned Document AI Verifier.
2. Select the DocType (Reference DocType). Use a regular DocType. Single, child table and virtual DocTypes are not allowed.
3. Set the filters to choose the documents.
4. Write the prompt. Say what to check and what columns you want.
5. Set Max Documents (default 200). The run takes the oldest documents first.
6. Optional: set AI Account and Model to choose the model for this verifier. Set both or leave both blank.
7. Save, then click Run.

The form shows live progress (documents done, total and failed). The progress comes through realtime updates. When the run is Completed, click Download Excel.

The model chooses the report columns from your prompt. The Excel always starts with these columns: Document, Scanned Document(s), Status and Remarks.

Example prompt:

```
Compare the invoice total and the item quantities on the scan with the document.
Give columns for scan total, document total and the difference.
Put any mismatch in the remarks.
```

### Model choice

When AI Account and Model are set, the run uses that account and model first. The Verifier rows in AI Task Models are the fallbacks. When both are blank, the run uses the Verifier rows. The app selects no model for you. The system prompt is the Verifier System Prompt in Document Extraction Settings. The app adds the output rules to it.

### Read-only tools

For each document the model gets the document, its page data and four read-only tools. The model can look up related records before it answers, for example the linked order, receipt, supplier, item, stock entries or other invoices of the same party.

| Tool | What it returns |
|---|---|
| get_document | One document with its child tables. `_links` gives the DocType of each filled Link field, so the model can follow it. |
| get_list | Up to 50 rows of any readable DocType. Filters can use the fields of the DocType or of its child tables. |
| get_doctype_fields | The fields of a DocType or child table, with the Link target or child table in options. |
| get_document_pages | The stored page data of a document (at most 20 pages). Unread files are read first. |

The tools run as the user who clicked Run, with the normal Frappe permission checks and field-level read permissions. The tools cannot write, run SQL, call methods, run reports, download files or make HTTP calls. Each document allows at most 12 tool calls. Each tool result is cut at 20,000 characters. The model calls and tool calls of one document share the 150-second budget. The final answer must match a strict JSON schema with one value for each column and the remarks.

What is never sent to the model:

- Documents of credential, settings, session and log DocTypes: AI Provider Account, Document Extraction Settings, User, Email Account, Social Login Key, Connected App, Token Cache, Webhook, Integration Request, System Settings, Version, AWS and S3 settings, every DocType whose name starts with OAuth or WebAuthn or ends with Log, and every Single DocType.
- Documents of any DocType that has a Password field.
- Password fields, and fields whose name contains key, secret, token or password. The tools also refuse these fields in filters and in order_by.
- Provider credentials, the site encryption key and the database password. The app replaces these values with `<redacted>` in the prompt and in every tool result.

### Run statuses

| Status | Meaning |
|---|---|
| Draft | Not run yet. |
| Queued | Waiting for a worker. |
| Running | The run is in progress. You cannot edit the form. |
| Completed | The Excel file is ready. |
| Failed | The run stopped. See Error Log. |

A document row has its own status: Completed, Failed or No attachment. No attachment means that the document has no scanned pages and no attached image or PDF file. A failed document does not stop the run.

### Build Excel for a failed run

The app saves each finished row as it goes. When a run is Failed and the columns were already chosen, click Build Excel. The app makes a private Excel file from the saved rows of that run.

### Limits

- Each document uses at most 20 pages. The Remarks column shows how many pages were left out.
- The job timeout is 150 seconds for each document. The minimum is 10 minutes and the maximum is 6 hours.
- Each document allows at most 12 tool calls.

### Permissions

- A System Manager has full access.
- An Accounts Manager can create, run and see only the verifiers that this user owns.
- The user must be able to read the Reference DocType. The run reads only the documents and fields that this user can read. Page data is read only for documents that this user can read.
- The Excel file is private. Only a user who can see the verifier can download it.

## AI history

Every AI call is saved in the Document AI Call Log. Each model attempt, also a fallback model, gets its own log. The log keeps the model, status, latency, token counts, cost, the request (image data is removed) and the response.

| Phase | Used by |
|---|---|
| Page Extraction | Reading one page. The reference is the scan, or the document for a directly attached file. |
| Layout Decision | Choosing the layout of a scan (Scan Decision task). |
| Section Decision | Choosing the section of the pages (Scan Decision task). |
| Categorization Outcome | The final result of one scan check. |
| Verifier Columns | Choosing the verifier columns. |
| Verifier Agent Step | One model turn of one verified document. A tool-call turn keeps the tool results in the next request. |
| Verifier Row | One verified document. The saved row is also the checkpoint for Build Excel. |

All logs of one check or one verifier run have the same Run ID. Filter by Run ID to see one run.

Click AI History (in the AI group of a Scanned Document, or on the verifier form) to open the logs of that record.

Who can see the logs:

- A System Manager sees all logs.
- Other users see the logs of the verifiers that they own in the list.
- Other users can open one log of a Scanned Document when they can read the linked document. These logs do not show in their list, so AI History on a Scanned Document is for System Managers.
- Only a System Manager can change or delete a log.

## AI providers

Enable Document Extraction in Document Extraction Settings. A System Manager can create AI Provider Accounts for OpenRouter, OpenAI Codex or Anthropic. Set the model names in AI Task Models. No model is selected for you.

For OpenRouter, select API Key and enter the key. For OpenAI Codex, select ChatGPT Login and save the account. Click Connect ChatGPT account, open the shown URL and enter the code. The form updates when login is complete. You can also click Paste Codex auth.json and paste the contents of your local file. After a paste, the app and the Codex CLI share one login, and the first one that refreshes it logs the other out. Use Connect ChatGPT account when you can. The app stores the tokens with encryption and refreshes them before expiry.

For Anthropic, select API Key or Setup Token. Put the key or the setup token in API Key / Setup Token. A setup token starts with sk-ant-oat. Schema requests use native JSON output through output_config.format. This uses the same Messages API for both authentication types and needs no JSON output beta. It also avoids the forced tool restriction on some models. Select a model that supports structured output.

Add a row for each task, account and model in AI Task Models. Row order sets the priority for each task. The first enabled row with an enabled account is the primary model. Later rows are fallback models. The app logs each attempt. It retries temporary errors, then tries the next model. A disabled row or account is skipped.

When a task has no rows, the app uses the legacy Model and OpenRouter API Key fields. When a task has rows but none can run, the call fails. The upgrade copies an existing OpenRouter key to an OpenRouter account and keeps the legacy fields. Add task rows to use the new account.

### AI prompts

The background jobs take their prompts from the AI Prompts section of Document Extraction Settings. The models come from AI Task Models.

| Field | Used by |
|---|---|
| Page Extraction Prompt | Hourly page reading. The model reads each page into page data. |
| Scan Decision Prompt | Nightly AI categorization. The model selects the layout and the section of each page. |
| Verifier System Prompt | Scanned Document AI Verifier. |

When a field is blank, the app uses the built-in default prompt. The app always adds the output schema and the safety rules after your prompt, so a custom prompt cannot change the shape of the result. The app rejects a result that does not match the schema.

To edit a default, click Load default prompts. This fills only the blank fields with the built-in defaults, then you can change the text and save. It does not change a field that has text. Only a System Manager can use this button.
