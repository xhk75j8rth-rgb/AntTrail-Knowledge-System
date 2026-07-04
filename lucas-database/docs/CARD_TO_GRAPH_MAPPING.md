# CARD_TO_GRAPH_MAPPING.md / 卡片到图谱映射规则

## 数据来源

卡片到图谱的规则映射只读取结构化数据：

- `cards.raw_json` 中的 `ComposedCardV1`
- `card_knowledge_blocks`
- `card_items`
- `card_source_materials`
- `card_comment_signals`
- `cards.node_id`
- `cards.model_provider`
- `cards.model_used`

不会读取或反解析 SiYuan Markdown，也不会从 Markdown 标题猜测图谱关系。

## 关系类型

| 关系类型 | 起点 | 终点 | 来源字段 |
| --- | --- | --- | --- |
| `MOUNTED_ON` | Card | Node | `cards.node_id` |
| `HAS_CONCEPT` | Card | Concept | `knowledge_blocks[].concept` / `card_knowledge_blocks.concept` |
| `TAGGED_AS` | Card | Tag | `tags[]` / `card_items.item_type = tag` |
| `HAS_RISK` | Card | Risk | `risks[]` / `card_items.item_type = risk` |
| `SUGGESTS_ACTION` | Card | Action | `follow_up_actions[]` / `card_items.item_type = follow_up_action` |
| `HAS_METHOD` | Card | Method | `methodology[]` / `card_items.item_type = methodology` |
| `DERIVED_FROM_SOURCE` | Card | Source | `source_title`、`source_url`、`comments_video_id`、`comments_job_id` |
| `GENERATED_BY_MODEL` | Card | Model | `model_provider`、`model_used` |

## 虚拟实体

Concept、Tag、Risk、Action、Method、Source、Model 在 V0 先作为虚拟图节点返回，不单独建实体表。

这些虚拟节点的信息保存在 `node_relations.metadata_json`：

```json
{
  "target_entity_type": "concept",
  "target_entity_id": "concept_xxx",
  "target_label": "结构化卡片",
  "source_field": "knowledge_blocks[].concept"
}
```

## 重建策略

`POST /api/cards/:id/rebuild-graph` 会软删除该卡片之前由 `card_mapping` 或 `system_rule` 生成的关系，并重新写入规则型关系。

手动关系、Agent 推理关系不会被重建覆盖。

## 关系来源

- `card_mapping`：Lucas Database 根据卡片结构生成。
- `system_rule`：系统规则生成，预留。
- `manual`：用户或本地 UI 手动创建。
- `agent`：接入 Agent 提交的推理型关系。
