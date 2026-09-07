import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

// In dev, the FastAPI backend runs on :8000 (pixi run dev-api). Vite
// proxies /api and /auth so the SPA can use same-origin cookies. In
// production the SPA is served by FastAPI itself, so no proxy is needed.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(process.env.VITE_APP_VERSION ?? "dev"),
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/auth": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
