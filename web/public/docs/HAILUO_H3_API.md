# MiniMax Hailuo H3 视频任务接入文档

> 文档版本：h3.v1
> 模型：hailuo-03
> 服务地址：https://api-leo.clawsea.ai
> Swagger：https://api-leo.clawsea.ai/docs

## 1. 接入概览

服务提供异步视频生成任务接口，支持：

1. text-to-video：文生视频。
2. image-to-video：图生视频，支持首帧和可选尾帧。
3. reference-to-video：参考生视频，支持图片、音频和视频 URL 参数。

图片、音频和视频参数直接提交公网 HTTP(S) URL。API 会持久化原始 URL；Worker 分配账号后，才下载媒体、检查格式与尺寸、上传第三方并转换成图片 ID 或 Media ID。

### 1.1 鉴权

所有业务接口携带：

~~~http
X-API-Key: YOUR_API_KEY
~~~

任务提交还需要幂等键：

~~~http
Idempotency-Key: YOUR_UNIQUE_REQUEST_KEY
~~~

幂等键长度为 8–128 字符。相同键和相同请求体返回原任务；相同键配合不同请求体返回 409 IDEMPOTENCY_CONFLICT。

### 1.2 公共请求结构

~~~json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "hailuo-03",
  "mode": "text-to-video",
  "input": {},
  "priority": 0,
  "estimated_credit_cost": 700
}
~~~

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| provider | string | 否 | 当前值为 leonardo |
| task_type | string | 否 | 当前值为 VIDEO_GENERATION |
| model | string | 是 | H3 使用 hailuo-03 |
| mode | string | 是 | 三种模式之一 |
| input | object | 是 | 模式对应的输入参数 |
| priority | integer | 否 | -100 至 100，默认 0 |
| estimated_credit_cost | integer | 否 | 按 H3 模型规则计算 | 类型化 H3 请求按 2K 基准和时长自动计算，用于预留账户积分；最终以 `actual_credit_cost` 为准。 |

---

## 2. 任务提交接口

### POST /v1/tasks

~~~bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: h3-request-0001' \
  -H 'Content-Type: application/json' \
  --data-binary @request.json
~~~

成功状态码：202 Accepted。

### 2.0 H3 积分报价（浏览器实测）

以下是 Leonardo 页面 `Generate` 按钮读取到的积分预览。H3 页面最低时长为 5 秒，时长范围为 5–15 秒；本次只读取报价，没有点击生成，因此不会产生新的扣费。H3 页面显示原生音频能力，未提供独立的 Audio 开关。

H3 当前页面只显示一个 `2K` 分辨率档位，没有单独的 1080P 或 4K 选择器。5 秒时，各比例的 2K 报价相同：

| 比例 | 页面尺寸 | 5 秒积分 |
| --- | ---: | ---: |
| `21:9` | `3360×1440` | **700** |
| `16:9` | `2560×1440` | **700** |
| `4:3` | `1920×1440` | **700** |
| `1:1` | `1440×1440` | **700** |
| `3:4` | `1440×1920` | **700** |
| `9:16` | `1440×2560` | **700** |

在 `2K + 16:9` 下按时长读取到：

| 时长 | 页面尺寸 | Generate 预览积分 |
| ---: | ---: | ---: |
| 5 秒 | `2560×1440` | **700** |
| 10 秒 | `2560×1440` | **1400** |
| 15 秒 | `2560×1440` | **2100** |

这些数值是当前账号和页面条件下的预览记录；后端按定价规则版本 `leonardo-ui-20260807.v2` 自动计算类型化任务的 `estimated_credit_cost`，并在提交时优先采用上游 `apiCreditCost`。任务完成后的 `actual_credit_cost` 是最终结算值；该值会写入账号积分结算流水。

账号分配前，Worker 会重新计算当前预算，只领取 `balance_credits - reserved_credits >= estimated_credit_cost` 且仍有并发槽位的活跃账号，并在行锁事务内立即增加预留；提交上游前还会刷新真实余额。没有符合条件的账号时，任务进入 `WAITING_ACCOUNT`。

### 2.1 文生视频

~~~json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "hailuo-03",
  "mode": "text-to-video",
  "input": {
    "prompt": "A white kitten chases a butterfly across a sunlit garden.",
    "duration": 5,
    "resolution": "2K",
    "aspect_ratio": "16:9"
  }
}
~~~

### 2.2 图生视频 / 首尾帧

~~~json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "hailuo-03",
  "mode": "image-to-video",
  "input": {
    "prompt": "A continuous cinematic transformation with a slow camera orbit.",
    "duration": 5,
    "resolution": "2K",
    "image_url": "https://cdn.example.com/start-frame.jpg",
    "end_image_url": "https://cdn.example.com/end-frame.jpg"
  }
}
~~~

- image_url：必填，首帧图片 HTTP(S) URL。
- end_image_url：可选，尾帧图片 HTTP(S) URL。
- 输出宽高依据首帧宽高比映射。
- 首尾帧在账号分配后分别转换为 start_frame 和 end_frame 图片 ID。

### 2.3 参考生视频

~~~json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "hailuo-03",
  "mode": "reference-to-video",
  "input": {
    "prompt": "Preserve the subject identity and follow the reference pacing.",
    "duration": 10,
    "resolution": "2K",
    "aspect_ratio": "16:9",
    "reference_image_urls": ["https://cdn.example.com/character.png"],
    "reference_audio_urls": ["https://cdn.example.com/voice.mp3"],
    "reference_video_urls": []
  }
}
~~~

约束：

