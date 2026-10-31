import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { createMockBackend } from "./src/mock/mockBackend";

// `npm run dev:mock` のときだけ /api をモックで返す(バックエンド無しで画面を確かめる用)
function mockApi(): Plugin {
  const backend = createMockBackend();
  return {
    name: "spinebox-mock-api",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/api/")) return next();
        let body = "";
        req.on("data", (c) => (body += c));
        req.on("end", () => {
          const r = backend.handle(req.method ?? "GET", req.url!, body || undefined);
          res.statusCode = r.status;
          res.setHeader("Content-Type", r.contentType);
          res.end(r.body);
        });
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(mode === "mock" ? [mockApi()] : [])],
  server: {
    host: "127.0.0.1",
    proxy: mode === "mock" ? undefined : { "/api": "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
}));
