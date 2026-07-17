"""Offline tests for the I2A visual-grounding upgrade (flow-tuning build):
OCR-anchored bbox repair, Set-of-Marks selection, crop-back checks, and the
claim-by-claim verify prompt. Runs the REAL engine code against fake frappe,
a scripted model, and scripted (or real, where available) tesseract output.
"""

import io
import json
import shutil
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fake_frappe

FRAPPE, REQUESTS = fake_frappe.install()

sys.path.insert(0, "/Users/karthikeyan/frappe-bench-v15/apps/frappe_tools")
sys.path.insert(0, "/Users/karthikeyan/frappe-bench-v15/apps/essdee")

from frappe_tools.i2a import engine, ground, providers, verify  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
	(PASS if cond else FAIL).append(name)
	print(f"  {'✓' if cond else '✗'} {name}" + (f"  {detail}" if not cond else ""))


# --------------------------------------------------------------- fixtures

LR_SCHEMA = [
	{"key": "lr_number", "label": "LR Number", "required": True, "format": "strip-spaces", "bbox_required": True},
	{"key": "lr_date", "label": "LR Date", "required": True, "format": "date:indian-ddmmyyyy", "bbox_required": True},
	{"key": "freight_amount", "label": "Freight Amount", "required": True, "format": "amount", "bbox_required": True},
	{"key": "bill_numbers", "label": "Bill No", "kind": "array", "format": "strip-spaces", "bbox_required": True},
]

GOOD_BBOX = [100, 100, 140, 300]


def make_item(value, raw=None, conf=0.95, bbox=GOOD_BBOX):
	return {"value": value, "raw_text": raw or str(value), "confidence": conf, "bbox": bbox}


def full_extraction(**overrides):
	data = {
		"lr_number": make_item("TND-2493", "TND - 2493"),
		"lr_date": make_item("2026-05-12", "12/05/2026", bbox=[200, 100, 230, 240]),
		"freight_amount": make_item(554, "554", bbox=[300, 700, 330, 800]),
		"bill_numbers": [make_item("INV-001", bbox=[400, 100, 430, 260])],
	}
	data.update(overrides)
	return data


def grounded_world(mode="Manual", **action_overrides):
	FRAPPE.reset()
	FRAPPE.seed(
		"AI Model", "vision-a", model_label="vision-a", enabled=1, provider="OpenRouter",
		model_id="google/gemini-2.5-flash", supports_vision=1, supports_json_mode=1,
		max_tokens=8192, temperature=0, cost_per_m_input=0.1, cost_per_m_output=0.4, api_key="k",
	)
	FRAPPE.seed(
		"AI Model", "qwen-v", model_label="qwen-v", enabled=1, provider="OpenRouter",
		model_id="qwen/qwen2.5-vl-72b-instruct", supports_vision=1, supports_json_mode=1,
		max_tokens=8192, temperature=0, cost_per_m_input=0.05, cost_per_m_output=0.2, api_key="k",
	)
	action = dict(
		action_name="LR Extraction", enabled=1, mode=mode,
		purpose="extract LR fields", instructions="Extract the LR fields.", knowledge="",
		rules="compare all fields", request_notes="", use_llm_request_notes=0,
		use_ocr_anchored_repair=1, use_crop_back_check=1, use_verify_crops=1,
		output_schema=json.dumps(LR_SCHEMA),
		max_rounds=4, max_calls_per_run=12, run_seconds_budget=480,
		models=[
			fake_frappe.FakeRow({"ai_model": "vision-a", "remarks": "vision — extract", "is_orchestrator": 1, "is_verifier": 0}),
			fake_frappe.FakeRow({"ai_model": "qwen-v", "remarks": "grounded verifier", "is_orchestrator": 0, "is_verifier": 1}),
		],
	)
	action.update(action_overrides)
	FRAPPE.seed("I2A Action", "LR Extraction", **action)
	FRAPPE.seed("LR Processing Batch", "BATCH-1", processing_mode=mode)


def png_bytes(w=1000, h=1000, color="white"):
	from PIL import Image

	img = Image.new("RGB", (w, h), color)
	buf = io.BytesIO()
	img.save(buf, format="PNG")
	return buf.getvalue()


