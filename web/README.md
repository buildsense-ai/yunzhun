# Yunzhun Board

上游数据自动化看板前端 —— Nuxt 3 (SPA) + UnoCSS，生物实验室暗色主题。

## 开发

```bash
pnpm install
NUXT_PUBLIC_API_BASE=http://localhost:8000 \
NUXT_PUBLIC_API_KEY=dev-key-change-me \
pnpm dev          # http://localhost:3000
```

## 构建 / 部署

```bash
pnpm build
NUXT_PUBLIC_API_BASE=... NUXT_PUBLIC_API_KEY=... node .output/server/index.mjs
```

每 15s 轮询 `GET /v1/board`；点卡片看存储引用与逐文件下载记录。
