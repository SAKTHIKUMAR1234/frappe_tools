import { defineStore } from "pinia";
import { imageToDataURL } from "../utils";

const SIDE_ORDER = { Front: 0, Back: 1 };

function findEmptySlot(images) {
  return images
    .filter((img) => !img.attachment)
    .sort((a, b) => a.page_no - b.page_no || SIDE_ORDER[a.page_type] - SIDE_ORDER[b.page_type])[0];
}

// Fills the first empty slot, else appends a page; a filled Single Page takes no more.
function placeScan(section, dataUrl) {
  const images = section.images;
  const slot = findEmptySlot(images);
  if (slot) {
    Object.assign(slot, { attachment: dataUrl, detail: null });
    return slot;
  }
  if (section.section_type === "Single Page" && images.length) return null;

  const page_no = Math.max(0, ...images.map((img) => img.page_no)) + 1;
  const front = { page_no, page_type: "Front", attachment: dataUrl };
  images.push(front);
  if (section.section_type.startsWith("Front And Back")) {
    images.push({ page_no, page_type: "Back", attachment: null });
  }
  return front;
}

function getPageIndex(images, page_no) {
  const pageNumbers = [...new Set(images.map((img) => img.page_no))].sort((a, b) => a - b);
  return pageNumbers.indexOf(page_no);
}

export const useGloabalDragMemory = defineStore("drag_and_drop_memory", {
  state: () => ({
    is_dragging: false,
    drag_image_section: null,
    drag_image_page_no: null,
    drag_image_page_type: null,
    curr_page_section: null,
    curr_page_no: null,
    curr_page_type: null,
    imagesList: [],
    document_scanner_details: {},
    focused_section: null,
  }),

  getters: {
    defaultScanSection(state) {
      const sections = state.document_scanner_details?.sections || {};
      const keys = Object.keys(sections);
      return keys.find((key) => sections[key].section_type === "Series Vertical") || keys[0];
    },
  },

  actions: {
    dragStart(curr_section, curr_page_no, curr_page_type) {
      this.is_dragging = true;
      this.drag_image_section = curr_section;
      this.drag_image_page_no = curr_page_no;
      this.drag_image_page_type = curr_page_type;
    },

    dragHover(hover_section, hover_page, hover_type) {
      this.curr_page_section = hover_section;
      this.curr_page_no = hover_page;
      this.curr_page_type = hover_type;
    },

    // The hovered section slot; null for the tray.
    dropSlot() {
      if (this.curr_page_section == "un_used_section") return null;
      const images = this.document_scanner_details.sections?.[this.curr_page_section]?.images || [];
      return images.find(
        (img) => img.page_no == this.curr_page_no && img.page_type == this.curr_page_type
      ) || null;
    },

    dragEnd() {
      const slot = this.dropSlot();
      // A scan can fill the hovered slot during the drag: keep both images by cancelling the move.
      if (!this.is_dragging || slot?.attachment) {
        this.reset();
        return;
      }
      // An image moves with its stored detail, so a save keeps its current file and original.
      let curr_image = null;
      if (this.drag_image_section == "un_used_section") {
        curr_image = this.imagesList.splice(this.drag_image_page_no, 1)[0];
      } else {
        const image = this.document_scanner_details.sections[this.drag_image_section].images.find(
          (img) => img.page_no == this.drag_image_page_no && img.page_type == this.drag_image_page_type
        );
        if (image) {
          curr_image = { attachment: image.attachment, detail: image.detail || null };
          Object.assign(image, { attachment: null, detail: null });
        }
      }

      if (this.curr_page_section == "un_used_section") {
        this.imagesList.splice(this.curr_page_no, 0, curr_image);
      } else if (slot) {
        Object.assign(slot, { attachment: curr_image?.attachment || null, detail: curr_image?.detail || null });
      }

      this.reset();
    },

    async setDocScannerDetails(details) {
      if (!details || !details.sections) {
        this.document_scanner_details = details;
        return;
      }
      let keys = Object.keys(details.sections);
      for (let i = 0; i < keys.length; i++) {
        let images = details.sections[keys[i]].images;
        this.renumberPages(images);
        await Promise.all(
          images.map(async (img) => {
            if (img.attachment) {
              img.attachment = await imageToDataURL(img.attachment);
            }
          })
        );
      }
      this.document_scanner_details = details;
      console.log(details);
    },

    // Number pages 1..n in their current order; same page_no stays one page.
    renumberPages(images) {
      const page_map = {};
      let curr_page = 1;
      for (const image of images) {
        if (!page_map[image.page_no]) {
          page_map[image.page_no] = curr_page;
          curr_page += 1;
        }
        image.page_no = page_map[image.page_no];
      }
    },

    focusSection(key) {
      this.focused_section = key;
    },

    // For layouts with auto_capture: place a scan in the focused section (else
    // the default one), filling the first empty slot before adding a page.
    // Returns { section, page_index } of the filled slot, or null when none fits.
    appendScannedPage(dataUrl) {
      const sections = this.document_scanner_details?.sections;
      if (!sections) return null;

      const keys = [this.focused_section, this.defaultScanSection].filter(
        (key) => key && sections[key]
      );
      for (const key of new Set(keys)) {
        const slot = placeScan(sections[key], dataUrl);
        if (slot) {
          return { section: key, page_index: getPageIndex(sections[key].images, slot.page_no) };
        }
      }
      return null;
    },

    // Tray images are { attachment, detail }; a new scan may be given as its data URL.
    async setImagesDetails(images) {
      this.imagesList = [];
      for (const e of images) {
        const image = typeof e === "string" ? { attachment: e, detail: null } : e;
        this.imagesList.push({ ...image, attachment: await imageToDataURL(image.attachment) });
      }
    },
    reset() {
      this.is_dragging = false;
      this.drag_image_section = null;
      this.drag_image_page_no = null;
      this.drag_image_page_type = null;
      this.curr_page_section = null;
      this.curr_page_no = null;
      this.curr_page_type = null;
    },
    clearAll() {
      this.imagesList = [];
      this.document_scanner_details = {};
      this.focused_section = null;
      this.reset();
    }
  },
});
