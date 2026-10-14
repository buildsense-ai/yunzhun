# Yunzhun (Go + MyGo)

邮件→判定→下载 的**全 Go 桌面应用**版：MyGo 原生 UI（`package ui`，GPU 直渲，无 HTML/JS/webview/cgo），单二进制。

Python 版仍在上级目录（REST 网关形态）；这个目录是自包含桌面应用——不跑服务器，不需要前端。

## 跑起来

```bash
cd goapp
YUNZHUN_VERCEL_GATEWAY_KEY=<vck key> \
go tool mygo dev        # 开发模式，改 .go 文件自动重载
# 打包: go tool mygo build  → build/darwin-arm64/Yunzhun.app
```

## 结构

```
main.go                    看板视图（7列）+ 加账号表单 + 详情抽屉
internal/
  config/                  env 配置（YUNZHUN_*，与 Python 版同名）
  store/                   sqlite（modernc，纯 Go）——同一套表结构
  mailx/                   IMAP（go-imap/v2 + 网易 ID 握手 + IDLE）、
                           MIME 解析（go-message + GBK）、
                           存储地址正则、邮件内 MD5 提取、UTF-7 文件夹名
  jev/                     SystemOne 判定客户端 + LLM 兜底提取
  pull/                    官方纯 Go SDK puller：OSS（aliyun-oss-go-sdk）、
                           COS（cos-go-sdk-v5）、OBS（obs-go-sdk）；
                           预签名 URL 走 stdlib GET；原子写+校验
  pipeline/                事件驱动编排：sync → judge → gate → pull，
                           IDLE 守护 goroutine、退避重连、失败计数
```

## 与 Python 版的对应

| 能力 | Go 版状态 |
|---|---|
| 增量同步/懒正文/网易握手 | ✅ go-imap/v2 移植 |
| 正则提取 + MD5 校验 | ✅ 完整移植（同测试） |
| Jev 判定 + LLM 兜底 | ✅ |
| 置信门禁 + 原子下载 + 去重 | ✅ |
| IDLE 推送 + 事件驱动 | ✅ goroutine |
| 凭据化下载 | ✅ OSS/COS/OBS 官方纯 Go SDK；S3 待接（预签名 https 已可直接下） |
| REST API / SMTP 发信 / POP3 | ❌ 桌面应用不需要（要外部接入就继续用 Python 网关） |

## 环境变量

同 Python 版：`YUNZHUN_VERCEL_GATEWAY_KEY`、`YUNZHUN_LLM_MODEL`、`YUNZHUN_DOWNLOAD_DIR`、
`YUNZHUN_SYNC_INTERVAL_SECONDS`、`YUNZHUN_IDLE_*`、`YUNZHUN_PIPELINE_*`、`YUNZHUN_WEBHOOK_URL`、
`YUNZHUN_DB`（默认 `yunzhun.db`）。
