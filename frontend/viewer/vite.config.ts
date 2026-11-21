import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// amazon-cognito-identity-js pulls in `buffer@4`, which reads the Node global `global`.
export default defineConfig({
  plugins: [react()],
  define: { global: "globalThis" },
  optimizeDeps: { esbuildOptions: { define: { global: "globalThis" } } },
  // fs.allow: portal imports backend/app/reserved_slugs.json (the one shared reserved-slug list)
  server: { fs: { allow: ["../.."] }, port: 5174, proxy: { "/api": "http://localhost:8000" } },
  build: {
    chunkSizeWarningLimit: 650,
    rollupOptions: {
      output: {
        // Keep the charting libraries out of the main bundle: they only load with the view page.
        manualChunks(id) {
          // Libraries shared by the main bundle and recharts live in "vendor" so the main bundle never imports the recharts chunk.
          if (/node_modules\/(react|react-dom|scheduler|react-is|prop-types|clsx|object-assign|react-transition-group|dom-helpers|@babel\/runtime)\//.test(id)) return "vendor";
          if (/node_modules\/(recharts|recharts-scale|victory-vendor|d3-[^/]+|internmap|react-smooth|decimal\.js-light|fast-equals)\//.test(id)) return "recharts";
        },
      },
    },
  },
});
