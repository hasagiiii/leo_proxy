# Seed Audio 1.0 API 请求指南

本文档对应 Leonardo 声音模型 **Seed Audio 1.0**。公共模型 ID 为 `seed-audio-1.0`，输入 schema 为 `seed-audio-1.v1`，用于文字转语音。

> 参数来源：2026-08-09 在 Leonardo AI Creation 的 Seed Audio 1.0 页面逐项读取；Prompt 上限为 3000 字符，Speed/Volume 为 0.50–2.00，Pitch 为 -12–12，单次可生成 1–4 条。接口会固定 `public=false`，不会把生成结果公开。

## 1. 接口

- 服务地址：`https://api-leo.clawsea.ai`
- 创建任务：`POST /v1/tasks`
- 查询任务：`GET /v1/tasks/{task_uuid}`
- 鉴权：`X-API-Key`
- 幂等：创建任务必须传 `Idempotency-Key`，长度 8–128 字符。

## 2. 固定字段

| 字段 | 固定值 | 说明 |
| --- | --- | --- |
| `provider` | `leonardo` | 上游提供方 |
| `task_type` | `AUDIO_GENERATION` | 声音任务类型 |
| `model` | `seed-audio-1.0` | 公共模型 ID |
| `mode` | `text-to-speech` | 文字转语音 |
| `public` | `false` | Worker 内部固定，不允许调用方覆盖 |

## 3. 输入参数

`input` 对象支持以下字段，未列出的字段会返回 HTTP 422。

| 字段 | 类型 | 必填 | 默认 | 范围/格式 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `prompt` | string | 是 | — | 去除首尾空白后 1–3000 字符 | 需要朗读的文本 |
| `voice_id` | string | 否 | `zh_female_jitangnv_uranus_bigtts` | 1–128；`A-Z a-z 0-9 _ -` | 上游音色 ID；默认对应 Nadia |
| `speed` | number | 否 | `1.0` | 0.50–2.00，步长 0.05 | 语速倍率 |
| `volume` | number | 否 | `1.0` | 0.50–2.00，步长 0.05 | 音量倍率 |
| `pitch` | integer | 否 | `0` | -12–12 | 音高，单位为半音 |
| `quantity` | integer | 否 | `1` | 1、2、3、4 | 生成音频数量 |

### 3.1 Leonardo 页面可选音色

当前页面展示 20 个音色：`Cedric`、`Celeste`、`Corinne`、`Esther`、`Felix`、`Jean`、`Kian`、`Lyla`、`Mabel`、`Magnus`、`Mindy`、`Monkey King`、`Nadia`、`Opal`、`Pearl`、`Quentin`、`Sandy`、`Sophie`、`Tracy`、`Vivi`。

API 保存并透传 `voice_id`，便于上游新增音色时无需发布新的 schema。调用方应使用 Leonardo 当前返回的音色 ID；未指定时使用已验证的 Nadia ID。

## 4. 积分预算与账号分配

2026-08-09 的 Generate 预览为每条 **350 积分**，数量线性累加：

| `quantity` | 预估积分 |
| ---: | ---: |
| 1 | 350 |
| 2 | 700 |
| 3 | 1050 |
| 4 | 1400 |

创建任务时 API 会忽略调用方传入的 `estimated_credit_cost`，按 `350 × quantity` 写入预算。Worker 领取任务前再次报价，只选择满足以下条件的账号：

```text
balance_credits - reserved_credits >= estimated_credit_cost
```

分配时预留积分；上游返回 `apiCreditCost` 时以其为准更新预留；完成后写入 `actual_credit_cost` 和 `AccountCreditLedger` 的 `SETTLE` 记录。失败任务释放预留且实际积分为 0。

## 5. 创建任务示例

```bash
curl -X POST 'https://api-leo.clawsea.ai/v1/tasks' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: seed-audio-demo-0001' \
  -H 'Content-Type: application/json' \
  --data '{
    "provider": "leonardo",
    "task_type": "AUDIO_GENERATION",
    "model": "seed-audio-1.0",
    "mode": "text-to-speech",
    "input": {
      "prompt": "欢迎使用 FRAME OPS。Seed Audio 一点零任务已经创建。",
      "voice_id": "zh_female_jitangnv_uranus_bigtts",
      "speed": 1.0,
      "volume": 1.0,
      "pitch": 0,
      "quantity": 1
    }
  }'
```

