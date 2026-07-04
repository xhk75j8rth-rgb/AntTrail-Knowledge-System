# API Contract V1

本文件用于让 mobile-first Web UI / 未来手机 App 在设计阶段提前对齐后端边界。它描述“前端可以调用什么、不能碰什么、哪些接口已经存在、哪些只是近期目标或未来预留”。

## Scope

- 当前正式入口仍是后端 API。前端不能直接调用 `tools/run_link_job.py`、不能直接写 SiYuan、不能读取或保存 `SIYUAN_TOKEN`、不能读取或保存 DeepSeek / OpenAI / Claude 等模型 API Key。
- `Minimal Chat UI` 只是测试页，不是正式 UI。正式 Web UI 建议放在 `apps/web`；未来原生手机 App 放在 `apps/mobile`；后端服务后续可迁到 `apps/backend`。
- 本契约不把 MCP 写入生产链路。所有响应都应带 `used_mcp: false` 或等价字段。
- 当前 `POST /api/chat/messages` 是同步调用；真实链接处理可能阻塞较久。移动端 V1 设计应优先面向未来异步 job 模型。

## Interface Groups

### 已实现

- `GET /health`
- `GET /ui`
- `POST /api/chat/messages`
- `GET /api/system/status`

### 近期必须实现

- `GET /api/jobs`
- `GET /api/jobs/{job_id}`：当前有基础读取路由，但返回的是本地 job 原始文件包；移动端稳定契约仍需补齐。
- `GET /api/cards`
- `GET /api/cards/{card_id}`

### 未来预留

- `GET /api/search?q=...`
- `POST /api/jobs/{job_id}/retry`
- `POST /api/cards/{card_id}/sync-siyuan`
- `PATCH /api/cards/{card_id}`
- `POST /api/share/receive`
- `POST /api/intake/file`

## Common Error Shape

当前 FastAPI 未统一包装的错误可能是：

```json
{
  "detail": "Job not found"
}
```

移动端 V1 建议统一为：

```json
{
  "ok": false,
  "error": {
    "code": "job_not_found",
    "message": "Job not found",
    "stage": "load_job",
    "details": {}
  },
  "used_mcp": false
}
```

前端只展示 `error.message`，调试页可展示 `error.code` 和 `error.stage`。错误结构不得包含 token、API key、cookie、完整本地密钥路径或敏感账号信息。

## Implemented APIs

### GET /health

- Endpoint / 接口路径：`/health`
- Method / 请求方法：`GET`
- Purpose / 用途：确认 Chat Gateway API 是否存活。
- Frontend Screen / 对应前端页面：App 启动检查、System Status Screen。
- Request JSON / 请求 JSON：无。
- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "service": "chat_gateway",
  "used_mcp": false
}
```

- Status Values / 状态枚举：`ok=true` 表示 API 进程可响应；不代表模型、SiYuan、转写工具或 Storage Sink 可用。
- Error Shape / 错误结构：网络错误或 HTTP 5xx；未来统一为 `Common Error Shape`。
- Side Effects / 副作用：不写 SiYuan；不调用模型；不调用 `run_link_job.py`；不调用 MCP。
- Implemented Status / 实现状态：已实现。
- Notes / 备注：这是最轻量健康检查，不应替代 `GET /api/system/status`。

### GET /ui

- Endpoint / 接口路径：`/ui`
- Method / 请求方法：`GET`
- Purpose / 用途：返回当前 Minimal Chat UI 测试页。
- Frontend Screen / 对应前端页面：仅开发测试页；不是正式手机端 UI。
- Request JSON / 请求 JSON：无。
- Response JSON / 返回 JSON：不是 JSON，返回 `text/html`。
- Status Values / 状态枚举：HTTP `200` 表示 HTML 存在；HTTP `404` 表示 `ui/minimal_chat/index.html` 不存在。
- Error Shape / 错误结构：

```json
{
  "detail": "UI not found"
}
```

- Side Effects / 副作用：不写 SiYuan；不调用模型；不调用 `run_link_job.py`；不调用 MCP。
- Implemented Status / 实现状态：已实现。
- Notes / 备注：正式 UI 不应继续扩展这个页面，应新建 `apps/web`。

### POST /api/chat/messages

- Endpoint / 接口路径：`/api/chat/messages`
- Method / 请求方法：`POST`
- Purpose / 用途：移动端 / Web UI 向 Chat Gateway 发送用户消息，由后端生成 `MessageEvent` 并路由到 command、link 或 fallback handler。
- Frontend Screen / 对应前端页面：Chat Screen。
- Request JSON / 请求 JSON：

```json
{
  "channel": "my_chat_app",
  "conversation_id": "mobile-local",
  "sender_id": "lucas",
  "sender_name": "Lucas",
  "message_type": "text",
  "text": "https://v.douyin.com/example/",
  "dry_run": true,
  "timeout_sec": 900,
  "metadata": {
    "source": "mobile_first_web",
    "client_request_id": "client-20260630-001",
    "dry_run": true
  }
}
```

也支持 query 参数：

```text
POST /api/chat/messages?dry_run=true&timeout_sec=900
```

- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "reply_text": "已检测到链接：https://v.douyin.com/example/\nDry-run：未调用 run_link_job.py，未写入 SiYuan。",
  "job_id": null,
  "status": "dry_run",
  "handled_by": "link_handler",
  "error": null,
  "data": {
    "message_id": "8d9c2e8ab2d1453e9bc2d4cba7e4b55a",
    "url": "https://v.douyin.com/example/",
    "urls": ["https://v.douyin.com/example/"],
    "extra_url_count": 0,
    "runner_called": false,
    "write_skipped": true,
    "config_warnings": [],
    "config_preflight": {
      "ok": true,
      "status": "ready",
      "used_mcp": false,
      "warnings": []
    },
    "used_mcp": false
  }
}
```

- Status Values / 状态枚举：
  - command：`command_help`、`command_ping`、`command_unknown`
  - fallback：`normal_chat`、`job_status`、`unknown`
  - link dry-run：`dry_run`
  - link real-run：`completed_formal`、`completed_needs_model_review`、`completed_low_confidence`、`failed_but_recorded`、`failed_unrecorded`、`timeout`、`runner_invalid_output`、`failed`
  - URL 提取失败：`extract_url_failed`
