# LEO Proxy 本地运行与运维

## 前置条件

- Docker 与 Docker Compose
- Node.js/npm（仅用于安装、测试和构建 Web）
- 可访问 Leonardo 上游网络

## 首次启动

在 `leo_proxy/` 目录执行：

```bash
npm --prefix web ci
npm --prefix web run build
docker compose config --quiet
docker compose up -d --build
```

默认地址：

- Web：`http://127.0.0.1:28081`
- API：`http://127.0.0.1:28080`
- API 就绪探针：`http://127.0.0.1:28080/health/ready`
- Web 探针：`http://127.0.0.1:28081/console-health`

Compose 的默认口令仅用于隔离的本地开发环境。生产启动前必须通过 `LEO_PROXY_*`
环境变量覆盖 API Key、管理 Key、控制台密码、数据库密码和凭据主密钥。

## 常用检查

```bash
docker compose ps
curl -fsS http://127.0.0.1:28080/health/ready
curl -u "$LEO_PROXY_CONSOLE_USER:$LEO_PROXY_CONSOLE_PASSWORD" \
  -fsS http://127.0.0.1:28081/console-health
docker compose logs --tail=200 api worker syncer web
```

## 测试

```bash
docker compose run --rm api pytest -q
docker compose run --rm api ruff check src tests scripts
npm --prefix web test -- --run
npm --prefix web run build
npm --prefix web audit --omit=dev
```

## 上游模式

默认 `LEO_PROXY_UPSTREAM_MODE=leonardo`，会调用真实上游并消耗账号积分。仅在本地生命周期
测试时使用：

```bash
LEO_PROXY_UPSTREAM_MODE=mock docker compose up -d --build
```

Leonardo Schema 默认跟随当前 Web 版本 `latest`。如确需固定版本，可设置
`LEO_PROXY_LEONARDO_SCHEMA_VERSION`。

## Cookie ZIP 验收

在“账号池”选择“导入 Cookie ZIP”，选择目标空间并上传压缩包。验收标准：

- 批次进入 `COMPLETED`；
- 创建数与压缩包内有效账号数一致，失败数为 0；
- 账号变为 `ACTIVE` 且协议续签状态可用；
- 不在日志、截图或报告中复制 Cookie/Token。

## 停止与清理

停止服务并保留数据：

```bash
docker compose down
```

删除数据卷会永久移除导入账号和任务历史，不属于日常停止操作；只有明确需要重置测试环境时才执行。
