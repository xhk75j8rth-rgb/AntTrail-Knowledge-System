# Ingest To Lucas Database Flow

## 当前链路

```text
Source Reader / Media Processing
-> tools/card_composer.py
-> tools/card_quality_gate.py
-> TaxonomyRouter / taxonomy_decision.json
-> rendered Markdown
-> tools/write_siyuan.py      # Markdown 展示 Sink
-> tools/write_lucas_database.py  # 结构化数据库 Sink
```

当前 `tools/run_link_job.py` 可以按 `storage_targets` 同时写 SiYuan 和 Lucas Database / Brain。Lucas Database 仍通过独立 sink `tools/write_lucas_database.py` 执行，避免把 API 细节混进读取、写卡和质量门禁。

## 手动完整流程

已有 job：

```powershell
python tools/write_lucas_database.py --job-dir runtime/jobs/<job_id>
```

只验证组装请求、不调用数据库：

```powershell
python tools/write_lucas_database.py --job-dir runtime/jobs/<job_id> --dry-run
```

使用真实数据库：

```powershell
python tools/write_lucas_database.py --job-dir runtime/jobs/<job_id>
```

这个命令默认读取“存储配置”里保存的 Brain Base URL 和 API Key。先在本地 UI 的“存储配置”里保存一次；命令行参数 `--base-url`、`--endpoint`、`--token` 只用于一次性覆盖。

手动文本测试：

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:3963/api/submit `
  -ContentType application/json `
  -Body '{"title":"demo","content":"manual note","tags":["手动文本"]}'
```

这个接口只用于手动文本和 Brain API 连通性测试。它会生成 `ComposedCardV1` 包装，但 `card_type=temporary_card`，`quality_gate.passed=false`。真实链接入库仍以 `run_link_job -> source reader/media processing -> card_composer -> card_quality_gate -> Storage Sink` 为准。

存储设置：

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:3963/api/storage/config `
  -ContentType application/json `
  -Body '{"provider_id":"lucas_database","base_url":"http://127.0.0.1:8765","api_key":"<token>"}'
```

## 数据来源映射

- `card`：直接读取 `composed_card.json`，保持 `ComposedCardV1` schema。
- `source_material.source_url`：优先来自 `composer_input.source.source_url`。
- `source_material.transcript`：优先来自 `composer_input.transcript.raw_transcript`。
- `source_material.ocr_text`：优先来自 `composer_input.ocr.merged_text`。
- `source_material.comments`：优先来自 `composer_input.comments.comment_items`。
- `quality_gate`：由 `quality_gate.json` 转为 `{ passed, quality_level, errors, warnings, raw_json }`。
- `taxonomy_decision`：如果 job 目录存在 `taxonomy_decision.json`，Brain 和 SiYuan 都优先使用该分类路径。
- `dedupe`：由规范化来源、来源 hash、标题 fingerprint 和内容 fingerprint 组成，供 Brain 判断同一来源更新、近似内容复核或保留既有卡。
- `rendered_views.markdown`：优先使用 `result.json` 记录的 `written_card_source`，否则回退到 `composed_card.md`。
- `rendered_views.plain_text`：由 `ComposedCardV1` 关键字段拼接。
- `target_path`：优先由 `taxonomy_decision.recommended_path` 加安全标题生成；缺失 taxonomy 时才回退到 tag 或 source type。

## 失败排查

- `stage=quality_gate`：严格模式下卡片不是正式 `ComposedCardV1`，composer 失败，gate 未通过，或缺少 Markdown。
- `stage=auth`：存储配置里缺少 Brain API Key。
- `stage=connect`：Lucas Database 未启动、地址错误或网络不可达。
- `stage=api_response`：数据库返回 4xx/5xx，常见是 token 错误、接口路径错误或 schema 不匹配。

失败不会删除 job 产物，也不会写入 SiYuan。

测试阶段主链路可配置：

```json
{
  "storage_targets": ["siyuan", "lucas_database"],
  "lucas_database_write_policy": "all_cards"
}
```

`all_cards` 会让临时卡、来源材料卡和失败卡也进入 Brain，但保留真实 `card_type` 与 `quality_gate.passed=false`。如果要回到只写正式卡，改为 `lucas_database_write_policy=formal_only`。

## 后续集成点

后续可以把存储目标暴露成 UI 选择，例如：

```powershell
storage_targets = ["siyuan"] | ["lucas_database"] | ["siyuan", "lucas_database"]
```

当前先由 `config/link_pipeline.json` 控制，UI 选择只需要写入同一配置语义。
