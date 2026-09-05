import json
import frappe
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from frappe_tools.api import doc_scanner


class _Redis:
	def __init__(self):
		self.rows = {}

	def rpush(self, key, value):
		self.rows.setdefault(key, []).append(value)

	def expire(self, key, seconds):
		return key in self.rows and seconds > 0

	def blpop(self, key, timeout=0):
		rows = self.rows.get(key) or []
		return (key, rows.pop(0)) if rows else None


class TestDocumentScannerWebRTC(FrappeTestCase):
	@patch("frappe_tools.api.doc_scanner.redis.Redis.from_url")
	def test_redis_uses_the_active_sites_merged_configuration(self, from_url):
		with patch.object(frappe.local, "conf", frappe._dict(redis_cache="redis://127.0.0.1:13001")):
			doc_scanner.get_redis()
		from_url.assert_called_once_with("redis://127.0.0.1:13001", decode_responses=True, socket_connect_timeout=5, socket_timeout=30)

	@patch("frappe_tools.api.doc_scanner.get_redis")
	def test_zero_timeout_cannot_hold_a_worker_forever(self, get_redis):
		get_redis.return_value.blpop.return_value = None
		doc_scanner.get_signal("room-1", timeout=0)
		self.assertEqual(get_redis.return_value.blpop.call_args.kwargs["timeout"], 1)

	@patch("frappe_tools.api.doc_scanner.get_redis")
	def test_pin_registration_is_atomic(self, get_redis):
		get_redis.return_value.set.side_effect = [False, True]
		pin = doc_scanner.register_pin("room-1")
		self.assertEqual(len(pin), 4)
		self.assertEqual(get_redis.return_value.set.call_count, 2)
		self.assertEqual(get_redis.return_value.set.call_args.kwargs, {"ex": 600, "nx": True})

	@patch("frappe_tools.api.doc_scanner.frappe.publish_realtime")
	@patch("frappe_tools.api.doc_scanner.get_redis")
	def test_signals_are_queued_for_the_opposite_peer(self, get_redis, publish):
		redis = _Redis()
		get_redis.return_value = redis

		doc_scanner.send_signal("room-1", {"type": "offer", "sdp": "offer"}, "web")
		doc_scanner.send_signal("room-1", {"type": "answer", "sdp": "answer"}, "mobile")

		mobile = doc_scanner.get_signal("room-1", timeout=0, device="mobile")
		web = doc_scanner.get_signal("room-1", timeout=0, device="web")
		self.assertEqual(mobile, [{"type": "offer", "sdp": "offer"}])
		self.assertEqual(web, [{"type": "answer", "sdp": "answer"}])
		publish.assert_called_once()

	@patch("frappe_tools.api.doc_scanner.get_redis", return_value=_Redis())
	def test_invalid_signal_recipient_is_rejected(self, _redis):
		with self.assertRaises(Exception):
			doc_scanner.get_signal("room-1", timeout=0, device="other")
