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


def is_corroborated(target_row, cfg, fields, for_gate=False):
	"""Deterministic safety gate on an AI match: the chosen record must share
	an EXACT key with the document (e.g. its ewaybill equals one the LR shows,
	or its name equals a bill number). Without an exact key the match cannot
	auto-finalize — the model's confidence alone never links two records.

	Config `corroborate`: list of {target_field, from}. Corroborated if ANY
	rule's target value equals one of the document's extracted `from` values.
	No `corroborate` config → returns None (feature off, confidence stands).

	for_gate=True (autonomous WRITE gate): a numeric_suffix rule is NOT
	sufficient by itself — a bare trailing number collides across series
	(INV2627-00327 vs SOI2627-00327 vs last year's) and could steer an
	auto-write to the wrong invoice. Suffix corroboration is allowed for the
	gate only when match_config sets suffix_autoapply=true (opt-in). Suffix
	still scores candidates for human review (deterministic_candidates).
	"""
	rules = cfg.get("corroborate")
	if not rules:
		return None
	if not target_row:
		return False
	suffix_ok = bool(cfg.get("suffix_autoapply")) or not for_gate
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
			if not suffix_ok:
				continue  # suffix alone may not authorize an autonomous write
			tnum = _trailing_int(tval)
			if tnum is not None and any(_trailing_int(v) == tnum for v in src_values):
				return True
		elif str(tval) in src_values:
			return True
	return False


def resolve_references(cfg, fields, rows):
	"""Cross-reference consistency check (the careful-clerk rule).

	Corroboration is a per-ROW predicate — `is_corroborated` returns True the
	moment any one rule links the row to the document. Consistency is a
	per-DOCUMENT property: nothing else in the pipeline resolves ALL of the
	document's references independently and asks whether they agree. This does.

	Every extracted reference value is resolved on its own against the fetched
	candidate rows, then the resolutions are tested for mutual consistency: the
	document's references can jointly describe at most max(#values per reference
	kind) records — an EWB that exactly proves invoice A while a bill number on
	the same document points to a DIFFERENT invoice B is a contradiction the
	per-row check cannot see.

	Domain-agnostic: reference kinds, strengths and labels all derive from
	`match_config.corroborate`. Returns None when the feature does not apply
	(no rules, no reference values, or the `detect_conflicts` kill switch is 0),
	else a dict {expected, resolutions, union, issues, conflict, reason}. Pure —
	no frappe DB calls; it operates on the rows the caller already fetched (so
	date-net-fetched rows participate too)."""
	rules = cfg.get("corroborate")
	if not rules or cint(cfg.get("detect_conflicts", 1)) == 0:
		return None

	# reference kind = each distinct `from` key across the rules; per kind we
	# split the target_fields the rules match on into exact vs numeric_suffix,
	# and remember the first label a rule of that kind carries (reason text only)
	kinds, exact_fields, suffix_fields, labels = [], {}, {}, {}
	for rule in rules:
		src, tf = rule.get("from"), rule.get("target_field")
		if not src or not tf:
			continue
		if src not in kinds:
			kinds.append(src)
			exact_fields[src], suffix_fields[src], labels[src] = [], [], src
		if rule.get("label") and labels[src] == src:
			labels[src] = rule.get("label")
		if rule.get("match", "exact") == "numeric_suffix":
			suffix_fields[src].append(tf)
		else:
			exact_fields[src].append(tf)

	values = {k: [str(v) for v in _all_values(fields, k)] for k in kinds}
	if not any(values[k] for k in kinds):
		return None
	# the most records the references can JOINTLY describe (one per value of the
	# richest reference kind); more distinct records than that means disagreement
	expected = max(len(values[k]) for k in kinds if values[k])

	# resolve every (kind, value) independently against the fetched rows
	resolutions = []
	for k in kinds:
		for v in values[k]:
			exact, exact_via = [], {}
			for row in rows:
				nm = row.get("name")
				for tf in exact_fields[k]:
					tval = row.get(tf)
					if tval in (None, "") or str(tval) != v:
						continue
					if nm not in exact:
						exact.append(nm)
						exact_via[nm] = tf
					break
			suffix, suffix_via = [], {}
			vnum = _trailing_int(v)
			if vnum is not None:
				for row in rows:
					nm = row.get("name")
					for tf in suffix_fields[k]:
						tval = row.get(tf)
						if tval in (None, ""):
							continue
						if _trailing_int(tval) == vnum:
							if nm not in suffix:
								suffix.append(nm)
								suffix_via[nm] = tf
							break
			resolutions.append({"kind": k, "label": labels[k], "value": v,
				"exact": exact, "suffix": suffix, "target": None, "strength": None,
				"via": None, "_exact_via": exact_via, "_suffix_via": suffix_via, "_dup": False})

	# phase 1 — hard proofs: a lone exact key resolves the reference; the SAME
	# exact key matching two records is a data contradiction (block everything)
	issues = []
	for r in resolutions:
		if len(r["exact"]) == 1:
			r["target"], r["strength"] = r["exact"][0], "exact"
			r["via"] = r["_exact_via"].get(r["target"])
		elif len(r["exact"]) > 1:
			r["_dup"] = True
			names = ", ".join(r["exact"])
			issues.append({"type": "conflict", "kind": r["kind"], "value": r["value"],
				"targets": list(r["exact"]),
				"reason": f"{r['label']} '{r['value']}' exactly matches {len(r['exact'])} "
					f"different records ({names}) — cannot decide"})
	proven = {r["target"] for r in resolutions if r["strength"] == "exact"}

	# phase 2 — weak (suffix) resolutions with a pairing tiebreak: a tail that
	# suffix-matches a record ALREADY proven by an exact key claims no NEW record
	# (consistent); a lone tail resolves but adds its record; a tail matching
	# several records with no proof to break the tie is ambiguous (human review)
	union = set(proven)
	for r in resolutions:
		if r["target"] is not None or r["_dup"]:
			continue
		paired = [t for t in r["suffix"] if t in proven]
		if len(paired) == 1:
			r["target"], r["strength"] = paired[0], "paired"
			r["via"] = r["_suffix_via"].get(r["target"])
		elif len(r["suffix"]) == 1:
			r["target"], r["strength"] = r["suffix"][0], "suffix"
			r["via"] = r["_suffix_via"].get(r["target"])
			union.add(r["target"])
		elif len(r["suffix"]) >= 2:
			names = ", ".join(r["suffix"])
			issues.append({"type": "ambiguous", "kind": r["kind"], "value": r["value"],
				"targets": list(r["suffix"]),
				"reason": f"{r['label']} '{r['value']}' matches {len(r['suffix'])} records "
					f"({names}) with no exact key to break the tie — needs human review"})
		# len == 0 → unresolved: INSUFFICIENT, not conflicting (may be a record
		# that carries no e-way bill) — no issue, and never auto-applied anyway

	# the count test: more distinct records claimed than the references can
	# jointly describe → they disagree (leads the reason string with the story)
	if len(union) > expected:
		story = "; ".join(
			f"{r['label']} '{r['value']}' → {r['target']} ({r['strength']} {r['via']})"
			for r in resolutions if r["target"] in union)
		issues.insert(0, {"type": "conflict", "kind": None, "value": None,
			"targets": sorted(union),
			"reason": (f"references disagree: {story}. These references jointly describe at "
				f"most {expected} {cfg.get('target_doctype')} record(s) but {len(union)} "
				f"different ones are claimed — not deciding; needs human review")})

	conflict = any(i["type"] == "conflict" for i in issues)
	clean = [{"kind": r["kind"], "label": r["label"], "value": r["value"],
		"target": r["target"], "strength": r["strength"], "via": r["via"],
		"exact": r["exact"], "suffix": r["suffix"]} for r in resolutions]
	return {"expected": expected, "resolutions": clean, "union": sorted(union),
		"issues": issues, "conflict": conflict,
		"reason": "; ".join(i["reason"] for i in issues) or None}


