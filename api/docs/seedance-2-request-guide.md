# Seedance 2.0 系列 API 请求指南

本文档说明 Video Task API 中以下三个 Leonardo 模型的请求、响应、状态轮询和错误处理：

- `seedance-2.0-mini`
- `seedance-2.0`
- `seedance-2.0-fast`

对应输入 schema 版本为 `seedance.v1`。

## 1. 接口概览

生产服务地址：

```text
https://api-leo.clawsea.ai
```

| 操作 | 方法与路径 | 成功状态码 |
| --- | --- | --- |
| 创建任务 | `POST /v1/tasks` | `202 Accepted` |
| 查询任务 | `GET /v1/tasks/{task_uuid}` | `200 OK` |
| 查询任务列表 | `GET /v1/tasks` | `200 OK` |
| 取消待提交任务 | `POST /v1/tasks/{task_uuid}/cancel` | `200 OK` |

所有 `/v1` 请求使用业务 API Key：

```http
X-API-Key: local-api-key
```

创建任务还需要幂等键和 JSON Content-Type：

```http
Idempotency-Key: seedance-request-00000001
Content-Type: application/json
```

`Idempotency-Key` 长度为 8–128 个字符。同一个幂等键配合同一个请求体会返回原任务；同一个幂等键配合不同请求体返回 HTTP `409` 和 `IDEMPOTENCY_CONFLICT`。

## 2. 模型能力

| 模型 ID | 时长 | 分辨率 | 模式 |
| --- | --- | --- | --- |
| `seedance-2.0-mini` | 4–15 秒 | `480P`、`720P` | 文生视频、首尾帧、Omni |
| `seedance-2.0` | 4–15 秒 | `480P`、`720P`、`1080P`、`4K` | 文生视频、首尾帧、Omni |
| `seedance-2.0-fast` | 4–15 秒 | `480P`、`720P` | 文生视频、首尾帧、Omni |

三个模型均接受以下画面比例：

```text
21:9, 16:9, 4:3, 1:1, 3:4, 9:16
```

## 3. 创建任务的顶层参数

请求结构：