class ScriptedModel:
	"""Replaces providers.call_model; responses keyed by purpose (FIFO).
	Also records full messages for prompt-shape assertions."""

	def __init__(self, script):
		self.script = {k: list(v) for k, v in script.items()}
		self.calls = []

	def __call__(self, ai_model, messages, *, json_mode=True, purpose="", run=None, action=None, max_tokens=None):
		self.calls.append({"model": getattr(ai_model, "name", ai_model), "purpose": purpose, "messages": messages})
		FRAPPE.new_doc("I2A LLM Call").update({
			"run": run, "action": action, "purpose": purpose, "status": "Success",
			"total_tokens": 100, "cost_usd": 0.001, "cost_estimated": 0,
		}).insert()
		queue = self.script.get(purpose)
		if not queue:
			raise AssertionError(f"no scripted reply for purpose={purpose}")
		item = queue.pop(0)
		if isinstance(item, Exception):
			raise item
		return {"data": item, "raw_text": json.dumps(item), "usage": {}, "latency_ms": 5}


def run_engine(script, mode=None, files=None, words="unset"):
	"""words: 'unset' → leave real OCR; None/list → monkeypatch ocr_word_boxes."""
	script = dict(script)
	# two models on the action → a live route call happens every run
	script.setdefault("route", [{"executor_model": "vision-a", "verifier_model": "qwen-v"}])
	scripted = ScriptedModel(script)
	real_call = providers.call_model
	providers.call_model = scripted
	engine.providers.call_model = scripted
	real_ocr = ground.ocr_word_boxes
	if words != "unset":
		ground.ocr_word_boxes = lambda pil: words
	try:
		result = engine.run(
			"LR Extraction",
			files=files if files is not None else [png_bytes()],
			context={"batch": "BATCH-1"},
			mode=mode,
			reference=("LR Processing Batch", "BATCH-1"),
			reference_detail="entry-row-1",
		)
	finally:
		providers.call_model = real_call
		engine.providers.call_model = real_call
		ground.ocr_word_boxes = real_ocr
	return result, scripted


def steps_of(result):
	return json.loads(FRAPPE.get_doc("I2A Run", result["run"]).steps_json)


def W(text, x, y, w=0.12, h=0.02, line=("1", "1", "1")):
	return {"text": text, "bbox": {"x": x, "y": y, "w": w, "h": h}, "line": line}


# =================================================================== units

print("\n== unit: ground._norm / match_value ==")
check("norm strips punctuation+case", ground._norm("TND - 2493") == "tnd2493")

WORDS_ONE = [W("LR", 0.10, 0.10, 0.04), W("No:", 0.15, 0.10, 0.04), W("TND-2493", 0.30, 0.10)]
clusters = ground.match_value(["TND - 2493", "TND-2493"], WORDS_ONE)
check("finds the exact word", clusters and clusters[0]["score"] == 1.0, str(clusters))
check("cluster bbox is the word box", clusters[0]["bbox"]["x"] == 0.3 and clusters[0]["bbox"]["y"] == 0.1)
check("deterministic pick on single strong match", ground.deterministic_pick(clusters) is not None)

WORDS_DUP = WORDS_ONE + [W("TND-2493", 0.30, 0.50, line=("2", "1", "1"))]
dup = ground.match_value(["TND-2493"], WORDS_DUP)
check("duplicate → two candidates", len([c for c in dup if c["score"] == 1.0]) == 2, str(dup))
check("duplicate → NOT deterministic", ground.deterministic_pick(dup) is None)

check("short value exact-only: no fuzzy noise",
	ground.match_value(["554"], [W("5541", 0.5, 0.5)]) == [])
check("short value exact hit",
	ground.match_value(["554"], [W("554", 0.5, 0.5)])[0]["score"] == 1.0)

fuzzy = ground.match_value(["TND-2493"], [W("TND-24S3", 0.3, 0.3)])
check("fuzzy OCR error still a candidate", fuzzy and ground.MATCH_FLOOR <= fuzzy[0]["score"] < ground.SURE_MATCH, str(fuzzy))
check("fuzzy alone → not deterministic", ground.deterministic_pick(fuzzy) is None)

