"""Whitelisted surface of the I2A engine.

Consumers (essdee etc.) call `run_action` from server code or the client.
Permission model: the caller must have WRITE on the reference document —
no reference, System Manager only. Engine artifacts (I2A Run / I2A LLM
Call) are engine-owned and written with ignore_permissions internally.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint


@frappe.whitelist()
def run_action(action, files=None, context=None, mode=None, reference_doctype=None, reference_name=None, reference_detail=None):
	"""Run an I2A Action. `files` = JSON list of file URLs (or a single URL)."""
	if isinstance(files, str):
		try:
			files = json.loads(files)
		except ValueError:
			files = [files]
	if isinstance(context, str):
		context = json.loads(context) if context else {}
	if mode and mode not in ("Automated", "Manual"):
		frappe.throw(_("mode must be Automated or Manual"))

	if reference_doctype and reference_name:
		if not frappe.has_permission(reference_doctype, ptype="write", doc=reference_name):
			frappe.throw(_("Not permitted to run actions against {0} {1}").format(reference_doctype, reference_name), frappe.PermissionError)
		reference = (reference_doctype, reference_name)
	else:
		frappe.only_for("System Manager")
		reference = None

	from frappe_tools.i2a import engine

	return engine.run(
		action,
		files=files or [],
		context=context or {},
		mode=mode,
		reference=reference,
		reference_detail=reference_detail,
	)


@frappe.whitelist()
def get_run(name):
	"""Full trace of a run — permission-gated on the run's reference document."""
	run = frappe.get_doc("I2A Run", name)
	if run.reference_doctype and run.reference_name:
		if not frappe.has_permission(run.reference_doctype, ptype="read", doc=run.reference_name):
			frappe.throw(_("Not permitted"), frappe.PermissionError)
	else:
		frappe.only_for("System Manager")

	calls = frappe.get_all(
		"I2A LLM Call",
		filters={"run": name},
		fields=["name", "purpose", "ai_model", "status", "latency_ms", "total_tokens", "cost_usd", "error_message"],
		order_by="creation asc",
	)
	return {
		"run": run.as_dict(),
		"calls": calls,
	}


# ---------------------------------------------------------------- review screen

def _run_readable(run):
	"""Review permission = read on the run's reference document."""
	if run.reference_doctype and run.reference_name:
		if not frappe.has_permission(run.reference_doctype, ptype="read", doc=run.reference_name):
			frappe.throw(_("Not permitted"), frappe.PermissionError)
	else:
		frappe.only_for("System Manager")


@frappe.whitelist()
def get_review_queue(action=None):
	"""Runs awaiting a human — the review queue IS the engine's flagged runs.
	Newest run per reference; a reference whose latest run resolved drops out.
	Runs without a reference are excluded: there is no record to act on, so
	no review tool could ever execute (run_review_tool checks write permission
	on the reference)."""
	filters = {"status": "Needs Review", "reference_name": ["!=", ""]}
	if action:
		filters["action"] = action
	rows = frappe.get_all(
		"I2A Run",
		filters=filters,
		fields=["name", "action", "reference_doctype", "reference_name",
			"reference_detail", "creation", "total_cost_usd"],
		order_by="creation desc",
		limit=100,
	)
	latest, seen = [], set()
	for r in rows:
		key = (r.reference_doctype, r.reference_name, r.reference_detail)
		if key in seen:
			continue
		seen.add(key)
		# skip references whose NEWER run already resolved
		newer = frappe.get_all("I2A Run", filters={
			"reference_doctype": r.reference_doctype or "",
			"reference_name": r.reference_name or "",
			"reference_detail": r.reference_detail or "",
			"creation": [">", r.creation],
			"status": ["in", ["Completed"]],
		}, limit=1)
		if newer:
			continue
		r["title"] = _run_title(r.name, _queue_action(r.action)) or r.reference_detail or r.reference_name or r.name
		latest.append(r)
	return latest


def _queue_action(name):
	"""Cached per-request action doc for queue title lookups."""
	cache = getattr(frappe.local, "_i2a_queue_actions", None)
	if cache is None:
		cache = frappe.local._i2a_queue_actions = {}
	if name not in cache:
		try:
			cache[name] = frappe.get_doc("I2A Action", name)
		except Exception:
			cache[name] = None
	return cache[name]


