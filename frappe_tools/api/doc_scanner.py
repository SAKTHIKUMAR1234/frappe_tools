from frappe_tools.utils import save_file_always_new
import frappe
from six import string_types

import base64
import io
import uuid
import json
from frappe.utils.file_manager import save_file
from frappe.utils import cint, sbool
from PIL import Image
from frappe import get_site_config
import redis

REDIS_SIGNAL_PREFIX = "doc_scanner_signal"

def get_redis():
    conf = get_site_config()
    return redis.Redis.from_url(
        conf.get("redis_cache", "redis://127.0.0.1:13000"),
        decode_responses=True
    )
def _signal_key(room):
	return f"{REDIS_SIGNAL_PREFIX}:{room}"

def add_to_signals(room, signal_data):

	r = get_redis()
	r.rpush(_signal_key(room), json.dumps(signal_data))


@frappe.whitelist(allow_guest=True)
def get_signal(room, timeout=25):
	
	r = get_redis()

	result = r.blpop(_signal_key(room), timeout=timeout)

	if not result:
		return []

	_, data = result
	return [json.loads(data)]


@frappe.whitelist(allow_guest=True)
def get_ice_servers():
	settings = frappe.get_single("Document Scanner Settings")
	ice_servers = []
	for server in settings.stun_and_turn_servers:
		config = {
			"urls": server.url
		}
		if server.username:
			config["username"] = server.username
		if server.password:
			config["credential"] = server.password
		ice_servers.append(config)
	return ice_servers

@frappe.whitelist(allow_guest=True)
def ping_to_device(device_type, event, room):
	frappe.publish_realtime(
		event=room,
		message={
			"device_type": device_type,
			"event": event
		}
	)


@frappe.whitelist(allow_guest=True)
def add_scanner(room):
	ping_to_device("mobile", "scanner_added", room)


@frappe.whitelist(allow_guest=True)
def remove_scanner(room):
	ping_to_device("mobile", "scanner_removed", room)


@frappe.whitelist(allow_guest=True)
def send_signal(room, signal_data, device):
	"""
	Bidirectional signaling entry point
	"""
	if device == "web":
		add_to_signals(room, signal_data)

	elif device == "mobile":
		frappe.publish_realtime(
			event=room,
			message={
				"device_type": "mobile",
				"event": "signal",
				"data": signal_data
			}
		)


@frappe.whitelist()
def get_docscanner_allowed_doctypes():
	return frappe.get_all(
		"Document Scanner Settings Items",
		fields=["doctype_link"],
		pluck="doctype_link"
	)


@frappe.whitelist()
def get_scanned_documents_list(doctype, docname):
	return frappe.get_all(
		"Scanned Document",
		filters={
			"_doctype": doctype,
			"_docname": docname
		},
		order_by="creation desc",
		fields=["*"]
	)


@frappe.whitelist()
def load_scanned_document_details(docname):
	document = frappe.get_doc("Scanned Document", docname)

	query = f"""
		SELECT 
			t1.page_no,
			t1.attachment,
			t1.layout_type,
			t1.page_type,
			t1.title,
			{ 't3.custom_is_s3_uploaded,' if "frappe_s3_integration" in frappe.get_installed_apps() else '' }
			{ 't3.custom_s3_key,' if "frappe_s3_integration" in frappe.get_installed_apps() else '' }
			t3.name,
			t3.file_type
		FROM `tabScanned Document Detail` t1
		LEFT JOIN `tabScanned Document` t2
			ON t1.scanner_document = t2.name
		LEFT JOIN `tabFile` t3
			ON t3.file_url = t1.attachment
		WHERE t1.is_deleted = 0
		  AND t2.name = {frappe.db.escape(docname)}
		ORDER BY t1.page_no ASC
		"""
	attachments = frappe.db.sql(
		query,
		as_dict=True
	)

	response = {
		"doctype": document._doctype,
		"docname": document._docname,
		"layout": document.scanner_layout,
		"attachments": []
	}

	for i in attachments:
		attach = {
			"page_no": i["page_no"],
			"attachment": i["attachment"],
			"page_type": i["page_type"],
			"layout_type": i["layout_type"],
			"title": i["title"]
		}

		if (
			"frappe_s3_integration" in frappe.get_installed_apps() and
			i["custom_is_s3_uploaded"]
			and i["custom_s3_key"]
		):
			from frappe_s3_integration.s3_core import getS3Connection
			connection = getS3Connection()
			attach["attachment"] = connection.get_pre_signed_url(
				i["name"],
				extra={
					"ResponseContentType": f"image/{i['file_type'].lower()}",
				},
			)

		response["attachments"].append(attach)

	return response