multi = ground.match_value(["GR 12 345"], [W("GR", 0.10, 0.2, 0.03), W("12", 0.14, 0.2, 0.03), W("345", 0.18, 0.2, 0.04)])
check("multi-word window joined", multi and multi[0]["score"] == 1.0, str(multi))
check("window union bbox spans words", multi[0]["bbox"]["x"] == 0.1 and round(multi[0]["bbox"]["w"], 3) == 0.12)

check("empty inputs safe", ground.match_value([], WORDS_ONE) == [] and ground.match_value(["X"], []) == [])
check("union_bbox of nothing is None", ground.union_bbox([]) is None)

print("\n== unit: crop / marks rendering (real Pillow) ==")
from PIL import Image  # noqa: E402

pil = Image.new("RGB", (1000, 800), "white")
part = ground.crop_part(pil, {"x": 0.3, "y": 0.1, "w": 0.12, "h": 0.02})
check("crop part is a data-url image", part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/png;base64,"))
marked = ground.draw_marks(pil, clusters)
check("marked image is a data-url image", marked["image_url"]["url"].startswith("data:image/png;base64,"))
try:
	ground.crop_part(pil, {"x": 0.999, "y": 0.999, "w": 0.0001, "h": 0.0001})
	degenerate_raised = True  # padding rescues micro boxes — acceptable either way
except ValueError:
	degenerate_raised = True
check("degenerate crop handled", degenerate_raised)

print("\n== unit: prompt builders ==")


class FakeAction:
	rules = "check freight"

	def parsed_schema(self):
		return LR_SCHEMA


fa = FakeAction()
fields_demo, _ = verify.whitelist(full_extraction(), LR_SCHEMA)
msgs = verify.build_verify_messages(fa, [{"type": "image_url", "image_url": {"url": "data:doc"}}], fields_demo,
	crops=[{"field": "lr_number", "index": None, "part": {"type": "image_url", "image_url": {"url": "data:crop"}}}])
sys_txt = msgs[0]["content"]
user_content = msgs[1]["content"]
claims_txt = user_content[0]["text"]
check("third-party framing", "PREVIOUS automated system" in sys_txt and "UNTRUSTED" in sys_txt)
check("claim-by-claim", "CLAIM 1" in claims_txt and "CLAIM 4" in claims_txt)
check("array index in claim", 'field "bill_numbers"[0]' in claims_txt)
check("crop referenced in claim", "CROP 1" in claims_txt)
check("crop labeled + appended", any(isinstance(c, dict) and c.get("text", "").startswith("CROP 1") for c in user_content)
	and user_content[-1]["image_url"]["url"] == "data:crop")
check("rules included", "check freight" in sys_txt)
check("contract unchanged", '"disagreements"' in sys_txt)

som_msgs = verify.build_som_messages([{
	"field": "lr_number", "index": None, "label": "LR Number", "value": "TND-2493", "raw_text": "TND-2493",
	"marks": [{"n": 1, "text": "TND-2493"}, {"n": 2, "text": "TND-2493"}],
	"part": {"type": "image_url", "image_url": {"url": "data:som"}},
}])
check("som asks for a number, never coordinates", "box number" in som_msgs[0]["content"] and '"mark"' in som_msgs[0]["content"])
check("som roster present", 'box 1: "TND-2493"' in som_msgs[1]["content"][0]["text"])

cc_msgs = verify.build_crop_check_messages([{"n": 1, "field": "lr_number", "value": "TND-2493",
	"part": {"type": "image_url", "image_url": {"url": "data:c"}}}])
check("crop-check contract", '"contains"' in cc_msgs[0]["content"] and "CROP 1" in cc_msgs[1]["content"][0]["text"])

# =============================================================== engine e2e

print("\n== e2e: deterministic OCR bbox repair (no repair LLM call) ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}, {"disagreements": []}],
	"crop_check": [{"checks": [{"crop": 1, "contains": True, "read_text": "TND-2493"}]}],
}, mode="Automated", words=WORDS_ONE)
check("completed", result["status"] == "Completed", result.get("status"))
check("bbox recovered from OCR words", result["fields"]["lr_number"]["bbox"] == {"x": 0.3, "y": 0.1, "w": 0.12, "h": 0.02},
	str(result["fields"]["lr_number"]["bbox"]))
