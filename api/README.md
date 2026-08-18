# Video Task API

`apps/api/` 是本项目的 **后端主服务**。它负责服务端账号池、独立邮箱验证码池、图像/视频任务接口、任务调度、上游提交、状态同步和 MySQL 持久化。

React 控制台属于独立主服务 [`../web/`](../web/README.md)，桌面软件属于独立主服务 [`../desktop/`](../desktop/README.md)。后端内部的 Worker、Syncer 和数据库迁移仍由本目录统一维护。

## 后端组件

| 组件 | 入口/命令 | 职责 |
| --- | --- | --- |
| API | `video_task_service.api.main:app` | 健康检查、模型目录、账号、空间、统计和任务接口 |
| Worker | `python -m video_task_service.worker` | 从数据库领取任务、选择账号并提交上游 |
| Syncer | `python -m video_task_service.syncer` | 同步上游状态、结果和积分，校验账号 Token、Cookie 导入及注册流水 |
| Mailbox Validator | `python -m video_task_service.mailbox_validator` | 领取邮箱校验租约，验证 Microsoft OAuth 与 Graph 读取能力 |
| Migrate | `alembic upgrade head` | 启动前更新 MySQL 表结构 |

这些组件共同构成 Video Task API，不视为四个独立项目服务。

管理端创建账号接口支持可选的 `label` 字段，当前仅接受 `mmoshenqi` 或 `macbook`；
标签保存在 `accounts.label`，并随账号查询响应返回。历史账号的标签保持为空。

## 代码结构

```text
apps/api/
├── src/video_task_service/
│   ├── api/           # FastAPI 路由
│   ├── config.py      # VIDEO_SERVICE_* 配置
│   ├── db.py          # 数据库会话
│   ├── gemini_omni_flash.py # Gemini Omni Flash 尺寸与上游请求组装
│   ├── seed_audio.py  # Seed Audio 1.0 文字转语音请求组装
│   ├── gpt_image_2.py # GPT Image 2 尺寸与上游请求组装
│   ├── login_dispatch.py # 登录作业额度、续约窗口和退避规则
│   ├── models.py      # SQLAlchemy 模型
│   ├── schemas.py     # 请求/响应模型
│   ├── upstream.py    # mock/leonardo 上游适配器
│   ├── worker.py      # 提交进程
│   └── syncer.py      # 状态同步进程
├── migrations/       # Alembic 迁移
├── scripts/           # 联调与验收脚本
└── tests/             # 单元与组件测试
```

## 本地启动

推荐从仓库根目录启动完整编排：

```bash
docker compose up -d --build mysql migrate api worker syncer
docker compose ps
```

默认入口：

- API：`http://127.0.0.1:18080`
- OpenAPI：`http://127.0.0.1:18080/docs`
- 健康检查：`http://127.0.0.1:18080/health/ready`
- MySQL：`127.0.0.1:13306`

```bash
curl -fsS http://127.0.0.1:18080/health/live
curl -fsS http://127.0.0.1:18080/health/ready
```

## 上游模式

本地 Compose 默认配置：

```text
VIDEO_SERVICE_UPSTREAM_MODE=mock
```

`mock` 会在短时间内返回确定结构的结果，用于测试任务队列闭环。设置为 `leonardo` 后才会使用真实 GraphQL 上游；真实可用性还依赖有效账号、Token、余额、媒体 URL 和网络环境。

环境变量示例见 [`.env.example`](.env.example)。所有后端变量使用 `VIDEO_SERVICE_` 前缀。
Leonardo 私有 GraphQL 请求默认跟随当前网页使用的 Schema `latest`；可通过
`VIDEO_SERVICE_LEONARDO_SCHEMA_VERSION` 显式升级，升级时需同步运行 Seedance
请求构造回归测试。

邮箱校验器按批领取租约后，以 `VIDEO_SERVICE_MAILBOX_VALIDATION_CONCURRENCY=10` 为上限并发
执行 OAuth 刷新和 Graph 读取，并将整批状态一次提交；单条内部异常只回退该邮箱，不会中断其余
邮箱。`VIDEO_SERVICE_MAILBOX_VALIDATION_BATCH_SIZE=10` 控制单次领取量，限流与临时故障仍按
60/300/900 秒退避。批量与并发高于 10 时，应同步评估提供方限流及 60 秒校验租约。

Worker 默认使用 `VIDEO_SERVICE_WORKER_CONCURRENCY=12` 路任务提交并发。单任务的参考素材以
`VIDEO_SERVICE_MEDIA_RESOLUTION_CONCURRENCY=3` 为上限并行下载、探测和上传，同时保持原始素材
顺序。图片下载独立使用 5 秒连接超时和 30 秒读取超时，音视频仍保留 10/120 秒窗口。
`VIDEO_SERVICE_MEDIA_CIRCUIT_BREAKER_HOSTS` 默认包含 `cdn.quantv.com`：60 秒窗口内连续 3 次
网络失败后熔断 60 秒，熔断错误携带重试等待时间，任务回到 `RETRY_WAIT` 而不是持续占用 Worker。
阈值、窗口、开启时长及图片超时均可通过 `.env.example` 中对应变量调整。

Syncer 遇到账号 Token 过期或新 Token 尚在校验时，会保留任务当前状态并暂停该账号的
上游轮询；Token 更新并通过校验后，任务自动恢复同步。`RUNNING` 状态默认最多保留
`7200` 秒，超过 `VIDEO_SERVICE_TASK_RUNNING_TIMEOUT_SECONDS` 后任务标记为 `FAILED`，
释放账号和空间并发位，并记录 `TASK_RUNNING_TIMEOUT`。

## 鉴权与接口

本地 Compose 的示例鉴权头：

- 业务接口：`X-API-Key: local-api-key`
- 管理接口：`X-Admin-Key: local-admin-key`
- 登录作业机器：`X-Login-Worker-Key: local-login-worker-key`

这些值只用于本机开发，共享环境应通过 Secret/KMS 注入不同的凭据。

核心接口：

