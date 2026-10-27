import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发与构建预览使用固定端口，便于核对演示环境。
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 4173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:18474" },
  },
  preview: { host: "0.0.0.0", port: 4173, strictPort: true },
});