```json
{
  "provider": "leonardo",
  "model": "seedance-2.0-mini",
  "task_type": "VIDEO_GENERATION",
  "mode": "text-to-video",
  "input": {},
  "priority": 0,
  "estimated_credit_cost": 320
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `provider` | string | 否 | `leonardo` | 类型化 Seedance 请求固定使用 `leonardo`。 |
| `model` | string | 是 | — | 三个 Seedance 模型 ID 之一。 |
| `task_type` | string | 否 | `VIDEO_GENERATION` | 类型化视频请求固定为该值。 |
| `mode` | string | 是 | — | `text-to-video`、`image-to-video`、`reference-to-video`；Omni 也接受 `omni` 或 `omini`。 |
| `input` | object | 是 | — | 与模式对应的输入对象，详见后续章节。 |
| `priority` | integer | 否 | `0` | 范围 `-100` 到 `100`；数值越大，队列优先级越高。 |
| `estimated_credit_cost` | integer | 否 | 按模型规则计算 | 非负整数；类型化 Seedance 请求会按模型、分辨率和时长自动计算并用于预留账户积分，调用方传入值会被规则值覆盖；最终消耗以 `actual_credit_cost` 为准。 |

当 `mode` 使用 `omni` 或 `omini` 时，任务响应中的规范化值为 `reference-to-video`。

## 4. 公共 `input` 参数

三种模式共享以下字段：

| 字段 | 类型 | 必填 | 默认值 | 约束 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `prompt` | string | 是 | — | 1–7000 字符 | 视频内容、动作、镜头、风格和声音要求。 |
| `duration` | integer | 否 | `4` | 4–15 | 输出时长，单位为秒。 |
| `resolution` | string | 否 | `480P` | 模型相关 | `480P`、`720P`；标准版额外接受 `1080P`、`4K`。 |
| `aspect_ratio` | string | 否 | `16:9` | 六种枚举值 | 输出画面比例。 |

输入对象使用严格字段校验。拼写错误或额外字段会得到 HTTP `422`。

### 4.1 分辨率与提交尺寸

| 比例 | 480P | 720P | 1080P | 4K |
| --- | --- | --- | --- | --- |
| `21:9` | 992×432 | 1470×630 | 2520×1080 | 5040×2160 |
| `16:9` | 864×496 | 1280×720 | 1920×1080 | 3840×2160 |
| `4:3` | 752×560 | 1112×834 | 1440×1080 | 2880×2160 |
| `1:1` | 640×640 | 960×960 | 1440×1440 | 2880×2880 |
| `3:4` | 560×752 | 834×1112 | 1080×1440 | 2160×2880 |
| `9:16` | 496×864 | 720×1280 | 1080×1920 | 2160×3840 |

这是 Leonardo UI/GraphQL 的提交尺寸。供应商返回的媒体元数据与文件视频流可能不同：生产 `480P + 16:9` 请求提交为 `864×496`，API 的 `output.media` 元数据为 `864×480`，下载后的 MP4 经 ffprobe 验证为 `864×496`。客户端对像素尺寸有严格要求时，应以实际文件流探针结果为准。

标准版的文生视频和首尾帧请求会把分辨率转换为：

| API 值 | GraphQL `parameters.mode` |
| --- | --- |
| `480P` | `RESOLUTION_480` |
| `720P` | `RESOLUTION_720` |
| `1080P` | `RESOLUTION_1080` |
| `4K` | `RESOLUTION_2160` |

Mini 与 Fast 以 `width`/`height` 作为规范分辨率字段，不发送
`parameters.mode`。三个模型的 Omni 请求同样仅发送 `width`/`height`，
不发送该旧字段，并原样保留调用方选择的模型 ID。Omni/Omini 仅控制
`image_reference`、`video_reference_base`、`audio_reference` guidance，
不会把标准版或 Fast 替换为 Mini。

## 5. 文生视频

模式：

```json
"mode": "text-to-video"
```

`input` 仅使用公共参数。

### 5.0 4 秒积分报价（浏览器实测）

以下数值来自 Leonardo 页面 `Generate` 按钮的即时积分报价，采集条件为：Video、时长 4 秒、比例 `16:9`、数量 1、无参考素材。Audio 开启与关闭时读取到的数值相同；本次只读取报价，没有点击生成，因此不产生新的扣费。

| 模型 | 480P | 720P | 1080P | 4K |
| --- | ---: | ---: | ---: | ---: |
| `seedance-2.0-mini` | **320**（864×496） | **640**（1280×720） | — | — |
| `seedance-2.0` | **562**（864×496） | **1209**（1280×720） | **2721**（1920×1080） | **7616**（3840×2160） |
| `seedance-2.0-fast` | **449**（864×496） | **967**（1280×720） | — | — |

这是当前浏览器 UI 的预览报价。定价规则版本为 `leonardo-ui-20260808.v7`；后端始终按调用方选择的模型使用这组 4 秒基准，时长按基准时长线性换算并向上取整。任务提交时若上游返回 `apiCreditCost`，会校正账户预留；任务完成后以 `actual_credit_cost` 为准，并将估算、预留、实际值写入积分结算流水。

提交前的账号选择使用可用积分 `balance_credits - reserved_credits`：Worker 会重新计算当前任务预算，只领取可用积分大于等于预算且仍有并发槽位的活跃账号，并在行锁事务内立即增加预留；随后还会刷新上游真实余额再提交。没有符合条件的账号时，任务进入 `WAITING_ACCOUNT`，不会向上游发送生成请求。

### 5.1 Mini 示例

```bash
curl -X POST https://api-leo.clawsea.ai/v1/tasks \
  -H 'X-API-Key: local-api-key' \
  -H 'Idempotency-Key: seedance-mini-text-0001' \
  -H 'Content-Type: application/json' \
  -d '{
    "provider":"leonardo",
    "model":"seedance-2.0-mini",
    "task_type":"VIDEO_GENERATION",
    "mode":"text-to-video",
    "input":{
      "prompt":"A red paper lantern floating above a calm river at sunrise, gentle cinematic camera motion.",
      "duration":4,
      "resolution":"480P",
      "aspect_ratio":"16:9"
    },
    "priority":0,
    "estimated_credit_cost":320
  }'
