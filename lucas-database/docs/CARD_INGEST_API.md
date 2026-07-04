# CARD_INGEST_API.md / 卡片入库 API 文档

## POST /api/cards/ingest

用途：接收接入 Agent 产出的 `ComposedCardV1`，写入 Lucas Database 的 Node/Card/RenderedView，并生成规则型图谱关系。

写入类接口需要：

```http
Authorization: Bearer <LUCAS_DB_API_TOKEN>
```

推荐同时传入幂等键，避免同一个任务重试时重复写入：

```http
Idempotency-Key: <stable-job-or-card-key>
```

如果没有请求头，服务端会尝试使用 `source_material.metadata.job_id` 作为去重键。

请求体：

```json
{
  "target_path": "/Lucas Brain/示例卡片",
  "actor": "agent",
  "card": {
    "schema_name": "ComposedCardV1",
    "schema_version": "1.0",
    "display_title": "AI 写小说实践",
    "one_sentence_summary": "结构化总结一段创作实践。",
    "knowledge_blocks": [
      {
        "concept": "结构化卡片",
        "explanation": "把脏数据整理成可复用知识单元。"
      }
    ],
    "tags": ["AI", "写作"]
  },
  "source_material": {
    "metadata": {
      "job_id": "job_20260630_001"
    }
  },
  "quality_gate": {
    "passed": true
  },
  "rendered_views": {},
  "relations": []
}
```

返回：

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

## 入库后行为

- `target_path` 不存在时会自动创建 Node。
- Node 的 `content` 默认来自 `rendered_views.plain_text`；没有 plain text 时由卡片和 Markdown 视图派生。
- `cards.raw_json` 保存完整 `ComposedCardV1`。
- `card_rendered_views` 至少保存 `markdown` 和 `plain_text`。
- 如果 `source_material.ocr_evidence_items` 或 `source_material.metadata.ocr_evidence_items` 里有 `image_worth_saving: true` 且包含本机 `frame_path`，服务端会把该图片复制到 Lucas Database 附件存储，生成 `asset_url` 和 `attachment_id`，并把 Markdown / plain text 视图里的本机路径或接入侧临时 HTTP 图片 URL 替换成 Lucas Database 自己的 `/api/assets/...`。
- 幂等刷新既有卡片时也会执行 OCR 图片资产化，刷新 `card_rendered_views` 和最新 `card_source_materials.metadata_json.ocr_evidence_items`，但不重复创建 Node/Card 或重建图谱关系；后续重复提交会复用已保存的附件资产。
- 增强后的 OCR evidence 会写入 `card_source_materials.metadata_json.ocr_evidence_items`；原始 `frame_path` 仅保留作诊断，不作为前端渲染 URL。
- 入库后会调用卡片图谱重建逻辑，写入 `node_relations`。
- 入库创建或刷新卡片展示视图后，默认会把该 Card 加入后台向量索引队列，自动切块、生成 embedding 并写入当前 vector store；可用 `LUCAS_AUTO_INDEX_ON_CARD_INGEST=false` 关闭。
- 重复提交同一个 `Idempotency-Key` 或 `source_material.metadata.job_id` 时，不会重复创建卡片；返回已有卡片且 `created` 为 `false`。

## 边界

- 只接受 `schema_name = ComposedCardV1`。
- `quality_gate.passed` 必须为 `true`，否则返回 `QUALITY_GATE_FAILED`。
- `image_worth_saving: true` 的 OCR 帧如果没有已有 `asset_url` 且本机 `frame_path` 不存在，会返回 `OCR_FRAME_NOT_FOUND`，避免保存不可渲染的临时路径。
- 不从 SiYuan Markdown 反解析卡片或图谱。
- 接入 Agent 可以提交 `relations` 作为推理型关系，但规则型关系仍由 Lucas Database 统一生成。
