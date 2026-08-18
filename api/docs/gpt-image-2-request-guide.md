# GPT Image 2 API 请求指南

本文档对应 Leonardo 模型 **`gpt-image-2`**（界面名称 GPT Image 2），输入 schema 版本为 **`gpt-image-2.v1`**。服务支持文生图和 1–6 张参考图的图生图；输出固定为 1 张图片。

> 核验来源：2026-08-08 在 Leonardo AI Creation 页面逐档操作宽高比滑块，并逐项读取质量、Size、像素尺寸和 Generate 积分；图像参考上限与请求结构同时对照 Leonardo 官方 [GPT Image-1.5 v2 API 指南](https://docs.leonardo.ai/me/docs/gpt-image-1-5)。滑块 `aria-valuemin=0`、`aria-valuemax=9`，因此实际为 10 个档位；GPT Image 2 的尺寸与积分以本页浏览器实测值为准。

## 1. 接口与固定行为

- Base URL：`https://api-leo.clawsea.ai`
- 创建任务：`POST /v1/tasks`
- 查询任务：`GET /v1/tasks/{task_uuid}`
- 鉴权：`X-API-Key: YOUR_API_KEY`
- 幂等：创建请求必须携带唯一 `Idempotency-Key`（8–128 字符）

Worker 固定写入以下上游参数，业务请求不能覆盖：

| 上游字段 | 固定值 | 说明 |
| --- | --- | --- |
| `model` | `gpt-image-2` | 模型标识 |
| `parameters.prompt_enhance` | `OFF` | 提示词优化关闭 |
| `parameters.style_ids` | `556c1ee5-ec38-42e8-955a-1e82dad0ffa1` | Leonardo 的 `None` 风格 |
| `parameters.quantity` | `1` | 每任务只生成 1 张 |
| `guidances.image_reference[].strength` | `MID` | 图生图参考强度固定 |

请求中传入 `prompt_enhance`、`style`、`style_ids` 或 `quantity` 会返回 `422`，避免调用方绕过固定配置。

## 2. 创建任务公共结构

```json
{
  "provider": "leonardo",
  "task_type": "IMAGE_GENERATION",
  "model": "gpt-image-2",
  "mode": "text-to-image",
  "input": {
    "prompt": "A cobalt-blue paper airplane on a clean white studio background.",
    "quality": "MEDIUM",
    "aspect_ratio": "1:1",
    "size": "SMALL"
  }
}
```

### 顶层字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `provider` | string | 否 | 固定使用 `leonardo` |
| `task_type` | string | 是 | 固定 `IMAGE_GENERATION` |
| `model` | string | 是 | 固定 `gpt-image-2` |
| `mode` | string | 是 | `text-to-image` 或 `image-to-image` |
| `input` | object | 是 | 见下表 |
| `priority` | integer | 否 | `-100`–`100`，默认 `0` |
| `estimated_credit_cost` | integer | 否 | 调用方值会被服务端实测规则覆盖 |

### `input` 字段

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `prompt` | string | 是 | — | 1–7000 字符 |
| `quality` | enum | 否 | `MEDIUM` | `LOW` / `MEDIUM` / `HIGH` |
| `aspect_ratio` | enum | 否 | `1:1` | `21:9` / `16:9` / `3:2` / `4:3` / `5:4` / `1:1` / `4:5` / `3:4` / `2:3` / `9:16` |
| `size` | enum | 否 | `SMALL` | `SMALL` / `MEDIUM` / `LARGE` |
| `resolution` | string | 否 | 自动推导 | `WIDTHxHEIGHT`；若传入，必须与 `aspect_ratio + size` 对应 |
| `reference_image_urls` | URL[] | 图生图必填 | — | 1–6 个公网 HTTP(S) PNG/JPG/WEBP 地址 |

服务会把选定的 `aspect_ratio + size` 解析成精确 `resolution`，将该值写回任务 `input`，再向上游发送 `width` 与 `height`。

## 3. 尺寸与积分矩阵

以下积分均为 **1 张图片**。文生图和图生图加入参考图片后的 Generate 积分一致。

| 宽高比 | Size | 分辨率 | LOW | MEDIUM | HIGH |
| --- | --- | ---: | ---: | ---: | ---: |
| 21:9 | SMALL | 1584×672 | 8 | 66 | 263 |
| 21:9 | MEDIUM | 2048×864 | 13 | 110 | 436 |
| 21:9 | LARGE | 3808×1632 | 44 | 385 | 1530 |
| 16:9 | SMALL | 1376×768 | 8 | 66 | 261 |
| 16:9 | MEDIUM | 2048×1136 | 17 | 144 | 573 |
| 16:9 | LARGE | 3584×2016 | 51 | 447 | 1779 |
| 3:2 | SMALL | 1264×848 | 8 | 67 | 264 |
| 3:2 | MEDIUM | 2048×1376 | 20 | 175 | 694 |
| 3:2 | LARGE | 3504×2336 | 58 | 507 | 2015 |
| 4:3 | SMALL | 1200×896 | 8 | 67 | 265 |
| 4:3 | MEDIUM | 2048×1536 | 23 | 195 | 775 |
| 4:3 | LARGE | 3264×2448 | 56 | 495 | 1967 |
| 5:4 | SMALL | 1152×928 | 8 | 67 | 264 |
| 5:4 | MEDIUM | 2048×1648 | 24 | 209 | 831 |
| 5:4 | LARGE | 3200×2560 | 58 | 507 | 2017 |
| 1:1 | SMALL | 1024×1024 | 8 | 65 | 259 |
| 1:1 | MEDIUM | 2048×2048 | 30 | 260 | 1033 |
| 1:1 | LARGE | 2880×2880 | 59 | 513 | 2042 |
| 4:5 | SMALL | 928×1152 | 8 | 67 | 264 |
| 4:5 | MEDIUM | 1648×2048 | 24 | 209 | 831 |
| 4:5 | LARGE | 2560×3200 | 58 | 507 | 2017 |
| 3:4 | SMALL | 896×1200 | 8 | 67 | 265 |
| 3:4 | MEDIUM | 1536×2048 | 23 | 195 | 775 |
| 3:4 | LARGE | 2448×3264 | 56 | 495 | 1967 |
| 2:3 | SMALL | 848×1264 | 8 | 67 | 264 |
| 2:3 | MEDIUM | 1376×2048 | 20 | 175 | 694 |
| 2:3 | LARGE | 2336×3504 | 58 | 507 | 2015 |
| 9:16 | SMALL | 768×1376 | 8 | 66 | 261 |
| 9:16 | MEDIUM | 1136×2048 | 17 | 144 | 573 |
| 9:16 | LARGE | 2016×3584 | 51 | 447 | 1779 |

计费规则版本：`leonardo-ui-20260808.v5`。API 创建任务时写入 `estimated_credit_cost`；Worker 领取任务前会重新报价，只选择 `balance_credits - reserved_credits >= estimated_credit_cost` 的账号，并在上游提交前刷新一次真实余额。上游返回 `apiCreditCost` 时，它会覆盖预估并参与最终结算。

## 4. 文生图

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: gpt-image-2-t2i-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"IMAGE_GENERATION",
    "model":"gpt-image-2",
    "mode":"text-to-image",
    "input":{
      "prompt":"A cobalt-blue paper airplane on a clean white studio background.",
      "quality":"MEDIUM",
      "aspect_ratio":"1:1",
      "size":"SMALL"
    }
  }'