def _run_title(run_name, action=None):
	"""Human queue label from the run's own extraction. Which field titles a
	document is CONFIG: the output_schema entry flagged "is_title" (falls back
	to the first non-empty extracted value) — no consumer field names here."""
	raw = frappe.db.get_value("I2A Run", run_name, "result_json")
	if not raw:
		return None
	try:
		fields = (json.loads(raw).get("fields") or {})
	except ValueError:
		return None
	title_keys = []
	if action is not None:
		try:
			title_keys = [f["key"] for f in action.parsed_schema() if f.get("is_title")]
		except Exception:
			title_keys = []
	for key in title_keys:
		entry = fields.get(key)
		item = entry[0] if isinstance(entry, list) and entry else entry
		if isinstance(item, dict) and item.get("value"):
			return str(item["value"])[:40]
	for entry in fields.values():
		items = entry if isinstance(entry, list) else [entry]
		for item in items:
			if isinstance(item, dict) and item.get("value"):
				return str(item["value"])[:40]
	return None


def _review_context(action, run, result):
	"""The eligibility context for review-time candidate lookups: the RUN's
	own persisted context when present; else the action's configured
	review_context offsets (e.g. {"date_from": "-60d", "date_to": "+3d"},
	relative to the run's creation). No config, no context → None (an
	unbounded candidate search is worse than none)."""
	ctx = result.get("context")
	if isinstance(ctx, dict) and ctx:
		return ctx
	mcfg = i2a_match.parse_config(action) or {}
	spec = mcfg.get("review_context")
	if not isinstance(spec, dict) or not spec:
		return None
	base = frappe.utils.getdate(run.creation)
	out = {}
	for key, offset in spec.items():
		try:
			days = int(str(offset).lower().replace("d", "").replace("+", ""))
			out[key] = str(frappe.utils.add_days(base, days))
		except (ValueError, TypeError):
			out[key] = str(offset)
	return out


@frappe.whitelist()
def get_review_run(name):
	"""Everything the review screen needs for one run: the engine's persisted
	result (fields + boxes + match/agent outcome + file urls) plus the action's
	human-triggerable tools (finalizes/escalates) from config."""
	run = frappe.get_doc("I2A Run", name)
	_run_readable(run)

	result = {}
	if run.result_json:
		try:
			result = json.loads(run.result_json)
		except ValueError:
			result = {}

	from frappe_tools.i2a import match as i2a_match
	from frappe_tools.i2a import tools as i2a_tools

	action = frappe.get_doc("I2A Action", run.action)
	catalog = i2a_tools.parse_catalog(action)
	actions = [{
		"name": t["name"],
		"description": t.get("description", ""),
		"kind": "finalize" if t.get("finalizes") else "escalate",
		"arg": (t.get("corroborate") or {}).get("arg"),
		"target_doctype": (t.get("corroborate") or {}).get("doctype"),
		"parameters": t.get("parameters") or {},
	} for t in catalog if t.get("finalizes") or t.get("escalates")]

	feedback = {}
	if run.get("feedback_json"):
		try:
			feedback = json.loads(run.feedback_json)
		except ValueError:
			feedback = {}

	# runs persisted before the candidates pass (or whose agent errored) have
	# no match block — compute the LLM-free candidates live so the reviewer
	# always sees scored records from the ERP
	if run.status == "Needs Review" and not ((result.get("match") or {}).get("matches")):
		try:
			ctx = _review_context(action, run, result)
			if ctx is not None:
				live = i2a_match.deterministic_candidates(action, result.get("fields") or {}, ctx)
				if live and live.get("matches"):
					result["match"] = live
		except Exception:
			pass

	mcfg = i2a_match.parse_config(action) or {}
	return {
		"run": {
			"name": run.name, "action": run.action, "status": run.status,
			"reference_doctype": run.reference_doctype, "reference_name": run.reference_name,
			"reference_detail": run.reference_detail, "rounds_used": run.rounds_used,
			"total_cost_usd": run.total_cost_usd, "started_at": run.started_at,
			"error_message": run.error_message,
		},
		"result": result,
		"actions": actions,
		"target_doctype": mcfg.get("target_doctype"),
		"feedback": feedback,
		"array_fields": [f["key"] for f in action.parsed_schema() if f.get("kind") == "array"],
	}