- Error Shape / 错误结构：成功路由但业务失败时返回 `HandlerResponse.ok=false` 和 `error` 字符串；HTTP 参数错误返回 FastAPI `detail`；未来统一为 `Common Error Shape`。
- Side Effects / 副作用：
  - `dry_run=true`：不写 SiYuan；不调用模型；不调用 `run_link_job.py`；不调用 MCP。
  - `dry_run=false` 且消息包含链接：后端可能同步调用 `tools/run_link_job.py`，并进一步调用页面读取、转写、条件 OCR、评论读取、`tools/card_composer.py`、`tools/card_quality_gate.py` 和 SiYuan writer。
  - 普通 fallback / command：不写 SiYuan；不调用 `run_link_job.py`。
- Implemented Status / 实现状态：已实现。
- Notes / 备注：前端只能传消息和开关，不能选择本地脚本路径，不能传 token，不能直接调用 runner。链接和普通聊天响应会执行只读配置预检；如果模型 API Key、SiYuan Token、Lucas Database API Key 或写入目标缺失，`reply_text` 会在正文前追加“配置提醒”，并在 `data.config_warnings` / `data.config_preflight` 返回结构化状态。该预检不测试连接、不调用模型、不写入。

### GET /api/system/status

- Endpoint / 接口路径：`/api/system/status`
- Method / 请求方法：`GET`
- Purpose / 用途：给移动端展示后端、AI Layer、Storage Sink、SiYuan readiness、runtime 目录和能力边界。
- Frontend Screen / 对应前端页面：System Status Screen、Settings Screen。
- Request JSON / 请求 JSON：无。
- Response JSON / 返回 JSON：

```json
{
  "ok": false,
  "status": "degraded",
  "service": "chat_gateway",
  "api": {
    "version": "0.1.0",
    "health_ok": true
  },
  "runtime": {
    "jobs_dir_configured": true,
    "jobs_dir_readable": true,
    "runner_available": true
  },
  "ai": {
    "provider_id": "deepseek_compatible",
    "label": "DeepSeek",
    "kind": "ai",
    "protocol": "openai_compatible",
    "configured": false,
    "api_key_present": false,
    "api_key_env": "DEEPSEEK_API_KEY",
    "base_url_present": true,
    "base_url_label": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "status": "not_configured",
    "supports_vision": false,
    "supports_json_mode": true,
    "error": ""
  },
  "storage": {
    "active_provider": "lucas_database",
    "storage_targets": ["siyuan", "lucas_database"],
    "auto_ingest_enabled": true,
    "configured": false,
    "status": "not_configured",
    "targets": [
      {
        "provider_id": "siyuan",
        "label": "SiYuan",
        "configured": false,
        "api_key_present": false,
        "api_key_env": "SIYUAN_TOKEN",
        "status": "not_configured"
      },
      {
        "provider_id": "lucas_database",
        "label": "Lucas Database / Brain",
        "configured": false,
        "api_key_present": false,
        "api_key_env": "LUCAS_DB_API_KEY",
        "status": "not_configured"
      }
    ]
  },
  "config_warnings": [
    {
      "code": "ai_api_key_missing",
      "severity": "warning",
      "target": "ai",
      "title": "模型 API Key 未配置",
      "message": "当前模型提供方是 DeepSeek，但没有检测到 API Key。普通聊天和正式知识卡写作会失败。",
      "action": "在设置里的模型配置保存 API Key，或配置环境变量 DEEPSEEK_API_KEY。"
    }
  ],
  "capabilities": {
    "chat_gateway": true,
    "link_intake": true,
    "douyin_level3_transcript": true,
    "conditional_ocr": true,
    "comments_enhancement": "opportunistic",
    "formal_card_requires_composer_and_quality_gate": true
  },
  "used_mcp": false
}
```

- Status Values / 状态枚举：
  - system：`ok`、`degraded`、`failed`
  - provider：`connected`、`configured`、`not_configured`、`not_checked`、`failed`、`unknown`
- Error Shape / 错误结构：`Common Error Shape`。
- Side Effects / 副作用：只读检查；不写 SiYuan；默认不调用模型；不调用 `run_link_job.py`；不调用 MCP。若未来增加 provider live test，必须由独立测试接口触发。
- Implemented Status / 实现状态：已实现。
- Notes / 备注：当前实现只做本地配置存在性预检，不做 live provider 连接测试；只返回 `api_key_present` / `api_key_env` / 安全配置状态，不返回完整 API Key 或 token。

## Near-Term Required APIs

### GET /api/jobs

- Endpoint / 接口路径：`/api/jobs`
- Method / 请求方法：`GET`
- Purpose / 用途：列出近期 intake jobs，供任务列表页查看处理进度和结果。
- Frontend Screen / 对应前端页面：Job List Screen。
- Request JSON / 请求 JSON：无 body。建议 query：

```text
GET /api/jobs?status=processing&limit=20&cursor=optional-cursor
```

- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "items": [
    {
      "job_id": "20260630-153000-a1b2c3d4",
      "source_url": "https://v.douyin.com/example/",
      "source_type": "video/douyin",
      "title": "一个知识卡示例",
      "status": "processing",
      "phase": "transcribing",
      "card_type": null,
      "content_level": null,
      "created_at": "2026-06-30T15:30:00+08:00",
      "updated_at": "2026-06-30T15:31:20+08:00",
      "has_card": false,
      "write_ok": false,
      "error": "",
      "used_mcp": false
    }
  ],
  "next_cursor": null,
  "used_mcp": false
}
```

- Status Values / 状态枚举：
  - list filter：`queued`、`processing`、`completed`、`failed`、`needs_review`、`low_confidence`
  - phase：沿用 job runtime 状态，如 `accepted`、`fetching_page`、`transcribing`、`ocr_running`、`comments_checking`、`build_card`、`writing_siyuan`
- Error Shape / 错误结构：`Common Error Shape`。
- Side Effects / 副作用：只读 `runtime/jobs` 或未来数据库索引；不写 SiYuan；不调用模型；不调用 `run_link_job.py`；不调用 MCP。
- Implemented Status / 实现状态：未实现。
- Notes / 备注：列表页不应暴露本地绝对 `job_dir`；如需调试可返回 `debug.job_dir_label`。

### GET /api/jobs/{job_id}

- Endpoint / 接口路径：`/api/jobs/{job_id}`
- Method / 请求方法：`GET`
- Purpose / 用途：查看单个 job 的状态、读取材料、卡片生成、质量门禁和写入结果。
- Frontend Screen / 对应前端页面：Job Detail Screen。
- Request JSON / 请求 JSON：无。
- Response JSON / 返回 JSON（目标契约）：

```json
{
  "ok": true,
  "job": {
    "job_id": "20260630-153000-a1b2c3d4",
    "source_url": "https://v.douyin.com/example/",
    "source_type": "video/douyin",
    "status": "completed",
    "phase": "completed_formal",
    "title": "一个知识卡示例",
    "card_type": "formal_summary",
    "content_level": "Level 5 评论与互动增强级",
    "created_at": "2026-06-30T15:30:00+08:00",
    "updated_at": "2026-06-30T15:36:00+08:00",
    "used_mcp": false
  },
  "materials": {
    "content": {
      "status": "ok",
      "title": "页面标题",
      "author": "未读取到",
      "final_url": "https://www.douyin.com/video/123"
    },
    "transcript": {
      "status": "transcribed",
      "has_speech": true,
      "confidence": "medium",
      "excerpt": "这里只返回短摘录，不返回大段 transcript。"
    },
    "ocr": {
      "status": "skipped_not_needed",
      "frame_count": 0,
      "excerpt": ""
    },
    "comments": {
      "status": "comments_done",
      "comment_count": 12,
      "comments_hash": "sha256:mock"
    }
  },
  "composer": {
    "composer_status": "success",
    "model_provider": "deepseek_compatible",
    "model_used": "deepseek-chat"
  },
  "quality_gate": {
    "quality_gate_passed": true,
    "failed_checks": []
  },
  "storage": {
    "siyuan_write_ok": true,
    "write_path": "/知识卡/AI/一个知识卡示例",
    "doc_id": "mock-doc-id"
  },
  "card_id": "card_20260630_153000_a1b2c3d4",
  "used_mcp": false
}
```

- Status Values / 状态枚举：
  - job display：`queued`、`processing`、`completed`、`failed`、`needs_review`、`low_confidence`
  - runtime phase：`accepted`、`fetching_page`、`page_fetched`、`page_fetch_failed`、`transcribe_dry_run`、`transcribing`、`transcribed`、`ocr_running`、`ocr_skipped`、`ocr_done`、`comments_checking`、`comments_done`、`comments_skipped`、`build_card`、`card_built`、`writing_siyuan`、`completed_formal`、`completed_needs_model_review`、`completed_low_confidence`、`failed_but_recorded`、`failed_unrecorded`
- Error Shape / 错误结构：当前实现会返回 FastAPI `detail`，如 `Invalid job_id`、`Job not found`；未来统一为 `Common Error Shape`。
- Side Effects / 副作用：只读 job 文件；不写 SiYuan；不调用模型；不调用 `run_link_job.py`；不调用 MCP。
- Implemented Status / 实现状态：部分实现。
- Notes / 备注：当前代码已存在路由，但返回 `{ok, job_id, job_dir, status, result, used_mcp}`；移动端不应依赖 `job_dir` 和原始 `result` 结构。

### GET /api/cards

- Endpoint / 接口路径：`/api/cards`
- Method / 请求方法：`GET`
- Purpose / 用途：列出已生成的知识卡、临时复核卡和失败记录卡。
- Frontend Screen / 对应前端页面：Card List Screen。
- Request JSON / 请求 JSON：无 body。建议 query：

```text
GET /api/cards?type=formal_summary&limit=20&cursor=optional-cursor
```

- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "items": [
    {
      "card_id": "card_20260630_153000_a1b2c3d4",
      "job_id": "20260630-153000-a1b2c3d4",
      "title": "一个知识卡示例",
      "card_type": "formal_summary",
      "content_level": "Level 5 评论与互动增强级",
      "quality_level": "high",
      "source_url": "https://v.douyin.com/example/",
      "tags": ["AI", "个人知识库"],
      "created_at": "2026-06-30T15:36:00+08:00",
      "updated_at": "2026-06-30T15:36:00+08:00",
      "storage_status": "siyuan_synced",
      "used_mcp": false
    }
  ],
  "next_cursor": null,
  "used_mcp": false
}
```

- Status Values / 状态枚举：
  - card_type：`formal_summary`、`temporary_review_card`、`temporary_card`、`failure_card`
  - quality_level：`high`、`medium`、`low`
  - storage_status：`not_synced`、`siyuan_synced`、`siyuan_failed`、`database_synced`、`database_failed`
- Error Shape / 错误结构：`Common Error Shape`。
- Side Effects / 副作用：只读 `runtime/jobs`、未来数据库或卡片索引；不写 SiYuan；不调用模型；不调用 `run_link_job.py`；不调用 MCP。
- Implemented Status / 实现状态：未实现。
- Notes / 备注：V1 可以先从 `runtime/jobs/*/result.json` 派生列表，后续再接数据库索引。

### GET /api/cards/{card_id}

