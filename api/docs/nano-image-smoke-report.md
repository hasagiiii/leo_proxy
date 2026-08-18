# Nano Banana 2 / Nano Banana Pro 冒烟测试报告

- Run ID：`20260807T235350Z-9d529f83`
- 时间：`2026-08-07T23:53:50.195476+00:00` → `2026-08-07T23:59:52.449813+00:00`
- API：`https://api-leo.clawsea.ai`
- 环境：`production-leonardo`
- 定价版本：`leonardo-ui-20260808.v5`
- 总结果：**PASS**

## 1. 契约矩阵

验证 `120` 个组合：Nano 2 两模式 60 组 + Nano Pro 两模式 60 组。
通过 `120`，失败 `0`。

| 固定/冲突字段 | 预期 | 结果 |
| --- | --- | --- |
| `quality` | 422/校验错误 | PASS |
| `prompt_enhance` | 422/校验错误 | PASS |
| `style` | 422/校验错误 | PASS |
| `style_ids` | 422/校验错误 | PASS |
| `quantity` | 422/校验错误 | PASS |
| `guidances` | 422/校验错误 | PASS |
| `public` | 422/校验错误 | PASS |
| `resolution` | 422/校验错误 | PASS |
| `nano-video-reference` | 422/校验错误 | PASS |

## 2. 端到端任务矩阵

计划积分：`1780`；实际积分：`1780`。

| 案例 | 模型 | 模式 | 比例 | Size | 输入分辨率 | 输出元数据 | 下载实测 | 预估/预留/实际 | 状态 | 结果 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `nano2-t2i-small-square` | `nano-banana-2` | `text-to-image` | `1:1` | `SMALL` | `1024x1024` | `1024x1024` | `1024x1024` | `80/80/80` | `COMPLETED` | PASS |
| `nano2-t2i-medium-wide` | `nano-banana-2` | `text-to-image` | `16:9` | `MEDIUM` | `2752x1536` | `2752x1536` | `2752x1536` | `120/120/120` | `COMPLETED` | PASS |
| `nano2-t2i-large-landscape` | `nano-banana-2` | `text-to-image` | `3:2` | `LARGE` | `5056x3392` | `5056x3392` | `5056x3392` | `160/160/160` | `COMPLETED` | PASS |
| `nano2-i2i-small-classic` | `nano-banana-2` | `image-to-image` | `4:3` | `SMALL` | `1200x896` | `1200x896` | `1200x896` | `80/80/80` | `COMPLETED` | PASS |
| `nano2-i2i-medium-classic` | `nano-banana-2` | `image-to-image` | `5:4` | `MEDIUM` | `2304x1856` | `2304x1856` | `2304x1856` | `120/120/120` | `COMPLETED` | PASS |
| `nano2-i2i-large-portrait` | `nano-banana-2` | `image-to-image` | `4:5` | `LARGE` | `3712x4608` | `3712x4608` | `3712x4608` | `160/160/160` | `COMPLETED` | PASS |
| `nanopro-t2i-small-portrait` | `nano-banana-pro` | `text-to-image` | `3:4` | `SMALL` | `896x1200` | `896x1200` | `896x1200` | `140/140/140` | `COMPLETED` | PASS |
| `nanopro-t2i-medium-portrait` | `nano-banana-pro` | `text-to-image` | `2:3` | `MEDIUM` | `1696x2528` | `1696x2528` | `1696x2528` | `140/140/140` | `COMPLETED` | PASS |
| `nanopro-t2i-large-vertical` | `nano-banana-pro` | `text-to-image` | `9:16` | `LARGE` | `3072x5504` | `3072x5504` | `3072x5504` | `250/250/250` | `COMPLETED` | PASS |
| `nanopro-i2i-small-ultrawide` | `nano-banana-pro` | `image-to-image` | `21:9` | `SMALL` | `1584x672` | `1584x672` | `1584x672` | `140/140/140` | `COMPLETED` | PASS |
| `nanopro-i2i-medium-square` | `nano-banana-pro` | `image-to-image` | `1:1` | `MEDIUM` | `2048x2048` | `2048x2048` | `2048x2048` | `140/140/140` | `COMPLETED` | PASS |
| `nanopro-i2i-large-wide` | `nano-banana-pro` | `image-to-image` | `16:9` | `LARGE` | `5504x3072` | `5504x3072` | `5504x3072` | `250/250/250` | `COMPLETED` | PASS |

## 3. 覆盖与证据

- 模型：`nano-banana-2, nano-banana-pro`
- 模式：`image-to-image, text-to-image`
- Size：`LARGE, MEDIUM, SMALL`
- 比例：`16:9, 1:1, 21:9, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16`
- `report.json`：完整结构化检查结果
- `responses/`：创建、状态与最终响应
- `media/`：真实任务下载结果

## 4. 生产积分账本复核

对上述 12 个 `task_uuid` 在生产数据库执行只读复核：

- `SETTLE` 账本：12/12 条。
- `credit_delta` 合计：`-1780`。
- `actual_credit_cost` 合计：`1780`。
- 每项 `estimated_credit_cost = reserved_credit_cost = actual_credit_cost = -credit_delta`：全部成立。

## 5. 发布信息

- 能力版本：`frame-ops-v1.0.11`。
- Commit：`4ea539240c8bbb3e357409be8b65c01d02c3fff4`。
- Worker 上游模式：`leonardo`。
- 生产健康：API、Web、Worker、Syncer 全部通过。
