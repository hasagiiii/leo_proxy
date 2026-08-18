# Nano Banana 2 / Nano Banana Pro 接入方案

> 日期：2026-08-08

> 基线：FRAME OPS 图片任务链路

> 结论：生产真实请求确认两款模型均支持文生图和 1–6 张参考图片的图生图；不对外暴露共享界面中未被模型上游接受的视频参考入口。

## 1. 模型与固定参数

| 公共模型 ID | Leonardo 上游 ID | 支持模式 |
| --- | --- | --- |
| `nano-banana-2` | `nano-banana-2` | `text-to-image`、`image-to-image` |
| `nano-banana-pro` | `gemini-image-2` | `text-to-image`、`image-to-image` |

服务端固定：

- `quantity=1`
- `prompt_enhance=OFF`
- Style None（`556c1ee5-ec38-42e8-955a-1e82dad0ffa1`）
- `public=false`
- 图片参考强度 `MID`

客户端传入 `quantity`、`quality`、`prompt_enhance`、`style`、`style_ids`、`guidances` 或 `public` 时返回 422。图生图接收 1–6 个公网 HTTP(S) 图片 URL；文生图不接收参考媒体。

## 2. 尺寸矩阵

`resolution` 由 `aspect_ratio + size` 唯一确定。显式传入时必须与矩阵一致。

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

## 3. 积分与账号选择

定价规则版本：`leonardo-ui-20260808.v5`。积分只由模型与 Size 决定，不随比例或模式变化：

| 模型 | SMALL | MEDIUM | LARGE |
| --- | ---: | ---: | ---: |
| Nano Banana 2 | 80 | 120 | 160 |
| Nano Banana Pro | 140 | 140 | 250 |

扣费链路：

1. 创建任务时写入当前 `estimated_credit_cost`。
2. Worker 领取前重新报价。
3. 只锁定 `balance_credits - reserved_credits >= estimated_credit_cost` 且有并发槽位的账号。
4. 分配时原子预留积分；上游返回 `apiCreditCost` 后修正预留。
5. 完成时写入 `actual_credit_cost` 和积分账本，失败时释放预留。

## 4. 请求合约

### 文生图

```json
{
  "provider": "leonardo",
  "task_type": "IMAGE_GENERATION",
  "model": "nano-banana-2",
  "mode": "text-to-image",
  "input": {
    "prompt": "A premium product photo on a clean studio background.",
    "aspect_ratio": "16:9",
    "size": "MEDIUM"
  }
}
```

规范化结果：`resolution=2752x1536`，预估积分 `120`。

### 图生图

```json
{
  "provider": "leonardo",
  "task_type": "IMAGE_GENERATION",
  "model": "nano-banana-pro",
  "mode": "image-to-image",
  "input": {
    "prompt": "Keep the product identity and redesign the scene as a luxury campaign.",
    "aspect_ratio": "4:5",
    "size": "LARGE",
    "reference_image_urls": [
      "https://cdn.example.com/product-front.png",
      "https://cdn.example.com/style-reference.jpg"
    ]
  }
}
```

规范化结果：`resolution=3712x4608`，预估积分 `250`。

## 5. 上游映射

`src/video_task_service/nano_images.py` 将公共模型 ID 与 Leonardo ID 分离，并生成统一 `Generate` 请求。图片参考被媒体解析器下载、探测、上传，再转换为：

```json
{
  "image_reference": [
    {
      "image": {"id": "UPSTREAM_IMAGE_ID", "type": "UPLOADED"},
      "strength": "MID"
    }
  ]
}
```

上游请求始终包含精确 `width`、`height`、固定字段和可选图片 guidance。

## 6. 自动化验收

契约冒烟覆盖：

- `2 模型 × 2 模式 × 10 比例 × 3 Size = 120` 个合法组合；
- 30 组尺寸、60 组定价；
- 固定参数、错误模式、错误分辨率和参考数量的拒绝用例；
- `public=false`、Prompt Enhance Off、Style None、数量 1 和上游模型映射。

生产真实冒烟覆盖 12 个代表性任务：每个模型的文生图/图生图、三个 Size 和全部 10 种比例。计划积分 `1780`。每项必须验证：

- API 规范化输入分辨率；
- 输出响应宽高；
- 下载图片真实像素；
- 预估、预留、上游和实际积分；
- 任务终态及参考媒体解析进度。

执行：

```bash
PYTHONPATH=apps/api/src python3 apps/api/scripts/smoke_nano_images.py \
  --live \
  --environment production-leonardo \
  --base-url https://api-leo.clawsea.ai \
  --api-key YOUR_API_KEY \
  --output-dir .secrets/verification/nano-image-live \
  --max-credits 1800
```

本地 Compose 默认 `VIDEO_SERVICE_UPSTREAM_MODE=mock`，只能证明服务链路；模型上线结论以生产 `leonardo` 模式的真实任务报告为准。

## 7. 发布与回滚

发布必须包含 API、Worker、Syncer 和 Web 文档，并验证公网健康、OpenAPI schema、120 组契约及 12 个真实任务。发布器会保留上一 Tag；使用 `/opt/frame-ops/latest-deployment/rollback.sh --check` 验证回滚目标和命令。
