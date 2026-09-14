import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    allowedHosts: ['70a2-95-105-228-129.ngrok-free.app'],
    proxy: {
      '/api': 'http://127.0.0.1:8000'
    }
  }
})
