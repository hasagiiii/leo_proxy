# Kling Video O3 Omni API 请求指南

本文档对应 Leonardo 模型 **`kling-video-o-3`**（界面名称 Kling Video O3 Omni），输入 schema 版本为 `kling-o3.v1`。模型支持文生视频、首尾帧图生视频，以及带图片/视频参考的 Omni 模式。

> 参数依据：Chrome 中 Leonardo AI Creation 的模型控件（2026-08-07）与 [Leonardo Kling O3 REST 指南](https://docs.leonardo.ai/v1.0/docs/kling-o3)。本服务不把上游 Token 放进业务请求；Worker 从账号池分配账号后才组装第三方请求。

## 1. 能力与约束

| 项目 | 支持值 |
| --- | --- |
| 模型 ID | `kling-video-o-3` |
| 时长 | 3–15 秒；视频参考模式最多 10 秒 |
| 画幅 | `16:9`、`1:1`、`9:16` |
| 分辨率 | `720P`（Standard）、`1080P`（Pro）、`4K` |
| 输出 | `video/mp4`；上游返回的 `apiCreditCost` 是最终计费值 |
| 音频 | `audio: true/false`，映射为上游 `motion_has_audio` |
| 首尾帧 | `start_frame` 最多 1 张；`end_frame` 最多 1 张且必须同时有首帧 |
| 图片参考 | 最多 7 张；与视频参考同时使用时最多 4 张；仅 Standard/Pro；4K 不支持图片参考 |
| 视频参考 | 1 个已生成视频 ID（`reference_video_id`）；不能与首尾帧同时使用 |

三个模式的 `mode`：

* `text-to-video`：只使用文字和公共参数。
* `image-to-video`：`image_url` 是首帧，`end_image_url` 可选为尾帧。
* `reference-to-video`：图片参考 URL 和/或上一次任务结果中的 `reference_video_id`。`omni`、`omini` 是该模式的兼容别名。

## 2. 顶层请求

```http
POST https://api-leo.clawsea.ai/v1/tasks
X-API-Key: YOUR_API_KEY
Idempotency-Key: kling-o3-00000001
Content-Type: application/json
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `provider` | string | 否 | 固定 `leonardo` |
| `task_type` | string | 否 | 固定 `VIDEO_GENERATION` |
| `model` | string | 是 | 固定 `kling-video-o-3` |
| `mode` | string | 是 | `text-to-video`、`image-to-video`、`reference-to-video`、`omni`、`omini` |
| `input` | object | 是 | 见第 3 节；额外字段会返回 HTTP 422 |
| `priority` | integer | 否 | `-100` 至 `100`，默认 `0` |
| `estimated_credit_cost` | integer | 否 | 服务按当前定价自动覆盖；用于账号余额预筛选 |

幂等键重复且请求体相同会返回原任务；请求体不同返回 `409 IDEMPOTENCY_CONFLICT`。

## 3. `input` 字段

### 3.1 公共字段

| 字段 | 类型 | 默认 | 约束/说明 |
| --- | --- | --- | --- |
| `prompt` | string | — | 必填，1–1500 字符 |
| `duration` | integer | `3` | 3–15 秒；视频参考模式 3–10 秒 |
| `resolution` | enum | `1080P` | `720P`、`1080P`、`4K` |
| `aspect_ratio` | enum | `16:9` | `16:9`、`1:1`、`9:16` |
| `audio` | boolean | `true` | 是否生成音频；对应 `motion_has_audio` |

提交给 Leonardo 的尺寸矩阵：

| 画幅 | 720P | 1080P | 4K |
| --- | --- | --- | --- |
| `16:9` | 1280×720 | 1920×1080 | 3840×2160 |
| `1:1` | 960×960 | 1440×1440 | 2880×2880 |
| `9:16` | 720×1280 | 1080×1920 | 2160×3840 |

### 3.2 文生视频

`mode` 为 `text-to-video`，仅发送公共字段：

```json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "kling-video-o-3",
  "mode": "text-to-video",
  "input": {
    "prompt": "A paper boat drifting across a quiet lake at sunrise, slow dolly shot.",
    "duration": 3,
    "resolution": "720P",
    "aspect_ratio": "16:9",
    "audio": false
  }
}
```

### 3.3 首尾帧图生视频

`mode` 为 `image-to-video`：

```json
{
  "model": "kling-video-o-3",
  "mode": "image-to-video",
  "input": {
    "prompt": "The camera moves from the start composition to the ending composition.",
    "duration": 5,
    "resolution": "1080P",
    "aspect_ratio": "16:9",
    "audio": true,
    "image_url": "https://cdn.example.com/start.jpg",
    "end_image_url": "https://cdn.example.com/end.jpg"
  }
}
```

`image_url` 必须是公网 HTTP(S) 图片；`end_image_url` 可省略。Worker 会下载、校验并上传图片，生成 `guidances.start_frame` 与可选 `guidances.end_frame`。

### 3.4 Omni 参考生视频

`mode` 可写 `reference-to-video`、`omni` 或 `omini`。图片使用公网 URL；视频参考使用已完成任务 `output.media[].id`（上游生成视频 ID），因为 Leonardo 的 `video_reference_base` 只接受 `GENERATED` 类型：

```json
{
  "model": "kling-video-o-3",
  "mode": "omni",
  "input": {
    "prompt": "Keep the character identity and camera language from the references.",
    "duration": 8,
    "resolution": "1080P",
    "aspect_ratio": "1:1",
    "audio": true,
    "reference_image_urls": [
      "https://cdn.example.com/character.jpg",
      "https://cdn.example.com/style.jpg"
    ],
    "reference_video_id": "GENERATED_VIDEO_MEDIA_ID"
  }
}
```

图片参考和视频参考可以组合，但最多 4 张图片；只有图片参考时最多 7 张。4K 与图片参考组合会在 API 层返回 422。视频参考模式时长超过 10 秒会返回 422。

## 4. 积分预估与扣费

浏览器 Generate 按钮实测为“每秒 × 分辨率/音频档位”，数量固定为 1：

当前服务端定价规则版本为 `leonardo-ui-20260807.v2`。

| 分辨率 | 无音频（积分/秒） | 有音频（积分/秒） |
| --- | ---: | ---: |
| 720P | 168 | 224 |
| 1080P | 224 | 280 |
| 4K | 420 | 420 |

示例：3 秒 720P 无音频 = `504`；4 秒 1080P 有音频 = `1120`；15 秒 4K 有/无音频均为 `6300`。服务创建任务时写入 `estimated_credit_cost`，Worker 领取任务前再次计算，并按 `balance_credits - reserved_credits >= estimated_credit_cost` 选择账号；上游 `apiCreditCost` 返回后更新预留，完成时写入 `actual_credit_cost` 和积分流水。没有余额足够的账号时任务停留在 `WAITING_ACCOUNT`，不会提交上游。

## 5. 创建响应

HTTP `202 Accepted`：

```json
{
  "task_uuid": "4641529b-37a9-4a9d-bdf2-e318fa2ca698",
  "model": "kling-video-o-3",
  "mode": "text-to-video",
  "input_schema_version": "kling-o3.v1",
  "status": "QUEUED",
  "estimated_credit_cost": 504,
  "reserved_credit_cost": 0,
  "actual_credit_cost": null,
  "progress": {"phase": "QUEUED", "resolved_assets": 0, "total_assets": 0}
}
```

## 6. 查询与最终输出

```http
GET https://api-leo.clawsea.ai/v1/tasks/{task_uuid}
X-API-Key: YOUR_API_KEY
```

状态依次可能为 `QUEUED`、`WAITING_ACCOUNT`、`RESOLVING_MEDIA`、`SUBMITTING`、`RUNNING`、`COMPLETED` 或 `FAILED`。建议每 3–10 秒轮询，进入终态后停止。

完成示例：

```json
{
  "task_uuid": "4641529b-37a9-4a9d-bdf2-e318fa2ca698",
  "upstream_task_id": "1f190d64-3730-6ad0-ab0d-0b93f35886d1",
  "status": "COMPLETED",
  "estimated_credit_cost": 504,
  "reserved_credit_cost": 504,
  "actual_credit_cost": 504,
  "output": {
    "provider": "leonardo",
    "generation_id": "1f190d64-3730-6ad0-ab0d-0b93f35886d1",
    "media": [
      {
        "id": "GENERATED_VIDEO_MEDIA_ID",
        "type": "video/mp4",
        "width": 1280,
        "height": 720,
        "url": "https://cdn.example.com/result.mp4"
      }
    ]
  }
}
```

输出媒体的 `id` 可作为后续 Omni 请求的 `reference_video_id`。

## 7. 错误约定

| HTTP/任务错误 | 含义 |
| --- | --- |
| `422 INPUT_VALIDATION` | 字段、枚举、模式、URL 或时长约束失败 |
| `409 IDEMPOTENCY_CONFLICT` | 幂等键对应不同请求体 |
| `TASK MEDIA_*` | 媒体下载、探测或上传失败 |
| `TASK UPSTREAM_*` | Leonardo GraphQL/生成/轮询错误 |

上游返回的 `apiCreditCost` 优先于本地预估，实际结算值保存在任务详情和 `AccountCreditLedger` 中。
