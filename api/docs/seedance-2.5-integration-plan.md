# Seedance 2.5 接入方案

> 调研日期：2026-08-10
> 目标模型：`bytedance/seedance-2.5`
> 目标服务：`apps/api`（FastAPI、Worker、Syncer）与 `apps/web`（FRAME OPS 文档入口）

## 1. 结论

Seedance 2.5 可以沿用当前 Seedance 2.0 的媒体解析、上游资产上传、异步任务和积分预占链路，但应使用独立的 `seedance-2.5.v1` 输入契约，避免 2.0 的 15 秒上限、模型分辨率表和积分表影响 2.5。

首期接入以下三种业务模式：

1. `text-to-video`：文生视频。
2. `image-to-video`：首帧，及首帧加尾帧。
3. `reference-to-video`：Image / Video / Audio 多模态参考；兼容调用别名 `omni`、`omini`。

服务端固定：

- `quantity = 1`
- `public = false`
- `seed = -1`
- 调用方只传业务媒体 URL，不传 Leonardo Token、Asset ID 或 Cookie。

## 2. 浏览器实测能力

调研绑定的是 Leonardo AI Creation 中的 Seedance 2.5 页面。页面内嵌的官方模型卡描述为：

> Precise camera control, consistent scenes, and editable videos up to 30 seconds.

### 2.1 已确认参数

| 能力 | 浏览器确认值 | 接入值 |
|---|---|---|
| 模型 ID | `bytedance/seedance-2.5` | 原样提交 |
| 时长 | 滑块 `aria-valuemin=4`、`aria-valuemax=30`，键盘步长为 1 秒 | `4..30`，默认 `8` |
| 数量 | URL 为 `quantity=1` | 固定 `1` |
| 比例 | `21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16` | 六种全部开放 |
| 分辨率 | Standard / HD | 外部值 `480P` / `720P` |
| 音频 | Audio 开关 | `audio: boolean`，默认 `true` |
| 图片参考 | Image Reference，强度显示为 `LOW/MID/HIGH`，当前样本为 `MID` | 支持强度；旧 URL 列表按 `MID` 兼容 |
| 帧控制 | Start Frame、End Frame | 尾帧依赖首帧 |
| 视频参考 | Video Reference | 走现有视频探测与上传链路 |
| 音频参考 | Audio Reference | 走现有音频探测与上传链路 |

Seedance 2.5 的参考容量为 `30 图片 + 10 视频 + 10 音频`；图片保留输入顺序和 `LOW/MID/HIGH` 强度。该容量只应用于 2.5，Seedance 2.0 系列继续使用原有上限。

### 2.2 尺寸矩阵

| 比例 | Standard / 480P | HD / 720P |
|---|---:|---:|
| `21:9` | `992×432` | `1470×630` |
| `16:9` | `864×496` | `1280×720` |
| `4:3` | `752×560` | `1112×834` |
| `1:1` | `640×640` | `960×960` |
| `3:4` | `560×752` | `834×1112` |
| `9:16` | `496×864` | `720×1280` |

Standard 六项、HD 的 `21:9`、`16:9`、`1:1`、`9:16` 已在 2.5 控件中直接读取；HD `4:3`、`3:4` 与 Leonardo 当前 Seedance 对齐矩阵一致，真实请求测试时同时核验输出像素。

### 2.3 积分预估

浏览器样本带 1 张 Image Reference；Audio 开关切换前后报价相同。

| 分辨率 | 4 秒 | 8 秒 | 30 秒 | 规则 |
|---|---:|---:|---:|---|
| Standard / 480P | 720 | 1440 | 5400 | `180 × duration` |
| HD / 720P | 1168 | 2336 | 8760 | `292 × duration` |

观察记录：

- Standard、30 秒、Audio On：`5400`
- Standard、30 秒、Audio Off：`5400`
- HD、4 秒、Audio Off：`1168`
- HD、8 秒、Audio Off：`2336`
- HD、30 秒、Audio On / Off：均为 `8760`

初始定价版本建议为 `leonardo-ui-20260810.seedance25.v1`。浏览器报价用于排队前预算和账号选择；上游提交响应中的 `apiCreditCost` 继续作为结算权威值。

## 3. 对外请求契约

### 3.1 文生视频

```json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "bytedance/seedance-2.5",
  "mode": "text-to-video",
  "input": {
    "prompt": "A paper kite rises above a quiet meadow, cinematic camera movement.",
    "duration": 8,
    "resolution": "720P",
    "aspect_ratio": "16:9",
    "audio": true
  }
}
```

### 3.2 首尾帧

