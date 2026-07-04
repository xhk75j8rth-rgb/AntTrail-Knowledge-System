# API.md / API 文档

默认地址：`http://localhost:8765`

写入类接口需要请求头：

```http
Authorization: Bearer <LUCAS_DB_API_TOKEN>
```

未配置环境变量且没有数据库 Token 时，本地开发默认 Token 是 `lucas-local-dev-token`，来源标记为 `local_config`。

如果没有设置 `LUCAS_DB_API_TOKEN`，V0 也支持通过本地数据库生成 Token；如果环境变量已设置，则设置页会显示为环境变量锁定。

错误返回格式：

```json
{
  "ok": false,
  "error": {
    "code": "NODE_NOT_FOUND",
    "message": "Node not found",
    "request_id": "req_xxx"
  }
}
```

常见错误 code：`TOKEN_MISSING`、`TOKEN_INVALID`、`SCHEMA_INVALID`、`QUALITY_GATE_FAILED`、`DUPLICATE_CARD`、`OCR_FRAME_NOT_FOUND`、`EMBEDDING_PROVIDER_INVALID`、`VECTOR_STORE_INVALID`、`INTERNAL_ERROR`。

## GET /api/health

用途：检查 API 服务和 SQLite 是否可用。

请求示例：

```bash
curl http://localhost:8765/api/health
```

返回示例：

```json
{
  "ok": true,
  "name": "AntTrail Database",
  "version": "0.1.0",
  "database": {
    "ok": true,
    "path": "C:/path/to/lucas.db"
  },
  "auth": {
    "configured": true,
    "source": "env"
  },
  "indexing": {
    "embedding_provider": {
      "provider": "mock",
      "embedding_model": "mock-embedding-v0",
      "embedding_dim": 8
    },
    "vector_store": {
      "vector_store": "mock"
    }
  }
}
```

错误情况：数据库初始化失败时返回 `INTERNAL_ERROR`。

权限要求：不需要 Token。

## GET /api/settings/api-token

用途：读取本地 API 地址、Token 状态和当前 Token 来源。

请求示例：

```bash
curl http://localhost:8765/api/settings/api-token
```

返回示例：

```json
{
  "ok": true,
  "apiBaseUrl": "http://localhost:8765",
  "configured": false,
  "source": "local_config",
  "tokenPreview": null,
  "copyable": false
}
```

权限要求：不需要 Token。

## POST /api/settings/api-token/verify

用途：验证调用方当前保存的 Bearer Token 是否和 Lucas Database 服务端正在使用的认证来源匹配。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/settings/api-token/verify" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

返回示例：

```json
{
  "ok": true,
  "valid": true,
  "tokenPreview": "lucas_db_****777d",
  "source": "database"
}
```

`valid: false` 表示请求里的 Token 与服务端当前来源不匹配；`source` 会返回服务端当前认证来源，取值为 `env`、`local_config` 或 `database`。

错误情况：缺少 Bearer Token 时返回 `TOKEN_MISSING`。

权限要求：不需要预先通过写入鉴权。

## POST /api/settings/api-token/reset

用途：生成新的本地 API Token，并覆盖旧的数据库 Token；如果环境变量 `LUCAS_DB_API_TOKEN` 已设置，则返回 409。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/settings/api-token/reset" \
  -H "Content-Type: application/json" \
  -d '{}'
```

返回示例：

```json
{
  "ok": true,
  "apiBaseUrl": "http://localhost:8765",
  "token": "lucas_db_xxxxxxxxxxxxxxxxxx",
  "tokenPreview": "lucas_db_****1234"
}
```

权限要求：不需要 Token。

## GET /api/tree

用途：获取整棵未删除节点树，按 `sort_order` 和 `created_at` 排序。

请求示例：

```bash
curl http://localhost:8765/api/tree
```

返回示例：

```json
[
  {
    "id": "node_project_a",
    "parent_id": null,
    "title": "项目A",
    "type": "page",
    "children": []
  }
]
```

错误情况：数据库读取失败时返回 `INTERNAL_ERROR`。

权限要求：不需要 Token。

## POST /api/nodes

用途：创建根节点或任意节点下的子节点。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/nodes" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{
    "parent_id": null,
    "title": "技术方案",
    "content": "技术方案\n\n这里写方案内容。",
    "type": "page",
    "actor": "agent"
  }'
```

