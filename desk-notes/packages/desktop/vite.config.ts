/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'node:path'

export default defineConfig(({ mode }) => ({
  base: mode === 'development' ? '/' : './',
  test: {
    environment: 'jsdom',
    globals: true,
    // A1：纳入 shared 包测试（此前默认 include 只覆盖 desktop/src，
    // shared 的 backendEventsStore/chatSessionSlice 等测试不在管线内）
    // V2.1 批次4：追加 tests/ 目录（electron 主进程安全单测 b4-e2/e3/e4）
    include: ['src/**/*.test.{ts,tsx}', '../shared/src/**/*.test.{ts,tsx}', 'tests/**/*.test.{ts,tsx,cjs}'],
    exclude: ['e2e/**', 'node_modules/**'],
    setupFiles: ['./src/test-setup.ts'],
  },
  resolve: {
    alias: {
      '@desk-notes/shared': path.resolve(__dirname, '../shared/src'),
    },
  },
  plugins: [
    react(),
    // V2.1 批次4（P1-14）：生产构建产物移除 meta CSP 的 script-src 'unsafe-inline'。
    // 模板保留 unsafe-inline 仅为 Vite dev HMR（内联注入）；生产 dist 无内联脚本，
    // 移除后 XSS 无法注入内联脚本（与 main.cjs 的 header CSP `script-src 'self'` 对齐）。
    {
      name: 'strip-unsafe-inline-from-dist-csp',
      apply: 'build',
      transformIndexHtml(html) {
        return html.replace(
          /script-src 'self' 'unsafe-inline';/,
          "script-src 'self';"
        )
      },
    },
    VitePWA({
      registerType: 'autoUpdate',
      devOptions: { enabled: false },
      includeAssets: ['icon.svg', 'icon-192.png', 'icon-512.png'],
      manifest: {
        name: '思忆便签',
        short_name: '便签',
        description: '轻量级浮动便签应用',
        start_url: '.',
        display: 'standalone',
        background_color: '#1a1a24',
        theme_color: '#1a1a24',
        lang: 'zh-CN',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2}'],
        // A5：桌宠设计参考图（public/pet/xiaowen/references/，2.5MB+）不进 PWA 预缓存，
        // 避免超过 workbox 默认 2MiB 上限导致构建失败。归档目录（archive-*/references/）同理。
        globIgnores: ['pet/xiaowen/references/**', 'pet/xiaowen/archive-*/**'],
        navigateFallback: '/index.html',
        runtimeCaching: [
          {
            urlPattern: /^https:\/\/fonts\.googleapis\.com\/.*/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'google-fonts-cache',
              expiration: { maxEntries: 10, maxAgeSeconds: 60 * 60 * 24 * 365 },
            },
          },
          {
            urlPattern: /^https:\/\/cdnjs\.cloudflare\.com\/.*/i,
            handler: 'StaleWhileRevalidate',
            options: {
              cacheName: 'cdn-cache',
              expiration: { maxEntries: 20, maxAgeSeconds: 60 * 60 * 24 * 30 },
            },
          },
          {
            urlPattern: /^https:\/\/wttr\.in\/.*/i,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'weather-cache',
              expiration: { maxEntries: 5, maxAgeSeconds: 60 * 60 },
              networkTimeoutSeconds: 3,
            },
          },
        ],
      },
    }),
  ],
  server: {
    port: 5190,
    host: '127.0.0.1',
    // 允许访问 node_modules 里的字体(本地化 Font Awesome),否则 woff2/ttf 被 fs.allow 拦截
    fs: { allow: ['..', '../../node_modules'] },
  },
  optimizeDeps: { exclude: ['three'] },
  build: {
    outDir: 'dist',
    sourcemap: false,
    minify: 'esbuild',
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          motion: ['framer-motion'],
          // 3.4：marked/turndown 经 utils/markdownConvert.ts 静态挂在
          // App→FloatingNote/AcrylicPanel 入口链上，拆出主 chunk；
          // @uiw/react-md-editor、react-syntax-highlighter、mermaid(含 katex)
          // 均已是 React.lazy/import() 动态 chunk，无需手动分组。
          markdown: ['marked', 'turndown'],
        },
      },
    },
  },
}))