@frappe.whitelist()
def upload_image(image_data):
	if isinstance(image_data, string_types):
		image_data = frappe.json.loads(image_data)

	doc = frappe.new_doc("Scanned Document Detail")
	doc.update({
		"page_no": image_data["page_no"],
		"title": image_data["title"],
		"page_type": image_data["page_type"],
		"layout_type": image_data["layout_type"],
		"is_deleted": 1,
		"attachment": None
	})
	doc.save(ignore_permissions=True)

	if image_data.get("attachment"):
		file_name = create_image_upload(
			image_data["attachment"],
			"Scanned Document Detail",
			doc.name
		)
		doc.db_set("attachment", file_name)

	return doc.name


def create_image_upload(attach, doctype, docname):
	if attach.startswith("data:"):
		header, attach = attach.split(",", 1)
		mime = header.split(";")[0].split(":")[1]
		ext = mime.split("/")[-1]
	else:
		mime = "image/png"
		ext = "png"

	try:
		decoded = base64.b64decode(attach)
	except Exception:
		frappe.throw("Invalid image data")

	try:
		img = Image.open(io.BytesIO(decoded))
		output = io.BytesIO()
		save_format = img.format or ext.upper()
		img.save(output, format=save_format)
		content = output.getvalue()
		if "frappe_s3_integration" in frappe.get_installed_apps():
			setting = frappe.get_single("File Image Settings")
			if not setting.get("optimize_images_in_s3"):
				return
			from frappe_s3_integration.frappe_s3_integration.image_optimization.optimization_scheduler import optimize_image
			content = optimize_image(content=content, content_type=mime, optimize=True, quality=cint(setting.get("image_optimization_quantity")))
	except Exception:
		frappe.throw("Invalid or unsupported image format")

	file_name = f"{uuid.uuid4()}.{ext}"

	file_doc = save_file_always_new(
		fname=file_name,
		content=content,
		dt=doctype,
		df="attachment",
		dn=docname,
		is_private=1,
	)

	if (
		"frappe_s3_integration" in frappe.get_installed_apps()
		and not frappe.db.get_single_value(
			"AWS S3 Settings",
			"disable_s3_operations"
		)
	):
		file_doc.db_set("custom_is_s3_uploaded", 1)

	return file_doc.file_url


@frappe.whitelist()
def make_or_update_main_doc(
	doctype,
	layout,
	docname,
	is_new=False,
	scan_name=None,
	documents=[]
):
	is_new = sbool(is_new)

	if is_new or not scan_name:
		doc = frappe.new_doc("Scanned Document")
	else:
		doc = frappe.get_doc("Scanned Document", scan_name)

	doc.update({
		"_doctype": doctype,
		"_docname": docname,
		"scanner_layout": layout
	})

	if isinstance(documents, string_types):
		documents = frappe.json.loads(documents)

	doc.flags.documents = documents
	doc.save(ignore_permissions=True)
	doc.set_prev_documents_delete()
	doc.set_new_document_names()

	return doc.name


@frappe.whitelist()
def delete_scanned_docs(doc):
	frappe.get_doc("Scanned Document", doc).delete(
		ignore_permissions=True
	)
