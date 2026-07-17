"""Generic record-matching phase (engine step MATCH).

Opt-in per Action via `match_config`. When present, after the fields are
extracted and verified the engine fetches candidate records from an ERP
doctype and asks the model which candidate(s) the document refers to — the
reconciliation a human used to do by eye. The engine stays domain-agnostic:
the target doctype, the candidate query and the matching guidance are all
configuration (LR, or any document, supplies its own).

Two deterministic guards bound the model:
  - candidates come from a config query with a hard row cap (the model never
    sees the whole table, and can only pick a name it was shown);
  - every returned target is validated against the fetched candidate set —
    an invented name is dropped, never trusted.
"""

import json
import re

import frappe
from frappe.utils import cint, flt

from frappe_tools.i2a import verify

MAX_CANDIDATES = 60  # hard bound regardless of what the config asks for


def parse_config(action):
	"""Return the action's match config dict, or None when matching is off."""
	raw = getattr(action, "match_config", None)
	if not raw:
		return None
	try:
		cfg = raw if isinstance(raw, dict) else json.loads(raw)
	except (ValueError, TypeError):
		return None
	if not isinstance(cfg, dict) or not cfg.get("target_doctype"):
		return None
	return cfg


_PLACEHOLDER = re.compile(r"^\{([\w.]+)\}$")  # a value that is ONLY a placeholder


def _all_values(fields, key):
	"""Every extracted value of a field (array → all items, scalar → one)."""
	entry = fields.get(key)
	if isinstance(entry, list):
		out = []
		for i in entry:
			v = i.get("value") if i else None
			if v not in (None, ""):
				out.append(v)
		# de-dupe, preserve order (the model often emits the same EWB twice)
		seen, uniq = set(), []
		for v in out:
			if v not in seen:
				seen.add(v)
				uniq.append(v)
		return uniq
	if isinstance(entry, dict):
		v = entry.get("value")
		return [v] if v not in (None, "") else []
	return []


def _resolve(value, fields, context):
	"""Substitute {schema_key} (first extracted value) and {context.key}
	placeholders inside a single config value; non-strings pass through."""
	if not isinstance(value, str):
		return value

	def sub(m):
		ref = m.group(1)
		if ref.startswith("context."):
			return str(context.get(ref[8:], ""))
		first = verify._first_value(fields, ref)
		return str(first if first is not None else "")

	return re.sub(r"\{([\w.]+)\}", sub, value)


def resolve_filters(raw_filters, fields, context):
	"""Render a Frappe filter spec (dict OR list-of-[field,op,value]) with
	placeholders resolved. Conditions whose template resolves to empty are
	DROPPED so a missing extracted value never silently matches everything.

	For `in`/`not in` operators a bare-placeholder value ("{eway_bills}")
	expands to the FULL list of that field's extracted values, so one filter
	can match any of several e-way bills / bill numbers."""
	if isinstance(raw_filters, dict):
		out = {}
		for k, v in raw_filters.items():
			rv = _resolve(v, fields, context)
			if rv not in ("", None):
				out[k] = rv
		return out
	if isinstance(raw_filters, list):
		out = []
		for cond in raw_filters:
			if not isinstance(cond, (list, tuple)) or len(cond) != 3:
				continue
			field, op, val = cond
			lop = str(op).lower()
			ph = isinstance(val, str) and _PLACEHOLDER.match(val)
			if lop in ("in", "not in") and ph and not ph.group(1).startswith("context."):
				values = _all_values(fields, ph.group(1))
				if not values:
					continue  # nothing to match → drop, never fetch the whole table
				out.append([field, op, values])
				continue
			if lop in ("like", "not like"):
				expanded = _expand_like(field, op, val, fields, context)
				if expanded is not None:
					out.extend(expanded)
					continue
				# scalar like: guard against an all-wildcard pattern (a placeholder
				# that resolved to empty → "%%") which would match everything
				rv = _resolve(val, fields, context)
				if rv in ("", None) or not str(rv).strip("%_ "):
					continue
				out.append([field, op, rv])
				continue
			rv = _resolve(val, fields, context)
			if rv in ("", None):
				continue
			out.append([field, op, rv])
		return out
	return None