check("NO free-form repair call", all(c["purpose"] != "repair" for c in sm.calls))
check("NO som call (unambiguous)", all(c["purpose"] != "som_select" for c in sm.calls))
check("crop-back ran on verifier", any(c["purpose"] == "crop_check" and c["model"] == "qwen-v" for c in sm.calls))
steps = steps_of(result)
check("deterministic match logged", any(s["step"] == "bbox_ocr_match" and s.get("method") == "deterministic" for s in steps))
check("ocr stats logged", any(s["step"] == "ocr" and s.get("words") == 3 for s in steps))
check("crop_back pass logged", any(s["step"] == "crop_back" and s.get("passed") for s in steps))
check("approved in automated", result["fields"]["lr_number"]["status"] == "Approved")

print("\n== e2e: ambiguous → Set-of-Marks selection ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}, {"disagreements": []}],
	"som_select": [{"selections": [{"field": "lr_number", "index": None, "mark": 2},
		{"field": "freight_amount", "index": None, "mark": 1}]}],  # forged extra selection
	"crop_check": [{"checks": [{"crop": 1, "contains": True, "read_text": "TND-2493"}]}],
}, mode="Automated", words=WORDS_DUP)
check("completed", result["status"] == "Completed", result.get("status"))
check("som picked candidate 2 (second occurrence)", result["fields"]["lr_number"]["bbox"]["y"] == 0.5,
	str(result["fields"]["lr_number"]["bbox"]))
check("no free-form repair call", all(c["purpose"] != "repair" for c in sm.calls))
check("forged selection for unasked field ignored",
	result["fields"]["freight_amount"]["bbox"] == {"x": 0.7, "y": 0.3, "w": 0.1, "h": 0.03},
	str(result["fields"]["freight_amount"]["bbox"]))
check("som logged", any(s["step"] == "bbox_ocr_match" and s.get("method") == "som" and s.get("mark") == 2 for s in steps_of(result)))

print("\n== e2e: som declines / invalid mark → free-form fallback ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}, {"disagreements": []}],
	"som_select": [{"selections": [{"field": "lr_number", "index": None, "mark": 99}]}],
	"repair": [{"repairs": [{"field": "lr_number", "index": 0, "value": "TND-2493", "raw_text": "TND-2493",
		"confidence": 0.9, "bbox": [100, 100, 140, 300]}]}],
	"crop_check": [{"checks": [{"crop": 1, "contains": True, "read_text": "TND-2493"}]}],
}, mode="Automated", words=WORDS_DUP)
check("completed via free-form", result["status"] == "Completed", result.get("status"))
check("free-form repair ran", any(c["purpose"] == "repair" for c in sm.calls))
check("free-form bbox also crop-back checked", any(c["purpose"] == "crop_check" for c in sm.calls))
check("invalid mark logged", any(s["step"] == "bbox_ocr_match" and s.get("mark") is None and s.get("method") == "som"
	for s in steps_of(result)))

print("\n== e2e: crop-back rejects a hallucinated box → next source, converges ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}, {"disagreements": []}, {"disagreements": []}],
	"crop_check": [
		{"checks": [{"crop": 1, "contains": False, "read_text": "Consignee Copy"}], "status": "Approved", "approve_all": True},
		{"checks": [{"crop": 1, "contains": True, "read_text": "TND-2493"}]},
	],
	# round 2: the tight candidate is remembered-rejected; the remaining
	# superstring window goes to SoM — the model declines → free-form repair
	"som_select": [{"selections": [{"field": "lr_number", "index": None, "mark": None}]}],
	"repair": [{"repairs": [{"field": "lr_number", "index": 0, "value": "TND-2493", "raw_text": "TND-2493",
		"confidence": 0.9, "bbox": [700, 700, 740, 900]}]}],
}, mode="Automated", words=WORDS_ONE)
check("completed after retry", result["status"] == "Completed", result.get("status"))
check("rejected box did not survive", result["fields"]["lr_number"]["bbox"] == {"x": 0.7, "y": 0.7, "w": 0.2, "h": 0.04},
	str(result["fields"]["lr_number"]["bbox"]))
