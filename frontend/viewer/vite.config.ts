import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  envDir: "../..", // shared repo-root .env (only VITE_* vars are exposed to the bundle)
  server: { port: 5174 },
});
