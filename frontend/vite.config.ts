import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes straight into the backend so one container serves everything.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "../backend/app/static", emptyOutDir: true },
});