def _expand_like(field, op, val, fields, context):
	"""A `like` value embedding an ARRAY placeholder (e.g. "%{bill_numbers}%")
	expands to one condition per extracted value — match any of several bill
	numbers / substrings. Returns a list of conditions, or None to let the
	caller handle it as an ordinary scalar substitution."""
	if not isinstance(val, str):
		return None
	array_ref = None
	for ref in re.findall(r"\{([\w.]+)\}", val):
		if ref.startswith("context."):
			continue
		if isinstance(fields.get(ref), list):
			array_ref = ref
			break
	if not array_ref:
		return None
	values = _all_values(fields, array_ref)
	if not values:
		return []  # array present but empty → match nothing (never fetch all)
	conds = []
	for v in values:
		one = val.replace("{" + array_ref + "}", str(v))
		one = _resolve(one, fields, context)  # resolve any remaining placeholders
		if one not in ("", None):
			conds.append([field, op, one])
	return conds


def _fetch_one(target_doctype, q, fields, context):
	filters = resolve_filters(q.get("filters"), fields, context)
	raw_or = q.get("or_filters")
	or_filters = resolve_filters(raw_or, fields, context)
	# A strong-key query DECLARES or_filters (e.g. ewaybill/name in [...]); if
	# every key dropped to empty, skip the whole query — otherwise its
	# surviving AND-filters (docstatus etc.) would fetch the whole table.
	if raw_or and not or_filters:
		return []
	# A query with no surviving condition at all must not run either, unless it
	# explicitly opts in (a pure date/status net that intends a broad fetch).
	if not filters and not or_filters and not q.get("allow_unfiltered"):
		return []
	want = list(q.get("fields") or ["name"])
	if "name" not in want:
		want = ["name"] + want
	limit = min(cint(q.get("limit")) or 40, MAX_CANDIDATES)
	return frappe.get_all(
		target_doctype,
		filters=filters or None,
		or_filters=or_filters or None,
		fields=want,
		limit=limit,
		order_by=q.get("order_by") or None,
		ignore_permissions=True,
	)


def fetch_candidates(cfg, fields, context):
	"""Run the candidate query (or a LIST of queries — union of strategies,
	e.g. strong-key fetch + a date net) and return de-duped rows, capped."""
	queries = cfg.get("candidate_query") or {}
	if isinstance(queries, dict):
		queries = [queries]
	target = cfg["target_doctype"]
	seen, out = set(), []
	for q in queries:
		if not isinstance(q, dict):
			continue
		for row in _fetch_one(target, q, fields, context):
			name = row.get("name")
			if name in seen:
				continue
			seen.add(name)
			out.append(row)
			if len(out) >= MAX_CANDIDATES:
				return out
	return out


def is_corroborated(target_row, cfg, fields):
	"""Deterministic safety gate on an AI match: the chosen record must share
	an EXACT key with the document (e.g. its ewaybill equals one the LR shows,
	or its name equals a bill number). Without an exact key the match cannot
	auto-finalize — the model's confidence alone never links two records.

	Config `corroborate`: list of {target_field, from}. Corroborated if ANY
	rule's target value equals one of the document's extracted `from` values.
	No `corroborate` config → returns None (feature off, confidence stands).
	"""
	rules = cfg.get("corroborate")
	if not rules:
		return None
	if not target_row:
		return False
	for rule in rules:
		tf, src = rule.get("target_field"), rule.get("from")
		if not tf or not src:
			continue
		tval = target_row.get(tf)
		if tval in (None, ""):
			continue
		src_values = [str(v) for v in _all_values(fields, src)]
		mode = rule.get("match", "exact")
		if mode == "numeric_suffix":
			# invoice's trailing number (zero-stripped) == the printed bill number.
			# Safe against digit-count collisions (1422 != 11422 as integers) but
			# NOT against cross-series ones (INV…-01422 vs SOI…-01422 share 1422)
			# — this rule corroborates only the record the matcher already chose,
			# it never picks between candidates. Weakest of the exact keys.
			tnum = _trailing_int(tval)
			if tnum is not None and any(_trailing_int(v) == tnum for v in src_values):
				return True
		elif str(tval) in src_values:
			return True
	return False


