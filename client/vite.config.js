import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: mode === "production" ? "/app/" : "/",
  server: {
    port: 5173,
    // Same-origin `/api/...` when VITE_OPEN_CASE_API_BASE is unset must hit local
    // FastAPI (port 8000), not a remote default — otherwise the UI talks to
    // production and local-only case UUIDs return 4xx/5xx there while curl to
    // localhost:8000 succeeds.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
}));
