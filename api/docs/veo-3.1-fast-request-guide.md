# Veo 3.1 Fast API 请求指南

FRAME OPS 通过 Leonardo 账号池提交 `veo-3.1-fast-generate-001` 视频任务。线上基址：

```text
https://api-leo.clawsea.ai
```

## 能力摘要

| 项目 | 值 |
| --- | --- |
| `model` | `veo-3.1-fast-generate-001` |
| `task_type` | `VIDEO_GENERATION` |
| 模式 | `text-to-video`、`image-to-video` |
| 时长 | `4`、`6`、`8` 秒 |
| 分辨率 | `720P`、`1080P`、`4K` |
| 比例 | `16:9`、`9:16` |
| 音频 | `audio=true/false` |
| 首尾帧 | 首帧必填、尾帧可选；尾帧不可脱离首帧使用 |
| 图片参考 | Fast 不支持 `image_reference` / `reference-to-video` |
| 数量 | 固定 `1` |
| 可见性 | 固定 `public=false` |
| schema | `veo-3.1-fast.v1` |

## 创建任务

```text
POST /v1/tasks
X-API-Key: YOUR_API_KEY
Idempotency-Key: 8_TO_128_CHARS
Content-Type: application/json
```

顶层字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `provider` | string | 否 | 固定使用 `leonardo` |
| `task_type` | string | 是 | 固定 `VIDEO_GENERATION` |
| `model` | string | 是 | 固定 `veo-3.1-fast-generate-001` |
| `mode` | string | 是 | `text-to-video` 或 `image-to-video` |
| `input` | object | 是 | 下表中的模型参数 |
| `priority` | integer | 否 | `-100` 到 `100`，默认 `0` |

`input` 字段：

| 字段 | 类型 | 必填 | 默认值 | 约束与映射 |
| --- | --- | --- | --- | --- |
| `prompt` | string | 是 | — | 1–9999 字符，映射 `parameters.prompt` |
| `duration` | integer | 否 | `8` | `4` / `6` / `8` |
| `resolution` | string | 否 | `720P` | `720P` / `1080P` / `4K` |
| `aspect_ratio` | string | 否 | `16:9` | `16:9` / `9:16` |
| `audio` | boolean | 否 | `true` | 映射 `parameters.motion_has_audio` |
| `negative_prompt` | string | 否 | — | 最长 1000 字符 |
| `seed` | integer | 否 | — | 0–4294967295 |
| `image_url` | URL | 图生必填 | — | 首帧公网 HTTP(S) URL |
| `end_image_url` | URL | 否 | — | 尾帧公网 HTTP(S) URL，仅图生模式有效 |

调用方不传 `public`、`quantity`、`width`、`height`、`guidances` 或 Leonardo 媒体 ID。Worker 固定写入 `public=false` 和 `quantity=1`，并在账号分配后把网络图片转换为 Leonardo 资产 ID。

## 尺寸矩阵

| 分辨率 | `16:9` | `9:16` |
| --- | ---: | ---: |
| `720P` | 1280×720 | 720×1280 |
| `1080P` | 1920×1080 | 1080×1920 |
| `4K` | 3840×2160 | 2160×3840 |

## 文生视频请求

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: veo31-fast-t2v-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"VIDEO_GENERATION",
    "model":"veo-3.1-fast-generate-001",
    "mode":"text-to-video",
    "input":{
      "prompt":"A white paper boat crosses a quiet lake at sunrise.",
      "duration":4,
      "resolution":"720P",
      "aspect_ratio":"16:9",
      "audio":false
    }
  }'
