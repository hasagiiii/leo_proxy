# Veo 3.1 线上真实任务与接口审计报告

测试日期：2026-08-09  
生产 API：`https://api-leo.clawsea.ai`  
模型：`veo-3.1-generate-001`  
Schema：`veo-3.1.v1`  
能力发布：`frame-ops-v1.0.34`  
扣费修复：`frame-ops-v1.0.36`、`frame-ops-v1.0.41`

## 1. 审计结论

Veo 3.1 主模型已经完成生产真实任务、媒体下载、输出探测、积分账本和错误合同审计：

- `text-to-video` 已通过 720P、1080P、4K、横竖屏、4/6 秒及音频开关的真实组合。
- `image-to-video` 的首帧+尾帧模式已真实完成，输入为 720P 竖屏，输出为 720×1280。
- 6 个成功任务均满足预估积分、上游实际积分和任务结算一致，合计实际消耗 6800 积分。
- 下载文件的真实宽高、时长和音轨均与请求一致；静音任务无音轨，`audio=true` 任务包含 AAC 双声道。
- 参数校验、幂等重放与幂等冲突行为正确。
- 已修复“任务运行中刷新到已扣款的上游余额，完成时再次本地扣除”的重复扣费风险。
- 已修复提交前失败时只释放预留但没有积分流水的问题；现在每次失败尝试都写入 `RELEASE`。
- `reference-to-video` 当前仍未通过真实上游验收。单图和双图请求在三个不同账号上均于上游 GraphQL 提交阶段返回 `UPSTREAM_GRAPHQL_ERROR`。参考模式合同、媒体上传、预算与释放链路可用，但生成通道不应标记为稳定。

因此，当前生产稳定范围为：

| 模式 | 生产状态 |
| --- | --- |
| `text-to-video` | 已通过真实任务验收 |
| `image-to-video` | 已通过首尾帧真实任务验收 |
| `reference-to-video` | 接口已接入；上游提交通道阻塞，暂未通过生成验收 |

## 2. 成功任务组合

| 用例 | 模式 | 请求组合 | 任务 UUID | 上游 ID | 输出探测 | 实际积分 |
| --- | --- | --- | --- | --- | --- | ---: |
| 文生横屏静音 | `text-to-video` | 720P / 16:9 / 4s / 无音频 | `aa8cdb27-af54-4a4e-841b-277dc8de6132` | `1f193f06-6c3f-6b30-93ef-2c53ce205df2` | H.264，1280×720，4.000000s，无音轨 | 800 |
| 文生竖屏静音 | `text-to-video` | 1080P / 9:16 / 6s / 无音频 | `319e273d-25c6-4c14-91ba-b82b750b4ee0` | `1f193f0e-346a-6d10-a03c-09725b035977` | H.264，1080×1920，6.000000s，无音轨 | 1200 |
| 文生 4K 静音 | `text-to-video` | 4K / 16:9 / 4s / 无音频 | `20605dc7-bc52-4342-95cb-9b4218e60c21` | `1f193f0a-ee0f-6ad0-b93c-2ff758244024` | H.264，3840×2160，4.000000s，无音轨 | 1600 |
| 文生横屏带音频 | `text-to-video` | 720P / 16:9 / 4s / 音频 | `32239224-b92f-43d6-9103-bf8f53c23879` | `1f193f0a-8d06-65e0-933f-90eb0f445e44` | H.264 + AAC 2 声道，1280×720，4.010000s | 1600 |
| 首尾帧竖屏 | `image-to-video` | 720P / 9:16 / 4s / 无音频 / 首帧+尾帧 | `cb87c45e-637e-4d9a-988a-6850a6f1ca77` | `1f193f0a-8829-6400-95c7-8d7e39f95bd7` | H.264，720×1280，4.000000s，无音轨 | 800 |
| 余额对账复验 | `text-to-video` | 720P / 16:9 / 4s / 无音频 | `7fe5ec17-94e8-494e-b560-5d7454465c48` | `1f193f36-6424-60b0-bff6-7ce9b56a3788` | H.264，1280×720，4.000000s，无音轨 | 800 |

所有成功任务的 `estimated_credit_cost`、`reserved_credit_cost` 与 `actual_credit_cost` 均等于对应表格积分。任务级 `reserved_credit_cost` 是审计快照；账号池的实时 `reserved_credits` 在终态均回到 0。

## 3. 输出文件校验

| 文件 | SHA-256 |
| --- | --- |
| 720P 横屏静音 | `75b8e8529f1659ecdb4f3f84d51c71e7f91a440020a31cca34e42d6cdf34c99d` |
| 1080P 竖屏静音 | `8ca6e8b8f1c966592fc1ad7a4127667f716517bb250da2b4e89389c37cc1778f` |
| 4K 横屏静音 | `154234f93e16908c67a7de51e0e9cd5a518e7260ebd5c4c92a6192aad75f73a2` |
| 720P 横屏带音频 | `ca0849c32ded79095286bf4818b95910861ae7bb831d2f69c63fef67eb26132e` |
| 720P 首尾帧竖屏 | `16b61dc3a966643fb5c4b72b8fc0df42250e40ba853a3b16921481790ad0ab65` |
| 余额对账复验 | `9ef774fd16f67f73f00c878fa8f03730c065f3f4d67b5862f3fade13c3c9c643` |

