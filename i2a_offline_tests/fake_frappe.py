"""Minimal fake `frappe` + `requests` layer for the offline I2A test suite.

The real engine code (frappe_tools.i2a.*, essdee.*) imports `frappe`, `requests`
and `frappe.utils` at module load. `install()` registers lightweight fakes into
sys.modules BEFORE those app modules are imported, so the REAL engine logic runs
against an in-memory doc store and a scripted HTTP transport — no site, no
network, no production touch.

Surface intentionally covers only what the suite exercises; it is a test double,
not a Frappe reimplementation. Behaviour is kept faithful where a test asserts on
it (cint/flt coercion, get_all aggregates, nx cache lock, image redaction path).
"""

import json
import sys
import types
from datetime import date, datetime, timedelta


# ------------------------------------------------------------------ helpers

def cint(value):
	"""Frappe-style integer coercion: None/'' → 0, floats truncate."""
	if value is None or value == "":
		return 0
	if isinstance(value, bool):
		return int(value)
	try:
		return int(value)
	except (ValueError, TypeError):
		try:
			return int(float(str(value).replace(",", "").strip() or 0))
		except (ValueError, TypeError):
			return 0


def flt(value, precision=None):
	"""Frappe-style float coercion: None/'' → 0.0, strips commas."""
	if value is None or value == "":
		return 0.0
	if isinstance(value, bool):
		return float(value)
	try:
		out = float(value)
	except (ValueError, TypeError):
		try:
			out = float(str(value).replace(",", "").strip() or 0)
		except (ValueError, TypeError):
			return 0.0
	if precision is not None:
		out = round(out, precision)
	return out


def now_datetime():
	return datetime.now()


def now():
	return now_datetime().strftime("%Y-%m-%d %H:%M:%S.%f")


def getdate(value=None):
	if value is None:
		return date.today()
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	try:
		return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
	except (ValueError, TypeError):
		return date.today()


def add_days(value, days):
	return getdate(value) + timedelta(days=cint(days))


def add_to_date(value, days=0, seconds=0, hours=0, minutes=0, **kwargs):
	if value is None:
		value = now_datetime()
	if isinstance(value, str):
		try:
			value = datetime.strptime(value[:26], "%Y-%m-%d %H:%M:%S.%f")
		except (ValueError, TypeError):
			value = now_datetime()
	return value + timedelta(days=days, seconds=seconds, hours=hours, minutes=minutes)


def get_url(*args, **kwargs):
	return "http://localhost"


# ------------------------------------------------------------------ exceptions

class ValidationError(Exception):
	pass


class PermissionError(Exception):
	pass


class DoesNotExistError(Exception):
	pass


# ------------------------------------------------------------------ documents

class FakeRow(dict):
	"""Dict-backed doc double: attribute access AND item access, plus the doc
	methods the engine calls (insert / save / update / db_set / get_password /
	as_dict / append / parsed_schema)."""

	def __init__(self, data=None, doctype=None):
		super().__init__(data or {})
		object.__setattr__(self, "flags", types.SimpleNamespace(ignore_permissions=False))
		object.__setattr__(self, "_doctype", doctype or (data or {}).get("doctype"))
		object.__setattr__(self, "_store", None)

	# attribute surface -------------------------------------------------
	def __getattr__(self, name):
		# only reached when the attribute is not a real instance/class member;
		# unknown doc fields read as None so getattr(x, key, default) + cint()
		# behave like an absent-but-tolerated Frappe field.
		if name in self:
			return self[name]
		return None

	def __setattr__(self, name, value):
		if name in ("flags", "_doctype", "_store"):
			object.__setattr__(self, name, value)
		else:
			self[name] = value

	# doc methods -------------------------------------------------------
	def get(self, key, default=None):
		return self[key] if key in self else default

	def update(self, data=None, **kwargs):
		if data:
			super().update(data)
		if kwargs:
			super().update(kwargs)
		return self

	def insert(self, *args, **kwargs):
		store = object.__getattribute__(self, "_store")
		if store is not None:
			store._register(self)
		return self

	def save(self, *args, **kwargs):
		store = object.__getattribute__(self, "_store")
		if store is not None:
			store._register(self)
		return self

	def db_set(self, field, value=None, **kwargs):
		if isinstance(field, dict):
			super().update(field)
		else:
			self[field] = value
		return self

	def get_password(self, fieldname, raise_exception=True):
		return self.get(fieldname)

	def as_dict(self):
		return dict(self)

	def append(self, table, row=None):
		child = row if isinstance(row, FakeRow) else FakeRow(row or {})
		self.setdefault(table, [])
		self[table].append(child)
		return child

	def parsed_schema(self):
		raw = self.get("output_schema")
		if not raw:
			return []
		return raw if isinstance(raw, list) else json.loads(raw)


