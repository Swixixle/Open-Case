import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: mode === "production" ? "/app/" : "/",
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "https://open-case.onrender.com",
        changeOrigin: true,
        secure: true,
      },
    },
  },
}));