| 方法与路径 | 鉴权 | 用途 |
| --- | --- | --- |
| `GET /health/live` | 无 | 进程存活 |
| `GET /health/ready` | 无 | 数据库与服务就绪 |
| `GET /admin/stats/dashboard` | Admin | 总览统计；`period=total\|today\|hour` 切换总数、当天、最近 1 小时，`timezone_offset_minutes` 指定客户端时区偏移 |
| `GET /admin/stats/protocol-renewals` | Admin | 协议续签健康、严格成功率、队列、覆盖率、趋势与失败分布；支持 `hour\|six_hours\|day\|week` |
| `GET /admin/protocol-renewals/accounts` | Admin | 按续签状态、错误码、会话覆盖和到期窗口查询账号续签快照 |
| `GET /admin/protocol-renewals/accounts/{account_uuid}/events` | Admin | 查询单账号最近的非敏感续签终态事件 |
| `GET/POST /admin/spaces` | Admin | 空间管理 |
| `GET/POST /admin/accounts` | Admin | 账号池管理；`login_name` 与兼容字段 `login_name_masked` 均返回完整登录邮箱 |
| `POST /admin/accounts/blocked-check` | Admin | 只读按邮箱比对线上账号池状态，并统计每个账号的图片任务成功/失败记录；不连接浏览器、不提交任务 |
| `POST /admin/account-cookie-imports` | Admin + `Idempotency-Key` | 上传 Cookie ZIP，解析后加密暂存有效会话并返回 `202` 批次 |
| `GET /admin/account-cookie-imports` | Admin | 分页读取导入批次及账号、积分、作业观察汇总 |
| `GET /admin/account-cookie-imports/{batch_uuid}` | Admin | 读取批次五阶段进度和逐文件结果；响应不含 Cookie/Token 值 |
| `GET /admin/mailboxes` | Admin | 独立邮箱池分页、状态及邮箱搜索；可用 `import_period=today|yesterday|recent_7d|older` 与 `timezone_offset_minutes` 按导入时间分类 |
| `GET /admin/mailboxes/stats` | Admin | 邮箱池状态统计 |
| `POST /admin/mailboxes/import` | Admin | 导入 `邮箱----密码----client_id----refresh_token` 文本，凭据加密落库 |
| `POST /admin/mailboxes/{mailbox_uuid}/revalidate` | Admin | 将邮箱重新放入后台校验队列 |
| `PATCH/DELETE /admin/mailboxes/{mailbox_uuid}` | Admin | 停用、恢复或删除邮箱 |
| `POST /v1/mailboxes/claim` | API + `Idempotency-Key` | 按规范化项目名领取该项目从未领取过的一个 ACTIVE 邮箱 |
| `POST /v1/mailbox-codes/query` | API | 按邮箱轮询最近十分钟最新邮件并自动提取验证码；必须携带业务 API Key |
| `POST /v1/atomicmail-codes/query` | Public | 输入单行 `邮箱|密码`，同步登录 Atomic Mail 并在最长 60 秒内返回最近验证码；无需 API Key |
| `POST /v1/mailbox-codes/query-for-registration` | API | 按注册任务 UUID、客户端 ID 和报告令牌解析已绑定邮箱并轮询验证码；不接受客户端自带邮箱 |
| `GET /admin/parent-accounts` | Admin | 独立母号池按邮箱搜索、分页，并返回管理端展示所需的邮箱、解密密码、邀请链接和计数 |
| `GET /admin/parent-accounts/stats` | Admin | 聚合母号总数、邀请成功总次数和邀请失败总次数 |
| `POST /admin/parent-accounts/import` | Admin | 按 `邮箱 密码 邀请链接` 批量导入；密码使用 AES-GCM 加密落库，计数初始化为 0 |
| `DELETE /admin/parent-accounts/{parent_account_uuid}` | Admin | 删除指定母号 |
| `POST /admin/parent-accounts/{parent_account_uuid}/invitation-result` | Admin | 依据 `success` 布尔值在数据库中原子累加成功或失败次数 |
| `POST /admin/accounts/sync` | Admin | 桌面端批量同步账号 |
| `GET /v1/registration-jobs/preflight` | API | 校验邀请注册业务 API Key 与接口版本，不消耗母号或邮箱 |
| `POST /v1/registration-jobs/claim` | API + `Idempotency-Key` | 按 `project_name`（桌面固定 `Canvas`）并发领取最早 ACTIVE 母号和永久唯一子邮箱 |
| `POST /v1/registration-jobs/{uuid}/heartbeat` | API | 使用客户端 ID 与报告令牌延长注册租约 |
| `POST /v1/registration-jobs/{uuid}/result` | API + `Idempotency-Key` | 上报失败或注册邮箱及 CDP Cookie；请求模型没有积分字段 |
| `POST /v1/registration-jobs/{uuid}/status` | API | 使用客户端 ID 与报告令牌读取后端校验状态与服务端积分 |
| `POST /v1/registration-cookies/export` | Public | 传 `email` 下载单个 `{邮箱}.json`，或传最多 500 个 `emails` 下载逐账号 JSON ZIP；整批成功后把对应记录标记为 `is_used=true` |
| `POST /v1/mailbox-codes/query` | API | 查询指定 ACTIVE 邮箱最近验证码；受业务 API Key 保护 |
| `GET /admin/registration-records` | Admin | 独立分页读取 `SUCCEEDED` 注册账号，返回账号、积分、注册成功时间、归属母号、`is_used` 及全局 `unused_8500_count`；支持账号/母号、使用状态与 `credits` 积分筛选，`limit` 允许 1–500 |
| `GET /admin/registration-clients` | Admin | 按默认 10 分钟或显式 `from/to` 聚合独立注册客户端、健康状态、成功率、耗时和最近错误 |
| `GET /admin/registration-clients/{client_id}` | Admin | 返回单客户端窗口摘要和自适应时间桶领取/成功/失败趋势 |
| `GET /admin/registration-clients/{client_id}/registrations` | Admin | 分页读取单客户端所选时段的注册任务时间线、租约、耗时和非敏感错误字段 |
| `GET /admin/parent-accounts/{uuid}/registrations` | Admin | 读取母号注册抽屉元数据，不返回 Cookie/Token |
| `POST /admin/registration-records/{uuid}/revalidate` | Admin | 重新进入后端 Cookie 校验队列 |
| `POST /admin/registration-records/{uuid}/promote` | Admin | 使用固定 ACTIVE 空间手动加入账号池 |
| `GET/PATCH /admin/registration-settings` | Admin | 读取或按版本更新固定空间与默认账号并发 |
| `POST /admin/accounts/ledger-import` | Admin | 按邮箱幂等导入完整子账号账本档案；新账号创建、已有账号更新 |
| `GET /admin/accounts/{account_uuid}/ledger` | Admin | 读取非敏感账本扩展字段、敏感字段存在标记及原始记录哈希 |
| `POST /admin/accounts/export` | Admin | 按 UUID 导出无表头 `邮箱|密码|token` UTF-8 文本，并返回 10 分钟有效的选择集签名回执 |
| `POST /admin/accounts/bulk-delete/preview` | Admin | 预检所选账号的可删除、受保护与已不存在数量 |
| `POST /admin/accounts/bulk-delete` | Admin + Export Receipt | 校验导出回执后，重新加锁检查并删除仍满足条件的账号 |
| `POST /internal/v1/account-login-jobs/claim` | Login Worker | ACTIVE 总积分低于水位时保留一个新账号激活通道，其余容量继续处理 Token 续约 |
| `GET /internal/v1/account-login-jobs/worker-status` | Login Worker | 只读验证 Worker Key 和登录调度配置，不创建租约 |
| `POST /internal/v1/account-login-jobs/{job_uuid}/heartbeat` | Login Worker + Lease | 延长登录租约 |
| `POST /internal/v1/account-login-jobs/{job_uuid}/token` | Login Worker + Lease | 异步上报新 Token，返回 `202 VALIDATING` |
| `POST /internal/v1/account-login-jobs/{job_uuid}/fail` | Login Worker + Lease | 上报登录失败并进入退避 |
| `GET /internal/v1/account-login-jobs/{job_uuid}` | Login Worker | 查询异步校验结果 |
| `GET /v1/models` | API | 读取上游首页模型卡片目录 |
| `GET/POST /v1/tasks` | API | 查询和提交任务；列表查询支持 `status` 与精确 `model` 筛选，并返回可选模型列表 |
| `GET /v1/tasks/{task_uuid}` | API | 查询单个任务 |

总览接口默认使用 `period=total&timezone_offset_minutes=0`，保持原有累计口径。任务状态、
成功率、平均耗时、积分消耗和模型分布都按任务 `created_at` 过滤；`today` 从客户端本地
零点开始，`hour` 是滚动 60 分钟。趋势粒度分别为最近 7 天按日、当天按小时、最近 1 小时
按 5 分钟。账号数量、余额和并发容量始终返回查询时的实时快照，不受任务时间维度影响。
`accounts.max_concurrency` 保留所有账号槽位的理论总和；调度容量应读取
`effective_max_concurrency` 和 `effective_available_concurrency`，它们只统计 ACTIVE、Token
通过保护窗口、仍有可用积分的账号，并按 Worker 实际执行的 Space 上限收敛。
| `POST /v1/tasks/{task_uuid}/cancel` | API | 取消任务 |

完整请求模型以 `/docs` 和 `/openapi.json` 为准。

<!-- BEGIN EMAIL AUDIT API -->
### 按邮箱读取线上账号池与图片任务

`POST /admin/accounts/blocked-check` 直接读取 API 使用的线上 MySQL 数据库，适合把一批
Cookie 导出的邮箱与账号池做一次只读比对。接口按 `login_name` 精确匹配，返回账号状态、
积分、累计任务字段，以及 `task_type=IMAGE_GENERATION` 的图片任务计数和模型列表。响应
不会返回 Cookie、密码或 Token，并设置 `Cache-Control: no-store`。

数据库只持久化账号池状态，没有独立的 Leonardo `users.blocked` 列：
`MANUAL_DISABLED` 且 `disabled_reason=manual` 时，响应将 `blocked=true`、
`blocked_source=DB_MANUAL_STATUS`；其余账号的 `blocked` 为 `null`，并保留原始
`account_status`/`disabled_reason` 供进一步判断。

