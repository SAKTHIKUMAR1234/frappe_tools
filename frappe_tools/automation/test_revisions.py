import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_tools.automation import revisions, runtime
from frappe_tools.automation.contracts import Decision, ReferenceBundle
from frappe_tools.extractors import pipeline


class TestReviewRevisionSafety(FrappeTestCase):
	def review(self):
		doc = frappe.new_doc("Document Extraction")
		doc.update({"target_doctype": "ToDo", "status": "Review", "auto_process": 0, "auto_decide": 0})
		doc.append("extracted_fields", {
			"fieldname": "description", "fieldtype": "Data", "label": "Description",
			"value": "Reviewed text", "llm_value": "Reviewed text", "status": "Approved",
		})
		doc.insert(ignore_permissions=True)
		return doc

	def bind(self, doc, *, applied=True):
		revisions.bind(doc, Decision("review", 0.98, {}, []).as_dict(), applied=applied)
		doc.decision_phase = "Applied to Review" if applied else "Review Ready"
		with revisions.decision_write():
			doc.save(ignore_permissions=True)
		doc.reload()
		self.assertTrue(revisions.is_current(doc, applied=applied))

	def test_changed_field_invalidates_but_retains_audit(self):
		doc = self.review()
		self.bind(doc)
		old_decision = doc.decision_json
		doc.extracted_fields[0].value = "Changed after verification"
		doc.save()
		doc.reload()
		self.assertEqual(doc.decision_phase, "Stale")
		self.assertEqual(doc.decision_json, old_decision)
		self.assertTrue(revisions.creation_issues(doc))
		with self.assertRaises(frappe.ValidationError), patch("frappe_tools.extractors.pipeline.get_plugin") as plugin:
			pipeline.build(doc.name)
		plugin.return_value.target_for_build.assert_not_called()

	def test_unchanged_acknowledgement_keeps_verification(self):
		doc = self.review()
		self.bind(doc)
		doc.extracted_fields[0].status = "Edited"
		doc.save()
		doc.reload()
		self.assertTrue(revisions.is_current(doc, applied=True))
		self.assertEqual(revisions.creation_issues(doc), [])

	def test_rejection_invalidates_verification(self):
		doc = self.review()
		self.bind(doc)
		doc.extracted_fields[0].status = "Rejected"
		doc.save()
		self.assertEqual(doc.decision_phase, "Stale")

	def test_generic_api_cannot_forge_or_clear_decision_even_with_flags(self):
		doc = self.review()
		doc.flags.decision_write = True
		revisions.bind(doc, Decision("review", 1, {}, []).as_dict(), applied=True)
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)
		doc.reload()
		self.bind(doc)
		doc.decision_json = None
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)

	def test_legacy_unbound_decision_is_not_trusted(self):
		doc = self.review()
		doc.decision_json = json.dumps(Decision("review", 1, {}, []).as_dict())
		doc.decision_phase = "Applied to Review"
		with revisions.decision_write():
			doc.save()
		self.assertTrue(revisions.creation_issues(doc))

	def test_mapping_page_and_bbox_changes_affect_fingerprint(self):
		doc = self.review()
		doc.append("lines", {"row_no": 1, "table": "items", "raw_json": '{"qty":1}', "matched_item": "A"})
		doc.append("pages", {"page_no": 1, "image": "/private/files/a.jpg"})
		before = revisions.fingerprint(doc)
		for mutate in (
			lambda d: setattr(d.lines[0], "matched_item", "B"),
			lambda d: setattr(d.lines[0], "raw_json", '{"qty":2}'),
			lambda d: setattr(d.pages[0], "image", "/private/files/b.jpg"),
			lambda d: setattr(d.extracted_fields[0], "bbox_json", '{"x":0.2}'),
		):
			changed = copy.deepcopy(doc)
			mutate(changed)
			self.assertNotEqual(before, revisions.fingerprint(changed))

	def test_direct_child_db_edit_cannot_bypass_final_guard(self):
		doc = self.review()
		self.bind(doc)
		frappe.db.set_value("Document Extraction Field", doc.extracted_fields[0].name, "value", "Bypassed controller")
		with self.assertRaises(frappe.ValidationError):
			pipeline.build(doc.name)

	def test_agent_response_cannot_overwrite_a_new_review(self):
		doc = self.review()
		plugin = Mock()
		plugin.collect_references.return_value = ReferenceBundle("test", {}, {})
		plugin.decision_tools.return_value = []
		plugin.decision_instructions.return_value = "Read only"
		def response(*args, **kwargs):
			changed = frappe.get_doc("Document Extraction", doc.name)
			changed.extracted_fields[0].value = "Human correction during matching"
			changed.save()
			return {"content": '{"status":"review","confidence":1,"values":{}}'}
		with patch.object(runtime, "get_plugin", return_value=plugin), \
			patch.object(runtime, "_validate_document_agent_model"), \
			patch.object(runtime.agent_providers, "supports_live_tools", return_value=False), \
			patch.object(runtime.agent_providers, "call_with_tools", side_effect=response):
			result = runtime.decide(doc.name, "Document Verification Sol")
		doc.reload()
		self.assertEqual(result["status"], "handoff")
		self.assertEqual(doc.decision_phase, "Stale")
		self.assertEqual(doc.extracted_fields[0].value, "Human correction during matching")
		plugin.apply_decision.assert_not_called()
		plugin.validate_decision.assert_not_called()

	def test_apply_revalidates_records_and_binds_saved_staging(self):
		doc = self.review()
		self.bind(doc, applied=False)
		plugin = Mock()
		plugin.collect_references.return_value = ReferenceBundle("test", {}, {})
		plugin.validate_decision.side_effect = lambda doc, bundle, decision: decision
		def apply(extraction, decision):
			extraction.extracted_fields[0].value = "Verified selection"
			extraction.save()
			return {"ok": True}
		plugin.apply_decision.side_effect = apply
		with patch.object(runtime, "get_plugin", return_value=plugin):
			self.assertTrue(runtime.apply(doc.name)["ok"])
		doc.reload()
		self.assertTrue(revisions.is_current(doc, applied=True))
		self.assertEqual(doc.decision_phase, "Applied to Review")
		plugin.validate_decision.assert_called_once()

	def test_stale_apply_does_not_call_adapter(self):
		doc = self.review()
		self.bind(doc, applied=False)
		frappe.db.set_value("Document Extraction Field", doc.extracted_fields[0].name, "value", "New value")
		with patch.object(runtime, "get_plugin") as plugin, self.assertRaises(frappe.ValidationError):
			runtime.apply(doc.name)
		plugin.return_value.apply_decision.assert_not_called()

	def test_failed_post_insert_validation_rolls_back_draft(self):
		from frappe_tools.extractors.generic.plugin import GenericPlugin
		doc = self.review()
		plugin = GenericPlugin("ToDo")
		inserted = []
		def reject(ctx, target, extraction):
			inserted.append(target.name)
			self.assertTrue(frappe.db.exists("ToDo", target.name))
			frappe.throw("Synthetic accounting mismatch after insert")
		with patch.object(pipeline, "get_plugin", return_value=plugin), \
			patch.object(plugin, "after_insert", side_effect=reject), self.assertRaises(frappe.ValidationError):
			pipeline.build(doc.name)
		self.assertEqual(len(inserted), 1)
		self.assertFalse(frappe.db.exists("ToDo", inserted[0]))
		self.assertTrue(frappe.db.exists("Document Extraction", doc.name))
