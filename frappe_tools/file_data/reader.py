"""Read each File once and store its page data on the File."""

import json
import mimetypes
import time
from base64 import b64encode
from io import BytesIO

import frappe
import pypdfium2
from frappe.utils import add_to_date, cint, get_datetime, now_datetime
from rq.timeouts import JobTimeoutException

from frappe_tools.ai.client import DeadlineReached
from frappe_tools.extractors.pipeline import file_to_data_url
from frappe_tools.file_data.extraction import extract_pages
from frappe_tools.i2a.extract import _read_file

FILE = "File"
MAX_AUTO_ATTEMPTS = 3
RETRY_AFTER_HOURS = 6
PDF_RENDER_SCALE = 2
FILE_FIELDS = [
	"name", "file_name", "file_url", "content_hash", "ai_page_data", "ai_page_count", "ai_page_model",
	"ai_page_error", "ai_page_error_on", "ai_page_attempts",
]


def file_kind(file: dict) -> str | None:
	"""Return "image", "pdf" or None for a file that cannot be read."""
	mime = mimetypes.guess_type(file.file_name or file.file_url or "")[0] or ""
	if mime == "application/pdf":
		return "pdf"
	return "image" if mime.startswith("image/") else None


def read_file(
	file_name: str, retry_failed: bool = False, context: dict | None = None, deadline: float | None = None,
) -> dict | None:
	"""Return the stored {"pages": [...]} of a File, reading it first when a read is due.

	Holds no lock during the model call and commits after a read. Returns None when the File stays unread.
	Raises DeadlineReached when `deadline` (time.monotonic()) ends the read; the File stays unread.
	"""
	file = frappe.db.get_value(FILE, file_name, FILE_FIELDS, as_dict=True)
	if not file:
		raise frappe.DoesNotExistError(f"File {file_name} not found")
	if file.ai_page_data:
		return frappe.parse_json(file.ai_page_data)
	if not (retry_failed or is_retry_due(file)):
		return None

	stored = _save(file, _read_values(file, context or {}, deadline))
	return frappe.parse_json(stored) if stored else None


def retry_cutoff():
	return add_to_date(now_datetime(), hours=-RETRY_AFTER_HOURS)


def is_retry_due(file: dict) -> bool:
	"""A File with no error, or a failed File under the attempt cap whose last failure is old enough."""
	if not file.ai_page_error:
		return True
	return cint(file.ai_page_attempts) < MAX_AUTO_ATTEMPTS and (
		not file.ai_page_error_on or get_datetime(file.ai_page_error_on) <= retry_cutoff()
	)


def data_values(data: dict, model: str | None) -> dict:
	"""Return the File field values that store `data` and clear any earlier error."""
	return {
		"ai_page_data": json.dumps(data, ensure_ascii=False), "ai_page_count": len(data["pages"]),
		"ai_page_data_on": now_datetime(), "ai_page_model": model, "ai_page_error": None,
		"ai_page_error_on": None, "ai_page_attempts": 0,
	}


def _read_values(file, context, deadline):
	try:
		return _copied_values(file) or data_values(*_read_pages(file, context, deadline))
	except (JobTimeoutException, DeadlineReached):
		raise
	except Exception as exc:  # One unreadable file must not stop the other files.
		return {"ai_page_error": str(exc)[:1000], "ai_page_error_on": now_datetime(),
			"ai_page_attempts": cint(file.ai_page_attempts) + 1}


def _copied_values(file):
	"""Copy the data of another File with the same content, without a model call."""
	if not file.content_hash:
		return None
	source = frappe.db.get_value(FILE, {
		"name": ["!=", file.name], "content_hash": file.content_hash, "ai_page_data": ["is", "set"],
	}, ["ai_page_data", "ai_page_model"], as_dict=True)
	return data_values(frappe.parse_json(source.ai_page_data), source.ai_page_model) if source else None


def _read_pages(file, context, deadline):
	"""Read each page in its own model call; no call starts after `deadline`."""
	images = _page_images(file)
	pages, model = [], None
	for number, image in enumerate(images, 1):
		if deadline and time.monotonic() > deadline:
			raise DeadlineReached(f"The time limit was reached while reading {file.file_name or file.name}. Try again later.")
		[page], model = extract_pages([image], deadline=deadline, **context)
		pages.append({**page, "page_no": number})
	return {"pages": pages}, model


def _page_images(file):
	kind = file_kind(file)
	if kind == "image":
		return [file_to_data_url(file.file_url)]
	if kind != "pdf":
		raise ValueError("Only image and PDF files can be read")
	pdf = pypdfium2.PdfDocument(_read_file(file.file_url)[0])
	try:
		return [_pdf_page_image(page) for page in pdf]
	finally:
		pdf.close()


def _pdf_page_image(page):
	buffer = BytesIO()
	page.render(scale=PDF_RENDER_SCALE).to_pil().convert("RGB").save(buffer, "JPEG", quality=85)
	return "data:image/jpeg;base64," + b64encode(buffer.getvalue()).decode()


def _save(file, values):
	"""Write under a short File row lock when the File is unchanged and unread; return its stored page data.

	When another worker stored the data first, return that data.
	"""
	current = frappe.db.get_value(FILE, file.name, ["file_url", "content_hash", "ai_page_data"],
		as_dict=True, for_update=True)
	unchanged = bool(current) and (current.file_url, current.content_hash) == (file.file_url, file.content_hash)
	if unchanged and not current.ai_page_data:
		frappe.db.set_value(FILE, file.name, values, update_modified=False)
	frappe.db.commit()
	if not unchanged:
		return None
	return current.ai_page_data or values.get("ai_page_data")
