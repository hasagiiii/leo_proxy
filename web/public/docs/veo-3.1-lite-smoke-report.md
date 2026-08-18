# Veo 3.1 Lite 线上真实任务测试报告

测试日期：2026-08-09  
生产 API：`https://api-leo.clawsea.ai`  
能力版本：`frame-ops-v1.0.37`  
模型：`veo-3.1-lite`  
Schema：`veo-3.1-lite.v1`

## 1. 结论

Veo 3.1 Lite 已完成生产接入与真实上游任务验收：

- 文生视频与首尾帧图生视频均成功完成。
- `720P` 横屏输出为 1280×720，竖屏输出为 720×1280，和请求尺寸一致。
- 两个结果均为 H.264 MP4、4.000000 秒，并且在 `audio=false` 时没有音频流。
- 每个任务预估、预留、实际结算均为 120 积分；任务账本各写入一条 `SETTLE -120`。
- 不属于 Lite 合同的 `4K` 与 `reference-to-video` 在入队前返回 HTTP 422。
- 生产发布和回滚检查均通过。

## 2. 覆盖矩阵

契约测试覆盖 24 组有效价格组合：

```text
2 个比例 × 2 个分辨率 × 3 个时长 × 2 个音频开关 = 24
```

| 分辨率 | 音频 | 单价/秒 | 4 秒 | 6 秒 | 8 秒 |
| --- | --- | ---: | ---: | ---: | ---: |
| 720P | 关闭 | 30 | 120 | 180 | 240 |
| 720P | 开启 | 50 | 200 | 300 | 400 |
| 1080P | 关闭 | 50 | 200 | 300 | 400 |
| 1080P | 开启 | 80 | 320 | 480 | 640 |

`16:9` 与 `9:16` 使用相同积分。生产真实任务选择最低付费档位验证两种模式与两个方向；其余参数组合由自动化契约矩阵验证。

## 3. 真实任务结果

| 用例 | 模式 | 请求参数 | 任务 UUID | 上游 ID | 终态 | 输出检查 | 积分 |
| --- | --- | --- | --- | --- | --- | --- | ---: |
| 文生横屏 | `text-to-video` | 720P / 16:9 / 4s / 无音频 | `bf8feea3-1544-4aab-86e8-0cd3f34a26d7` | `1f193f3a-f597-6ed0-83e2-9bfd8f699402` | `COMPLETED` | H.264，1280×720，4.000000s，无音轨 | 120 |
| 首尾帧竖屏 | `image-to-video` | 720P / 9:16 / 4s / 无音频 / 首帧+尾帧 | `17ebba76-f129-4154-b194-9f846a449134` | `1f193f3f-1dd3-6c60-a813-dfc64b8633c6` | `COMPLETED` | H.264，720×1280，4.000000s，无音轨 | 120 |

积分字段均满足：

```text
estimated_credit_cost = reserved_credit_cost = actual_credit_cost = 120
```

生产只读账本复核：

```text
17ebba76...  SETTLE  -120  balance 6080 -> 5960  reserved 120 -> 0
bf8feea3...  SETTLE  -120  balance 5074 -> 4954  reserved 120 -> 0
```

合计实际消耗 240 积分。

## 4. 负向约束

| 请求 | HTTP | 结果 |
| --- | ---: | --- |
| `resolution=4K` | 422 | `veo-3.1-lite does not expose the 4K resolution tier` |
| `mode=reference-to-video` | 422 | `veo-3.1-lite does not expose the reference-to-video mode` |

这两项在任务持久化和账号分配前拒绝，不产生上游任务或积分预留。

## 5. 自动化验证

- Desktop：189 项测试通过。
- API：781 项测试通过。
- Ruff：全部检查通过。
- Web：Vite 生产构建通过，共转换 2171 个模块。
- Compose 配置校验通过。
- 生产 `frame-ops-api`、`frame-ops-worker`、`frame-ops-syncer` 均为 `active`。
- API `/health/ready` 与 Web 公网健康检查均通过。

## 6. 可复核工件

本机完整证据目录：

```text
/Users/m007/mul_key_chrome-leo/.secrets/production/20260809T130955Z-veo31-lite-smoke
```

目录包含提交响应、终态响应、下载 MP4、`ffprobe` JSON、SHA-256、积分账本、负向响应与回滚检查记录。关键结果 SHA-256：

```text
9de1cae6e4ec9f648610879f508533a9fbc0de59a74ea4ca5fd63db53597b454  text_720_landscape_4s_muted.mp4
67ab7d6a6537fd2c745729e609a3d63d0c38bc389413055b684ead9f948c5839  image_720_portrait_start_end_4s_muted.mp4
```

生产发布记录：

```text
/opt/frame-ops/deployments/frame-ops-v1.0.37-20260809T130851Z
```

已验证回滚目标：

```text
/opt/frame-ops/releases/frame-ops-v1.0.36-cf661c8f622e
```
