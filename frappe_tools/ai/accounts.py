"""Encrypted provider credentials and ChatGPT device login."""

import base64
import json
import time
from contextlib import contextmanager

import frappe
import requests
from frappe.database import get_db
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, get_datetime, now_datetime
from rq.timeouts import JobTimeoutException

from frappe_tools.ai.providers import ProviderError

DOCTYPE = "AI Provider Account"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_BASE = "https://auth.openai.com"
EVENT = "ai_provider_login"


def credentials(account: Document | dict, timeout: int = 120) -> dict:
	"""Load credentials without exposing them to Desk."""
	if isinstance(account, dict):
		return account
	if account.provider == "OpenAI Codex":
		return _codex_credentials(account, timeout)
	key = account.get_password("api_key", raise_exception=False)
	if not key:
		raise ProviderError(frappe._("Provider account has no API key or setup token"), 401)
	return {
		"provider": account.provider,
		"auth_type": account.auth_type,
		"base_url": account.base_url,
		"api_key": key,
	}


def _codex_credentials(account, timeout):
	"""Decide and refresh under the row lock so a rotated refresh token is never sent twice."""
	if _is_token_fresh(account):
		return _token_credentials(account)
	with _credential_transaction():
		current = _locked_account(account.name)
		stored = _token_credentials(current)
		if _is_token_fresh(current):
			return stored
		if not stored["refresh_token"]:
			raise ProviderError(frappe._("Connect the ChatGPT account before using it"), 401)
		error, status_code = _refresh_tokens(current, stored["refresh_token"], timeout)
		if not error:
			return _token_credentials(current)
		frappe.db.set_value(DOCTYPE, account.name, "last_error", error)
	raise ProviderError(error, status_code)


def _is_token_fresh(account):
	return bool(account.token_expires) and get_datetime(account.token_expires) > add_to_date(now_datetime(), minutes=5)


def _refresh_tokens(account, refresh_token, timeout):
	try:
		tokens = _auth_post(
			"/oauth/token",
			timeout,
			json={"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": refresh_token},
		)
		tokens["refresh_token"] = tokens.get("refresh_token") or refresh_token
		_store_tokens(account, tokens)
	except (requests.RequestException, ValueError) as exc:
		return _auth_error(exc), getattr(exc, "status_code", None) or 401
	return None, None


def _token_credentials(account):
	return {
		"provider": account.provider,
		"auth_type": account.auth_type,
		"base_url": account.base_url,
		"access_token": account.get_password("access_token", raise_exception=False),
		"refresh_token": account.get_password("refresh_token", raise_exception=False),
		"chatgpt_account_id": account.chatgpt_account_id,
	}


@contextmanager
def _credential_transaction():
	"""Release credential locks without committing the caller's work."""
	original = frappe.local.db
	conf = frappe.conf
	frappe.local.db = get_db(
		socket=conf.db_socket,
		host=conf.db_host,
		port=conf.db_port,
		user=conf.db_user or conf.db_name,
		password=conf.db_password,
		cur_db_name=conf.db_name,
	)
	try:
		yield
		frappe.db.commit()
	finally:
		frappe.db.close()
		frappe.local.db = original


def _locked_account(name):
	frappe.db.get_value(DOCTYPE, name, "name", for_update=True)
	return frappe.get_doc(DOCTYPE, name)


def _auth_post(path, timeout=30, **kwargs):
	response = requests.post(AUTH_BASE + path, timeout=timeout, **kwargs)
	try:
		if response.status_code >= 400:
			raise ProviderError(
				frappe._("ChatGPT authentication failed (HTTP {0})").format(response.status_code), response.status_code
			)
		result = response.json()
		if not isinstance(result, dict):
			raise ValueError(frappe._("Invalid token response"))
		return result
	finally:
		response.close()


def _auth_error(exc):
	if isinstance(exc, ProviderError):
		return str(exc)
	return frappe._("ChatGPT authentication failed ({0}). Connect the account again.").format(type(exc).__name__)


def _jwt_claims(token):
	try:
		payload = token.split(".")[1]
		claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
		if not isinstance(claims, dict):
			raise ValueError(frappe._("Invalid access token claims"))
		return claims
	except (ValueError, IndexError, AttributeError):
		raise ValueError(frappe._("Invalid access token")) from None


