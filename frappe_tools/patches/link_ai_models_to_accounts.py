import frappe
from frappe.utils import cint, flt
from frappe.utils.password import get_decrypted_password

from frappe_tools.patches import create_openrouter_account

ACCOUNT = "AI Provider Account"
MODEL = "AI Model"
ACCOUNT_PROVIDERS = ("OpenRouter", "OpenAI Codex", "Anthropic")
SETTINGS = "Document Extraction Settings"


def execute() -> None:
	"""Move AI Model credentials to accounts, and point task rows and verifiers at AI Models."""
	for model in frappe.get_all(MODEL, filters={"account": ("is", "not set")}, fields=["name", "provider"]):
		account = get_or_create_account(model, _old_base_url(model.name))
		frappe.db.set_value(MODEL, model.name, {"account": account.name, "provider": account.provider}, update_modified=False)

	if _has_columns("AI Task Model", "account", "model"):
		rows = frappe.db.sql(
			"""select name, account, model from `tabAI Task Model`
			where ifnull(ai_model, '') = '' and ifnull(account, '') != '' and ifnull(model, '') != ''""",
			as_dict=True,
		)
		for row in rows:
			_link_model("AI Task Model", row.name, row.account, row.model)

	if _has_columns("Scanned Document AI Verifier", "ai_account", "model"):
		verifiers = frappe.db.sql(
			"""select name, ai_account, model from `tabScanned Document AI Verifier`
			where ifnull(ai_model, '') = '' and ifnull(ai_account, '') != '' and ifnull(model, '') != ''""",
			as_dict=True,
		)
		for verifier in verifiers:
			_link_model("Scanned Document AI Verifier", verifier.name, verifier.ai_account, verifier.model)

	add_legacy_task_rows()


def add_legacy_task_rows() -> None:
	"""Add an enabled row for the old settings model to each task that has no row, as the old fallback did."""
	rows = frappe.get_all("AI Task Model", filters={"parenttype": SETTINGS}, fields=["task", "idx"])
	tasks = frappe.get_meta("AI Task Model").get_field("task").options.split("\n")
	missing = [task for task in tasks if task not in {row.task for row in rows}]
	model_id = legacy_setting("model")
	if not missing or not model_id:
		return
	create_openrouter_account.execute()
	if not frappe.db.exists(ACCOUNT, "OpenRouter"):
		print(f"{SETTINGS}: no OpenRouter account for the old model {model_id}. Add rows in AI Task Models.")
		return

	ai_model = get_or_create_model("OpenRouter", model_id)
	for index, task in enumerate(missing, start=max((row.idx for row in rows), default=0) + 1):
		row = {"task": task, "ai_model": ai_model, "enabled": 1, "idx": index}
		parent = {"parent": SETTINGS, "parenttype": SETTINGS, "parentfield": "task_models"}
		frappe.get_doc({"doctype": "AI Task Model", **parent, **row}).db_insert()


def legacy_setting(field: str) -> str | None:
	"""Return an old settings value; the field is no longer in the DocType, so read tabSingles."""
	value = frappe.db.sql("select value from `tabSingles` where doctype = %s and field = %s", (SETTINGS, field))
	return ((value[0][0] if value else None) or "").strip() or None


def get_or_create_account(model: dict, base_url: str | None):
	"""Return the account with the model's provider, base URL and API key; create it when none matches."""
	key = get_decrypted_password(MODEL, model.name, "api_key", raise_exception=False)
	provider = account_provider(model.provider, key, base_url)
	account = find_account(provider, key, base_url) if provider else None
	return account or _create_account(model, provider, key, base_url)


def account_provider(provider: str | None, key: str | None, base_url: str | None) -> str | None:
	"""Map an old AI Model provider to an account provider; return None when it is not clear."""
	if provider in ACCOUNT_PROVIDERS:
		return provider
	if "codex" in (provider or "").lower():
		return "OpenAI Codex"
	if "openrouter.ai" in (base_url or "") or (key or "").startswith("sk-or-"):
		return "OpenRouter"
	return None


def find_account(provider: str, key: str | None, base_url: str | None):
	"""Return the first account with the same provider, base URL and API key; a ChatGPT login needs no key."""
	for name in frappe.get_all(ACCOUNT, filters={"provider": provider}, pluck="name", order_by="creation asc"):
		account = frappe.get_doc(ACCOUNT, name)
		if (account.base_url or None) != base_url:
			continue
		if provider == "OpenAI Codex" or (account.get_password("api_key", raise_exception=False) or None) == (key or None):
			return account
	return None


def _create_account(model, provider, key, base_url):
	message = _setup_message(model, provider, key)
	account = frappe.get_doc(
		{
			"doctype": ACCOUNT,
			"account_name": _free_name(ACCOUNT, provider or "OpenRouter"),
			"provider": provider or "OpenRouter",
			"auth_type": _auth_type(provider, key),
			"enabled": int(not message),
			"api_key": key,
			"base_url": base_url,
			"last_error": message,
		}
	).insert(ignore_permissions=True)
	if message:
		print(f"{ACCOUNT} {account.name}: {message}")
	return account


def _setup_message(model, provider, key):
	if not provider:
		return frappe._(
			"Created from AI Model {0} with provider {1}. Check the provider and the Base URL, then enable this account."
		).format(model.name, model.provider)
	if not key and provider != "OpenAI Codex":
		return frappe._(
			"Created from AI Model {0}, which had no readable API key. Add the API key, then enable this account."
		).format(model.name)
	if not key:
		return frappe._("Created from AI Model {0}. Connect the ChatGPT account, then enable this account.").format(
			model.name
		)
	return None


def _auth_type(provider, key):
	if provider == "OpenAI Codex":
		return "ChatGPT Login"
	if provider == "Anthropic" and (key or "").startswith("sk-ant-oat"):
		return "Setup Token"
	return "API Key"


def _link_model(doctype, name, account, model_id):
	if not frappe.db.exists(ACCOUNT, account):
		print(f"{doctype} {name}: AI Provider Account {account} does not exist. Set the AI Model manually.")
		return
	frappe.db.set_value(doctype, name, "ai_model", get_or_create_model(account, model_id), update_modified=False)


def get_or_create_model(account: str, model_id: str) -> str:
	"""Return the enabled AI Model for (account, model_id); create an enabled vision model when none exists."""
	existing = frappe.get_all(
		MODEL, filters={"account": account, "model_id": model_id, "enabled": 1}, pluck="name", order_by="creation asc", limit=1
	)
	if existing:
		return existing[0]
	return (
		frappe.get_doc(
			{
				"doctype": MODEL,
				"model_label": _free_name(MODEL, f"{account} {model_id}"),
				"enabled": 1,
				"account": account,
				"model_id": model_id,
				"supports_vision": 1,
				"supports_json_mode": 1,
				"max_tokens": cint(legacy_setting("max_tokens")) or 8192,
				"temperature": flt(legacy_setting("temperature")),
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _old_base_url(name):
	"""Return the base URL that I2A used as the full chat completions URL, as an account base URL."""
	if not frappe.db.has_column(MODEL, "base_url"):
		return None
	url = (frappe.db.sql("select base_url from `tabAI Model` where name = %s", name)[0][0] or "").strip().rstrip("/")
	return url.removesuffix("/chat/completions") or None


def _has_columns(doctype, *columns):
	return all(frappe.db.has_column(doctype, column) for column in columns)


def _free_name(doctype, base):
	name, number = base, 1
	while frappe.db.exists(doctype, name):
		number += 1
		name = f"{base} {number}"
	return name