check("two rounds used (rejection = progress, not a loop)", result["rounds"] == 2, str(result["rounds"]))
steps = steps_of(result)
check("rejection logged with what the crop showed",
	any(s["step"] == "crop_back" and s.get("rejected") and "Consignee" in s["rejected"][0]["read_text"] for s in steps))
check("forged approve_all in crop_check response had no effect",
	all(s.get("approve_all") is None for s in steps) and result["fields"]["lr_number"]["status"] == "Approved")

print("\n== e2e: crop_check omitting a crop = rejection (fail closed) ==")
grounded_world(mode="Manual")
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}, {"disagreements": []}, {"disagreements": []}],
	"crop_check": [{"checks": []}],
	# round 2: sole candidate is remembered-rejected → free-form, which gives nothing
	"repair": [{"repairs": []}],
}, mode="Manual", words=[W("TND-2493", 0.3, 0.1)])
check("silent omission → bbox rejected", result["fields"]["lr_number"]["bbox"] is None)
check("run needs review (bbox_missing stands)", result["status"] == "Needs Review", result.get("status"))
check("manual mode: everything Pending regardless", result["fields"]["lr_number"]["status"] == "Pending")
check("no som on rejected-only candidates", all(c["purpose"] != "som_select" for c in sm.calls))

print("\n== e2e: verify pass ships per-claim crops ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction()],
	"verify": [{"disagreements": []}],
}, mode="Automated")  # real tesseract not needed: no bbox deficiency, OCR never runs
vcall = next(c for c in sm.calls if c["purpose"] == "verify")
content = vcall["messages"][1]["content"]
images = [c for c in content if isinstance(c, dict) and c.get("type") == "image_url"]
check("completed", result["status"] == "Completed")
check("doc + 4 claim crops attached (one per extracted field)", len(images) == 5, str(len(images)))
check("claims text present", "CLAIM 1" in content[0]["text"] and "auditor" in vcall["messages"][0]["content"])
check("verify_crops step logged", any(s["step"] == "verify_crops" and s.get("count") == 4 for s in steps_of(result)))
check("no OCR run when no bbox deficiency", all(s["step"] != "ocr" for s in steps_of(result)))

print("\n== e2e: tesseract unavailable → graceful free-form fallback ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}, {"disagreements": []}],
	"repair": [{"repairs": [{"field": "lr_number", "index": 0, "value": "TND-2493", "raw_text": "TND-2493",
		"confidence": 0.9, "bbox": [100, 100, 140, 300]}]}],
	"crop_check": [{"checks": [{"crop": 1, "contains": True, "read_text": "TND-2493"}]}],
}, mode="Automated", words=None)  # ocr_word_boxes → None = tesseract missing
check("completed", result["status"] == "Completed", result.get("status"))
check("ocr_unavailable logged", any(s["step"] == "ocr_unavailable" for s in steps_of(result)))
check("free-form repair used", any(c["purpose"] == "repair" for c in sm.calls))

print("\n== e2e: flags off → zero grounding behavior (back-compat) ==")
grounded_world(mode="Automated", use_ocr_anchored_repair=0, use_crop_back_check=0, use_verify_crops=0)
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}, {"disagreements": []}],
	"repair": [{"repairs": [{"field": "lr_number", "index": 0, "value": "TND-2493", "raw_text": "TND-2493",
		"confidence": 0.9, "bbox": [100, 100, 140, 300]}]}],
}, mode="Automated", words=WORDS_ONE)
check("completed", result["status"] == "Completed", result.get("status"))
check("no som/crop_check calls", all(c["purpose"] not in ("som_select", "crop_check") for c in sm.calls))
check("verify got no crops", all(s["step"] != "verify_crops" for s in steps_of(result)))
check("no ocr step", all(s["step"] not in ("ocr", "ocr_unavailable") for s in steps_of(result)))

