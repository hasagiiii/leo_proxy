# Nano Banana 2 / Nano Banana Pro API 请求指南

本文档对应 Leonardo 图片模型 **Nano Banana 2** 与 **Nano Banana Pro**。公共 API 模型 ID 分别为 `nano-banana-2`、`nano-banana-pro`，输入 schema 版本统一为 `nano-image.v1`，每个任务固定输出 1 张图片。

> 参数来源：2026-08-08 在 Leonardo AI Creation 页面逐档读取 10 个宽高比、3 个 Size、Generate 积分与参考媒体能力，并对照 Leonardo 官方 [Nano Banana 2](https://docs.leonardo.ai/docs/nano-banana-2) 和 [Nano Banana Pro](https://docs.leonardo.ai/me/docs/nano-banana-pro) 接入说明。生产调用请以本文档的公共模型 ID 和字段约束为准。

## 1. 接口、鉴权与固定行为

- Base URL：`https://api-leo.clawsea.ai`
- 创建任务：`POST /v1/tasks`
- 查询任务：`GET /v1/tasks/{task_uuid}`
- 鉴权：`X-API-Key: YOUR_API_KEY`
- 幂等：创建请求必须携带唯一 `Idempotency-Key`（8–128 字符）

Worker 会固定以下上游字段，业务请求不能覆盖：

| 上游字段 | 固定值 | 说明 |
| --- | --- | --- |
| Nano 2 `model` | `nano-banana-2` | Leonardo 上游模型 ID |
| Nano Pro `model` | `gemini-image-2` | Leonardo 上游模型 ID |
| `public` | `false` | 所有任务固定为非公开 |
| `parameters.prompt_enhance` | `OFF` | 提示词优化关闭 |
| `parameters.style_ids` | `556c1ee5-ec38-42e8-955a-1e82dad0ffa1` | Leonardo 的 `None` 风格 |
| `parameters.quantity` | `1` | 每个任务只生成 1 张 |
| `guidances.image_reference[].strength` | `MID` | 图片参考强度固定 |

请求中传入 `public`、`prompt_enhance`、`style`、`style_ids`、`quantity`、`quality` 或原始 `guidances` 会返回 HTTP `422`，避免覆盖服务端固定参数。

## 2. 模型与模式

| 公共模型 ID | 文生图 | 图生图 |
| --- | --- | --- |
| `nano-banana-2` | `text-to-image` | `image-to-image`，1–6 张图片 |
| `nano-banana-pro` | `text-to-image` | `image-to-image`，1–6 张图片 |

参考媒体必须是 API Worker 能访问的公网 HTTP(S) URL。Worker 会下载、校验并上传为 Leonardo `UPLOADED` 资产，再按调用顺序组装 guidance。

## 3. 创建任务公共结构

```json
{
  "provider": "leonardo",
  "task_type": "IMAGE_GENERATION",
  "model": "nano-banana-2",
  "mode": "text-to-image",
  "input": {
    "prompt": "A cobalt-blue paper airplane on a clean white studio background.",
    "aspect_ratio": "1:1",
    "size": "SMALL"
  }
}
```

### 顶层字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `provider` | string | 否 | 默认并固定使用 `leonardo` |
| `task_type` | string | 是 | 固定 `IMAGE_GENERATION` |
| `model` | enum | 是 | `nano-banana-2` / `nano-banana-pro` |
| `mode` | enum | 是 | 见“模型与模式”表 |
| `input` | object | 是 | 业务输入，见下表 |
| `priority` | integer | 否 | `-100`–`100`，默认 `0` |
| `estimated_credit_cost` | integer | 否 | 调用方传值会被服务端当前定价规则覆盖 |

### `input` 字段

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `prompt` | string | 是 | — | 1–7000 字符 |
| `aspect_ratio` | enum | 否 | `1:1` | `21:9` / `16:9` / `3:2` / `4:3` / `5:4` / `1:1` / `4:5` / `3:4` / `2:3` / `9:16` |
| `size` | enum | 否 | `SMALL` | `SMALL` / `MEDIUM` / `LARGE` |
| `resolution` | string | 否 | 自动推导 | `WIDTHxHEIGHT`；传入时必须与 `aspect_ratio + size` 完全一致 |
| `reference_image_urls` | URL[] | 图生图必填 | — | 1–6 个有序图片 URL |

API 会把选定的 `aspect_ratio + size` 转换为精确 `resolution` 并写回任务输入；上游请求使用对应 `width`、`height`。

## 4. 10 种比例与 30 组分辨率

两款模型使用同一尺寸矩阵：

| 宽高比 | SMALL | MEDIUM | LARGE |
| --- | ---: | ---: | ---: |
| `21:9` | 1584×672 | 3168×1344 | 6336×2688 |
| `16:9` | 1376×768 | 2752×1536 | 5504×3072 |
| `3:2` | 1264×848 | 2528×1696 | 5056×3392 |
| `4:3` | 1200×896 | 2400×1792 | 4800×3584 |
| `5:4` | 1152×928 | 2304×1856 | 4608×3712 |
| `1:1` | 1024×1024 | 2048×2048 | 4096×4096 |
| `4:5` | 928×1152 | 1856×2304 | 3712×4608 |
| `3:4` | 896×1200 | 1792×2400 | 3584×4800 |
| `2:3` | 848×1264 | 1696×2528 | 3392×5056 |
| `9:16` | 768×1376 | 1536×2752 | 3072×5504 |

## 5. 积分规则

以下积分均为数量 1。当前 Leonardo UI 实测中，积分由**模型 + Size**决定，不随宽高比或文生图/图生图模式变化：

| 模型 | SMALL | MEDIUM | LARGE |
| --- | ---: | ---: | ---: |
| `nano-banana-2` | 80 | 120 | 160 |
| `nano-banana-pro` | 140 | 140 | 250 |

计费规则版本为 `leonardo-ui-20260808.v5`：

1. API 创建任务时根据模型与 Size 写入 `estimated_credit_cost`。
2. Worker 领取任务前按当前规则重新报价。
3. 账号选择只接受 `balance_credits - reserved_credits >= estimated_credit_cost` 的活跃账号。
4. 分配成功后在同一事务内预留积分，并在上游提交前刷新真实余额。
5. 上游返回 `apiCreditCost` 时更新预留；完成后写入 `actual_credit_cost` 与账号积分流水。

## 6. 文生图请求

Nano Banana 2：

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: nano2-t2i-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"IMAGE_GENERATION",
    "model":"nano-banana-2",
    "mode":"text-to-image",
    "input":{
      "prompt":"A cobalt-blue paper airplane on a clean white studio background.",
      "aspect_ratio":"16:9",
      "size":"MEDIUM"
    }
  }'