```

### 5.2 Seedance 2.0 4K 示例

```json
{
  "provider": "leonardo",
  "model": "seedance-2.0",
  "task_type": "VIDEO_GENERATION",
  "mode": "text-to-video",
  "input": {
    "prompt": "A wide cinematic aerial view of ocean cliffs at sunrise, slow forward camera movement.",
    "duration": 8,
    "resolution": "4K",
    "aspect_ratio": "21:9"
  },
  "estimated_credit_cost": 15232
}
```

### 5.3 Fast 720P 示例

```json
{
  "provider": "leonardo",
  "model": "seedance-2.0-fast",
  "task_type": "VIDEO_GENERATION",
  "mode": "text-to-video",
  "input": {
    "prompt": "A green kite flying over a quiet meadow, smooth cinematic movement.",
    "duration": 4,
    "resolution": "720P",
    "aspect_ratio": "9:16"
  },
  "estimated_credit_cost": 967
}
```

示例积分值与当前自动定价公式一致；业务调用方可以省略该字段，类型化请求会由后端覆盖为规则计算值。

## 6. 首帧与尾帧模式

模式：

```json
"mode": "image-to-video"
```

在公共字段之外接受：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `image_url` | HTTP(S) URL | 是 | 起始帧图片地址。 |
| `end_image_url` | HTTP(S) URL | 否 | 结束帧图片地址；省略时为单首帧模式。 |

示例：

```bash
curl -X POST https://api-leo.clawsea.ai/v1/tasks \
  -H 'X-API-Key: local-api-key' \
  -H 'Idempotency-Key: seedance-mini-frames-0001' \
  -H 'Content-Type: application/json' \
  -d '{
    "provider":"leonardo",
    "model":"seedance-2.0-mini",
    "task_type":"VIDEO_GENERATION",
    "mode":"image-to-video",
    "input":{
      "prompt":"Subtle lantern movement on calm water, keep the composition stable.",
      "duration":4,
      "resolution":"480P",
      "aspect_ratio":"16:9",
      "image_url":"https://cdn.example.com/start.jpg",
      "end_image_url":"https://cdn.example.com/end.jpg"
    },
    "estimated_credit_cost":320
  }'
```

Worker 会将图片下载并上传到 Leonardo，随后生成：

```json
{
  "guidances": {
    "start_frame": [
      {"image": {"id": "UPLOADED_START_ID", "type": "UPLOADED"}}
    ],
    "end_frame": [
      {"image": {"id": "UPLOADED_END_ID", "type": "UPLOADED"}}
    ]
  }
}
```

## 7. Omni 参考模式

以下三个值均可作为请求模式：

```text
reference-to-video
omni
omini
```

API 会把后两个别名规范化为 `reference-to-video`。

在公共字段之外接受：

| 字段 | 类型 | 必填 | 默认值 | 数量上限 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `reference_image_urls` | URL 数组 | 条件必填 | `[]` | 4 | 风格、主体或构图参考图片。 |
| `reference_video_urls` | URL 数组 | 条件必填 | `[]` | 3 | 动作、镜头或节奏参考视频。 |
| `reference_audio_urls` | URL 数组 | 否 | `[]` | 1 | 声音参考；请求中还需至少一个图片或视频引用。 |

`reference_image_urls` 与 `reference_video_urls` 至少有一个非空数组。数组顺序会保留到上游 guidance 顺序中。

### 7.1 Omni 完整示例

```bash
curl -X POST https://api-leo.clawsea.ai/v1/tasks \
  -H 'X-API-Key: local-api-key' \
  -H 'Idempotency-Key: seedance-standard-omni-0001' \
  -H 'Content-Type: application/json' \
  -d '{
    "provider":"leonardo",
    "model":"seedance-2.0",
    "task_type":"VIDEO_GENERATION",
    "mode":"omni",
    "input":{
      "prompt":"Use the referenced character, camera motion and audio rhythm in a calm sunrise scene.",
      "duration":8,
      "resolution":"720P",
      "aspect_ratio":"16:9",
      "reference_image_urls":[
        "https://cdn.example.com/character-front.jpg",
        "https://cdn.example.com/character-side.jpg"
      ],
      "reference_video_urls":[
        "https://cdn.example.com/camera-motion.mp4"
      ],
      "reference_audio_urls":[
        "https://cdn.example.com/ambient-reference.mp3"
      ]
    },
    "estimated_credit_cost":2418
  }'