```bash
API_BASE='https://api-leo.clawsea.ai'
ADMIN_KEY='ADMIN_KEY'

curl -fsS -X POST "$API_BASE/admin/accounts/blocked-check" \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"emails":["ACCOUNT_1@example.com","ACCOUNT_2@example.com"]}'
```

响应中的 `image_task_success` 是已完成图片任务数量；`image_task_failed` 汇总
`FAILED` 与 `SUBMIT_UNKNOWN`。该接口只做 SELECT，不改变账号、任务或积分数据。
<!-- END EMAIL AUDIT API -->

<!-- BEGIN STANDALONE UNUSED 8500 AUDIT -->
### 独立核查未使用 8,500 积分注册记录

`scripts/audit_unused_8500_registrations.py` 是独立的只读审计脚本。它只筛选
`registration_records` 中 `SUCCEEDED`、`is_used=0`、`awarded_points=8500` 的记录，
不会创建、更新或删除 `accounts`，也不会提交图片任务。`live` 模式直接使用该注册记录
已加密保存的会话访问 Leonardo 的 `get-session` 和余额 GraphQL；不经过浏览器、不加入账号池。

脚本默认以 20 个并发 worker 检查账号，每个 worker 的检查结果立即追加到权限为 `0600` 的
TXT（不会等整批完成才落盘），完成后再物化为 `BLOCKED`、`NORMAL`、`INDETERMINATE`
三个分组。`--concurrency` 可调整并发数，`--interval-seconds` 是每个 worker 的间隔。
`live` 模式会先校验会话和邮箱身份，再按 JWT `sub` 查询 GraphQL `users.blocked`；只有
`users.blocked=false` 且身份匹配时标记为 `NORMAL`，`users.blocked=true` 或明确的会话/认证
拒绝标记为 `BLOCKED`。网络、限流、身份不一致、字段缺失或响应含糊的结果归入
`INDETERMINATE`。数据库源记录按 `created_at ASC, id ASC` 从最早到最新选择。

需要隔离并发出口时显式传 `--proxy-mode auto`。脚本先使用只读的 Cliproxy API Key 文件为
每个 worker 分配独立线路；API 线路不可用时可从 `--proxy-env-file` 读取
`LEONARDO_PROXY_*` 动态会话配置。每条线路会先通过 IP 探针校验，20 个 worker 必须得到
20 个不同的出口哈希后才开始账号复核。私有 proxy manifest 只写 worker、来源、地区及出口
IP 哈希，不保存 API Key、代理用户名、密码或原始出口 IP。默认 `--proxy-mode direct` 保留原行为。

`--retry-report` 可读取上一轮最终分组报告或尚在写入的 `[RESULTS]` 报告，只提取其中
`INDETERMINATE` 的 registration id，并在当前只读数据库筛选条件下按原时间顺序重新检查。

```bash
set -a; . /opt/frame-ops/shared/config/api.env; set +a
PYTHONPATH=/opt/frame-ops/current/apps/api/src \
  python3 /opt/frame-ops/current/apps/api/scripts/audit_unused_8500_registrations.py \
  --mode live --output /secure/audits/unused-8500-status.txt
```

```bash
PYTHONPATH=/opt/frame-ops/current/apps/api/src \
  python3 /opt/frame-ops/current/apps/api/scripts/audit_unused_8500_registrations.py \
  --mode live --concurrency 20 --interval-seconds 0 \
  --retry-report /secure/audits/unused-8500-pass1.txt \
  --proxy-mode auto \
  --cliproxy-api-key-file /secure/runtime/cliproxy.key \
  --proxy-env-file /secure/runtime/proxy.env \
  --proxy-country RANDOM \
  --proxy-manifest /secure/audits/unused-8500-retry.proxies.json \
  --output /secure/audits/unused-8500-retry.txt
```

使用 `--mode db` 可只生成数据库预览；`--limit 10` 可先做小批检查。脚本全程不执行
`INSERT`、`UPDATE`、`DELETE` 或账号池 promote 操作；数据库连接在查询前显式设置
`SET SESSION TRANSACTION READ ONLY`。
<!-- END STANDALONE UNUSED 8500 AUDIT -->

### Atomic Mail 同步验证码接口

`POST /v1/atomicmail-codes/query` 接受单行 `邮箱|密码` 凭据并保持 HTTP 请求，直到找到最近十分钟
内的验证码或达到截止时间。`timeout_seconds` 默认为 60，可设置为 1–60；60 秒覆盖登录、邮箱
发现、邮件列表轮询和正文读取的总时长。接口是公开路由，不读取 `X-API-Key`；响应固定使用
`Cache-Control: no-store`，服务不会将请求中的密码或 Atomic Mail Access Token 写入数据库。

```bash
API_BASE='http://127.0.0.1:18080'

curl -sS -X POST "$API_BASE/v1/atomicmail-codes/query" \
  -H 'Content-Type: application/json' \
  --data '{"credential":"ACCOUNT@atomicmail.io|PASSWORD","timeout_seconds":60}'
```

成功响应示例：

```json
{
  "email": "ACCOUNT@atomicmail.io",
  "code": "459866",
  "received_at": "2026-08-15T07:58:00Z",
  "subject": "你的登录码是459866",
  "sender": "no-reply@account.canva.com",
  "message_id": "57",
  "matched_by": "KEYWORD_NEARBY"
}
```

每次请求只建立一次登录会话，然后轮询 Inbox。扫描会跳过登录产生的 Atomic Mail 新设备提醒，
继续检查同一页中较早的验证码邮件。稳定错误为：`ATOMICMAIL_CREDENTIALS_INVALID`（409）、
`ATOMICMAIL_CODE_TIMEOUT`（408）、`ATOMICMAIL_PROVIDER_RATE_LIMITED`（503）和
`ATOMICMAIL_PROVIDER_UNAVAILABLE`（502）。请求格式错误由 FastAPI 返回 422。

### 按项目领取邮箱

调用方使用项目名和 8–128 字符的 `Idempotency-Key` 领取一个邮箱：

```bash
API_BASE='http://127.0.0.1:18080'
API_KEY='local-api-key'
PROJECT_NAME='project-a'
IDEMPOTENCY_KEY="$(python3 -c 'import uuid; print(uuid.uuid4())')"

curl -i -sS -X POST "$API_BASE/v1/mailboxes/claim" \
  -H "X-API-Key: $API_KEY" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -H 'Content-Type: application/json' \
  --data "{\"project_name\":\"$PROJECT_NAME\"}"
```

首次领取返回 HTTP `201`。同一项目使用相同幂等键重试时返回 HTTP `200`，并将
`replayed` 置为 `true`；`claim_uuid`、邮箱和首次领取时间保持不变。项目名执行 NFKC、空白
合并和 Unicode 大小写折叠，因此 ` Project   A ` 与 `project a` 属于同一项目。一个项目每次
使用新幂等键可继续领取不同邮箱，不同项目允许领取同一个邮箱。

服务只选择调用时为 `ACTIVE` 的邮箱。领取流水用 `(project_id, email_snapshot)` 永久去重；
删除邮箱后重新导入同一地址，也不会再次发给已经领取过它的项目。项目已领取所有当前可用
邮箱时返回 HTTP `409 PROJECT_MAILBOX_POOL_EXHAUSTED`。响应只包含领取 UUID、项目展示名、
邮箱 UUID、邮箱地址、首次领取时间和重放标记，并使用 `Cache-Control: no-store`。

### Cookie ZIP 账号池导入

上传合同为 `multipart/form-data`，文件字段固定为 `archive`，目标空间字段为 `space_name`，
请求头必须携带 8–128 字符的 `Idempotency-Key`。浏览器和 API 都按 20 MiB 原始 ZIP 上限
预检；API 还限制 500 个条目、单条解压 1 MiB、总解压 50 MiB、压缩比 100，并拒绝目录穿越、
符号链接、加密条目和嵌套压缩包。浏览器导出中常见的根目录标记与非 JSON 清单文件会被过滤，
只把安全路径下的 JSON 文件作为 Cookie 条目处理（支持常见的单层文件夹包装）；如果过滤后
没有可用 JSON，则返回 `ARCHIVE_NO_JSON_ENTRIES`。