返回示例：

```json
{
  "id": "node_abc123",
  "parent_id": null,
  "title": "技术方案",
  "type": "page",
  "content": "技术方案\n\n这里写方案内容。",
  "created_at": "2026-06-28T12:00:00.000Z",
  "updated_at": "2026-06-28T12:00:00.000Z"
}
```

错误情况：`TOKEN_MISSING`、`TOKEN_INVALID`、`TITLE_REQUIRED`、`NODE_NOT_FOUND`、`INTERNAL_ERROR`。

权限要求：需要写入 Token。

## POST /api/nodes/:id/children

用途：在指定节点下快速创建子节点。前端树节点右侧 `+` 使用这个接口。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/nodes/node_abc123/children" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{
    "title": "未命名子节点",
    "content": "",
    "type": "page",
    "actor": "user"
  }'
```

返回示例同 `POST /api/nodes`。

权限要求：需要写入 Token。

## GET /api/nodes/:id

用途：读取节点详情、内容视图、直接子节点和附件列表。

请求示例：

```bash
curl http://localhost:8765/api/nodes/node_abc123
```

返回示例：

```json
{
  "id": "node_abc123",
  "parent_id": null,
  "title": "技术方案",
  "type": "page",
  "content": "技术方案\n\n这里写方案内容。",
  "children": [],
  "attachments": [
    {
      "id": "att_xxx",
      "node_id": "node_abc123",
      "original_name": "diagram.png",
      "mime_type": "image/png",
      "size_bytes": 12345,
      "kind": "image",
      "url": "/api/attachments/att_xxx/file",
      "created_at": "2026-06-28T12:05:00.000Z",
      "deleted_at": null
    }
  ],
  "created_at": "2026-06-28T12:00:00.000Z",
  "updated_at": "2026-06-28T12:00:00.000Z"
}
```

错误情况：`NODE_NOT_FOUND`、`INTERNAL_ERROR`。已软删除节点按不存在处理。

权限要求：不需要 Token。

## POST /api/nodes/:id/attachments

用途：给指定节点上传图片、视频或任意本地文件。文件保存在本机附件目录，SQLite 只保存元数据和相对路径。

请求类型：`multipart/form-data`

字段：

- `file` / 必填，要上传的本地文件。
- `actor` / 可选，默认 `user`。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/nodes/node_abc123/attachments" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -F "file=@/path/to/image.png" \
  -F "actor=agent"
```

返回示例：

```json
{
  "ok": true,
  "attachment": {
    "id": "att_xxx",
    "node_id": "node_abc123",
    "original_name": "image.png",
    "mime_type": "image/png",
    "size_bytes": 12345,
    "kind": "image",
    "url": "/api/attachments/att_xxx/file",
    "created_at": "2026-06-28T12:05:00.000Z",
    "deleted_at": null
  }
}
```

`kind` 由 MIME 类型派生，取值为 `image`、`video` 或 `file`。默认最大上传大小为 500MB，可用 `LUCAS_DB_MAX_UPLOAD_BYTES` 调整。

错误情况：`TOKEN_MISSING`、`TOKEN_INVALID`、`NODE_NOT_FOUND`、`ATTACHMENT_REQUIRED`、`ATTACHMENT_PATH_INVALID`、`INTERNAL_ERROR`。

权限要求：需要写入 Token。

## GET /api/attachments/:id/file

用途：读取或预览附件文件。图片和视频会在前端直接预览，其他文件可通过该 URL 下载或打开。

请求示例：

```bash
curl -L "http://localhost:8765/api/attachments/att_xxx/file" -o attachment.bin
```

错误情况：`ATTACHMENT_NOT_FOUND`、`ATTACHMENT_FILE_MISSING`、`INTERNAL_ERROR`。

权限要求：不需要 Token。

## GET /api/assets/:id

用途：读取由服务端自动保存的可浏览器访问资产。当前主要用于卡片入库时把 OCR 关键帧从本机 `frame_path` 转成前端可渲染的 `asset_url`。该接口和 `GET /api/attachments/:id/file` 读取同一份附件文件，只是 URL 更适合写入 Markdown 或 `source_material`。

请求示例：

```bash
curl -L "http://localhost:8765/api/assets/att_xxx" -o frame.jpg
```

