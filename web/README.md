# FRAME OPS Web

`apps/web/` 是本项目的 **Web 主服务**。它是 React + TypeScript + Vite 运维控制台，通过 Video Task API 展示运行总览、账号池、独立邮箱池、独立母号池、成功账号、客户端监控、任务状态和模型信息。

## 主要职责

- 展示账号容量、可用积分、任务吞吐和成功率。
- 运行总览顶部提供“总数 / 当天 / 最近 1 小时”时间维度；任务、模型和积分消耗随维度切换，趋势图自动使用按日、按小时或按 5 分钟粒度，客户端时区用于计算当天边界。
- 运行总览的当前负载和容量卡使用账号资格与 Space 上限共同约束后的有效并发，同时保留账号理论槽位，避免把不可调度槽位显示成可用容量。
- 管理服务端账号、空间、Token 和账号状态。
- 账号池顶部积分卡严格展示 ACTIVE 账号总积分及 100 万目标，低于水位时使用橙色提示补水。
- 账号池顶部汇总各账号状态数量；状态统计卡可直接点击筛选，再次点击当前状态恢复全部账号，无需操作下拉菜单。
- 账号池在状态统计和账号表格之间展示协议续签监控：执行器心跳、严格成功率、成功/失败数、当前队列、会话覆盖、趋势和失败原因均按 15 秒刷新。
- 账号表格增加“协议续签”列，支持按等待、运行、重试、回退和未配置筛选；点击状态可查看该账号最近 20 条续签事件与 Token 延长结果。
- 账号池在搜索和状态筛选结果上提供分页，默认每页 20 条，并可切换为每页 50 或 100 条。
- 账号列表展示账号创建时间，使用 `created_at` 同时显示完整本地时间与相对时间；窄屏优先保留该列。
- 账号池、搜索结果及账号管理弹窗展示完整登录邮箱。
- 账号池支持按 `登录账号 | 登录密码` 每行一条批量导入；导入前必须为整批选择 `mmoshenqi` 或 `macbook` 标签，标签随创建请求持久化到账号记录；粘贴后会忽略空行、清理两侧空格、规范化账号大小写，并在提交前同时识别同批重复项和账号池已有账号，逐行展示重复原因且不向重复项发起导入请求；完成后保留失败行供修正或重试。
- 账号池表格新增“账号来源”列，直接展示账号记录的 `label`：`mmoshenqi` 与 `macbook` 使用不同颜色徽标，历史上没有标签的账号明确显示为“未标注”；窄屏布局优先保留该列。
- 账号池保留上述密码导入入口，并新增独立的 `导入 Cookie ZIP`：浏览器执行 ZIP 后缀和 20 MiB 预检、为每次选中文件生成幂等键，上传后每 1.5 秒读取服务端批次，展示会话验证、积分验证、账号激活、续签就绪以及后续作业/积分观察；页面没有 Cookie 或 Token 明文渲染路径。
- 邮箱池与账号池相互独立，支持粘贴 `邮箱----密码----client_id----refresh_token` 文本、预检格式与重复项、查看后台校验状态，以及重新校验、停用和删除；列表展示导入时间，并可按全部、今天、昨天、2–7 天和 7 天前五个互斥区间筛选；ACTIVE 邮箱可从行级“查看验证码”按钮调用业务接口，等待并展示最近验证码及最小邮件元数据；页面不展示密码、client ID、refresh token 或邮件正文。
- 母号池支持状态、成功/失败、连续 150、运行中数量和“注册记录”右侧抽屉；抽屉按校验中、可入池、失败、已入池筛选，展示后端积分和 Cookie 状态，不渲染任何 Cookie 或 Token 值。
- 侧栏新增独立“成功账号”页，只展示服务端已校验成功的注册记录；表格固定展示账号、积分、注册成功时间、归属母号和是否已使用，支持按账号/母号搜索、已使用/未使用筛选；分页可选每页 20、50、100、500 条，也可输入 1–500 的自定义数量，设置保存在当前浏览器并在切换数量后回到第一页。
- 侧栏 `06 客户端监控` 默认聚合最近 10 分钟的邀请注册客户端，可切换 30 分钟、1/6/24 小时和自定义时段；列表显示健康、吞吐、成功率、耗时与最近错误，详情抽屉显示趋势和分页任务时间线。“无作业”不解释为离线。
- “连接与密钥”中的“注册账号入池设置”通过 Admin API 保存固定目标空间和默认并发，不进入浏览器凭据 localStorage；空间失效时统一暂停手动入池。
- 账号池支持当前页或全部筛选结果跨页选择，并提供“导出选中”和“导出并删除选中”两种凭据操作。导出文件为无表头 UTF-8 文本，每行格式固定为 `邮箱|密码|token`；后一种操作只有在文件完整返回后才携带短期签名回执删除可删除账号。
- 批量删除会保留存在运行任务、预留积分、任务/媒体/积分流水或登录作业历史的账号，并在结果弹窗逐项展示保留原因。
- 账号响应兼容新字段 `login_name` 与旧字段 `login_name_masked`，避免前后端滚动发布期间页面中断。
- 新增账号表单的账号级最大并发默认值为 `3`，提交后仍可在账号编辑中调整。
- 查询、筛选、取消任务并查看结果；任务中心支持状态与模型组合筛选，模型选项来自完整任务历史而非当前分页；任务中心按输出 MIME、URL 后缀及任务类型区分图片、视频与音频，分别提供图片卡片/放大快览、视频播放器和音频播放器。
- 在“模型接入”页通过 `GET /v1/models` 展示上游实时模型卡片与后端补入的系统接入模型；
  `seed-audio-1.0` 即使未进入上游 HomepageCards 也会出现在可搜索目录中。
