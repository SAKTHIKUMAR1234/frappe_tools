"""Visual grounding for the I2A engine: OCR word boxes, value→box matching,
Set-of-Marks candidate rendering, and crop utilities.

Why this exists (flow-tuning research 2026-07-04): free-form bounding-box
emission is the weakest VLM capability on documents by an order of magnitude
(GPT-4o 66% value accuracy vs 2.4% region IoU). The fix is to stop asking
models for coordinates and let them SELECT among OCR-derived candidates
(Gemini 2.5 Flash 0 → 66-75% IoU with anchoring; Set-of-Marks 25.7 → 86.4),
and to verify any claimed box by cropping it back (models offered a null
option still hallucinate boxes at a 0.0% abstention rate).

Everything degrades gracefully and generically: no `tesseract` binary on the
host → ocr_word_boxes() returns None and the engine falls back to the old
free-form repair. Pillow is a hard Frappe dependency, so image ops are
always available. No per-app knowledge lives here.
"""

import base64
import io
import re
import shutil
import subprocess
from difflib import SequenceMatcher

from frappe_tools.i2a import extract

LOAD_MAX_DIM = 3000     # px — working-image ceiling (a 6600x9350 scan decodes to ~185MB RGB;
                        # capped here it stays ~27MB while OCR/marks/crops keep ample detail)
OCR_MAX_DIM = 2200      # px — OCR input downscale ceiling (speed; coords are relative)
OCR_TIMEOUT_S = 45
OCR_MIN_CONF = 30       # tesseract word confidence floor
OCR_PSM = 11            # sparse text — LRs/forms are scattered boxes, not paragraphs

MAX_WINDOW = 6          # max adjacent words joined when matching a value
MATCH_FLOOR = 0.72      # min similarity for a candidate cluster
SURE_MATCH = 0.92       # deterministic accept: top candidate at/above this…
RUNNER_UP_GAP = 0.85    # …AND every other candidate below this
MAX_MARKS = 8           # candidate boxes offered per field in Set-of-Marks

SOM_MAX_DIM = 1400      # marked image max dimension (px)
CROP_PAD = 0.35         # crop padding relative to the box size
CROP_MIN_PX = 140       # crops upscaled until the short side reaches this
CROP_MAX_DIM = 1200     # crops downscaled past this — a near-full-page claimed
                        # box must not ship a full-resolution PNG to the provider


# ------------------------------------------------------------------ image io

def load_image(file_ref):
	"""File reference (bytes / data: URL / Frappe file_url incl. S3) → RGB PIL image."""
	from PIL import Image

	if isinstance(file_ref, bytes):
		data = file_ref
	elif isinstance(file_ref, str) and file_ref.startswith("data:"):
		data = base64.b64decode(file_ref.split(",", 1)[1])
	else:
		data, _mime = extract._read_file(file_ref)
	img = Image.open(io.BytesIO(data)).convert("RGB")
	if max(img.size) > LOAD_MAX_DIM:  # bound resident memory for the whole run
		s = LOAD_MAX_DIM / max(img.size)
		img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))))
	return img


def pil_to_part(img):
	"""PIL image → OpenAI-style image content part (PNG data URL)."""
	buf = io.BytesIO()
	img.save(buf, format="PNG")
	b64 = base64.b64encode(buf.getvalue()).decode()
	return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}


# ------------------------------------------------------------------ OCR

def ocr_word_boxes(pil_image):
	"""Run local tesseract → [{"text", "bbox" {x,y,w,h} 0..1, "line"}], or None
	when tesseract is missing/failed (caller falls back to free-form repair).
	An empty list is a valid result (blank page)."""
	if not shutil.which("tesseract"):
		return None

	img = pil_image
	if max(img.size) > OCR_MAX_DIM:
		s = OCR_MAX_DIM / max(img.size)
		img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))))

	buf = io.BytesIO()
	img.save(buf, format="PNG")
	try:
		proc = subprocess.run(
			["tesseract", "stdin", "stdout", "--psm", str(OCR_PSM), "tsv"],
			input=buf.getvalue(), capture_output=True, timeout=OCR_TIMEOUT_S,
		)
	except Exception:
		return None
	if proc.returncode != 0:
		return None

	words = []
	W, H = img.size
	for line in proc.stdout.decode("utf-8", "replace").splitlines()[1:]:
		cols = line.split("\t")
		if len(cols) < 12 or cols[0] != "5":  # level 5 = word
			continue
		text = cols[11].strip()
		try:
			conf = float(cols[10])
		except ValueError:
			conf = -1.0
		if not text or conf < OCR_MIN_CONF:
			continue
		try:
			left, top, w, h = int(cols[6]), int(cols[7]), int(cols[8]), int(cols[9])
		except ValueError:
			continue
		if w <= 0 or h <= 0:
			continue
		words.append({
			"text": text,
			"bbox": {"x": round(left / W, 4), "y": round(top / H, 4),
				"w": round(w / W, 4), "h": round(h / H, 4)},
			"line": (cols[2], cols[3], cols[4]),  # block/par/line — reading order group
		})
	return words


# ------------------------------------------------------------------ matching

def _norm(s):
	return re.sub(r"[^a-z0-9]", "", str(s).lower())


