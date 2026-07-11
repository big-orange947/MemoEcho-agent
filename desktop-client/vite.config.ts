import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/** 配置桌面客户端的 React 渲染进程构建入口。 */
export default defineConfig({
  plugins: [react()],
  base: "./",
});