- 在“模型接入”页提供 Hailuo H3、Seedance 2.0 系列、Kling Video O3 Omni、GPT Image 2、Nano Banana 2/Pro、Veo 3.1 系列与 Seed Audio 1.0 的 Markdown 接入文档入口。
- Markdown 查看入口统一进入 `/docs/viewer.html?doc=...` 在线阅读器，显式“下载 .md”入口仍下载原始文件。
- 保存操作者输入的 API 地址、业务 Key 和管理 Key。

Web 不直接访问 MySQL，也不直接操作桌面 Electron 数据；所有业务请求都通过 API。

客户端监控使用已有 `client_id`，页面显示后 8 位并允许复制完整值。页面不展示 Cookie、Token、报告令牌或密文，也不采集 hostname、MAC、IP 和硬件序列号。

“模型接入”页不会直接调用第三方 GraphQL。`src/api.ts` 携带业务 API Key 请求后端模型
目录，后端完成上游查询、字段归一化和模型标识解析。页面中的 Hailuo H3 请求样例是独立
的固定文档回退，不依赖实时目录成功返回；示例请求的 API 根地址来自操作者当前保存的连接
配置，不硬编码某个部署域名。页面信息架构的历史设计基线见
[`../../docs/model-integration-page-optimization-plan.md`](../../docs/model-integration-page-optimization-plan.md)。

## 代码结构

```text
apps/web/
├── src/
│   ├── App.tsx          # 页面和主交互
│   ├── api.ts           # API 客户端与本地凭据
│   ├── CookieImportModal.tsx # Cookie ZIP 上传、轮询与批次观察
│   ├── cookieImport.ts  # 文件预检、阶段和状态定义
│   ├── taskMedia.ts     # 任务输出媒体识别
│   ├── types.ts         # 接口类型
│   └── styles.css       # 控制台样式
├── public/
│   ├── docs/            # 可在线查看和下载的模型接入 Markdown
│   └── runtime-config.js
├── nginx/
│   ├── default.conf     # 静态站点、Basic Auth 和 /api 反代
│   └── 40-runtime-config.sh
├── Dockerfile
└── vite.config.ts
```

模型接入页的 Gemini Omni Flash 文档入口指向 `/docs/viewer.html?doc=gemini-omni-flash-request-guide.md`。真实冒烟报告保留在静态归档中，不在模型接入页展示。

## 本地开发

先启动 API：

```bash
docker compose up -d mysql migrate api worker syncer
```

再启动 Vite：

```bash
npm --prefix apps/web ci
npm --prefix apps/web run dev
```

开发地址为 `http://127.0.0.1:5173`。Vite 将 `/api` 代理到 `http://127.0.0.1:18080`。

## Compose 运行

从仓库根目录执行：

```bash
docker compose up -d --build console
docker compose ps console
```

默认地址：`http://127.0.0.1:18081`。

Nginx 提供两层连接配置：

1. HTTP Basic Auth 保护整个管理站点。
2. 页面内的 API Key/Admin Key 用于调用 Video Task API。

Compose 控制台的 `/api/` 反代配置了 `client_max_body_size 20m;`，与 Cookie ZIP 的服务端上限
保持一致。生产源站的 `/etc/nginx/conf.d/frame-ops-leo.conf` 也必须在同源 `/api/` location 或
server 块配置相同限制并通过 `nginx -t`。

## Cookie ZIP 操作路径