```

该请求解析为 `1024x1024`，预估积分为 `65`。

## 5. 图生图

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: gpt-image-2-i2i-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"IMAGE_GENERATION",
    "model":"gpt-image-2",
    "mode":"image-to-image",
    "input":{
      "prompt":"Keep the composition; replace the central object with a cobalt-blue paper airplane.",
      "quality":"HIGH",
      "aspect_ratio":"4:5",
      "size":"MEDIUM",
      "reference_image_urls":[
        "https://cdn.example.com/reference-1.png",
        "https://cdn.example.com/reference-2.jpg"
      ]
    }
  }'
```

Worker 会依次下载、探测并上传参考图，转换成 Leonardo `UPLOADED` 资产 ID，再提交 `guidances.image_reference`。示例分辨率为 `1648x2048`，预估积分为 `831`。

## 6. 创建响应

HTTP `202 Accepted`：

```json
{
  "task_uuid": "00000000-0000-0000-0000-000000000001",
  "provider": "leonardo",
  "task_type": "IMAGE_GENERATION",
  "model": "gpt-image-2",
  "mode": "text-to-image",
  "input_schema_version": "gpt-image-2.v1",
  "input": {
    "prompt": "A cobalt-blue paper airplane on a clean white studio background.",
    "quality": "MEDIUM",
    "aspect_ratio": "1:1",
    "size": "SMALL",
    "resolution": "1024x1024"
  },
  "status": "QUEUED",
  "estimated_credit_cost": 65,
  "reserved_credit_cost": 0,
  "actual_credit_cost": null,
  "progress": {"phase": "QUEUED", "resolved_assets": 0, "total_assets": 0}
}
```

