"""The I2A engine loop: ROUTE → BUILD → EXECUTE → VERIFY → REPAIR → GATE → LOG.

Deterministic code owns control flow; models are bounded tools consulted at
judgment points. Every model call is an `I2A LLM Call` row (request +
response + cost); every decision between the calls lands in the run's
steps_json — a run is fully reconstructable after the fact.
"""

import json
import math
import time

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from frappe_tools.i2a import extract, ground, match, providers, tools, verify

MAX_VERIFY_CROPS = 12  # per-claim crops attached to the verify pass (token bound)


BudgetExceeded = providers.BudgetExceeded  # canonical home: providers (import-cycle-free)


def run(action_name, files=None, context=None, mode=None, reference=None, reference_detail=None):
	"""Execute one engine run. Returns a dict:

	{"run": <I2A Run name>, "status": ..., "mode": ..., "fields": {key: item|[items]},
	 "deficiencies": [...unresolved...], "rounds": int}

	Field items carry: value, raw_text, confidence, bbox ({x,y,w,h} 0..1),
	cross_check (matched/miss/None), repaired (0/1), status (Approved/Pending).
	"""
	action = frappe.get_doc("I2A Action", action_name)
	if not cint(action.enabled):
		frappe.throw(_("I2A Action {0} is disabled").format(action_name))

	effective_mode = mode or action.mode or "Manual"
	ref_doctype, ref_name = reference if reference else (None, None)

	# Cross-worker in-flight guard: Redis NX lock (auto-expires). A DB commit
	# here would break the CALLER's transaction atomicity — never commit inside
	# the engine. The stale-Running-row cleanup below is the backstop.
	lock_key = _run_lock_key(action_name, ref_doctype, ref_name, reference_detail)
	if ref_name and not _acquire_run_lock(lock_key, action):
		frappe.throw(_("An I2A run is already in flight for this target — try again shortly."))

	_mark_stale_runs(action_name, ref_doctype, ref_name, reference_detail, action)

	run_doc = frappe.new_doc("I2A Run")
	run_doc.update({
		"action": action_name,
		"status": "Running",
		"mode": effective_mode,
		"reference_doctype": ref_doctype or "",
		"reference_name": ref_name or "",
		"reference_detail": reference_detail or "",
		"started_at": now_datetime(),
	})
	run_doc.flags.ignore_permissions = True
	run_doc.insert(ignore_permissions=True)

	state = _State(action, run_doc, effective_mode)
	try:
		result = _run_inner(state, action, files or [], context or {})
		run_doc.status = result["status"]
		# what the (engine-owned) review screen renders — no images, just
		# values/boxes/outcomes + the input file references
		state.final_result = {
			"status": result["status"],
			"fields": result.get("fields"),
			"match": result.get("match"),
			"agent": result.get("agent"),
			"files": list(files or []),
			# the run's eligibility context (e.g. the batch date window) — the
			# review screen re-runs candidate lookups with the SAME horizon
			"context": {k: str(v) for k, v in (context or {}).items()
				if isinstance(v, (str, int, float)) or hasattr(v, "isoformat")},
		}
	except Exception as exc:
		run_doc.status = "Failed"
		run_doc.error_message = str(exc)[:500]
		state.step("fatal", error=str(exc)[:500])
		result = {
			"run": run_doc.name, "status": "Failed", "mode": effective_mode,
			"fields": {}, "deficiencies": [], "rounds": state.rounds, "error": str(exc),
		}
	finally:
		_finalize(run_doc, state)
		if ref_name:
			_release_run_lock(lock_key)

	return result


# ------------------------------------------------------------------ inner