```json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "bytedance/seedance-2.5",
  "mode": "image-to-video",
  "input": {
    "prompt": "The camera slowly moves forward while daylight changes to sunset.",
    "duration": 8,
    "resolution": "720P",
    "aspect_ratio": "16:9",
    "audio": true,
    "image_url": "https://MEDIA_HOST/start.jpg",
    "end_image_url": "https://MEDIA_HOST/end.jpg"
  }
}
```

### 3.3 Omni 多模态参考

```json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "bytedance/seedance-2.5",
  "mode": "reference-to-video",
  "input": {
    "prompt": "Preserve the subject identity and follow the reference motion and sound rhythm.",
    "duration": 8,
    "resolution": "720P",
    "aspect_ratio": "1:1",
    "audio": true,
    "reference_images": [
      {"url": "https://MEDIA_HOST/subject.jpg", "strength": "MID"}
    ],
    "reference_video_urls": ["https://MEDIA_HOST/motion.mp4"],
    "reference_audio_urls": ["https://MEDIA_HOST/voice.wav"]
  }
}
```

兼容字段：

- 保留 `reference_image_urls: URL[]`，自动转换为 `reference_images` 且强度为 `MID`。
- `omni`、`omini` 在请求校验后统一归一为 `reference-to-video`。
- `end_image_url` 出现时必须同时提供 `image_url`。
- 首尾帧与 Omni 参考分属不同模式，不在同一个请求中混用。

## 4. 上游请求映射

目标请求骨架：

```json
{
  "model": "bytedance/seedance-2.5",
  "public": false,
  "parameters": {
    "prompt": "PROMPT",
    "duration": 8,
    "quantity": 1,
    "width": 1280,
    "height": 720,
    "seed": -1,
    "motion_has_audio": true,
    "guidances": {}
  }
}
```

映射规则：

| API 字段 | 上游字段 |
|---|---|
| `resolution + aspect_ratio` | 查尺寸表生成 `width + height` |
| `audio` | `motion_has_audio` |
| `image_url` | `guidances.start_frame[0].image` |
| `end_image_url` | `guidances.end_frame[0].image` |
| `reference_images` | `guidances.image_reference[]`，保留 `strength` 和 `order` |
| `reference_video_urls` | `guidances.video_reference_base[]` |
| `reference_audio_urls` | `guidances.audio_reference[]` |

`width`、`height` 作为尺寸权威字段。`mode=RESOLUTION_480/720` 是否随 2.5 请求提交，由首笔浏览器 Generate 的真实 GraphQL body 决定，不从 2.0 的模型分支直接推断。

## 5. 代码改造点

### 5.1 API schema

文件：`apps/api/src/video_task_service/schemas.py`

- 新增 `Seedance25InputBase`，`duration=4..30`、`resolution=480P|720P`、六种比例、`audio=true`。
- 新增 `Seedance25TextToVideoInput`、`Seedance25ImageToVideoInput`、`Seedance25ReferenceToVideoInput`。
- 图片参考改为带强度的对象，同时保留 URL 数组兼容入口。
- 对模型使用独立 `input_schema_version=seedance-2.5.v1`。
- OpenAPI 暴露三种模式的完整字段、范围和示例。

### 5.2 Provider builder

文件：`apps/api/src/video_task_service/seedance.py`

- 在模型表中增加 `bytedance/seedance-2.5`，不要把它改写为 `seedance-2.5`。
- 将模型能力改成表驱动：时长范围、分辨率、音频、参考上限、是否提交 legacy mode。
- 新建 `build_leonardo_seedance_25_request()` 或按能力表复用公共 guidance builder。
- `public=false`、`quantity=1`、`seed=-1` 固定在服务端。
- 保持输入 URL → 下载探测 → Leonardo Asset ID → Generate 的现有流程。

### 5.3 积分预算与账号选择

文件：`apps/api/src/video_task_service/pricing.py`、Worker 的任务领取逻辑。

```python
SEEDANCE_25_CREDITS_PER_SECOND = {
    "480P": 180,
    "720P": 292,
}
```

- 入队时写入 `estimated_credit_cost`。
- Worker 领取前用最新定价重新报价。
- 只从 `balance_credits >= estimated_credit_cost` 的可用账号中选择；继续遵守账号并发、Token 有效期和暂停状态。
- 领取后写 `reserved_credit_cost`，避免同一账号的并发任务重复占用余额。
- 提交成功后以 `apiCreditCost` 更新实际预留；终态写 `actual_credit_cost`。
- 上游退回积分或任务失败时释放预留，并保留账本事件。

### 5.4 Worker / Syncer

文件：`apps/api/src/video_task_service/worker.py`、`syncer.py`。

