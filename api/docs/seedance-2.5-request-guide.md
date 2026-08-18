# Seedance 2.5 API 请求指南

模型：`bytedance/seedance-2.5`
Schema：`seedance-2.5.v1`
API Base URL：`https://api-leo.clawsea.ai`
任务类型：`VIDEO_GENERATION`

## 1. 支持范围

| 项目 | 支持值 |
|---|---|
| 模式 | `text-to-video`、`image-to-video`、`reference-to-video` |
| Omni 别名 | `omni`、`omini`，服务端归一为 `reference-to-video` |
| 时长 | `4–30` 秒，整数，默认 `8` |
| 分辨率 | `480P`、`720P` |
| 比例 | `21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16` |
| 音频 | `audio=true/false`，默认 `true` |
| 结果数量 | 固定 `1` |
| 可见性 | 固定 `public=false` |
| 图片参考 | 最多 30 张，强度 `LOW/MID/HIGH` |
| 视频参考 | 最多 10 个；必须可探测到正时长，合计不超过 30 秒；本地预检不限制单视频时长区间或宽高 |
| 音频参考 | 最多 10 个；单个 2–30 秒，合计不超过 30 秒；需同时存在图片或视频参考 |

## 2. 鉴权与幂等

每次请求携带：

```http
X-API-Key: YOUR_API_KEY
Idempotency-Key: seedance25-request-0001
Content-Type: application/json
```

相同 `Idempotency-Key` 和相同请求体返回原任务。相同键配不同请求体返回 HTTP 409。

## 3. 创建任务

```http
POST /v1/tasks
```

### 3.1 公共外层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `provider` | string | 否 | 固定使用 `leonardo` |
| `task_type` | string | 是 | `VIDEO_GENERATION` |
| `model` | string | 是 | `bytedance/seedance-2.5` |
| `mode` | string | 是 | 三种模式之一，Omni 别名也可提交 |
| `input` | object | 是 | 模式对应的业务参数 |
| `priority` | integer | 否 | `-100..100`，默认 0 |
| `estimated_credit_cost` | integer | 否 | 服务端按当前定价覆盖支持模型的估值 |

调用方请求体中禁止出现 Token、Cookie、Authorization、密码和 Leonardo Asset ID。

### 3.2 公共 input 字段

| 字段 | 类型 | 默认 | 约束 |
|---|---|---:|---|
| `prompt` | string | — | 必填，1–5000 字符 |
| `duration` | integer | 8 | 4–30 秒 |
| `resolution` | string | `720P` | `480P` / `720P` |
| `aspect_ratio` | string | `16:9` | 六种比例之一 |
| `audio` | boolean | true | 是否请求原生音频 |

### 3.3 文生视频

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: seedance25-text-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"VIDEO_GENERATION",
    "model":"bytedance/seedance-2.5",
    "mode":"text-to-video",
    "input":{
      "prompt":"A paper kite rises above a quiet meadow, cinematic camera movement.",
      "duration":8,
      "resolution":"720P",
      "aspect_ratio":"16:9",
      "audio":true
    }
  }'
```

### 3.4 首帧 / 首尾帧

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `image_url` | HTTP(S) URL | 是 | 首帧图片 |
| `end_image_url` | HTTP(S) URL | 否 | 尾帧；出现时必须同时提供首帧 |

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: seedance25-frames-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"VIDEO_GENERATION",
    "model":"bytedance/seedance-2.5",
    "mode":"image-to-video",
    "input":{
      "prompt":"A slow dolly forward while daylight changes to sunset.",
      "duration":8,
      "resolution":"720P",
      "aspect_ratio":"16:9",
      "audio":true,
      "image_url":"https://MEDIA_HOST/start.jpg",
      "end_image_url":"https://MEDIA_HOST/end.jpg"
    }
  }'
```

### 3.5 Omni 多模态参考

附加字段：

| 字段 | 类型 | 上限 | 说明 |
|---|---|---:|---|
| `reference_images` | object[] | 30 | `{url, strength}`，保留顺序 |
| `reference_image_urls` | URL[] | 30 | 兼容入口，统一按 `MID` |
| `reference_video_urls` | URL[] | 10 | 必须可探测到正时长，合计不超过 30 秒；本地预检不限制单视频时长区间或宽高；由 Worker 下载、探测并上传 |
| `reference_audio_urls` | URL[] | 10 | 单个 2–30 秒，合计不超过 30 秒；需要图片或视频参考陪同 |

`reference_images` 与 `reference_image_urls` 二选一。

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: seedance25-omni-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"VIDEO_GENERATION",
    "model":"bytedance/seedance-2.5",
    "mode":"reference-to-video",
    "input":{
      "prompt":"Preserve the subject identity and follow the reference motion.",
      "duration":8,
      "resolution":"720P",
      "aspect_ratio":"1:1",
      "audio":true,
      "reference_images":[
        {"url":"https://MEDIA_HOST/subject.jpg","strength":"HIGH"}
      ],
      "reference_video_urls":["https://MEDIA_HOST/motion.mp4"],
      "reference_audio_urls":["https://MEDIA_HOST/voice.wav"]
    }
  }'
