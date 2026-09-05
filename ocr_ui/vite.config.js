import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import path from "path";
import { documentAdapters } from "./adapter-build.js";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [documentAdapters(), vue()],
    resolve: {
      dedupe: ["vue", "primevue"],
      alias: {
        "@": path.resolve(process.cwd(), "src"),
      },
    },
    server: {
      port: 8097,
      proxy: {
        "/api": {
          target: env.VITE_PROXY_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true,
        },
        "/assets": {
          target: env.VITE_PROXY_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true,
        },
        "/private": {
          target: env.VITE_PROXY_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
    build: {
      manifest: true,
      outDir: "../frappe_tools/public/ocr_ui",
      emptyOutDir: true,
      target: "es2018",
    },
  };
});