图生图的 `progress.total_assets` 等于参考图数量，媒体处理阶段为 `RESOLVING_MEDIA`。

## 7. 查询与完成响应

```bash
curl 'https://api-leo.clawsea.ai/v1/tasks/TASK_UUID' \
  -H 'X-API-Key: YOUR_API_KEY'
```

终态 `COMPLETED`。`output.media[].type` 会按结果 URL 扩展名返回实际 MIME；Leonardo 当前 GPT Image 2 实测结果为 JPEG：

```json
{
  "task_uuid": "00000000-0000-0000-0000-000000000001",
  "upstream_task_id": "UPSTREAM_GENERATION_ID",
  "status": "COMPLETED",
  "estimated_credit_cost": 65,
  "reserved_credit_cost": 0,
  "actual_credit_cost": 65,
  "progress": {"phase": "COMPLETED", "resolved_assets": 0, "total_assets": 0},
  "output": {
    "provider": "leonardo",
    "generation_id": "UPSTREAM_GENERATION_ID",
    "media": [
      {
        "id": "UPSTREAM_IMAGE_ID",
        "type": "image/jpeg",
        "url": "https://cdn.example.com/result.jpg",
        "width": 1024,
        "height": 1024
      }
    ]
  }
}
```

## 8. 状态与错误

状态通常按以下顺序流转：

`QUEUED → CLAIMED → RESOLVING_MEDIA → SUBMITTING → UPSTREAM_QUEUED → RUNNING → COMPLETED`

文生图虽然没有媒体输入，也会快速经过 `RESOLVING_MEDIA` 以统一构建类型化请求。常见错误：

| 错误 | 说明 |
| --- | --- |
| `422` | 模式、字段、枚举、参考图数量或分辨率组合不合法 |
| `409 IDEMPOTENCY_CONFLICT` | 同一幂等键用于不同请求体 |
| `MEDIA_URL_NOT_PUBLIC` | 参考图 URL 未解析到公网地址 |
| `MEDIA_TYPE_MISMATCH` | URL 不是有效 PNG/JPG/WEBP |
| `MEDIA_MODERATION_REJECTED` | 上游拒绝参考图 |
| `ACCOUNT_UNAVAILABLE` | 没有可用余额覆盖预算的账号 |
| `UPSTREAM_*` | 上游鉴权、限流、提交或生成失败 |
## 9. 完整冒泡测试

仓库提供可重复运行的测试器 `apps/api/scripts/smoke_gpt_image_2.py`。默认只执行本地契约矩阵，不提交付费任务：

```bash
PYTHONPATH=apps/api/src python3.11 apps/api/scripts/smoke_gpt_image_2.py \
  --output-dir artifacts/gpt-image-2-contract
```

契约矩阵覆盖 `2 模式 × 3 质量 × 10 比例 × 3 Size = 180` 个组合，逐项检查自动分辨率、上游 width/height、定价、固定 `quantity=1`、`prompt_enhance=OFF`、Style None 及图生图参考结构。

生产真实冒泡矩阵同时覆盖两种模式中的全部质量和 Size，并让 10 种比例至少出现一次。默认代表性矩阵预算为 1012 积分，`--max-credits` 会在提交前限制总预算：

```bash
set -a
source .secrets/production/20260806T011708Z/access.env
set +a
PYTHONPATH=apps/api/src python3.11 apps/api/scripts/smoke_gpt_image_2.py \
  --live \
  --base-url https://api-leo.clawsea.ai \
  --output-dir .secrets/verification/gpt-image-2-smoke \
  --max-credits 1100
```

报告会生成 `report.md`、`report.json`、每个任务的创建/状态/最终响应以及下载的真实图片。每个真实案例必须同时满足：创建与最终输入分辨率正确、输出元数据尺寸正确、下载像素正确、MIME 一致、预估/预留/实际积分全部等于定价矩阵。