- 将 `seedance-2.5.v1` 加入类型化请求分发。
- 参考媒体沿用 ffprobe 预检；视频必须可探测到正时长且视频总时长不超过 30 秒，Seedance 2.5 不设置本地单视频时长区间或视频宽高上下限；单音频 2–30 秒且音频总时长不超过 30 秒，两类额度分别计算。
- Syncer 继续读取 generation、视频 URL、宽高、时长、音频轨、`apiCreditCost` 和失败原因。
- 任务详情保留最终上游请求摘要，但过滤 Token、Cookie 和本地临时路径。

### 5.5 文档与控制台

- 新增 `apps/api/docs/seedance-2.5-request-guide.md`。
- 镜像到 `apps/web/public/docs/seedance-2.5-request-guide.md`。
- 更新 Markdown Viewer 白名单和模型接入卡片。
- 文档示例使用线上 API 地址 `https://api-leo.clawsea.ai`。

## 6. 测试方案

### 6.1 单元与契约测试

1. `4/8/30 秒 × 480P/720P` 报价准确。
2. Audio On/Off 预算相同。
3. 六种比例映射到精确像素。
4. 文生、首帧、首尾帧、30 图片、10 视频、10 音频与混合 Omni 的 JSON 快照。
5. 3 秒、31 秒、1080P、尾帧缺首帧、超出参考数、无效 URL、调用方传 `public/quantity/token` 均在提交前返回 422。
6. 余额不足账号跳过，余额满足预算账号被选中；并发预留不会超卖。
7. OpenAPI 中出现模型 ID、三种模式和 `seedance-2.5.v1`。

### 6.2 线上真实冒烟矩阵

| 编号 | 模式 | 时长 | 档位 | 比例 | Audio | 预估积分 |
|---|---|---:|---|---|---|---:|
| S25-01 | 文生视频 | 4 | 480P | 16:9 | Off | 720 |
| S25-02 | 文生视频 | 4 | 720P | 9:16 | On | 1168 |
| S25-03 | 首帧 | 4 | 480P | 1:1 | On | 720 |
| S25-04 | 首尾帧 | 4 | 720P | 16:9 | On | 1168 |
| S25-05 | 图片参考 | 4 | 480P | 4:3 | On | 720 |
| S25-06 | 视频参考 | 4 | 720P | 3:4 | On | 1168 |
| S25-07 | 图片+视频+音频 Omni | 4 | 480P | 21:9 | On | 720 |
| S25-08 | 最大时长 | 30 | 480P | 16:9 | On | 5400 |

总预算：`11784` 积分。先跑 S25-01 至 S25-07；确认任务链路、积分和尺寸后再跑最大时长 S25-08。

每个任务验收：

- POST 返回 202，模型和 schema 版本准确。
- 状态完整经过 `QUEUED → RESOLVING_MEDIA → SUBMITTING → RUNNING → COMPLETED/FAILED`。
- 任务选择账号时余额满足预算。
- 输出 URL 可下载，MP4 可被 ffprobe 解析。
- 实际宽高等于请求尺寸；时长误差控制在 1 秒内。
- Audio On 有音频流；Audio Off 的轨道行为按上游实测记录。
- `estimated_credit_cost`、`reserved_credit_cost`、`actual_credit_cost` 与账号余额变化可对账。
- 失败任务释放积分预留并记录准确的 `error_code`、`error_message`。

## 7. 发布与回滚

1. 本地运行 API pytest、ruff；Web 运行 `npm ci` 与 build。
2. Compose mock 只验证状态机和 JSON 组装，报告中明确标记为 mock。
3. 使用真实 provider 模式跑 S25-01，再逐步放量到完整矩阵。
4. 生产发布仅面向 `101.47.13.14`，记录 release、Git commit、镜像摘要、健康检查和真实 task UUID。
5. 首批任务通过后再在模型接入页展示“LIVE”。
6. 回滚为恢复上一版 API/Web 工件并重启 API、Worker、Syncer；本方案不要求数据库迁移，已入队的 2.5 任务在回滚前停止领取并导出任务 UUID。

## 8. 发布闸门

以下证据齐全后进入生产：

- 浏览器真实 Generate GraphQL request/response，确认 `mode`、guidances 和参考数上限。
- 480P、720P 各一笔完成任务，输出像素与积分结算一致。
- 首尾帧和 Omni 各一笔完成任务。
- 30 秒任务完成且同步器不会提前超时。
- 线上 OpenAPI、请求指南、模型接入页和任务账本均可访问。

## 9. 参考

- Leonardo 当前模型卡：`/generate?model=bytedance/seedance-2.5`
- Leonardo Seedance 2.0 官方请求形状与尺寸矩阵：<https://docs.leonardo.ai/docs/seedance-20>
- 现有实现：`apps/api/src/video_task_service/seedance.py`
- 现有积分预占：`apps/api/src/video_task_service/pricing.py`
