import os
import re
import shutil

import frappe
from frappe.utils import get_files_path

from frappe_tools.api.doc_scanner import get_direct_attachment_file_stem

OLD_FILE_NAME = re.compile(
	r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}([0-9a-f]{6})?\.[A-Za-z0-9]+$"
)
SCANNER_DOCTYPES = ("Scanned Document Detail", "Document Extraction")
FEATURE_START = "2025-12-30"
FILE_FIELDS = [
	"name", "file_name", "file_url", "is_private",
	"attached_to_doctype", "attached_to_name", "attached_to_field",
]
S3_FIELDS = ["custom_is_s3_uploaded", "custom_s3_key", "custom_s3_bucket_name", "custom_s3_source_url"]
LOCAL_URL_PREFIXES = ("/private/files/", "/files/")
DELETE_OLD_S3_FLAG = "rename_direct_attachments_delete_old_s3"
RERUN_COMMAND = "bench --site <site> execute frappe_tools.patches.rename_direct_attachment_files.execute"


def execute():
	"""Rename old uuid-named direct attachments to {doctype}_{docname}_{unique}.{ext}.

	Old S3 objects are kept unless site_config sets rename_direct_attachments_delete_old_s3,
	because restored backups and site copies share the bucket and still use the old keys."""
	files = get_old_direct_attachments()
	s3_connection = get_s3_connection() if any(is_s3_file(file) for file in files) else None
	skipped = []
	for file in files:
		try:
			skip_reason = get_skip_reason(file, s3_connection)
			if skip_reason:
				skipped.append(f"{file.name}: {skip_reason}")
				continue
			rename = DirectAttachmentRename(file, s3_connection)
			rename.run()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Direct attachment rename failed: {file.name}")
			continue
		rename.delete_old_blob()
	log_skipped(skipped)


def log_skipped(skipped):
	if skipped:
		message = "\n".join(skipped) + f"\n\nRerun after fixing: {RERUN_COMMAND}"
		frappe.log_error(title=f"Direct attachment rename skipped {len(skipped)} files", message=message)
		frappe.db.commit()


def get_old_direct_attachments():
	columns = frappe.db.get_table_columns("File")
	files = frappe.get_all(
		"File",
		filters=[
			["attached_to_doctype", "is", "set"],
			["attached_to_doctype", "not in", SCANNER_DOCTYPES],
			["attached_to_name", "is", "set"],
			["attached_to_field", "is", "set"],
			["creation", ">=", FEATURE_START],
			["is_folder", "=", 0],
			["file_name", "like", "%-%-4%-%-%.%"],
		],
		fields=FILE_FIELDS + [field for field in S3_FIELDS if field in columns],
		order_by="creation asc",
	)
	return [file for file in files if OLD_FILE_NAME.match(file.file_name or "")]


def get_s3_connection():
	if "frappe_s3_integration" not in frappe.get_installed_apps():
		return None
	from frappe_s3_integration.s3_core import getS3Connection, s3_operations_allowed

	if not s3_operations_allowed():
		return None
	try:
		return getS3Connection()
	except Exception:
		frappe.log_error(title="Direct attachment rename: S3 connection failed")
		return None


def is_s3_file(file):
	return bool(file.get("custom_is_s3_uploaded") and file.get("custom_s3_key"))


def get_local_url(file, file_name=None):
	return f"{'/private' if file.is_private else ''}/files/{file_name or file.file_name}"


def get_old_local_url(file):
	"""This File's own local url; the S3 key may carry a collision suffix missing from file_name."""
	if is_s3_file(file) and f"/{file.custom_s3_key}".startswith(LOCAL_URL_PREFIXES):
		return f"/{file.custom_s3_key}"
	if file.file_url.startswith(LOCAL_URL_PREFIXES):
		return file.file_url
	return get_local_url(file)


def get_skip_reason(file, s3_connection):
	if is_s3_file(file) and not s3_connection:
		return "S3 operations unavailable"
	if is_shared_blob(file):
		return "blob shared with another File"
	return None


def is_shared_blob(file):
	others = {"name": ("!=", file.name)}
	local_urls = list({get_local_url(file), get_old_local_url(file)})
	if frappe.db.count("File", {**others, "file_url": ("in", local_urls)}):
		return True
	if not is_s3_file(file):
		return False
	key_filters = {"custom_s3_key": file.custom_s3_key, "custom_s3_bucket_name": file.custom_s3_bucket_name}
	return bool(frappe.db.count("File", {**others, **key_filters}))