```

对应的上游 guidance 结构：

```json
{
  "guidances": {
    "image_reference": [
      {
        "image": {"id": "IMAGE_ID_1", "type": "UPLOADED"},
        "strength": "MID"
      },
      {
        "image": {"id": "IMAGE_ID_2", "type": "UPLOADED"},
        "strength": "MID"
      }
    ],
    "video_reference_base": [
      {"video": {"id": "VIDEO_ID_1", "type": "UPLOADED", "duration": 8}}
    ],
    "audio_reference": [
      {"audio": {"id": "AUDIO_ID_1", "type": "UPLOADED"}}
    ]
  }
}
```

Worker 会把探测到的引用视频时长从毫秒四舍五入为秒，并写入每个
`video_reference_base[].video.duration`；Seedance 上游请求同时显式发送
`motion_has_audio=true`。Omni 模式无论是仅图片还是图片/视频混合参考，
都以 `width`/`height` 作为上游分辨率，不发送旧的 `parameters.mode`，
并保留调用方选择的 Mini、标准版或 Fast 模型 ID。

## 8. 引用媒体要求

所有媒体 URL 由 Worker 主动下载并验证：

| 项目 | 要求 |
| --- | --- |
| URL 协议 | `http` 或 `https` |
| 网络地址 | 域名解析到公网地址 |
| URL 凭据 | URL 中不携带用户名或密码 |
| 重定向 | 最多跟随 3 次有效重定向 |
| 图片格式 | JPEG、PNG、WebP |
| 图片大小 | 默认最大 30 MiB |
| 音频大小 | 默认最大 30 MiB |
| 视频大小 | 默认最大 200 MiB |
| 音频/视频时长 | 2–15 秒 |
| 音频/视频文件名 | URL 路径或 Content-Type 可确定文件扩展名 |

大小上限可通过以下环境变量调整：

```text
VIDEO_SERVICE_MEDIA_MAX_IMAGE_BYTES
VIDEO_SERVICE_MEDIA_MAX_AUDIO_BYTES
VIDEO_SERVICE_MEDIA_MAX_VIDEO_BYTES
```

## 9. 创建任务响应

创建成功返回 HTTP `202` 和完整 `TaskView`。下面是进入队列时的示例：

```json
{
  "task_uuid": "33e0dfa4-1929-4115-be55-580e5c02c4b2",
  "idempotency_key": "seedance-mini-text-0001",
  "provider": "leonardo",
  "upstream_task_id": null,
  "account_uuid": null,
  "space_uuid": null,
  "task_type": "VIDEO_GENERATION",
  "model": "seedance-2.0-mini",
  "mode": "text-to-video",
  "input_schema_version": "seedance.v1",
  "input": {
    "prompt": "A red paper lantern floating above a calm river at sunrise.",
    "duration": 4,
    "resolution": "480P",
    "aspect_ratio": "16:9"
  },
  "output": null,
  "status": "QUEUED",
  "priority": 0,
  "estimated_credit_cost": 320,
  "reserved_credit_cost": 0,
  "actual_credit_cost": null,
  "submit_attempts": 0,
  "sync_attempts": 0,
  "error_code": null,
  "error_message": null,
  "progress": {
    "phase": "QUEUED",
    "resolved_assets": 0,
    "total_assets": 0
  },
  "created_at": "2026-08-06T15:09:08.560000",
  "queued_at": "2026-08-06T15:09:08.555000",
  "assigned_at": null,
  "upstream_submitted_at": null,
  "finished_at": null,
  "updated_at": "2026-08-06T15:09:08.560000"
}
```

### 9.1 `TaskView` 输出参数

| 字段 | 类型 | 何时有值 | 说明 |
| --- | --- | --- | --- |
| `task_uuid` | UUID | 始终 | 本系统任务 ID，用于查询、取消和日志关联。 |
| `idempotency_key` | string/null | 创建后 | 客户端提供的幂等键。 |
| `provider` | string | 始终 | 当前为 `leonardo`。 |
| `upstream_task_id` | string/null | 上游接受后 | Leonardo generation ID。 |
| `account_uuid` | UUID/null | 分配账号后 | 执行任务的本地账号记录 ID。 |
| `space_uuid` | UUID/null | 分配账号后 | 账号所属空间 ID。 |
| `task_type` | string | 始终 | `VIDEO_GENERATION`。 |
| `model` | string | 始终 | 实际模型 ID。 |
| `mode` | string/null | 始终 | 规范化后的模式；Omni 返回 `reference-to-video`。 |
| `input_schema_version` | string | 始终 | Seedance 类型化请求为 `seedance.v1`。 |
| `input` | object | 始终 | 已填充默认值并通过校验的输入。 |
| `output` | object/null | 运行中或终态 | 上游提交信息、完成媒体或错误详情。 |
| `status` | string | 始终 | 当前任务状态。 |
| `priority` | integer | 始终 | 队列优先级。 |
| `estimated_credit_cost` | integer | 始终 | 创建时由四模型规则自动计算的预估积分；类型化请求会覆盖调用方传值。 |
| `reserved_credit_cost` | integer | 分配后更新 | 当前预留积分；终态后释放。 |
| `actual_credit_cost` | integer/null | 终态 | 实际结算积分；普通上游失败通常为 `0`。 |
| `submit_attempts` | integer | 始终 | 上游提交尝试次数。 |
| `sync_attempts` | integer | 始终 | 上游状态同步次数。 |
| `error_code` | string/null | 失败时 | 稳定的机器可读错误码。 |
| `error_message` | string/null | 失败时 | 错误说明。 |
| `progress` | object | 始终 | 媒体解析和当前任务阶段的汇总对象。 |
| `progress.phase` | string | 始终 | 与顶层 `status` 对应的阶段。 |
| `progress.resolved_assets` | integer | 始终 | 已解析并上传的引用媒体数量。 |
| `progress.total_assets` | integer | 始终 | 任务需处理的引用媒体总数。 |
| `created_at` | datetime | 始终 | 任务记录创建时间。 |
| `queued_at` | datetime | 始终 | 进入队列时间。 |
| `assigned_at` | datetime/null | 分配后 | Worker 领取和分配账号的时间。 |
| `upstream_submitted_at` | datetime/null | 提交后 | 上游返回 generation ID 的时间。 |
| `finished_at` | datetime/null | 终态 | 完成、失败或取消时间。 |
| `updated_at` | datetime | 始终 | 最近更新时间。 |

## 10. 状态轮询

创建后使用 `task_uuid` 查询：

```bash
curl https://api-leo.clawsea.ai/v1/tasks/33e0dfa4-1929-4115-be55-580e5c02c4b2 \
  -H 'X-API-Key: local-api-key'
