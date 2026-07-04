# CARD_STORAGE_MODEL.md / 结构化卡片存储模型

## 定位

结构化卡片是 Lucas Database 的知识主数据。接入 Agent 负责把网页、视频、OCR、评论等脏数据整理成 `ComposedCardV1`，Lucas Database 负责接收、校验、保存、索引、展示和生成规则型图谱关系。

## 主表

`cards` 保存卡片主信息和完整 `raw_json`：

- `id` / 卡片 ID，使用 `card_` 前缀。
- `node_id` / 卡片挂载的 Lucas Node。
- `schema_name` / V0 只接受 `ComposedCardV1`。
- `schema_version` / 卡片 schema 版本。
- `display_title` / 展示标题。
- `one_sentence_summary`、`original_summary` / 摘要。
- `source_title` / 来源标题。
- `idempotency_key` / 入库幂等键，来自 `Idempotency-Key` 请求头或 `source_material.metadata.job_id`。
- `model_provider`、`model_used` / 生成模型。
- `raw_json` / 完整 Canonical Card JSON。
- `deleted_at` / 软删除字段，V0 预留。

## 附表

- `card_knowledge_blocks`：保存 `knowledge_blocks[].concept` 等知识块字段。
- `card_items`：保存标签、风险、方法论、行动建议等列表项。
- `card_comment_signals`：保存评论信号和评论任务 ID。
- `card_source_materials`：保存来源 URL、标题、原始文本、转写文本、OCR 文本等。
- `card_quality_gates`：保存质量门结果。
- `card_rendered_views`：保存 Markdown、plain text 等展示视图。
- `card_events`：记录卡片入库和图谱重建事件。

## 边界

- Node 内容、Markdown 和 plain text 都是展示/检索视图，不是图谱主数据。
- SiYuan Markdown 只作为 MarkdownStorageSink / 展示型存储出口。
- 图谱关系来自结构化卡片和关系表，不从 SiYuan Markdown 反解析。
