import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时前端跑在 5173，接口代理到本机 runtime(8000) ——
// 用代理而不是 CORS：浏览器眼里始终是同源，生产构建后由 FastAPI 直接托管，
// 两边的请求路径完全一致（都走 /api/*），不会出现"开发能跑、构建后 404"。
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
