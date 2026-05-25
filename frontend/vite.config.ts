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
    // SettingsModal.svelte bundles ~386 integration cards (one per audit
    // module — R&D #1 through #112 + hardening sprints). The component
    // file is ~1.39 MB raw → ~1.75 MB JS bundle / ~403 kB gzip. Above
    // vite's 500 kB warning threshold but acceptable for a LAN-served
    // homelab dashboard (loaded once, kept open, no CDN round-trip).
    // See docs/bundle-size.md for the full analysis and the future
    // dynamic-import lazy-section refactor recommendation.
    chunkSizeWarningLimit: 2000,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:9999",
    },
  },
});