```bash
ADMIN_KEY='ADMIN_KEY'
API_BASE='http://127.0.0.1:18080'
IDEMPOTENCY_KEY="$(python3 -c 'import uuid; print(uuid.uuid4())')"

curl -fsS -X POST "$API_BASE/admin/account-cookie-imports" \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -F 'space_name=cookie-import-20260813-1104' \
  -F 'archive=@/absolute/path/cookies.zip;type=application/zip'

curl -fsS "$API_BASE/admin/account-cookie-imports?limit=20&offset=0" \
  -H "X-Admin-Key: $ADMIN_KEY"

curl -fsS "$API_BASE/admin/account-cookie-imports/BATCH_UUID" \
  -H "X-Admin-Key: $ADMIN_KEY"
```

创建接口返回 HTTP `202`。同一幂等键、相同 ZIP 哈希和相同空间会返回原批次；同一键绑定其他
文件或空间返回 `409 COOKIE_IMPORT_IDEMPOTENCY_CONFLICT`。批次状态为 `QUEUED -> RUNNING ->
COMPLETED|PARTIAL_FAILED|FAILED`；条目按 `RECEIVED -> SESSION_VALIDATION -> BALANCE_VALIDATION ->
ACCOUNT_ACTIVATION -> RENEWAL_READY` 记录阶段。示例响应：

```json
{
  "batch_uuid": "BATCH_UUID",
  "status": "QUEUED",
  "archive_filename": "cookies.zip",
  "archive_sha256": "SHA256",
  "space_name": "cookie-import-20260813-1104",
  "item_count": 10,
  "queued": 10,
  "running": 0,
  "created": 0,
  "updated": 0,
  "failed": 0,
  "total_balance_credits": 0,
  "tasks_after_import": 0,
  "completed_tasks_after_import": 0,
  "failed_tasks_after_import": 0,
  "consumed_credits_after_import": 0,
  "created_at": "2026-08-13T03:04:00Z",
  "started_at": null,
  "finished_at": null,
  "items": []
}
```

有效会话先使用账号凭据主密钥加密写入 `account_cookie_import_items.session_ciphertext`，Syncer
领取租约后才解密。协议会话身份、上游账号邮箱与文件名邮箱三者必须一致；余额和 Token 校验
通过后按邮箱幂等新增或更新账号，并立即清除暂存密文。网络、上游 5xx 和限流错误最多按
60/300/900 秒退避 3 次；身份、结构、撤销和错误账号属于终态。稳定错误码如下：

- 压缩包：`ARCHIVE_TOO_LARGE`、`ARCHIVE_TOO_MANY_ENTRIES`、`ARCHIVE_ENTRY_TOO_LARGE`、
  `ARCHIVE_EXPANDED_TOO_LARGE`、`ARCHIVE_COMPRESSION_RATIO`、`ARCHIVE_EXTENSION_INVALID`、
  `ARCHIVE_INVALID`、`ARCHIVE_EMPTY`、`ARCHIVE_NO_JSON_ENTRIES`、`ARCHIVE_UNSAFE_PATH`、
  `ARCHIVE_SYMLINK_ENTRY`、`ARCHIVE_ENCRYPTED_ENTRY`、`ARCHIVE_NESTED_ARCHIVE`、
  `ARCHIVE_READ_FAILED`、`ARCHIVE_CONTENT_TYPE_INVALID`。
- 条目结构：`ENTRY_JSON_INVALID`、`COOKIE_ENTRY_INVALID`、`ENTRY_COOKIES_INVALID`、
  `ENTRY_URL_INVALID`、`COOKIE_COUNT_EXCEEDED`、`COOKIE_DUPLICATE_KEY`、
  `COOKIE_SESSION_TOKEN_MISSING`、`COOKIE_SESSION_DATA_MISSING`、`COOKIE_IMPORT_SESSION_MISSING`。
- 激活链路：`COOKIE_IMPORT_SESSION_INVALID`、`COOKIE_IMPORT_IDENTITY_MISMATCH`、
  `PROTOCOL_IDENTITY_UNAVAILABLE`、`PROTOCOL_WRONG_ACCOUNT`、`PROTOCOL_SESSION_REVOKED`、
  `PROTOCOL_SESSION_ROTATION_INVALID`、`UPSTREAM_IDENTITY_OR_BALANCE_MISSING`、
  `UPSTREAM_ACCOUNT_VALIDATION_FAILED`、`PROTOCOL_NETWORK_ERROR`、`PROTOCOL_TIMEOUT`、
  `PROTOCOL_RATE_LIMITED`、`UPSTREAM_NETWORK_ERROR`、`UPSTREAM_RATE_LIMITED`、
  `UPSTREAM_SERVER_ERROR`、`COOKIE_IMPORT_INTERNAL_ERROR`、`COOKIE_IMPORT_BATCH_NOT_FOUND`。

`accounts.credential_source` 区分 `PASSWORD` 与 `COOKIE_SESSION`。密码创建、桌面同步或管理端写入
密码时使用 `PASSWORD`；仅凭 ZIP 会话激活的账号使用 `COOKIE_SESSION`，不会被浏览器密码登录兜底
领取，凭据导出时密码列为空。两类账号的 Token、余额、调度、协议续签和积分结算规则一致。

批次完成表示账号已经通过身份/余额校验并具备进入调度的资格，不会自动提交收费图片或视频
任务。`tasks_after_import` 与 `consumed_credits_after_import` 只观察激活时间之后由正常任务入口创建、
执行和结算的作业。

母号密码只以密文保存在 `parent_accounts.password_encrypted`。经过 Admin Key 鉴权的母号列表会解密并返回 `password`；导入结果、错误详情和服务日志不回显密码。旧 `invitation-result` 布尔计数入口已返回迁移冲突，所有新成功/失败计数必须来自可追溯注册流水。

### 母号注册流水

领取事务锁定最早 `ACTIVE` 母号与规范化 `mailbox_projects` 项目行，再锁定一个 `ACTIVE`、从未出现在 `registration_records.email_snapshot` 且未被该项目领取的邮箱。事务同时创建 `project_mailbox_claims` 永久占用墓碑并由 `registration_records.project_mailbox_claim_id` 关联，避免客户端先领注册任务、再领项目邮箱时产生双邮箱。邮箱在领取时即永久占用，客户端失败或超时也不会回池。多个客户端可以共享同一母号，并通过各自租约、报告令牌和两组幂等键隔离。

客户端注册成功只推进到 `COOKIE_REPORTED`。Syncer 注册校验 coroutine 解密 CDP Cookie，会话续签后再次调用账号验证接口，以后端返回身份和 `balance_credits` 为准。网络、429、5xx 或暂缺身份/积分会有界重试；Cookie 撤销和邮箱不一致直接终止且不参与连续 150。成功结算与母号计数在同一事务完成，连续三次 150 把母号置为 `EXHAUSTED`；其在途成功仍有递增结算序号，但不改变耗尽状态。

管理端成功账号台账只读取服务端已结算的 `SUCCEEDED` 记录。列表以 `validation_finished_at` 作为注册成功时间，保留 `parent_email_snapshot` 作为归属母号，并直接返回持久化的 `is_used` 布尔标记；`credits=8500&is_used=false` 可组合筛选目标账号，响应中的 `unused_8500_count` 始终表示全局未使用 8500 积分账号数量。列表接口不返回保存的 Cookie、Token 或报告令牌。

### 客户端监控

客户端监控直接使用注册领取、心跳和结果上报中已有的 `client_id`，不增加 Desktop 协议字段。`GET /admin/registration-clients` 默认以服务端当前 UTC 时间向前滚动 10 分钟；显式窗口要求 `from < to` 且跨度不超过 31 天。窗口作业按 `started_at` 归属，最近活动综合开始、心跳、结果上报、校验完成和 `updated_at`。

服务端返回 `NORMAL`、`ATTENTION`、`ABNORMAL`、`NO_ACTIVITY` 及可解释原因。租约过期、最近三个终态连续失败或至少五个终态且失败率达到 30% 会标为异常；存在普通失败或校验重试等待标为需关注。“无作业”不表示机器离线。三个接口只投影任务元数据和错误码，不读取或返回 Cookie、报告令牌、会话密文与视频 Token 密文。

