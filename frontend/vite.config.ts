/**
 * 开发时 /api 会转发到 FastAPI。
 * 默认 http://127.0.0.1:8765；后端若改端口，在 frontend/.env.local 设置：
 *   VITE_PROXY_TARGET=http://127.0.0.1:8766
 */
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target =
    env.VITE_PROXY_TARGET?.trim() || 'http://127.0.0.1:8765';

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
        },
      },
    },
  };
});
