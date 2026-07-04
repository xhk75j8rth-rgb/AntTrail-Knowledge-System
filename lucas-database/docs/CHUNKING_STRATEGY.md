# CHUNKING_STRATEGY.md / 切块策略

## 总原则

切块只服务于派生索引。Node / Card 主数据仍保存在 Lucas Database，chunk 可以随时重建。

每个 chunk 都保存：

- `target_type` / `target_id`：回到主数据。
- `chunk_type`：切块来源和语义类型。
- `chunk_index`：同一目标下顺序。
- `text_hash`：基于规范化文本的 SHA-256，稳定可重建。
- `metadata_json`：字段来源、标题层级、schema 信息等。

## MarkdownChunker

用于普通 Node 内容视图。

- 优先按 `#`、`##`、`###` 标题切分。
- 每个标题段落形成 `markdown_section` chunk。
- 没有标题时生成 `markdown_text` chunk，并按长度继续拆分。
- 单块目标约 800-1200 字符，当前上限约 1100 字符，重叠约 120 字符。
- 代码块按 fenced code block 作为整体块处理，避免主动在代码块内部切分。

V0 限制：复杂未闭合代码围栏不会崩溃，但可能让后续内容被当作同一个代码块处理。

## CardAwareChunker

用于 `ComposedCardV1`。

至少生成这些类型：

- `card_summary`：`display_title`、`one_sentence_summary`、`original_summary`。
- `core_points`：`core_points[]` 聚合。
- `knowledge_block`：`knowledge_blocks[]` 每个知识块单独生成。
- `methodology`：`methodology[]` 聚合。
- `application_suggestions`：`application_suggestions[]` 聚合。
- `follow_up_actions`：`follow_up_actions[]` 聚合。
- `reusable_value`：`reusable_value[]` 或 `reusable_values[]` 聚合。
- `risks`：`risks[]` 聚合。
- `evidence_quotes`：`evidence_quotes[]` 聚合。
- `comment_signals`：`comment_signals` 对象展开。

空字段不生成空 chunk。metadata 至少保留：

```json
{
  "schema_name": "ComposedCardV1",
  "schema_version": "1",
  "field": "knowledge_blocks",
  "field_index": 0
}
```

## 回到主数据

命中 chunk 后，通过 `chunks.target_type` 和 `chunks.target_id` 回到 `nodes` 或 `cards`。真实正文、结构化字段、附件和图谱关系仍以主表为准。