- Endpoint / 接口路径：`/api/cards/{card_id}`
- Method / 请求方法：`GET`
- Purpose / 用途：查看单张知识卡的结构化内容、渲染 Markdown、来源 job、质量门禁和写入状态。
- Frontend Screen / 对应前端页面：Card Detail Screen。
- Request JSON / 请求 JSON：无。
- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "card": {
    "card_id": "card_20260630_153000_a1b2c3d4",
    "job_id": "20260630-153000-a1b2c3d4",
    "schema_name": "ComposedCardV1",
    "schema_version": "1",
    "card_type": "formal_summary",
    "content_level": "Level 5 评论与互动增强级",
    "quality_level": "high",
    "display_title": "一个知识卡示例",
    "one_sentence_summary": "这是移动端可展示的一句话总结。",
    "core_points": ["观点一", "观点二", "观点三"],
    "knowledge_blocks": [
      {
        "concept": "概念一",
        "explanation": "说明",
        "evidence": "短证据",
        "reusable_value": "可复用价值"
      }
    ],
    "application_suggestions": ["应用建议"],
    "follow_up_actions": ["后续动作"],
    "risks": ["风险边界"],
    "tags": ["AI", "个人知识库"]
  },
  "rendered_views": {
    "markdown": "# 一个知识卡示例\n\n## 一句话总结\n..."
  },
  "quality_gate": {
    "quality_gate_passed": true,
    "failed_checks": []
  },
  "storage": {
    "siyuan_write_ok": true,
    "write_path": "/知识卡/AI/一个知识卡示例",
    "doc_id": "mock-doc-id"
  },
  "used_mcp": false
}
```

- Status Values / 状态枚举：同 `GET /api/cards`。
- Error Shape / 错误结构：`Common Error Shape`。
- Side Effects / 副作用：只读；不写 SiYuan；不调用模型；不调用 `run_link_job.py`；不调用 MCP。
- Implemented Status / 实现状态：未实现。
- Notes / 备注：`card_id` 可以先由 `job_id` 派生，后续由数据库生成稳定 ID。

## Future Reserved APIs

### GET /api/search?q=...

- Endpoint / 接口路径：`/api/search?q=...`
- Method / 请求方法：`GET`
- Purpose / 用途：搜索任务、知识卡、来源标题、标签和摘要。
- Frontend Screen / 对应前端页面：Search Screen。
- Request JSON / 请求 JSON：无 body。Query 示例：

```text
GET /api/search?q=AI%20workflow&type=all&limit=20
```

- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "query": "AI workflow",
  "items": [
    {
      "type": "card",
      "id": "card_20260630_153000_a1b2c3d4",
      "title": "一个知识卡示例",
      "snippet": "命中的摘要片段",
      "score": 0.92,
      "target_screen": "card_detail"
    }
  ],
  "used_mcp": false
}
```

- Status Values / 状态枚举：`type=all|job|card|source`；结果项 `target_screen=job_detail|card_detail`。
- Error Shape / 错误结构：`Common Error Shape`。
- Side Effects / 副作用：只读；不写 SiYuan；不调用模型；不调用 `run_link_job.py`；不调用 MCP。
- Implemented Status / 实现状态：未实现。
- Notes / 备注：第一版可以用本地 JSON 索引，后续接数据库全文检索。

### POST /api/jobs/{job_id}/retry

- Endpoint / 接口路径：`/api/jobs/{job_id}/retry`
- Method / 请求方法：`POST`
- Purpose / 用途：基于旧 job 的来源 URL 重新运行处理流程。
- Frontend Screen / 对应前端页面：Job Detail Screen。
- Request JSON / 请求 JSON：

```json
{
  "dry_run": true,
  "force_ocr": false,
  "timeout_sec": 900,
  "reason": "user_retry_from_mobile"
}
```

- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "status": "queued",
  "parent_job_id": "20260630-153000-a1b2c3d4",
  "job_id": "20260630-160000-e5f6a7b8",
  "reply_text": "已创建重试任务。",
  "used_mcp": false
}
```

- Status Values / 状态枚举：`queued`、`dry_run`、`processing`、`failed_to_enqueue`。
- Error Shape / 错误结构：`Common Error Shape`。
- Side Effects / 副作用：
  - `dry_run=true`：只验证可重试性，不调用 `run_link_job.py`，不写 SiYuan。
  - `dry_run=false`：后端可能调用 `run_link_job.py`，并可能调用模型和写入 SiYuan。
- Implemented Status / 实现状态：未实现。
- Notes / 备注：前端只触发 API，不传脚本路径，不直接读取本地 job 文件。

### POST /api/cards/{card_id}/sync-siyuan

- Endpoint / 接口路径：`/api/cards/{card_id}/sync-siyuan`
- Method / 请求方法：`POST`
- Purpose / 用途：把已通过规则的卡片同步或重新同步到 SiYuan。
- Frontend Screen / 对应前端页面：Card Detail Screen。
- Request JSON / 请求 JSON：

```json
{
  "dry_run": true,
  "allow_temporary_card": false,
  "target_notebook": "Lucas",
  "reason": "manual_sync_from_mobile"
}
```

- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "status": "dry_run",
  "card_id": "card_20260630_153000_a1b2c3d4",
  "siyuan_write_ok": false,
  "write_path": "",
  "reply_text": "Dry-run：未写入 SiYuan。",
  "used_mcp": false
}
```

- Status Values / 状态枚举：`dry_run`、`syncing`、`synced`、`sync_failed`、`blocked_by_quality_gate`。
- Error Shape / 错误结构：`Common Error Shape`。
- Side Effects / 副作用：
  - `dry_run=true`：不写 SiYuan。
  - `dry_run=false`：后端通过 SiYuan API / Storage Sink 写入；不向前端暴露 `SIYUAN_TOKEN`；默认不调用模型；可先重跑质量门禁。
- Implemented Status / 实现状态：未实现。
- Notes / 备注：不得覆盖、删除、移动已有 SiYuan 笔记；自动写入优先新建。

### PATCH /api/cards/{card_id}

- Endpoint / 接口路径：`/api/cards/{card_id}`
- Method / 请求方法：`PATCH`
- Purpose / 用途：编辑卡片的用户可编辑字段，如标题、标签、人工备注、后续动作。
- Frontend Screen / 对应前端页面：Card Detail Screen。
- Request JSON / 请求 JSON：

```json
{
  "display_title": "更新后的标题",
  "tags": ["AI", "工作流"],
  "user_notes": "人工补充备注",
  "follow_up_actions": ["下一步验证这个流程"],
  "edit_reason": "mobile_card_edit"
}
```

- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "status": "updated",
  "card_id": "card_20260630_153000_a1b2c3d4",
  "updated_at": "2026-06-30T16:10:00+08:00",
  "card": {
    "display_title": "更新后的标题",
    "tags": ["AI", "工作流"]
  },
  "used_mcp": false
}
```

- Status Values / 状态枚举：`updated`、`validation_failed`、`not_found`。
- Error Shape / 错误结构：`Common Error Shape`。
- Side Effects / 副作用：更新未来数据库或本地卡片索引；默认不写 SiYuan；不调用模型；不调用 `run_link_job.py`；不调用 MCP。
- Implemented Status / 实现状态：未实现。
- Notes / 备注：如果需要同步到 SiYuan，应由 `POST /api/cards/{card_id}/sync-siyuan` 单独触发。

### POST /api/share/receive

- Endpoint / 接口路径：`/api/share/receive`
- Method / 请求方法：`POST`
- Purpose / 用途：接收手机系统分享、浏览器分享、聊天软件转发的链接或文本。
- Frontend Screen / 对应前端页面：系统分享入口、Chat Screen。
- Request JSON / 请求 JSON：

```json
{
  "source_app": "mobile_browser",
  "share_type": "url",
  "url": "https://v.douyin.com/example/",
  "title": "分享标题",
  "text": "分享附带文本",
  "dry_run": true,
  "metadata": {
    "received_from": "share_sheet"
  }
}
```

- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "status": "accepted",
  "job_id": null,
  "message_id": "share-20260630-001",
  "reply_text": "已收到分享内容。",
  "used_mcp": false
}
```

- Status Values / 状态枚举：`accepted`、`dry_run`、`queued`、`unsupported_share_type`、`failed`。
- Error Shape / 错误结构：`Common Error Shape`。
- Side Effects / 副作用：可内部转成 `MessageEvent` 并调用聊天路由；`dry_run=false` 时可能创建 job、调用 `run_link_job.py`、调用模型并写 SiYuan；`dry_run=true` 时不得写入。
- Implemented Status / 实现状态：未实现。
- Notes / 备注：正式移动端建议默认展示确认页，让用户看见是否 dry-run。

### POST /api/intake/file

- Endpoint / 接口路径：`/api/intake/file`
- Method / 请求方法：`POST`
- Purpose / 用途：接收截图、图片、PDF、音视频或文本文件，进入未来文件摄取流程。
- Frontend Screen / 对应前端页面：Chat Screen、未来文件导入页。
- Request JSON / 请求 JSON：实际上传应使用 `multipart/form-data`，其中 JSON 控制字段建议如下：

```json
{
  "files": [
    {
      "client_file_id": "local-file-001",
      "name": "screenshot.png",
      "mime_type": "image/png",
      "size_bytes": 345678
    }
  ],
  "text": "用户补充说明",
  "dry_run": true,
  "options": {
    "ocr": true,
    "transcribe": false
  }
}
```

- Response JSON / 返回 JSON：

```json
{
  "ok": true,
  "status": "accepted",
  "job_id": "20260630-170000-file001",
  "file_count": 1,
  "reply_text": "已接收文件，等待处理。",
  "used_mcp": false
}
```

- Status Values / 状态枚举：`accepted`、`queued`、`processing`、`unsupported_file_type`、`too_large`、`failed`。
- Error Shape / 错误结构：`Common Error Shape`。
- Side Effects / 副作用：可能在后端暂存文件、触发 OCR 或转写；正式写入仍必须经过模型写卡和质量门禁；不得把上传文件直接写入 SiYuan；不调用 MCP。
- Implemented Status / 实现状态：未实现。
- Notes / 备注：大文件和中间文件必须进入 TEMP 或受控对象存储，不进入项目源码目录。

## Data Structures

### MessageEvent

`MessageEvent` 是所有聊天入口统一后的内部消息结构。前端 POST 时可以省略 `message_id`、`received_at`、`raw_payload`，由后端补齐。

| Field | Type | Notes |
| --- | --- | --- |
| `message_id` | string | 外部 ID 或后端生成 UUID。 |
| `channel` | string | `wechat`、`my_chat_app`、`debug_cli`、`mobile_web` 等。 |
| `conversation_id` | string | 会话 / 房间 / 页面会话 ID。 |
| `sender_id` | string | 用户 ID，移动端可固定为 `lucas`。 |
| `sender_name` | string | 展示名。 |
| `message_type` | string | `text`、`image`、`file`、`link`、`unknown`。 |
| `text` | string | 用户输入文本或分享文本。 |
| `raw_payload` | object | 后端内部保留原始 payload。前端不依赖。 |
| `received_at` | string | ISO 时间。 |
| `metadata` | object | 客户端来源、dry-run 偏好、版本号等非敏感字段。 |

示例：

```json
{
  "message_id": "msg_20260630_001",
  "channel": "mobile_web",
  "conversation_id": "local-mobile-session",
  "sender_id": "lucas",
  "sender_name": "Lucas",
  "message_type": "text",
  "text": "https://v.douyin.com/example/",
  "raw_payload": {},
  "received_at": "2026-06-30T15:30:00+08:00",
  "metadata": {
    "source": "apps_web",
    "dry_run": true
  }
}
```

### HandlerResponse

| Field | Type | Notes |
| --- | --- | --- |
| `ok` | boolean | handler 是否成功处理请求；不等于最终写入成功。 |
| `reply_text` | string | 可直接展示给用户的简短回复。 |
| `job_id` | string/null | 真实处理创建 job 后返回；dry-run 通常为 null。 |
| `status` | string | handler 状态或 job 最终状态。 |
| `handled_by` | string | `command_handler`、`link_handler`、`fallback_handler`。 |
| `error` | string/null | 业务错误摘要。 |
| `data` | object | 调试和结构化补充信息，不应包含密钥。 |

`data.config_warnings` 是面向 UI 的配置提醒列表；`data.config_preflight` 是同一轮只读预检快照。前端应把 warning 作为用户可见提示展示，不能把它当成已经测试过 provider 连接或已经写入失败的证明。

示例见 Mock Data。

### JobSummary

| Field | Type | Notes |
| --- | --- | --- |
| `job_id` | string | job runtime ID。 |
| `source_url` | string | 原始 URL 或来源标识。 |
| `source_type` | string | `video/douyin`、`webpage`、`manual_text`、`file/image` 等。 |
| `title` | string | 页面标题、卡片标题或 fallback 标题。 |
| `status` | string | 面向 UI 的粗状态。 |
| `phase` | string | 后端 runtime 阶段。 |
| `card_type` | string/null | 已生成卡片类型。 |
| `content_level` | string/null | Level 1-5 或临时等级。 |
| `created_at` | string | 创建时间。 |
| `updated_at` | string | 更新时间。 |
| `has_card` | boolean | 是否已有本地卡片结果。 |
| `write_ok` | boolean | 是否写入成功。 |
| `error` | string | 失败摘要。 |
| `used_mcp` | boolean | 生产路径应为 false。 |