所有文件都从生产终态 `output.media[].url` 下载，再由 `ffprobe` 读取真实容器、编码、宽高、时长及音轨；没有只依赖 API 元数据判断。

## 4. 参数与幂等合同

| 用例 | HTTP | 结果 |
| --- | ---: | --- |
| `duration=5` | 422 | 仅接受 4、6、8 秒 |
| `resolution=2K` | 422 | 仅接受 720P、1080P、4K |
| 只有尾帧、没有首帧 | 422 | `image_url` 必填 |
| 4 张参考图 | 422 | 最多 3 张 |
| 同一幂等键、相同请求体 | 202 | 返回原任务 UUID，不创建第二个任务 |
| 同一幂等键、不同请求体 | 409 | `IDEMPOTENCY_CONFLICT` |

无效请求在任务持久化与账号分配前结束，不产生积分预留或上游任务。

## 5. 积分对账修复验收

对账任务 `7fe5ec17-94e8-494e-b560-5d7454465c48` 进入 `RUNNING` 后主动调用生产余额刷新接口：

```text
上游刷新前：14778
上游刷新后：13978
变化：       -800
任务预留：    800
```

终态积分流水：

```text
entry_type: SETTLE
credit_delta: -800
balance_before: 13978
balance_after:  13978
reserved_before: 800
reserved_after:  0
balance_reconciled_from_provider: true
```

`credit_delta=-800` 继续表达本任务的真实消耗，但余额不再从已含上游扣款的 13978 再减一次，消除了重复扣费。

## 6. 参考图模式审计

已执行单图、双图、显式 `MID` 和省略强度的真实提交。代表任务：

| 任务 UUID | 参考数 | 尝试账号数 | 终态 | 实际积分 |
| --- | ---: | ---: | --- | ---: |
| `8f22da4c-777b-437e-9c59-3976339f81ac` | 2 | 3 | `FAILED / UPSTREAM_GRAPHQL_ERROR` | 0 |
| `818152a0-9061-4743-88f0-c55d2e0796df` | 1 | 3 | `FAILED / UPSTREAM_GRAPHQL_ERROR` | 0 |
| `e0414bab-4134-4bbb-997b-724cb66f0c89` | 1 | 3 | `FAILED / UPSTREAM_GRAPHQL_ERROR` | 0 |

最新任务在 `frame-ops-v1.0.41` 上验证了三次失败尝试的完整释放记录。每次均为：

```text
entry_type: RELEASE
credit_delta: 0
reserved_before: 800
reserved_after: 0
source: worker_submit_failure
```

第三次流水同时记录 `retryable=false`，任务结束为 `FAILED`，账号没有真实积分损失。故障发生在媒体已解析后、取得上游 generation ID 之前。当前浏览器账号 JWT 调用官方 REST 探针也被授权钩子拒绝，不能作为现有账号池的直接回退通道。

## 7. 接口问题清单

| 优先级 | 问题 | 状态 |
| --- | --- | --- |
| P0 | Provider 余额刷新后本地终态可能再次减积分 | 已修复并真实复验 |
| P1 | 提交前失败释放预留但缺少 `RELEASE` 流水 | 已修复并真实复验 |
| P1 | `reference-to-video` 内部 GraphQL 通道持续返回通用错误 | 待上游通道或认证方式调整 |
| P2 | 生产版本切换瞬间观察到短暂 502 | 客户端轮询需按 3–10 秒退避重试；健康检查恢复后原任务可继续查询 |

## 8. 自动化与发布验证

- Desktop：189 项测试通过。
- API：907 项测试通过。
- Ruff：全部检查通过。
- Web：Vite 生产构建通过，转换 2171 个模块。
- 生产 `frame-ops-api`、`frame-ops-worker`、`frame-ops-syncer` 均为 `active`。
- 公网 API `/health/ready` 和 Web `/console-health` 均通过。
- `frame-ops-v1.0.41` 发布记录：`/opt/frame-ops/deployments/frame-ops-v1.0.41-20260809T132246Z`。
- `frame-ops-v1.0.41` 的回滚基线检查已通过；后续生产发布继续沿用同一原子 Tag 与回滚机制。

## 9. 可复核工件

本机完整证据目录：

```text
/Users/m007/mul_key_chrome-leo/.secrets/production/20260809T124601Z-veo-audit
```

目录包含提交/终态 JSON、状态时间线、无效请求、幂等结果、生产只读积分流水、下载 MP4、`ffprobe` JSON、SHA-256 以及余额刷新复验记录。

参数依据：[Leonardo Veo 3.1 API 指南](https://docs.leonardo.ai/me/docs/veo-31)；变更依据：[Leonardo API Deprecations & Changes](https://docs.leonardo.ai/me/docs/deprecations-changes)。