权限要求：不需要 Token。

## DELETE /api/attachments/:id

用途：软删除附件记录。V0 不立即删除磁盘文件，避免误删后无法恢复。

请求示例：

```bash
curl -X DELETE "http://localhost:8765/api/attachments/att_xxx" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{"actor":"agent"}'
```

返回示例：

```json
{
  "ok": true,
  "deleted_at": "2026-06-28T12:10:00.000Z"
}
```

权限要求：需要写入 Token。

## PATCH /api/nodes/:id

用途：更新节点标题、内容视图，或同时更新两者。

请求示例：

```bash
curl -X PATCH "http://localhost:8765/api/nodes/node_abc123" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{
    "title": "技术方案 V2",
    "content": "技术方案 V2\n\n更新后的内容。",
    "actor": "agent"
  }'
```

返回示例：

```json
{
  "id": "node_abc123",
  "title": "技术方案 V2",
  "content": "技术方案 V2\n\n更新后的内容。",
  "updated_at": "2026-06-28T12:30:00.000Z"
}
```

错误情况：`TOKEN_MISSING`、`TOKEN_INVALID`、`NODE_NOT_FOUND`、`TITLE_REQUIRED`、`NO_FIELDS_TO_UPDATE`、`INTERNAL_ERROR`。

权限要求：需要写入 Token。

## POST /api/nodes/:id/append

用途：向节点内容视图末尾追加内容，不覆盖原文。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/nodes/node_abc123/append" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{
    "content": "\n\nAgent 执行记录\n今天完成了 API 设计。",
    "actor": "agent"
  }'
```

返回示例：

```json
{
  "id": "node_abc123",
  "content": "技术方案\n\nAgent 执行记录\n今天完成了 API 设计。",
  "updated_at": "2026-06-28T12:35:00.000Z"
}
```

错误情况：`TOKEN_MISSING`、`TOKEN_INVALID`、`NODE_NOT_FOUND`、`CONTENT_REQUIRED`、`INTERNAL_ERROR`。

权限要求：需要写入 Token。

## POST /api/write-by-path

用途：按 `/` 分隔路径自动创建中间节点，并对最后一级节点覆盖或追加内容视图。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/write-by-path" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{
    "path": "/项目A/技术方案/API设计",
    "content": "API 设计\n\n这里是 API 方案。",
    "mode": "overwrite",
    "actor": "agent"
  }'
```

返回示例：

```json
{
  "id": "node_api_design",
  "path": "/项目A/技术方案/API设计",
  "title": "API设计",
  "mode": "overwrite",
  "created": true,
  "updated_at": "2026-06-28T12:00:00.000Z"
}
```

错误情况：`TOKEN_MISSING`、`TOKEN_INVALID`、`PATH_REQUIRED`、`CONTENT_REQUIRED`、`INVALID_MODE`、`TITLE_REQUIRED`、`INTERNAL_ERROR`。

权限要求：需要写入 Token。

## GET /api/search

用途：搜索节点标题和内容视图，返回节点路径和内容片段。

请求示例：

```bash
curl "http://localhost:8765/api/search?q=数据库设计"
```

返回示例：

```json
[
  {
    "id": "node_db_design",
    "title": "数据库设计",
    "path": "/项目A/技术方案/数据库设计",
    "snippet": "这里讨论 SQLite、节点树和 Agent API..."
  }
]
```

错误情况：数据库读取失败时返回 `INTERNAL_ERROR`。空查询返回空数组。

权限要求：不需要 Token。

## POST /api/cards/ingest

用途：接收标准化后的 `ComposedCardV1`，保存结构化卡片、完整 `raw_json`、原始材料、质量门控、渲染视图，并挂载到 `target_path` 对应 Node。入库完成后会根据卡片结构重建规则型图谱关系，并默认把该 Card 自动加入后台向量索引队列。

请求体核心字段：

- `target_path` / 必填，卡片挂载到 Lucas Database 的 Node 路径。
- `card` / 必填，`ComposedCardV1` 主数据。
- `source_material` / 可选，来源材料；若包含 `metadata.job_id`，会作为幂等去重键。
- `quality_gate` / 必填，质量门结果；`quality_gate.passed` 必须为 `true`。
- `rendered_views` / 可选，Markdown、plain text 等展示视图。
- `relations` / 可选，接入 Agent 提交的推理型关系。
- `idempotency_key` / 可选，也可以用 `Idempotency-Key` 请求头传入；请求头优先级更高。

