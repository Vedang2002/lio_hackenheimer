import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build lands inside the backend, which serves it: one service, one origin.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../backend/app/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