@frappe.whitelist(methods=["POST"])
def run_review_tool(name, tool, args=None, complete=None):
	"""Human-triggered execution of one of the action's declared tools against
	a Needs-Review run — the reviewer IS the authority, so this is how a human
	confirms (apply tool) or dismisses (flag tool) what the engine escalated.

	Guards: write permission on the run's reference document; the tool must be
	declared finalizes/escalates in the action's catalog (a human can never
	invoke an arbitrary method either); the trusted context comes from the RUN,
	never from the request."""
	run = frappe.get_doc("I2A Run", name)
	if run.reference_doctype and run.reference_name:
		if not frappe.has_permission(run.reference_doctype, ptype="write", doc=run.reference_name):
			frappe.throw(_("Not permitted"), frappe.PermissionError)
	else:
		frappe.only_for("System Manager")
	if run.status != "Needs Review":
		frappe.throw(_("Run {0} is not awaiting review (status: {1})").format(name, run.status))

	from frappe_tools.i2a import tools as i2a_tools

	action = frappe.get_doc("I2A Action", run.action)
	catalog = i2a_tools.parse_catalog(action)
	tool_def = next((t for t in catalog if t["name"] == tool), None)
	if not tool_def or not (tool_def.get("finalizes") or tool_def.get("escalates")):
		frappe.throw(_("Tool {0} is not a reviewable action on {1}").format(tool, run.action))

	if isinstance(args, str):
		args = json.loads(args or "{}")

	result = {}
	if run.result_json:
		try:
			result = json.loads(run.result_json)
		except ValueError:
			result = {}
	feedback = {}
	if run.get("feedback_json"):
		try:
			feedback = json.loads(run.feedback_json)
		except ValueError:
			feedback = {}
	context = {
		"reference_doctype": run.reference_doctype,
		"reference_name": run.reference_name,
		"reference_detail": run.reference_detail,
		"fields": _overlay_corrections({k: v for k, v in (result.get("fields") or {}).items()}, feedback),
		"context": {},
	}
	outcome = i2a_tools.execute(catalog, tool, args or {}, context)
	ok = not (isinstance(outcome, dict) and "error" in outcome)
	# One document can resolve into SEVERAL records (an LR carries many bill
	# numbers, each belonging to its own invoice) — a finalizing tool may run
	# once per record. The run closes when the CALLER says the review is done
	# (complete=1, the default for single-target reviews); complete=0 keeps it
	# open for the next apply.
	want_complete = cint(complete) if complete is not None else 1
	if ok and tool_def.get("finalizes") and want_complete:
		frappe.db.set_value("I2A Run", name, {
			"status": "Completed",
			"error_message": "",
		}, update_modified=False)
		frappe.db.commit()
	# the HUMAN's resolution trail: what the AI gave is in result_json, the
	# reviewer's verdicts in feedback_json — this appends which tool the human
	# ran, on what, and how it ended (a list: one entry per apply/flag).
	resolution = {
		"tool": tool, "args": args or {}, "ok": ok,
		"kind": "finalize" if tool_def.get("finalizes") else "escalate",
		"by": frappe.session.user, "at": frappe.utils.now(),
	}
	feedback.setdefault("__resolutions__", []).append(resolution)
	frappe.db.set_value("I2A Run", name, "feedback_json",
		json.dumps(feedback, default=str), update_modified=False)
	frappe.db.commit()
	_publish_feedback(run, "__resolution__", None, resolution)
	return {"ok": ok, "outcome": outcome, "completed": bool(ok and tool_def.get("finalizes") and want_complete)}