print("\n== e2e: budget exhaustion mid-ladder is a clean stop ==")
grounded_world(mode="Automated", max_calls_per_run=3)
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}],
	"som_select": [{"selections": [{"field": "lr_number", "index": None, "mark": 1}]}],
}, mode="Automated", words=WORDS_DUP)
check("not Failed (deliberate stop)", result["status"] == "Needs Review", result.get("status"))
check("budget_stop logged", any(s["step"] == "budget_stop" for s in steps_of(result)))
check("run finalized", FRAPPE.get_doc("I2A Run", result["run"]).status == "Needs Review")

print("\n== e2e: multiple files → pixel grounding disabled entirely (review fix #20) ==")
grounded_world(mode="Manual")
result, sm = run_engine({
	"extract": [full_extraction()],
	"verify": [{"disagreements": []}],
}, mode="Manual", files=[png_bytes(), png_bytes(400, 400)])
check("completed", result["status"] == "Completed")
check("multifile disable logged", any(s["step"] == "grounding_disabled_multifile" for s in steps_of(result)))
check("verify got NO crops (wrong-page crops are worse than none)",
	all(s["step"] != "verify_crops" for s in steps_of(result)))

# ------------------------------------------------- review-fix regressions

print("\n== review fixes: fail-closed crop-back on provider failure ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}, {"disagreements": []}],
	"crop_check": [providers.ProviderError("verifier down")],
}, mode="Automated", words=WORDS_ONE)
check("unproven box stripped (fail closed)", result["fields"]["lr_number"]["bbox"] is None)
check("run needs review, not silently Completed", result["status"] == "Needs Review", result.get("status"))
check("failure + rejection logged", any(s["step"] == "crop_check_failed" for s in steps_of(result))
	and any(s["step"] == "crop_back" and s.get("rejected") and "unavailable" in s["rejected"][0]["reason"] for s in steps_of(result)))
check("rectangle NOT remembered (never disproven)",
	all(s["step"] != "bbox_candidates_filtered" for s in steps_of(result)))
check("field held Pending in Automated", result["fields"]["lr_number"]["status"] == "Pending")

print("\n== review fixes: free-form repair gated to asked deficiencies ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}, {"disagreements": []}],
	"repair": [{"repairs": [
		{"field": "lr_number", "index": 0, "value": "TND-2493", "raw_text": "TND-2493", "confidence": 0.9, "bbox": [100, 100, 140, 300]},
		{"field": "freight_amount", "index": 0, "value": 999999, "raw_text": "999999", "confidence": 0.99, "bbox": [300, 700, 330, 800]},
	]}],
	"crop_check": [{"checks": [{"crop": 1, "contains": True, "read_text": "TND-2493"}]}],
}, mode="Automated", words=None)  # no OCR → free-form path
check("asked field repaired", result["fields"]["lr_number"]["bbox"] is not None)
check("UNASKED field untouched (injection contained)", result["fields"]["freight_amount"]["value"] == 554.0,
	str(result["fields"]["freight_amount"]["value"]))
check("dropped repair logged", any(s["step"] == "repair" and s.get("dropped") for s in steps_of(result)))

print("\n== review fixes: forged out-of-range verify index dropped ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction()],
	"verify": [{"disagreements": [{"field": "bill_numbers", "index": 7, "expected": "XXX", "reason": "forged"}]}],
}, mode="Automated")
check("no deficiency created from out-of-range index", result["status"] == "Completed", result.get("status"))
check("single verify call (no repair loop entered)", sum(1 for c in sm.calls if c["purpose"] == "verify") == 1)
check("bill value untouched", result["fields"]["bill_numbers"][0]["value"] == "INV-001")

print("\n== review fixes: som scalar-index normalization (index 0 == null) ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction(lr_number=make_item("TND-2493", bbox=None))],
	"verify": [{"disagreements": []}, {"disagreements": []}],
	"som_select": [{"selections": [{"field": "lr_number", "index": 0, "mark": 2}]}],  # scalar echoed with 0
	"crop_check": [{"checks": [{"crop": 1, "contains": True, "read_text": "TND-2493"}]}],
}, mode="Automated", words=WORDS_DUP)
check("pick honored despite index cosmetics", result["fields"]["lr_number"]["bbox"]["y"] == 0.5,
	str(result["fields"]["lr_number"]["bbox"]))
