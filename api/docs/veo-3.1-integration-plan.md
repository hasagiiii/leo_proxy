# Veo 3.1 初始接入方案（历史设计记录）

> 日期：2026-08-09
> 初始目标模型：`veo-3.1-generate-001`
> 当前状态：主模型、Fast 和 Lite 的代码与请求指南已进入仓库；本文保留初始设计决策，
> 运行环境是否发布及供应商任务是否可用仍以当次部署验证记录为准。

## 1. 接入结论

第一阶段只接入 Leonardo 稳定模型 ID `veo-3.1-generate-001`，对外提供三种互斥模式：

| FRAME OPS 模式 | 上游能力 | 媒体输入 |
| --- | --- | --- |
| `text-to-video` | 文生视频 | 无 |
| `image-to-video` | 首帧 / 首尾帧生视频 | `image_url` 必填，`end_image_url` 可选 |
| `reference-to-video` | 图片参考生视频 | `reference_image_urls` 1–3 张 |

服务端固定参数：

- `provider=leonardo`
- `task_type=VIDEO_GENERATION`
- `quantity=1`
- `public=false`
- 上游使用 `POST https://cloud.leonardo.ai/api/rest/v2/generations`
- schema 版本使用 `veo-3.1.v1`

Leonardo 官方说明同一请求结构还适用于 `veo-3.1-fast-generate-001` 和 `veo-3.1-lite`，但本阶段不把它们注册成公共模型。Fast 与 Lite 的定价、真实输出和账号池行为需单独验收；其中 Lite 最高只支持 1080P，`image_reference` 只支持主模型 `veo-3.1-generate-001`。

官方依据：

