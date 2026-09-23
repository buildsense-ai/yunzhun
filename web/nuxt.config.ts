// Yunzhun Board — bio-pipeline kanban frontend (SPA, talks to the gateway REST API)
export default defineNuxtConfig({
  ssr: false,
  modules: ["@unocss/nuxt"],
  css: ["@unocss/reset/tailwind.css"],
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
        { rel: "preconnect", href: "https://fonts.googleapis.com" },
        {
          rel: "stylesheet",
          href: "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Noto+Sans+SC:wght@400;500;700&display=swap",
        },
      ],
    },
  },
  devtools: { enabled: false },
});