# ------------------------------------------------------------------ cache

class FakeCache:
	def __init__(self):
		self._d = {}

	def set(self, key, value, nx=False, ex=None, **kwargs):
		if nx and key in self._d:
			return False
		self._d[key] = value
		return True

	def set_value(self, key, value, **kwargs):
		self._d[key] = value
		return True

	def get(self, key, **kwargs):
		return self._d.get(key)

	def get_value(self, key, **kwargs):
		return self._d.get(key)

	def delete(self, key, **kwargs):
		self._d.pop(key, None)

	def delete_value(self, key, **kwargs):
		self._d.pop(key, None)


# ------------------------------------------------------------------ db

class FakeDB:
	def __init__(self, store):
		self._store = store
		self.committed = 0

	def commit(self):
		self.committed += 1

	def rollback(self):
		pass

	def get_value(self, doctype, name, fields=None, as_dict=False, **kwargs):
		# {filters} form: name may be a dict of filters
		row = self._store._resolve(doctype, name)
		if row is None:
			return None
		if fields is None:
			fields = ["name"]
		if isinstance(fields, str):
			return row.get(fields)
		out = {f: row.get(f) for f in fields}
		if as_dict:
			return out
		return [out[f] for f in fields]

	def set_value(self, doctype, name, field, value=None, update_modified=True, **kwargs):
		row = self._store._resolve(doctype, name)
		if row is None:
			row = self._store.seed(doctype, name if isinstance(name, str) else self._store._auto(doctype))
		if isinstance(field, dict):
			row.update(field)
		else:
			row[field] = value
		return None

	def exists(self, doctype, name=None):
		return self._store._resolve(doctype, name) is not None

	def get_single_value(self, doctype, field):
		row = self._store._single(doctype)
		return row.get(field) if row else None


# ------------------------------------------------------------------ store

def _match_cond(row, field, op, value):
	rv = row.get(field)
	op = str(op).lower()
	if op in ("=", "=="):
		return rv == value
	if op in ("!=", "<>"):
		return rv != value
	if op == "<":
		return rv is not None and rv < value
	if op == "<=":
		return rv is not None and rv <= value
	if op == ">":
		return rv is not None and rv > value
	if op == ">=":
		return rv is not None and rv >= value
	if op == "in":
		return rv in (value or [])
	if op == "not in":
		return rv not in (value or [])
	if op in ("like", "not like"):
		pat = str(value).replace("%", "")
		hit = pat in str(rv or "")
		return hit if op == "like" else not hit
	return rv == value


def _row_matches(row, filters):
	if not filters:
		return True
	if isinstance(filters, dict):
		for field, spec in filters.items():
			if isinstance(spec, (list, tuple)) and len(spec) == 2:
				if not _match_cond(row, field, spec[0], spec[1]):
					return False
			else:
				if not _match_cond(row, field, "=", spec):
					return False
		return True
	if isinstance(filters, (list, tuple)):
		for cond in filters:
			if isinstance(cond, (list, tuple)) and len(cond) == 3:
				if not _match_cond(row, cond[0], cond[1], cond[2]):
					return False
		return True
	return True


def _row_matches_or(row, or_filters):
	if not or_filters:
		return True
	if isinstance(or_filters, dict):
		for field, spec in or_filters.items():
			if isinstance(spec, (list, tuple)) and len(spec) == 2:
				if _match_cond(row, field, spec[0], spec[1]):
					return True
			elif _match_cond(row, field, "=", spec):
				return True
		return False
	if isinstance(or_filters, (list, tuple)):
		for cond in or_filters:
			if isinstance(cond, (list, tuple)) and len(cond) == 3 and _match_cond(row, cond[0], cond[1], cond[2]):
				return True
		return False
	return True