幂等更新：

- 如果 `Idempotency-Key` 或 `source_material.metadata.job_id` 命中已存在卡片，服务不会重复创建 Node/Card。
- 若本次请求的 `rendered_views` 与库中展示视图不同，服务会刷新 `card_rendered_views`，并返回 `updated: true`。
- 该刷新只更新展示视图和卡片 `updated_at`，不重建卡片结构、图谱关系或来源材料。

成功返回：

```json
{
  "ok": true,
  "data": {
    "card_id": "card_xxx",
    "node_id": "node_xxx",
    "path": "/Lucas Brain/示例卡片",
    "created": true,
    "updated": false,
    "indexing": {
      "auto_rebuild": {
        "started": true,
        "mode": "background",
        "target_type": "card",
        "target_id": "card_xxx",
        "queued_at": "2026-07-02T12:00:00.000Z"
      }
    }
  }
}
```

重复提交同一个幂等键时返回 `200`，`created` 为 `false`，并指向已经存在的卡片。

自动索引：

- `created === true` 或 `updated === true` 时，默认后台执行 `rebuildCardIndex(card_id)`。
- 后台任务会重新切块、生成 embedding，并写入当前 vector store。
- 可用 `LUCAS_AUTO_INDEX_ON_CARD_INGEST=false` 关闭自动入队。
- 如果幂等提交没有产生变化，返回 `indexing.auto_rebuild.started=false`、`reason="unchanged"`。

OCR 关键帧处理：

- 服务端会读取 `source_material.ocr_evidence_items` 或 `source_material.metadata.ocr_evidence_items`。
- 只处理 `image_worth_saving === true` 的项。
- 如果该项已有 Lucas Database 自己的 `asset_url: "/api/assets/att_xxx"`，服务端会复用该资产，不重复复制。
- 如果该项带有接入侧临时 HTTP `asset_url`（例如 intake 的 `/api/jobs/<job>/ocr-images/<file>`）且本机 `frame_path` 仍可读取，服务端会复制本机帧图到 Lucas Database 附件存储，生成新的 `asset_url: "/api/assets/att_xxx"` 和 `attachment_id: "att_xxx"`，并把 Markdown 里的临时 URL 一并改写为本地资产 URL。
- 如果只有本机 `frame_path`，服务端同样会复制文件到 Lucas Database 附件存储并生成 `asset_url` / `attachment_id`。
- 增强后的 OCR evidence 写入 `card_source_materials.metadata_json.ocr_evidence_items`；`frame_path` 仅保留作诊断字段。
- `rendered_views.markdown` 里的 `![OCR frame](<C:/...jpg>)` 会被改写成 `![OCR frame](/api/assets/att_xxx)`。
- 如果幂等命中既有卡片，OCR 资产化仍会运行：展示视图和最新来源材料 metadata 会刷新为 `/api/assets/...`，但不会重复创建 Node/Card 或重建图谱关系；后续重复提交会复用已保存的附件资产。

错误情况：`TOKEN_MISSING`、`TOKEN_INVALID`、`SCHEMA_INVALID`、`QUALITY_GATE_FAILED`、`DUPLICATE_CARD`、`OCR_FRAME_NOT_FOUND`、`PATH_REQUIRED`、`INTERNAL_ERROR`。

权限要求：需要写入 Token。

## GET /api/cards/:id

用途：读取卡片详情，包括主表、完整 JSON、知识块、列表项、评论信号、原始材料、质量门控、渲染视图和关系。

权限要求：不需要 Token。

## GET /api/nodes/:id/cards

用途：读取指定 Node 下挂载的卡片摘要，前端“卡片信息”面板使用这个接口。

权限要求：不需要 Token。

## GET /api/graph

用途：读取图谱数据。传入 `node_id` 且 `scope=local` 时，以该 Node 为起点返回相关 Node、Card、虚拟实体和关系；`scope=global` 时返回最近 Node/Card 组成的全库图谱视图。

请求示例：

```bash
curl "http://localhost:8765/api/graph?node_id=node_xxx&depth=1&scope=local"
curl "http://localhost:8765/api/graph?scope=global&depth=1"
```