class DirectAttachmentRename:
	"""Copy one File's blob to the new name, repoint rows, commit, then drop the old blob."""

	def __init__(self, file, s3_connection):
		self.file = file
		self.s3_connection = s3_connection if is_s3_file(file) else None
		extension = os.path.splitext(file.file_name)[1]
		self.new_name = get_direct_attachment_file_stem(file.attached_to_doctype, file.attached_to_name) + extension
		self.old_local_url = get_old_local_url(file)
		self.old_disk_name = os.path.basename(self.old_local_url)
		self.old_path = get_files_path(self.old_disk_name, is_private=file.is_private)
		self.new_path = get_files_path(self.new_name, is_private=file.is_private)
		self.has_local_copy = os.path.exists(self.old_path)
		self.has_new_s3_object = False

	@property
	def new_s3_key(self):
		prefix = self.file.custom_s3_key.rpartition("/")[0]
		return f"{prefix}/{self.new_name}" if prefix else self.new_name

	def run(self):
		if not (self.s3_connection or self.has_local_copy):
			raise FileNotFoundError(self.old_path)
		try:
			self.copy_blob()
			self.update_records()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			self.discard_new_blob()
			raise

	def copy_blob(self):
		if self.s3_connection:
			self.copy_s3_object()
		if self.has_local_copy:
			shutil.copy2(self.old_path, self.new_path)

	def copy_s3_object(self):
		"""Server-side copy as in s3_normalize; public objects keep public-read."""
		self.s3_connection.ensure_operations_allowed()
		bucket = self.file.custom_s3_bucket_name
		params = {
			"Bucket": bucket,
			"Key": self.new_s3_key,
			"CopySource": {"Bucket": bucket, "Key": self.file.custom_s3_key},
		}
		if not self.file.is_private:
			params["ACL"] = "public-read"
		self.s3_connection.connection.copy_object(**params)
		self.has_new_s3_object = True
		if not self.s3_connection.verify_object(bucket, self.new_s3_key):
			raise frappe.ValidationError(f"Copied S3 object missing: {bucket}/{self.new_s3_key}")

	@property
	def new_file_url(self):
		if self.file.file_url.startswith(LOCAL_URL_PREFIXES):
			return get_local_url(self.file, self.new_name)
		return self.file.file_url.replace(self.file.file_name, self.new_name)

	@property
	def old_urls(self):
		"""Values that mean this File in an attach field: its file_url or its own local urls."""
		return list({self.file.file_url, get_local_url(self.file), self.old_local_url})

	def update_records(self):
		values = {"file_name": self.new_name, "file_url": self.new_file_url}
		if self.s3_connection:
			values["custom_s3_key"] = self.new_s3_key
		if self.file.get("custom_s3_source_url"):
			values["custom_s3_source_url"] = self.file.custom_s3_source_url.replace(self.old_disk_name, self.new_name)
		frappe.db.set_value("File", self.file.name, values, update_modified=False)
		self.repoint_attached_field(values["file_url"])

	def repoint_attached_field(self, new_url):
		doctype, name, field = self.file.attached_to_doctype, self.file.attached_to_name, self.file.attached_to_field
		if not frappe.get_meta(doctype).has_field(field):
			repoint_child_rows(doctype, name, field, self.old_urls, new_url)
		elif frappe.db.get_value(doctype, name, field) in self.old_urls:
			frappe.db.set_value(doctype, name, field, new_url, update_modified=False)

	def discard_new_blob(self):
		if self.has_new_s3_object:
			self.s3_connection.delete_file_from_bucket(self.new_s3_key, self.file.custom_s3_bucket_name)
		if self.has_local_copy and os.path.exists(self.new_path):
			os.remove(self.new_path)

	def delete_old_blob(self):
		"""Runs after commit, so a failure here only leaves an orphaned old blob."""
		try:
			if self.s3_connection and frappe.conf.get(DELETE_OLD_S3_FLAG):
				self.delete_old_s3_object()
			if self.has_local_copy and os.path.exists(self.new_path):
				os.remove(self.old_path)
		except Exception:
			frappe.log_error(title=f"Direct attachment renamed, old blob cleanup failed: {self.file.name}")

	def delete_old_s3_object(self):
		bucket, key = self.file.custom_s3_bucket_name, self.file.custom_s3_key
		error_log = self.s3_connection.delete_file_from_bucket(key, bucket)
		if error_log:
			frappe.log_error(
				title=f"Direct attachment renamed, old S3 object not deleted: {self.file.name}",
				message=f"{bucket}/{key} kept; see Error Log {error_log}",
			)


def repoint_child_rows(doctype, name, field, old_urls, new_url):
	"""Attach fields on child tables are recorded with the parent as attached_to_doctype."""
	for table_field in frappe.get_meta(doctype).get_table_fields():
		if not frappe.get_meta(table_field.options).has_field(field):
			continue
		filters = {"parenttype": doctype, "parent": name, "parentfield": table_field.fieldname, field: ("in", old_urls)}
		for row in frappe.get_all(table_field.options, filters=filters, pluck="name"):
			frappe.db.set_value(table_field.options, row, field, new_url, update_modified=False)
