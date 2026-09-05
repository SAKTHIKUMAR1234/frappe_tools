import { createApp } from "vue";
import PrimeVue from "primevue/config";
import ToastService from "primevue/toastservice";
import { definePreset } from "@primeuix/themes";
import Aura from "@primeuix/themes/aura";

import App from "./App.vue";
import "primeicons/primeicons.css";
import "./capture.css";

const app = createApp(App);
const FrappePreset = definePreset(Aura, {
  semantic: {
    primary: {
      50: "{zinc.50}",
      100: "{zinc.100}",
      200: "{zinc.200}",
      300: "{zinc.300}",
      400: "{zinc.400}",
      500: "{zinc.500}",
      600: "{zinc.600}",
      700: "{zinc.700}",
      800: "{zinc.800}",
      900: "{zinc.900}",
      950: "{zinc.950}",
    },
    colorScheme: {
      light: {
        primary: {
          color: "#24574b",
          inverseColor: "#ffffff",
          hoverColor: "#194338",
          activeColor: "#12372e",
        },
        highlight: {
          background: "#edf4ee",
          focusBackground: "#dcebe0",
          color: "#24574b",
          focusColor: "#194338",
        },
      },
    },
  },
});

app.use(PrimeVue, {
  theme: {
    preset: FrappePreset,
    options: {
      darkModeSelector: ".app-dark",
      cssLayer: false,
    },
  },
});
app.use(ToastService);
app.mount("#app");