import re as _re

_AGG = _re.compile(r"^\s*(\w+)\s*\(\s*([\w*]+)\s*\)\s+as\s+(\w+)\s*$", _re.IGNORECASE)


class FakeFrappe(types.ModuleType):
	def __init__(self):
		super().__init__("frappe")
		self._docs = {}
		self._counters = {}
		self._cache = FakeCache()
		self.db = FakeDB(self)
		self.local = types.SimpleNamespace()
		self.session = types.SimpleNamespace(user="Administrator")
		self.flags = types.SimpleNamespace()
		# test-overridable hooks (mirrors real frappe module attributes)
		self.whitelisted = []
		self.get_attr = self._default_get_attr
		self.has_permission = lambda *a, **k: True
		# translation + exceptions + utils
		self._ = lambda s, *a, **k: s
		self.ValidationError = ValidationError
		self.PermissionError = PermissionError
		self.DoesNotExistError = DoesNotExistError
		self.utils = _make_utils_module()

	# ---- store internals ---------------------------------------------
	def reset(self):
		self._docs = {}
		self._counters = {}
		self._cache = FakeCache()
		self.db = FakeDB(self)
		self.local = types.SimpleNamespace()

	def _auto(self, doctype):
		self._counters[doctype] = self._counters.get(doctype, 0) + 1
		return f"{doctype}-{self._counters[doctype]:04d}"

	def _register(self, row):
		doctype = object.__getattribute__(row, "_doctype") or row.get("doctype")
		if not doctype:
			return row
		if not row.get("name"):
			row["name"] = self._auto(doctype)
		self._docs.setdefault(doctype, {})[row["name"]] = row
		return row

	def _resolve(self, doctype, name):
		"""Resolve a get_value target: a name string OR a {filters} dict."""
		table = self._docs.get(doctype, {})
		if isinstance(name, dict):
			for row in table.values():
				if _row_matches(row, name):
					return row
			return None
		return table.get(name)

	def _single(self, doctype):
		table = self._docs.get(doctype, {})
		return next(iter(table.values()), None)

	# ---- public frappe API -------------------------------------------
	def seed(self, doctype, name, **fields):
		row = FakeRow({"name": name, "doctype": doctype, **fields}, doctype=doctype)
		object.__setattr__(row, "_store", self)
		self._docs.setdefault(doctype, {})[name] = row
		return row

	def new_doc(self, doctype, **kwargs):
		row = FakeRow({"doctype": doctype, **kwargs}, doctype=doctype)
		object.__setattr__(row, "_store", self)
		return row

	def get_doc(self, doctype, name=None):
		if isinstance(doctype, dict):
			d = dict(doctype)
			dt = d.pop("doctype", None)
			return self.new_doc(dt, **d)
		row = self._resolve(doctype, name)
		if row is None:
			raise DoesNotExistError(f"{doctype} {name} not found")
		return row

	def get_single(self, doctype):
		row = self._single(doctype)
		if row is None:
			return self.seed(doctype, doctype)
		return row

	def get_cached_doc(self, doctype, name=None):
		return self.get_doc(doctype, name)

	def get_all(self, doctype, filters=None, or_filters=None, fields=None,
			limit=None, limit_page_length=None, order_by=None, pluck=None,
			ignore_permissions=None, **kwargs):
		rows = [r for r in self._docs.get(doctype, {}).values()
			if _row_matches(r, filters) and _row_matches_or(r, or_filters)]
		if order_by:
			key = str(order_by).split()[0]
			desc = "desc" in str(order_by).lower()
			rows = sorted(rows, key=lambda r: (r.get(key) is None, r.get(key)), reverse=desc)
		cap = limit or limit_page_length
		if cap:
			rows = rows[:cint(cap)]
		if pluck:
			return [r.get(pluck) for r in rows]
		# aggregate field spec: ["sum(total_tokens) as tokens", ...]
		if fields and any(_AGG.match(str(f)) for f in fields):
			agg = {}
			for f in fields:
				m = _AGG.match(str(f))
				if not m:
					continue
				func, col, alias = m.group(1).lower(), m.group(2), m.group(3)
				vals = [r.get(col) for r in rows if r.get(col) is not None]
				if func == "sum":
					agg[alias] = sum(flt(v) for v in vals)
				elif func == "max":
					agg[alias] = max((flt(v) for v in vals), default=0)
				elif func == "min":
					agg[alias] = min((flt(v) for v in vals), default=0)
				elif func == "count":
					agg[alias] = len(rows)
				else:
					agg[alias] = None
			return [FakeRow(agg)]
		if fields:
			out = []
			for r in rows:
				out.append(FakeRow({f: r.get(f) for f in fields if f != "*"}))
			return out
		return list(rows)

	def get_list(self, doctype, **kwargs):
		return self.get_all(doctype, **kwargs)

	def cache(self):
		return self._cache

	def throw(self, message, exc=None):
		raise (exc or ValidationError)(message)

	def msgprint(self, *args, **kwargs):
		pass

	def as_json(self, obj, indent=None):
		return json.dumps(obj, default=str, indent=indent)

	def log_error(self, *args, **kwargs):
		return None

	def get_traceback(self, *args, **kwargs):
		import traceback
		return traceback.format_exc()

	def only_for(self, *args, **kwargs):
		return None

	def whitelist(self, *args, **kwargs):
		# supports both @frappe.whitelist and @frappe.whitelist(...)
		if args and callable(args[0]) and not kwargs:
			return args[0]

		def deco(fn):
			return fn

		return deco

	def get_installed_apps(self, *args, **kwargs):
		return ["frappe", "frappe_tools", "essdee"]

	def get_hooks(self, *args, **kwargs):
		return {}

	def get_site_path(self, *args):
		import tempfile
		return tempfile.gettempdir()

	def _default_get_attr(self, path):
		raise AttributeError(f"no fake attr for {path} (set FRAPPE.get_attr in the test)")


