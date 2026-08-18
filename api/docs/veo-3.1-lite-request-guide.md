# Veo 3.1 Lite API 请求指南

FRAME OPS 通过 Leonardo 账号池提交 `veo-3.1-lite` 视频任务。生产基址：

```text
https://api-leo.clawsea.ai
```

## 1. 能力总览

| 项目 | 支持值 |
| --- | --- |
| `model` | `veo-3.1-lite` |
| `task_type` | `VIDEO_GENERATION` |
| 模式 | `text-to-video`、`image-to-video` |
| 时长 | `4`、`6`、`8` 秒 |
| 分辨率 | `720P`、`1080P` |
| 比例 | `16:9`、`9:16` |
| 音频 | `audio=true/false` |
| 首尾帧 | 首帧必填、尾帧可选 |
| 图片参考模式 | 不支持；请使用首帧/尾帧模式 |
| 数量 | 固定 `1` |
| 可见性 | 固定 `public=false` |
| schema | `veo-3.1-lite.v1` |

`4K` 与 `reference-to-video` 是 Veo 3.1 主模型能力，不属于 Lite 合同。API 会在入队前以 HTTP 422 拒绝，避免错误任务占用账号与积分。

## 2. 认证与提交

每次请求携带 `X-API-Key`；任务提交还必须携带 8–128 字符且全局唯一的 `Idempotency-Key`。

```http
POST /v1/tasks
Content-Type: application/json
X-API-Key: YOUR_API_KEY
Idempotency-Key: veo31-lite-unique-key
```

## 3. 输入参数

### 顶层字段

| 字段 | 类型 | 必填 | 固定/允许值 | 说明 |
| --- | --- | --- | --- | --- |
| `provider` | string | 否 | `leonardo` | 默认值也是 `leonardo` |
| `task_type` | string | 是 | `VIDEO_GENERATION` | 视频生成任务 |
| `model` | string | 是 | `veo-3.1-lite` | 模型标识 |
| `mode` | string | 是 | `text-to-video` / `image-to-video` | 生成模式 |
| `input` | object | 是 | 见下表 | 业务参数 |
| `priority` | integer | 否 | `-100`–`100` | 默认 `0` |

### `input` 字段

| 字段 | 类型 | 必填 | 默认值 | 约束与说明 |
| --- | --- | --- | --- | --- |
| `prompt` | string | 是 | — | 1–9999 字符 |
| `duration` | integer | 否 | `8` | `4` / `6` / `8` |
| `resolution` | string | 否 | `720P` | `720P` / `1080P` |
| `aspect_ratio` | string | 否 | `16:9` | `16:9` / `9:16` |
| `audio` | boolean | 否 | `true` | 是否生成音轨，也参与积分预算 |
| `negative_prompt` | string | 否 | — | 最长 1000 字符 |
| `seed` | integer | 否 | — | 固定种子，0–4294967295 |
| `image_url` | URL | 图生必填 | — | 首帧公网 HTTP(S) URL |
| `end_image_url` | URL | 否 | — | 尾帧公网 HTTP(S) URL；必须与 `image_url` 同时使用 |

调用方不传 `public`、`quantity`、`width`、`height`、`motion_has_audio`、`guidances` 或 Leonardo 媒体 ID。Worker 固定 `public=false`、`quantity=1`，将尺寸、音频字段和媒体引用转换成上游请求。

## 4. 尺寸矩阵

| 分辨率 | `16:9` | `9:16` |
| --- | ---: | ---: |
| `720P` | 1280×720 | 720×1280 |
| `1080P` | 1920×1080 | 1080×1920 |

## 5. 文生视频示例

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: veo31-lite-t2v-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"VIDEO_GENERATION",
    "model":"veo-3.1-lite",
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

## 6. 首尾帧生视频示例

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: veo31-lite-i2v-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"VIDEO_GENERATION",
    "model":"veo-3.1-lite",
    "mode":"image-to-video",
    "input":{
      "prompt":"The paper boat begins to glide, with a slow forward camera move.",
      "duration":4,
      "resolution":"720P",
      "aspect_ratio":"9:16",
      "audio":false,
      "image_url":"https://cdn.example.com/start.png",
      "end_image_url":"https://cdn.example.com/end.png"
    }
  }'
