# Lucas Database Sink

## 定位

Lucas Database Sink 是接入侧新增的独立写入出口。它不替代 SiYuan writer，也不直接写数据库文件。

当前数据主次关系：

- `ComposedCardV1`：知识卡主结构化对象。
- `composed_card.md` / `improved_card.md`：Markdown 展示视图，可写入 SiYuan。
- Lucas Database：后续结构化知识主库，只通过 API 接收 ingest 请求。

## 工具

```powershell
python tools/write_lucas_database.py --job-dir <job_dir>
```

默认配置：

`tools/write_lucas_database.py` 默认读取同一套存储配置：`config/storage.local.json` 保存 provider、Base URL 和 endpoint，项目根目录 `.env` 保存 API Key。不要在前端代码里写 key，也不要让命令行工具和 UI 使用两套不同配置。

如果 job 目录存在 `taxonomy_decision.json`，Brain ingest payload 会包含该分类决策，并优先用 `/知识卡/<recommended_path>/<title>` 生成 `target_path`。

payload 还会包含 `dedupe`：

- `source_url` / `final_url`：去掉常见追踪参数后的来源标识。
- `source_identity_hash`：用于同一来源去重。
- `content_fingerprint`：用于同一段内容或近似内容的候选复核。
- `on_exact_source_match`：建议数据库端优先保留/更新既有记录。
- `on_near_content_match`：建议数据库端保留既有记录，并把新内容标为候选复核。

配置方式：

1. 打开本地 UI：`http://127.0.0.1:3963/ui`。
2. 在“存储配置”里选择 `Lucas Database / Brain`。
3. 填写 Base URL、API Key / Token，然后保存。
4. 运行 `python tools/write_lucas_database.py --job-dir runtime/jobs/<job_id>`。

也可以用命令行做一次性覆盖，但这不写入本地配置：

```powershell
python tools/write_lucas_database.py `
  --job-dir runtime/jobs/<job_id> `
  --base-url http://127.0.0.1:8765 `
  --token <token>
```

后端 UI / 手动文本测试入口使用同一套存储配置：

```text
GET  /api/storage/config
POST /api/storage/config
POST /api/submit
```

API Key 可以通过 `/api/storage/config` 保存到本地 `.env`，变量名为 `LUCAS_DB_API_KEY`。后端响应只返回 `api_key_present`，不会把 key 回传到前端。`config/storage.local.json` 和 `.env` 都是本机配置文件，不应提交。

`POST /api/submit` 只用于手动文本和 Brain API 连通性测试。它会组装 `ComposedCardV1`，但卡片类型是 `temporary_card`，质量门禁标记为未通过；真实网页 / 视频 / 评论内容仍必须先经过 source reader、`tools/card_composer.py` 和 `tools/card_quality_gate.py`，再由 Storage Sink 保留正式卡或非正式卡状态。

## 读取的 job 产物

- `composed_card.json`：canonical `ComposedCardV1`。
- `quality_gate.json`：真实质量门控结果。
- `composer_input.json`：来源材料归一化结果。
- `composed_card.md` / `improved_card.md`：Markdown 渲染视图。
- `content.json`、`transcript.json`、`ocr.json`、`comments.json`：补充 source material。
- `result.json`：job 元信息和写入路径参考。

## Ingest 请求

工具会组装：

```json
{
  "card": {},
  "source_material": {},
  "quality_gate": {},
  "rendered_views": {
    "markdown": "",
    "plain_text": ""
  },
  "relations": [],
  "target_path": "/知识卡/<标签或分类>/<安全标题>",
  "actor": "agent"
}
```

第一版 `relations` 保持空数组。规则型关系建议由 Lucas Database 统一生成，接入侧只预留 AI 推理型关系提交位置。

## 写入策略

`tools/write_lucas_database.py` 默认是严格模式，写入前必须满足：

- `card.schema_name == "ComposedCardV1"`。
- `card.schema_version == "1"`。
- `card.composer_status == "success"`。
- `card.card_type == "formal_summary"`。
- `quality_gate.quality_gate_passed == true`。
- 找到可用 Markdown view。

任一条件失败时，不调用 Lucas Database API，只写本地失败回执。

测试阶段可由主链路传入 `--allow-non-formal`。此模式允许 `temporary_card`、`temporary_review_card`、`extracted_source_card`、`failure_card` 写入 Brain，但必须保留真实 `card_type`、`quality_gate.passed=false` 和低置信状态，不能冒充正式知识卡。

主链路使用 `config/link_pipeline.json` 中的：

- `storage_targets`：`["siyuan"]`、`["lucas_database"]` 或 `["siyuan", "lucas_database"]`。
- `lucas_database_write_policy`：`formal_only` 或 `all_cards`。

## 回执文件

每次运行都会在 job 目录保存：

```text
write_lucas_database_result.json
```

并保存本次组装的请求体：

```text
lucas_database_ingest_request.json
```

缺少 token 时结果示例：

```json
{
  "ok": false,
  "stage": "auth",
  "base_url": "http://127.0.0.1:8765",
  "status_code": null,
  "error": "Missing Lucas Database API key in storage configuration (LUCAS_DB_API_KEY). Save it in the storage settings UI or pass --token for a one-off run."
}
```

数据库未启动或不可达时，`stage` 为 `connect`，`error` 会说明 Lucas Database API unavailable。

## 不影响 SiYuan

本工具不调用 `tools/write_siyuan.py`，也不修改 SiYuan 工作空间文件。现有 SiYuan 写入仍由 `tools/run_link_job.py` 和 `tools/write_siyuan.py` 负责。