迁移 `0019_registration_client_idx` 为客户端时间窗口、最近更新和完成时间增加复合索引；普通代码回滚可以保留这些索引，完全回退时再降级到 `0018_registration_used_flag`。

公开 Cookie 导出接口固定使用 `POST /v1/registration-cookies/export`，无需 API Key 或 Admin Key。兼容单个请求 `{"email":"账号邮箱"}` 与批量请求 `{"emails":["账号1邮箱","账号2邮箱"]}`；单个请求返回 `{邮箱}.json`，批量请求最多接收 500 个邮箱并返回 `leonardo-{积分}-cookies-{数量}-unused-{时间}.zip`。批量 ZIP 内每个邮箱对应一个 `{邮箱}.json`，并额外包含 UTF-8、LF 换行的 `emails.txt` 与 `leodev_links.txt`：前者按请求顺序每行记录一个规范化邮箱，后者按相同顺序每行记录一个 `https://leodev.app/?email={URL编码邮箱}` 链接，两个文件末尾均保留换行。邮箱会规范化为小写并按首次出现顺序去重；批量中任一邮箱不存在时整批返回 404 和缺失邮箱列表。接口只匹配 `SUCCEEDED` 注册记录，JSON 顶层固定为 `url` 与 `cookies`，Cookie 字段与基准压缩包条目一致，ZIP 条目时间固定为 1980-01-01。服务端会先完成整批查询、解密和全部 ZIP 内容构造，再在同一事务把对应记录的 `is_used` 置为 `true`；已标记记录仍可重复导出。响应使用 `no-store`，且不返回视频 Token 或报告令牌。

配置项使用 `VIDEO_SERVICE_REGISTRATION_*` 前缀，默认任务租约 300 秒、超时 900 秒、校验批量 2、校验租约 120 秒、最多 6 次、退避上限 1800 秒。

批量凭据导出一次最多接收 1000 个账号 UUID，按请求顺序输出，每个账号占一行；尚未配置
Token 的账号输出空第三列。密码或 Token 解密失败时整批导出返回错误，不生成残缺文件。
批量删除必须携带同一 UUID 集合最近一次导出得到的签名回执；回执与选择集哈希、数量和
过期时间绑定。存在运行任务、预留积分、任务/媒体/积分流水或登录作业历史的账号会被保护。

子账号账本导入把 `email`、`password` 和总积分同步到账号主表，并在
`account_ledger_profiles` 中保存邀请、注册、父账号、积分组成、源文件元数据以及每个源字段。
`registrationPassword`、`groupToken`、`authorizationToken` 与完整原始记录统一使用凭据主密钥
加密；管理查询只返回这些敏感字段是否存在，不返回明文。重复执行同一文件按规范化邮箱更新，
不会重复创建账号；`creditsCheckedAt` 不早于当前余额快照时才更新已有账号余额。

按积分筛选文件并导入的脚本为 `scripts/import_account_ledger.py`。Admin Key 只从环境变量读取，
审计文件不包含密码或 Token：

```bash
export VIDEO_SERVICE_ADMIN_AUTH_KEY='ADMIN_KEY'
python apps/api/scripts/import_account_ledger.py \
  --file /absolute/path/account-ledger.json \
  --space-uuid SPACE_UUID \
  --credits-total 8500 \
  --api-base-url https://API_HOST \
  --audit-output /secure/path/account-ledger-import-audit.json \
  --dry-run

# 核对 selected_records 后移除 --dry-run 执行真实写入。
```

服务器通过 `VIDEO_SERVICE_ACCOUNT_MAX_CONCURRENCY` 固定所有账号的账号级并发，生产值为 `3`。新建、桌面同步和已有账号续期都会归一为该值；管理接口提交不同值时返回 `ACCOUNT_CONCURRENCY_FIXED`。数据库列默认值与 API 请求模型保持为 `3`。

Worker 同时执行账号级和 Space 级并发限制。任务首次找不到候选账号时会把真实原因记录为
`NO_ACTIVE_ACCOUNT`、`TOKEN_UNAVAILABLE`、`ACCOUNT_SATURATED`、
`INSUFFICIENT_CREDITS`、`SPACE_UNAVAILABLE`、`SPACE_SATURATED` 或
`ACCOUNT_LOCK_CONTENTION`。同一轮 `WAITING_ACCOUNT` 不重复写事件，并按 2、4、8、16 秒逐步
退避，最大间隔由 `VIDEO_SERVICE_ACCOUNT_UNAVAILABLE_RETRY_MAX_SECONDS` 控制，默认 30 秒。

登录作业接口以状态严格等于 `ACTIVE` 的账号 `balance_credits` 总和作为补水水位，默认目标由
`VIDEO_SERVICE_LOGIN_ACTIVE_CREDIT_TARGET=1000000` 指定。总和低于目标时最多保持
`VIDEO_SERVICE_LOGIN_ACTIVATION_MAX_IN_FLIGHT=3` 个 `ACTIVATE_NEW` 在途，并优先选择失败次数更少、
最久未尝试的账号。Desktop 即使每次只领取一个作业，也会在没有激活作业在途时优先补充一个
`ACTIVATE_NEW`；已有激活作业在途后，其余领取机会继续用于 Token 续约，避免两类作业互相饿死。
普通登录累计失败 5 次、`TIMEOUT/LOGIN_STALLED` 等卡滞累计 3 次后账号进入
`MANUAL_DISABLED` 隔离；`ENOSPC` 等执行器故障不累计账号失败，作业退避 300 秒。
`reserved_credits`、运行任务数和其他账号状态不参与该总和。Token 有效期进入
`VIDEO_SERVICE_LOGIN_RENEWAL_WINDOW_SECONDS=600` 秒窗口且带有浏览器续签会话的账号，先由
Syncer 调用 `GET /api/auth/get-session` 做云端协议续签；成功后直接更新 Token，失败或在
`VIDEO_SERVICE_PROTOCOL_RENEWAL_GRACE_SECONDS=420` 秒内未完成才下发 `RENEW_TOKEN`。
登录站点请求使用 `curl-cffi` 的 Chrome 136 TLS/HTTP 指纹，默认并发为 `3`、请求起始间隔
为 `2` 秒；HTTP 429 会遵守 `Retry-After`，缺失时按 300 秒冷却，并以进程级熔断阻止请求风暴。
Better Auth 分片 Cookie 发生轮换但首个响应没有 JWT 时，会携带新 Cookie 再请求一次。
`cross-origin-cookie` 仅作为可选的 Cookie 同步步骤，默认不影响已取得的新 Token。
Syncer 还会按 `VIDEO_SERVICE_PROTOCOL_RENEWAL_KEEPALIVE_INTERVAL_SECONDS=900` 秒提前调用
`get-session`，将轮换后的会话 Cookie 加密写回；保活事件使用 `SESSION_ALIVE` 单独记录，不进入
严格 Token 续签成功率的分母。连续响应 `200 + null` 且关键 Better Auth Cookie 被删除时标记为
`PROTOCOL_SESSION_REVOKED` 并直接进入登录兜底，避免同一失效会话反复重试。
服务端同时记录桌面端真实上报时间、版本和 `better-auth-v1` 能力。浏览器会话达到
`VIDEO_SERVICE_PROTOCOL_RENEWAL_CLIENT_SESSION_MAX_AGE_SECONDS=4800` 秒后，登录调度仍会优先下发
`REFRESH_SESSION`，由桌面端重新登录并上报新 Token 与新 Cookie。该客户端年龄不再是 Syncer 的
硬截止：客户端真实上报或服务端 `SESSION_ALIVE` 保活确认任一仍在 4800 秒窗口内，存储的轮换
Cookie 就可继续参加保活和后续多轮 Token 协议续签；只有两类确认均陈旧、会话被撤销或协议实际
失败时才转入登录兜底。未携带续签会话的新版上报会删除服务端旧会话；监控中的“会话覆盖”仍只
统计 80 分钟内真实上报的会话，用于衡量桌面轮换健康，不等同于协议候选资格。
续签资格与任务资格分开计算：只要已有 Token 且同步积分仍大于 `0`，即使账号因低于任务阈值而
处于 `LOW_BALANCE/LOW_BALANCE_DISABLED`，Syncer 协议路径和登录 Worker 兜底路径都会继续多轮
续签；未知积分先保持续签资格，首次确认积分为 `0` 后才停止。任务分配和提交前余额校验仍要求
`ACTIVE` 且满足 `VIDEO_SERVICE_LOW_BALANCE_THRESHOLD`，不会因续签放宽而提交低余额任务。
协议领取会优先处理 `ACTIVE/TOKEN_EXPIRING/TOKEN_EXPIRED` 等任务账号，再处理仍有正积分的
`LOW_BALANCE_DISABLED` 账号，避免低余额批次占满有限的协议请求节流槽。Token 时间已经越过到期点、
但加密会话仍处于 `IDLE/PENDING/RETRY/RUNNING` 且客户端上报或服务端保活确认仍在有效窗口内时，
账号保持 `TOKEN_EXPIRING + token_renewal_pending`；会话确认陈旧或续签进入 `FALLBACK` 后才标记
`TOKEN_EXPIRED`。该状态仅控制调度与展示，不放宽任务提交的 Token 守卫。
未携带续签会话、Token 已过期或上游返回未授权的账号继续走登录作业兜底。
每次续签终态同时追加到 `protocol_renewal_events`；Syncer 每 15 秒更新
`protocol_renewal_runtime` 心跳。管理接口据此区分正常空闲、成功率下降、队列延迟和执行器失联。
严格成功要求结果已应用且新到期时间晚于旧到期时间。健康目标、最小样本、心跳和队列阈值由
`VIDEO_SERVICE_PROTOCOL_RENEWAL_SUCCESS_RATE_TARGET`、
`VIDEO_SERVICE_PROTOCOL_RENEWAL_HEALTH_MIN_SAMPLE`、
`VIDEO_SERVICE_PROTOCOL_RENEWAL_HEARTBEAT_STALE_SECONDS` 和
`VIDEO_SERVICE_PROTOCOL_RENEWAL_QUEUE_LAG_WARN_SECONDS` 配置。事件只记录状态、时延、错误码和
到期时间，并按 `VIDEO_SERVICE_PROTOCOL_RENEWAL_EVENT_RETENTION_DAYS=30` 清理；事件记录排除
Token、Cookie、密码、请求头和上游原始响应。
默认严格成功率目标为 70%；终态样本达到健康最小样本数后，低于该值即进入降级告警。
已同步且低于 `VIDEO_SERVICE_LOW_BALANCE_THRESHOLD` 的账号不会下发；刚导入、尚无余额快照的
账号只会在 ACTIVE 积分总额存在缺口时进入首次登录。完整状态机、请求示例和重试约定见
[`docs/account-login-jobs.md`](docs/account-login-jobs.md)。

