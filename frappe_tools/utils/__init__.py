import frappe


def save_file_always_new(
    fname,
    content,
    dt,
    dn,
    folder=None,
    is_private=0,
    df=None,
):
    """Create a NEW File doc for `content`, never reusing an existing File row.

    Hands the bytes to the File controller and lets it perform the single write.
    Writing the blob here first made File.before_insert write it a SECOND time
    under a hash-suffixed name (its generate_file_name saw the path already
    taken) and repoint file_url at that copy — orphaning the first blob, which
    nothing then referenced and no sweep could ever reclaim.

    Byte-identical content now shares one blob via core's dedup; the File doc is
    still always new, which is what callers need.
    """
    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": fname,
        "content": content,
        "decode": False,
        "attached_to_doctype": dt,
        "attached_to_name": dn,
        "attached_to_field": df,
        "folder": folder,
        "is_private": is_private,
    })
    file_doc.flags.ignore_permissions = True
    file_doc.insert()

    return file_doc