1. 进入 `账号池`。
2. 点击现有 `批量导入` 与 `添加账号` 之间的 `导入 Cookie ZIP`。
3. 在“新建导入”选择或拖入 ZIP，确认文件名、大小和 SHA-256 状态。
4. 填写目标空间，点击“开始导入”。
5. 保持窗口打开观察 1.5 秒轮询，或关闭窗口让服务端继续处理。
6. 重新打开入口并切换“最近批次”，读取最新 20 批、逐项状态、积分、Token 到期和续签结果。

终态会刷新账号池、ACTIVE 数、积分与续签监控。`已进入调度` 表示至少一个导入账号已为
`ACTIVE`；实际图片/视频作业仍从任务入口创建，不由导入弹窗自动发起。

## 成功账号筛选与 Cookie 导出

1. 进入 `成功账号`，顶部 `未使用 · 8,500` 卡片显示全局匹配数量；点击卡片会同时启用
   `未使用` 与 `8,500 积分` 两个筛选，二者也可在工具栏独立组合。
2. 表格只允许勾选未使用账号；表头复选框会选择当前页全部未使用账号。
   页面每页最多 500 条，因此一次 Cookie 导出最多提交 500 个邮箱；超过上限的请求由 API 以参数校验拒绝。
3. 点击 `导出选中` 后下载 ZIP：每个账号对应一个以邮箱命名的 Cookie JSON；压缩包还包含
   `emails.txt`（每行一个邮箱）和 `leodev_links.txt`（每行一个带 URL 编码邮箱参数的
   `https://leodev.app/?email=...` 链接），顺序与选中账号一致。
4. 服务端完成整批导出后自动把这些注册记录标记为 `已使用`，页面随即刷新顶部数量和表格。

导出响应使用 `Cache-Control: no-store`；页面不展示 Cookie 内容，已使用行的复选框保持禁用，
避免操作员从台账重复分配同一批账号。

相关环境变量：

| 变量 | 用途 |
| --- | --- |
| `CONSOLE_AUTH_USERNAME` | Basic Auth 用户名，默认 `admin` |
| `CONSOLE_AUTH_PASSWORD_SOURCE` | Compose 读取的密码文件路径 |
| `UI_API_BASE` | 页面 API 根路径，Compose 默认 `/api` |
| `UI_BOOTSTRAP_API_KEY` | 可选的初始业务 Key |
| `UI_BOOTSTRAP_ADMIN_KEY` | 可选的初始管理 Key |

`40-runtime-config.sh` 在容器启动时生成 `/runtime-config.js`。共享环境应将两个 bootstrap Key 留空，由操作者在“连接与密钥”中录入；页面将连接信息保存在浏览器 `localStorage`。

## 构建与验收

```bash
npm --prefix apps/web ci
npm --prefix apps/web run build
npm --prefix apps/web audit --omit=dev
```

容器检查：

```bash
curl -fsS http://127.0.0.1:18081/console-health
curl -fsS -u "admin:$(cat .secrets/console-auth-password)" \
  http://127.0.0.1:18081/api/health/ready
```

预期结果：

- `/console-health` 返回 `ok`。
- 未认证访问首页返回 HTTP 401。
- 认证后首页、JS/CSS、`runtime-config.js` 均返回 HTTP 200。
- 认证后 `/api/health/ready` 返回 `{"status":"ready"}`。

## 与另外两个主服务的关系

- Web 仅通过 [`../api/`](../api/README.md) 读写服务端业务数据。
- 桌面端与 Web 没有直接 IPC 或文件依赖。
- 三服务部署和状态判断见 [`../../docs/operations.md`](../../docs/operations.md)。

模型接入页的 Veo 3.1 文档入口指向 `/docs/viewer.html?doc=veo-3.1-request-guide.md`。生产组合、输出尺寸、积分对账与接口问题报告可由 `/docs/viewer.html?doc=veo-3.1-smoke-report.md` 直接打开；模型接入页按统一策略只展示接入指南，不混入测试报告按钮。

Veo 3.1 Fast 文档入口指向 `/docs/viewer.html?doc=veo-3.1-fast-request-guide.md`，Markdown viewer 对该文件采用内联打开方式。

Veo 3.1 Lite 文档入口指向 `/docs/viewer.html?doc=veo-3.1-lite-request-guide.md`。生产真实任务报告保留在静态归档中，不在模型接入页展示。

Seed Audio 1.0 文档入口指向 `/docs/viewer.html?doc=seed-audio-1-request-guide.md`；任务中心将 `audio/*`、MP3、WAV 等结果识别为音频并直接展示播放器。
