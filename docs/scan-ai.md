# Scan AI Guide

This guide covers the PDF preview, the nightly AI categorization, the Scanned Document AI Verifier and the AI history for scans.

## Requirements

- An OpenRouter API key in Document Extraction Settings.
- The Python packages `opencv-python-headless` and `numpy`. They are in `pyproject.toml` and install with the app.
- Document Scanner Layouts with sections for the target DocType. AI categorization needs them.

## Scanned document PDF preview

The PDF button on a scanned document opens a large dialog in the same page. The dialog shows the PDF. Use the Download button in the dialog to save the file. The file name is `Document_Scan_<name>.pdf`.

## Nightly AI categorization

### What it does

1. A vision model reads each page and returns the page data as JSON.
2. Jev by TypeSafe (through OpenRouter) decides the layout and the page section of each page.
3. OpenCV fixes the rotation and the tilt of each page.
4. The app applies the result only when the layout confidence is at or above the threshold. Otherwise the scan gets the status Needs Review.

The app keeps the original page images. A corrected page is saved as a new file.

### When it runs

The scheduler runs the job every day at 03:00. The job queues the scans that are not reviewed and have no AI status, or are stuck in Queued. It takes the oldest scans first.

### How to switch it on

1. Open Document Extraction Settings and set the OpenRouter API key.
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
| Run AI check | Queues the scan now. It needs Enable AI Categorization. |
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

Use the verifier to compare the scans of many documents with the document data and get the result in an Excel file.

### How to use it

1. Create a Scanned Document AI Verifier.
2. Select the DocType (Reference DocType). Use a regular DocType. Single, child table and virtual DocTypes are not allowed.
3. Set the filters to choose the documents.
4. Write the prompt. Say what to check and what columns you want.
5. Set Max Documents (default 200). The run takes the oldest documents first.
6. Save, then click Run.

The form shows live progress (documents done, total and failed). The progress comes through realtime updates. When the run is Completed, click Download Excel.

The model chooses the report columns from your prompt. The Excel always starts with these columns: Document, Scanned Document(s), Status and Remarks.

Example prompt:

```
Compare the invoice total and the item quantities on the scan with the document.
Give columns for scan total, document total and the difference.
Put any mismatch in the remarks.
```

### Run statuses

| Status | Meaning |
|---|---|
| Draft | Not run yet. |
| Queued | Waiting for a worker. |
| Running | The run is in progress. You cannot edit the form. |
| Completed | The Excel file is ready. |
| Failed | The run stopped. See Error Log. |

A document row has its own status: Completed, Failed or No scan. A failed document does not stop the run.

### Build Excel for a failed run

The app saves each finished row as it goes. When a run is Failed and the columns were already chosen, click Build Excel. The app makes a private Excel file from the saved rows of that run.

### Limits

- Each document uses at most 20 scan pages. The Remarks column shows how many pages were left out.
- The job timeout is 150 seconds for each document. The minimum is 10 minutes and the maximum is 6 hours.

### Permissions

- A System Manager has full access.
- An Accounts Manager can create, run and see only the verifiers that this user owns.
- The user must be able to read the Reference DocType. The run reads only the documents and fields that this user can read.
- The Excel file is private. Only a user who can see the verifier can download it.

## AI history

Every AI call is saved in the Document AI Call Log. The log keeps the model, status, latency, token counts, cost, the request (image data is removed) and the response.

| Phase | Used by |
|---|---|
| Page Extraction | Reading one page. |
| Layout Decision | Choosing the layout of a scan. |
| Section Decision | Choosing the section of the pages. |
| Categorization Outcome | The final result of one scan check. |
| Verifier Columns | Choosing the verifier columns. |
| Verifier Row | One verified document. The saved row is also the checkpoint for Build Excel. |

All logs of one check or one verifier run have the same Run ID. Filter by Run ID to see one run.

Click AI History (in the AI group of a Scanned Document, or on the verifier form) to open the logs of that record.

Who can see the logs:

- A System Manager sees all logs.
- Other users see the logs of the verifiers that they own in the list.
- Other users can open one log of a Scanned Document when they can read the linked document. These logs do not show in their list, so AI History on a Scanned Document is for System Managers.
- Only a System Manager can change or delete a log.
