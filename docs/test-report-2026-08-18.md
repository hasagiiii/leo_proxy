# LEO Proxy 迁移验收测试报告

- 测试日期：2026-08-18（Asia/Shanghai）
- 测试环境：本机 Docker Compose
- 上游模式：`leonardo`（真实上游，不是 Mock）
- 结论：**通过**

## 1. 验收范围

本次完成了独立目录 `leo_proxy/` 的创建与迁移，仅保留 Web 和 API，覆盖运行总览、账号池、任务中心、模型接入。桌面客户端及邮箱、母号、客户端注册/登录任务等能力不在新应用的导航和公开路由中。

## 2. 自动化测试结果

| 检查项 | 结果 | 证据 |
|---|---:|---|
| API 单元/集成测试 | 通过 | `1199 passed` |
| API Ruff 静态检查 | 通过 | `All checks passed` |
| Web Vitest | 通过 | 20 个测试文件、41 个测试全部通过 |
| Web 生产构建 | 通过 | Vite 构建完成，2181 个模块转换 |
| Web 生产依赖审计 | 通过 | `npm audit --omit=dev`：0 个漏洞 |
| Compose 配置解析 | 通过 | `docker compose config --quiet` 退出码 0 |
| API Ready | 通过 | `GET /health/ready` 返回 `ready` |
| Web Health | 通过 | `/console-health` 返回 `ok` |

说明：迁移初检曾包含原系统已删除路由的测试，产生 24 个预期范围冲突；将这些非迁移模块测试从独立项目测试集移出后，最终测试集全绿。完整 npm 开发依赖审计仍报告 1 个仅存在于开发工具链的 high 项，生产依赖审计为 0。

## 3. 服务与接口边界

Compose 启动并保持运行的组件：MySQL、migrate、API、worker、syncer、Web。API OpenAPI 文档共 24 条路径，均属于健康检查、任务、模型、账号、Cookie 导入、空间、统计或协议续签范围。

以下排除路由均实测返回 404：

- `/admin/mailboxes`
- `/admin/parent-accounts`
- `/admin/registration-records`
- `/admin/clients`
- `/v1/login-jobs`

## 4. Cookie ZIP 导入测试

测试文件：`leonardo-150-cookies-4-unused-20260818-094839.zip`

| 指标 | 结果 |
|---|---:|
| 压缩包归档项 | 6 |
| 解析账号 | 4 |
| 新建账号 | 4 |
| 更新账号 | 0 |
| 失败账号 | 0 |
| 最终批次状态 | `COMPLETED` |
| ACTIVE / RENEWAL_READY | 4 / 4 |
| 导入账号当前总积分 | 592 |

导入批次 UUID：`f0169a1d-5379-4e7f-88b3-8535aa7925a3`。报告未记录账号邮箱、Cookie 或 Token。

## 5. 真实图片任务

最终成功任务：

- 任务 UUID：`3c54fc24-5985-4dcc-b323-95a3eeab73fb`
- 模型：`gpt-image-2`
- 模式：文生图
- 任务状态：`COMPLETED`
- 输出：JPEG，1024 × 1024
- 预计/实际积分：8 / 8
- Generation ID：`1f19af32-a362-6d00-bdeb-aecb760ddeaf`
- 本地校验证据：`.evidence/real-image-task-3c54fc24.jpg`
- SHA-256：`329f27f963fe5cf7969fc4d29b6e583ca1fe28733eab03597d7c4eb1c4d28717`
- CDN 输出：[打开真实生成图片](https://cdn.leonardo.ai/users/3bfe7a25-0b7a-456b-b486-76e3c8478311/generations/1f19af32-a362-6d00-bdeb-aecb760ddeaf/gpt-image-2_A_polished_cobalt-blue_paper_airplane_hovering_above_a_matte_white_pedestal_soft-0.jpg)

视觉检查通过：主体是蓝色纸飞机、白色展台与绿色轮廓光，构图完整，无异常文字或水印。

## 6. 浏览器验收

在真实 Chrome 中完成以下检查：

| 页面/场景 | 结果 |
|---|---|
| 运行总览 | 4/4 组件在线，积分、任务、消耗数据正常 |
| 账号池 | 显示 4 个 ACTIVE 账号、总并发 12、续签覆盖 100% |
| 任务中心 | 显示 3 条任务历史；成功任务图片和 8 积分结算可见 |
| 模型接入 | 页面 LIVE，快速开始和模型文档入口可见 |
| 非迁移 URL | `?view=mailboxes` 安全回退到运行总览，导航仍只有 4 项 |
| 响应式/控制台 | 页面可交互，无阻断性运行错误 |

## 7. 测试中发现并修复的问题

1. 原固定 Schema `1.255.2` 已被上游淘汰，真实创建任务返回 500。默认值改为 `latest` 并增加回归测试后，真实任务成功。
2. Basic Auth 首次挑战会让外部 `runtime-config.js` 的重试不执行，导致页面缺少 API 配置。改为在受保护的 HTML 中同步内联运行配置，并保留 no-store 外部配置端点。
3. 修复前的两条诊断任务均失败且实际扣费为 0；它们被保留在本地任务历史中作为排障记录。

## 8. 残余事项与结论

- 当前是本地启动与验收，**未宣称已经部署到生产服务器**。
- 上线前必须替换 Compose 的所有本地默认凭据，并补齐正式域名、TLS、端口和部署路径。
- 为避免额外消耗积分，Schema 修复后只用 GPT Image 2 做了一次真实成功验收，没有再次消耗积分复测 Nano Banana 2。
- 测试证据目录、构建产物、依赖和运行数据均已加入忽略规则，不会作为源码提交。

最终判定：四项功能迁移、服务启动、Cookie ZIP 导入和真实图片生成链路均通过验收。