```

建议每 3–5 秒查询一次，直到进入终态。

| 状态 | 终态 | 说明 |
| --- | --- | --- |
| `QUEUED` | 否 | 已创建并等待 Worker。 |
| `CLAIMED` | 否 | Worker 已领取任务。 |
| `WAITING_ACCOUNT` | 否 | 等待可用账号或积分。 |
| `RESOLVING_MEDIA` | 否 | 下载、校验和上传首尾帧或参考媒体。 |
| `SUBMITTING` | 否 | 正在提交 Leonardo GraphQL 请求。 |
| `RETRY_WAIT` | 否 | 可重试错误后的退避等待。 |
| `SUBMIT_UNKNOWN` | 否 | 提交结果待恢复确认。 |
| `UPSTREAM_QUEUED` | 否 | 上游已接收并排队。 |
| `RUNNING` | 否 | 上游正在生成。 |
| `COMPLETED` | 是 | 生成完成，读取 `output.media`。 |
| `FAILED` | 是 | 任务失败，读取顶层错误及 `output.error`。 |
| `CANCELED` | 是 | 任务在上游提交前被取消。 |

取消接口适用于 `QUEUED`、`WAITING_ACCOUNT`、`RETRY_WAIT`：

```bash
curl -X POST https://api-leo.clawsea.ai/v1/tasks/TASK_UUID/cancel \
  -H 'X-API-Key: local-api-key'