示例：

```json
{
  "job_id": "20260630-153000-a1b2c3d4",
  "source_url": "https://v.douyin.com/example/",
  "source_type": "video/douyin",
  "title": "一个知识卡示例",
  "status": "completed",
  "phase": "completed_formal",
  "card_type": "formal_summary",
  "content_level": "Level 5 评论与互动增强级",
  "created_at": "2026-06-30T15:30:00+08:00",
  "updated_at": "2026-06-30T15:36:00+08:00",
  "has_card": true,
  "write_ok": true,
  "error": "",
  "used_mcp": false
}
```

### JobDetail

`JobDetail` 包含 `JobSummary` 加各阶段材料摘要。移动端默认只展示摘录，不展示大段 transcript。

```json
{
  "job": {
    "job_id": "20260630-153000-a1b2c3d4",
    "source_url": "https://v.douyin.com/example/",
    "source_type": "video/douyin",
    "title": "一个知识卡示例",
    "status": "completed",
    "phase": "completed_formal",
    "card_type": "formal_summary",
    "content_level": "Level 5 评论与互动增强级",
    "created_at": "2026-06-30T15:30:00+08:00",
    "updated_at": "2026-06-30T15:36:00+08:00",
    "used_mcp": false
  },
  "materials": {
    "content": {"status": "ok", "title": "页面标题", "author": "未读取到"},
    "transcript": {"status": "transcribed", "has_speech": true, "excerpt": "短摘录"},
    "ocr": {"status": "skipped_not_needed", "frame_count": 0, "excerpt": ""},
    "comments": {"status": "comments_done", "comment_count": 12, "comments_hash": "sha256:mock"}
  },
  "composer": {"composer_status": "success", "model_provider": "deepseek_compatible", "model_used": "deepseek-chat"},
  "quality_gate": {"quality_gate_passed": true, "failed_checks": []},
  "storage": {"siyuan_write_ok": true, "write_path": "/知识卡/AI/一个知识卡示例"},
  "card_id": "card_20260630_153000_a1b2c3d4"
}
```

### CardSummary

```json
{
  "card_id": "card_20260630_153000_a1b2c3d4",
  "job_id": "20260630-153000-a1b2c3d4",
  "title": "一个知识卡示例",
  "card_type": "formal_summary",
  "content_level": "Level 5 评论与互动增强级",
  "quality_level": "high",
  "source_url": "https://v.douyin.com/example/",
  "tags": ["AI", "个人知识库"],
  "created_at": "2026-06-30T15:36:00+08:00",
  "updated_at": "2026-06-30T15:36:00+08:00",
  "storage_status": "siyuan_synced",
  "used_mcp": false
}
```

### CardDetail

`CardDetail.card` 应兼容 `ComposedCardV1`。

```json
{
  "card_id": "card_20260630_153000_a1b2c3d4",
  "job_id": "20260630-153000-a1b2c3d4",
  "schema_name": "ComposedCardV1",
  "schema_version": "1",
  "card_type": "formal_summary",
  "content_level": "Level 5 评论与互动增强级",
  "quality_level": "high",
  "source_title": "页面标题",
  "display_title": "一个知识卡示例",
  "safe_filename_title": "2026-06-30_一个知识卡示例",
  "one_sentence_summary": "一句话总结。",
  "original_summary": "原始材料压缩摘要。",
  "core_points": ["观点一", "观点二", "观点三"],
  "knowledge_blocks": [
    {
      "concept": "概念一",
      "explanation": "说明",
      "evidence": "短证据",
      "reusable_value": "可复用价值"
    }
  ],
  "methodology": ["方法步骤"],
  "application_suggestions": ["应用建议"],
  "follow_up_actions": ["后续动作"],
  "comment_signals": {
    "comments_hash": "sha256:mock",
    "demand_or_resource_requests": [],
    "doubts_or_objections": [],
    "implementation_barriers": [],
    "resonance_or_agreement": [],
    "incremental_value": "评论提供了额外验证信号。"
  },
  "reusable_value": ["可复用价值"],
  "risks": ["自动读取边界说明"],
  "tags": ["AI", "个人知识库"],
  "evidence_quotes": ["短证据"],
  "appendix_transcript_excerpt": "短摘录，不是全文 transcript。",
  "comments_job_id": "20260630-153000-a1b2c3d4",
  "comments_video_id": "1234567890",
  "comments_hash": "sha256:mock",
  "composer_input_comments_hash": "sha256:mock",
  "composed_card_comments_hash": "sha256:mock",
  "composer_status": "success",
  "composer_error": "",
  "model_used": "deepseek-chat",
  "model_provider": "deepseek_compatible"
}
```

### SystemStatus

```json
{
  "ok": false,
  "status": "degraded",
  "service": "chat_gateway",
  "api": {
    "version": "0.1.0",
    "health_ok": true
  },
  "runtime": {
    "jobs_dir_configured": true,
    "jobs_dir_readable": true,
    "runner_available": true
  },
  "ai": {
    "provider_id": "deepseek_compatible",
    "label": "DeepSeek",
    "kind": "ai",
    "protocol": "openai_compatible",
    "configured": false,
    "api_key_present": false,
    "api_key_env": "DEEPSEEK_API_KEY",
    "base_url_present": true,
    "base_url_label": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "status": "not_configured",
    "supports_vision": false,
    "supports_json_mode": true,
    "error": ""
  },
  "storage": {
    "active_provider": "lucas_database",
    "storage_targets": ["siyuan", "lucas_database"],
    "auto_ingest_enabled": true,
    "configured": false,
    "status": "not_configured",
    "targets": [
      {"provider_id": "siyuan", "label": "SiYuan", "configured": false, "api_key_present": false, "api_key_env": "SIYUAN_TOKEN", "status": "not_configured"},
      {"provider_id": "lucas_database", "label": "Lucas Database / Brain", "configured": false, "api_key_present": false, "api_key_env": "LUCAS_DB_API_KEY", "status": "not_configured"}
    ]
  },
  "config_warnings": [
    {
      "code": "ai_api_key_missing",
      "severity": "warning",
      "target": "ai",
      "title": "模型 API Key 未配置",
      "message": "当前模型提供方是 DeepSeek，但没有检测到 API Key。普通聊天和正式知识卡写作会失败。",
      "action": "在设置里的模型配置保存 API Key，或配置环境变量 DEEPSEEK_API_KEY。"
    }
  ],
  "used_mcp": false
}
```