```

该请求解析为 `2752x1536`，预估 `120` 积分。

Nano Banana Pro 只需把 `model` 改为 `nano-banana-pro`；同一 Size 的预估为 `140` 积分。

## 7. 图生图请求

两款模型都支持 1–6 张有序参考图片：

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: nanopro-i2i-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"IMAGE_GENERATION",
    "model":"nano-banana-pro",
    "mode":"image-to-image",
    "input":{
      "prompt":"Keep the composition and replace the central object with a cobalt-blue airplane.",
      "aspect_ratio":"4:5",
      "size":"LARGE",
      "reference_image_urls":[
        "https://cdn.example.com/reference-1.png",
        "https://cdn.example.com/reference-2.jpg"
      ]
    }
  }'
```

该请求解析为 `3712x4608`，预估 `250` 积分。

## 8. 创建响应

HTTP `202 Accepted`：

```json
{
  "task_uuid": "00000000-0000-0000-0000-000000000001",
  "provider": "leonardo",
  "task_type": "IMAGE_GENERATION",
  "model": "nano-banana-2",
  "mode": "text-to-image",
  "input_schema_version": "nano-image.v1",
  "input": {
    "prompt": "A cobalt-blue paper airplane on a clean white studio background.",
    "aspect_ratio": "16:9",
    "size": "MEDIUM",
    "resolution": "2752x1536"
  },
  "status": "QUEUED",
  "estimated_credit_cost": 120,
  "reserved_credit_cost": 0,
  "actual_credit_cost": null,
  "progress": {"phase": "QUEUED", "resolved_assets": 0, "total_assets": 0}
}
```