返回：

```json
{
  "ok": true,
  "nodes": [],
  "edges": []
}
```

权限要求：不需要 Token。

参数：

- `node_id` / 可选。传入后以该 Node 为起点读取图谱。
- `scope` / 可选，`local` 或 `global`，默认 `local`。
- `depth` / 可选。V0 限制为 `0` 到 `3`，默认 `1`。

返回中的 `edges` 除了 `node_relations` 关系，还会包含由 `nodes.parent_id` 派生的 `CONTAINS` 结构边。

## POST /api/indexing/rebuild-node/:id

用途：读取指定 Node 的内容视图，按 Markdown 标题或长度生成 chunks，并用当前配置的 embedding provider 写入向量引用。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/indexing/rebuild-node/node_xxx" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

返回示例：

```json
{
  "ok": true,
  "job_id": "emb_job_xxx",
  "target_type": "node",
  "target_id": "node_xxx",
  "chunk_count": 3,
  "embedding_model": "mock-embedding-v0"
}
```

权限要求：需要写入 Token。

当 `LUCAS_EMBEDDING_PROVIDER=bge-m3` 时，`embedding_model` 会返回 `BAAI/bge-m3`。

## POST /api/indexing/rebuild-card/:id

用途：读取指定 Card 的 `raw_json`，使用 `CardAwareChunker` 按 `ComposedCardV1` 字段生成 chunks，并写入当前 provider 的向量引用。

权限要求：需要写入 Token。

## POST /api/indexing/rebuild-all

用途：批量重建 active Node / Card 的 chunks 和向量索引，解决检索覆盖率问题。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/indexing/rebuild-all" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "target_types": ["node", "card"]
  }'
```

可选字段：

- `target_types`：`["node"]`、`["card"]` 或 `["node","card"]`，默认二者都重建。
- `limit`：最多重建多少个 target，默认不限制。

返回示例：

```json
{
  "ok": true,
  "rebuilt": {
    "nodes": 68,
    "cards": 22
  },
  "target_count": 90,
  "chunk_count": 363,
  "embedding_model": "BAAI/bge-m3",
  "vector_store": "sqlite-vec",
  "errors": []
}
```

权限要求：需要写入 Token。

## GET /api/indexing/provider

用途：查看当前配置的 embedding provider 和 vector store，不触发 Python worker 或模型加载。

返回示例：

```json
{
  "ok": true,
  "embedding_provider": {
    "provider": "bge-m3",
    "embedding_model": "BAAI/bge-m3",
    "embedding_dim": 1024
  },
  "vector_store": {
    "vector_store": "sqlite-vec"
  }
}
```

权限要求：不需要 Token。

## GET /api/indexing/jobs

用途：查询索引任务。

可选参数：

- `status`
- `target_type`
- `target_id`
- `limit`，默认 50，最大 200

返回示例：

```json
{
  "ok": true,
  "jobs": []
}
```

权限要求：不需要 Token。

## GET /api/indexing/chunks

用途：查询某个 Node 或 Card 的活跃 chunks。

请求示例：

```bash
curl "http://localhost:8765/api/indexing/chunks?target_type=node&target_id=node_xxx"
```

返回示例：

```json
{
  "ok": true,
  "chunks": [
    {
      "id": "chunk_xxx",
      "target_type": "node",
      "target_id": "node_xxx",
      "chunk_type": "markdown_section",
      "chunk_text": "# 项目说明\n这是项目说明。",
      "chunk_index": 0,
      "text_hash": "sha256...",
      "metadata": {
        "heading": "项目说明",
        "heading_level": 1,
        "section_index": 0
      }
    }
  ]
}
```

权限要求：不需要 Token。

## GET /api/indexing/chunks/:id/vector

用途：查询某个 chunk 对应的向量引用。启用 BGE-M3 后，`embedding_model` / `embedding_dim` 会反映真实 provider；启用 sqlite-vec 后，`vector_store` 会返回 `sqlite-vec`。

返回示例：

```json
{
  "ok": true,
  "chunk_id": "chunk_xxx",
  "vector_store": "sqlite-vec",
  "vector_id": "sqlite_vec_vector_xxx",
  "embedding_model": "BAAI/bge-m3",
  "embedding_dim": 1024
}
```

权限要求：不需要 Token。

## POST /api/retrieval/vector

用途：用当前 embedding provider 生成 query embedding，调用当前 vector store 检索，并回 Lucas Database 补全 chunk、node、card、path 和 metadata。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/retrieval/vector" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "中文语义检索 BGE-M3 sqlite-vec",
    "limit": 5
  }'
```

