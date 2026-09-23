// Yunzhun Board — bio-pipeline kanban frontend (SPA, talks to the gateway REST API)
export default defineNuxtConfig({
  ssr: false,
  modules: ["@unocss/nuxt", "@vite-pwa/nuxt"],
  css: ["@unocss/reset/tailwind.css"],
  pwa: {
    registerType: "autoUpdate",
    manifest: {
      name: "Yunzhun 上游数据看板",
      short_name: "Yunzhun",
      description: "邮件→判定→下载 的实时流水线看板",
      theme_color: "#070d0b",
      background_color: "#070d0b",
      display: "standalone",
      icons: [
        { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
        { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
        { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
      ],
    },
    workbox: {
      navigateFallback: "/",
      runtimeCaching: [
        {
          // board data: fresh when online, last-known when the gateway is down
          urlPattern: /\/v1\/board/,
          handler: "NetworkFirst",
          options: {
            cacheName: "api-board",
            networkTimeoutSeconds: 5,
            expiration: { maxEntries: 5, maxAgeSeconds: 600 },
          },
        },
        {
          urlPattern: /\/v1\/messages\/\d+\/pulls/,
          handler: "NetworkFirst",
          options: { cacheName: "api-pulls", networkTimeoutSeconds: 5 },
        },
        {
          urlPattern: /^https:\/\/fonts\.(googleapis|gstatic)\.com/,
          handler: "CacheFirst",
          options: {
            cacheName: "fonts",
            expiration: { maxEntries: 20, maxAgeSeconds: 60 * 60 * 24 * 30 },
          },
        },
      ],
    },
    client: { installPrompt: true },
  },
  runtimeConfig: {
    public: {
      apiBase: process.env.NUXT_PUBLIC_API_BASE || "http://localhost:8000",
      apiKey: process.env.NUXT_PUBLIC_API_KEY || "dev-key-change-me",
    },
  },
  app: {
    head: {
      title: "Yunzhun · 上游数据看板",
      link: [
        { rel: "icon", type: "image/png", href: "/favicon.png" },
        { rel: "apple-touch-icon", href: "/icons/apple-touch-icon.png" },
        { rel: "preconnect", href: "https://fonts.googleapis.com" },
        {
          rel: "stylesheet",
          href: "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Noto+Sans+SC:wght@400;500;700&display=swap",
        },
      ],
      meta: [{ name: "theme-color", content: "#070d0b" }],
    },
  },
  devtools: { enabled: false },
});