```

服务先保存 URL，账号分配后再下载、探测并上传为 Leonardo 资产。只使用首帧时省略 `end_image_url`。

## 7. 创建响应与状态查询

创建成功返回 HTTP 202。积分预算由服务端按模型参数计算，调用方传入的预算值不会覆盖服务端结果。

```json
{
  "task_uuid": "TASK_UUID",
  "model": "veo-3.1-lite",
  "mode": "text-to-video",
  "input_schema_version": "veo-3.1-lite.v1",
  "status": "QUEUED",
  "estimated_credit_cost": 120,
  "reserved_credit_cost": 0,
  "actual_credit_cost": null,
  "progress": {
    "phase": "QUEUED",
    "resolved_assets": 0,
    "total_assets": 0
  }
}
```

```bash
curl 'https://api-leo.clawsea.ai/v1/tasks/TASK_UUID' \
  -H 'X-API-Key: YOUR_API_KEY'
```

状态依次为 `QUEUED`、`RESOLVING_MEDIA`（图生任务）、`SUBMITTING`、`SUBMITTED`/`PROCESSING`，最终进入 `COMPLETED`、`FAILED` 或 `CANCELLED`。成功任务的 `output.media` 包含 MP4 URL、宽度和高度。

## 8. 积分矩阵

以下为 Leonardo 生成按钮在 2026-08-09 的逐组合实测值；横竖屏同价。

| 音频 | 时长 | 720P | 1080P |
| --- | ---: | ---: | ---: |
| 关闭 | 4 秒 | 120 | 200 |
| 关闭 | 6 秒 | 180 | 300 |
| 关闭 | 8 秒 | 240 | 400 |
| 开启 | 4 秒 | 200 | 320 |
| 开启 | 6 秒 | 300 | 480 |
| 开启 | 8 秒 | 400 | 640 |

等价单价：720P 无音频 30/秒、720P 有音频 50/秒、1080P 无音频 50/秒、1080P 有音频 80/秒。

Worker 领取任务前重新报价，只选择 `balance_credits - reserved_credits >= estimated_credit_cost` 且并发可用的账号，并原子预留积分。上游返回 `apiCreditCost` 时会修正预留；终态把实际值写入 `actual_credit_cost` 与积分流水。

## 9. 输出字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `task_uuid` | UUID | 内部任务 ID |
| `upstream_task_id` | string/null | Leonardo generation ID |
| `status` | string | 当前状态 |
| `estimated_credit_cost` | integer | 入队预算 |
| `reserved_credit_cost` | integer | 已预留积分 |
| `actual_credit_cost` | integer/null | 终态实际扣除 |
| `output.provider` | string | `leonardo` |
| `output.generation_id` | string | 上游生成 ID |
| `output.media[]` | array | 输出媒体列表 |
| `output.media[].type` | string | 通常为 `video/mp4` |
| `output.media[].width` | integer | 输出宽度 |
| `output.media[].height` | integer | 输出高度 |
| `output.media[].url` | URL | 视频下载地址 |
| `error_code` | string/null | 失败错误码 |
| `error_message` | string/null | 失败详情 |

## 10. 错误与重试

| 错误 | 含义 |
| --- | --- |
| HTTP 422 | 模式、字段、URL、时长、比例、分辨率或固定字段校验失败 |
| HTTP 409 | 相同幂等键对应了不同请求体 |
| `WAITING_ACCOUNT` | 当前没有余额和并发都满足预算的账号 |
| `MEDIA_*` | 首尾帧下载、探测或上传失败 |
| `UPSTREAM_*` / `PROVIDER_*` | Leonardo 提交、生成、审核或输出失败 |

轮询建议为 3–10 秒；到达终态后停止。网络错误可使用同一个幂等键重试原请求，修改请求体时必须更换幂等键。
