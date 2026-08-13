// defineConfig comes from vitest/config rather than vite so the `test` block is typed. Importing
// it from vite leaves `test` an unknown property and the config silently loses its type checking.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The UI is served to an ordinary browser tab on this machine only. It is not a public site and
// binds to loopback, matching the brain. See docs/ARCHITECTURE.md section 4.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // In production the brain serves this UI and the socket from one origin, so the client derives
    // its URL from window.location. The dev server is the only place those origins differ, and
    // proxying /ws makes the same client code correct here as well - one URL, no dev-only branch.
    proxy: { "/ws": { target: "ws://127.0.0.1:8765", ws: true } },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
