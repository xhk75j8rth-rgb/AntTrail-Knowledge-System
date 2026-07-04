# GRAPH_DATA_MODEL.md / 图谱数据模型设计

## 主表

V0 使用 `node_relations` 存储图谱关系：

```sql
CREATE TABLE IF NOT EXISTS node_relations (
  id TEXT PRIMARY KEY,
  from_node_id TEXT,
  to_node_id TEXT,
  from_card_id TEXT,
  to_card_id TEXT,
  relation_type TEXT NOT NULL,
  label TEXT,
  source TEXT NOT NULL DEFAULT 'system_rule',
  confidence REAL NOT NULL DEFAULT 1,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
```

## 字段说明

- `id`：关系 ID，使用 `rel_` 前缀。
- `from_node_id` / `to_node_id`：Node 到 Node 或 Node 到其他端点的关系端点。
- `from_card_id` / `to_card_id`：Card 到 Card 或 Card 到其他端点的关系端点。
- `relation_type`：关系类型，例如 `HAS_CONCEPT`。
- `label`：中文显示标签。
- `source`：关系来源，例如 `card_mapping`、`manual`、`agent`。
- `confidence`：置信度，V0 默认 `1`。
- `metadata_json`：虚拟实体、来源字段、扩展信息。
- `deleted_at`：软删除时间，未删除为 `NULL`。

## 图谱节点返回格式

```json
{
  "id": "card:card_xxx",
  "entity_type": "card",
  "label": "AI 写小说实践",
  "source_id": "card_xxx",
  "metadata": {}
}
```

## 图谱边返回格式

```json
{
  "id": "rel_xxx",
  "from": "card:card_xxx",
  "to": "concept:concept_xxx",
  "relation_type": "HAS_CONCEPT",
  "label": "包含概念",
  "source": "card_mapping",
  "confidence": 1
}
```

结构边示例：

```json
{
  "id": "tree:node_parent:node_child",
  "from": "node:node_parent",
  "to": "node:node_child",
  "relation_type": "CONTAINS",
  "label": "包含",
  "source": "system_rule",
  "confidence": 1,
  "metadata": {
    "source_field": "nodes.parent_id",
    "structural": true
  }
}
```

## V0 设计取舍

- V0 不新增独立 entity 表，避免提前固化概念、标签、风险等实体模型。
- 虚拟实体 ID 由实体类型和 label 的 hash 生成，保证同名实体在图谱返回中稳定聚合。
- 关系删除先采用软删除，查询过滤 `deleted_at IS NULL`。
- Node 父子关系不写入 `node_relations`，由 `GET /api/graph` 根据 `nodes.parent_id` 查询时派生为 `CONTAINS` 结构边。
