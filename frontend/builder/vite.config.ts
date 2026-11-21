import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// amazon-cognito-identity-js pulls in `buffer@4`, which reads the Node global `global` when it loads.
// Browsers don't have it, so map it to globalThis (source files and pre-bundled deps both).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { global: "globalThis" },
  optimizeDeps: { esbuildOptions: { define: { global: "globalThis" } } },
  // /api is proxied to the local FastAPI server, so no CORS is needed in dev.
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
