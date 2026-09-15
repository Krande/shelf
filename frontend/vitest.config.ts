import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

// Kept separate from vite.config.ts so the dev/build config isn't carrying
// test-only settings — and so the tailwind plugin, which the tests don't
// need and which costs real time to run, stays out of the test pipeline.
export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify("test"),
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**"],
    },
  },
});