def check_conflict(action, fields, context):
	"""Convenience wrapper: parse_config + fetch_candidates + resolve_references
	for callers that hold no candidate rows yet. Returns None when matching is
	off or the conflict feature does not apply."""
	cfg = parse_config(action)
	if not cfg:
		return None
	rows = fetch_candidates(cfg, fields, context or {})
	return resolve_references(cfg, fields, rows)


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
	# Only evidence-backed candidates are shown by default: a row that matched a
	# real reference on the document (exact key / suffix). The date-net "recent
	# invoices" that matched nothing are NOT recommendations — showing them next
	# to a real match is misleading. Opt in per action via match_config.context_rows.
	context_rows = cint(cfg.get("context_rows"))
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
	# LEGACY-OFF return {"status": "candidates", "matches": matches,
	# LEGACY-OFF 	"note": "deterministic candidates — no model call"}
	# cross-reference consistency: even with zero model calls the document's own
	# references can disagree (EWB proves A, bill number points to B) — surface
	# it so nothing downstream auto-applies a side of a contradiction.
	refs = resolve_references(cfg, fields, rows)
	result = {"status": "candidates", "matches": matches,
		"note": "deterministic candidates — no model call"}
	if refs:
		result["references"] = refs               # per-reference resolution story for the reviewer
		if refs["conflict"]:
			result["status"] = "conflict"
			result["conflict"] = True
			result["conflict_reason"] = refs["reason"]
	return result


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

	# cross-reference consistency (see resolve_references): corroboration above
	# is per-row, so a wrongly-picked-but-corroborated record sails through. Here
	# we resolve EVERY reference independently and test them for mutual agreement.
	refs = resolve_references(cfg, fields, candidates)
	if refs:
		result["references"] = refs
		picked = {m["target"] for m in result["matches"]}
		proven = {r["target"] for r in refs["resolutions"] if r.get("strength") == "exact"}
		# surface deterministically-proven records the model ignored (an exact
		# EWB proof for A must be visible even when the model only picked B)
		for r in refs["resolutions"]:
			if r.get("strength") == "exact" and r["target"] not in picked:
				result["matches"].append({"target": r["target"], "confidence": 0.95,
					"reason": f"deterministic proof: exact {r['via']} matches the document's {r['kind']}",
					"corroborated": True, "proof": True})
		ambiguous_picked = any(i["type"] == "ambiguous" and picked & set(i.get("targets") or [])
			for i in refs["issues"])
		if refs["conflict"] or (proven and (picked - set(refs["union"]))):
			# references disagree, or the model chose a record no reference
			# supports while an exact proof points elsewhere — a human decides
			result["status"] = "conflict"
			result["conflict_reason"] = refs["reason"] or (
				"model matched " + ", ".join(sorted(picked - set(refs["union"])))
				+ " but exact reference proof points to " + ", ".join(sorted(proven)))
		elif ambiguous_picked and result["status"] == "matched":
			result["status"] = "doubt"
			result["downgraded"] = "picked one of several tied candidates — " + refs["reason"]

	state.step(
		"match",
		candidates=len(candidates),
		status=result["status"],
		threshold=threshold,
		corroborated=corroborated,
		matches=[{"target": m["target"], "confidence": m["confidence"]} for m in result["matches"]],
	)
	return result
