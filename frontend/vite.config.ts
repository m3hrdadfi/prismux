import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    proxy: {
      "/api": "http://localhost:8100",
      "/stats": "http://localhost:8100",
      "/metrics": "http://localhost:8100",
      "/charts": "http://localhost:8100",
      "/requests": "http://localhost:8100",
      "/health": "http://localhost:8100",
      "/reset": "http://localhost:8100",
      "/test": "http://localhost:8100",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
})
