# LEO Proxy 架构与迁移边界

## 目标

`leo_proxy/` 是从原 FRAME OPS 中抽离的独立 Web/API 项目，只承载以下四个操作面：

1. 运行总览
2. 账号池
3. 任务中心
4. 模型接入

桌面客户端、邮箱池、母号池、成功账号、客户端监控及其公开 API 均不在迁移范围内。

## 运行结构

```text
Browser
   │ Basic Auth
   ▼
Web / Nginx :28081 ── /api/* ──▶ FastAPI :28080
                                      │
                       ┌──────────────┼──────────────┐
                       ▼              ▼              ▼
                    MySQL          Worker          Syncer
                                      │              │
                                      └──── Leonardo ┘
```

- **Web**：React/Vite 单页控制台，Nginx 提供 Basic Auth、静态资源和 `/api` 反向代理。
- **API**：任务、模型、账号、Cookie ZIP 导入、空间、统计和协议续签接口。
- **Worker**：API 后台组件，领取任务、调用真实或 Mock 上游并记录结算结果。
- **Syncer**：API 后台组件，刷新账号、导入 Cookie ZIP、同步余额与续签状态。
- **MySQL**：账号、导入批次、任务、模型快照和运行统计的持久化存储。

`worker`、`syncer`、`migrate` 和 MySQL 是 API 的运行组件/依赖，不是额外产品服务。

## 对外接口边界

保留的公开路由族：

- `/health/*`
- `/v1/tasks*`
- `/v1/models*`
- `/admin/accounts*`
- `/admin/account-cookie-imports*`
- `/admin/spaces*`
- `/admin/stats*`
- `/admin/protocol-renewals*`

桌面登录任务、客户端注册、邮箱池、母号池和注册记录等路由没有挂载到 LEO Proxy 应用。

## 关键数据流

### Cookie ZIP 导入

Web 上传 ZIP → API 创建导入批次 → Syncer 逐项解析和校验会话 → 账号写入 MySQL → 总览与账号池刷新。

ZIP 原文件、Cookie、Token 和账号凭据不会写入源码目录或测试报告。

### 图片任务

Web/API 创建任务 → Worker 原子领取可用账号 → Leonardo GraphQL 创建生成 → Syncer/Worker 轮询结果 → 保存 CDN 输出、实际积分和任务状态 → Web 任务中心展示。

上游 Schema 默认值为 `latest`，避免固定到已经退役的 Web 协议版本；需要复现特定版本时仍可用环境变量覆盖。
