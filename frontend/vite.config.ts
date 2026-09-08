import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

// In dev, the FastAPI backend runs on :8000 (pixi run dev-api). Vite
// proxies /api and /auth so the SPA can use same-origin cookies. In
// production the SPA is served by FastAPI itself, so no proxy is needed.
const apiTarget = `http://localhost:${process.env.SHELF_DEV_API_PORT ?? "8000"}`;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(process.env.VITE_APP_VERSION ?? "dev"),
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    // strictPort is deliberately left off: :5173 is *the* vite port, so a
    // second frontend checkout is often already on it, and sliding to :5174
    // costs nothing here. The SPA reaches the API through the proxy below,
    // which makes those calls same-origin — so the port it lands on isn't
    // in the backend's CORS allowlist, and OIDC redirect URIs are built from
    // the backend's own public_base_url rather than this one.
    proxy: {
      // `pixi run up --api-port N` sets this so the proxy follows the
      // backend. Bare `npm run dev` gets the default.
      "/api": apiTarget,
      "/auth": apiTarget,
      "/health": apiTarget,
    },
  },
});
