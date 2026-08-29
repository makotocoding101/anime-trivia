import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The vite dev server owns the UI; the FastAPI process owns the game. Both
// surfaces proxy to :8000 so the browser sees one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