```

## 首尾帧生视频请求

```json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "veo-3.1-fast-generate-001",
  "mode": "image-to-video",
  "input": {
    "prompt": "Move smoothly from the first frame to the final frame.",
    "duration": 4,
    "resolution": "1080P",
    "aspect_ratio": "9:16",
    "audio": true,
    "image_url": "https://cdn.example.com/start.png",
    "end_image_url": "https://cdn.example.com/end.png"
  }
}
```

Worker 提交给 Leonardo 的核心结构：

```json
{
  "model": "veo-3.1-fast-generate-001",
  "public": false,
  "parameters": {
    "prompt": "Move smoothly from the first frame to the final frame.",
    "duration": 4,
    "motion_has_audio": true,
    "quantity": 1,
    "width": 1080,
    "height": 1920,
    "guidances": {
      "start_frame": [{"image": {"id": "LEONARDO_START_ID", "type": "UPLOADED"}}],
      "end_frame": [{"image": {"id": "LEONARDO_END_ID", "type": "UPLOADED"}}]
    }
  }
}
```

## 创建响应

成功创建返回 HTTP `202`。服务端会忽略调用方伪造的预算并按定价规则重新计算：

```json
{
  "task_uuid": "TASK_UUID",
  "model": "veo-3.1-fast-generate-001",
  "mode": "text-to-video",
  "input_schema_version": "veo-3.1-fast.v1",
  "status": "QUEUED",
  "progress": {
    "phase": "QUEUED",
    "resolved_assets": 0,
    "total_assets": 0
  },
  "estimated_credit_cost": 400,
  "reserved_credit_cost": 0,
  "actual_credit_cost": null
}
```

## 查询响应

```bash
curl 'https://api-leo.clawsea.ai/v1/tasks/TASK_UUID' \
  -H 'X-API-Key: YOUR_API_KEY'
```

成功终态示例：

```json
{
  "task_uuid": "TASK_UUID",
  "upstream_task_id": "LEONARDO_GENERATION_ID",
  "model": "veo-3.1-fast-generate-001",
  "mode": "text-to-video",
  "input_schema_version": "veo-3.1-fast.v1",
  "status": "COMPLETED",
  "estimated_credit_cost": 400,
  "reserved_credit_cost": 0,
  "actual_credit_cost": 400,
  "output": {
    "provider": "leonardo",
    "generation_id": "LEONARDO_GENERATION_ID",
    "media": [
      {
        "type": "video/mp4",
        "width": 1280,
        "height": 720,
        "url": "https://cdn.example.com/result.mp4"
      }
    ]
  }
}
```

任务状态依次可能为 `QUEUED`、`RESOLVING_MEDIA`、`SUBMITTING`、`RUNNING`，终态为 `COMPLETED`、`FAILED` 或 `CANCELLED`。

## 积分预算

以下为 Leonardo Generate 按钮逐项实测值；`16:9` 与 `9:16` 预算相同。

| 音频 | 时长 | 720P | 1080P | 4K |
| --- | ---: | ---: | ---: | ---: |
| 关闭 | 4 秒 | 400 | 400 | 1200 |
| 关闭 | 6 秒 | 600 | 600 | 1800 |
| 关闭 | 8 秒 | 800 | 800 | 2400 |
| 开启 | 4 秒 | 600 | 600 | 1400 |
| 开启 | 6 秒 | 900 | 900 | 2100 |
| 开启 | 8 秒 | 1200 | 1200 | 2800 |

Worker 领取任务前重新报价，只选择 `balance_credits - reserved_credits >= estimated_credit_cost` 且并发可用的活跃账号，并原子预留预算。上游返回 `apiCreditCost` 时修正预留，任务终态写入 `actual_credit_cost` 和积分流水。

## 校验与错误

| 错误 | 含义 |
| --- | --- |
| HTTP 422 | 模式、字段、URL、时长、尺寸或字段范围校验失败 |
| HTTP 409 | 同一个幂等键对应了不同请求体 |
| `WAITING_ACCOUNT` | 当前没有余额及并发都满足预算的账号 |
| `MEDIA_*` | 首尾帧下载、探测或上传失败 |
| `UPSTREAM_*` / `PROVIDER_*` | Leonardo 提交、生成、审核或输出失败 |

Fast 明确拒绝 `reference-to-video`，避免把仅标准版支持的 `image_reference` 错发给上游。

官方参数依据：[Leonardo Veo 3.1 API 指南](https://docs.leonardo.ai/me/docs/veo-31)。
