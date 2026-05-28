"""Embedding client + item semantic ranking — the optional semantic layer.

Degrades to a no-op whenever embeddings are disabled or the backend is
unreachable, so item matching never hard-fails because of this layer.

Default provider is a LOCAL Ollama server running EmbeddingGemma
(`ollama pull embeddinggemma`) — no API key, no external calls, invoice data
stays on the server. Google's embedding API is supported as an alternative.
"""

import hashlib
import json
import math

import frappe
import requests
from frappe.utils import cint

SETTINGS_DOCTYPE = "Document Extraction Settings"
EMBED_DOCTYPE = "Item Embedding"


def _settings():
	return frappe.get_single(SETTINGS_DOCTYPE)


def is_enabled():
	try:
		return bool(_settings().enable_embeddings)
	except Exception:
		return False


def embed(text):
	"""Return a vector (list[float]) for text, or None if disabled/unavailable."""
	if not text:
		return None
	s = _settings()
	if not s.enable_embeddings:
		return None

	provider = s.embedding_provider or "Ollama (Local)"
	model = s.embedding_model or "embeddinggemma"
	try:
		if provider == "Ollama (Local)":
			base = (s.embedding_base_url or "http://localhost:11434").rstrip("/")
			r = requests.post(f"{base}/api/embed", json={"model": model, "input": text}, timeout=30)
			r.raise_for_status()
			data = r.json()
			vecs = data.get("embeddings")
			if vecs:
				return vecs[0]
			if data.get("embedding"):
				return data["embedding"]
			return None
		if provider == "Google":
			key = s.get_password("embedding_api_key") if s.embedding_api_key else None
			if not key:
				return None
			url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent?key={key}"
			r = requests.post(
				url,
				json={"model": f"models/{model}", "content": {"parts": [{"text": text}]}},
				timeout=30,
			)
			r.raise_for_status()
			return (r.json().get("embedding") or {}).get("values")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Embedding call failed")
		return None
	return None


def _hash(text):
	return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def ensure_item_embedding(item_code, text, model):
	"""Return an item's cached embedding, computing + caching it if missing/stale."""
	h = _hash(text)
	existing = frappe.db.get_value(EMBED_DOCTYPE, item_code, ["text_hash", "vector"], as_dict=True)
	if existing and existing.text_hash == h and existing.vector:
		try:
			return json.loads(existing.vector)
		except Exception:
			pass

	vec = embed(text)
	if not vec:
		return None

	doc = (
		frappe.get_doc(EMBED_DOCTYPE, item_code)
		if frappe.db.exists(EMBED_DOCTYPE, item_code)
		else frappe.new_doc(EMBED_DOCTYPE)
	)
	doc.item = item_code
	doc.model = model
	doc.text_hash = h
	doc.dims = len(vec)
	doc.vector = json.dumps(vec)
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	return vec


def cosine(a, b):
	if not a or not b:
		return 0.0
	dot = sum(x * y for x, y in zip(a, b))
	na = math.sqrt(sum(x * x for x in a))
	nb = math.sqrt(sum(y * y for y in b))
	if not na or not nb:
		return 0.0
	return dot / (na * nb)


def rank_items_semantic(query_text, item_rows, limit=8):
	"""Rank candidate items by cosine similarity to the query text.

	item_rows: list of {name, item_name, description}. Returns
	[{value, label, score, method}] sorted best-first. Empty if disabled.
	"""
	if not is_enabled() or not query_text or not item_rows:
		return []
	qv = embed(query_text)
	if not qv:
		return []
	model = _settings().embedding_model or "embeddinggemma"

	scored = []
	for row in item_rows:
		text = " ".join(filter(None, [row.get("item_name"), row.get("description")])) or row["name"]
		iv = ensure_item_embedding(row["name"], text, model)
		if iv:
			scored.append((round(cosine(qv, iv), 3), row))
	scored.sort(key=lambda x: x[0], reverse=True)
	return [
		{"value": r["name"], "label": r.get("item_name") or r["name"], "score": s, "method": "semantic"}
		for s, r in scored[:limit]
	]