### 读取模型目录

批量核对注册成功流水中哪些账号具备 Seedance 2.5 时，运行只读审计脚本。脚本直接从数据库筛选
`SUCCEEDED + is_used=false + awarded_points=8500`，在内存中解密已存 JWT 身份声明并查询账号功能
开关与公共模型 release，输出完整 JSON/CSV、命中账号 CSV 和按 release 保存的模型列表；不会
续签会话、修改使用状态或输出邮箱、密码、Cookie、Token：

```bash
set -a; . /opt/frame-ops/shared/config/api.env; set +a
/opt/frame-ops/current/venv/bin/python \
  /opt/frame-ops/current/apps/api/scripts/audit_seedance25_accounts.py \
  --concurrency 12
```

先只核对数据库命中数量、不请求上游时加 `--dry-run`。可用 `--offset`、`--limit` 分片续跑，
或用 `--used` 显式检查已使用记录；默认结果写入 `work/seedance25-account-audit-<时间>/`，也可用
`--output-dir` 指定目录。脚本默认按 8500 积分 Canva 会话使用 `plan=BASIC` 构造功能开关上下文，若
目标批次的计划不同可显式传 `--plan PLAN_NAME`。

若要核对“账号池”而不是注册流水，并覆盖 `ACTIVE`、低余额、Token 过期等全部状态，使用独立的
全池脚本。它通过管理 API 获取账号快照及已存 Token；即使 Token 已过期，仍可使用 JWT 中的稳定
身份声明读取公开功能开关，同时在结果中用 `token_expired` 和 `usable_now` 区分模型可见性与当前
可执行性：

```bash
python3 apps/api/scripts/audit_seedance25_account_pool.py \
  --env-file .env.local \
  --status ALL \
  --concurrency 24
```

全池脚本输出完整结果、Seedance 2.5 命中账号、未知账号和模型 release 四类报告；所有报告均为
私有文件且不包含密码或原始 Token。可用 `--status ACTIVE` 只扫单一状态，或用 `--offset`、
`--limit` 分片。

```bash
curl 'http://127.0.0.1:18080/v1/models?type=MODEL' \
  -H 'X-API-Key: local-api-key'
```

API 使用上游 GraphQL `HomepageCards` 查询，将 `data.homepageCards` 转成稳定的
`ModelCatalogResponse`，并从每张卡片的 `url` 查询参数解析 `model` 标识。API 随后合并本系统
已经接入、但上游首页卡片可能尚未展示的模型；当前固定补入 `seed-audio-1.0`，若上游已经返回
同一模型则保留上游卡片且不重复。`type` 默认是 `MODEL`，也可传 `BLUEPRINT`、`GUIDE` 或
`ALL`；响应按 `rank` 排序，并带有浏览器端 300 秒缓存指令。该目录是上游首页推荐与系统已
接入模型的组合，不等同于平台所有可用模型的完整枚举。

### 提交任务示例

```bash
curl -X POST http://127.0.0.1:18080/v1/tasks \
  -H 'X-API-Key: local-api-key' \
  -H 'Idempotency-Key: request-00000001' \
  -H 'Content-Type: application/json' \
  -d '{
    "provider":"leonardo",
    "model":"hailuo-03",
    "task_type":"VIDEO_GENERATION",
    "mode":"text-to-video",
    "input":{
      "prompt":"A paper boat on a calm lake",
      "duration":5,
      "resolution":"2K",
      "aspect_ratio":"16:9"
    },
    "estimated_credit_cost":700
  }'
```

`image-to-video` 可在 `input` 中提供 `image_url` 和可选 `end_image_url`；`reference-to-video` 可提供有序的 `reference_image_urls`、`reference_video_urls` 和 `reference_audio_urls`。

Hailuo H3 的 Leonardo UI 预览报价已记录在接入文档：当前只有 2K 档，5 秒各比例均为 `700`；`2K + 16:9` 时 10 秒为 `1400`、15 秒为 `2100`。定价规则版本为 `leonardo-ui-20260807.v2`，类型化任务会自动计算并预留积分；提交时以上游 `apiCreditCost` 校正预留，结算以任务的 `actual_credit_cost` 为准，估算/预留/实际值会写入积分流水。

Hailuo H3 提示词在 API 入库和 Worker 组装上游请求时都会去除首尾空白并截断到最多 `2000` 个 Unicode 字符，避免 Leonardo H3 在 `Generate` 阶段返回 `VALIDATION_ERROR`。

### Seedance 2.0 系列

完整的请求字段、三种模式、媒体限制、状态轮询、成功/失败输出和客户端示例见 [`docs/seedance-2-request-guide.md`](docs/seedance-2-request-guide.md)。

后端的 `seedance.v1` 请求模型覆盖以下三个 Leonardo 模型 ID：

| 模型 | 时长 | 分辨率档位 |
| --- | --- | --- |
| `seedance-2.0-mini` | 4–15 秒 | `480P`、`720P` |
| `seedance-2.0` | 4–15 秒 | `480P`、`720P`、`1080P`、`4K` |
| `seedance-2.0-fast` | 4–15 秒 | `480P`、`720P` |

4 秒、`16:9`、数量 1、无参考素材条件下，Leonardo UI 的积分预览为：Mini `480P=320`、`720P=640`；标准版 `480P=562`、`720P=1209`、`1080P=2721`、`4K=7616`；Fast `480P=449`、`720P=967`。标准版 480P 的当前浏览器时长表为 `4..15 秒 = 562,703,843,984,1124,1265,1406,1546,1687,1828,1968,2109`。定价规则版本为 `leonardo-ui-20260808.v8`；类型化任务始终按调用方选择的模型报价，标准版 480P 使用浏览器实测表，其余组合按 4 秒基准线性换算并向上取整。提交时以上游 `apiCreditCost` 校正预留，最终以 `actual_credit_cost` 结算并写入积分流水。