- reference_image_urls：公共 schema 最多 9 条；当前 Leonardo 映射最多使用 5 张。
- reference_audio_urls：最多 3 条，需要搭配图片或视频参考。
- reference_video_urls：最多 3 条，第三方 video guidance 开关可用后接线。
- 所有参考媒体总数最多 12。
- 单类音频或视频组合时长最多 15 秒。
- 当前第三方落地分辨率为 2K。
- 媒体字段使用 HTTP(S) URL，data/base64 URI 会返回 422。

### 2.4 提交响应

~~~json
{
  "task_uuid": "4641529b-37a9-4a9d-bdf2-e318fa2ca698",
  "provider": "leonardo",
  "upstream_task_id": null,
  "model": "hailuo-03",
  "mode": "image-to-video",
  "input_schema_version": "h3.v1",
  "status": "QUEUED",
  "progress": {
    "phase": "QUEUED",
    "resolved_assets": 0,
    "total_assets": 2
  }
}
~~~

保存 task_uuid，后续使用它查询任务。

---

## 3. 任务查询接口

### GET /v1/tasks/{task_uuid}

~~~bash
curl 'https://api-leo.clawsea.ai/v1/tasks/4641529b-37a9-4a9d-bdf2-e318fa2ca698' \
  -H 'X-API-Key: YOUR_API_KEY'
~~~

成功状态码：200 OK。建议每 3–10 秒查询一次。

### 3.1 生成中响应

~~~json
{
  "task_uuid": "4641529b-37a9-4a9d-bdf2-e318fa2ca698",
  "upstream_task_id": "1f190d64-3730-6ad0-ab0d-0b93f35886d1",
  "status": "RUNNING",
  "progress": {
    "phase": "RUNNING",
    "resolved_assets": 2,
    "total_assets": 2
  },
  "output": null,
  "error_code": null,
  "error_message": null
}
~~~

### 3.2 完成响应

~~~json
{
  "task_uuid": "4641529b-37a9-4a9d-bdf2-e318fa2ca698",
  "upstream_task_id": "1f190d64-3730-6ad0-ab0d-0b93f35886d1",
  "status": "COMPLETED",
  "progress": {
    "phase": "COMPLETED",
    "resolved_assets": 2,
    "total_assets": 2
  },
  "output": {
    "provider": "leonardo",
    "generation_id": "1f190d64-3730-6ad0-ab0d-0b93f35886d1",
    "media": [{
      "url": "https://cdn.example.com/result.mp4",
      "type": "video/mp4",
      "width": 1440,
      "height": 1440
    }]
  },
  "error_code": null,
  "error_message": null
}
~~~

结果视频地址位于 output.media[0].url。

### 3.3 任务列表

~~~http
GET /v1/tasks?status=COMPLETED&limit=50&offset=0
~~~

---

## 4. 任务状态

| 状态 | 说明 |
|---|---|
| QUEUED | 已进入任务队列 |
| WAITING_ACCOUNT | 等待可用账号 |
| RESOLVING_MEDIA | 下载网络媒体并转换第三方 ID |
| SUBMITTING | 正在组装并提交第三方请求 |
| SUBMITTED / RUNNING | 第三方正在处理 |
| COMPLETED | 生成完成，output 含结果 |
| FAILED | 任务失败 |
| SUBMIT_UNKNOWN | 提交结果需要审计 |

进入 COMPLETED、FAILED、CANCELLED 或 SUBMIT_UNKNOWN 后停止轮询。

---

## 5. 错误处理

| 状态码 | 场景 |
|---|---|
| 401 | API Key 缺失或错误 |
| 404 | 任务 UUID 不存在 |
| 409 | 幂等键冲突 |
| 422 | 参数、URL scheme 或字段范围校验失败 |

任务级错误示例：

~~~json
{
  "status": "FAILED",
  "error_code": "MEDIA_DOWNLOAD_REJECTED",
  "error_message": "media server returned HTTP 403"
}
~~~

常见错误类型：

- MEDIA_*：URL、下载、尺寸、格式、时长或上传错误。
- UPSTREAM_*：第三方鉴权、限流、网络或 GraphQL 错误。
- PROVIDER_CAPABILITY_MISMATCH：公共参数超出当前第三方落地能力。
- PRE_SUBMIT_*：提交前账号余额或状态发生变化。

---

## 6. Python 轮询示例

~~~python
import time
import requests

BASE_URL = "https://api-leo.clawsea.ai"

payload = {
    "provider": "leonardo",
    "task_type": "VIDEO_GENERATION",
    "model": "hailuo-03",
    "mode": "text-to-video",
    "input": {
        "prompt": "A paper boat crosses a moonlit lake.",
        "duration": 5,
        "resolution": "2K",
        "aspect_ratio": "16:9",
    },
}

response = requests.post(
    f"{BASE_URL}/v1/tasks",
    headers={
        "X-API-Key": "YOUR_API_KEY",
        "Idempotency-Key": "h3-python-0001",
    },
    json=payload,
    timeout=30,
)
response.raise_for_status()
task_uuid = response.json()["task_uuid"]

terminal = {"COMPLETED", "FAILED", "CANCELLED", "SUBMIT_UNKNOWN"}

while True:
    result = requests.get(
        f"{BASE_URL}/v1/tasks/{task_uuid}",
        headers={"X-API-Key": "YOUR_API_KEY"},
        timeout=30,
    )
    result.raise_for_status()
    task = result.json()
    print(task["status"], task["progress"])
    if task["status"] in terminal:
        break
    time.sleep(5)

if task["status"] == "COMPLETED":
    print(task["output"]["media"][0]["url"])
else:
    print(task.get("error_code"), task.get("error_message"))
~~~
