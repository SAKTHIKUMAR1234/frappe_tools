import { createApp } from 'vue';
import ImageScanner from './vue/Doc/Pages/ImageScanner.vue';
import DocumentListViewer from './vue/Doc/Pages/DocumentListViewer.vue';


import { createPinia } from 'pinia'
import piniaPersist from 'pinia-plugin-persistedstate'

const pinia = createPinia()
pinia.use(piniaPersist)


frappe.provide('frappe.frappe_tools.doc_scanner');


frappe.frappe_tools.doc_scanner.ImageScanner = class {
    constructor({wrapper, is_new, document_name, doctype, scan_name}={}) {
        this.$wrapper = $(wrapper);
        this.is_new = is_new;
        this.document_name = document_name;
        this.doctype = doctype;
        this.scan_name = scan_name;
        this.make_body();
    }
    
    make_body() {
        this.$page_container = $('<div class="attribute-value-template frappe-control">').appendTo(this.$wrapper);
        this.app = createApp(ImageScanner, {
            is_new: this.is_new,
            document_name: this.document_name,
            doctype: this.doctype,
            scan_name : this.scan_name
        });
        this.app.use(pinia);
        SetVueGlobals(this.app);
        this.vue = this.app.mount(this.$wrapper.get(0));
    }
};


frappe.frappe_tools.doc_scanner.DocumentListViewer = class {
    constructor({wrapper, doctype, docname}={}) {
        this.$wrapper = $(wrapper);
        this.doctype = doctype;
        this.docname = docname;
        this.make_body();
    }
    
    make_body() {
        this.$page_container = $('<div class="attribute-value-template frappe-control">').appendTo(this.$wrapper);
        this.app = createApp(DocumentListViewer, {
            doctype: this.doctype,
            docname: this.docname
        });
        this.app.use(pinia);
        SetVueGlobals(this.app);
        this.vue = this.app.mount(this.$wrapper.get(0));
    }
}