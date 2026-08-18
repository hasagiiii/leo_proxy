# Seed Audio 1.0 线上真实测试报告

- 执行日期：2026-08-09
- 生产环境：`https://api-leo.clawsea.ai`
- 模型：`seed-audio-1.0`
- 模式：`text-to-speech`
- Schema：`seed-audio-1.v1`
- 能力版本：`frame-ops-v1.0.43` / `f0beeb27d4e807983360a3d170c4e561cd75ee8a`

## 1. 测试结论

Seed Audio 1.0 已在生产 `leonardo` 上游模式完成真实付费任务。请求、账号余额筛选、积分预留、上游生成、音频结果解析、下载探测和积分结算均通过。

| 检查项 | 结果 |
| --- | --- |
| 真实任务 | `6a57b668-bc47-417e-8c2d-d35c48c94bc2` |
| 上游 generation | `1f193f47-2ee5-6000-8d25-03ea31a318d3` |
| 终态 | `COMPLETED` |
| 输出 | 1 个 `audio/mpeg` |
| 文件 | MP3，48,621 bytes |
| 音频流 | MP3、24,000 Hz、双声道、6.000 秒 |
| 积分 | 预估 350 / 预留 350 / 实际 350 |
| 账本 | `SETTLE`，`credit_delta=-350`，预留 350 → 0 |

## 2. 请求参数

```json
{
  "provider": "leonardo",
  "task_type": "AUDIO_GENERATION",
  "model": "seed-audio-1.0",
  "mode": "text-to-speech",
  "input": {
    "prompt": "欢迎使用 FRAME OPS。Seed Audio 一点零真实任务测试完成。",
    "voice_id": "zh_female_jitangnv_uranus_bigtts",
    "speed": 1.0,
    "volume": 1.0,
    "pitch": 0,
    "quantity": 1
  }
}
```

上游请求固定 `public=false`。本次使用默认 Nadia 音色，数量 1。

## 3. 状态与输出验收

数据库事件按顺序记录：

```text
TASK_CREATED
  -> ACCOUNT_ASSIGNED
  -> PRE_SUBMIT_BALANCE_REFRESHED
  -> MEDIA_RESOLUTION_COMPLETED
  -> UPSTREAM_SUBMITTED
  -> UPSTREAM_RUNNING
  -> TASK_COMPLETED
```

Leonardo 对音频结果返回嵌套 `generated_images[].urls.asset`。生产解析器已读取该字段，并按 `.mp3` 后缀返回 `audio/mpeg`；结果下载后使用 `ffprobe` 验证为 24 kHz、双声道、6 秒 MP3。

## 4. 积分账本验收

分配账号前按 350 积分预算，并检查：

```text
balance_credits - reserved_credits >= 350
```

完成时任务字段为：

```text
estimated_credit_cost = 350
reserved_credit_cost  = 350
actual_credit_cost    = 350
```

对应账本终态记录：

```text
entry_type     = SETTLE
credit_delta   = -350
reserved_before = 350
reserved_after  = 0
```

## 5. 自动化回归

- Desktop：189/189
- API：907/907
- Ruff：通过
- Web：TypeScript + Vite 生产构建通过
- Seed Audio 契约脚本：覆盖 108 组 `speed` / `volume` / `pitch` / `quantity` 边界组合
- 公网健康：API `ready`，Web `ok`

本次只提交 1 个真实付费任务；输出解析修复复用了同一任务，没有重复生成或重复扣费。
