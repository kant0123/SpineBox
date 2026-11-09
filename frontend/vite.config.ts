import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { createMockBackend } from "./src/mock/mockBackend";

// `npm run dev:mock` のときだけ /api をモックで返す(バックエンド無しで画面を確かめる用)
function mockApi(): Plugin {
  // 遅延・失敗の再現: 「ページ数未確定の巻(入れ子)」は初回だけ 4 秒かかる。#/read/too-big は展開上限超過、#/read/999 は 404
  const backend = createMockBackend(undefined, {
    firstOpenDelayMs: 4000,
    failures: { "too-big": { status: 422, detail: "この巻を読めません: キャッシュの上限(2147483648 バイト)より大きい: 3221225472 バイト" } },
  });
  return {
    name: "spinebox-mock-api",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/api/")) return next();
        let body = "";
        req.on("data", (c) => (body += c));
        req.on("end", () => {
          const r = backend.handle(req.method ?? "GET", req.url!, body || undefined);
          setTimeout(() => {
            res.statusCode = r.status;
            res.setHeader("Content-Type", r.contentType);
            res.end(r.body);
          }, r.delayMs ?? 0);
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
    setupFiles: ["src/test/setup.ts"],
    fileParallelism: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
}));
