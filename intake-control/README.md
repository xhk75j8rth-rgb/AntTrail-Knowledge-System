# Lucas Knowledge DB Lab

Lucas Knowledge DB Lab 是个人知识库内容入库中控系统。它把聊天入口、网页、视频、OCR、转写和评论材料归一化后，生成 `ComposedCardV1`，通过质量门控，再写入展示型或结构化存储 Sink。

## 当前主链路

```text
Chat Gateway / 微信桥 / debug CLI
-> MessageEvent
-> Handler
-> tools/run_link_job.py
-> 页面读取 / 转写 / OCR / 评论增强
-> tools/card_composer.py
-> tools/card_quality_gate.py
-> Storage Sink
```

## 数据主次

- `ComposedCardV1` 是主数据。
- SiYuan Markdown 是展示型 Sink。
- Lucas Database 是后续结构化知识主库。
- 生产自动化不依赖 MCP。

## MCP / Python 边界

- Python 脚本、HTTP API、固定命令参数和 `runtime/jobs` 文件是生产自动化契约。
- MCP 如果存在，只作为 debug-only / interactive-only 能力，用于人工排查、页面状态观察和 Codex 探索。
- 新增自动化能力必须优先落到本地脚本、HTTP endpoint 或 Storage Sink；不得把 MCP 授权弹窗、Playwright MCP、SiYuan MCP 或 Codex MCP 作为后台服务依赖。
- 不因为“看起来有两套能力”而直接删除 MCP。只有在确认没有交互调试价值、没有文档/测试引用、且已有 Python/HTTP 替代路径后，才单独清理。

## Lucas Database Sink

独立写入工具：

```powershell
python tools/write_lucas_database.py --job-dir runtime/jobs/<job_id>
```

配置：

通过本地 UI 的“存储配置”保存 provider、Base URL 和 API Key。前端不保存 API Key；后端把 secret 写入项目根目录 `.env`，把非 secret 配置写入 `config/storage.local.json`。

请求接口：

```text
POST /api/cards/ingest
```

本地回执：

```text
runtime/jobs/<job_id>/write_lucas_database_result.json
runtime/jobs/<job_id>/lucas_database_ingest_request.json
```

更多说明见：

- `docs/LUCAS_DATABASE_SINK.md`
- `docs/INGEST_TO_DATABASE_FLOW.md`

## 常用验证

```powershell
python -m unittest tests.test_lucas_database_sink
python tools/write_lucas_database.py --job-dir runtime/jobs/<job_id> --dry-run
```

`--dry-run` 只组装并保存 ingest 请求，不调用 Lucas Database。

## 手动文本测试

本地后端提供手动文本测试代理，前端不保存 API Key。这个入口只用于 Brain 连通性测试，不代表真实网页 / 视频主链路：

```text
POST http://127.0.0.1:3963/api/submit
```

存储设置接口：

```text
GET  /api/storage/config
POST /api/storage/config
```

API Key 写入项目根目录 `.env`，变量名为 `LUCAS_DB_API_KEY`。`.env` 和 `config/storage.local.json` 已在 `.gitignore` 中，不应提交。
