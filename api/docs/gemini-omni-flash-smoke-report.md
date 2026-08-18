# Gemini Omni Flash 生产真实冒烟测试报告

## 结论

- 能力版本：`frame-ops-v1.0.28`
- 生产 API：`https://api-leo.clawsea.ai`
- 上游模式：`leonardo`
- 定价规则：`leonardo-ui-20260809.v9`
- 契约矩阵：`32/32` 通过
- 真实任务：`2/2` 完成并通过全部断言
- 计划积分：`600`
- 实际积分：`600`
- 积分流水：两条 `SETTLE`，合计 `credit_delta=-600`

测试覆盖两种公开模式、两个比例、媒体转换、真实文件下载、输出尺寸、时长和积分结算。

## 真实任务

| 用例 | 模式 | 比例 | 时长 | 内部任务 ID | 上游 Generation ID | 参考媒体 | 预估/预留/实际 | 结果 |
| --- | --- | --- | ---: | --- | --- | ---: | --- | --- |
| 文生横屏 | `text-to-video` | `16:9` | 3 秒 | `8c16f6b6-39cd-4723-ad94-9c6346e0d4f7` | `1f1937c5-e503-6aa0-b709-8f70b72859e6` | 0/0 | `300/300/300` | PASS |
| 单图 Omni 竖屏 | `reference-to-video` | `9:16` | 3 秒 | `06ca3529-14d5-4db7-a0c4-1d3bb0c91c67` | `1f1937c7-922a-6f20-bcd9-9bffba7e2d74` | 1/1 | `300/300/300` | PASS |

两个任务均经历 `QUEUED → RUNNING → COMPLETED`，返回 `input_schema_version=gemini-omni-flash.v1`。

## 文件流核验

| 用例 | API/预期尺寸 | 下载文件尺寸 | 编码 | MIME | 时长 | 音频流 | 文件大小 |
| --- | --- | --- | --- | --- | ---: | ---: | ---: |
| 文生横屏 | 1280×720 | 1280×720 | H.264 | `video/mp4` | 3.008 秒 | 1 | 807,183 bytes |
| 单图 Omni 竖屏 | 720×1280 | 720×1280 | H.264 | `video/mp4` | 3.008 秒 | 1 | 789,022 bytes |

输出元数据尺寸、下载后的真实视频流尺寸与输入选择完全一致。

## 积分流水核验

只读查询 `tasks` 与 `account_credit_ledger` 得到：

| 任务 | entry_type | credit_delta | provider_reported | 规则版本 |
| --- | --- | ---: | --- | --- |
| `8c16f6b6-39cd-4723-ad94-9c6346e0d4f7` | `SETTLE` | -300 | `true` | `leonardo-ui-20260809.v9` |
| `06ca3529-14d5-4db7-a0c4-1d3bb0c91c67` | `SETTLE` | -300 | `true` | `leonardo-ui-20260809.v9` |

每条流水的 `estimated_credit_cost`、`reserved_credit_cost`、`actual_credit_cost` 均为 `300`。这同时验证了任务预算、余额覆盖筛选、事务预留、上游回报校正和最终结算链路。

## 已执行检查

- Desktop：189/189 通过。
- API：587/587 通过。
- Ruff：通过。
- Web：`npm ci` 与生产构建通过。
- 发布：API、Worker、Syncer 均为 `active`。
- 健康检查：公网 API `ready`，Web `ok`。
- 回滚检查：目标 `frame-ops-v1.0.27` 可执行。

服务器原始 JSON、Markdown 与两个 MP4 位于：

```text
/opt/frame-ops/deployments/frame-ops-v1.0.28-20260808T225520Z/gemini-omni-flash-live/
```