```

## 11. 完成响应

任务完成后，`output` 结构如下：

```json
{
  "output": {
    "media": [
      {
        "id": "53c46fb2-5d2e-42b5-818c-816958b594ef",
        "url": "https://cdn.leonardo.ai/path/generated-video.mp4",
        "type": "video/mp4",
        "width": 864,
        "height": 480,
        "gif_url": null,
        "thumbnail_url": "https://cdn.leonardo.ai/path/generated-thumbnail.jpg"
      }
    ],
    "provider": "leonardo",
    "generation_id": "1f191a8c-baf9-60d0-ba0c-998ad3edadac"
  },
  "status": "COMPLETED",
  "actual_credit_cost": 320,
  "error_code": null,
  "error_message": null
}
```

### 11.1 `output` 参数

`output` 会随任务阶段变化：上游刚接受时主要包含 `submit`，完成时包含 `media`，失败时包含 `error`。

| 字段 | 类型 | 何时有值 | 说明 |
| --- | --- | --- | --- |
| `output.submit` | object/null | 上游提交后 | Leonardo Generate mutation 的提交结果摘要。 |
| `output.submit.apiCreditCost` | integer/null | 上游提交后 | 上游在提交响应中给出的积分；供应商返回空值时为 `null`。 |
| `output.provider` | string/null | 完成或失败同步后 | 输出来源，当前为 `leonardo`。 |
| `output.generation_id` | string/null | 上游接受后 | 与顶层 `upstream_task_id` 对应的 generation ID。 |
| `output.media` | array/null | 完成时 | 生成媒体数组。 |
| `output.error` | object/null | 失败时 | 上游失败详情。 |

### 11.2 `output.media[]` 参数

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | Leonardo 媒体记录 ID。 |
| `url` | URL | 生成视频 MP4 地址。 |
| `type` | string | MIME 类型，通常为 `video/mp4`。 |
| `width` | integer/null | 成片像素宽度。 |
| `height` | integer/null | 成片像素高度。 |
| `gif_url` | URL/null | GIF 预览地址；供应商未返回时为 `null`。 |
| `thumbnail_url` | URL/null | 视频缩略图地址。 |

## 12. 失败响应

上游生成失败示例：

```json
{
  "output": {
    "error": {
      "code": "PROVIDER_MODERATION_ERROR",
      "nsfw": false,
      "flagged": false,
      "message": "The content of your generation was moderated by this Model. Try rewording your prompt, changing reference images or changing the Model. Your tokens have been credited back to your account.",
      "upstream_status": "FAILED",
      "note_type": "PROVIDER_FAILURE",
      "failure_reason": {
        "errorCode": "PROVIDER_MODERATION_ERROR"
      }
    },
    "submit": {
      "apiCreditCost": null
    },
    "provider": "leonardo",
    "generation_id": "UPSTREAM_GENERATION_ID"
  },
  "status": "FAILED",
  "actual_credit_cost": 0,
  "error_code": "PROVIDER_MODERATION_ERROR",
  "error_message": "The content of your generation was moderated by this Model. Try rewording your prompt, changing reference images or changing the Model. Your tokens have been credited back to your account."
}
```

处理时优先判断顶层 `status` 和 `error_code`，同时保留 `output.error` 供日志和排障使用。

### 12.1 `output.error` 参数

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | string | 稳定的供应商或任务错误码，通常与顶层 `error_code` 相同。 |
| `message` | string | 错误说明，通常与顶层 `error_message` 相同。 |
| `upstream_status` | string/null | Leonardo 返回的终态，例如 `FAILED`。 |
| `nsfw` | boolean/null | 上游内容审核的 NSFW 标记。 |
| `flagged` | boolean/null | 上游通用 flagged 标记。 |
| `note_type` | string/null | Leonardo `generation_notes.noteType`，存在已识别备注时返回。 |
| `failure_reason` | object/null | Leonardo `generation_notes.failureReason`，包含供应商错误码和可能的附加信息。 |

同步器按 Leonardo Web 相同的顺序处理失败备注：先匹配
`failureReason.errorCode`，再匹配 `noteType=CC_NSFW_TOTAL_FAILURE`，最后使用视频失败兜底文案。
支持的供应商错误码为 `PROVIDER_AUTHENTICATION_ERROR`、`PROVIDER_RATE_LIMIT`、
`PROVIDER_INTERNAL_ERROR`、`PROVIDER_INVALID_REQUEST`、`PROVIDER_MODERATION_ERROR`、
`PROVIDER_OUTPUT_ERROR`、`PROVIDER_TIMEOUT` 和 `ALL_PROVIDERS_FAILED`。

### 12.2 常见 HTTP 与任务错误

| HTTP/状态 | 错误码 | 说明 |
| --- | --- | --- |
| `401` | `INVALID_API_KEY` | `X-API-Key` 缺失或不匹配。 |
| `409` | `IDEMPOTENCY_CONFLICT` | 幂等键已用于不同请求。 |
| `404` | `TASK_NOT_FOUND` | 查询的任务 UUID 不存在。 |
| `422` | FastAPI validation detail | 字段类型、枚举、数量或模型分辨率校验失败。 |
| `FAILED` | `MEDIA_URL_INVALID` | 媒体 URL 协议或结构错误。 |
| `FAILED` | `MEDIA_URL_NOT_PUBLIC` | 媒体域名解析到非公网地址。 |
| `FAILED` | `MEDIA_TOO_LARGE` | 媒体超过配置大小。 |
| `FAILED` | `MEDIA_DURATION_INVALID` | 音频或视频参考时长超出 2–15 秒。 |
| `FAILED` | `MEDIA_TYPE_MISMATCH` | URL 内容与声明的媒体类型不一致。 |
| `FAILED` | `MEDIA_MODERATION_REJECTED` | 参考图片被上游审核拒绝。 |
| `FAILED` | `PROVIDER_MODERATION_ERROR` | 生成请求触发上游内容审核。 |
| `FAILED` | `UPSTREAM_GENERATION_FAILED` | 上游生成进入普通失败终态。 |
| `FAILED` | `TASK_RUNNING_TIMEOUT` | 上游运行超过配置的任务超时时间。 |

## 13. Python 提交与轮询示例

```python
import time
import uuid