返回示例：

```json
{
  "ok": true,
  "query": "中文语义检索 BGE-M3 sqlite-vec",
  "limit": 5,
  "embedding_provider": {
    "embedding_model": "BAAI/bge-m3",
    "embedding_dim": 1024
  },
  "vector_store": {
    "vector_store": "sqlite-vec"
  },
  "confidence": {
    "low_confidence": false,
    "reason": null,
    "best_distance": 0.68,
    "threshold": 0.9
  },
  "results": [
    {
      "chunk_id": "chunk_xxx",
      "distance": 0.68,
      "vector_store": "sqlite-vec",
      "vector_id": "sqlite_vec_vector_xxx",
      "embedding_model": "BAAI/bge-m3",
      "embedding_dim": 1024,
      "path": "/知识库/示例节点",
      "chunk": {
        "id": "chunk_xxx",
        "target_type": "node",
        "target_id": "node_xxx",
        "chunk_type": "markdown_section",
        "chunk_text": "# 示例\n正文片段",
        "chunk_index": 0,
        "text_hash": "sha256..."
      },
      "node": {
        "id": "node_xxx",
        "title": "示例节点",
        "type": "page",
        "path": "/知识库/示例节点"
      },
      "card": null,
      "metadata": {
        "chunk": {},
        "vector": {
          "sqlite_vec_table": "lucas_vec_embeddings_1024"
        }
      }
    }
  ]
}
```

权限要求：不需要 Token。

说明：

- 默认 `mock` provider + `mock` vector store 可直接跑通，适合测试。
- `LUCAS_EMBEDDING_PROVIDER=bge-m3` + `LUCAS_VECTOR_STORE=sqlite-vec` 时走真实 BGE-M3 + sqlite-vec KNN。
- `distance` 越小越相近。
- BGE-M3 高维向量默认低置信度阈值是 `0.9`，可用 `LUCAS_VECTOR_LOW_CONFIDENCE_DISTANCE` 覆盖。

## POST /api/retrieval/keyword

用途：在 active chunks、Node 标题、Card 标题上做精确关键词包含匹配，返回 hydrated chunk 结果。适合标题、专有名词、英文产品名等精确命中场景。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/retrieval/keyword" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "App Store Connect",
    "limit": 5
  }'
```

返回示例：

```json
{
  "ok": true,
  "query": "App Store Connect",
  "limit": 5,
  "retrieval_type": "keyword",
  "confidence": {
    "low_confidence": false,
    "reason": null,
    "best_score": 152
  },
  "results": [
    {
      "chunk_id": "chunk_xxx",
      "score": 152,
      "path": "/知识卡/AI/工程化/质量门禁/iPhone_App上架准备流程",
      "chunk": {
        "id": "chunk_xxx",
        "chunk_text": "AI 生成 iPhone App 后如何准备上架 App Store..."
      },
      "node": {
        "id": "node_xxx",
        "title": "iPhone_App上架准备流程"
      },
      "card": null,
      "metadata": {
        "chunk": {}
      }
    }
  ]
}
```

权限要求：不需要 Token。

## POST /api/retrieval/hybrid

用途：同时执行 vector retrieval 和 keyword retrieval，按 `chunk_id` 融合排序，返回可解释的 hybrid 检索结果。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/retrieval/hybrid" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "App Store Connect",
    "limit": 5
  }'
```

返回示例：

```json
{
  "ok": true,
  "query": "App Store Connect",
  "limit": 5,
  "retrieval_type": "hybrid",
  "fusion": {
    "vector_weight": 0.65,
    "keyword_weight": 0.35
  },
  "confidence": {
    "low_confidence": false,
    "reason": null,
    "best_score": 1,
    "threshold": 0.35
  },
  "results": [
    {
      "chunk_id": "chunk_xxx",
      "hybrid_score": 1,
      "path": "/知识卡/AI/工程化/质量门禁/iPhone_App上架准备流程",
      "signals": {
        "vector": {
          "rank": 1,
          "distance": 0.7773,
          "score": 1
        },
        "keyword": {
          "rank": 2,
          "score": 152,
          "normalized_score": 1,
          "exact_match": true,
          "exact_boost": 0.35
        }
      }
    }
  ]
}
```