HTTP 202：

```json
{
  "task_uuid": "27c28aa7-d57b-48f2-9516-a5b02a9dcd37",
  "task_type": "AUDIO_GENERATION",
  "model": "seed-audio-1.0",
  "mode": "text-to-speech",
  "input_schema_version": "seed-audio-1.v1",
  "status": "QUEUED",
  "estimated_credit_cost": 350,
  "reserved_credit_cost": 0,
  "actual_credit_cost": null,
  "progress": {
    "phase": "QUEUED",
    "resolved_assets": 0,
    "total_assets": 0
  }
}
```

## 6. 查询与输出

```bash
curl 'https://api-leo.clawsea.ai/v1/tasks/TASK_UUID' \
  -H 'X-API-Key: YOUR_API_KEY'
```

完成响应的 `output.media` 为有序音频数组；`quantity=1` 时包含一项：

```json
{
  "task_uuid": "27c28aa7-d57b-48f2-9516-a5b02a9dcd37",
  "status": "COMPLETED",
  "estimated_credit_cost": 350,
  "reserved_credit_cost": 350,
  "actual_credit_cost": 350,
  "output": {
    "provider": "leonardo",
    "generation_id": "UPSTREAM_GENERATION_ID",
    "media": [
      {
        "id": "UPSTREAM_MEDIA_ID",
        "type": "audio/mpeg",
        "url": "https://cdn.example.com/result.mp3"
      }
    ]
  }
}
```

客户端应以实际 `type` 为准；常见结果为 MP3。下载地址由 Leonardo CDN 提供，建议在业务侧及时转存。

## 7. 状态流转

```text
QUEUED
  -> CLAIMED
  -> RESOLVING_MEDIA
  -> SUBMITTING
  -> UPSTREAM_QUEUED / RUNNING
  -> COMPLETED | FAILED
```

Seed Audio 没有参考媒体，因此 `media_total=0`，`RESOLVING_MEDIA` 只负责构造类型化上游请求。

## 8. 校验错误

| 场景 | HTTP/任务错误 | 说明 |
| --- | --- | --- |
| 参数越界、未知字段、错误 task_type/mode | 422 | 请求不会入队 |
| 相同幂等键对应不同请求体 | 409 `IDEMPOTENCY_CONFLICT` | 换用新的幂等键 |
| 账号可用积分不足 | 任务进入 `WAITING_ACCOUNT` | 不会向上游发送必然失败的请求 |
| 音色 ID 被上游拒绝 | `UPSTREAM_*` | 使用当前音色目录中的 ID 重试 |
| 上游审核或生成失败 | `PROVIDER_*` / `UPSTREAM_GENERATION_FAILED` | 预留积分释放 |

## 9. 冒烟测试

仓库脚本：

```bash
# 108 组离线契约矩阵
python apps/api/scripts/smoke_seed_audio.py \
  --output-dir artifacts/seed-audio

# 线上真实任务（预算上限 350）
LEO_API_KEY='YOUR_API_KEY' \
python apps/api/scripts/smoke_seed_audio.py \
  --live \
  --base-url https://api-leo.clawsea.ai \
  --max-credits 350 \
  --output-dir artifacts/seed-audio-live
```

真实层检查 HTTP 202、schema、状态终态、音频 MIME、下载文件编码/采样率/时长，以及预估、预留、实际积分。

## 10. 生产验收记录

2026-08-09 已完成真实任务 `6a57b668-bc47-417e-8c2d-d35c48c94bc2`，上游 generation 为 `1f193f47-2ee5-6000-8d25-03ea31a318d3`。任务终态 `COMPLETED`，输出为 `audio/mpeg`；下载结果经 `ffprobe` 验证为 MP3、24 kHz、双声道、6.000 秒。预估、预留、实际积分均为 350，账本 `SETTLE` 记录 `credit_delta=-350` 并将预留从 350 释放为 0。

完整记录：[Seed Audio 1.0 线上真实测试报告](https://leo.clawsea.ai/docs/viewer.html?doc=seed-audio-1-smoke-report.md&release=frame-ops-v1.0.46)。
