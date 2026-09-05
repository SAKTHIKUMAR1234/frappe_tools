from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from frappe_tools.extractors.generic.plugin import _link_candidates


class _Meta:
	def has_field(self, fieldname):
		return fieldname in {"supplier_name", "tax_id"}

	def get_title_field(self):
		return "supplier_name"


class TestGenericResolver(FrappeTestCase):
	@patch("frappe_tools.extractors.generic.plugin.frappe.get_list")
	@patch("frappe_tools.extractors.generic.plugin.frappe.get_meta", return_value=_Meta())
	def test_exact_resolver_uses_only_configured_fields_and_fixed_filters(self, _meta, get_list):
		get_list.return_value = [SimpleNamespace(name="SUP-1", supplier_name="Acme", tax_id="GST-1", get=lambda k, d=None: {"supplier_name": "Acme", "tax_id": "GST-1"}.get(k, d))]
		rows = _link_candidates("Supplier", "GST-1", {
			"match_fields": ["tax_id", "not_a_field"], "filters": {"disabled": 0},
		})
		self.assertEqual(rows[0]["value"], "SUP-1")
		self.assertTrue(rows[0]["exact"])
		self.assertEqual(rows[0]["matched_fields"], ["tax_id"])
		kwargs = get_list.call_args.kwargs
		self.assertEqual(kwargs["filters"], {"disabled": 0})
		self.assertEqual(kwargs["or_filters"], [["Supplier", "tax_id", "=", "GST-1"]])

	@patch("frappe_tools.extractors.generic.plugin.frappe.get_list", return_value=[])
	@patch("frappe_tools.extractors.generic.plugin.frappe.get_meta", return_value=_Meta())
	def test_search_resolver_is_bounded(self, _meta, get_list):
		_link_candidates("Supplier", "acme", {"match_fields": ["supplier_name"]}, search=True, limit=999)
		self.assertEqual(get_list.call_args.kwargs["limit_page_length"], 20)
		self.assertEqual(get_list.call_args.kwargs["or_filters"][0][2], "like")
