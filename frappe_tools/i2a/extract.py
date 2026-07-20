"""Generic image/file handling + canonical normalization for the I2A engine.

Everything here is app-agnostic: no per-doctype and no per-app knowledge.
The canonical bbox form everywhere inside the engine (and in what callers
receive) is {"x", "y", "w", "h"} floats on 0..1 — consumer UIs (e.g. the
essdee LR review SVG overlay) render that directly.
"""

import base64
import math
import mimetypes
import re

import frappe
from frappe.utils import flt


def shrink_image_part(part, max_px=1024, quality=78, detail=None):
	"""Downscaled copy of an image content part for token-priced re-reads.

	Providers charge vision input by pixel tiles — OpenAI minis multiply image
	tokens ~30x, so a 2000px page costs more to VERIFY than to extract. The
	verify/SoM passes only need legible text, not print resolution. Fail-open:
	any decode problem returns the original part untouched.
	"""
	try:
		import io

		from PIL import Image

		url = part["image_url"]["url"]
		b64 = url.split(",", 1)[1]
		img = Image.open(io.BytesIO(base64.b64decode(b64)))
		w, h = img.size
		if max(w, h) <= max_px and detail is None:
			return part
		if max(w, h) > max_px:
			ratio = max_px / max(w, h)
			img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)
		if img.mode in ("RGBA", "P", "LA"):
			img = img.convert("RGB")
		buf = io.BytesIO()
		img.save(buf, format="JPEG", quality=quality)
		small = base64.b64encode(buf.getvalue()).decode()
		image_url = {"url": f"data:image/jpeg;base64,{small}"}
		if detail:
			# OpenAI-family models bill low-detail images at a small flat rate;
			# others ignore the key. Use for context images whose fine reading
			# happens elsewhere (crops).
			image_url["detail"] = detail
		return {"type": "image_url", "image_url": image_url}
	except Exception:
		return part


def file_to_image_part(file_ref):
	"""Turn a file reference into an OpenAI-style image content part.

	Accepts a Frappe file_url (public or private, S3-backed File docs
	included), or raw bytes, or a ready data: URL.
	"""
	if isinstance(file_ref, bytes):
		data, mime = file_ref, "image/jpeg"
	elif isinstance(file_ref, str) and file_ref.startswith("data:"):
		return {"type": "image_url", "image_url": {"url": file_ref}}
	else:
		data, mime = _read_file(file_ref)

	b64 = base64.b64encode(data).decode()
	return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _read_file(file_url):
	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	file_doc = frappe.get_doc("File", file_name) if file_name else None

	# S3-backed files (frappe_s3_integration rewrites file_url to a proxy URL
	# that core File.get_content() rejects, and the local copy may be gone).
	if file_doc is None and "/api/method/frappe_s3_integration" in (file_url or ""):
		from urllib.parse import parse_qs, urlparse

		file_id = parse_qs(urlparse(file_url).query).get("file_id", [None])[0]
		if file_id:
			file_doc = frappe.get_doc("File", file_id)

	if file_doc is not None and _is_s3_file(file_doc):
		from frappe_s3_integration.s3_core import getS3Connection

		s3_obj = getS3Connection().get_file_from_bucket(
			file_doc.custom_s3_key, file_doc.custom_s3_bucket_name
		)
		mime = mimetypes.guess_type(file_doc.file_name or "image.jpg")[0] or "image/jpeg"
		return s3_obj["Body"].read(), mime

	if file_doc is not None:
		mime = mimetypes.guess_type(file_doc.file_name or file_url)[0] or "image/jpeg"
		return file_doc.get_content(), mime

	# Fallback: direct site path (files not tracked by a File doc)
	path = frappe.get_site_path(file_url.lstrip("/"))
	mime = mimetypes.guess_type(path)[0] or "image/jpeg"
	with open(path, "rb") as f:
		return f.read(), mime


def _is_s3_file(file_doc):
	if "frappe_s3_integration" not in frappe.get_installed_apps():
		return False
	return bool(file_doc.get("custom_s3_key") and file_doc.get("custom_s3_bucket_name"))


# ---------------------------------------------------------------- bbox

