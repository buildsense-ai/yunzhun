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

## PWA

- 可安装到桌面/手机主屏（standalone，DNA 图标，深色启动屏）
- Service Worker：应用壳预缓存 + 离线可开
- `/v1/board`、`/v1/messages/{id}/pulls` 走 NetworkFirst（5s 超时回缓存）
- 断网时渲染 localStorage 里最近一次看板快照，顶栏显示「离线缓存」
- Google Fonts CacheFirst 30 天
- `autoUpdate`：新版本上线自动换 SW，下次刷新生效