```

## 4. 尺寸矩阵

| 比例 | 480P / Standard | 720P / HD |
|---|---:|---:|
| `21:9` | `992×432` | `1470×630` |
| `16:9` | `864×496` | `1280×720` |
| `4:3` | `752×560` | `1112×834` |
| `1:1` | `640×640` | `960×960` |
| `3:4` | `560×752` | `834×1112` |
| `9:16` | `496×864` | `720×1280` |

## 5. 积分规则

规则版本：`leonardo-ui-20260812.v15`

| 分辨率 | 每秒积分 | 4 秒 | 8 秒 | 30 秒 |
|---|---:|---:|---:|---:|
| `480P` | 180 | 720 | 1440 | 5400 |
| `720P` | 292 | 1168 | 2336 | 8760 |

以上是无视频参考（文生视频、首尾帧或仅图片参考）的基础报价。请求中只要包含
`reference_video_urls`，上游会增加 Video Reference 处理积分：`480P` 每秒增加
`90`，`720P` 每秒增加 `180`，因此含视频参考的总费率分别为 `270/s` 和
`472/s`。例如 `720P + 18 秒 + 视频参考` 的预估为 `8496`，而不是仅按基础
输出费率得到的 `5256`。视频数量不重复叠加该费率。

这项修正来自 2026-08-12 生产账本的 157 次可判定上游提交：按新费率计算后，
128 次已受理提交全部满足余额条件，29 次 `Insufficient tokens` 全部低于余额
条件；旧费率会把余额 `6979`、`8242`、`6638` 的账号错误地用于一笔实际需要
`8496` 积分的任务。

当前浏览器报价中 Audio On / Off 数值相同。任务入队和 Worker 领取前都会重新报价；账号必须满足：

```text
balance_credits - reserved_credits >= estimated_credit_cost
```

领取账号后在同一数据库事务中增加预留。上游 `apiCreditCost` 返回后更新预留和实际结算；失败、审核终止或上游退回积分时释放预留。

## 6. 创建响应

HTTP 202：

```json
{
  "task_uuid": "TASK_UUID",
  "idempotency_key": "seedance25-text-0001",
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "bytedance/seedance-2.5",
  "mode": "text-to-video",
  "input_schema_version": "seedance-2.5.v1",
  "status": "QUEUED",
  "estimated_credit_cost": 2336,
  "reserved_credit_cost": 0,
  "actual_credit_cost": null,
  "progress": {
    "phase": "QUEUED",
    "resolved_assets": 0,
    "total_assets": 0
  }
}
```

## 7. 查询任务

```bash
curl 'https://api-leo.clawsea.ai/v1/tasks/TASK_UUID' \
  -H 'X-API-Key: YOUR_API_KEY'
```

状态流转：

```text
QUEUED
  → CLAIMED / WAITING_ACCOUNT
  → RESOLVING_MEDIA
  → SUBMITTING
  → RUNNING
  → COMPLETED | FAILED
```

完成响应的核心字段：

```json
{
  "task_uuid": "TASK_UUID",
  "upstream_task_id": "GENERATION_ID",
  "status": "COMPLETED",
  "estimated_credit_cost": 2336,
  "reserved_credit_cost": 0,
  "actual_credit_cost": 2336,
  "output": {
    "provider": "leonardo",
    "generation_id": "GENERATION_ID",
    "media": [
      {
        "type": "video/mp4",
        "width": 1280,
        "height": 720,
        "url": "https://CDN_HOST/result.mp4"
      }
    ]
  }
}
```

## 8. 校验与错误

| HTTP / 任务错误 | 场景 |
|---|---|
| `422 INPUT_VALIDATION` | 时长、分辨率、比例、URL、参考数量或字段组合错误 |
| `409 IDEMPOTENCY_CONFLICT` | 相同幂等键对应不同请求体 |
| `WAITING_ACCOUNT` | 当前账号池可用余额低于任务预算 |
| `MEDIA_*` | 下载、格式、尺寸、帧率、时长或音轨预检失败 |
| `PROVIDER_CAPABILITY_MISMATCH` | 组装阶段发现模型和模式参数冲突 |
| `UPSTREAM_GRAPHQL_ERROR` | Leonardo GraphQL 调用错误 |
| `UPSTREAM_GENERATION_FAILED` | 上游已接受任务，生成阶段失败 |
| `PROVIDER_MODERATION_ERROR` | 模型审核终止；任务记录上游退回积分结果 |

参考视频预检：MP4/MOV、可探测到正时长、24–60 FPS，最多 10 个且视频总时长不超过 30 秒；Seedance 2.5 不设置本地单视频时长区间或视频宽高上下限。参考音频单个 2–30 秒，最多 10 个且音频总时长不超过 30 秒；音频和视频分别计算各自的 30 秒额度。

## 9. 固定字段

下列字段由 Worker 生成：

```json
{
  "model": "bytedance/seedance-2.5",
  "public": false,
  "parameters": {
    "quantity": 1,
    "seed": -1
  }
}
```

调用方控制 `prompt`、`duration`、`resolution`、`aspect_ratio`、`audio` 和业务媒体 URL。
