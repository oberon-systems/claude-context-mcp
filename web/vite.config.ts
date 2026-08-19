import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API and the graph are served by this project's own express server, so
// `npm run dev` proxies both rather than mounting a second copy of them.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist/client",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:3002",
      "/graph": "http://localhost:3002",
      "/vis-network.min.js": "http://localhost:3002",
    },
  },
});