说明：

- 默认融合权重：vector `0.65`，keyword `0.35`。
- 可用 `LUCAS_HYBRID_VECTOR_WEIGHT` 和 `LUCAS_HYBRID_KEYWORD_WEIGHT` 调整权重。
- 可用 `LUCAS_HYBRID_LOW_CONFIDENCE_SCORE` 调整低置信度阈值，默认 `0.35`。
- 如果 vector retrieval 自身低置信，hybrid 会把本轮 vector 排序贡献降为 `0`，避免低置信 vector-only 结果压过精确 keyword 命中。
- 当 keyword 命中完整 query 短语时，`signals.keyword.exact_match=true`，并追加 `exact_boost`，用于保护标题、日期、专有名词和精确短语检索。
- 如果 keyword 无命中且 vector 自身低置信，hybrid 也会标记 `low_confidence=true`。

权限要求：不需要 Token。

## POST /api/context/build

用途：基于 Hybrid Search 结果构建可直接给模型使用的上下文包。V0 会去重、按来源分组、保留 sources/signals，并按估算 token budget 截断。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/context/build" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "App Store Connect 上架 iPhone 应用需要准备什么",
    "limit": 6,
    "token_budget": 1800,
    "max_chunks_per_source": 2
  }'
```

返回示例：

```json
{
  "ok": true,
  "query": "App Store Connect 上架 iPhone 应用需要准备什么",
  "retrieval_type": "context",
  "token_budget": 1800,
  "estimated_tokens": 900,
  "confidence": {
    "low_confidence": false
  },
  "sources": [
    {
      "source_index": 1,
      "source_key": "card:card_xxx",
      "source_type": "card",
      "source_id": "card_xxx",
      "title": "AI 生成 iPhone App 后如何准备上架 App Store",
      "path": "/知识卡/AI/工程化/质量门禁/iPhone_App上架准备流程",
      "chunk_ids": ["chunk_xxx"],
      "best_score": 1
    }
  ],
  "blocks": [
    {
      "source_index": 1,
      "chunk_id": "chunk_xxx",
      "estimated_tokens": 220,
      "hybrid_score": 1,
      "signals": {}
    }
  ],
  "omitted": {
    "duplicate_chunks": 0,
    "duplicate_text": 0,
    "source_limit": 0,
    "token_budget": 0
  },
  "context_text": "[Source 1] ..."
}
```

说明：

- `token_budget` 默认 `1800`，范围 `200..12000`。
- `max_chunks_per_source` 默认 `3`，用于防止单一来源占满上下文。
- token 估算是轻量近似，不等同于具体模型 tokenizer。
- `sources` 保留来源追踪，`blocks` 保留 chunk 级别证据，`context_text` 是可直接塞给模型的文本。

权限要求：不需要 Token。

## GET /api/agent/retrieve/spec

用途：读取 Agent Retrieve API V0 的稳定调用契约，方便 OpenClaw、Hermes 等外部 Agent 先按通用 HTTP JSON 方式接入。

请求示例：

```bash
curl "http://localhost:8765/api/agent/retrieve/spec"
```

说明：

- 这是通用 HTTP JSON contract，不假设 OpenClaw/Hermes 的原生插件或工具清单格式。
- 如果要生成 OpenClaw/Hermes 原生 adapter manifest，需要提供它们的真实 schema 或软件资料后再实现。

权限要求：不需要 Token。

## POST /api/agent/retrieve

用途：Agent-facing 检索入口。它基于 Context Builder，把 query 转成 `context.text`、`sources`、`citations`、`confidence`、`answerability` 和 `warnings`，让外部 Agent 不需要自己调用 vector/keyword/hybrid/context 多个底层接口。

请求示例：

```bash
curl -X POST "http://localhost:8765/api/agent/retrieve" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "App Store Connect 上架 iPhone 应用需要准备什么",
    "limit": 6,
    "token_budget": 1800,
    "max_chunks_per_source": 2,
    "agent": "openclaw",
    "response_format": "messages"
  }'
