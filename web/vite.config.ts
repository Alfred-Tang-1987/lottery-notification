import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import UnoCSS from 'unocss/vite';

export default defineConfig({
  plugins: [vue(), UnoCSS()],
  server: {
    proxy: {
      "/api": "http://localhost:8280",
      "/auth": "http://localhost:8280",
    },
  },
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
});
