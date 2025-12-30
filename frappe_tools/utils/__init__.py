import mimetypes
from copy import copy

import frappe
from frappe.utils.file_manager import (
    check_max_file_size,
    get_content_hash,
    get_file_name,
    save_file_on_filesystem,
)
from frappe.utils import call_hook_method, get_hook_method

def save_file_always_new(
    fname,
    content,
    dt,
    dn,
    folder=None,
    is_private=0,
    df=None,
):
    file_size = check_max_file_size(content)
    content_hash = get_content_hash(content)
    content_type = mimetypes.guess_type(fname)[0]
    fname = get_file_name(fname, content_hash[-6:])
    call_hook_method("before_write_file", file_size=file_size)
    write_file_method = get_hook_method(
        "write_file",
        fallback=save_file_on_filesystem
    )
    file_data = write_file_method(
        fname,
        content,
        content_type=content_type,
        is_private=is_private
    )
    file_data = copy(file_data)

    file_data.update({
        "doctype": "File",
        "attached_to_doctype": dt,
        "attached_to_name": dn,
        "attached_to_field": df,
        "folder": folder,
        "file_size": file_size,
        "is_private": is_private,
    })
    file_doc = frappe.get_doc(file_data)
    file_doc.flags.ignore_permissions = True
    file_doc.insert()

    return file_doc