def deterministic_candidates(action, fields, context, limit=8):
	"""LLM-free match candidates for the review screen: the config's own
	candidate queries fetch plausible records from the ERP, and each row is
	scored by the corroborate rules — exact shared key (0.95) > numeric
	suffix (0.7) > plain query hit (0.4). Zero model calls: this is "what
	does OUR data say", not a judgment. Used when the agent could not
	resolve, so the reviewer still sees scored invoice candidates."""
	cfg = parse_config(action)
	if not cfg:
		return None
	rows = fetch_candidates(cfg, fields, context or {})
	rules = cfg.get("corroborate") or []
	# ALL weights are CONFIG with engine defaults: per-rule "score" on each
	# corroborate entry, plus base_score / context_rows / limit in match_config
	# — the engine encodes NO opinion about how strong a domain's keys are.
	base_score = flt(cfg.get("base_score")) or 0.4
	limit = cint(cfg.get("limit")) or limit
	context_rows = cint(cfg.get("context_rows")) or 3
	# ranking is CONFIG, not code: e.g. {"field": "posting_date", "order": "desc"}
	# encodes "a document belongs to the most recently created matching record"
	rank = cfg.get("rank") or {}
	rank_field = rank.get("field")
	rank_desc = (rank.get("order") or "desc").lower() != "asc"
	out = []
	for row in rows:
		# a document carries MANY references (bill numbers, e-way bills) and
		# each maps to ITS OWN record — matched_value says which reference
		# this candidate answers, so the reviewer sees a per-bill mapping.
		score, reason, matched_value = base_score, "matches the candidate search", None
		for rule in rules:
			tf, src = rule.get("target_field"), rule.get("from")
			if not tf or not src:
				continue
			tval = row.get(tf)
			if tval in (None, ""):
				continue
			src_values = [str(v) for v in _all_values(fields, src)]
			if rule.get("match", "exact") == "numeric_suffix":
				rule_score = flt(rule.get("score")) or 0.7
				tnum = _trailing_int(tval)
				hit = next((v for v in src_values if _trailing_int(v) == tnum), None) if tnum is not None else None
				if hit is not None and score < rule_score:
					score, reason, matched_value = rule_score, f"{tf} ends with the document's {src}", hit
			elif str(tval) in src_values:
				rule_score = flt(rule.get("score")) or 0.95
				score, reason, matched_value = rule_score, f"exact {tf} matches the document's {src}", str(tval)
				break
		info = {}
		for k, v in dict(row).items():
			if k == "name" or v in (None, ""):
				continue
			if hasattr(v, "isoformat"):
				v = str(v)
			if isinstance(v, (str, int, float)):
				info[k] = v
		out.append({"target": row.get("name"), "confidence": score, "reason": reason,
			"matched_value": matched_value, "info": info,
			"_rank": str(row.get(rank_field) or "") if rank_field else ""})
	if rank_field:
		out.sort(key=lambda m: m["_rank"], reverse=rank_desc)
	out.sort(key=lambda m: -m["confidence"])  # stable: confidence first, then rank
	for m in out:
		m.pop("_rank", None)
	# corroborated matches all matter (one per document reference — NEVER
	# truncated); the uncorroborated broad-net rows are context, not answers
	strong = [m for m in out if m["matched_value"]]
	weak = [m for m in out if not m["matched_value"]][:context_rows]
	matches = strong + weak[:max(0, limit - len(strong))]
	return {"status": "candidates", "matches": matches,
		"note": "deterministic candidates — no model call"}