def normalize_bbox(bbox):
	"""Canonicalize any accepted bbox form to {x, y, w, h} on 0..1, or None.

	Accepts {x,y,w,h} (0..1), or [ymin,xmin,ymax,xmax] on 0..1000 or 0..1
	(Gemini's native form). Auto-scales when any value > 1.5.
	"""
	if not bbox:
		return None

	if isinstance(bbox, dict):
		try:
			x, y, w, h = flt(bbox.get("x")), flt(bbox.get("y")), flt(bbox.get("w")), flt(bbox.get("h"))
		except Exception:
			return None
		if not all(math.isfinite(v) for v in (x, y, w, h)):  # NaN/inf sail past comparisons
			return None
		if w <= 0 or h <= 0:
			return None
		if max(x, y, w, h) > 1.5:  # dict form on a 0..1000 scale
			x, y, w, h = x / 1000, y / 1000, w / 1000, h / 1000
		return _clamp_bbox(x, y, w, h)

	if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
		try:
			ymin, xmin, ymax, xmax = [flt(v) for v in bbox]
		except Exception:
			return None
		if not all(math.isfinite(v) for v in (ymin, xmin, ymax, xmax)):
			return None
		if max(ymin, xmin, ymax, xmax) > 1.5:
			ymin, xmin, ymax, xmax = ymin / 1000, xmin / 1000, ymax / 1000, xmax / 1000
		w, h = xmax - xmin, ymax - ymin
		if w <= 0 or h <= 0:
			return None
		return _clamp_bbox(xmin, ymin, w, h)

	return None


def _clamp_bbox(x, y, w, h):
	x = min(max(x, 0), 1)
	y = min(max(y, 0), 1)
	w = min(max(w, 0), 1 - x)
	h = min(max(h, 0), 1 - y)
	if w <= 0 or h <= 0:
		return None
	return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def bbox_iou(a, b):
	if not a or not b:
		return 0.0
	ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
	bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
	ix = max(0.0, min(ax2, bx2) - max(a["x"], b["x"]))
	iy = max(0.0, min(ay2, by2) - max(a["y"], b["y"]))
	inter = ix * iy
	union = a["w"] * a["h"] + b["w"] * b["h"] - inter
	return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------- values

_MONTHS = {
	"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
	"jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _alnum(s):
	return re.sub(r"[^0-9A-Za-z]", "", str(s)).upper()


def normalize_value(value, fmt, raw_text=None):
	"""Apply a schema `format` to a raw value. Returns (normalized, ok).

	ok=False means the value failed the format — a deterministic deficiency.
	Formats: strip-spaces | digits:N | amount | date:indian-ddmmyyyy | regex:<pattern>

	Normalization is deterministic and belongs to the engine, not the model.
	For separator-preserving formats we normalize from the printed `raw_text`
	when it carries the same characters as `value` (ignoring separators): the
	model sometimes drops meaningful separators in its own `value`
	(e.g. printed "IND-57794" returned as value "IND57794") while raw_text
	keeps what's on the page. The alnum-equality guard makes this safe — if
	raw_text also holds a label or extra tokens, we fall back to `value`.
	"""
	if value is None or value == "":
		return value, True  # emptiness is judged by `required`, not by format

	text = str(value).strip()

	if not fmt:
		return text, True

	if fmt == "strip-spaces":
		src = text
		if raw_text and _alnum(raw_text) == _alnum(value):
			src = str(raw_text).strip()
		return re.sub(r"\s+", "", src), True

	if fmt.startswith("digits:"):
		digits = re.sub(r"\D", "", text)
		want = int(fmt.split(":", 1)[1])
		return digits, len(digits) == want

	if fmt == "amount":
		cleaned = re.sub(r"[^\d.\-]", "", text)
		try:
			val = round(float(cleaned), 2)
		except (ValueError, OverflowError):
			return text, False
		if not math.isfinite(val):  # 300-digit garbage parses to inf
			return text, False
		return val, True

	if fmt == "date:indian-ddmmyyyy":
		parsed = parse_indian_date(text)
		return (parsed, True) if parsed else (text, False)

	if fmt.startswith("regex:"):
		pattern = fmt.split(":", 1)[1]
		return text, bool(re.fullmatch(pattern, text))

	return text, True


def parse_indian_date(text):
	"""DD-MM-YYYY (any of -/. or space separators, 2- or 4-digit year,
	or textual month) → 'YYYY-MM-DD' or None. Day-first, always."""
	if not text:
		return None
	text = str(text).strip()

	m = re.match(r"^(\d{1,2})[\s\-/.]+(\d{1,2})[\s\-/.]+(\d{2,4})$", text)
	if m:
		day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
		if year < 100:
			year += 2000
		return _valid_date(year, month, day)

	m = re.match(r"^(\d{1,2})[\s\-/.]*([A-Za-z]{3,9})[\s\-/.,]*(\d{2,4})$", text)
	if m:
		month = _MONTHS.get(m.group(2)[:3].lower())
		if not month:
			return None
		day, year = int(m.group(1)), int(m.group(3))
		if year < 100:
			year += 2000
		return _valid_date(year, month, day)

	m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)  # already ISO
	if m:
		return _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

	return None


def _valid_date(year, month, day):
	import datetime

	try:
		return datetime.date(year, month, day).isoformat()
	except ValueError:
		return None
