import { createApp } from 'vue';
import ImageScanner from './vue/Doc/Pages/ImageScanner.vue';
import QrCode from './vue/Doc/Components/QrCode.vue';

import { createPinia } from 'pinia'
import piniaPersist from 'pinia-plugin-persistedstate'

const pinia = createPinia()
pinia.use(piniaPersist)


frappe.provide('frappe.frappe_tools.doc_scanner');


frappe.frappe_tools.doc_scanner.ImageScanner = class {
    constructor({wrapper}={}) {
        this.$wrapper = $(wrapper);
        this.make_body();
    }
    
    make_body() {
        this.$page_container = $('<div class="attribute-value-template frappe-control">').appendTo(this.$wrapper);
        this.app = createApp(ImageScanner);
        this.app.use(pinia);
        SetVueGlobals(this.app);
        this.vue = this.app.mount(this.$wrapper.get(0));
    }
};
