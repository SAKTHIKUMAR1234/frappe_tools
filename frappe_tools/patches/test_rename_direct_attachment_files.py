import os
import uuid
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_files_path

from frappe_tools.patches import rename_direct_attachment_files as rename_patch

SERVE_FILE = "/api/method/frappe_s3_integration.s3_core.serve_file"
BUCKET = "test-bucket"
SLIDESHOW = "Direct Attachment Rename Test"


class TestRenameDirectAttachmentFiles(FrappeTestCase):
	def setUp(self):
		self.started_at = frappe.db.sql("select now(6)")[0][0]
		self.file_names = []
		self.created_paths = []
		self.s3 = MagicMock()
		self.s3.verify_object.return_value = True
		self.s3.delete_file_from_bucket.return_value = False
		for target in ("commit", "rollback"):
			patcher = patch.object(frappe.db, target)
			patcher.start()
			self.addCleanup(patcher.stop)

	def tearDown(self):
		"""Remove only blobs this test wrote: its tmp files and the renamed copies of its rows."""
		frappe.db.delete("Error Log", {"method": ("like", "Direct attachment%"), "creation": (">=", self.started_at)})
		frappe.db.commit()
		current_names = frappe.get_all("File", filters={"name": ("in", self.file_names)}, pluck="file_name")
		for path in self.created_paths + [get_files_path(name, is_private=1) for name in current_names]:
			if os.path.exists(path):
				os.remove(path)

	def make_file(self, s3=True, doctype="User", docname="Administrator", file_name=None, **values):
		file_name = file_name or f"{uuid.uuid4()}.jpeg"
		name = frappe.generate_hash(length=10)
		row = {
			"doctype": "File", "name": name, "file_name": file_name, "is_private": 1, "is_folder": 0,
			"file_url": f"{SERVE_FILE}/{file_name}?file_id={name}" if s3 else f"/private/files/{file_name}",
			"attached_to_doctype": doctype, "attached_to_name": docname, "attached_to_field": "user_image",
		}
		if s3:
			row.update(custom_is_s3_uploaded=1, custom_s3_key=f"private/files/{file_name}", custom_s3_bucket_name=BUCKET)
		row.update(values)
		frappe.get_doc(row).db_insert()
		self.file_names.append(name)
		return frappe._dict(row)

	def make_local_blob(self, file_name, content=b"image-bytes"):
		path = get_files_path(file_name, is_private=1)
		with open(path, "wb") as handle:
			handle.write(content)
		self.created_paths.append(path)
		return path

	def set_parent_url(self, url):
		frappe.db.set_value("User", "Administrator", "user_image", url, update_modified=False)

	def get_parent_url(self):
		return frappe.db.get_value("User", "Administrator", "user_image")

	def run_patch(self, s3_connection="default"):
		select = rename_patch.get_old_direct_attachments
		only_test_rows = lambda: [row for row in select() if row.name in self.file_names]
		connection = self.s3 if s3_connection == "default" else s3_connection
		with patch.object(rename_patch, "get_old_direct_attachments", only_test_rows), patch.object(
			rename_patch, "get_s3_connection", return_value=connection
		):
			rename_patch.execute()

	def get_file(self, name):
		return frappe.db.get_value("File", name, ["file_name", "file_url", "custom_s3_key"], as_dict=True)

	def get_deleted_keys(self):
		return [call.args[0] for call in self.s3.delete_file_from_bucket.call_args_list]

	def has_error_log(self, title):
		return frappe.db.exists("Error Log", {"method": title})

	def test_s3_file_is_copied_repointed_and_old_object_kept(self):
		row = self.make_file()
		self.set_parent_url(row.file_url)
		self.run_patch()
		file = self.get_file(row.name)
		self.assertRegex(file.file_name, r"^User_Administrator_[a-z0-9]{10}\.jpeg$")
		self.assertEqual(file.file_url, f"{SERVE_FILE}/{file.file_name}?file_id={row.name}")
		self.assertEqual(file.custom_s3_key, f"private/files/{file.file_name}")
		self.s3.connection.copy_object.assert_called_once_with(
			Bucket=BUCKET, Key=file.custom_s3_key, CopySource={"Bucket": BUCKET, "Key": row.custom_s3_key}
		)
		self.s3.delete_file_from_bucket.assert_not_called()
		self.assertEqual(self.get_parent_url(), file.file_url)

	def test_old_s3_object_deleted_only_with_site_config_flag(self):
		row = self.make_file()
		with patch.dict(frappe.conf, {rename_patch.DELETE_OLD_S3_FLAG: 1}):
			self.run_patch()
		self.s3.delete_file_from_bucket.assert_called_once_with(row.custom_s3_key, BUCKET)

	def test_failed_old_s3_delete_is_logged(self):
		row = self.make_file()
		self.s3.delete_file_from_bucket.return_value = "error-log-name"
		with patch.dict(frappe.conf, {rename_patch.DELETE_OLD_S3_FLAG: 1}):
			self.run_patch()
		self.assertNotEqual(self.get_file(row.name).file_name, row.file_name)
		self.assertTrue(self.has_error_log(f"Direct attachment renamed, old S3 object not deleted: {row.name}"))

	def test_public_s3_file_keeps_public_read(self):
		file_name = f"{uuid.uuid4()}.jpeg"
		self.make_file(file_name=file_name, is_private=0, custom_s3_key=f"files/{file_name}")
		self.run_patch()
		self.assertEqual(self.s3.connection.copy_object.call_args.kwargs["ACL"], "public-read")

	def test_parent_holding_old_local_url_of_s3_file_is_repointed(self):
		row = self.make_file()
		self.set_parent_url(f"/{row.custom_s3_key}")
		self.run_patch()
		self.assertEqual(self.get_parent_url(), self.get_file(row.name).file_url)

	def test_suffixed_s3_key_repoints_parent_and_renames_local_copy(self):
		row = self.make_file()
		suffixed_name = row.file_name.replace(".jpeg", "a984d8.jpeg")
		frappe.db.set_value("File", row.name, "custom_s3_key", f"private/files/{suffixed_name}")
		old_path = self.make_local_blob(suffixed_name)
		self.set_parent_url(f"/private/files/{suffixed_name}")
		self.run_patch()
		file = self.get_file(row.name)
		self.assertEqual(self.get_parent_url(), file.file_url)
		self.assertFalse(os.path.exists(old_path))
		self.assertTrue(os.path.exists(get_files_path(file.file_name, is_private=1)))

	def test_child_table_attach_field_is_repointed(self):
		row = self.make_file(s3=False, doctype="Website Slideshow", docname=SLIDESHOW, attached_to_field="image")
		self.make_local_blob(row.file_name)
		slideshow = frappe.get_doc({
			"doctype": "Website Slideshow", "name": SLIDESHOW, "slideshow_name": SLIDESHOW,
			"slideshow_items": [{"name": frappe.generate_hash(length=10), "image": row.file_url}],
		})
		slideshow.db_insert()
		slideshow.slideshow_items[0].db_insert()
		self.run_patch()
		new_url = self.get_file(row.name).file_url
		self.assertNotEqual(new_url, row.file_url)
		item = slideshow.slideshow_items[0].name
		self.assertEqual(frappe.db.get_value("Website Slideshow Item", item, "image"), new_url)

	def test_local_file_is_renamed_on_disk(self):
		row = self.make_file(s3=False)
		old_path = self.make_local_blob(row.file_name)
		self.set_parent_url(row.file_url)
		self.run_patch()
		file = self.get_file(row.name)
		self.assertEqual(file.file_url, f"/private/files/{file.file_name}")
		self.assertEqual(self.get_parent_url(), file.file_url)
		self.assertFalse(os.path.exists(old_path))
		with open(get_files_path(file.file_name, is_private=1), "rb") as handle:
			self.assertEqual(handle.read(), b"image-bytes")
		self.s3.connection.copy_object.assert_not_called()

	def test_local_copy_beside_s3_object_is_renamed(self):
		row = self.make_file()
		old_path = self.make_local_blob(row.file_name)
		self.run_patch()
		file = self.get_file(row.name)
		self.assertFalse(os.path.exists(old_path))
		self.assertTrue(os.path.exists(get_files_path(file.file_name, is_private=1)))

	def test_post_commit_cleanup_failure_is_not_a_rename_failure(self):
		row = self.make_file(s3=False)
		self.make_local_blob(row.file_name)
		with patch.object(rename_patch.os, "remove", side_effect=OSError("busy")):
			self.run_patch()
		self.assertNotEqual(self.get_file(row.name).file_name, row.file_name)
		self.assertTrue(self.has_error_log(f"Direct attachment renamed, old blob cleanup failed: {row.name}"))
		self.assertFalse(self.has_error_log(f"Direct attachment rename failed: {row.name}"))

	def test_parent_field_pointing_elsewhere_is_left_alone(self):
		row = self.make_file()
		self.set_parent_url("/private/files/some-other-file.png")
		self.run_patch()
		self.assertNotEqual(self.get_file(row.name).file_name, row.file_name)
		self.assertEqual(self.get_parent_url(), "/private/files/some-other-file.png")

	def test_scanner_and_new_format_files_are_not_selected(self):
		scanner = self.make_file(doctype="Scanned Document Detail")
		renamed = self.make_file(file_name="User_Administrator_abcdefghij.jpeg")
		suffixed = self.make_file(file_name=f"{uuid.uuid4()}a984d8.jpeg")
		selected = [row.name for row in rename_patch.get_old_direct_attachments()]
		self.assertNotIn(scanner.name, selected)
		self.assertNotIn(renamed.name, selected)
		self.assertIn(suffixed.name, selected)

	def test_failure_on_one_file_is_logged_and_others_continue(self):
		broken, healthy = self.make_file(), self.make_file()

		def copy_object(**params):
			if params["CopySource"]["Key"] == broken.custom_s3_key:
				raise RuntimeError("copy failed")

		self.s3.connection.copy_object.side_effect = copy_object
		self.run_patch()
		self.assertEqual(self.get_file(broken.name).file_name, broken.file_name)
		self.assertNotEqual(self.get_file(healthy.name).file_name, healthy.file_name)
		self.assertTrue(self.has_error_log(f"Direct attachment rename failed: {broken.name}"))

	def test_unverified_copy_keeps_old_object_and_discards_new_one(self):
		row = self.make_file()
		self.s3.verify_object.return_value = False
		self.run_patch()
		self.assertEqual(self.get_file(row.name).file_name, row.file_name)
		deleted_keys = self.get_deleted_keys()
		self.assertNotIn(row.custom_s3_key, deleted_keys)
		self.assertEqual(len(deleted_keys), 1)
		self.assertRegex(deleted_keys[0], r"^private/files/User_Administrator_[a-z0-9]{10}\.jpeg$")

	def test_db_failure_after_copy_rolls_back_and_discards_new_blobs(self):
		row = self.make_file()
		old_path = self.make_local_blob(row.file_name)
		self.set_parent_url(row.file_url)
		frappe.db.savepoint("before_rename")
		rollback = lambda *args, **kwargs: frappe.db.sql("rollback to savepoint before_rename")
		frappe.db.rollback.side_effect = rollback
		with patch.object(rename_patch.DirectAttachmentRename, "repoint_attached_field", side_effect=RuntimeError):
			self.run_patch()
		self.assertEqual(self.get_file(row.name).file_name, row.file_name)
		self.assertEqual(self.get_parent_url(), row.file_url)
		self.assertTrue(os.path.exists(old_path))
		deleted_keys = self.get_deleted_keys()
		self.assertNotIn(row.custom_s3_key, deleted_keys)
		self.assertEqual(len(deleted_keys), 1)
		new_name = os.path.basename(deleted_keys[0])
		self.assertFalse(os.path.exists(get_files_path(new_name, is_private=1)))

	def test_s3_rows_skipped_without_connection_are_logged(self):
		row = self.make_file()
		self.run_patch(s3_connection=None)
		self.assertEqual(self.get_file(row.name).file_name, row.file_name)
		message = frappe.db.get_value("Error Log", {"method": "Direct attachment rename skipped 1 files"}, "error")
		self.assertIn(f"{row.name}: S3 operations unavailable", message)
		self.assertIn("bench --site", message)

	def test_shared_blob_is_skipped(self):
		first = self.make_file()
		second = self.make_file(file_name=first.file_name, file_url=f"{SERVE_FILE}/{first.file_name}?file_id=other")
		self.run_patch()
		self.assertEqual(self.get_file(first.name).file_name, first.file_name)
		self.assertEqual(self.get_file(second.name).file_name, first.file_name)
		self.s3.connection.copy_object.assert_not_called()
		self.s3.delete_file_from_bucket.assert_not_called()