### ProviderStatus

`ProviderStatus` 可用于 AI provider、Storage provider、SiYuan provider 等只读展示。

```json
{
  "provider_id": "deepseek_compatible",
  "label": "DeepSeek",
  "kind": "ai",
  "protocol": "openai_compatible",
  "configured": true,
  "api_key_present": true,
  "api_key_last4": "",
  "base_url_label": "https://api.deepseek.com",
  "model": "deepseek-chat",
  "supports_vision": false,
  "supports_json_mode": true,
  "status": "unknown",
  "latency_ms": null,
  "error": ""
}
```

注意：`api_key_last4` 不是必需字段。正式 mobile UI 优先只展示 `api_key_present`，不展示完整 key。

## Mock Data

### 成功的 HandlerResponse

```json
{
  "ok": true,
  "reply_text": "已检测到链接：https://v.douyin.com/example/\nDry-run：未调用 run_link_job.py，未写入 SiYuan。",
  "job_id": null,
  "status": "dry_run",
  "handled_by": "link_handler",
  "error": null,
  "data": {
    "message_id": "msg_mock_success",
    "url": "https://v.douyin.com/example/",
    "urls": ["https://v.douyin.com/example/"],
    "extra_url_count": 0,
    "runner_called": false,
    "write_skipped": true,
    "config_warnings": [],
    "config_preflight": {"ok": true, "status": "ready", "warnings": [], "used_mcp": false},
    "used_mcp": false
  }
}
```

### 失败的 HandlerResponse

```json
{
  "ok": false,
  "reply_text": "处理失败或写入未完成，但已保留本地 job：\njob_id：20260630-153000-failed01\n失败阶段：transcribe_failed\n本地路径：已隐藏\n错误：whisper 转写失败",
  "job_id": "20260630-153000-failed01",
  "status": "failed_but_recorded",
  "handled_by": "link_handler",
  "error": "whisper 转写失败",
  "data": {
    "message_id": "msg_mock_failed",
    "url": "https://v.douyin.com/example-failed/",
    "urls": ["https://v.douyin.com/example-failed/"],
    "extra_url_count": 0,
    "runner_exit_code": 0,
    "result": {
      "ok": false,
      "job_id": "20260630-153000-failed01",
      "source_url": "https://v.douyin.com/example-failed/",
      "failure_stage": "transcribe_failed",
      "write_ok": false,
      "used_mcp": false
    },
    "config_warnings": [],
    "config_preflight": {"ok": true, "status": "ready", "warnings": [], "used_mcp": false},
    "stderr_tail": "",
    "used_mcp": false
  }
}
```

### processing 的 JobDetail

```json
{
  "ok": true,
  "job": {
    "job_id": "20260630-153000-processing01",
    "source_url": "https://v.douyin.com/processing/",
    "source_type": "video/douyin",
    "title": "处理中任务",
    "status": "processing",
    "phase": "transcribing",
    "card_type": null,
    "content_level": null,
    "created_at": "2026-06-30T15:30:00+08:00",
    "updated_at": "2026-06-30T15:31:30+08:00",
    "used_mcp": false
  },
  "materials": {
    "content": {"status": "page_fetched", "title": "处理中任务", "author": "未读取到"},
    "transcript": {"status": "transcribing", "has_speech": null, "excerpt": ""},
    "ocr": {"status": "not_started", "frame_count": 0, "excerpt": ""},
    "comments": {"status": "not_started", "comment_count": 0, "comments_hash": ""}
  },
  "composer": {"composer_status": "not_started", "model_provider": "", "model_used": ""},
  "quality_gate": {"quality_gate_passed": null, "failed_checks": []},
  "storage": {"siyuan_write_ok": false, "write_path": ""},
  "card_id": null,
  "used_mcp": false
}
```

### completed 的 JobDetail

```json
{
  "ok": true,
  "job": {
    "job_id": "20260630-153000-completed01",
    "source_url": "https://v.douyin.com/completed/",
    "source_type": "video/douyin",
    "title": "AI 工作流复盘",
    "status": "completed",
    "phase": "completed_formal",
    "card_type": "formal_summary",
    "content_level": "Level 5 评论与互动增强级",
    "created_at": "2026-06-30T15:30:00+08:00",
    "updated_at": "2026-06-30T15:36:00+08:00",
    "used_mcp": false
  },
  "materials": {
    "content": {"status": "ok", "title": "AI 工作流复盘", "author": "未读取到"},
    "transcript": {"status": "transcribed", "has_speech": true, "excerpt": "这个视频讲的是把内容处理流程拆成可复用步骤。"},
    "ocr": {"status": "skipped_not_needed", "frame_count": 0, "excerpt": ""},
    "comments": {"status": "comments_done", "comment_count": 8, "comments_hash": "sha256:completed-mock"}
  },
  "composer": {"composer_status": "success", "model_provider": "deepseek_compatible", "model_used": "deepseek-chat"},
  "quality_gate": {"quality_gate_passed": true, "failed_checks": []},
  "storage": {"siyuan_write_ok": true, "write_path": "/知识卡/AI/AI 工作流复盘"},
  "card_id": "card_20260630_153000_completed01",
  "used_mcp": false
}
```

### failed 的 JobDetail