def _make_utils_module():
	mod = types.ModuleType("frappe.utils")
	mod.cint = cint
	mod.flt = flt
	mod.now = now
	mod.now_datetime = now_datetime
	mod.getdate = getdate
	mod.add_days = add_days
	mod.add_to_date = add_to_date
	mod.get_url = get_url
	mod.cstr = lambda v: "" if v is None else str(v)
	return mod


# ------------------------------------------------------------------ requests

class FakeRequests(types.ModuleType):
	class Timeout(Exception):
		pass

	class ConnectionError(Exception):
		pass

	class RequestException(Exception):
		pass

	class HTTPError(Exception):
		pass

	class _Resp:
		def __init__(self, status_code, body=None, text=None):
			self.status_code = status_code
			self._body = body if body is not None else {}
			self.text = text if text is not None else json.dumps(self._body)

		def json(self):
			if isinstance(self._body, Exception):
				raise ValueError("no json")
			return self._body

		def raise_for_status(self):
			if self.status_code and self.status_code >= 400:
				raise FakeRequests.HTTPError(f"HTTP {self.status_code}")

	def __init__(self):
		super().__init__("requests")
		self.script = []
		self.calls = []
		# exceptions namespace (requests.exceptions.*)
		exc = types.ModuleType("requests.exceptions")
		exc.Timeout = FakeRequests.Timeout
		exc.ConnectionError = FakeRequests.ConnectionError
		exc.RequestException = FakeRequests.RequestException
		exc.HTTPError = FakeRequests.HTTPError
		self.exceptions = exc

	def post(self, url, headers=None, json=None, data=None, timeout=None, **kwargs):
		self.calls.append({"url": url, "headers": headers, "body": json, "timeout": timeout})
		if not self.script:
			return FakeRequests._Resp(200, {"choices": [{"message": {"content": "{}"},
				"finish_reason": "stop"}], "usage": {}})
		item = self.script.pop(0)
		if isinstance(item, Exception):
			raise item
		return item

	def get(self, url, **kwargs):
		return self.post(url, **kwargs)


# ------------------------------------------------------------------ install

def install():
	"""Register the fakes in sys.modules and return (FRAPPE, REQUESTS).

	Call this FIRST — before any `from frappe_tools...` / `from essdee...`
	import — so the app modules bind to these fakes at load time."""
	frappe = FakeFrappe()
	requests = FakeRequests()

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe.utils
	sys.modules["requests"] = requests
	sys.modules["requests.exceptions"] = requests.exceptions

	return frappe, requests
