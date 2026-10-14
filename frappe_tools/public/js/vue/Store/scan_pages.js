const slotKey = (page_no, page_type) => `${page_no}|${page_type}`;

// One page per stored detail and position, in server order (attached rows before empty ones).
// An empty slot at a taken position is dropped; an attached page at a taken position gets a new page.
export function storedPages(rows, title, layout_type) {
  const section_rows = rows.filter((row) => row.title === title && row.layout_type === layout_type);
  const details = new Set();
  const taken = new Set();
  let last_page_no = Math.max(0, ...section_rows.map((row) => row.page_no));
  const pages = [];

  for (const row of section_rows) {
    if (details.has(row.detail)) continue;
    details.add(row.detail);

    let page_no = row.page_no;
    if (taken.has(slotKey(page_no, row.page_type))) {
      if (!row.attachment) continue;
      last_page_no += 1;
      page_no = last_page_no;
    }
    taken.add(slotKey(page_no, row.page_type));
    pages.push({
      attachment: row.attachment,
      detail: row.attachment ? row.detail : null,
      page_no,
      page_type: row.page_type,
    });
  }
  return pages;
}

// A save retires every earlier detail of the scan. Point each image at the detail saved for it;
// an image without one (in the tray, or new to its slot) is uploaded again on the next save.
export function refreshSavedDetails(saved, images) {
  const saved_by_image = new Map(saved.map((row) => [row.image, row]));
  const renamed = new Map(saved.filter((row) => row.sent_detail).map((row) => [row.sent_detail, row.detail]));

  for (const image of images) {
    const row = saved_by_image.get(image);
    if (row?.attachment && image.attachment === row.attachment) {
      image.detail = row.detail;
    } else {
      image.detail = renamed.get(image.detail) || null;
    }
  }
}