def _token_values(tokens):
	access = tokens.get("access_token")
	refresh = tokens.get("refresh_token")
	if not isinstance(access, str) or not isinstance(refresh, str) or not refresh:
		raise ValueError(frappe._("Access and refresh tokens are required"))
	claims = _jwt_claims(access)
	account_id = tokens.get("account_id") or claims.get("https://api.openai.com/auth", {}).get(
		"chatgpt_account_id"
	)
	lifetime = cint(tokens.get("expires_in")) or (cint(claims.get("exp")) - int(time.time()))
	if not account_id or not (tokens.get("expires_in") or claims.get("exp")):
		raise ValueError(frappe._("Access token needs an account ID and expiry"))
	return {
		"access_token": access,
		"refresh_token": refresh,
		"chatgpt_account_id": account_id,
		"token_expires": add_to_date(now_datetime(), seconds=lifetime),
		"last_error": "",
	}


def _store_tokens(account, tokens):
	account.update(_token_values(tokens))
	account.save(ignore_permissions=True)


def _login_account(name):
	frappe.only_for("System Manager")
	account = frappe.get_doc(DOCTYPE, name)
	account.check_permission("write")
	if account.provider != "OpenAI Codex" or account.auth_type != "ChatGPT Login":
		frappe.throw(frappe._("Select OpenAI Codex with ChatGPT Login first"))
	return account


@frappe.whitelist(methods=["POST"])
def import_auth_json(name: str, auth_json: str) -> dict:
	"""Import a Codex auth.json without storing its source text."""
	account = _login_account(name)
	frappe.local.form_dict.pop("auth_json", None)
	try:
		tokens = json.loads(auth_json)["tokens"]
		_store_tokens(account, tokens)
	except (ValueError, KeyError, TypeError, AttributeError):
		frappe.throw(frappe._("Invalid Codex auth.json. Supply tokens with a valid access token and refresh token."))
	return {"connected": True}


@frappe.whitelist(methods=["POST"])
def start_device_login(name: str) -> dict:
	"""Start headless ChatGPT login and notify the starting user."""
	_login_account(name)
	try:
		flow = _auth_post("/api/accounts/deviceauth/usercode", json={"client_id": CLIENT_ID})
		user_code = flow.get("user_code") or flow["usercode"]
		device_auth_id = flow["device_auth_id"]
	except (requests.RequestException, ValueError, KeyError) as exc:
		error = _auth_error(exc)
		with _credential_transaction():
			frappe.db.set_value(DOCTYPE, name, "last_error", error)
		frappe.throw(error)
	frappe.enqueue(
		complete_device_login,
		queue="long",
		timeout=960,
		enqueue_after_commit=True,
		name=name,
		user=frappe.session.user,
		device_auth_id=device_auth_id,
		user_code=user_code,
		interval=max(cint(flow.get("interval")), 1),
	)
	return {"verification_url": AUTH_BASE + "/codex/device", "user_code": user_code}


def complete_device_login(name: str, user: str, device_auth_id: str, user_code: str, interval: int) -> None:
	"""Poll once per server interval for at most fifteen minutes."""
	error = None
	try:
		code = _poll_device(device_auth_id, user_code, interval)
		tokens = _auth_post(
			"/oauth/token",
			data={
				"grant_type": "authorization_code",
				"client_id": CLIENT_ID,
				"code": code["authorization_code"],
				"code_verifier": code["code_verifier"],
				"redirect_uri": AUTH_BASE + "/deviceauth/callback",
			},
		)
		with _credential_transaction():
			_store_tokens(_locked_account(name), tokens)
	except Exception as exc:  # always tell the waiting dialog
		error = _auth_error(exc)
		if not isinstance(exc, (requests.RequestException, ValueError, KeyError, JobTimeoutException)):
			frappe.log_error("ChatGPT device login failed")
		with _credential_transaction():
			frappe.db.set_value(DOCTYPE, name, "last_error", error)
	frappe.publish_realtime(EVENT, {"name": name, "success": error is None, "error": error}, user=user)


def _poll_device(device_auth_id, user_code, interval):
	deadline = time.monotonic() + 900
	while time.monotonic() < deadline:
		try:
			return _auth_post(
				"/api/accounts/deviceauth/token",
				min(30, max(1, deadline - time.monotonic())),
				json={"device_auth_id": device_auth_id, "user_code": user_code},
			)
		except ProviderError as exc:
			if exc.status_code not in (403, 404):
				raise
		time.sleep(min(max(interval, 1), max(0, deadline - time.monotonic())))
	raise ProviderError(frappe._("ChatGPT device login expired. Start login again."))
