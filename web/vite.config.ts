import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import UnoCSS from 'unocss/vite';

const API_ORIGIN = process.env.VITE_API_ORIGIN ?? 'http://localhost:8280';

export default defineConfig({
  plugins: [vue(), UnoCSS()],
  server: {
    proxy: {
      '/api': API_ORIGIN,
      '/auth': API_ORIGIN,
    },
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
});