Seedance Video Reference 与 Leonardo 当前浏览器选择器保持一致：单个视频仅接受 MP4/MOV、3–10 秒、宽高各 720–2160px、24–60 FPS；最多 3 个参考视频且总时长不超过 15 秒。API 在上传和 Generate mutation 前均执行预检（包括无效音轨），避免把明确的媒体参数错误折叠为 `UPSTREAM_GRAPHQL_ERROR`。`seedance-2.0-mini`、`seedance-2.0`、`seedance-2.0-fast` 始终按请求模型原样提交，不做模型替换。

Leonardo 的 `UploadMedia` mutation 在对象上传完成后、生成服务可解析该媒体 ID 之前就会返回。Worker 默认等待 `VIDEO_SERVICE_MEDIA_UPLOAD_SETTLE_SECONDS=8` 秒后再发 Generate；线上同一上传 ID 的实测结果为上传后约 4.4 秒仍返回通用 GraphQL 错误、约 5.9 秒可被同一 Seedance 2.0 请求接受。该等待只覆盖音频/视频上传处理窗口，不改变调用方选择的模型或 Generate 参数。

这条媒体处理等待由 Mini、标准版、Fast 共用；三模型的视频参考请求都保持调用方指定的模型 ID，并统一使用 `guidances.video_reference_base`、省略旧 `mode` 字段。生产回归覆盖了三模型的一次提交受理：标准版和 Fast 视频单参考均完成，Mini 的安全图片+视频 Omni 组合完成；Mini 视频单参考也一次获得 generation ID，随后由模型审核终止并退回积分，属于生成阶段结果而非上传竞态或模型替换。

账号分配前，Worker 会按当前规则重新计算任务预算，并且只锁定 `balance_credits - reserved_credits >= estimated_credit_cost` 的活跃账号；候选账号优先按并发数、最近分配时间和可用积分排序。锁定账号后在同一事务内增加预留，提交上游前再刷新一次真实余额，防止余额已被其他任务占用时继续提交。

Leonardo 偶发返回 `Missing "data" field with no errors in response from remote` 时，API 将其归类为 `UPSTREAM_PROVIDER_UNAVAILABLE`，与普通请求级 `UPSTREAM_GRAPHQL_ERROR` 分开处理。Worker 对该上游故障默认最多提交 5 次，按 10、20、40、80 秒退避；普通提交错误仍使用 3 次和 2 秒基准退避。可分别通过 `VIDEO_SERVICE_UPSTREAM_OUTAGE_MAX_SUBMIT_ATTEMPTS` 与 `VIDEO_SERVICE_UPSTREAM_OUTAGE_RETRY_BASE_SECONDS` 调整。

三个模型均接受 `21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16`。支持的模式为：

- `text-to-video`：文生视频。
- `image-to-video`：首帧和可选尾帧。
- `reference-to-video`：Omni 参考模式；`omni` 与兼容拼写 `omini` 会归一为该模式。

Omni 单任务最多接收 4 张参考图片、3 个参考视频和 1 个参考音频。音频参考需与至少一张图片或一个视频一起提交。媒体 URL 会由 worker 下载、校验、上传到 Leonardo，然后转换成 `image_reference`、`video_reference_base` 和 `audio_reference` guidance。

上游参数以浏览器 GraphQL 为准：Mini 与 Fast 始终使用 `width`/`height`，不发送
旧的 `parameters.mode`；标准版仅在文生视频、首尾帧模式发送该字段。三个模型的
Omni 请求均不发送 `parameters.mode`，并保持调用方选择的模型 ID。
Omni/Omini 只决定 `guidances` 参考结构；标准版和 Fast 均不得替换为 Mini。

分辨率到 Leonardo UI/GraphQL 提交尺寸的映射如下。供应商媒体元数据可能与实际文件流不同；生产 `480P`、`16:9` 请求提交为 `864×496`，API 元数据为 `864×480`，实际 MP4 流经 ffprobe 验证为 `864×496`：

| 比例 | 480P | 720P | 1080P | 4K |
| --- | --- | --- | --- | --- |
| `21:9` | 992×432 | 1470×630 | 2520×1080 | 5040×2160 |
| `16:9` | 864×496 | 1280×720 | 1920×1080 | 3840×2160 |
| `4:3` | 752×560 | 1112×834 | 1440×1080 | 2880×2160 |
| `1:1` | 640×640 | 960×960 | 1440×1440 | 2880×2880 |
| `3:4` | 560×752 | 834×1112 | 1080×1440 | 2160×2880 |
| `9:16` | 496×864 | 720×1280 | 1080×1920 | 2160×3840 |

Mini 文生视频示例：

```bash
curl -X POST http://127.0.0.1:18080/v1/tasks \
  -H 'X-API-Key: local-api-key' \
  -H 'Idempotency-Key: seedance-mini-request-0001' \
  -H 'Content-Type: application/json' \
  -d '{
    "provider":"leonardo",
    "model":"seedance-2.0-mini",
    "task_type":"VIDEO_GENERATION",
    "mode":"text-to-video",
    "input":{
      "prompt":"A red paper boat on a calm lake",
      "duration":4,
      "resolution":"480P",
      "aspect_ratio":"16:9"
    },
    "estimated_credit_cost":320
  }'
```

### Seedance 2.5

独立的 `seedance-2.5.v1` 契约和完整请求说明见 [`docs/seedance-2.5-request-guide.md`](docs/seedance-2.5-request-guide.md)。模型 ID 为 `bytedance/seedance-2.5`，支持文生视频、首尾帧和 Omni 多模态参考；参考容量为 30 张图片、10 个视频和 10 个音频。参考视频只要求可探测到正时长，视频总时长不超过 30 秒，本地预检不限制单视频时长区间或视频宽高；参考音频单个 2–30 秒且音频总时长不超过 30 秒，两类额度分别计算。输出时长 `4–30` 秒，分辨率为 `480P` / `720P`，比例为 `21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16`。Audio 可开关，`quantity=1`、`public=false`、`seed=-1` 由 Worker 固定。定价规则 `leonardo-ui-20260812.v15` 对含视频参考任务增加上游实际要求的处理费率：`480P +90/s`、`720P +180/s`，避免把余额只够基础输出费用的账号提交给上游。

浏览器实测定价为 480P 每秒 180 积分、720P 每秒 292 积分，Audio On / Off 报价相同。Worker 领取前按 `leonardo-ui-20260810.v9` 重新报价，仅选择可用余额覆盖预算的账号，并在领取事务内预留积分。

本地 Compose 接入结果见 [`docs/seedance-2.5-local-verification.md`](docs/seedance-2.5-local-verification.md)。报告明确区分 mock 状态机验收与真实提供方生成证据。
30 张图片、10 个视频和 10 个音频的边界准备及可选目标 API 冒烟脚本为
`scripts/smoke_seedance25_reference_limits.py`；默认只生成请求证据，只有显式传入 `--live`
才会提交任务，输入媒体 JSON 通过命令行参数提供。

### Kling Video O3 Omni

完整字段、三种模式、尺寸矩阵、参考视频 ID 用法和积分规则见 [`docs/kling-video-o3-request-guide.md`](docs/kling-video-o3-request-guide.md)。模型 ID 为 `kling-video-o-3`，支持 3–15 秒、`720P`/`1080P`/`4K`、`16:9`/`1:1`/`9:16`，模式为文生、首尾帧和 Omni 参考生视频。浏览器实测积分为：720P 无/有音频 `168/224` 每秒，1080P `224/280` 每秒，4K 均为 `420` 每秒。规则引擎会在任务创建和 Worker 领取前重新预估，并只选择可用余额（余额减去预留）不低于预算的账号。

### GPT Image 2

