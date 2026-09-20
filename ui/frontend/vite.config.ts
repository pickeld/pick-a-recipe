import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: 'auto',
      includeAssets: ['favicon.svg', 'icons/*.png'],
      manifest: {
        id: 'pick-a-recipe-pwa',
        name: 'Pick-a-Recipe',
        short_name: 'Pick-a-Recipe',
        description: 'Extract and save recipes from social media videos',
        start_url: '/?source=pwa',
        scope: '/',
        display: 'standalone',
        display_override: ['standalone', 'minimal-ui'],
        background_color: '#0f172a',
        theme_color: '#6366f1',
        orientation: 'portrait-primary',
        prefer_related_applications: false,
        categories: ['food', 'lifestyle', 'utilities'],
        icons: [
          { src: '/icons/icon-72x72.png',   sizes: '72x72',   type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-96x96.png',   sizes: '96x96',   type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-128x128.png', sizes: '128x128', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-144x144.png', sizes: '144x144', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-152x152.png', sizes: '152x152', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-192x192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-192x192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
          { src: '/icons/icon-384x384.png', sizes: '384x384', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
        share_target: {
          action: '/share',
          method: 'POST',
          enctype: 'application/x-www-form-urlencoded',
          params: {
            title: 'title',
            text: 'text',
            url: 'url',
          },
        },
        shortcuts: [
          {
            name: 'New Recipe',
            short_name: 'New',
            description: 'Extract a new recipe from video',
            url: '/',
            icons: [{ src: '/icons/icon-192x192.png', sizes: '192x192' }],
          },
        ],
      },
      workbox: {
        // Adds the push / notificationclick listeners to the generated worker.
        // Kept as an import so everything below stays stock Workbox output.
        importScripts: ['/push-sw.js'],
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff,woff2}'],
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/socket\.io\//,
          /^\/auth\//,
          /^\/login/,
          /^\/logout/,
          /^\/share/,
          /^\/healthz/,
        ],
        runtimeCaching: [
          {
            urlPattern: /\/socket\.io\//,
            handler: 'NetworkOnly',
          },
          {
            urlPattern: /^\/api\//,
            handler: 'NetworkOnly',
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:5006', changeOrigin: true },
      // Socket.IO needs WebSocket upgrade passthrough
      '/socket.io': {
        target: 'http://localhost:5006',
        changeOrigin: true,
        ws: true,
      },
      // Auth + session endpoints stay on Flask
      '/login': { target: 'http://localhost:5006', changeOrigin: true },
      '/auth': { target: 'http://localhost:5006', changeOrigin: true },
      '/logout': { target: 'http://localhost:5006', changeOrigin: true },
      '/share': { target: 'http://localhost:5006', changeOrigin: true },
      '/healthz': { target: 'http://localhost:5006', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