def _run_inner(state, action, files, context):
	schema = action.parsed_schema()
	image_parts = [extract.file_to_image_part(f) for f in files]
	# verify re-reads the SAME page up to max_rounds times; a 1024px low-detail
	# copy is enough for overview (the per-claim CROPS carry the fine reading)
	# and costs a fraction of the tile-priced tokens. Extraction keeps the
	# full-resolution parts — reading fine print is ITS job.
	verify_parts = [extract.shrink_image_part(p, detail="low") for p in image_parts]
	gctx = _grounding(state, action, files)

	executor, verifier, request_notes = _route(state, action, image_parts)
	state.executor_doc = executor

	extraction_raw = state.chat(executor, "extract", session="executor",
		seed=verify.build_extract_messages(action, image_parts, request_notes))

	fields, dropped = verify.whitelist(extraction_raw, schema)
	if dropped:
		state.step("whitelist", dropped_keys=dropped)
	_snap_boxes(state, fields, gctx)

	# When a tool catalog exists, the agentic resolution IS the point of the
	# run — reserve calls for it so a churning verify↔repair loop on a rough
	# scan can never starve it of budget.
	if tools.parse_catalog(action):
		state.reserved_calls = min(3, max(1, (cint(action.max_calls_per_run) or 12) // 4))

	unresolved = []
	prev_fingerprint = None
	while True:
		deficiencies = []
		deficiencies += verify.apply_formats(fields, schema)
		deficiencies += verify.deterministic_check(fields, schema)
		deficiencies += verify.cross_check(fields, schema, context)

		# Round 0 audits every claim. Later rounds re-audit ONLY what the
		# repair touched: untouched fields already passed the full pass, and
		# the deterministic checks above still cover every field every round.
		#
		# skip_model_verify (config): omit the LLM re-read of the image entirely.
		# For a trusted single-pass extractor the second-opinion isn't worth its
		# cost (re-sending the image is the dominant token spend), and write
		# safety does NOT rest on it — the deterministic corroboration gate
		# guards every auto-apply regardless. Deterministic checks above still
		# run every round.
		if not cint(getattr(action, "skip_model_verify", 0)):
			only = getattr(state, "last_repair_targets", None) if state.rounds else None
			model_disagreements = _model_verify(state, action, verifier, verify_parts, fields, gctx, only=only)
			deficiencies += model_disagreements

		state.step(
			"verify",
			round=state.rounds,
			deficiencies=[{
				"field": d.get("field"), "index": d.get("index"), "kind": d.get("kind"),
				"detail": str(d.get("detail"))[:300],
			} for d in deficiencies],
		)

		if not deficiencies:
			unresolved = []
			break

		fingerprint = _fingerprint(deficiencies, fields, gctx)
		if fingerprint == prev_fingerprint:
			state.step("no_progress", round=state.rounds, note="identical deficiency set — stopping repair")
			unresolved = deficiencies
			break
		prev_fingerprint = fingerprint

		if not state.budget_ok(reason_step="budget_stop"):
			unresolved = deficiencies
			break
		if state.rounds >= cint(action.max_rounds or 4):
			state.step("max_rounds", rounds=state.rounds)
			unresolved = deficiencies
			break

		state.rounds += 1
		# cross_check_miss is ADVISORY: "not in the ERP" usually means the
		# record doesn't exist, not that the model misread — re-asking invites
		# the model to corrupt a correct value. It still blocks auto-approval.
		repairable = [
			d for d in deficiencies if d["kind"] not in ("cross_check_error", "cross_check_miss")
		]
		if not repairable:
			unresolved = deficiencies
			break
		state.last_repair_targets = {(d["field"], d.get("index")) for d in repairable}
		_repair(state, action, executor, verifier, image_parts, fields, repairable, gctx)

	# EXECUTE: resolve the document against the ERP autonomously.
	#   - Tool catalog present  → agentic loop: the model calls the exposed
	#     tools (search / cross-check / apply) to verify and finalise itself.
	#   - Else match_config      → the built-in reconcile phase (legacy path).
	catalog = tools.parse_catalog(action)
	match_result = None
	agent_result = None
	if catalog:
		state.reserved_calls = 0  # the reservation was FOR this phase — release it
		try:
			agent_result = _agentic_phase(state, action, executor, fields, context, catalog)
		except BudgetExceeded as exc:
			state.step("budget_stop", at="agent", note=str(exc)[:200])
		if not (agent_result and agent_result.get("resolved")):
			# the agent couldn't finalize — still give the reviewer scored
			# candidates from the ERP (config queries + corroborate rules,
			# zero model calls) so the review screen shows what the data says
			try:
				match_result = match.deterministic_candidates(action, fields, context)
				if match_result:
					state.step("candidates", count=len(match_result.get("matches") or []))
			except Exception as exc:
				state.step("candidates_failed", error=str(exc)[:200])
	else:
		try:
			match_result = match.run_match(state, action, executor, fields, context)
		except BudgetExceeded as exc:
			state.step("budget_stop", at="match", note=str(exc)[:200])

	match_unresolved = bool(match_result and match_result.get("status") in ("doubt", "none", "error"))
	agent_unresolved = bool(agent_result and not agent_result.get("resolved"))

	verdict = _gate(state, fields, schema, unresolved)
	status = "Completed" if not (unresolved or match_unresolved or agent_unresolved) else "Needs Review"
	state.verdict = verdict

	return {
		"run": state.run_doc.name,
		"status": status,
		"mode": state.mode,
		"fields": fields,
		"deficiencies": unresolved,
		"match": match_result,
		"agent": agent_result,
		"rounds": state.rounds,
	}


def _corroborate_write(action, tool_def, args, fields, context=None):
	"""Deterministic exact-key gate for a finalizing write tool.

	Tool config: "corroborate": {"arg": <arg holding the record name>,
	"doctype": <target doctype>}. The rules themselves come from the action's
	match_config "corroborate" list (shared with the match phase). Returns
	None only when the tool never declared gating; a DECLARED gate with
	broken/missing config FAILS CLOSED (config drift must never silently
	un-gate a write). match_config "eligibility" additionally bounds the
	record by a context window (e.g. posting_date within date_from/date_to) —
	recency as a deterministic check, not prompt prose.
	"""
	if not tool_def.get("finalizes"):
		return None
	spec = tool_def.get("corroborate") or {}
	if not spec:
		return None  # tool never declared gating
	arg, doctype = spec.get("arg"), spec.get("doctype")
	mcfg = match.parse_config(action) or {}
	rules = mcfg.get("corroborate")
	if not (arg and doctype and rules):
		return False  # declared gate, broken config → fail closed
	name = (args or {}).get(arg)
	if not name:
		return False
	elig = mcfg.get("eligibility") or {}
	want = ["name"] + [r["target_field"] for r in rules if r.get("target_field")]
	if elig.get("field"):
		want.append(elig["field"])
	row = frappe.db.get_value(doctype, name, list(dict.fromkeys(want)), as_dict=True)
	if not row:
		return False
	if elig.get("field"):
		val = str(row.get(elig["field"]) or "")
		lo = str((context or {}).get(elig.get("context_from") or "") or "")
		hi = str((context or {}).get(elig.get("context_to") or "") or "")
		if val and ((lo and val < lo) or (hi and val > hi)):
			return False  # outside the configured eligibility window
	# for_gate: numeric_suffix alone must NOT authorize an autonomous write
	# (cross-series suffix collision) unless the action opts in.
	return bool(match.is_corroborated(row, mcfg, fields, for_gate=True))


def _agentic_phase(state, action, executor, fields, context, catalog):
	"""Tool-calling loop: the model uses the action's exposed tools to verify
	and resolve the extracted document itself (search ERP → apply), replacing
	human review. Every tool call is logged; write tools carry the run's
	trusted reference and are permission-checked in tools.execute."""
	specs = tools.function_specs(catalog)
	values = verify._values_only(fields)
	tool_context = {
		"reference_doctype": state.run_doc.reference_doctype,
		"reference_name": state.run_doc.reference_name,
		"reference_detail": state.run_doc.reference_detail,
		"fields": values,
		"context": context or {},
	}
	# agent_text_only (config): the resolving "brain" runs on TEXT ONLY — it
	# never carries the document image, so the matching loop costs a fraction of
	# an image-bearing one. When it genuinely needs something the extraction did
	# not capture, it calls ask_document, which the engine routes to the vision
	# model on the image-bearing session (a cheap follow-up, image already
	# cached). Off (default) → the brain continues the vision session as before.
	text_only = cint(getattr(action, "agent_text_only", 0)) and "executor" in state.sessions
	# Generic mechanics ONLY — what counts as sufficient evidence, and any
	# domain wording, comes from the action's agent_instructions config.
	system = verify._join(
		"You resolve an already-extracted document against the ERP using ONLY the tools provided.",
		"The extracted fields are given below. Use the read tools to find the correct record(s), then "
		"apply with the write tool — but ONLY when the evidence criteria in your instructions are met. "
		"If a reference cannot be confidently resolved, do NOT apply a guess — use the tool that flags "
		"it for human review instead. Every document ends by applying, flagging, or both (applied "
		"references stay applied; flag covers the rest).",
		("You CANNOT see the document image. When a value is missing, ambiguous, or you need to "
			"re-read something from the document, call ask_document with a specific question — a vision "
			"model that can see the image will answer." if text_only else None),
		"Do NOT re-extract or repeat the fields. When finished, reply with ONE short plain-text "
		"sentence describing what you did (which records you applied and on what evidence) and "
		"make NO further tool call.",
		(getattr(action, "agent_instructions", None) or None),
	)
	if text_only:
		# fresh text conversation — no image, not the vision session
		specs = specs + [{
			"type": "function",
			"function": {
				"name": "ask_document",
				"description": "Ask the vision model a specific question about the document image "
					"(e.g. 'what customer name is printed near the consignee box?'). Use when the "
					"extracted fields are missing or unclear.",
				"parameters": {"type": "object", "required": ["question"], "properties": {
					"question": {"type": "string", "description": "A specific question about the document image"}}},
			},
		}]
		messages = [
			{"role": "system", "content": system},
			{"role": "user", "content": "Extracted document fields:\n" + json.dumps(values, default=str, indent=1)},
		]
	else:
		# Continue the executor's per-run session: the model already holds the
		# document image + its own extraction in-context (prompt-cached), so the
		# resolution directive rides as one text turn. Local copy — the tool loop
		# appends its own messages without polluting the session.
		messages = [
			*state.sessions.get("executor", []),
			{"role": "user", "content": verify._join(
				system, "Extracted document fields:\n" + json.dumps(values, default=str, indent=1))},
		]

	calls_made = []
	applied = False   # a finalizing write succeeded (e.g. apply_lr_to_invoice)
	applied_targets = []  # every record a finalizing write landed on (one per reference)
	flagged = False   # the agent escalated to a human (e.g. flag_for_review)
	clean_finish = False  # the MODEL declared it was done (or deliberately escalated)
	summary = ""
	max_tool_rounds = max(2, cint(action.max_rounds or 4) * 2)
	for _round in range(max_tool_rounds):
		try:
			resp = state.call_tools(executor, messages, specs, purpose="agent")
		except BudgetExceeded as exc:
			# Do NOT re-raise: a budget stop AFTER a partial apply must still
			# return the partial result (applied_targets + resolved=False) so
			# the run goes to review and the writes already made are recorded —
			# re-raising left agent_result None and the run wrongly Completed.
			state.step("budget_stop", at="agent", note=str(exc)[:200])
			break
		except providers.ProviderError as exc:
			state.step("agent_call_failed", error=str(exc)[:200])
			break
		msg = resp.get("message") or {"role": "assistant", "content": resp.get("content", "")}
		messages.append(msg)
		tcs = resp.get("tool_calls") or []
		if not tcs:
			summary = resp.get("content") or ""
			clean_finish = True
			state.step("agent_done", summary=summary[:300], applied=applied, flagged=flagged)
			break
		for tc in tcs:
			# ask_document: the text brain queries the vision model about the
			# image. Routed to the executor's image-bearing session (cheap
			# follow-up), never a catalog method — so it bypasses the tool gate.
			if text_only and tc["name"] == "ask_document":
				q = (tc.get("arguments") or {}).get("question") or ""
				try:
					ans = state.chat(executor, "ask_document", session="executor",
						content="Answer this question about the document image ONLY, concisely: " + str(q)[:500])
					result = {"answer": ans}
				except providers.ProviderError as exc:
					result = {"error": f"vision model unavailable: {str(exc)[:150]}"}
				calls_made.append({"tool": "ask_document", "ok": "error" not in result})
				messages.append({"role": "tool", "tool_call_id": tc.get("id"),
					"content": json.dumps(result, default=str)[:4000]})
				continue
			tool_def = next((t for t in catalog if t["name"] == tc["name"]), {})
			# Manual mode NEVER autonomously writes: a finalizing tool is refused
			# (the model is told to flag instead) so a non-Automated batch only
			# ever produces review candidates, never an invoice write.
			if tool_def.get("finalizes") and state.mode != "Automated":
				result = {"error": "manual mode: do not apply autonomously — call the "
					"review/flag tool so a human confirms this match"}
			# Once an escalation landed, no further FINALIZING write may run in
			# the same batch — a parallel [flag, apply] emission must not let the
			# apply clobber the human handoff the flag just recorded.
			elif flagged and tool_def.get("finalizes"):
				result = {"error": "skipped: this document was already escalated to a human "
					"in this round — no further applies"}
			else:
				# Deterministic gate on finalizing writes: the model's confidence is
				# NOT an enforcement boundary — the chosen record must share an exact
				# key with the extracted document (prompt-injected text in a scanned
				# image must never be able to steer an apply to a foreign record).
				gate = _corroborate_write(action, tool_def, tc["arguments"], fields, context)
				if gate is False:
					result = {"error": "corroboration failed: the chosen record shares no exact key "
						"with this document (or is outside the eligibility window) — do NOT apply; "
						"use the review/flag tool instead"}
				else:
					result = tools.execute(catalog, tc["name"], tc["arguments"], tool_context)
			# a tool succeeds unless it returned a dict carrying an "error"
			errored = isinstance(result, dict) and "error" in result
			ok = not errored
			# `finalizes` and `escalates` are declared per tool in config —
			# "write" alone must NOT mean resolved (flagging is also a write).
			if ok and tool_def.get("finalizes"):
				applied = True
				arg = (tool_def.get("corroborate") or {}).get("arg")
				target = (tc["arguments"] or {}).get(arg) if arg else None
				if target and target not in applied_targets:
					applied_targets.append(target)
			if ok and tool_def.get("escalates"):
				flagged = True
			calls_made.append({"tool": tc["name"], "ok": ok,
				"detail": (result.get("error") if errored else "ok")})
			messages.append({
				"role": "tool", "tool_call_id": tc.get("id"),
				"content": json.dumps(result, default=str)[:8000],
			})
		state.step("agent_round", round=_round,
			calls=[{"tool": c["tool"], "ok": c["ok"]} for c in calls_made[-len(tcs):]])
		if flagged:
			# Escalation is terminal — the human owns it from here; closing
			# prose from the model is not worth another LLM call.
			summary = "flagged for review"
			clean_finish = True
			state.step("agent_done", summary=summary, applied=applied, flagged=flagged,
				note="escalated — loop ended without a closing model call")
			break
		# an APPLY is NOT terminal: one document resolves into several records
		# (an LR carries many bill numbers, each belonging to its own invoice)
		# — the loop continues so the model can resolve the next reference.
	else:
		state.step("agent_max_rounds", rounds=max_tool_rounds, applied=applied, flagged=flagged)

	if applied and not summary:
		summary = f"applied to {', '.join(applied_targets)}" if applied_targets else "applied"

	# Resolved ONLY on a CLEAN model-declared finish with applies and no
	# escalation. An abnormal exit (provider failure, round cap) after a
	# partial apply must NOT complete the run — the remaining references
	# would silently vanish; Needs Review keeps a human on them.
	return {
		"resolved": applied and not flagged and clean_finish,
		"applied": applied,
		"applied_targets": applied_targets,
		"flagged": flagged,
		"clean_finish": clean_finish,
		"tool_calls": calls_made,
		"summary": summary or ("agent stopped before finishing — needs review" if applied else ""),
	}


def _grounding(state, action, files):
	"""Per-run visual-grounding context (all three features are action-level
	config toggles; everything degrades gracefully and is logged).

	pil: the first file as a PIL image (crops + Set-of-Marks need pixels).
	words: OCR word boxes, loaded lazily by _ensure_words on first bbox repair.
	rejected: crop-back-rejected boxes per (field, index) — never re-offered,
	and counted in the round fingerprint so retrying the next candidate
	registers as progress.
	"""
	g = {
		"pil": None, "words": None, "words_tried": False,
		"ocr_repair": cint(getattr(action, "use_ocr_anchored_repair", 0) or 0),
		"crop_back": cint(getattr(action, "use_crop_back_check", 0) or 0),
		"verify_crops": cint(getattr(action, "use_verify_crops", 0) or 0),
		"bbox_snap": cint(getattr(action, "use_bbox_snap", 0) or 0),
		"rejected": {},
	}
	if not files or not (g["ocr_repair"] or g["crop_back"] or g["verify_crops"] or g["bbox_snap"]):
		return g
	if len(files) > 1:
		# Page-1 pixels against page-2 coordinates would crop the WRONG page and
		# invite corruption of correct values — degrade to free-form instead.
		g["ocr_repair"] = g["crop_back"] = g["verify_crops"] = g["bbox_snap"] = 0
		state.step("grounding_disabled_multifile",
			note="pixel grounding needs per-page mapping — free-form fallback for multi-file runs")
		return g
	try:
		g["pil"] = ground.load_image(files[0])
	except Exception as exc:
		state.step("grounding_unavailable", error=str(exc)[:200])
	return g


def _ensure_words(state, gctx):
	"""Lazy one-shot OCR. False → anchored repair unavailable (logged once)."""
	if gctx["words_tried"]:
		return gctx["words"] is not None
	gctx["words_tried"] = True
	if gctx["pil"] is None:
		return False
	t0 = time.monotonic()
	try:
		gctx["words"] = ground.ocr_word_boxes(gctx["pil"])
	except Exception:
		gctx["words"] = None
	if gctx["words"] is None:
		state.step("ocr_unavailable", note="tesseract missing/failed — free-form bbox repair fallback")
		return False
	state.step("ocr", words=len(gctx["words"]), ms=int((time.monotonic() - t0) * 1000))
	return True


def _route(state, action, image_parts):
	"""Pick executor + verifier. Deterministic shortcut when the choice is
	forced; orchestrator call (validated, one retry, deterministic fallback)
	when there is a real choice. Every decision is logged."""
	rows = [r for r in action.models if r.ai_model]
	models = {r.ai_model: frappe.get_doc("AI Model", r.ai_model) for r in rows}
	enabled = [r for r in rows if cint(models[r.ai_model].enabled)]
	if not enabled:
		frappe.throw(_("I2A Action {0} has no enabled AI Models").format(action.name))

	need_vision = bool(image_parts)
	executor_candidates = [
		r for r in enabled if not need_vision or cint(models[r.ai_model].supports_vision)
	]
	if not executor_candidates:
		frappe.throw(_("No enabled vision-capable AI Model on action {0}").format(action.name))

	# The verifier sees the image too — same capability pool as executors.
	verifier_pool = {r.ai_model for r in executor_candidates}

	orchestrator_row = next((r for r in enabled if cint(r.is_orchestrator)), enabled[0])
	verifier_hint = next(
		(r for r in executor_candidates if cint(r.is_verifier)), None
	)

	want_notes = cint(action.use_llm_request_notes)
	request_notes = action.request_notes or None

	if len(executor_candidates) == 1 and not want_notes:
		executor = executor_candidates[0].ai_model
		verifier = _fallback_verifier(state, executor, verifier_hint, verifier_pool, models)
		state.step("route", shortcut=True, executor=executor, verifier=verifier,
			note="single capable candidate — no orchestrator call")
		return models[executor], models[verifier], request_notes

	# Dedicated-verifier shortcut: rows marked is_verifier exist to CHECK, not
	# to execute. When exactly one non-verifier candidate remains, the routing
	# answer is forced — an orchestrator call would buy nothing (one wasted
	# LLM call on every single run).
	non_verifier = [r for r in executor_candidates if not cint(r.is_verifier)]
	if len(non_verifier) == 1 and not want_notes:
		executor = non_verifier[0].ai_model
		verifier = _fallback_verifier(state, executor, verifier_hint, verifier_pool, models)
		state.step("route", shortcut=True, executor=executor, verifier=verifier,
			note="single non-verifier candidate — no orchestrator call")
		return models[executor], models[verifier], request_notes

	candidates = [{"label": r.ai_model, "remarks": r.remarks} for r in executor_candidates]
	executor = verifier = None
	for attempt in (1, 2):
		try:
			messages = verify.build_route_messages(action, candidates, want_notes)
			answer = state.call(models[orchestrator_row.ai_model], messages, purpose="route")
		except providers.ProviderError as exc:
			state.step("route", attempt=attempt, error=str(exc)[:200])
			break
		picked = (answer or {}).get("executor_model")
		if any(r.ai_model == picked for r in executor_candidates):
			executor = picked
			v = (answer or {}).get("verifier_model")
			verifier = v if v in verifier_pool else None
			if want_notes:
				request_notes = (answer or {}).get("request_notes") or request_notes
			state.step("route", attempt=attempt, executor=executor, verifier=verifier)
			break
		candidates_note = f"'{picked}' is not one of the allowed labels"
		state.step("route", attempt=attempt, invalid_pick=picked)
		if attempt == 1:
			candidates = candidates + [{"label": "(reminder)", "remarks": candidates_note}]

	if not executor:  # deterministic fallback
		executor = executor_candidates[0].ai_model
		state.step("route", fallback=True, executor=executor,
			note="orchestrator failed/invalid twice — deterministic fallback")

	verifier = verifier or _fallback_verifier(state, executor, verifier_hint, verifier_pool, models)
	return models[executor], models[verifier], request_notes


def _fallback_verifier(state, executor, verifier_hint, verifier_pool, models):
	"""Pick a verifier from the capability-filtered pool (enabled + vision
	when the task has images). Preference: explicit hint → cheapest other
	capable model → the executor itself (self-verify, flagged in the trace)."""
	if verifier_hint and verifier_hint.ai_model != executor:
		return verifier_hint.ai_model
	others = [n for n in verifier_pool if n != executor]
	if others:
		others.sort(key=lambda n: flt(models[n].cost_per_m_input) + flt(models[n].cost_per_m_output))
		return others[0]
	state.step("verifier", self_verify=True, note="only one capable model — executor verifies itself")
	return executor


def _sanitize_index(schema, key, index, fields=None):
	"""Model-supplied index is untrusted: coerce for arrays, force None for
	scalars, and (when `fields` is given) bounds-check against the live array
	so an out-of-range index can never alias to element 0 via _item_for's
	fallback. Returns (index, ok) — ok=False means drop the entry entirely."""
	schema_field = next((f for f in schema if f["key"] == key), None)
	if not schema_field:
		return None, False
	if schema_field.get("kind") != "array":
		return None, True
	try:
		idx = int(index or 0)
	except (TypeError, ValueError, OverflowError):  # 1e999 parses to inf
		return None, False
	if fields is not None:
		entry = fields.get(key)
		n = len(entry) if isinstance(entry, list) else 0
		if idx < 0 or idx >= n:
			return None, False
	return idx, True


def _live_verifier(state, verifier):
	"""The verifier to actually use this round. A verifier whose provider is
	DEAD (every attempt failed — e.g. the model was delisted upstream) is
	swapped for the executor (self-verify) instead of failing round after
	round. The swap is logged once via the dead_verifiers step."""
	if verifier.name in state.dead_verifiers and state.executor_doc is not None:
		return state.executor_doc
	return verifier


def _mark_verifier_dead(state, verifier, at):
	if verifier.name in state.dead_verifiers or verifier is state.executor_doc:
		return
	state.dead_verifiers.add(verifier.name)
	state.step("verifier_dead", model=verifier.name, at=at,
		note="verifier provider failing — falling back to executor self-verify for the rest of the run")


def _model_verify(state, action, verifier, image_parts, fields, gctx=None, only=None):
	"""Image-grounded verify pass. Mandatory when mode=Automated.

	With use_verify_crops on, each claim ships the crop of its claimed
	region so the check is perception (read the crop) rather than recall.
	`only`: optional {(field, index)} set — restrict the audit to those
	claims (delta re-verify after a repair round)."""
	if state.mode != "Automated" and not (action.rules or "").strip():
		return []
	verifier = _live_verifier(state, verifier)

	crops = []
	if gctx and gctx["verify_crops"] and gctx["pil"] is not None:
		crop_failures = 0
		for f in action.parsed_schema():
			for idx, item in verify._iter_items(fields, f):
				if len(crops) >= MAX_VERIFY_CROPS:
					break
				if only is not None and (f["key"], idx) not in only:
					continue
				if not (item and item.get("bbox") and item.get("value") not in (None, "")):
					continue
				b = item["bbox"]
				if b["w"] * b["h"] > verify.MAX_BBOX_AREA:
					continue  # near-full-page box carries no grounding value
				try:
					# raw crops are lossless PNGs off the print-res page — a wide
					# row crop outweighs the whole shrunk document. 700px JPEG
					# keeps the value legible at a fraction of the tokens.
					part = extract.shrink_image_part(ground.crop_part(gctx["pil"], b), max_px=700)
				except Exception:
					crop_failures += 1
					continue  # one bad box must not strip crops from every claim
				crops.append({"field": f["key"], "index": idx, "part": part})
		if crops or crop_failures:
			state.step("verify_crops", count=len(crops), failed=crop_failures)

	try:
		# session flow: the doc image travels only on the verifier's FIRST
		# turn; later rounds append claims (+ new crops) to the same
		# conversation, whose prefix the provider prompt-caches.
		first_turn = "verifier" not in state.sessions
		turn = verify.build_verify_turn(
			action, image_parts if first_turn else None, fields, crops=crops, only=only)
		answer = state.chat(verifier, "verify", session="verifier",
			seed=[{"role": "system", "content": verify.verify_system(action)}], content=turn)
	except BudgetExceeded as exc:
		state.step("budget_stop", at="verify", note=str(exc)[:200])
		if state.mode == "Automated":
			# The mandatory pass didn't run → nothing may be auto-approved.
			state.mode_degraded = True
		return []
	except providers.ProviderError as exc:
		state.step("verify_call_failed", error=str(exc)[:200])
		_mark_verifier_dead(state, verifier, at="verify")
		if state.mode == "Automated":
			state.mode_degraded = True
			state.step("mode_degraded", note="verify model unavailable — Automated approvals disabled for this run")
		return []

	schema = action.parsed_schema()
	disagreements = []
	for d in (answer or {}).get("disagreements") or []:
		if not isinstance(d, dict) or not isinstance(d.get("field"), str) or not d.get("field"):
			continue
		index, ok = _sanitize_index(schema, d["field"], d.get("index"), fields)
		if not ok:
			continue
		disagreements.append({
			"field": d["field"],
			"index": index,
			"kind": "value_disagreement",
			"detail": f"verifier: image shows {d.get('expected')!r} ({d.get('reason', '')})"[:300],
		})
	return disagreements


def _repair(state, action, executor, verifier, image_parts, fields, deficiencies, gctx=None):
	"""Repair ladder. Bbox deficiencies go through OCR anchoring first
	(deterministic word-box match → Set-of-Marks selection → free-form
	coordinates as last resort); value deficiencies go straight to the
	free-form repair call. Every repaired bbox must then survive the
	crop-back check — a hallucinated box never sticks."""
	bbox_kinds = ("bbox_missing", "bbox_collision", "bbox_insane")
	bbox_defs = [d for d in deficiencies if d["kind"] in bbox_kinds]
	other_defs = [d for d in deficiencies if d["kind"] not in bbox_kinds]

	crop_back_queue = []  # [{"field", "index", "item"}] — new/changed boxes to prove

	if bbox_defs and gctx and gctx["ocr_repair"] and _ensure_words(state, gctx):
		leftover_bbox = _anchored_bbox_repair(
			state, action, executor, fields, bbox_defs, gctx, crop_back_queue
		)
	else:
		leftover_bbox = bbox_defs

	freeform = other_defs + leftover_bbox
	if freeform:
		_freeform_repair(state, action, executor, image_parts, fields, freeform, crop_back_queue, gctx)

	if crop_back_queue and gctx and gctx["crop_back"] and gctx["pil"] is not None:
		_crop_back_check(state, verifier, crop_back_queue, gctx)


def _snap_boxes(state, fields, gctx):
	"""Deterministic bbox TIGHTENING (opt-in: use_bbox_snap). Model-drawn
	boxes routinely sit a line above/below the value or span neighbouring
	text; OCR word geometry is pixel-true. A boxed value whose printed text
	is found at exactly ONE confident location (ground.deterministic_pick)
	NEAR the claimed box gets the OCR box instead.

	Tighten-only by design: boxless values stay with the deficiency →
	anchored-repair → crop-back pipeline (which PROVES what it materializes);
	a unique hit far from the claim is evidence of a wrong claim, not a
	license to silently move it (verify/crop checks own that call); and
	crop-back-rejected regions are never re-applied. Runs once, after the
	initial extraction — post-repair boxes are already proof-gated."""
	if not gctx or not gctx.get("bbox_snap") or not _ensure_words(state, gctx):
		return
	snapped = []
	for key, entry in fields.items():
		items = entry if isinstance(entry, list) else [entry]
		for idx, item in enumerate(items):
			real_idx = idx if isinstance(entry, list) else None
			if not item or item.get("value") in (None, "") or not item.get("bbox"):
				continue
			old = item["bbox"]
			clusters = ground.match_value(_match_targets(item), gctx["words"])
			clusters = [c for c in clusters if not _was_rejected(gctx, key, real_idx, c["bbox"])]
			pick = ground.deterministic_pick(clusters)
			if not pick:
				continue
			grown = {"x": old["x"], "y": max(0.0, old["y"] - 0.06),
				"w": old["w"], "h": min(1.0, old["h"] + 0.12)}
			if extract.bbox_iou(pick["bbox"], grown) <= 0:
				continue
			if extract.bbox_iou(pick["bbox"], old) > 0.9:
				continue  # already tight — nothing to report
			item["bbox"] = pick["bbox"]
			snapped.append({"field": key, "index": real_idx, "score": pick["score"]})
	if snapped:
		state.step("bbox_snap", count=len(snapped), items=snapped)


def _anchored_bbox_repair(state, action, executor, fields, bbox_defs, gctx, crop_back_queue):
	"""OCR-anchored bbox recovery. Returns the deficiencies it could NOT
	resolve (they fall through to the free-form repair call)."""
	leftover, ambiguous = [], []
	for d in bbox_defs:
		key, idx = d["field"], d.get("index")
		item = verify._item_for(fields, key, idx)
		if item is None or item.get("value") in (None, ""):
			leftover.append(d)
			continue
		found = ground.match_value(_match_targets(item), gctx["words"])
		clusters = [c for c in found if not _was_rejected(gctx, key, idx, c["bbox"])]
		filtered = len(found) - len(clusters)
		if filtered:
			state.step("bbox_candidates_filtered", field=key, index=idx, dropped=filtered,
				note="candidate(s) previously crop-back-rejected")
		pick = ground.deterministic_pick(clusters)
		if pick:
			item["bbox"] = pick["bbox"]
			item["repaired"] = 1
			state.step("bbox_ocr_match", field=key, index=idx, method="deterministic",
				score=pick["score"], matched_text=pick["text"][:80])
			crop_back_queue.append({"field": key, "index": idx, "item": item})
		elif clusters:
			ambiguous.append((d, item, clusters))
		else:
			state.step("bbox_ocr_match", field=key, index=idx, method="none",
				note=("all OCR candidates previously crop-back-rejected — free-form fallback"
					if filtered else "no OCR candidates — free-form fallback"))
			leftover.append(d)

	if ambiguous:
		leftover += _som_select(state, action, executor, ambiguous, gctx, crop_back_queue)
	return leftover


def _match_targets(item):
	"""Printed-form variants of a value for OCR matching (raw_text first —
	it is what the document shows; canonical value second; integral floats
	also as bare integers so 554.0 finds '554')."""
	targets = []
	for v in (item.get("raw_text"), item.get("value")):
		if v in (None, ""):
			continue
		targets.append(str(v))
		if isinstance(v, float) and math.isfinite(v) and v == int(v):
			targets.append(str(int(v)))
	return targets


def _som_select(state, action, executor, ambiguous, gctx, crop_back_queue):
	"""One Set-of-Marks call for every ambiguous field this round. The model
	picks a candidate NUMBER (or null) — it never emits coordinates. Returns
	the deficiencies still unresolved."""
	entries, index_map = [], {}
	for d, item, clusters in ambiguous:
		key, idx = d["field"], d.get("index")
		try:
			# 1400px keeps the numbered marks legible at a fraction of the tokens
			part = extract.shrink_image_part(ground.draw_marks(gctx["pil"], clusters), max_px=1400)
		except Exception as exc:
			state.step("som_render_failed", field=key, error=str(exc)[:120])
			continue
		entries.append({
			"field": key, "index": idx, "label": verify._label_for(action, key),
			"value": item.get("value"), "raw_text": item.get("raw_text"),
			"marks": [{"n": n, "text": c["text"][:60]} for n, c in enumerate(clusters, 1)],
			"part": part,
		})
		index_map[(key, idx)] = (d, item, clusters)

	skipped = [d for d, item, clusters in ambiguous if (d["field"], d.get("index")) not in index_map]
	if not entries:
		return skipped

	try:
		answer = state.chat(executor, "som_select", session="executor",
			content=verify.build_som_turn(entries))
	except BudgetExceeded as exc:
		state.step("budget_stop", at="som_select", round=state.rounds, note=str(exc)[:200])
		return skipped + [d for d, _i, _c in ambiguous if (d["field"], d.get("index")) in index_map]
	except providers.ProviderError as exc:
		state.step("som_select_failed", error=str(exc)[:200])
		return skipped + [d for d, _i, _c in ambiguous if (d["field"], d.get("index")) in index_map]

	# Model output is untrusted: only (field, index) pairs we asked about are
	# honored, and the mark must be a valid candidate number. The index is
	# schema-normalized (scalars → None even if the model echoes 0) so a
	# correct pick is never discarded over index cosmetics.
	schema = action.parsed_schema()
	sel_by = {}
	for s in (answer or {}).get("selections") or []:
		if not isinstance(s, dict) or not isinstance(s.get("field"), str) or not s.get("field"):
			continue
		s_idx, ok = _sanitize_index(schema, s["field"], s.get("index"))
		if not ok:
			continue
		sel_by[(s["field"], s_idx)] = s.get("mark")

	leftover = list(skipped)
	for (key, idx), (d, item, clusters) in index_map.items():
		if (key, idx) in sel_by:
			mark = sel_by[(key, idx)]
		else:
			# field-only fallback when the field appears exactly once in both
			# the batch and the reply — index mismatch must not waste the pick
			ours = [k for k in index_map if k[0] == key]
			theirs = [m for (fk, _fi), m in sel_by.items() if fk == key]
			mark = theirs[0] if len(ours) == 1 and len(theirs) == 1 else None
		try:
			mark = int(mark) if mark is not None else None
		except (TypeError, ValueError, OverflowError):
			mark = None
		if mark is not None and 1 <= mark <= len(clusters):
			c = clusters[mark - 1]
			item["bbox"] = c["bbox"]
			item["repaired"] = 1
			state.step("bbox_ocr_match", field=key, index=idx, method="som",
				mark=mark, score=c["score"], matched_text=c["text"][:80])
			crop_back_queue.append({"field": key, "index": idx, "item": item})
		else:
			state.step("bbox_ocr_match", field=key, index=idx, method="som", mark=None,
				note="model declined or invalid mark — free-form fallback")
			leftover.append(d)
	return leftover


def _freeform_repair(state, action, executor, image_parts, fields, deficiencies, crop_back_queue, gctx=None):
	schema = action.parsed_schema()
	try:
		# executor session already holds the document from the extract turn —
		# the repair ask travels as text only (image_parts deliberately unused)
		answer = state.chat(executor, "repair", session="executor",
			content=verify.build_repair_turn(action, fields, deficiencies))
	except BudgetExceeded as exc:
		state.step("budget_stop", at="repair", round=state.rounds, note=str(exc)[:200])
		return
	except providers.ProviderError as exc:
		state.step("repair_call_failed", round=state.rounds, error=str(exc)[:200])
		return

	# A repair may only touch the (field, index) pairs it was ASKED about —
	# an extra repairs row for any other field is untrusted output, dropped.
	allowed = {(d["field"], d.get("index")) for d in deficiencies}

	applied, dropped = [], []
	for r in (answer or {}).get("repairs") or []:
		if not isinstance(r, dict) or not isinstance(r.get("field"), str) or not r.get("field"):
			continue
		key = r["field"]
		schema_field = next((f for f in schema if f["key"] == key), None)
		if not schema_field:
			continue
		index, ok = _sanitize_index(schema, key, r.get("index"))
		if not ok:
			continue
		if (key, index) not in allowed and (key, None) not in allowed:
			dropped.append({"field": key, "index": index, "note": "not among the asked deficiencies"})
			continue
		item = verify._item_for(fields, key, index)
		old_bbox = item.get("bbox") if item else None
		old_value = item.get("value") if item else None
		if item is None:
			# The field was missing entirely (None scalar / empty array) — the
			# repair SUPPLIED it. Materialize instead of dropping the answer.
			if r.get("value") in (None, ""):
				continue
			item = verify._shape_item({
				"value": r.get("value"),
				"raw_text": r.get("raw_text"),
				"confidence": r.get("confidence"),
				"bbox": r.get("bbox"),
			})
			if schema_field.get("kind") == "array":
				fields.setdefault(key, [])
				fields[key].append(item)
			else:
				fields[key] = item
		else:
			if r.get("value") is not None:
				item["value"] = r["value"]
				item["raw_text"] = r.get("raw_text") or str(r["value"])
			if r.get("confidence") is not None:
				item["confidence"] = r["confidence"]
			bbox = extract.normalize_bbox(r.get("bbox"))
			if bbox:
				item["bbox"] = bbox
		# free-form boxes get the same sanity bar as everything else: no
		# near-page rectangles, no rectangle the crop-back already disproved
		if item.get("bbox") and item["bbox"] != old_bbox:
			if item["bbox"]["w"] * item["bbox"]["h"] > verify.MAX_BBOX_AREA:
				item["bbox"] = old_bbox
			elif gctx and _was_rejected(gctx, key, index, item["bbox"]):
				item["bbox"] = old_bbox
		item["repaired"] = 1
		item.pop("cross_check", None)  # re-checked next round
		applied.append({"field": key, "index": index})
		if item.get("bbox") and (item["bbox"] != old_bbox or item.get("value") != old_value):
			# a new box OR a new value under the old box — either way the
			# (value, box) claim changed and must be proven by crop-back
			crop_back_queue.append({"field": key, "index": index, "item": item})

	state.step("repair", round=state.rounds, applied=applied,
		**({"dropped": dropped} if dropped else {}))


def _crop_back_check(state, verifier, crop_back_queue, gctx):
	"""Crop every newly-claimed box and make the VERIFIER read it. FAIL
	CLOSED: a box is kept ONLY on an explicit contains=true — omitted
	verdicts, render failures, provider failures and budget exhaustion all
	strip the unproven box (disproven rectangles are additionally remembered
	so the next round offers the next candidate, never the same one)."""
	verifier = _live_verifier(state, verifier)
	# one item can be queued from two sources in a round — check it once
	unique = {}
	for q in crop_back_queue:
		unique[(q["field"], q["index"])] = q
	queue = list(unique.values())

	entries, rejected = [], []

	def _strip(q, reason, read_text="", remember=False):
		if remember:
			_remember_rejection(gctx, q["field"], q["index"], q["item"]["bbox"])
		q["item"]["bbox"] = None
		rejected.append({"field": q["field"], "index": q["index"],
			"reason": reason, "read_text": str(read_text)[:80]})

	for n, q in enumerate(queue, 1):
		item = q["item"]
		if not item.get("bbox"):
			continue
		try:
			part = extract.shrink_image_part(ground.crop_part(gctx["pil"], item["bbox"]), max_px=700)
		except Exception as exc:
			state.step("crop_back_render_failed", field=q["field"], error=str(exc)[:120])
			_strip(q, "crop render failed")  # unproven box may not survive
			continue
		entries.append({
			"n": n, "field": q["field"], "index": q["index"], "item": item, "q": q,
			"value": item.get("raw_text") or item.get("value"), "part": part,
		})
	if not entries:
		if rejected:
			state.step("crop_back", passed=[], rejected=rejected)
		return

	try:
		# rides the verifier's session (crops are new turns; the contract text
		# travels in-turn so it works whether or not the session exists yet)
		answer = state.chat(verifier, "crop_check", session="verifier",
			content=verify.build_crop_check_turn(entries))
	except (BudgetExceeded, providers.ProviderError) as exc:
		# The check could not run — the boxes stay UNPROVEN and are stripped
		# (not remembered: the rectangles were never disproven, a later round
		# with budget may legitimately re-propose them).
		at = "budget_stop" if isinstance(exc, BudgetExceeded) else "crop_check_failed"
		if not isinstance(exc, BudgetExceeded):
			_mark_verifier_dead(state, verifier, at="crop_check")
		state.step(at, at_stage="crop_check", round=state.rounds, note=str(exc)[:200])
		for e in entries:
			_strip(e["q"], "crop check unavailable — fail closed")
		state.step("crop_back", passed=[], rejected=rejected)
		return

	verdicts = {}
	for c in (answer or {}).get("checks") or []:
		if not isinstance(c, dict):
			continue
		try:
			verdicts[int(c.get("crop"))] = c
		except (TypeError, ValueError, OverflowError):
			continue

	passed = []
	for e in entries:
		v = verdicts.get(e["n"]) or {}
		if v.get("contains") is True:
			passed.append({"field": e["field"], "index": e["index"],
				"read_text": str(v.get("read_text") or "")[:80]})
			continue
		# Explicitly disproven or silently omitted — reject; remember the
		# rectangle so it is never offered again.
		_strip(e["q"], "verifier did not confirm", v.get("read_text") or "", remember=True)
	state.step("crop_back", passed=passed, rejected=rejected)


def _remember_rejection(gctx, key, index, bbox):
	"""Duplicates are not re-appended: the rejection count feeds the round
	fingerprint as a progress signal, so re-rejecting the same rectangle must
	NOT read as progress (it would defeat the no_progress guard)."""
	if _was_rejected(gctx, key, index, bbox):
		return
	gctx["rejected"].setdefault((key, index), []).append(bbox)


def _was_rejected(gctx, key, index, bbox):
	return any(
		extract.bbox_iou(bbox, r) > 0.6
		for r in gctx["rejected"].get((key, index), [])
	)


def _gate(state, fields, schema, unresolved):
	"""Per-field verdicts. Automated mode approves only fields with zero
	unresolved deficiencies AND a passed (non-degraded) verify pass;
	cross_check=matched is recorded as the strongest evidence tier."""
	bad = {}
	for d in unresolved:
		bad.setdefault((d["field"], d.get("index")), []).append(d["kind"])

	verdict = {}
	automated = state.mode == "Automated" and not getattr(state, "mode_degraded", False)

	for f in schema:
		key = f["key"]
		entry = fields.get(key)
		items = entry if isinstance(entry, list) else [entry]
		verdicts = []
		for idx, item in enumerate(items):
			real_idx = idx if isinstance(entry, list) else None
			if item is None:
				verdicts.append({"index": real_idx, "status": "Pending", "reason": "no value"})
				continue
			kinds = list(bad.get((key, real_idx), []))
			if real_idx is not None:
				kinds += bad.get((key, None), [])
			if automated and not kinds:
				item["status"] = "Approved"
				reason = "ground-truth cross-check" if item.get("cross_check") == "matched" else "all checks passed"
			else:
				item["status"] = "Pending"
				reason = ",".join(kinds) if kinds else ("manual mode" if not automated else "held")
			verdicts.append({
				"index": real_idx, "status": item["status"], "reason": reason,
				"cross_check": item.get("cross_check"), "repaired": item.get("repaired", 0),
			})
		if not verdicts and (key, None) in bad:
			# required array came back empty — surface its blocking deficiency
			verdicts.append({"index": None, "status": "Pending", "reason": ",".join(bad[(key, None)])})
		verdict[key] = verdicts

	state.step("gate", automated=automated, summary={
		k: [v["status"] for v in vs] for k, vs in verdict.items()
	})
	return verdict


# ------------------------------------------------------------------ state

class _State:
	def __init__(self, action, run_doc, mode):
		self.action = action
		self.run_doc = run_doc
		self.mode = mode
		self.mode_degraded = False
		self.rounds = 0
		self.calls = 0
		self.executor_doc = None
		self.dead_verifiers = set()
		self.reserved_calls = 0  # calls held back for the agentic phase
		self.final_result = None  # persisted to run.result_json for the review screen
		self.steps = []
		self.verdict = {}
		self.started = time.monotonic()
		self.max_calls = cint(action.max_calls_per_run) or 12
		self.seconds_budget = cint(action.run_seconds_budget) or 480
		# One append-only conversation per ROLE for the whole run (Sakthi's
		# session architecture, 2026-07-16): the document image and system
		# prompt enter a role's conversation ONCE; every later ask appends a
		# user turn and resends the identical prefix. Providers prompt-cache
		# that prefix (Gemini implicit ~75% off, OpenAI ~50% off), and the
		# image is never re-attached per call. Deleted with the run — nothing
		# outlives the "chat".
		self.sessions = {}

	def chat(self, ai_model, purpose, *, session, content=None, seed=None):
		"""Session-continuing model call. `session` names the role-scoped
		conversation ('executor' / 'verifier'); `seed` initialises it on
		first use; `content` appends one user turn. The parsed reply is
		appended back so the next ask builds on everything before it."""
		s = self.sessions.get(session)
		if s is None:
			s = self.sessions[session] = list(seed or [])
		if content is not None:
			s.append({"role": "user", "content": content})
		data = self.call(ai_model, s, purpose)
		s.append({"role": "assistant", "content": json.dumps(data, default=str)[:20000]})
		return data

	def call(self, ai_model, messages, purpose):
		usable = self.max_calls - (0 if purpose == "agent" else self.reserved_calls)
		if self.calls >= usable:
			raise BudgetExceeded(_("max_calls_per_run ({0}, {1} reserved for resolution) reached").format(
				self.max_calls, self.reserved_calls))
		if time.monotonic() - self.started > self.seconds_budget:
			raise BudgetExceeded(_("run_seconds_budget ({0}s) exceeded").format(self.seconds_budget))
		self.calls += 1
		outcome = providers.call_model(
			ai_model, messages, purpose=purpose,
			run=self.run_doc.name, action=self.action.name,
		)
		return outcome["data"]

	def call_tools(self, ai_model, messages, specs, purpose):
		if self.calls >= self.max_calls:
			raise BudgetExceeded(_("max_calls_per_run ({0}) reached").format(self.max_calls))
		if time.monotonic() - self.started > self.seconds_budget:
			raise BudgetExceeded(_("run_seconds_budget ({0}s) exceeded").format(self.seconds_budget))
		self.calls += 1
		return providers.call_with_tools(
			ai_model, messages, specs, purpose=purpose,
			run=self.run_doc.name, action=self.action.name,
		)

	def budget_ok(self, reason_step):
		usable = self.max_calls - self.reserved_calls
		if self.calls >= usable:
			note = f"call budget {self.max_calls} exhausted"
			if self.reserved_calls:
				note = f"repair budget {usable} exhausted ({self.reserved_calls} calls reserved for resolution)"
			self.step(reason_step, note=note)
			return False
		if time.monotonic() - self.started > self.seconds_budget:
			self.step(reason_step, note=f"time budget {self.seconds_budget}s exhausted")
			return False
		return True

	def step(self, kind, **data):
		self.steps.append({"step": kind, "t_ms": int((time.monotonic() - self.started) * 1000), **data})


def _fingerprint(deficiencies, fields, gctx=None):
	"""Round signature for no-progress detection. Includes the bbox so a
	repair that moved a box (but not the value) still counts as progress,
	and the crop-back rejection counts so 'same deficiency, next candidate'
	is progress too (bounded by max_rounds/max_calls regardless)."""
	sig = []
	for d in deficiencies:
		item = verify._item_for(fields, d["field"], d.get("index"))
		sig.append((
			d["field"], d.get("index"), d["kind"],
			str(item.get("value")) if item else None,
			str(item.get("bbox")) if item else None,
		))
	rejections = tuple(sorted(
		(k[0], str(k[1]), len(v)) for k, v in ((gctx or {}).get("rejected") or {}).items()
	))
	return (tuple(sorted(sig, key=lambda t: (t[0], str(t[1]), t[2]))), rejections)


def _run_lock_key(action_name, ref_doctype, ref_name, reference_detail):
	return f"i2a:run:{action_name}:{ref_doctype or ''}:{ref_name or ''}:{reference_detail or ''}"


def _acquire_run_lock(lock_key, action):
	ttl = 2 * (cint(action.run_seconds_budget) or 480)
	try:
		return bool(frappe.cache().set(lock_key, "1", nx=True, ex=ttl))
	except Exception:
		return True  # Redis down: proceed rather than block; DB backstop below


def _release_run_lock(lock_key):
	try:
		frappe.cache().delete(lock_key)
	except Exception:
		pass


def _mark_stale_runs(action_name, ref_doctype, ref_name, reference_detail, action):
	"""Backstop: a SIGKILLed worker can leave a Running row behind (its Redis
	lock expires on its own). Mark such rows Failed so traces stay truthful."""
	if not ref_name:
		return
	from frappe.utils import add_to_date

	stale_cutoff = 2 * (cint(action.run_seconds_budget) or 480)
	stale = frappe.get_all(
		"I2A Run",
		filters={
			"action": action_name, "status": "Running",
			"reference_doctype": ref_doctype or "", "reference_name": ref_name,
			"reference_detail": reference_detail or "",
			"started_at": ["<", add_to_date(now_datetime(), seconds=-stale_cutoff)],
		},
		pluck="name",
	)
	for name in stale:
		frappe.db.set_value("I2A Run", name, {
			"status": "Failed",
			"error_message": "stale — worker died before finalize; superseded by a newer run",
			"ended_at": now_datetime(),
		}, update_modified=False)


def _finalize(run_doc, state):
	"""Always runs. Totals summed from the call rows (billing truth)."""
	try:
		totals = frappe.get_all(
			"I2A LLM Call",
			filters={"run": run_doc.name},
			fields=["sum(total_tokens) as tokens", "sum(cost_usd) as cost", "max(cost_estimated) as est"],
		)
		row = totals[0] if totals else {}
		run_doc.total_tokens = cint(row.get("tokens"))
		run_doc.total_cost_usd = flt(row.get("cost"))
		run_doc.cost_estimated = cint(row.get("est"))
		run_doc.rounds_used = state.rounds
		if state.final_result is not None:
			run_doc.result_json = json.dumps(state.final_result, default=str)[:140000]
		run_doc.verdict_json = json.dumps(state.verdict, indent=1, default=str)
		run_doc.steps_json = json.dumps(state.steps, indent=1, default=str)
		run_doc.ended_at = now_datetime()
		run_doc.flags.ignore_permissions = True
		run_doc.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "I2A Run finalize failed")