```json
{
  "ok": true,
  "job": {
    "job_id": "20260630-153000-failed01",
    "source_url": "https://v.douyin.com/failed/",
    "source_type": "video/douyin",
    "title": "处理失败任务",
    "status": "failed",
    "phase": "failed_but_recorded",
    "card_type": "failure_card",
    "content_level": "none",
    "created_at": "2026-06-30T15:30:00+08:00",
    "updated_at": "2026-06-30T15:34:00+08:00",
    "used_mcp": false
  },
  "materials": {
    "content": {"status": "page_fetch_failed", "title": "", "author": ""},
    "transcript": {"status": "transcribe_failed", "has_speech": false, "excerpt": ""},
    "ocr": {"status": "not_run", "frame_count": 0, "excerpt": ""},
    "comments": {"status": "comments_skipped", "comment_count": 0, "comments_hash": ""}
  },
  "composer": {"composer_status": "not_run", "model_provider": "", "model_used": ""},
  "quality_gate": {"quality_gate_passed": false, "failed_checks": ["composer_not_run"]},
  "storage": {"siyuan_write_ok": false, "write_path": "", "error": "SiYuan not started"},
  "card_id": null,
  "error": "页面读取或转写失败，已保留本地 job 记录。",
  "used_mcp": false
}
```

### CardDetail

```json
{
  "ok": true,
  "card": {
    "card_id": "card_20260630_153000_completed01",
    "job_id": "20260630-153000-completed01",
    "schema_name": "ComposedCardV1",
    "schema_version": "1",
    "card_type": "formal_summary",
    "content_level": "Level 5 评论与互动增强级",
    "quality_level": "high",
    "source_title": "AI 工作流复盘",
    "display_title": "AI 工作流复盘",
    "safe_filename_title": "2026-06-30_AI 工作流复盘",
    "one_sentence_summary": "把自动化内容处理拆成可检查、可复盘、可复用的 job 流程。",
    "original_summary": "原始内容围绕 AI 工作流、任务记录和复盘展开。",
    "core_points": [
      "入口层只负责接收消息和路由。",
      "正式知识卡必须经过模型写卡和质量门禁。",
      "失败结果应保留为临时复核或失败记录。"
    ],
    "knowledge_blocks": [
      {
        "concept": "入口与处理分离",
        "explanation": "Chat Gateway 不直接承担转写、OCR、写卡或存储职责。",
        "evidence": "入口层只负责接收消息和路由。",
        "reusable_value": "前端只要对齐 API，就可以替换入口形态而不改变主链路。"
      },
      {
        "concept": "质量门禁",
        "explanation": "正式卡必须通过 composer 和 gate，失败时降级。",
        "evidence": "正式知识卡必须经过模型写卡和质量门禁。",
        "reusable_value": "避免把低置信度材料伪装成高等级知识卡。"
      }
    ],
    "methodology": ["接收消息", "创建或读取 job", "生成卡片", "通过质量门禁", "写入 Storage Sink"],
    "application_suggestions": ["移动端先围绕任务状态和卡片复核做设计。"],
    "follow_up_actions": ["实现 `/api/jobs` 和 `/api/cards` 后接入真实列表。"],
    "comment_signals": {
      "comments_hash": "sha256:completed-mock",
      "demand_or_resource_requests": [],
      "doubts_or_objections": [],
      "implementation_barriers": [],
      "resonance_or_agreement": ["希望能看到任务状态"],
      "incremental_value": "评论强化了任务可视化的必要性。"
    },
    "reusable_value": ["把 job 状态抽象成移动端可读的任务时间线。"],
    "risks": ["OCR 和评论增强不是每条内容都稳定可达。"],
    "tags": ["AI", "个人知识库", "移动端"],
    "evidence_quotes": ["入口层只负责接收消息和路由。"],
    "appendix_transcript_excerpt": "这里只放短摘录，不放全文 transcript。",
    "comments_job_id": "20260630-153000-completed01",
    "comments_video_id": "1234567890",
    "comments_hash": "sha256:completed-mock",
    "composer_input_comments_hash": "sha256:completed-mock",
    "composed_card_comments_hash": "sha256:completed-mock",
    "composer_status": "success",
    "composer_error": "",
    "model_used": "deepseek-chat",
    "model_provider": "deepseek_compatible"
  },
  "rendered_views": {
    "markdown": "# AI 工作流复盘\n\n## 一句话总结\n把自动化内容处理拆成可检查、可复盘、可复用的 job 流程。"
  },
  "quality_gate": {
    "quality_gate_passed": true,
    "failed_checks": []
  },
  "storage": {
    "siyuan_write_ok": true,
    "write_path": "/知识卡/AI/AI 工作流复盘",
    "doc_id": "mock-doc-id"
  },
  "used_mcp": false
}
```

### SystemStatus

```json
{
  "ok": true,
  "status": "degraded",
  "service": "chat_gateway",
  "api": {
    "version": "0.1.0",
    "health_ok": true,
    "base_url": "http://127.0.0.1:3963"
  },
  "runtime": {
    "jobs_dir_configured": true,
    "jobs_dir_readable": true,
    "runner_available": true,
    "latest_job_count": 18
  },
  "ai": {
    "provider_id": "deepseek_compatible",
    "label": "DeepSeek",
    "kind": "ai",
    "protocol": "openai_compatible",
    "configured": true,
    "api_key_present": true,
    "base_url_label": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "supports_vision": false,
    "supports_json_mode": true,
    "status": "unknown",
    "latency_ms": null,
    "error": ""
  },
  "storage": {
    "active_provider": "lucas_database",
    "storage_targets": ["siyuan", "lucas_database"],
    "auto_ingest_enabled": true,
    "configured": true,
    "status": "configured",
    "targets": [
      {
        "provider_id": "siyuan",
        "label": "SiYuan",
        "kind": "storage",
        "configured": true,
        "api_key_present": true,
        "base_url_label": "http://127.0.0.1:6806",
        "status": "configured"
      },
      {
        "provider_id": "lucas_database",
        "label": "Lucas Database / Brain",
        "kind": "storage",
        "configured": true,
        "api_key_present": true,
        "base_url_label": "http://127.0.0.1:8765",
        "status": "configured"
      }
    ]
  },
  "config_warnings": [],
  "capabilities": {
    "chat_gateway": true,
    "link_intake": true,
    "douyin_level3_transcript": true,
    "conditional_ocr": true,
    "comments_enhancement": "opportunistic",
    "formal_card_requires_composer_and_quality_gate": true,
    "mcp_required_for_production": false
  },
  "used_mcp": false
}
```
