import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Output goes into the Python package's static/ folder so server.py serves
// it as-is without any extra wiring. Dev mode proxies /api to the running
// Python backend on port 9999.
export default defineConfig({
  plugins: [svelte()],
  // The Python backend serves /static/* — Vite needs to know this so it emits
  // <script src="/static/assets/index-XXX.js"> instead of /assets/...
  base: "/static/",
  build: {
    outDir: "../src/gpu_dashboard/static",
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:9999",
    },
  },
});
