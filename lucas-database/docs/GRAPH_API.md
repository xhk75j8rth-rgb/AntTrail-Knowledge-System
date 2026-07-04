# GRAPH_API.md / 图谱 API 设计

默认地址：`http://localhost:8765`

写入类接口继续使用：

```http
Authorization: Bearer <LUCAS_DB_API_TOKEN>
```

## GET /api/graph

用途：读取当前节点相关图谱，或读取最近 Node/Card 组成的全库图谱视图。

请求示例：

```bash
curl "http://localhost:8765/api/graph?node_id=node_xxx&depth=1&scope=local"
curl "http://localhost:8765/api/graph?scope=global&depth=1"
```

返回示例：

```json
{
  "ok": true,
  "nodes": [],
  "edges": []
}
```

参数：

- `node_id`：可选。传入后以该 Node 为起点读取图谱。
- `scope`：可选，`local` 或 `global`，默认 `local`。`local` 使用 `node_id` 作为起点；`global` 忽略 `node_id`，读取最近 Node/Card 和相关关系。
- `depth`：可选。V0 限制为 `0` 到 `3`，默认 `1`。

返回说明：

- `nodes[]` 包含真实 `node`、`card` 和由规则关系元数据派生的 `concept`、`tag`、`risk`、`action`、`method`、`source`、`model` 等虚拟实体。
- `edges[]` 包含 `node_relations` 中未软删除的图谱关系，也包含由 `nodes.parent_id` 派生的结构边 `CONTAINS`。

权限要求：不需要 Token。

## POST /api/relations

用途：手动新增关系。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/relations" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{
    "from_node_id": "node_a",
    "to_node_id": "node_b",
    "relation_type": "RELATED_TO",
    "label": "相关",
    "source": "manual",
    "confidence": 1
  }'
```

权限要求：需要写入 Token。

## DELETE /api/relations/:id

用途：软删除关系。

请求示例：

```bash
curl -X DELETE "http://localhost:8765/api/relations/rel_xxx" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN"
```

返回示例：

```json
{
  "ok": true,
  "id": "rel_xxx",
  "deleted_at": "2026-06-30T00:00:00.000Z"
}
```

权限要求：需要写入 Token。

## POST /api/cards/:id/rebuild-graph

用途：根据指定卡片重新生成规则型图谱关系。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/cards/card_xxx/rebuild-graph" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

权限要求：需要写入 Token。

## POST /api/cards/ingest

用途：接收接入 Agent 产出的 `ComposedCardV1`，写入 Node/Card/RenderedView，并生成规则型图谱关系。

请求体核心字段：

- `target_path`：卡片挂载到 Lucas Database 的 Node 路径。
- `card`：`ComposedCardV1` 主数据。
- `source_material`：可选来源材料。
- `quality_gate`：可选质量门结果。
- `rendered_views`：可选渲染视图，例如 Markdown。
- `relations`：可选 Agent 推理关系。

权限要求：需要写入 Token。
