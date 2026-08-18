# Veo 3.1 API 请求指南

FRAME OPS 通过 Leonardo 账号池提交 `veo-3.1-generate-001` 视频任务。线上基址：

```text
https://api-leo.clawsea.ai
```

## 能力总览

| 项目 | 支持值 |
| --- | --- |
| `model` | `veo-3.1-generate-001` |
| `task_type` | `VIDEO_GENERATION` |
| 模式 | `text-to-video`、`image-to-video`、`reference-to-video` |
| 时长 | `4`、`6`、`8` 秒 |
| 分辨率 | `720P`、`1080P`、`4K` |
| 比例 | `16:9`、`9:16` |
| 音频 | `audio=true/false` |
| 首尾帧 | 首帧必填、尾帧可选 |
| 图片参考 | 1–3 张，强度 `LOW`/`MID`/`HIGH` |
| 数量 | 固定 `1` |
| 可见性 | 固定 `public=false` |
| schema | `veo-3.1.v1` |

生产真实任务、尺寸探测、积分流水和接口问题见 [`veo-3.1-smoke-report.md`](veo-3.1-smoke-report.md)。当前 `text-to-video` 与 `image-to-video` 已通过真实任务验收；`reference-to-video` 的类型化合同与媒体处理已接入，但现有账号池的上游 GraphQL 提交通道持续返回 `UPSTREAM_GRAPHQL_ERROR`，暂未达到稳定生产状态。

## 输入字段

| 字段 | 类型 | 必填 | 默认值 | 约束 |
| --- | --- | --- | --- | --- |
| `prompt` | string | 是 | — | 1–9999 字符 |
| `duration` | integer | 否 | `8` | `4` / `6` / `8` |
| `resolution` | string | 否 | `720P` | `720P` / `1080P` / `4K` |
| `aspect_ratio` | string | 否 | `16:9` | `16:9` / `9:16` |
| `audio` | boolean | 否 | `true` | 是否生成音轨 |
| `negative_prompt` | string | 否 | — | 最长 1000 字符 |
| `seed` | integer | 否 | — | 0–4294967295 |
| `image_url` | URL | 图生必填 | — | 首帧公网 HTTP(S) URL |
| `end_image_url` | URL | 否 | — | 尾帧公网 HTTP(S) URL |
| `reference_image_urls` | URL[] | 参考模式必填 | — | 1–3 张、有序 |
| `reference_strength` | string | 否 | `MID` | `LOW` / `MID` / `HIGH` |

调用方不传 `public`、`quantity`、`width`、`height`、`guidances` 或上游媒体 ID。Worker 固定私有生成和单输出，并在账号分配后把网络图片转换为 Leonardo 资产 ID。

## 尺寸矩阵

| 分辨率 | `16:9` | `9:16` |
| --- | ---: | ---: |
| `720P` | 1280×720 | 720×1280 |
| `1080P` | 1920×1080 | 1080×1920 |
| `4K` | 3840×2160 | 2160×3840 |

## 文生视频

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: veo31-t2v-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"VIDEO_GENERATION",
    "model":"veo-3.1-generate-001",
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

## 首尾帧生视频

```json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "veo-3.1-generate-001",
  "mode": "image-to-video",
  "input": {
    "prompt": "Move smoothly from the first frame to the final frame.",
    "duration": 4,
    "resolution": "720P",
    "aspect_ratio": "9:16",
    "audio": false,
    "image_url": "https://cdn.example.com/start.png",
    "end_image_url": "https://cdn.example.com/end.png"
  }
}
```

## 图片参考生视频

> 当前生产限制：参考图请求可以通过校验、预算、账号选择和媒体上传，但在取得 generation ID 前被上游 GraphQL 通道拒绝。失败任务实际积分为 0，预留积分会释放并记录 `RELEASE` 流水。调用方应以终态为准，不要把 HTTP 202 视为生成成功。

```json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "veo-3.1-generate-001",
  "mode": "reference-to-video",
  "input": {
    "prompt": "Create a restrained premium film using the ordered references.",
    "duration": 4,
    "resolution": "720P",
    "aspect_ratio": "16:9",
    "audio": false,
    "reference_image_urls": [
      "https://cdn.example.com/subject.png",
      "https://cdn.example.com/style.png"
    ],
    "reference_strength": "HIGH"
  }
}
```

## 创建响应与查询

成功创建返回 HTTP `202`，其中预算由服务端覆盖调用方传值：

```json
{
  "task_uuid": "TASK_UUID",
  "model": "veo-3.1-generate-001",
  "mode": "text-to-video",
  "input_schema_version": "veo-3.1.v1",
  "status": "QUEUED",
  "estimated_credit_cost": 800,
  "reserved_credit_cost": 0,
  "actual_credit_cost": null
}
```

```bash
curl 'https://api-leo.clawsea.ai/v1/tasks/TASK_UUID' \
  -H 'X-API-Key: YOUR_API_KEY'
```

终态为 `COMPLETED`、`FAILED` 或 `CANCELLED`。成功任务的 `output.media` 包含 MP4 URL、宽度和高度。

## 积分与账号选择

| 音频 | 时长 | 720P | 1080P | 4K |
| --- | ---: | ---: | ---: | ---: |
| 关闭 | 4 秒 | 800 | 800 | 1600 |
| 关闭 | 6 秒 | 1200 | 1200 | 2400 |
| 关闭 | 8 秒 | 1600 | 1600 | 3200 |
| 开启 | 4 秒 | 1600 | 1600 | 2400 |
| 开启 | 6 秒 | 2400 | 2400 | 3600 |
| 开启 | 8 秒 | 3200 | 3200 | 4800 |

两个比例使用相同预算。Worker 领取前重新报价，只选择 `balance_credits - reserved_credits >= estimated_credit_cost` 的活跃账号并原子预留。上游返回 `apiCreditCost` 时修正预留，终态写入 `actual_credit_cost` 和积分流水。若任务运行期间的余额刷新时间晚于上游提交时间，完成结算会把该余额视为已包含 Provider 扣款，仅写任务 `credit_delta` 而不再次减少余额；提交前失败的每次尝试都会写 `RELEASE`，并把账号预留归零。

## 错误处理

| 错误 | 含义 |
| --- | --- |
| HTTP 422 | 模式、字段、URL、时长、尺寸、参考数量或固定字段校验失败 |
| HTTP 409 | 幂等键对应不同请求体 |
| `WAITING_ACCOUNT` | 当前没有余额和并发都满足预算的账号 |
| `MEDIA_*` | 网络媒体下载、探测或上传失败 |
| `UPSTREAM_*` / `PROVIDER_*` | Leonardo 提交、生成、审核或输出失败 |

官方参数依据：[Leonardo Veo 3.1 API 指南](https://docs.leonardo.ai/me/docs/veo-31)。
