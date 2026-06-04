/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Minimal Node `process` shim (config runs in Node; no @types/node dependency).
declare const process: { env: Record<string, string | undefined> };

// https://vitejs.dev/config/
// base: "/" for OCI single-origin serving; "/trend-score-dashboard/" for GitHub
// Pages (project site). Set via VITE_BASE env at build time.
export default defineConfig({
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/healthz": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
  },
});
