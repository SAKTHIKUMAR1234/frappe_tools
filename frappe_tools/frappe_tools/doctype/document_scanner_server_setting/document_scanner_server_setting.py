# Copyright (c) 2025, sakthi123msd@gmail.com and contributors
# For license information, please see license.txt

from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.model.document import Document


class DocumentScannerServerSetting(Document):
	def validate(self):
		if urlsplit(str(self.url or "").strip()).scheme.lower() not in {"turn", "turns"}:
			return
		if not self.username or not self.password:
			frappe.throw(
				_("TURN server rows require both Username and Password"),
				title=_("Incomplete TURN Configuration"),
			)