```

请求字段：

- `query` / 必填，自然语言问题或检索词。
- `limit` / 可选，最多返回多少个 context blocks，默认 `12`，范围 `1..50`。
- `token_budget` / 可选，默认 `1800`，范围 `200..12000`。
- `max_chunks_per_source` / 可选，默认 `3`，范围 `1..10`。
- `agent` / 可选，调用方标签，例如 `openclaw` 或 `hermes`；V0 只记录到响应里，不改变排序。
- `response_format` / 可选，`default` 或 `messages`。传 `messages` 时会额外返回可直接给聊天模型使用的 messages 数组。

返回示例：

```json
{
  "ok": true,
  "api_version": "agent-retrieve-v0",
  "adapter": {
    "name": "lucas-agent-retrieve",
    "profile": "generic-http-json",
    "requested_agent": "openclaw",
    "native_adapter_status": "generic_contract_only"
  },
  "query": "App Store Connect 上架 iPhone 应用需要准备什么",
  "status": "ready",
  "answerability": {
    "can_answer": true,
    "reason": null
  },
  "confidence": {
    "low_confidence": false
  },
  "context": {
    "text": "[Source 1] ...",
    "estimated_tokens": 449,
    "token_budget": 1800,
    "source_count": 1,
    "block_count": 2
  },
  "sources": [
    {
      "source_index": 1,
      "citation_label": "[Source 1]",
      "source_type": "card",
      "source_id": "card_xxx",
      "title": "AI 生成 iPhone App 后如何准备上架 App Store",
      "path": "/知识卡/AI/工程化/质量门禁/iPhone_App上架准备流程",
      "chunk_ids": ["chunk_xxx"],
      "best_score": 1
    }
  ],
  "citations": [
    {
      "source_index": 1,
      "citation_label": "[Source 1]",
      "chunk_id": "chunk_xxx",
      "title": "AI 生成 iPhone App 后如何准备上架 App Store",
      "path": "/知识卡/AI/工程化/质量门禁/iPhone_App上架准备流程",
      "text": "证据片段...",
      "score": 1,
      "signals": {}
    }
  ],
  "warnings": [],
  "usage": {
    "context_field": "context.text",
    "citation_rule": "Cite evidence with citation_label such as [Source 1].",
    "low_confidence_rule": "If answerability.can_answer is false, ask for clarification or say no reliable Lucas Database match was found."
  }
}
```

权限要求：不需要 Token。

## POST /api/relations

用途：手动新增图谱关系。

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

用途：软删除手动或自动关系，设置 `node_relations.deleted_at`。

权限要求：需要写入 Token。

## POST /api/cards/:id/rebuild-graph

用途：根据卡片结构化数据重建规则型图谱关系。该接口只软删除并重建来源为 `card_mapping` 或 `system_rule` 的生成关系，不覆盖 `manual` 和 `agent` 关系。

权限要求：需要写入 Token。

## DELETE /api/nodes/:id

用途：软删除节点及其所有未删除子节点。

请求示例：

```bash
curl -X DELETE "http://localhost:8765/api/nodes/node_abc123" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{"actor":"agent"}'
```

返回示例：

```json
{
  "ok": true,
  "deleted_at": "2026-06-28T13:00:00.000Z",
  "deleted_ids": ["node_abc123", "node_child001"]
}
```

错误情况：`TOKEN_MISSING`、`TOKEN_INVALID`、`NODE_NOT_FOUND`、`INTERNAL_ERROR`。

权限要求：需要写入 Token。

## Agent 示例：按路径写入项目方案

```bash
curl -X POST "http://localhost:8765/api/write-by-path" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{
    "path": "/Lucas Brain/S26 Context Builder/技术方案",
    "content": "技术方案\n\n这里是 Agent 写入的方案。",
    "mode": "overwrite",
    "actor": "agent"
  }'
```

## Agent 示例：按路径追加开发日志

```bash
curl -X POST "http://localhost:8765/api/write-by-path" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LUCAS_DB_API_TOKEN" \
  -d '{
    "path": "/Lucas Brain/S26 Context Builder/开发日志/2026-06-28",
    "content": "\n\nAgent 记录\n完成了节点树和 API 初版。",
    "mode": "append",
    "actor": "agent"
  }'
```
