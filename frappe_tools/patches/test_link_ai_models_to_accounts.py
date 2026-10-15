from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import set_encrypted_password

from frappe_tools.ai.client import TASKS
from frappe_tools.patches import link_ai_models_to_accounts as patch_module

OPENROUTER_KEY = "sk-or-local-only-key"


class TestLinkAIModelsToAccounts(FrappeTestCase):
	def setUp(self):
		self._outcome.result.tb_locals = False
		frappe.set_user("Administrator")
		self.prefix = "LOCAL-PATCH-" + frappe.generate_hash(length=8)
		self.print = self.enterContext(patch("builtins.print"))

	def tearDown(self):
		frappe.db.rollback()

	def old_model(self, label, provider="OpenRouter", key=None, base_url=None):
		"""Insert an AI Model in the shape it had before accounts: no account, its own key and URL."""
		model = frappe.new_doc("AI Model")
		model.update(dict(model_label=f"{self.prefix} {label}", provider=provider, model_id=f"local/{label}", enabled=1))
		model.db_insert()
		if key:
			set_encrypted_password("AI Model", model.name, key, "api_key")
		if base_url:
			self.require_columns("AI Model", "base_url")
			frappe.db.sql("update `tabAI Model` set base_url = %s where name = %s", (base_url, model.name))
		return model.name

	def account(self, provider="OpenRouter", auth_type="API Key", key=OPENROUTER_KEY, suffix=""):
		return frappe.get_doc(dict(doctype="AI Provider Account", account_name=self.prefix + suffix, provider=provider,
			auth_type=auth_type, api_key=key, enabled=1)).insert()

	def require_columns(self, doctype, *columns):
		if not all(frappe.db.has_column(doctype, column) for column in columns):
			self.skipTest(f"{doctype} has no old columns on this site")

	def linked(self, name):
		return frappe.get_doc("AI Provider Account", frappe.db.get_value("AI Model", name, "account"))

	def test_model_with_the_same_key_reuses_the_account(self):
		account = self.account()
		model = self.old_model("same-key", key=OPENROUTER_KEY)
		patch_module.execute()
		self.assertEqual(self.linked(model).name, account.name)
		self.assertEqual(frappe.db.get_value("AI Model", model, "provider"), "OpenRouter")

	def test_new_key_gets_one_enabled_account_for_all_its_models(self):
		key = "sk-or-local-only-new-key"
		first = self.old_model("first", key=key)
		second = self.old_model("second", key=key)
		patch_module.execute()
		account = self.linked(first)
		self.assertEqual(self.linked(second).name, account.name)
		self.assertEqual((account.provider, account.auth_type, account.enabled), ("OpenRouter", "API Key", 1))
		self.assertEqual(account.get_password("api_key"), key)

	def test_unclear_provider_gets_a_disabled_account_and_keeps_the_key(self):
		model = self.old_model("openai", provider="OpenAI", key="local-only-openai-key")
		patch_module.execute()
		account = self.linked(model)
		self.assertFalse(account.enabled)
		self.assertIn("Check the provider", account.last_error)
		self.assertEqual(account.get_password("api_key"), "local-only-openai-key")

	def test_openrouter_url_maps_custom_provider_and_moves_the_base_url(self):
		model = self.old_model("custom", provider="Custom", key="local-only-custom-key",
			base_url="https://openrouter.ai/api/v1/chat/completions")
		patch_module.execute()
		account = self.linked(model)
		self.assertEqual((account.provider, account.base_url, account.enabled), ("OpenRouter", "https://openrouter.ai/api/v1", 1))

	def test_model_without_a_key_links_an_account_of_its_provider(self):
		self.account("OpenAI Codex", "ChatGPT Login", key=None)
		accounts = frappe.db.count("AI Provider Account")
		model = self.old_model("codex", provider="Codex OAuth")
		patch_module.execute()
		self.assertEqual(self.linked(model).provider, "OpenAI Codex")
		self.assertEqual(frappe.db.count("AI Provider Account"), accounts)

	def test_unreadable_key_gets_a_disabled_account_and_a_second_run_changes_nothing(self):
		model = self.old_model("no-key", base_url="https://local.invalid/v1")
		patch_module.execute()
		account = self.linked(model)
		self.assertFalse(account.enabled)
		self.assertIn("no readable API key", account.last_error)
		accounts = frappe.db.count("AI Provider Account")
		patch_module.execute()
		self.assertEqual((self.linked(model).name, frappe.db.count("AI Provider Account")), (account.name, accounts))

	def test_unreadable_key_or_unclear_provider_never_reuses_a_keyed_account(self):
		keyed = self.account()
		no_key = self.old_model("no-key")
		unclear = self.old_model("unclear", provider="Custom")
		patch_module.execute()
		self.assertNotIn(keyed.name, (self.linked(no_key).name, self.linked(unclear).name))
		self.assertFalse(self.linked(no_key).enabled or self.linked(unclear).enabled)

	def test_task_row_never_points_at_a_disabled_ai_model(self):
		self.require_columns("AI Task Model", "account", "model")
		account = self.account("OpenAI Codex", "ChatGPT Login", key=None)
		disabled = frappe.get_doc(dict(doctype="AI Model", model_label=f"{self.prefix} off", account=account.name,
			model_id="local-luna", enabled=0)).insert()
		row = self.old_task_row("Page Extraction", account.name)
		patch_module.execute()
		linked = frappe.db.get_value("AI Task Model", row, "ai_model")
		self.assertNotEqual(linked, disabled.name)
		self.assertEqual(frappe.db.get_value("AI Model", linked, ["model_id", "enabled"]), ("local-luna", 1))

	def test_task_rows_and_verifiers_point_at_one_ai_model(self):
		self.require_columns("AI Task Model", "account", "model")
		self.require_columns("Scanned Document AI Verifier", "ai_account", "model")
		account = self.account("OpenAI Codex", "ChatGPT Login", key=None)
		rows = [self.old_task_row(task, account.name) for task in ("Page Extraction", "Verifier")]
		verifier = frappe.get_doc(dict(doctype="Scanned Document AI Verifier", reference_doctype="ToDo", prompt="Check",
			max_documents=1, filters_data="[]")).insert()
		frappe.db.sql("update `tabScanned Document AI Verifier` set ai_account = %s, model = 'local-luna' where name = %s",
			(account.name, verifier.name))
		patch_module.execute()
		patch_module.execute()
		models = frappe.get_all("AI Model", filters={"account": account.name}, fields=["name", "model_id", "enabled",
			"supports_vision"])
		self.assertEqual(len(models), 1)
		self.assertEqual(models[0].name, f"{account.name} local-luna")
		self.assertEqual((models[0].enabled, models[0].supports_vision), (1, 1))
		linked = [frappe.db.get_value("AI Task Model", row, "ai_model") for row in rows]
		self.assertEqual(linked, [models[0].name] * 2)
		self.assertEqual(frappe.db.get_value("Scanned Document AI Verifier", verifier.name, "ai_model"), models[0].name)

	def test_legacy_settings_model_gets_one_row_per_task(self):
		model_id = self.legacy_settings()
		self.openrouter_account()
		patch_module.execute()
		patch_module.execute()
		rows = frappe.get_all("AI Task Model", filters={"parenttype": patch_module.SETTINGS},
			fields=["task", "ai_model", "enabled"], order_by="idx asc")
		self.assertEqual([row.task for row in rows], list(TASKS))
		self.assertEqual({(row.ai_model, row.enabled) for row in rows}, {(rows[0].ai_model, 1)})
		model = frappe.db.get_value("AI Model", rows[0].ai_model, ["account", "model_id", "max_tokens", "temperature"])
		self.assertEqual(tuple(model), ("OpenRouter", model_id, 8192, 0))

	def test_legacy_settings_model_without_openrouter_account_adds_no_rows(self):
		self.legacy_settings()
		frappe.db.delete("AI Provider Account", {"name": "OpenRouter"})
		patch_module.execute()
		self.assertFalse(frappe.db.count("AI Task Model", {"parenttype": patch_module.SETTINGS}))
		self.assertIn("no OpenRouter account", str(self.print.call_args))

	def test_legacy_settings_key_creates_the_openrouter_account(self):
		model_id = self.legacy_settings(max_tokens="32768", temperature="0.2")
		frappe.db.delete("AI Provider Account", {"name": "OpenRouter"})
		set_encrypted_password(patch_module.SETTINGS, patch_module.SETTINGS, OPENROUTER_KEY, "openrouter_api_key")
		patch_module.execute()
		account = frappe.get_doc("AI Provider Account", "OpenRouter")
		self.assertEqual((account.enabled, account.get_password("api_key")), (1, OPENROUTER_KEY))
		model = frappe.db.get_value("AI Model", {"account": "OpenRouter", "model_id": model_id},
			["max_tokens", "temperature"])
		self.assertEqual(tuple(model), (32768, 0.2))

	def test_legacy_settings_model_fills_only_tasks_without_a_row(self):
		self.legacy_settings()
		account = self.account("OpenAI Codex", "ChatGPT Login", key=None)
		codex = frappe.get_doc(dict(doctype="AI Model", model_label=f"{self.prefix} luna", account=account.name,
			model_id="local-luna", enabled=1)).insert()
		frappe.get_doc(dict(doctype="AI Task Model", parent=patch_module.SETTINGS, parenttype=patch_module.SETTINGS,
			parentfield="task_models", idx=1, task="Verifier", ai_model=codex.name, enabled=1)).db_insert()
		self.openrouter_account()
		patch_module.execute()
		rows = frappe.get_all("AI Task Model", filters={"parenttype": patch_module.SETTINGS}, fields=["task", "ai_model"])
		self.assertEqual(sorted(row.task for row in rows), sorted(TASKS))
		self.assertEqual([row.ai_model for row in rows if row.task == "Verifier"], [codex.name])
		self.assertNotIn(codex.name, [row.ai_model for row in rows if row.task != "Verifier"])

	def openrouter_account(self):
		if not frappe.db.exists("AI Provider Account", "OpenRouter"):
			frappe.get_doc(dict(doctype="AI Provider Account", account_name="OpenRouter", provider="OpenRouter",
				auth_type="API Key", api_key=OPENROUTER_KEY, enabled=1)).insert()

	def legacy_settings(self, max_tokens=None, temperature=None):
		"""Leave the settings with no task rows and old model values; return the model."""
		model_id = f"local/{self.prefix}"
		frappe.db.delete("AI Task Model", {"parenttype": patch_module.SETTINGS})
		values = {"model": model_id, "max_tokens": max_tokens, "temperature": temperature}
		frappe.db.delete("Singles", {"doctype": patch_module.SETTINGS, "field": ("in", list(values))})
		frappe.db.delete("__Auth", {"doctype": patch_module.SETTINGS, "fieldname": "openrouter_api_key"})
		for field, value in values.items():
			if value is not None:
				frappe.db.sql("insert into `tabSingles` (doctype, field, value) values (%s, %s, %s)",
					(patch_module.SETTINGS, field, value))
		return model_id

	def old_task_row(self, task, account):
		name = frappe.generate_hash(length=10)
		frappe.db.sql(
			"""insert into `tabAI Task Model` (name, parent, parenttype, parentfield, idx, task, account, model, enabled)
			values (%s, 'Document Extraction Settings', 'Document Extraction Settings', 'task_models', 99, %s, %s,
			'local-luna', 1)""",
			(name, task, account),
		)
		return name