## 9. 查询与完成响应

```bash
curl 'https://api-leo.clawsea.ai/v1/tasks/TASK_UUID' \
  -H 'X-API-Key: YOUR_API_KEY'
```

终态响应示例：

```json
{
  "task_uuid": "00000000-0000-0000-0000-000000000001",
  "upstream_task_id": "UPSTREAM_GENERATION_ID",
  "status": "COMPLETED",
  "estimated_credit_cost": 120,
  "reserved_credit_cost": 0,
  "actual_credit_cost": 120,
  "progress": {"phase": "COMPLETED", "resolved_assets": 0, "total_assets": 0},
  "output": {
    "provider": "leonardo",
    "generation_id": "UPSTREAM_GENERATION_ID",
    "media": [
      {
        "id": "UPSTREAM_IMAGE_ID",
        "type": "image/jpeg",
        "url": "https://cdn.example.com/result.jpg",
        "width": 2752,
        "height": 1536
      }
    ]
  }
}
```

## 10. 状态与错误

典型状态流转：

`QUEUED → CLAIMED → RESOLVING_MEDIA → SUBMITTING → UPSTREAM_QUEUED → RUNNING → COMPLETED`

常见错误：

| 错误 | 说明 |
| --- | --- |
| HTTP `422` | 模型、模式、固定字段、参考数量或分辨率组合不合法 |
| HTTP `409` / `IDEMPOTENCY_CONFLICT` | 同一幂等键用于不同请求体 |
| `MEDIA_URL_NOT_PUBLIC` | 参考媒体不是可访问的公网 URL |
| `MEDIA_TYPE_MISMATCH` | 参考媒体类型不符合字段要求 |
| `MEDIA_MODERATION_REJECTED` | 上游拒绝参考媒体 |
| `ACCOUNT_UNAVAILABLE` | 当前无账号的可用积分覆盖任务预算 |
| `UPSTREAM_*` | 上游鉴权、限流、提交或生成错误 |

失败任务的 `reserved_credit_cost` 会释放；已完成任务按 `actual_credit_cost` 结算并记录积分流水。

## 11. 完整冒烟测试

默认执行**不扣费**的本地契约矩阵：

```bash
docker compose run --rm api python scripts/smoke_nano_images.py \
  --output-dir /tmp/nano-image-contract
```

契约矩阵共 120 组：

- Nano 2：`2 模式 × 10 比例 × 3 Size = 60`。
- Nano Pro：`2 模式 × 10 比例 × 3 Size = 60`。
- 每组检查输入 `resolution`、上游 `width/height`、积分报价、模型映射、`public=false`、`quantity=1`、Prompt Enhance Off、Style None 与参考结构。
- 另有 9 个非法固定字段/模式用例，必须全部被校验拒绝。

生产真实冒烟矩阵为 12 个代表性任务，覆盖两款模型的两种模式、三个 Size 与全部 10 种比例，计划积分为 1780。脚本提交后会下载真实图片，检查输入尺寸、响应元数据尺寸、下载像素及预估/预留/实际积分：

```bash
PYTHONPATH=apps/api/src python3 apps/api/scripts/smoke_nano_images.py \
  --live \
  --environment production-leonardo \
  --base-url https://api-leo.clawsea.ai \
  --api-key YOUR_API_KEY \
  --output-dir .secrets/verification/nano-image-live \
  --max-credits 1800
```

输出包括 `report.md`、`report.json`、`responses/` 和 `media/`。任一参数、尺寸或积分检查不一致时，脚本以非零状态退出。

本次生产 Leonardo 真实任务的已归档报告见 [`nano-image-smoke-report.md`](nano-image-smoke-report.md)。
