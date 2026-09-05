import json

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class DocumentExtraction(Document):
	def validate(self):
		self.source_count = len([row for row in self.source_files if row.source_file])
		self.page_count = len([row for row in self.pages if row.image])
		self._validate_unique_sources()
		self._validate_immutable_target()
		from frappe_tools.automation.revisions import validate_save
		validate_save(self, self.get_doc_before_save())
		from frappe_tools.extractors.phases import validate_save as validate_phases
		validate_phases(self, self.get_doc_before_save())

	def after_insert(self):
		self._auto_start()

	def on_update(self):
		self._auto_start()

	def set_status(self, status, commit=False):
		self.db_set("status", status)
		if commit:
			frappe.db.commit()

	def add_processing_event(self, stage, status=None, message=None, details=None):
		"""Append an audit row; the caller saves the parent in its own transaction."""
		self.append("processing_events", {
			"event_time": now_datetime(),
			"stage": stage,
			"status": status or self.status,
			"message": message,
			"details_json": json.dumps(details, default=str, ensure_ascii=False) if details else None,
		})

	@frappe.whitelist()
	def start_processing(self):
		"""Explicit retry/start entry point for code and the standard DocType form."""
		self.check_permission("write")
		from frappe_tools.extractors.service import enqueue_attached_sources
		return enqueue_attached_sources(self.name, enqueue_after_commit=True, check_permissions=True)

	def _auto_start(self):
		if (
			self.flags.get("skip_auto_process")
			or self.flags.get("source_preparation_scheduled")
			or not self.auto_process
			or self.status != "Draft"
			or not any(row.source_file for row in self.source_files)
			or any(row.image for row in self.pages)
		):
			return
		from frappe_tools.extractors.service import enqueue_attached_sources
		enqueue_attached_sources(self.name, enqueue_after_commit=True, check_permissions=False)
		self.flags.source_preparation_scheduled = True

	def _validate_unique_sources(self):
		files = [row.source_file for row in self.source_files if row.source_file]
		if len(files) != len(set(files)):
			frappe.throw(frappe._("The same source file cannot be attached twice to one processing run."))

	def _validate_immutable_target(self):
		previous = self.get_doc_before_save()
		if (
			previous
			and not previous.target_doctype
			and self.target_doctype
			and self.classification_phase in {"Classified", "Human Confirmed"}
		):
			return
		if previous and previous.target_doctype != self.target_doctype and (
			previous.status != "Draft" or previous.pages
		):
			frappe.throw(frappe._("Target DocType cannot be changed after source preparation begins."))