@frappe.whitelist(methods=["POST"])
def complete_review(name):
	"""Close a multi-reference review: when a document resolves into several
	records (one per bill number), each apply keeps the run open — this marks
	the review finished once the reviewer has handled every reference."""
	run = frappe.get_doc("I2A Run", name)
	if run.reference_doctype and run.reference_name:
		if not frappe.has_permission(run.reference_doctype, ptype="write", doc=run.reference_name):
			frappe.throw(_("Not permitted"), frappe.PermissionError)
	else:
		frappe.only_for("System Manager")
	if run.status != "Needs Review":
		frappe.throw(_("Run {0} is not awaiting review").format(name))
	frappe.db.set_value("I2A Run", name, {"status": "Completed", "error_message": ""},
		update_modified=False)
	# the human's close decision is part of the trail — an audit must be able
	# to tell "engine resolved everything" from "reviewer closed it"
	feedback = {}
	if run.get("feedback_json"):
		try:
			feedback = json.loads(run.feedback_json)
		except ValueError:
			feedback = {}
	resolution = {"tool": "__complete_review__", "args": {}, "ok": True,
		"kind": "finalize", "by": frappe.session.user, "at": frappe.utils.now()}
	feedback.setdefault("__resolutions__", []).append(resolution)
	frappe.db.set_value("I2A Run", name, "feedback_json",
		json.dumps(feedback, default=str), update_modified=False)
	frappe.db.commit()
	_publish_feedback(run, "__resolution__", None, resolution)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def record_field_feedback(name, field, verdict, index=None):
	"""Reviewer's per-field verdict on a run's extracted values — the human
	telling the engine 'this value is correct / wrong'. Stored on the run
	(feedback_json) as ground truth for audit and future distillation.

	verdict: 'correct' | 'wrong' | 'clear' (clear removes the verdict)."""
	if verdict not in ("correct", "wrong", "clear"):
		frappe.throw(_("Invalid verdict {0}").format(verdict))
	if not field or len(str(field)) > 140:
		frappe.throw(_("Invalid field"))
	run = frappe.get_doc("I2A Run", name)
	if run.reference_doctype and run.reference_name:
		if not frappe.has_permission(run.reference_doctype, ptype="write", doc=run.reference_name):
			frappe.throw(_("Not permitted"), frappe.PermissionError)
	else:
		frappe.only_for("System Manager")

	feedback = {}
	if run.get("feedback_json"):
		try:
			feedback = json.loads(run.feedback_json)
		except ValueError:
			feedback = {}

	key = f"{field}:{index}" if index not in (None, "") else field
	if verdict == "clear":
		feedback.pop(key, None)
	else:
		feedback[key] = {
			"verdict": verdict,
			"by": frappe.session.user,
			"at": frappe.utils.now(),
		}
	frappe.db.set_value("I2A Run", name, "feedback_json",
		json.dumps(feedback, default=str), update_modified=False)
	frappe.db.commit()
	_publish_feedback(run, field, index, feedback.get(key) or {"verdict": "clear"},
		model_value=_model_value(run, field, index))
	return {"ok": True, "feedback": feedback}


@frappe.whitelist(methods=["POST"])
def correct_field_value(name, field, value, index=None):
	"""Reviewer's correction of an extracted value — the human's reading
	REPLACES the model's for every downstream action (run_review_tool overlays
	corrections onto the trusted context before executing a tool). The model's
	original stays in result_json untouched; the correction lives in
	feedback_json as verdict='corrected' — audit trail and training signal."""
	if not field or len(str(field)) > 140:
		frappe.throw(_("Invalid field"))
	value = "" if value is None else str(value)
	if len(value) > 500:
		frappe.throw(_("Value too long"))
	run = frappe.get_doc("I2A Run", name)
	if run.reference_doctype and run.reference_name:
		if not frappe.has_permission(run.reference_doctype, ptype="write", doc=run.reference_name):
			frappe.throw(_("Not permitted"), frappe.PermissionError)
	else:
		frappe.only_for("System Manager")

	feedback = {}
	if run.get("feedback_json"):
		try:
			feedback = json.loads(run.feedback_json)
		except ValueError:
			feedback = {}
	key = f"{field}:{index}" if index not in (None, "") else field
	feedback[key] = {
		"verdict": "corrected",
		"corrected_value": value,
		"by": frappe.session.user,
		"at": frappe.utils.now(),
	}
	frappe.db.set_value("I2A Run", name, "feedback_json",
		json.dumps(feedback, default=str), update_modified=False)
	frappe.db.commit()
	_publish_feedback(run, field, index, feedback[key],
		model_value=_model_value(run, field, index))
	return {"ok": True, "feedback": feedback}


