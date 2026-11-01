import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API serves the built app in production, so dev-only proxying keeps one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
