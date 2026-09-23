# Yunzhun Mail Gateway

邮件基建：上游对接 **163/126 邮箱**（IMAP / POP3 / SMTP + 授权码），下游提供统一 **REST API** 读信、发信、管理附件。

```
┌────────────┐   IMAP 993 (读)   ┌─────────────────────┐   REST + X-API-Key   ┌──────────┐
│ 163 / 126  │ ◄────────────────►│  Yunzhun Mail       │ ◄───────────────────► │ 任意前端/ │
│  NetEase   │   POP3 995 (备)   │  Gateway            │                       │ 服务/脚本 │
│   服务器   │   SMTP 465 (发)   │  FastAPI + SQLite   │  /v1/...              │          │
└────────────┘                   └─────────────────────┘                       └──────────┘
```

## 为什么是网关形态

NetEase 不提供官方 REST API，第三方接入只有 IMAP/POP3/SMTP + 授权码一条路。本服务把协议层封装掉：

- **增量同步引擎**：按 `UIDVALIDITY` + UID 游标增量拉取，标记漂移（网页端已读/标星）定期回刷
- **懒加载正文**：同步只拉信头，首次 GET 正文时才 `BODY.PEEK[]` 拉全量并缓存入库
- **授权码加密**：Fernet 加密落库，密钥独立管理，DB 泄露不暴露凭据
- **NetEase 兼容**：自动处理 163 IMAP 必需的 `ID` 命令（否则报 `Unsafe Login. Please contact kefu@188.com`）；GBK/gb2312 老邮件解析

## 快速开始

```bash
pdm install
cp .env.example .env        # 改 YUNZHUN_API_KEY
pdm run uvicorn app.main:app --port 8000
# Swagger: http://localhost:8000/docs
```

### 获取 163 授权码

网页版 163 邮箱 → 设置 → POP3/SMTP/IMAP → 开启服务 → 获取**授权码**（不是登录密码）。

## API 速览

所有请求带 `X-API-Key` 头。

```bash
# 1. 注册账号（verify=true 会实测 IMAP+SMTP 登录）
curl -X POST :8000/v1/accounts -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"address": "you@163.com", "auth_code": "你的授权码"}'

# 2. 立即同步（平时由后台定时增量同步）
curl -X POST :8000/v1/accounts/1/sync -H "X-API-Key: $KEY" -d '{"mode": "incremental"}'
# mode=full 强制全量重建（UIDVALIDITY 变化时自动触发）

# 3. 文件夹列表（INBOX / 草稿箱 / 已发送 / 垃圾邮件 / 废纸篓…）
curl :8000/v1/accounts/1/folders -H "X-API-Key: $KEY"

# 4. 读信列表（headers-only，秒回）
curl ":8000/v1/accounts/1/messages?folder=INBOX&limit=50&unseen_only=false&q=发票" -H "X-API-Key: $KEY"

# 5. 读正文（首次触发懒加载；?mark_seen=true 顺手置已读）
curl ":8000/v1/messages/42?mark_seen=true" -H "X-API-Key: $KEY"

# 6. 原始 RFC 822 / 附件下载
curl ":8000/v1/messages/42/raw" -H "X-API-Key: $KEY"
curl ":8000/v1/messages/42/attachments/7" -H "X-API-Key: $KEY" -o file.pdf

# 7. 标记：已读/未读/标星/删除
curl -X PATCH :8000/v1/messages/42/flags -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"add": ["\\Flagged"], "remove": ["\\Seen"]}'
curl -X DELETE :8000/v1/messages/42 -H "X-API-Key: $KEY"   # IMAP \\Deleted + EXPUNGE

# 8. 发信（含 base64 附件；NetEase SMTP 自动归档到已发送）
curl -X POST :8000/v1/accounts/1/send -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"to": ["who@example.com"], "subject": "Hi", "text": "hello",
       "attachments": [{"filename": "a.txt", "content_base64": "aGVsbG8="}]}'

# 9. POP3 备用通道（列最近 N 封摘要）
curl ":8000/v1/accounts/1/pop3/messages?limit=20" -H "X-API-Key: $KEY"

# 10. 对象存储引用提取（OSS/OBS/COS/S3/GCS/Azure/MinIO）
curl ":8000/v1/messages/42/objects" -H "X-API-Key: $KEY"
# → [{"provider":"tencent-cos","bucket":"bkt-1250000000","region":"ap-guangzhou",
#     "key":"reports/2026/summary.pdf","presigned":true,...}]

# 账号级汇总（按 provider/bucket 过滤，跨所有已读邮件）
curl ":8000/v1/accounts/1/objects?provider=aliyun-oss" -H "X-API-Key: $KEY"

# 直接拉取公开/预签名对象（SSRF 防护：仅放行可识别的存储域名，≤100MB）
curl -X POST :8000/v1/objects/fetch -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"url": "https://...cos.ap-guangzhou.myqcloud.com/a.pdf?sign=..."}'
```

## 对象存储引用（OSS / OBS / COS）