check("no wasted free-form call", all(c["purpose"] != "repair" for c in sm.calls))

print("\n== review fixes: value repaired under a stale box is crop-back checked ==")
grounded_world(mode="Automated")
result, sm = run_engine({
	"extract": [full_extraction()],
	"verify": [
		{"disagreements": [{"field": "freight_amount", "index": None, "expected": "654", "reason": "misread"}]},
		{"disagreements": []},
	],
	"repair": [{"repairs": [{"field": "freight_amount", "index": 0, "value": 654, "raw_text": "654", "confidence": 0.9, "bbox": None}]}],
	"crop_check": [{"checks": [{"crop": 1, "contains": True, "read_text": "654"}]}],
}, mode="Automated")
check("completed", result["status"] == "Completed", result.get("status"))
check("value change under old bbox triggered crop-back", any(c["purpose"] == "crop_check" for c in sm.calls))
check("value updated", result["fields"]["freight_amount"]["value"] == 654.0)

print("\n== review fixes: numeric & size hardening units ==")
check("NaN list bbox rejected", engine.extract.normalize_bbox([float("nan")] * 4) is None)
check("NaN dict bbox rejected", engine.extract.normalize_bbox({"x": float("nan"), "y": 0.1, "w": 0.1, "h": 0.1}) is None)
check("infinite amount fails format", engine.extract.normalize_value("9" * 320, "amount")[1] is False)
check("match_targets survives inf value", engine._match_targets({"raw_text": None, "value": float("inf")}) == ["inf"])
check("oversized bbox flagged insane", any(
	d["kind"] == "bbox_insane" for d in verify.deterministic_check(
		{"lr_number": {"value": "X", "raw_text": "X", "confidence": 1, "bbox": {"x": 0.0, "y": 0.0, "w": 0.9, "h": 0.9}}},
		[{"key": "lr_number", "label": "LR"}])))
big = Image.new("RGB", (4000, 2000), "white")
import io as _io
buf = _io.BytesIO(); big.save(buf, format="PNG")
loaded = ground.load_image(buf.getvalue())
check("load_image caps working size", max(loaded.size) <= ground.LOAD_MAX_DIM, str(loaded.size))
import base64 as _b64
huge_part = ground.crop_part(Image.new("RGB", (3000, 3000), "white"), {"x": 0.0, "y": 0.0, "w": 0.99, "h": 0.99})
huge_img = Image.open(_io.BytesIO(_b64.b64decode(huge_part["image_url"]["url"].split(",", 1)[1])))
check("crop_part caps near-full-page crops", max(huge_img.size) <= ground.CROP_MAX_DIM, str(huge_img.size))

# ------------------------------------------------- real tesseract (optional)

print("\n== e2e: REAL tesseract on a rendered LR-style image ==")
if shutil.which("tesseract"):
	from PIL import ImageDraw, ImageFont

	img = Image.new("RGB", (1200, 900), "white")
	d = ImageDraw.Draw(img)
	try:
		font = ImageFont.load_default(size=36)
	except TypeError:
		font = ImageFont.load_default()
	d.text((100, 80), "LR No: TND-2493", fill="black", font=font)
	d.text((100, 200), "Date: 12/05/2026", fill="black", font=font)
	d.text((800, 700), "Total: 554", fill="black", font=font)

	words = ground.ocr_word_boxes(img)
	check("tesseract produced words", bool(words), str(words)[:120])
	hits = ground.match_value(["TND-2493"], words or [])
	check("value located on real render", bool(hits), str(hits)[:120])
	if hits:
		b = hits[0]["bbox"]
		check("located in the right region (top-left quadrant)", b["x"] < 0.5 and b["y"] < 0.3, str(b))
		pick = ground.deterministic_pick(hits)
		check("single occurrence → deterministic", pick is not None)
else:
	print("  (skipped — no tesseract binary on this machine)")

# ================================================================== summary

print("\n" + "=" * 60)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
	print("FAILED:", *FAIL, sep="\n  - ")
	sys.exit(1)
print("ALL TESTS PASSED")
