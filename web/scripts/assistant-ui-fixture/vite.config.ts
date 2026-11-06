import { fileURLToPath } from "node:url";
import base from "../../vite.config";

const src = fileURLToPath(new URL("../../src", import.meta.url));

// The synthetic API takes a private port so a real dev backend can stay on 8000.
export default {
  ...base,
  root: fileURLToPath(new URL("../..", import.meta.url)),
  resolve: {
    ...base.resolve,
    alias: { "@": src },
  },
  server: {
    port: 5174,
    proxy: { "/api": `http://127.0.0.1:${process.env.FIXTURE_PORT ?? 8001}` },
  },
};