@frappe.whitelist(methods=["POST"])
def add_field_value(name, field, value):
	"""Reviewer ADDS a value the model missed (array fields only — e.g. a
	second bill number handwritten on the LR). Stored on the run and mirrored
	to the consumer via the same feedback hook; Apply sees it like any other
	extracted value."""
	if not field or len(str(field)) > 140:
		frappe.throw(_("Invalid field"))
	value = ("" if value is None else str(value)).strip()
	if not value or len(value) > 500:
		frappe.throw(_("Invalid value"))
	run = frappe.get_doc("I2A Run", name)
	if run.reference_doctype and run.reference_name:
		if not frappe.has_permission(run.reference_doctype, ptype="write", doc=run.reference_name):
			frappe.throw(_("Not permitted"), frappe.PermissionError)
	else:
		frappe.only_for("System Manager")

	feedback = {}
	if run.get("feedback_json"):
		try:
			feedback = json.loads(run.feedback_json)
		except ValueError:
			feedback = {}
	added = feedback.setdefault("__added__", [])
	if len(added) >= 40:
		frappe.throw(_("Too many added values"))
	entry = {"field": field, "value": value,
		"by": frappe.session.user, "at": frappe.utils.now()}
	added.append(entry)
	frappe.db.set_value("I2A Run", name, "feedback_json",
		json.dumps(feedback, default=str), update_modified=False)
	frappe.db.commit()
	_publish_feedback(run, field, None, {"verdict": "added", "corrected_value": value,
		"by": entry["by"], "at": entry["at"]})
	return {"ok": True, "feedback": feedback}


def _model_value(run, field, index):
	"""The AI's original value at (field, index) from the persisted result."""
	if not run.get("result_json"):
		return None
	try:
		entry = (json.loads(run.result_json).get("fields") or {}).get(field)
	except ValueError:
		return None
	if isinstance(entry, list):
		try:
			entry = entry[int(index)] if index not in (None, "") else None
		except (ValueError, IndexError):
			entry = None
	return entry.get("value") if isinstance(entry, dict) else None


def _publish_feedback(run, field, index, entry, model_value=None):
	"""App hook `i2a_review_feedback`: consumer apps mirror reviewer verdicts
	into their own tables (audit pages, accuracy reports). The engine stays
	generic — it publishes what happened on a run; it never writes consumer
	rows itself. Hook failures are logged, never fatal: the run-level record
	(feedback_json) is already stored."""
	for method in frappe.get_hooks("i2a_review_feedback") or []:
		try:
			frappe.get_attr(method)(
				run=run.name, action=run.action,
				reference_doctype=run.reference_doctype,
				reference_name=run.reference_name,
				reference_detail=run.reference_detail,
				field=field, index=index, entry=entry, model_value=model_value,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(),
				f"i2a_review_feedback hook failed: {method}")


def _overlay_corrections(fields, feedback):
	"""Reviewer input wins: patch corrected values into a fields dict (loaded
	fresh from result_json, safe to mutate) and append reviewer-ADDED values
	to their array fields before tools run."""
	for fkey, fb in (feedback or {}).items():
		if not isinstance(fb, dict) or fb.get("verdict") != "corrected":
			continue
		field, _, idx = str(fkey).partition(":")
		entry = fields.get(field)
		if entry is None and not idx:
			# the model returned nothing for this scalar — the human's reading
			# IS the value (this is exactly the field whose absence caused the
			# review); it must materialize, not silently vanish
			fields[field] = {"value": fb.get("corrected_value"),
				"raw_text": fb.get("corrected_value"), "confidence": None,
				"bbox": None, "corrected_by_reviewer": 1}
			continue
		if entry is None:
			continue
		if isinstance(entry, list):
			try:
				item = entry[int(idx)]
			except (ValueError, IndexError):
				continue
		else:
			item = entry
		if isinstance(item, dict):
			item["value"] = fb.get("corrected_value")
			item["corrected_by_reviewer"] = 1
		elif item is None and not isinstance(entry, list):
			fields[field] = {"value": fb.get("corrected_value"),
				"raw_text": fb.get("corrected_value"), "confidence": None,
				"bbox": None, "corrected_by_reviewer": 1}
	for add in (feedback or {}).get("__added__") or []:
		if not isinstance(add, dict) or not add.get("value"):
			continue
		entry = fields.setdefault(add.get("field"), [])
		if isinstance(entry, list):
			entry.append({"value": add["value"], "raw_text": add["value"],
				"confidence": None, "bbox": None, "added_by_reviewer": 1})
	return fields
