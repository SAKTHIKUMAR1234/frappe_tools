# Copyright (c) 2025, sakthi123msd@gmail.com and Contributors
# See license.txt

from unittest import TestCase

import frappe

from frappe_tools.api.doc_scanner import _get_ice_server_config


class TestDocumentScannerSettings(TestCase):
	def test_stun_server_does_not_require_credentials(self):
		server = frappe._dict(url="stun:stun.example.com:3478")

		self.assertEqual(
			_get_ice_server_config(server),
			{"urls": "stun:stun.example.com:3478"},
		)

	def test_turn_server_requires_username_and_credential(self):
		server = frappe._dict(url="turn:turn.example.com:3478", username="user")

		self.assertIsNone(_get_ice_server_config(server))

	def test_complete_turn_server_is_returned(self):
		server = frappe._dict(
			url="turns:turn.example.com:5349",
			username="user",
			password="secret",
		)

		self.assertEqual(
			_get_ice_server_config(server),
			{
				"urls": "turns:turn.example.com:5349",
				"username": "user",
				"credential": "secret",
			},
		)