def _trailing_int(s):
	m = re.search(r"(\d+)$", str(s))
	return int(m.group(1)) if m else None


def parse_answer(answer, candidate_names, threshold):
	"""Validate the model's match reply against the fetched candidates.

	Returns {"status": matched|doubt|none, "matches": [...], "best": float}.
	A target not in `candidate_names` is discarded (untrusted output).
	"""
	matches = []
	for m in (answer or {}).get("matches") or []:
		if not isinstance(m, dict):
			continue
		target = m.get("target")
		if target not in candidate_names:
			continue
		try:
			conf = flt(m.get("confidence"))
		except (TypeError, ValueError):
			conf = 0.0
		conf = max(0.0, min(1.0, conf))
		matches.append({
			"target": target,
			"confidence": conf,
			"reason": str(m.get("reason") or "")[:300],
		})
	# de-dupe by target keeping the highest confidence, then rank
	best_by = {}
	for m in matches:
		if m["target"] not in best_by or m["confidence"] > best_by[m["target"]]["confidence"]:
			best_by[m["target"]] = m
	matches = sorted(best_by.values(), key=lambda x: -x["confidence"])

	best = matches[0]["confidence"] if matches else 0.0
	if not matches:
		status = "none"
	elif best >= threshold:
		status = "matched"
	else:
		status = "doubt"
	return {"status": status, "matches": matches, "best": best}


def run_match(state, action, matcher_model, fields, context):
	"""Fetch candidates and reconcile the document against them. Returns a
	match-result dict, or None when the action has no match_config."""
	cfg = parse_config(action)
	if not cfg:
		return None

	try:
		candidates = fetch_candidates(cfg, fields, context or {})
	except Exception as exc:
		state.step("match_fetch_failed", error=str(exc)[:200])
		return {"status": "error", "matches": [], "candidates": 0, "error": str(exc)[:200]}

	state.step("match_fetch", doctype=cfg["target_doctype"], candidates=len(candidates))
	if not candidates:
		return {"status": "none", "matches": [], "candidates": 0, "reason": "no candidates fetched"}

	names = {c.get("name") for c in candidates}
	threshold = flt(cfg.get("confidence_threshold") or 0.8)

	from frappe_tools.i2a import providers

	try:
		messages = verify.build_match_messages(action, fields, candidates, cfg)
		answer = state.call(matcher_model, messages, purpose="match")
	except providers.BudgetExceeded:
		raise  # a budget stop is not a provider failure — the engine labels it
	except providers.ProviderError as exc:
		state.step("match_call_failed", error=str(exc)[:200])
		return {"status": "error", "matches": [], "candidates": len(candidates), "error": str(exc)[:200]}

	result = parse_answer(answer, names, threshold)
	result["candidates"] = len(candidates)
	result["threshold"] = threshold

	# deterministic corroboration: a document maps EACH of its references to
	# its own record, so EVERY returned match must share an exact key — one
	# uncorroborated match anywhere downgrades the whole run to a doubt (a
	# human decides), mirroring how the agentic path gates every write.
	by_name = {c.get("name"): c for c in candidates}
	corroborated = None
	for m in result["matches"]:
		c = is_corroborated(by_name.get(m["target"]), cfg, fields)
		m["corroborated"] = c
		if corroborated is None:
			corroborated = c
		elif c is not None:
			corroborated = bool(corroborated) and bool(c)
	if corroborated is False and result["status"] == "matched":
		result["status"] = "doubt"
		result["downgraded"] = "a match lacks exact key corroboration — routed to review"
	result["corroborated"] = corroborated

	state.step(
		"match",
		candidates=len(candidates),
		status=result["status"],
		threshold=threshold,
		corroborated=corroborated,
		matches=[{"target": m["target"], "confidence": m["confidence"]} for m in result["matches"]],
	)
	return result
