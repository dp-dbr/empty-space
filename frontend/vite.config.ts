import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

const backend = process.env.DPAC_BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  build: { chunkSizeWarningLimit: 4000 },
  server: {
    port: 5173,
    proxy: { "/api": backend, "/mcp": backend, "/oauth": backend },
  },
});
