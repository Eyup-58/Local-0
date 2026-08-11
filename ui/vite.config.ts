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
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
