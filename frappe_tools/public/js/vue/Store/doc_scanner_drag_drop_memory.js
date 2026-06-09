import { defineStore } from "pinia";
import { imageToDataURL } from "../utils";

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
  }),

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

    dragEnd() {
      if (!this.is_dragging) {
        this.reset();
        return;
      }
      let curr_image = null;
      if (this.drag_image_section == "un_used_section") {
        curr_image = this.imagesList.splice(this.drag_image_page_no, 1)[0];
      } else {
        for(let i= 0 ;i<this.document_scanner_details['sections'][this.drag_image_section]['images'].length;i++){
          let image = this.document_scanner_details['sections'][this.drag_image_section]['images'][i];
          if(image['page_no'] == this.drag_image_page_no && image['page_type'] == this.drag_image_page_type){
            curr_image = image['attachment'];
            image['attachment'] = null;
            break;
          }
        }
      }

      if (this.curr_page_section == "un_used_section") {
        this.imagesList.splice(this.curr_page_no, 0, curr_image);
      } else {
        for (
          let i = 0;
          i <
          this.document_scanner_details.sections[this.curr_page_section][
            "images"
          ].length;
          i++
        ) {
          let section =
            this.document_scanner_details.sections[this.curr_page_section][
              "images"
            ][i];
          if (
            section["page_no"] == this.curr_page_no &&
            section["page_type"] == this.curr_page_type
          ) {
            section["attachment"] = curr_image;
            break;
          }
        }
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
        let page_map = {};
        let curr_page = 1;
        for (let j = 0; j < images.length; j++) {
          if (!page_map[images[j]["page_no"]]) {
            page_map[images[j]["page_no"]] = curr_page;
            curr_page += 1;
          }
          images[j]["page_no"] = page_map[images[j]["page_no"]];
        }
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

    // Auto-capture: drop a freshly-scanned image straight into the layout as a
    // new page in the first section, bypassing the Unused Scans carousel.
    // Returns false when there is no layout/section to append into.
    appendScannedPage(dataUrl) {
      const details = this.document_scanner_details;
      if (!details || !details.sections) return false;
      const sectionKeys = Object.keys(details.sections);
      if (!sectionKeys.length) return false;

      // Prefer a Series section (renders every page); else fall back to the first.
      const targetKey =
        sectionKeys.find(
          (k) => details.sections[k].section_type === "Series Vertical"
        ) || sectionKeys[0];
      const images = details.sections[targetKey].images;

      let maxPage = 0;
      for (const img of images) {
        if (img.page_no > maxPage) maxPage = img.page_no;
      }

      images.push({
        page_no: maxPage + 1,
        page_type: "Front",
        attachment: dataUrl,
      });
      return true;
    },

    async setImagesDetails(images) {
      this.imagesList = [];
      for (const e of images) {
        const dataUrl = await imageToDataURL(e);
        this.imagesList.push(dataUrl);
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
      this.reset();
    }
  },
});
