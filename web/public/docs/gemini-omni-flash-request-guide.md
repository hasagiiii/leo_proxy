# Gemini Omni Flash API 请求指南

本文说明 FRAME OPS 如何通过 Leonardo 账号池提交 `gemini-omni-flash` 视频任务。线上基址：

```text
https://api-leo.clawsea.ai
```

## 能力总览

| 项目 | 支持值 |
| --- | --- |
| `model` | `gemini-omni-flash` |
| `task_type` | `VIDEO_GENERATION` |
| 模式 | `text-to-video`、`reference-to-video`；`omni`/`omini` 会归一为参考模式 |
| 时长 | `3`–`10` 秒，整数步进 1 秒 |
| 分辨率 | 固定 `720P` |
| 比例 | `16:9`、`9:16` |
| 像素 | `1280×720`、`720×1280` |
| 参考图 | 1–5 张，PNG/JPG/WEBP；Leonardo UI 单文件上限 25 MB |
| 数量 | 固定 `1` |
| 可见性 | 固定 `public=false` |
| 输出 | `video/mp4` |
| schema | `gemini-omni-flash.v1` |

该模型没有首帧、尾帧、视频参考或音频参考入口。图片以 `image_reference` guidance 参与生成，不作为首帧。

## 鉴权与幂等

每次请求携带 `X-API-Key`。任务提交还必须携带 8–128 字符的唯一 `Idempotency-Key`。

```bash
export LEO_API_BASE='https://api-leo.clawsea.ai'
export LEO_API_KEY='YOUR_API_KEY'
```

## 输入字段

### 顶层字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `provider` | string | 是 | 固定 `leonardo` |
| `task_type` | string | 是 | 固定 `VIDEO_GENERATION` |
| `model` | string | 是 | 固定 `gemini-omni-flash` |
| `mode` | string | 是 | `text-to-video` 或 `reference-to-video` |
| `input` | object | 是 | 见下表 |
| `priority` | integer | 否 | `-100`–`100`，默认 `0` |

### `input` 字段

| 字段 | 类型 | 必填 | 默认值 | 约束 |
| --- | --- | --- | --- | --- |
| `prompt` | string | 是 | — | 1–2500 字符 |
| `duration` | integer | 否 | `5` | 3–10 |
| `resolution` | string | 否 | `720P` | 只接受 `720P` |
| `aspect_ratio` | string | 否 | `16:9` | `16:9` / `9:16` |
| `reference_image_urls` | URL[] | 参考模式是 | — | 1–5 个公网 HTTP(S) 图片 URL |

调用方不能覆盖 `public`、`quantity`、guidance strength 或上游媒体 ID。Worker 固定 `public=false`、`quantity=1`、参考强度 `MID`，并在分配账号后下载、探测、上传参考图。

## 文生视频

```bash
curl -X POST "$LEO_API_BASE/v1/tasks" \
  -H "X-API-Key: $LEO_API_KEY" \
  -H 'Idempotency-Key: gemini-omni-t2v-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"VIDEO_GENERATION",
    "model":"gemini-omni-flash",
    "mode":"text-to-video",
    "input":{
      "prompt":"A cobalt-blue paper airplane glides above a quiet meadow at sunrise.",
      "duration":3,
      "resolution":"720P",
      "aspect_ratio":"16:9"
    }
  }'
```

## 参考图生视频

```bash
curl -X POST "$LEO_API_BASE/v1/tasks" \
  -H "X-API-Key: $LEO_API_KEY" \
  -H 'Idempotency-Key: gemini-omni-ref-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"VIDEO_GENERATION",
    "model":"gemini-omni-flash",
    "mode":"reference-to-video",
    "input":{
      "prompt":"Animate the reference with a slow cinematic camera move.",
      "duration":3,
      "resolution":"720P",
      "aspect_ratio":"9:16",
      "reference_image_urls":[
        "https://cdn.example.com/reference-01.png"
      ]
    }
  }'
```

## 创建响应

成功创建返回 HTTP `202`：

```json
{
  "task_uuid": "TASK_UUID",
  "model": "gemini-omni-flash",
  "mode": "text-to-video",
  "input_schema_version": "gemini-omni-flash.v1",
  "status": "QUEUED",
  "estimated_credit_cost": 300,
  "reserved_credit_cost": 0,
  "actual_credit_cost": null,
  "progress": {
    "phase": "QUEUED",
    "resolved_assets": 0,
    "total_assets": 0
  }
}
```

## 状态查询与输出

```bash
curl "$LEO_API_BASE/v1/tasks/TASK_UUID" -H "X-API-Key: $LEO_API_KEY"
```

终态是 `COMPLETED`、`FAILED` 或 `CANCELLED`。成功示例：

```json
{
  "task_uuid": "TASK_UUID",
  "upstream_task_id": "GENERATION_ID",
  "status": "COMPLETED",
  "estimated_credit_cost": 300,
  "reserved_credit_cost": 0,
  "actual_credit_cost": 300,
  "output": {
    "provider": "leonardo",
    "generation_id": "GENERATION_ID",
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

失败时查看顶层 `error_code`、`error_message` 和 `output.error`。媒体处理阶段还可能出现 `MEDIA_*` 错误。

## 积分与账号选择

Leonardo Generate 按钮的实测预览为每秒 `100` 积分：

| 时长 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 积分 | 300 | 400 | 500 | 600 | 700 | 800 | 900 | 1000 |

两个比例价格相同。任务创建时写入 `estimated_credit_cost`；Worker 领取前按当前规则重新报价，只选择 `balance_credits - reserved_credits >= estimated_credit_cost` 的活跃账号，并在同一事务预留积分。上游返回 `apiCreditCost` 时会校正预留，任务终态以 `actual_credit_cost` 结算并写入积分流水。

## 上游请求映射

`reference-to-video` 会转换成：

```json
{
  "model": "gemini-omni-flash",
  "public": false,
  "parameters": {
    "prompt": "...",
    "duration": 3,
    "quantity": 1,
    "width": 720,
    "height": 1280,
    "guidances": {
      "image_reference": [
        {
          "image": {"id": "UPLOADED_MEDIA_ID", "type": "UPLOADED"},
          "strength": "MID"
        }
      ]
    }
  }
}
```

上游请求不发送旧的 `parameters.mode` 字段。
