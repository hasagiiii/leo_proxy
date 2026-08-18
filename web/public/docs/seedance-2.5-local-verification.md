# Seedance 2.5 本地接入验收记录

验收日期：2026-08-10
模型：`bytedance/seedance-2.5`
Schema：`seedance-2.5.v1`
定价版本：`leonardo-ui-20260812.v15`

> 本记录使用 `VIDEO_SERVICE_UPSTREAM_MODE=mock` 验证 API、Worker、媒体解析、请求组装、积分预留与结算状态机。`example.invalid` 输出是本地占位结果，不代表真实提供方生成已经完成；真实提供方冒烟需在发布闸门阶段单独记录。

> 2026-08-10 时长规则修正：参考视频总时长和参考音频总时长分别不得超过 30 秒；Seedance 2.5 不再设置本地单视频时长区间，视频只需可探测到正时长，单音频为 2–30 秒。下方 50 资产 Mock 记录只保留为历史请求组装证据，其中 10 段 Mock 视频合计 50 秒的结果已被新规则取代。

> 2026-08-10 尺寸规则修正：Seedance 2.5 参考视频继续探测宽高并记录元数据，但本地预检不再设置 720–2160 像素的宽高上下限；格式、时长、帧率和音轨校验保持生效。

## 自动化检查

| 检查 | 结果 |
|---|---|
| API 参考容量专项 pytest | `134 passed` |
| API 全量 pytest | `609 passed` |
| API Ruff | `All checks passed!` |
| Web `npm ci` | 0 个漏洞 |
| Web production build | 成功，2171 modules transformed |
| Compose API readiness | `{"status":"ready"}` |
| Compose console health | `ok` |
| Markdown 静态文件 | HTTP 200，`text/plain; charset=utf-8` |
| 参考容量契约 | 图片 30、视频 10、音频 10；超出任一上限返回 422 |

## 任务矩阵

| 模式 | 时长 | 档位 / 比例 | Audio | 预算 / 实扣 | 结果 | Task UUID |
|---|---:|---|---|---:|---|---|
| 文生视频 | 4s | 480P / 21:9 | Off | 720 / 720 | COMPLETED | `9523ffa8-b08c-4a4a-b1f0-2998921786e5` |
| 文生视频 | 8s | 720P / 9:16 | On | 2336 / 2336 | COMPLETED | `cc60d1ea-26d1-4952-9e87-2f46d09b085b` |
| 文生视频 | 30s | 720P / 1:1 | Off | 8760 / 8760 | COMPLETED | `7421ab89-2ab9-4135-9a85-1410eb39539e` |
| 首尾帧 | 4s | 480P / 16:9 | Off | 720 / 720 | COMPLETED | `d90febe7-488c-4ee5-8d8b-9481e83664a9` |
| Omni 图片+视频+音频 | 4s | 720P / 4:3 | On | 1168 / 1168 | COMPLETED | `8e3eaba7-74b2-4c11-b821-f0af9141cf39` |
| Omni 历史容量组装：30 图+10 视频+10 音频 | 4s | 480P / 16:9 | On | 720 / 720 | 历史 Mock 结果；当前受独立 30 秒音频/视频总时长约束 | `3727f511-f52f-4e1a-85a7-de77cf32745a` |

## 上游请求组装

三组文生请求分别组装为：

| 输入 | 上游 width × height | 固定字段 |
|---|---:|---|
| 480P / 21:9 | `992×432` | `public=false`、`quantity=1`、`seed=-1` |
| 720P / 9:16 | `720×1280` | `motion_has_audio=true` |
| 720P / 1:1 | `960×960` | `duration=30` |

首尾帧任务解析 2 个资产，并写入 `guidances.start_frame` 与 `guidances.end_frame`。基础 Omni 任务解析 4 个资产，写入两张图片的 `strength=HIGH/MID` 与 `order=0/1`、一个 `video_reference_base` 和一个 `audio_reference`。

历史容量任务共解析 `50/50` 个资产；持久化上游请求的 guidance 数量为 `image_reference=30`、`video_reference_base=10`、`audio_reference=10`，并确认 `public=false`。该任务的 10 段 Mock 参考视频各 5 秒、合计 50 秒，现应由 `MEDIA_COMBINED_DURATION_INVALID` 拒绝。当前规则允许视频合计恰好 30 秒、音频合计恰好 30 秒，并分别拒绝任一类别超过 30 秒的组合。分别提交 31 张图片、11 个视频和 11 个音频时，接口仍返回 HTTP 422；OpenAPI 中对应 `maxItems` 为 `30/30/10/10`。

## 积分门槛验证

最大规格任务预算为 `292 × 30 = 8760`。验收时配置两个 ACTIVE 账号：

- 账号 A 可用余额 `8759`，低于预算 1 积分；
- 账号 B 可用余额 `9000`，覆盖预算。

任务 `6379dc80-643d-47db-8554-719351939bc4` 被分配给账号 B，状态为 `COMPLETED`，`estimated_credit_cost=reserved_credit_cost=actual_credit_cost=8760`，账号 A 未被选中。结论：Worker 领取前的余额门槛和事务内预留已生效。

## 结论

本地接入链路已覆盖三种模式、双分辨率、多比例、Audio 开关、4–30 秒、图片 30 / 视频 10 / 音频 10 的数量契约、视频与音频各自 30 秒的总时长约束、参考顺序与强度，以及预算驱动的账号选择。下一阶段使用真实 provider 运行 `seedance-2.5-integration-plan.md` 的线上矩阵，并校验可下载 MP4 的真实尺寸、时长、音轨和上游 `apiCreditCost`。