import requests

base_url = "https://api-leo.clawsea.ai"
headers = {
    "X-API-Key": "local-api-key",
    "Idempotency-Key": f"seedance-{uuid.uuid4()}",
    "Content-Type": "application/json",
}
payload = {
    "provider": "leonardo",
    "model": "seedance-2.0-fast",
    "task_type": "VIDEO_GENERATION",
    "mode": "text-to-video",
    "input": {
        "prompt": "A paper kite above a meadow at sunrise.",
        "duration": 4,
        "resolution": "480P",
        "aspect_ratio": "16:9",
    },
    "estimated_credit_cost": 449,
}

created = requests.post(
    f"{base_url}/v1/tasks",
    headers=headers,
    json=payload,
    timeout=30,
)
created.raise_for_status()
task = created.json()

poll_headers = {"X-API-Key": headers["X-API-Key"]}
while task["status"] not in {"COMPLETED", "FAILED", "CANCELED"}:
    time.sleep(5)
    response = requests.get(
        f"{base_url}/v1/tasks/{task['task_uuid']}",
        headers=poll_headers,
        timeout=30,
    )
    response.raise_for_status()
    task = response.json()

if task["status"] == "COMPLETED":
    print(task["output"]["media"][0]["url"])
else:
    print(task["error_code"], task["error_message"])
```

## 14. 运行模式说明

本地 Compose 默认值为：

```text
VIDEO_SERVICE_UPSTREAM_MODE=mock
```

`mock` 用于验证任务队列、幂等、状态和积分闭环。真实 Leonardo 请求使用：

```text
VIDEO_SERVICE_UPSTREAM_MODE=leonardo
```

真实模式还依赖已同步的 Leonardo 账号、有效 Token、余额以及可公开下载的媒体 URL。

OpenAPI 页面：

```text
https://api-leo.clawsea.ai/docs
```