- [Leonardo Veo 3.1 API 指南](https://docs.leonardo.ai/me/docs/veo-31)
- [Leonardo 模型弃用与变更](https://docs.leonardo.ai/me/docs/deprecations-changes)

## 2. 参数合同

### 2.1 顶层请求

```json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "veo-3.1-generate-001",
  "mode": "text-to-video",
  "input": {
    "prompt": "A white paper boat crosses a quiet lake at sunrise.",
    "duration": 4,
    "resolution": "720P",
    "aspect_ratio": "16:9",
    "audio": false
  }
}
```

### 2.2 `input` 字段

| 字段 | 类型 | 必填 | 默认值 | 约束与映射 |
| --- | --- | --- | --- | --- |
| `prompt` | string | 是 | — | 1–9999 字符；映射 `parameters.prompt` |
| `duration` | integer | 否 | `8` | 只接受 `4`、`6`、`8` |
| `resolution` | string | 否 | `720P` | `720P`、`1080P`、`4K`；映射精确宽高 |
| `aspect_ratio` | string | 否 | `16:9` | `16:9`、`9:16` |
| `audio` | boolean | 否 | `true` | 映射 `parameters.motion_has_audio` |
| `negative_prompt` | string | 否 | — | 最长 1000 字符 |
| `seed` | integer | 否 | — | `0`–`4294967295` |
| `image_url` | HTTP(S) URL | 图生视频是 | — | 首帧；只允许 `image-to-video` |
| `end_image_url` | HTTP(S) URL | 否 | — | 尾帧；只有首帧存在时才允许 |
| `reference_image_urls` | HTTP(S) URL[] | 参考模式是 | — | 1–3 张、有序；只允许 `reference-to-video` |
| `reference_strength` | string | 否 | `MID` | `LOW`、`MID`、`HIGH`；应用到每张参考图 |

调用方传入以下上游内部字段时返回 HTTP 422：`public`、`quantity`、`width`、`height`、`guidances`、`motion_has_audio`、上游媒体 ID。

第一阶段不混合首尾帧和普通参考图。这样可以复用现有三模式合同并保持幂等摘要稳定。后续如需同一任务同时带首尾帧与参考图，应新增独立模式和 schema 版本，而不是改变 `veo-3.1.v1` 的含义。

## 3. 尺寸矩阵

| 分辨率 | `16:9` | `9:16` |
| --- | ---: | ---: |
| `720P` | 1280×720 | 720×1280 |
| `1080P` | 1920×1080 | 1080×1920 |
| `4K` | 3840×2160 | 2160×3840 |

API 只接收 `resolution + aspect_ratio`，Worker 按上表生成上游 `width` 和 `height`。调用方不直接提交像素值，避免出现分辨率标签与宽高不一致。

## 4. 三种请求示例

### 4.1 文生视频

```bash
curl -X POST "${API_BASE_URL}/v1/tasks" \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: veo31-t2v-20260809-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider":"leonardo",
    "task_type":"VIDEO_GENERATION",
    "model":"veo-3.1-generate-001",
    "mode":"text-to-video",
    "input":{
      "prompt":"A white paper boat crosses a quiet lake at sunrise, slow cinematic dolly.",
      "duration":4,
      "resolution":"720P",
      "aspect_ratio":"16:9",
      "audio":false
    }
  }'
```

### 4.2 首尾帧生视频

```json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "veo-3.1-generate-001",
  "mode": "image-to-video",
  "input": {
    "prompt": "Move smoothly from the first composition to the final composition.",
    "duration": 6,
    "resolution": "1080P",
    "aspect_ratio": "9:16",
    "audio": true,
    "image_url": "https://cdn.example.com/veo-start.png",
    "end_image_url": "https://cdn.example.com/veo-end.png"
  }
}
```

### 4.3 图片参考生视频

```json
{
  "provider": "leonardo",
  "task_type": "VIDEO_GENERATION",
  "model": "veo-3.1-generate-001",
  "mode": "reference-to-video",
  "input": {
    "prompt": "Create a restrained premium product film using the ordered visual references.",
    "duration": 8,
    "resolution": "4K",
    "aspect_ratio": "16:9",
    "audio": true,
    "reference_image_urls": [
      "https://cdn.example.com/subject.png",
      "https://cdn.example.com/style.png",
      "https://cdn.example.com/product.png"
    ],
    "reference_strength": "HIGH"
  }
}
```

## 5. 上游请求映射

Worker 分配账号后下载、探测并上传网络图片，再把公共 URL 转换为 Leonardo 资产 ID。首尾帧请求应生成：

```json
{
  "model": "veo-3.1-generate-001",
  "public": false,
  "parameters": {
    "prompt": "...",
    "duration": 6,
    "motion_has_audio": true,
    "quantity": 1,
    "width": 1080,
    "height": 1920,
    "guidances": {
      "start_frame": [
        {"image": {"id": "START_MEDIA_ID", "type": "UPLOADED"}}
      ],
      "end_frame": [
        {"image": {"id": "END_MEDIA_ID", "type": "UPLOADED"}}
      ]
    }
  }
}
```

参考模式应生成：

```json
{
  "guidances": {
    "image_reference": [
      {
        "image": {"id": "REFERENCE_MEDIA_ID", "type": "UPLOADED"},
        "strength": "HIGH"
      }
    ]
  }
}
```

`end_frame` 缺少 `start_frame`、媒体数量超限、参考图次序丢失、资产类型不是 `UPLOADED`/`GENERATED` 时，在提交上游前终止并记录明确错误码。

## 6. 积分预算与账号选择

以下为 2026-08-09 在 Leonardo Generate 页面逐项切换后记录的 1 个视频预览积分。横竖屏价格一致；当前方案按分辨率、时长和音频开关报价，模式不加价。真实提交返回的 `apiCreditCost` 继续作为最终权威值。

### 6.1 完整积分表

| 音频 | 时长 | 720P | 1080P | 4K |
| --- | ---: | ---: | ---: | ---: |
| 关闭 | 4 秒 | 800 | 800 | 1600 |
| 关闭 | 6 秒 | 1200 | 1200 | 2400 |
| 关闭 | 8 秒 | 1600 | 1600 | 3200 |
| 开启 | 4 秒 | 1600 | 1600 | 2400 |
| 开启 | 6 秒 | 2400 | 2400 | 3600 |
| 开启 | 8 秒 | 3200 | 3200 | 4800 |

等价每秒单价：

| 分辨率 | 音频关闭 | 音频开启 |
| --- | ---: | ---: |
| 720P | 200/秒 | 400/秒 |
| 1080P | 200/秒 | 400/秒 |
| 4K | 400/秒 | 600/秒 |

实现时在 `pricing.py` 增加 `VEO_3_1_CREDITS_PER_SECOND` 并升级 `PRICING_RULE_VERSION`。扣费链路保持现有流程：

1. 创建任务时写入 `estimated_credit_cost`。
2. Worker 领取任务前按当前规则重新报价。
3. 只选择 `balance_credits - reserved_credits >= estimated_credit_cost` 且有并发槽位的活跃账号。
4. 分配账号与预留积分在同一数据库事务完成。
5. 上游返回 `apiCreditCost` 时校正预留值。
6. 终态写入 `actual_credit_cost` 与积分流水；失败任务释放预留。

上线门槛：三种模式的真实任务都必须验证 `apiCreditCost`。如果参考图或首尾帧模式存在额外费用，应以真实结果更新定价表后再开放流量。

## 7. 代码改动清单

| 路径 | 改动 |
| --- | --- |
| `apps/api/src/video_task_service/veo_3_1.py` | 新增模型识别、尺寸映射、请求构造器和三模式资产规则 |
| `apps/api/src/video_task_service/schemas.py` | 新增 Veo 输入模型、字段约束、模式校验和自动报价 |
| `apps/api/src/video_task_service/pricing.py` | 新增积分矩阵并升级规则版本 |
| `apps/api/src/video_task_service/api/tasks.py` | 为 Veo 任务写入 `veo-3.1.v1` |
| `apps/api/src/video_task_service/worker.py` | 注册 typed schema、媒体解析和 Veo 请求构造器分支 |
| `apps/api/tests/test_veo_3_1.py` | 参数、尺寸、请求映射、媒体顺序和异常合同测试 |
| `apps/api/tests/test_pricing.py` | 18 个积分组合、横竖屏同价和非法参数测试 |
| `apps/api/tests/test_worker_account_selection.py` | 余额边界、预留和无足额账号等待测试 |
| `apps/api/docs/veo-3.1-request-guide.md` | 对外 API 请求指南 |
| `apps/web/public/docs/veo-3.1-request-guide.md` | Web 可读取的文档副本 |
| `apps/web/public/docs/viewer.html` | 文档白名单与标题 |
| `apps/web/src/ModelDocsView.tsx` | 模型接入页入口 |

`upstream.py` 的 v2 提交与 `apiCreditCost` 读取逻辑可复用，不新增第二套上游客户端。

## 8. 自动化测试设计

### 8.1 合同与请求构造

合法参数矩阵：

- `3 时长 × 3 分辨率 × 2 比例 × 2 音频 = 36` 个基础组合；
- 每个组合验证精确 `width/height`、`motion_has_audio`、`quantity=1`、`public=false`；
- 文生、首帧、首尾帧、1 张参考图、3 张参考图；
- 参考强度 `LOW/MID/HIGH` 和参考图顺序；
- `negative_prompt`、边界 seed、默认值归一化。

拒绝用例：

- 时长不是 4/6/8；
- 未知分辨率或比例；
- 尾帧没有首帧；
- 文生模式携带媒体；
- 参考模式 0 张或超过 3 张；
- 同时携带首尾帧和普通参考图；
- 调用方覆盖固定上游字段；
- 非 HTTP(S) 媒体 URL。

### 8.2 积分与调度

- 18 个唯一积分组合全部断言；
- 两个比例对同一组合报价一致；
- 余额恰好等于预算时可分配；
- 余额少 1 分时进入 `WAITING_ACCOUNT`；
- 多任务并发时按 `balance - reserved` 选择账号；
- 上游实际积分高于或低于预算时正确修正预留；
- 成功、上游失败和媒体失败均生成正确积分流水。

### 8.3 输出验收

每个成功任务下载结果并使用 `ffprobe` 检查：

- 实际宽高与输入尺寸矩阵一致；
- 实际时长与请求档位一致；
- `audio=true` 存在音轨，`audio=false` 不存在音轨；
- 输出 URL 可访问且媒体类型为 `video/mp4`；
- API 的 `estimated_credit_cost`、上游 `apiCreditCost`、`actual_credit_cost` 与账本一致。

## 9. 真实任务冒烟矩阵

先运行本地 mock 回归，再在生产 Leonardo 模式提交 8 个代表性任务：

| 编号 | 模式 | 时长 | 分辨率 | 比例 | 音频 | 预算 |
| --- | --- | ---: | --- | --- | --- | ---: |
| VEO-01 | 文生 | 4s | 720P | 16:9 | 关 | 800 |
| VEO-02 | 文生 | 4s | 1080P | 9:16 | 关 | 800 |
| VEO-03 | 文生 | 4s | 4K | 16:9 | 关 | 1600 |
| VEO-04 | 文生 | 4s | 720P | 9:16 | 开 | 1600 |
| VEO-05 | 首帧 | 4s | 720P | 16:9 | 关 | 800 |
| VEO-06 | 首尾帧 | 4s | 720P | 9:16 | 关 | 800 |
| VEO-07 | 单参考图 | 4s | 720P | 16:9 | 关 | 800 |
| VEO-08 | 三参考图 | 4s | 720P | 9:16 | 关 | 800 |

计划预算合计 `8000` 积分。所有任务使用中性测试素材和唯一幂等键。报告记录任务 UUID、上游 generation ID、分配账号脱敏 ID、输入/输出尺寸、音轨、预估/预留/实际积分、终态和耗时。

## 10. 发布与回滚

发布顺序：

1. 合入 API 合同、构造器、定价和测试。
2. 运行 API pytest、ruff、Web build 和 Compose 配置检查。
3. 本地 `VIDEO_SERVICE_UPSTREAM_MODE=mock` 验证完整状态机；报告明确标注 mock 不是供应商真实验收。
4. 部署 API、Worker、Syncer 与 Web 文档到项目生产服务器 `101.47.13.14`。
5. 验证 `/health/ready`、OpenAPI、文档 URL 与任务提交接口。
6. 先执行 VEO-01；积分和媒体结果正确后再执行剩余 7 项。
7. 生成 `veo-3.1-smoke-report.md`，全部通过后开放模型。

回滚应恢复上一 API/Web 发布版本，并保留已创建任务、积分流水和原始请求证据。回滚后新 Veo 请求返回明确的未注册模型错误，既有终态任务仍可查询。

## 11. 完成标准

- `veo-3.1-generate-001` 三模式通过合同测试；
- 36 个基础参数组合和 18 个积分组合全部通过；
- 8 个生产真实任务有可下载结果或可解释的供应商终态；
- 输入尺寸、实际视频尺寸、时长、音轨和积分账本逐项一致；
- 低余额账号不会被选中，足额账号的预留与释放正确；
- 接入文档和测试报告可从 FRAME OPS 模型接入页直接打开；
- 发布与回滚命令均经过验证。
