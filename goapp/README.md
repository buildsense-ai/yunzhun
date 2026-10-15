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
                           COS（cos-go-sdk-v5）、OBS（obs-go-sdk）、
                           S3/s3-compatible（minio-go v7）；
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
| 凭据化下载 | ✅ OSS/COS/OBS/S3(minio) 官方纯 Go SDK 全覆盖 |
| REST API / SMTP 发信 / POP3 | ❌ 桌面应用不需要（要外部接入就继续用 Python 网关） |

## 环境变量

同 Python 版：`YUNZHUN_VERCEL_GATEWAY_KEY`、`YUNZHUN_LLM_MODEL`、`YUNZHUN_DOWNLOAD_DIR`、
`YUNZHUN_SYNC_INTERVAL_SECONDS`、`YUNZHUN_IDLE_*`、`YUNZHUN_PIPELINE_*`、`YUNZHUN_WEBHOOK_URL`、
`YUNZHUN_DB`（默认 `yunzhun.db`）。

### 归档命名规范（自动）

拉取落盘按实验室规范组织：`<YYYY-M-D>/<组学目录>/<远端key>`

```
downloads/2026-9-28/
  10X/          10x转录组
  huada/        华大转录组
  vdj/          免疫组
  ATAC/         ATAC
  demultiplex/  demultiplex 拆分
  10X空转/      10X 空间转录
  华大空转/      华大空间转录
  other/        未匹配
```

类型判定是双层的：**Jev 语义判（主）**——批量提问里第 4 问 `assay`
（8 选项含置信度，能认 "scRNA-seq"/"Chromium"/"Stereo-seq" 等变体写法），
**关键词匹配（兜底）**——Jev 说 other 或未配置时用 `mailx.AssayDir`。
日期取邮件 INTERNALDATE。`script/` 目录留给脚本，不走邮件管道。

### 下载目标（`YUNZHUN_DOWNLOAD_DIR`）

- 本地路径：`./downloads`（默认）、`/Volumes/share/dir`（挂载点直接用）
- **SMB 直写**（无需挂载）：`smb://user:pass@host/share/sub/dir`
  ——纯 Go `go-smb2`，NTLM 认证，`.part`+rename 原子写，去重/校验/断点语义与本地一致
  例：`YUNZHUN_DOWNLOAD_DIR=smb://nas:secret@192.168.1.10/datasets/yunzhun`
