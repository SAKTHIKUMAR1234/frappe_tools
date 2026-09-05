from unittest import TestCase
from unittest.mock import Mock, patch

from frappe_tools.automation import setup


class TestModelSetup(TestCase):
	def test_existing_model_configuration_is_preserved(self):
		for provider in ("Codex OAuth", "OpenRouter"):
			with self.subTest(provider=provider):
				frappe = Mock()
				frappe.db.exists.return_value = True
				doc = Mock(provider=provider)
				doc.get_password.return_value = None
				frappe.get_doc.return_value = doc
				with patch.object(setup, "frappe", frappe):
					result = setup.ensure_models()
				self.assertFalse(result["created"])
				self.assertEqual(result["provider"], provider)
				doc.set.assert_not_called()
				doc.save.assert_not_called()
				frappe.db.commit.assert_not_called()

	def test_missing_model_is_seeded(self):
		frappe = Mock()
		frappe.db.exists.return_value = False
		with patch.object(setup, "frappe", frappe):
			result = setup.ensure_models()
		self.assertTrue(result["created"])
		self.assertEqual(frappe.get_doc.call_args.args[0]["model_id"], "gpt-5.6-luna")
		frappe.get_doc.return_value.insert.assert_called_once_with(ignore_permissions=True)

	def test_missing_verifier_seeds_sol_separately(self):
		frappe = Mock()
		frappe.db.exists.return_value = False
		with patch.object(setup, "frappe", frappe):
			result = setup.ensure_verifier_model()
		self.assertTrue(result["created"])
		config = frappe.get_doc.call_args.args[0]
		self.assertEqual(config["model_id"], "gpt-5.6-sol")
		self.assertEqual(config["agent_max_concurrency"], 1)
		self.assertNotIn("fallback_model", config)

	def test_existing_verifier_is_not_overwritten(self):
		frappe = Mock()
		frappe.db.exists.return_value = True
		with patch.object(setup, "frappe", frappe):
			result = setup.ensure_verifier_model()
		self.assertFalse(result["created"])
		frappe.get_doc.assert_not_called()