完整字段、两种模式、30 组像素尺寸和 90 档积分见 [`docs/gpt-image-2-request-guide.md`](docs/gpt-image-2-request-guide.md)。模型 ID 为 `gpt-image-2`，任务类型为 `IMAGE_GENERATION`，支持 `text-to-image` 和带 1–6 张公网参考图的 `image-to-image`。`prompt_enhance=OFF`、Style None、`quantity=1` 与参考强度 `MID` 均由 Worker 固定；调用方只配置提示词、质量、宽高比和 Size。规则引擎在任务创建和账号分配前按 `leonardo-ui-20260808.v5` 重新报价。

完整冒泡脚本为 `scripts/smoke_gpt_image_2.py`：无 `--live` 时验证全部 180 个模式/质量/比例/Size 契约组合；加 `--live` 时提交 10 个代表性生产任务，下载结果并核对输入分辨率、输出元数据、真实像素、MIME 与预估/预留/实际积分。默认真实矩阵预算 1012 积分，并由 `--max-credits` 提交前限额。

### Nano Banana 2 / Nano Banana Pro

完整字段、10 种比例、30 组尺寸、模式和积分见 [`docs/nano-image-request-guide.md`](docs/nano-image-request-guide.md)。公共模型 ID 为 `nano-banana-2`、`nano-banana-pro`；两者支持文生图和 1–6 张参考图的图生图。Worker 固定 `public=false`、`prompt_enhance=OFF`、Style None、`quantity=1`，按 `leonardo-ui-20260808.v5` 报价并只分配可用余额覆盖预算的账号。

完整冒烟脚本为 `scripts/smoke_nano_images.py`：默认验证全部 120 个模型/模式/比例/Size 契约组合；`--live` 提交 12 个代表性任务并验证输入/输出像素与预估、预留、实际积分。

### Gemini Omni Flash

完整请求字段、两种模式、尺寸与积分矩阵见 [`docs/gemini-omni-flash-request-guide.md`](docs/gemini-omni-flash-request-guide.md)。模型 ID 为 `gemini-omni-flash`，支持 `text-to-video` 与带 1–5 张公网参考图的 `reference-to-video`（`omni`/`omini` 会归一为参考模式），时长 3–10 秒，固定 `720P`，比例为 `16:9` 或 `9:16`。Worker 固定 `public=false`、`quantity=1`、参考强度 `MID`，按 100 积分/秒预算，并在账号分配前复算与预留。

冒烟脚本 `scripts/smoke_gemini_omni_flash.py` 默认验证全部 32 个模式/比例/时长契约组合；加 `--live` 会真实提交 16:9 文生视频和 9:16 参考图生视频，下载 MP4 并核对输入/输出尺寸、时长和积分结算。

生产 `frame-ops-v1.0.28` 的真实验收为 2/2 通过，总实际积分 600；任务、上游 ID、ffprobe 和 `SETTLE` 流水见 [`docs/gemini-omni-flash-smoke-report.md`](docs/gemini-omni-flash-smoke-report.md)。

### 失败任务输出

任务失败时，顶层 `error_code` 与 `error_message` 保持可直接判断；同步器会同时查询
Leonardo 的 `generation_notes`，按 Web 端相同优先级解析
`failureReason.errorCode` 和 `noteType`。上游返回的失败详情会写入
`output.error`，并保留提交阶段的 `output.submit`。例如内容审核失败会返回：

```json
{
  "status": "FAILED",
  "output": {
    "submit": {"apiCreditCost": null},
    "provider": "leonardo",
    "generation_id": "UPSTREAM_TASK_ID",
    "error": {
      "code": "PROVIDER_MODERATION_ERROR",
      "message": "The content of your generation was moderated by this Model. Try rewording your prompt, changing reference images or changing the Model. Your tokens have been credited back to your account.",
      "upstream_status": "FAILED",
      "nsfw": false,
      "flagged": false,
      "note_type": "PROVIDER_FAILURE",
      "failure_reason": {
        "errorCode": "PROVIDER_MODERATION_ERROR"
      }
    }
  },
  "error_code": "PROVIDER_MODERATION_ERROR",
  "error_message": "The content of your generation was moderated by this Model. Try rewording your prompt, changing reference images or changing the Model. Your tokens have been credited back to your account."
}
```

`failureReason.errorCode` 支持与 Leonardo Web 一致的八种类型：

- `PROVIDER_AUTHENTICATION_ERROR`
- `PROVIDER_RATE_LIMIT`
- `PROVIDER_INTERNAL_ERROR`
- `PROVIDER_INVALID_REQUEST`
- `PROVIDER_MODERATION_ERROR`
- `PROVIDER_OUTPUT_ERROR`
- `PROVIDER_TIMEOUT`
- `ALL_PROVIDERS_FAILED`

另外兼容 `noteType=CC_NSFW_TOTAL_FAILURE`。没有已知失败码时返回
`UPSTREAM_GENERATION_FAILED` 和 Leonardo Web 的视频失败兜底文案。

## 测试与验收

```bash
# 仓库根目录
docker compose run --rm api pytest -q
docker compose run --rm api ruff check src tests scripts
docker compose run --rm api python -m compileall -q src scripts tests migrations

# 按项目领取邮箱的基础回归
docker compose run --rm api pytest -q \
  tests/test_project_mailbox_claims.py \
  tests/test_project_mailbox_claim_api.py
```

完整本地 Smoke Test：

```bash
python3 apps/api/scripts/smoke_test.py
```

Smoke Test 会创建本地空间、账号和任务记录，用于验证幂等提交、任务完成、积分结算和 Token 版本冲突。运行前应确认当前数据库允许写入测试数据。

## 与另外两个主服务的连接

- 桌面端使用管理接口同步账号和 Token；相关客户端位于 `apps/desktop/lib/account-backend-sync.js` 与 `video-task-backend-client.js`。
- Web 使用 `/api` 同源路径访问本服务；Nginx 在 `apps/web/nginx/default.conf` 中完成反向代理。
- API 不读取桌面端本地浏览器数据，Web 也不直接连接 MySQL。

跨服务运行和故障定位见 [`../../docs/operations.md`](../../docs/operations.md)。

Veo 3.1 使用 `veo-3.1.v1` 类型化合同，支持文生、首尾帧和 1–3 张图片参考三种视频模式。请求字段、尺寸和积分矩阵见 [`docs/veo-3.1-request-guide.md`](docs/veo-3.1-request-guide.md)，生产组合、输出探测、扣费对账及参考模式上游限制见 [`docs/veo-3.1-smoke-report.md`](docs/veo-3.1-smoke-report.md)；初始合同与发布验收决策保留在 [`docs/veo-3.1-integration-plan.md`](docs/veo-3.1-integration-plan.md)。

Veo 3.1 Fast 使用模型标识 `veo-3.1-fast-generate-001` 与 `veo-3.1-fast.v1` 合同，支持文生和首尾帧两种模式；数量固定为 1、`public=false`，参数、尺寸和浏览器实测积分矩阵见 [`docs/veo-3.1-fast-request-guide.md`](docs/veo-3.1-fast-request-guide.md)。

Veo 3.1 Lite 使用 `veo-3.1-lite.v1` 类型化合同，支持文生与首尾帧两种视频模式、4/6/8 秒、720P/1080P 横竖屏和可选音频。请求、输出与积分矩阵见 [`docs/veo-3.1-lite-request-guide.md`](docs/veo-3.1-lite-request-guide.md)，生产双模式结果、尺寸探测、积分流水及回滚证据见 [`docs/veo-3.1-lite-smoke-report.md`](docs/veo-3.1-lite-smoke-report.md)。

### Seed Audio 1.0

Seed Audio 1.0 使用模型标识 `seed-audio-1.0`、任务类型 `AUDIO_GENERATION` 和 `seed-audio-1.v1` 合同。支持 1–3000 字符文字转语音、音色 ID、0.50–2.00 语速/音量、-12–12 音高及 1–4 条输出；上游请求固定 `public=false`。Leonardo UI 实测为每条 350 积分，Worker 在账号分配前按 `350 × quantity` 预算、筛选余额、预留并在终态写入积分流水。完整字段见 [`docs/seed-audio-1-request-guide.md`](docs/seed-audio-1-request-guide.md)，契约/真实任务脚本为 `scripts/smoke_seed_audio.py`。
