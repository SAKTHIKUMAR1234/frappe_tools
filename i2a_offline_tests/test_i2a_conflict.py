"""Offline tests for I2A cross-reference conflict detection (the careful-clerk
rule). One case per use case UC1..UC10 from the conflict-detection design.

Runs the REAL engine code (frappe_tools.i2a.*) + the essdee surface helper
(essdee.ocr._match_note) against the fake frappe layer and scripted models. No
site, no network, no production touch.

Header mirrors test_i2a.py: fake_frappe.install() FIRST (so the app modules bind
to the fakes at import), then app-path inserts, then imports.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fake_frappe

FRAPPE, REQUESTS = fake_frappe.install()

sys.path.insert(0, "/mnt/storage/dev/frappe-v15/apps/frappe_tools")
sys.path.insert(0, "/mnt/storage/dev/frappe-v15/apps/essdee")

from frappe_tools.i2a import engine, match, providers, tools, verify  # noqa: E402
from essdee.essdee.utils import ocr  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
	(PASS if cond else FAIL).append(name)
	if not cond:
		print(f"  ✗ {name}  {detail}")
	else:
		print(f"  ✓ {name}")


# --------------------------------------------------------------- fixtures

GOOD_BBOX = [100, 100, 140, 300]

# Two Sales Invoices from the original defect: an e-way bill exactly proves A,
# while a bill number on the same LR points to a DIFFERENT invoice B.
SI_A = {"name": "INV2627-00311", "ewaybill": "582012827096"}
SI_B = {"name": "SOI2627-00327", "ewaybill": None}
# extra rows for multi-reference cases
SI_C = {"name": "INV2627-00099", "ewaybill": "999999999999"}   # 2nd exact e-way
SI_E = {"name": "INV2627-00312", "ewaybill": "111111111111"}   # pairs to tail 00312
SI_D = {"name": "INV2627-00327", "ewaybill": None}             # collides on tail 327

CORRO = [
	{"target_field": "ewaybill", "from": "eway_bills", "label": "e-way bill"},
	{"target_field": "name", "from": "bill_numbers", "label": "bill number"},
	{"target_field": "name", "from": "bill_numbers", "match": "numeric_suffix", "label": "bill number"},
]

LR_SCHEMA = [
	{"key": "lr_number", "label": "LR Number", "required": True, "format": "strip-spaces", "bbox_required": True},
	{"key": "lr_date", "label": "LR Date", "required": True, "format": "date:indian-ddmmyyyy", "bbox_required": True},
	{"key": "freight_amount", "label": "Freight Amount", "required": True, "format": "amount", "bbox_required": True},
	{"key": "bill_numbers", "label": "Bill No", "kind": "array", "format": "strip-spaces", "bbox_required": True},
	{"key": "eway_bills", "label": "EWB", "kind": "array", "format": "digits:12", "bbox_required": True},
]


def make_item(value, conf=0.95, bbox=None):
	return {"value": value, "raw_text": str(value), "confidence": conf, "bbox": bbox or GOOD_BBOX}


def fields(eway=None, bills=None):
	"""Extraction dict with the reference arrays under test."""
	out = {}
	if eway is not None:
		out["eway_bills"] = [make_item(v) for v in eway]
	if bills is not None:
		out["bill_numbers"] = [make_item(v) for v in bills]
	return out


def cfg(candidate_query=None, **over):
	c = {"target_doctype": "Sales Invoice", "corroborate": [dict(r) for r in CORRO]}
	if candidate_query is not None:
		c["candidate_query"] = candidate_query
	c.update(over)
	return c


EWAY_Q = {"or_filters": {"ewaybill": ["in", "{eway_bills}"]}, "fields": ["name", "ewaybill"]}
BILL_Q = {"or_filters": {"name": ["in", "{bill_numbers}"]}, "fields": ["name", "ewaybill"]}


def build_action(match_config, tools_list=None, **extra):
	c = FakeAction(match_config, tools_list, **extra)
	return c


class FakeAction(fake_frappe.FakeRow):
	def __init__(self, match_config, tools_list=None, **extra):
		data = {"name": "LR Extraction", "doctype": "I2A Action",
			"match_config": json.dumps(match_config)}
		if tools_list is not None:
			data["tools"] = json.dumps(tools_list)
		data.update(extra)
		super().__init__(data, doctype="I2A Action")

	def parsed_schema(self):
		return LR_SCHEMA


APPLY_TOOL = {"name": "apply_lr_to_invoice", "method": "x.apply", "kind": "write", "finalizes": True,
	"corroborate": {"arg": "sales_invoice", "doctype": "Sales Invoice"},
	"parameters": {"type": "object", "properties": {"sales_invoice": {"type": "string"}}}}
FLAG_TOOL = {"name": "flag_for_review", "method": "x.flag", "kind": "read", "escalates": True,
	"parameters": {"type": "object", "properties": {"reason": {"type": "string"}}}}


def use_rows(rows):
	"""Point FRAPPE.get_all at a fixed candidate set for 'Sales Invoice'."""
	orig = FRAPPE.get_all
	FRAPPE.get_all = lambda dt, *a, **k: ([dict(r) for r in rows] if dt == "Sales Invoice" else orig(dt, *a, **k))
	return orig


def use_rows_fifo(batches):
	"""FIFO get_all: each successive 'Sales Invoice' query returns the next batch
	(so a strong-key query and a date-net query return different rows)."""
	orig = FRAPPE.get_all
	queue = [list(b) for b in batches]

	def _ga(dt, *a, **k):
		if dt != "Sales Invoice":
			return orig(dt, *a, **k)
		return [dict(r) for r in (queue.pop(0) if queue else [])]

	FRAPPE.get_all = _ga
	return orig


class StubState:
	"""Minimal engine state for _deterministic_resolve / direct-call paths."""

	def __init__(self, mode="Automated"):
		self.mode = mode
		self.sessions = {}
		self.steps = []
		self.run_doc = fake_frappe.FakeRow({"reference_doctype": "LR Processing Batch",
			"reference_name": "B1", "reference_detail": "E1", "name": "RUN1"})

	def step(self, kind, **data):
		self.steps.append({"step": kind, **data})


class MatchStubState(StubState):
	"""Scripts the model answer for match.run_match."""

	def __init__(self, answer):
		super().__init__()
		self.answer = answer

	def call(self, ai_model, messages, purpose):
		self.steps.append({"step": "call", "purpose": purpose})
		return self.answer


class ScriptedTools(StubState):
	"""Drives engine._agentic_phase: each turn is a list of tool calls or a
	final string. Mirrors test_i2a.ScriptedTools + the state surface the phase
	touches (call_tools / step / sessions / mode / run_doc)."""

	def __init__(self, script, mode="Automated"):
		super().__init__(mode=mode)
		self.turns = list(script)
		self.i = 0

	def call_tools(self, ai_model, messages, specs, purpose="agent"):
		item = self.turns[self.i] if self.i < len(self.turns) else "done"
		self.i += 1
		if isinstance(item, str):
			return {"message": {"role": "assistant", "content": item}, "tool_calls": [], "content": item}
		tcs = [{"id": f"c{n}", "name": t["name"], "arguments": t.get("arguments", {})} for n, t in enumerate(item)]
		return {"message": {"role": "assistant", "content": "", "tool_calls": tcs}, "tool_calls": tcs, "content": ""}


# ---------------------------------------------------------- e2e harness (UC7/8)

def setup_world(mode="Manual"):
	FRAPPE.reset()
	FRAPPE.seed("AI Model", "vision-a", model_label="vision-a", enabled=1, provider="OpenRouter",
		model_id="google/gemini-2.5-flash", supports_vision=1, supports_json_mode=1,
		max_tokens=8192, temperature=0, cost_per_m_input=0.1, cost_per_m_output=0.4, api_key="k")
	models_rows = [fake_frappe.FakeRow({"ai_model": "vision-a", "remarks": "vision — extract",
		"is_orchestrator": 1, "is_verifier": 0})]
	FRAPPE.seed("I2A Action", "LR Extraction", action_name="LR Extraction", enabled=1, mode=mode,
		purpose="extract LR fields", instructions="Extract the LR fields.", knowledge="",
		rules="compare all fields", request_notes="", use_llm_request_notes=0,
		output_schema=json.dumps(LR_SCHEMA), max_rounds=4, max_calls_per_run=12,
		run_seconds_budget=480, models=models_rows)
	FRAPPE.seed("LR Processing Batch", "BATCH-1", processing_mode=mode)


def full_extraction(**overrides):
	data = {
		"lr_number": make_item("TND-2493"),
		"lr_date": make_item("2026-05-12", bbox=[200, 100, 230, 240]),
		"freight_amount": make_item(554, bbox=[300, 700, 330, 800]),
		"bill_numbers": [make_item("INV-001", bbox=[400, 100, 430, 260])],
		"eway_bills": [make_item("123456789012", bbox=[500, 100, 530, 300])],
	}
	data.update(overrides)
	return data


class ScriptedModel:
	def __init__(self, script):
		self.script = {k: list(v) for k, v in script.items()}
		self.calls = []

	def __call__(self, ai_model, messages, *, json_mode=True, purpose="", run=None, action=None, max_tokens=None):
		self.calls.append({"model": getattr(ai_model, "name", ai_model), "purpose": purpose})
		FRAPPE.new_doc("I2A LLM Call").update({
			"run": run, "action": action, "purpose": purpose, "status": "Success",
			"total_tokens": 100, "cost_usd": 0.001, "cost_estimated": 0}).insert()
		queue = self.script.get(purpose)
		if not queue:
			raise AssertionError(f"no scripted reply for purpose={purpose}")
		item = queue.pop(0)
		if isinstance(item, Exception):
			raise item
		return {"data": item, "raw_text": json.dumps(item), "usage": {}, "latency_ms": 5}


def run_engine(script, mode=None):
	scripted = ScriptedModel(script)
	real = providers.call_model
	providers.call_model = scripted
	engine.providers.call_model = scripted
	try:
		result = engine.run("LR Extraction", files=[b"fakeimagebytes"], context={"batch": "BATCH-1"},
			mode=mode, reference=("LR Processing Batch", "BATCH-1"), reference_detail="entry-row-1")
	finally:
		providers.call_model = real
		engine.providers.call_model = real
	return result, scripted


# =================================================================== UC1

print("\n== UC1: exact EWB→A + bill number→B is a conflict (nothing applied) ==")
# 1a: bill number EXACTLY matches invoice B's name
refs = match.resolve_references(cfg(), fields(eway=["582012827096"], bills=["SOI2627-00327"]), [SI_A, SI_B])
check("UC1 conflict flagged", refs and refs["conflict"] is True, str(refs))
check("UC1 reason names BOTH invoices",
	"INV2627-00311" in refs["reason"] and "SOI2627-00327" in refs["reason"], refs["reason"])
check("UC1 reason carries the e-way reference value", "582012827096" in refs["reason"], refs["reason"])

# 1b: bill number matches B only by numeric SUFFIX (distinct printed value "00327")
refs_sfx = match.resolve_references(cfg(), fields(eway=["582012827096"], bills=["00327"]), [SI_A, SI_B])
check("UC1 suffix variant still a conflict", refs_sfx and refs_sfx["conflict"] is True, str(refs_sfx))
check("UC1 suffix reason carries BOTH distinct reference values",
	"582012827096" in refs_sfx["reason"] and "00327" in refs_sfx["reason"], refs_sfx["reason"])
check("UC1 suffix reason names both invoices",
	"INV2627-00311" in refs_sfx["reason"] and "SOI2627-00327" in refs_sfx["reason"], refs_sfx["reason"])

# ADVERSARIAL: neuter the new check (detect_conflicts=0) → NO conflict is seen.
# These asserts FAIL against the old pre-conflict behaviour, proving the tests
# above are load-bearing on the new logic (not passing by accident).
refs_off = match.resolve_references(cfg(detect_conflicts=0),
	fields(eway=["582012827096"], bills=["SOI2627-00327"]), [SI_A, SI_B])
check("UC1 neutered detector sees no conflict (guards against false-pass)", refs_off is None, str(refs_off))

# engine._deterministic_resolve: under conflict NOTHING is auto-applied
_orig = use_rows([SI_A, SI_B])
_act = build_action(cfg(candidate_query=[EWAY_Q, BILL_Q]), [APPLY_TOOL])
_st1 = StubState()
_dr = engine._deterministic_resolve(_st1, _act,
	fields(eway=["582012827096"], bills=["SOI2627-00327"]), {}, tools.parse_catalog(_act))
check("UC1 applied_targets stays EMPTY under conflict", _dr["applied_targets"] == [], str(_dr))
check("UC1 deterministic resolve not resolved", _dr["resolved"] is False)
check("UC1 summary carries the conflict reason",
	"INV2627-00311" in _dr["summary"] and "SOI2627-00327" in _dr["summary"], _dr["summary"])
check("UC1 reference_conflict step logged", any(s["step"] == "reference_conflict" for s in _st1.steps), str(_st1.steps))
FRAPPE.get_all = _orig

# =================================================================== UC2

print("\n== UC2: 2 exact EWB {A,C} + 1 exact bill→B → union 3 > expected 2 ==")
refs2 = match.resolve_references(cfg(),
	fields(eway=["582012827096", "999999999999"], bills=["SOI2627-00327"]), [SI_A, SI_C, SI_B])
check("UC2 conflict flagged", refs2 and refs2["conflict"] is True, str(refs2))
check("UC2 expected count is 2", refs2["expected"] == 2, str(refs2["expected"]))
check("UC2 three distinct records claimed", len(refs2["union"]) == 3, str(refs2["union"]))
check("UC2 reason carries all three resolutions",
	all(n in refs2["reason"] for n in ("INV2627-00311", "INV2627-00099", "SOI2627-00327")), refs2["reason"])

# =================================================================== UC3

print("\n== UC3: one bill tail matches two invoices, no proof → ambiguous ==")
refs3 = match.resolve_references(cfg(), fields(bills=["327"]), [SI_B, SI_D])
amb = [i for i in refs3["issues"] if i["type"] == "ambiguous"]
check("UC3 one ambiguous issue", len(amb) == 1, str(refs3["issues"]))
check("UC3 ambiguous names both tied candidates",
	"SOI2627-00327" in amb[0]["reason"] and "INV2627-00327" in amb[0]["reason"], amb[0]["reason"])
check("UC3 not a conflict (insufficient, not contradictory)", refs3["conflict"] is False, str(refs3))

# ambiguity blocks the WRITE gate for BOTH tied targets, even with suffix_autoapply on
_orig = use_rows([SI_B, SI_D])
_gv = FRAPPE.db.get_value
_uc3_rows = {"SOI2627-00327": dict(SI_B), "INV2627-00327": dict(SI_D)}
FRAPPE.db.get_value = lambda dt, name, f=None, as_dict=False, **k: (_uc3_rows.get(name) if dt == "Sales Invoice" else _gv(dt, name, f, as_dict=as_dict, **k))
_act3 = build_action(cfg(candidate_query=[BILL_Q], suffix_autoapply=1), [APPLY_TOOL])
g_b = engine._corroborate_write(_act3, APPLY_TOOL, {"sales_invoice": "SOI2627-00327"}, fields(bills=["327"]), {})
g_d = engine._corroborate_write(_act3, APPLY_TOOL, {"sales_invoice": "INV2627-00327"}, fields(bills=["327"]), {})
check("UC3 gate refuses tied target 1 (even with suffix_autoapply)", g_b is False, str(g_b))
check("UC3 gate refuses tied target 2 (even with suffix_autoapply)", g_d is False, str(g_d))
FRAPPE.db.get_value = _gv
FRAPPE.get_all = _orig

# =================================================================== UC4

print("\n== UC4: run_match — model picks B, exact EWB proof for A surfaces, status conflict ==")
_orig = use_rows([SI_A, SI_B])
_act4 = build_action(cfg(candidate_query=[EWAY_Q, BILL_Q], confidence_threshold=0.8))
_answer = {"matches": [{"target": "SOI2627-00327", "confidence": 0.95, "reason": "bill number match"}]}
_res4 = match.run_match(MatchStubState(_answer), _act4, object(), fields(eway=["582012827096"], bills=[]), {})
check("UC4 status conflict", _res4["status"] == "conflict", str(_res4.get("status")))
check("UC4 conflict_reason names A as the exact proof", "INV2627-00311" in (_res4.get("conflict_reason") or ""), str(_res4.get("conflict_reason")))
check("UC4 exact-proof A surfaced as a proof row in matches",
	any(m.get("proof") and m["target"] == "INV2627-00311" for m in _res4["matches"]), str(_res4["matches"]))
FRAPPE.get_all = _orig

# =================================================================== UC5

print("\n== UC5: agentic phase refuses the apply under conflict, flags instead ==")
_apply_calls = []


def _fn_flag(**kw):
	return {"flagged": True, "reason": kw.get("reason")}


def _fn_apply5(**kw):
	_apply_calls.append(kw)
	return {"applied": True, "invoice": kw.get("sales_invoice")}


FRAPPE.get_attr = lambda path: {"x.apply": _fn_apply5, "x.flag": _fn_flag}.get(path)
FRAPPE.whitelisted = [_fn_apply5, _fn_flag]
FRAPPE.has_permission = lambda *a, **k: True

_orig = use_rows([SI_A, SI_B])
_gv = FRAPPE.db.get_value
_uc5_rows = {"INV2627-00311": dict(SI_A), "SOI2627-00327": dict(SI_B)}
FRAPPE.db.get_value = lambda dt, name, f=None, as_dict=False, **k: (_uc5_rows.get(name) if dt == "Sales Invoice" else _gv(dt, name, f, as_dict=as_dict, **k))

_act5 = build_action(cfg(candidate_query=[EWAY_Q, BILL_Q]), [APPLY_TOOL, FLAG_TOOL])
_f5 = fields(eway=["582012827096"], bills=["SOI2627-00327"])
_agent = engine._agentic_phase(
	ScriptedTools([
		[{"name": "apply_lr_to_invoice", "arguments": {"sales_invoice": "SOI2627-00327"}}],
		[{"name": "flag_for_review", "arguments": {"reason": "engine conflict"}}],
		"flagged for review",
	]),
	_act5, object(), _f5, {}, tools.parse_catalog(_act5))
_apply_detail = next((c["detail"] for c in _agent["tool_calls"] if c["tool"] == "apply_lr_to_invoice"), "")
check("UC5 apply refused (not ok)", not any(c["tool"] == "apply_lr_to_invoice" and c["ok"] for c in _agent["tool_calls"]), str(_agent["tool_calls"]))
check("UC5 apply detail starts with 'conflict'", _apply_detail.startswith("conflict"), _apply_detail)
check("UC5 apply detail names both invoices",
	"INV2627-00311" in _apply_detail and "SOI2627-00327" in _apply_detail, _apply_detail)
check("UC5 no invoice was actually applied", _apply_calls == [], str(_apply_calls))
check("UC5 flagged True", _agent["flagged"] is True)
check("UC5 resolved False", _agent["resolved"] is False)

# direct write-gate: B is exact-name corroborated, yet the gate FAILS under the conflict
g5 = engine._corroborate_write(_act5, APPLY_TOOL, {"sales_invoice": "SOI2627-00327"}, _f5, {})
check("UC5 write gate refuses exact-name B under conflict", g5 is False, str(g5))

# ADVERSARIAL for UC5: with detect_conflicts=0 the gate PASSES B (old behaviour)
_act5_off = build_action(cfg(candidate_query=[EWAY_Q, BILL_Q], detect_conflicts=0), [APPLY_TOOL, FLAG_TOOL])
g5_off = engine._corroborate_write(_act5_off, APPLY_TOOL, {"sales_invoice": "SOI2627-00327"}, _f5, {})
check("UC5 neutered detector lets the wrong apply through (guards false-pass)", g5_off is True, str(g5_off))

FRAPPE.db.get_value = _gv
FRAPPE.get_all = _orig

# =================================================================== UC6

print("\n== UC6: deterministic_candidates carries the conflict + per-reference story ==")
_orig = use_rows([SI_A, SI_B])
_act6 = build_action(cfg(candidate_query=[EWAY_Q, BILL_Q]))
_dc = match.deterministic_candidates(_act6, fields(eway=["582012827096"], bills=["SOI2627-00327"]), {})
check("UC6 status conflict", _dc["status"] == "conflict", str(_dc.get("status")))
check("UC6 conflict_reason set", bool(_dc.get("conflict_reason")), str(_dc.get("conflict_reason")))
_res = (_dc.get("references") or {}).get("resolutions") or []
check("UC6 resolutions carry per-reference story",
	_res and all(set(("kind", "value", "target", "strength")).issubset(r) for r in _res), str(_res))
check("UC6 story maps e-way value → A (exact)",
	any(r["value"] == "582012827096" and r["target"] == "INV2627-00311" and r["strength"] == "exact" for r in _res), str(_res))
FRAPPE.get_all = _orig

# =================================================================== UC7 / UC8

print("\n== UC7/UC8: e2e — conflict reason reaches I2A Run.result_json; run Needs Review ==")
setup_world(mode="Manual")
_orig = use_rows([SI_A, SI_B])
FRAPPE.get_doc("I2A Action", "LR Extraction").match_config = json.dumps(cfg(candidate_query=[EWAY_Q, BILL_Q]))
_e2e, _sm = run_engine({
	"extract": [full_extraction(eway_bills=[make_item("582012827096", bbox=[500, 100, 530, 300])], bill_numbers=[])],
	"verify": [{"disagreements": []}],
	"match": [{"matches": [{"target": "SOI2627-00327", "confidence": 0.95, "reason": "bill number"}]}],
}, mode="Manual")
check("UC8 run status Needs Review (engine treats conflict as unresolved)", _e2e["status"] == "Needs Review", str(_e2e.get("status")))
_run_doc = FRAPPE.get_doc("I2A Run", _e2e["run"])
_rj = json.loads(_run_doc.result_json or "{}")
_e2e_match = _rj.get("match") or {}
check("UC7 result_json.match carries conflict_reason", bool(_e2e_match.get("conflict_reason")), str(_e2e_match.get("conflict_reason")))
check("UC7 result_json conflict_reason names the exact-proof invoice A", "INV2627-00311" in (_e2e_match.get("conflict_reason") or ""), str(_e2e_match.get("conflict_reason")))
FRAPPE.get_all = _orig

# essdee surface: the SAME payload → essdee ocr._match_note yields the reason
_note = ocr._match_note({"match": _e2e_match})
check("UC7 essdee _match_note returns the engine conflict reason", _note == _e2e_match.get("conflict_reason"), str(_note))
# ambiguity payload → joined ambiguity reasons; clean payload → None
_amb_payload = {"match": {"references": {"issues": [
	{"type": "ambiguous", "reason": "bill number '327' matches 2 records"}]}}}
check("UC7 _match_note joins ambiguity reasons when no conflict_reason",
	ocr._match_note(_amb_payload) == "bill number '327' matches 2 records", str(ocr._match_note(_amb_payload)))
check("UC7 _match_note None on a clean payload", ocr._match_note({"match": {"references": {"issues": []}}}) is None)

# =================================================================== UC9

print("\n== UC9: consistent multi-invoice LR still auto-applies BOTH (no conflict) ==")
# 2 exact EWB {A,E} + 2 bill tails that suffix-PAIR to A and E → union == expected
refs9 = match.resolve_references(cfg(),
	fields(eway=["582012827096", "111111111111"], bills=["00311", "00312"]), [SI_A, SI_E])
check("UC9 NOT a conflict (references agree)", refs9["conflict"] is False, str(refs9))
check("UC9 tails resolved as paired (claim no new record)",
	all(r["strength"] == "paired" for r in refs9["resolutions"] if r["value"] in ("00311", "00312")), str(refs9["resolutions"]))
check("UC9 union == expected (2)", len(refs9["union"]) == refs9["expected"] == 2, str(refs9))

# _deterministic_resolve auto-applies BOTH exact-key invoices, exactly as before
_apply9 = []


def _fn_apply9(**kw):
	_apply9.append(kw.get("sales_invoice"))
	return {"applied": True, "invoice": kw.get("sales_invoice")}


FRAPPE.get_attr = lambda path: {"x.apply": _fn_apply9}.get(path)
FRAPPE.whitelisted = [_fn_apply9]
FRAPPE.has_permission = lambda *a, **k: True
_orig = use_rows([SI_A, SI_E])
_gv = FRAPPE.db.get_value
_uc9_rows = {"INV2627-00311": dict(SI_A), "INV2627-00312": dict(SI_E)}
FRAPPE.db.get_value = lambda dt, name, f=None, as_dict=False, **k: (_uc9_rows.get(name) if dt == "Sales Invoice" else _gv(dt, name, f, as_dict=as_dict, **k))
_act9 = build_action(cfg(candidate_query=[EWAY_Q, BILL_Q]), [APPLY_TOOL])
_st9 = StubState()
_dr9 = engine._deterministic_resolve(_st9, _act9,
	fields(eway=["582012827096", "111111111111"], bills=["00311", "00312"]), {}, tools.parse_catalog(_act9))
check("UC9 both exact-key invoices auto-applied",
	sorted(_dr9["applied_targets"]) == ["INV2627-00311", "INV2627-00312"], str(_dr9["applied_targets"]))
check("UC9 no reference_conflict step", not any(s["step"] == "reference_conflict" for s in _st9.steps) and _dr9.get("match", {}).get("status") != "conflict", str(_dr9.get("match", {}).get("status")))
FRAPPE.db.get_value = _gv
FRAPPE.get_all = _orig

# UC9b: an EXTRA bill tail with no exact key (lone C) — still NOT a conflict,
# and C is NOT auto-applied (only the exact-key A is)
print("\n== UC9b: extra EWB-less bill tail → review by coverage, not a conflict ==")
refs9b = match.resolve_references(cfg(),
	fields(eway=["582012827096"], bills=["00311", "00312", "00399"]), [SI_A, SI_E, SI_C])
check("UC9b not a conflict (legit EWB-less invoices)", refs9b["conflict"] is False, str(refs9b))
_apply9.clear()
_orig = use_rows([SI_A, SI_E, SI_C])
_uc9b_rows = {"INV2627-00311": dict(SI_A)}
_gv = FRAPPE.db.get_value
FRAPPE.db.get_value = lambda dt, name, f=None, as_dict=False, **k: (_uc9b_rows.get(name) if dt == "Sales Invoice" else _gv(dt, name, f, as_dict=as_dict, **k))
FRAPPE.get_attr = lambda path: {"x.apply": _fn_apply9}.get(path)
FRAPPE.whitelisted = [_fn_apply9]
_dr9b = engine._deterministic_resolve(StubState(), _act9,
	fields(eway=["582012827096"], bills=["00311", "00312", "00399"]), {}, tools.parse_catalog(_act9))
check("UC9b only the exact-key A auto-applied (EWB-less tails go to review)",
	_dr9b["applied_targets"] == ["INV2627-00311"], str(_dr9b["applied_targets"]))
FRAPPE.db.get_value = _gv
FRAPPE.get_all = _orig

# =================================================================== UC10

print("\n== UC10: conflict detected across the UNION of candidate queries ==")
# strong-key query returns only A; the date-net query returns only B — the
# detector must run on the de-duped union of BOTH fetches (fetch_candidates)
_orig = use_rows_fifo([[SI_A], [SI_B]])
_act10 = build_action(cfg(candidate_query=[EWAY_Q, BILL_Q]))
_refs10 = match.check_conflict(_act10, fields(eway=["582012827096"], bills=["SOI2627-00327"]), {})
check("UC10 conflict found on the unioned fetch", _refs10 and _refs10["conflict"] is True, str(_refs10))
check("UC10 both fetched-apart records are in the reason",
	"INV2627-00311" in _refs10["reason"] and "SOI2627-00327" in _refs10["reason"], _refs10["reason"])
FRAPPE.get_all = _orig

# ------------------------------------------------------------------ summary
print(f"\n{'='*60}\nPASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
	print("FAILED:", *FAIL, sep="\n  - ")
	sys.exit(1)
print("ALL TESTS PASSED")