邮件正文里的云存储链接会在**首次读信时自动提取入库**（`object_refs` 表），解析出
`provider / bucket / key / region / presigned` 五元组，支持：阿里云 OSS（含 internal endpoint）、
腾讯云 COS、华为云 OBS（virtual-host + path-style）、AWS S3（含 .cn）、GCS、Azure Blob、
`s3://` scheme 与 MinIO 预签名 URL。

### 凭据化访问：Apache OpenDAL

提取只解决"找到"；要**读取私有 bucket**，用统一访问层 [Apache OpenDAL](https://opendal.apache.org/) ——
唯一把 `oss` / `obs` / `cos` 作为一等公民服务的开源方案（Rust 内核，sync+async 双 API，50+ 后端）：

```bash
pdm install --extra opendal   # 或 pip install opendal
```

```python
import opendal  # 提取结果里的 region/bucket/key 直接映射
op = opendal.Operator("oss", root="/", bucket="bkt-1250000000",
                      endpoint="https://oss-cn-guangzhou.aliyuncs.com",
                      access_key_id="...", secret_access_key="...")
data = op.read("reports/2026/summary.pdf")
# 同一套代码换 "cos"/"obs"/"s3" 服务名即可，无需改业务逻辑
```

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `YUNZHUN_API_KEY` | `dev-key-change-me` | 网关 API Key |
| `YUNZHUN_DB_URL` | SQLite | 生产可换 `postgresql://...` |
| `YUNZHUN_SYNC_ENABLED` | `true` | 后台定时同步开关 |
| `YUNZHUN_SYNC_INTERVAL_SECONDS` | `120` | 同步周期 |
| `YUNZHUN_ENCRYPTION_KEY` | 自动生成 `.fernet.key` | Fernet 密钥 |

### 语义判断层：Jev（可选，Vercel 通道免费）

### 语义判断层：Jev（可选，Vercel 通道免费）

**找地址的三级流水线**（intent routing：Jev 先判 → 正则再提 → LLM 兑底）：

```
① Jev 判断（单次批量调用，毫秒级）：是否交付邮件 / 交付概率 / 紧迫度
② 正则提取（确定性）：oss:// obs:// https:// pan.* 等全部形态，零误报验证
③ LLM 兑底（仅当 ①说有交付 且 ②一无所获）：读全文，把散文形态
   （"bucket: x，前缀： y"）归一化成标准 URI；LLM 结果仍须通过 classify() 验证入库
```

LLM 兑底默认关闭，设 `YUNZHUN_LLM_MODEL=openai/gpt-4.1-mini`（走同一个 AI Gateway key）开启；
每条入库地址带 `source` 字段（regex | llm）可追溯。

正则提取负责"地址在哪、是什么"（确定性、零成本）；语义判断交给 [Jev](https://docs.typesafe.ai)
（TypeSafe System One 决策模型，毫秒级、无幻觉、结构化概率输出）。支持两个 provider（协议一致，仅换
base_url/key/model）：

| provider | 端点 | 模型 | 费用 |
|---|---|---|---|
| `vercel`（默认） | `ai-gateway.vercel.sh/typesafe/v1/systemone` | `typesafe-ai/jev` | **免费**（AI Gateway free tier） |
| `typesafe` | `api.typesafe.ai/v1/systemone` | `jev-latest` | $0.04/M input tokens |

对任意已读邮件发一次批量判断（单次调用并行三问）：

```bash
curl -X POST :8000/v1/messages/42/judge -H "X-API-Key: $KEY"
# → {"category": "delivery", "category_confidence": 0.92,
#    "storage_delivery": 0.97, "action_required": 1, "model": "typesafe-ai/jev"}

# 按语义查询：找出所有数据交付邮件
curl ":8000/v1/accounts/1/judgments?category=delivery&min_storage_delivery=0.5" -H "X-API-Key: $KEY"
```

启用：设置 `YUNZHUN_VERCEL_GATEWAY_KEY`（即 Vercel AI Gateway 的 `AI_GATEWAY_API_KEY`），
未配置时接口返回 503。分类维度：`delivery/billing/security/notification/personal/other`
+ 交付概率（Noul）+ 行动紧迫度（Score 0-2）。

### 自动化下载（凭据注册 + 置信门禁 + OpenDAL）

```bash
pdm install --extra opendal   # Apache OpenDAL（Rust 内核，oss/obs/cos/s3 一等支持）

# 1. 注册 bucket 凭据（Fernet 加密落库，永不回显）
curl -X POST :8000/v1/stores -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{
  "name": "novo-oss", "provider": "aliyun-oss", "bucket": "novo-china-region",
  "region": "cn-hangzhou", "access_key_id": "...", "secret_access_key": "..."}'

# 2. 拉取某封交付邮件的全部存储地址（递归目录、上限保护）
curl -X POST :8000/v1/messages/17/pull -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"recursive": true, "max_files": 200}'
# → {"downloaded": ["./downloads/msg-17/..."], "skipped": [...], "gate": {"passed": true}}
```

**置信门禁**：`category=delivery` 或 `storage_delivery ≥ 0.5` 才允许自动拉取；
未判定/低置信邮件需要 `force=true`（或先人工看一眼）——这就是“高置信自动、低置信人工”的路由模式。
目录落到 `YUNZHUN_DOWNLOAD_DIR`（默认 `./downloads/msg-{id}/`）。

**进度与可靠性**：
- `GET /v1/messages/{id}/pulls` 查每个文件的下载记录（remote_key、大小、状态、时间）
- 原子写入：每个文件先写 `*.part` 再 rename，半截文件永远不会被记为完成
- DB 级去重：重跑自动跳过已下载的 key；文件被删后自愈重下
- 单文件失败不中断整批，失败原因落库（`status=failed` + `error`）
- `YUNZHUN_WEBHOOK_URL`：每次自动拉取成功后 POST 通知（best-effort）

### 实时推送（IMAP IDLE，默认开启）

每个账号一条 IDLE 守护线程盯 INBOX：服务器一推送 `EXISTS`/`RECENT`/`FETCH`/`EXPUNGE`
就立即触发增量同步 + pipeline 一轮——交付邮件**秒级落盘**，不用等轮询。
服务器不支持 IDLE 时自动回退到 `YUNZHUN_SYNC_INTERVAL_SECONDS` 轮询；
掉线指数退避重连（5s→300s），25 分钟心跳重发 IDLE（RFC 2177 <29min 限制）。
开关：`YUNZHUN_IDLE_ENABLED=true`、`YUNZHUN_IDLE_FOLDER=INBOX`。

### 入站 Webhook（免 IMAP 收信）

任何能 POST 的来源都可以把原始邮件直接推进流水线——存库、提地址、后台立即判定+下载：

```bash
# 裸 MIME
curl -X POST :8000/v1/inbound -H "X-API-Key: $KEY" \
  -H "Content-Type: message/rfc822" --data-binary @mail.eml
# 或 JSON
curl -X POST :8000/v1/inbound -H "X-API-Key: $KEY" \
  -d '{"raw_base64": "..."}'
```

配 Cloudflare Email Routing 就是真正的实时 webhook：你的域名收信 → Worker POST 到这里，
IMAP 都不用连。Worker 示例：

```js
export default {
  async email(message, env) {
    const raw = await new Response(message.raw).arrayBuffer();
    await fetch(env.GATEWAY + "/v1/inbound", {
      method: "POST",
      headers: { "X-API-Key": env.KEY, "Content-Type": "message/rfc822" },
      body: raw,
    });
    await message.forward(env.FALLBACK);  // 可选：同时转发到原邮箱
  },
};
```

入站邮件落在合成账号 `inbound@webhook.local` 下（provider=webhook，不参与 IMAP 同步/IDLE）。

### 全自动流水线（事件驱动 + 定时兑底）

**事件驱动**：任何入口发现新邮件都立即激活下载链——IDLE 推送、入站 webhook、
手动 `/sync`、轮询同步检出 `new_messages>0` 时都会当场 kick 一轮 pipeline。
后台循环（每 `YUNZHUN_PIPELINE_INTERVAL_SECONDS`，默认 60s）只是兑底清扫。

```
新邮件事件 → 拉正文 → Jev 判定 → [delivery 且 ≥0.5] → OpenDAL 自动下载
                                       ↘ 未注册凭据的 bucket → skipped 带原因
```

开关与参数：`YUNZHUN_PIPELINE_ENABLED=true`、`YUNZHUN_PIPELINE_INTERVAL_SECONDS=60`、
`YUNZHUN_PIPELINE_BATCH_LIMIT=20`（每轮最多处理条数，防免费层限流）。手动触发单轮：

```bash
curl -X POST ":8000/v1/pipeline/run" -H "X-API-Key: $KEY"
```

## 设计边界（v1）

- POP3 仅作备用读取通道；同步、标记、删除等主链路基于 IMAP（UID 语义远强于 POP3 UIDL）
- 正文/附件缓存随用随取，`raw` BLOB 存 SQLite；大体量场景需要加留存上限与压缩
- IMAP IDLE 实时推送已上线（每账号守护线程，不支持时回退轮询）；轮询仍作兑底
- API 鉴权为单把静态 Key；多租户时升级为 per-client key + 账号级授权
- 对象引用提取基于 URL 模式识别（正文 + HTML href）；私有 bucket 下载需配 OpenDAL 凭据层

## Roadmap

- [ ] 大文件断点续传（OpenDAL range read）
- [ ] cloud-drive（pan.*）/ gcs 的自动拉取（非标准对象存储）
- [ ] 附件磁盘存储 + CDN 直链，替代 BLOB
- [ ] 全文检索（SQLite FTS5 → Meilisearch）
- [ ] 多提供商预设（QQ 企业邮、Outlook、自建）
- [ ] 发件箱队列与重试（出站可靠性）