def match_value(targets, words):
	"""Find where a value's text sits among OCR words.

	targets: printed-form variants of the value (raw_text first). Returns up
	to MAX_MARKS candidate clusters [{"bbox", "text", "score"}] sorted by
	score desc, overlap-suppressed. Short targets (<4 normalized chars) must
	match exactly — fuzzy noise on "554"-class values is worse than none.
	"""
	norms = []
	for t in targets or []:
		n = _norm(t)
		if n and n not in [x for _, x in norms]:
			norms.append((str(t), n))
	if not norms or not words:
		return []
	max_len = max(len(n) for _, n in norms)

	lines = {}
	for w in words:
		lines.setdefault(w["line"], []).append(w)

	clusters = []
	for ws in lines.values():
		ws = sorted(ws, key=lambda w: w["bbox"]["x"])
		for i in range(len(ws)):
			joined = ""
			for j in range(i, min(i + MAX_WINDOW, len(ws))):
				joined += _norm(ws[j]["text"])
				if not joined:
					continue
				if len(joined) > max_len * 1.4 + 2:
					break
				score = 0.0
				for _t, tn in norms:
					if joined == tn:
						score = 1.0
						break
					if len(tn) >= 4:
						score = max(score, SequenceMatcher(None, joined, tn).ratio())
				if score >= MATCH_FLOOR:
					clusters.append({
						"bbox": union_bbox([w["bbox"] for w in ws[i:j + 1]]),
						"text": " ".join(w["text"] for w in ws[i:j + 1]),
						"score": round(score, 4),
					})

	clusters.sort(key=lambda c: -c["score"])
	kept = []
	for c in clusters:
		if c["bbox"] and all(extract.bbox_iou(c["bbox"], k["bbox"]) < 0.5 for k in kept):
			kept.append(c)
		if len(kept) >= MAX_MARKS:
			break
	return kept


def deterministic_pick(clusters):
	"""One clearly-best candidate → no LLM needed. Top must clear SURE_MATCH
	and no strong runner-up may sit at a DIFFERENT location (two exact hits
	of the same text elsewhere = ambiguous, a model must look). Runners that
	overlap the top box are just label+value superstring windows of the same
	spot — they don't count as rivals."""
	if not clusters:
		return None
	top = clusters[0]
	if top["score"] < SURE_MATCH:
		return None
	for c in clusters[1:]:
		if c["score"] >= RUNNER_UP_GAP and not _same_spot(top["bbox"], c["bbox"]):
			return None
	return top


def _same_spot(a, b):
	"""Overlap fraction of the smaller box inside the intersection ≥ 0.8 —
	the two candidates point at the same printed text."""
	if not a or not b:
		return False
	ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
	iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
	inter = ix * iy
	small = min(a["w"] * a["h"], b["w"] * b["h"])
	return small > 0 and inter / small >= 0.8


def union_bbox(boxes):
	boxes = [b for b in boxes if b]
	if not boxes:
		return None
	x0 = min(b["x"] for b in boxes)
	y0 = min(b["y"] for b in boxes)
	x1 = max(b["x"] + b["w"] for b in boxes)
	y1 = max(b["y"] + b["h"] for b in boxes)
	return extract._clamp_bbox(x0, y0, x1 - x0, y1 - y0)


# ------------------------------------------------------------------ rendering

def draw_marks(pil_image, clusters):
	"""Set-of-Marks: numbered red boxes drawn on a (downscaled) copy of the
	document. Returns an image content part."""
	from PIL import ImageDraw, ImageFont

	img = pil_image
	if max(img.size) > SOM_MAX_DIM:
		s = SOM_MAX_DIM / max(img.size)
		img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))))
	else:
		img = img.copy()

	draw = ImageDraw.Draw(img)
	W, H = img.size
	lw = max(2, W // 500)
	try:
		font = ImageFont.load_default(size=max(14, W // 70))
	except TypeError:  # older Pillow: no size kwarg
		font = ImageFont.load_default()
	th = getattr(font, "size", 14)

	for n, c in enumerate(clusters, 1):
		b = c["bbox"]
		x0, y0 = b["x"] * W, b["y"] * H
		x1, y1 = (b["x"] + b["w"]) * W, (b["y"] + b["h"]) * H
		draw.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=lw)
		label = str(n)
		tw = draw.textlength(label, font=font)
		ly = y0 - th - 6
		if ly < 0:
			ly = y1 + 2
		draw.rectangle([x0, ly, x0 + tw + 10, ly + th + 6], fill=(255, 0, 0))
		draw.text((x0 + 5, ly + 3), label, fill=(255, 255, 255), font=font)

	return pil_to_part(img)


def crop_part(pil_image, bbox):
	"""Crop a claimed box (with padding, upscaled if tiny) → image part.
	Used for crop-back checks and for per-claim verify grounding."""
	W, H = pil_image.size
	px, py = bbox["x"] * W, bbox["y"] * H
	pw, ph = bbox["w"] * W, bbox["h"] * H
	pad_x = max(pw * CROP_PAD, 10)
	pad_y = max(ph * CROP_PAD, 10)
	x0, y0 = max(0, int(px - pad_x)), max(0, int(py - pad_y))
	x1, y1 = min(W, int(px + pw + pad_x)), min(H, int(py + ph + pad_y))
	if x1 <= x0 or y1 <= y0:
		raise ValueError("degenerate crop")
	crop = pil_image.crop((x0, y0, x1, y1))
	if min(crop.size) < CROP_MIN_PX:
		s = min(4.0, CROP_MIN_PX / max(1, min(crop.size)))
		crop = crop.resize((max(1, int(crop.width * s)), max(1, int(crop.height * s))))
	elif max(crop.size) > CROP_MAX_DIM:
		s = CROP_MAX_DIM / max(crop.size)
		crop = crop.resize((max(1, int(crop.width * s)), max(1, int(crop.height * s))))
	return pil_to_part(crop)
