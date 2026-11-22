import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The answer-contract test only loads app modules through Vite's SSR loader. The production
// plugins (Tailwind, PWA, dependency optimization) are irrelevant here and made the test slow
// and memory-hungry.
export default defineConfig({
  plugins: [react()],
  root: fileURLToPath(new URL("../..", import.meta.url)),
  resolve: {
    alias: { "@": fileURLToPath(new URL("../../src", import.meta.url)) },
  },
  // SSR externalizes dependencies; prebundling them for a browser is wasted work.
  optimizeDeps: { noDiscovery: true, include: [] },
});
