# Chat Gateway Abstraction

## 背景

本项目现在定位为个人知识库内容入库中控系统。Chat Gateway 只是入口层，负责把微信、自研聊天软件、debug CLI 等外部消息转换成统一 `MessageEvent`，再交给后端 intake handler。它不承担内容读取、模型写卡、质量门禁或存储写入职责。

旧链路把入口绑定在微信桥上：

```text
cli-wechat-bridge -> wechat-bridge.js URL 拦截 -> tools/wechat_link_entry.py -> tools/run_link_job.py
```

V1.ChatGateway-Abstraction 的目标是把“从哪里收到消息”和“如何处理链接内容”拆开。微信桥、自研聊天软件、debug CLI 都只是 adapter；核心系统只接收统一的 `MessageEvent`，并返回统一的 `HandlerResponse`。

本轮只改入口抽象，不改转写、OCR、评论抓取、模型写卡、质量门禁、SiYuan writer，也不接数据库。

## 结构

```text
chat_gateway/
  message_schema.py      # MessageEvent
  response_schema.py     # HandlerResponse
  link_extractor.py      # URL 提取
  message_router.py      # command/link/fallback 路由
  handlers/
    link_handler.py      # 固定调用 tools/run_link_job.py
    command_handler.py
    fallback_handler.py

adapters/
  wechat_entry_adapter.py
  http_chat_adapter.py
  debug_cli_adapter.py

server/
  chat_api.py
```

`chat_gateway` 不依赖微信、Codex、MCP 或 SiYuan API。`link_handler` 只知道有一个固定的本地 runner：`tools/run_link_job.py`。

MCP 边界：

- Chat Gateway 生产路径不得调用 MCP。
- MCP 只允许作为 debug-only / interactive-only 工具，用于人工排查页面状态、浏览器状态、SiYuan 状态或 Codex 探索。
- 如果某个调试动作未来需要自动化，必须沉淀为 Python 脚本、HTTP API、固定参数子进程或 Storage Sink，再接入 Gateway。
- 不要因为 MCP 与 Python 脚本并存就直接删除 MCP；清理 MCP glue 必须单独审计引用和调试价值。

## MessageEvent

```json
{
  "message_id": "uuid-or-external-id",
  "channel": "wechat | my_chat_app | debug_cli",
  "conversation_id": "room-or-thread-id",
  "sender_id": "lucas",
  "sender_name": "Lucas",
  "message_type": "text | image | file | link | unknown",
  "text": "message text",
  "raw_payload": {},
  "received_at": "2026-06-28T14:00:00+08:00",
  "metadata": {}
}
```

## HandlerResponse

```json
{
  "ok": true,
  "reply_text": "已检测到链接...",
  "job_id": null,
  "status": "dry_run | completed | ignored_no_url | failed",
  "handled_by": "link_handler",
  "error": null,
  "data": {}
}
```

普通无链接消息会进入 fallback 对话分支。普通知识型无链接消息必须先尝试 Lucas Database RAG：Chat Gateway 通过 HTTP 调用 Lucas Database `POST /api/agent/retrieve`，而不是通过 MCP。命中可靠上下文时，模型 prompt 会包含 `context.text`，回复应使用 `[Source n]` 引用证据；低置信、超时或不可用时，fallback 会确定性返回“没有可靠命中 / 检索不可用”，不会再让模型用通用常识补答案。问候、状态追问、配置问答和修订请求会跳过检索。

RAG 结果会透传到 `data.retrieval`：

```json
{
  "retrieval": {
    "attempted": true,
    "ok": true,
    "status": "ready",
    "can_answer": true,
    "context_text_present": true,
    "sources": [
      {
        "citation_label": "[Source 1]",
        "title": "AI 生成 iPhone App 后如何准备上架 App Store",
        "path": "/知识卡/AI/工程化/质量门禁/iPhone_App上架准备流程"
      }
    ]
  }
}
```

默认 RAG 超时窗口由 `LUCAS_CHAT_RAG_TIMEOUT_SEC` 控制；当前代码默认 45 秒，最大可配置到 120 秒，以适配本地 BGE-M3/sqlite-vec 冷启动。排查时应先看 `data.retrieval.attempted/ok/can_answer/status/error`，不要只凭自然语言回复判断是否查库。

若用户表达“调整/修改/不满意 + 已入库笔记/知识卡/卡片”，fallback 会返回 `status=note_revision_request`，并在 `data.revision` 中给出修订协商上下文：

```json
{
  "revision": {
    "requested": true,
    "mode": "discuss_then_modify",
    "candidate_title": "最近一条入库卡片标题",
    "requested_fields": ["note_identity", "dissatisfaction", "desired_change"]
  }
}
```

这个状态只表示“先和用户对齐修改目标”，不表示已经覆盖、删除、移动或写回既有 SiYuan / Brain 笔记。真正修订和受控写入需要后续独立的修改 agent / Storage Sink 流程。

为了让前端能明确知道“修订请求已经进入可复盘流程”，`note_revision_request` 还会创建本地 `runtime/jobs/revision-*` 任务记录，并返回 `data.revision_job` 与 `data.queue`：

```json
{
  "job_id": "revision-20260702-214517-title",
  "status": "note_revision_request",
  "data": {
    "revision_job": {
      "schema_name": "RevisionIntentV1",
      "job_id": "revision-20260702-214517-title",
      "status": "pending_quality_gate",
      "write_policy": "not_started"
    },
    "queue": {
      "mode": "single",
      "status": "pending",
      "items": [
        {
          "item_type": "note_revision",
          "queue_status": "in_progress",
          "card_status": "revision_intent_recorded",
          "write_status": "pending_policy",
          "final_status": "pending_quality_gate",
          "stages": ["修订草稿", "质量门禁", "写入策略"]
        }
      ]
    }
  }
}
```

`revision-*` 任务只保存修改意图、候选标题、当前请求和模型回复元信息；它不调用 SiYuan / Brain 写入，不覆盖旧笔记，也不表示质量门禁或写入策略已经完成。前端可以把该队列项显示为“待写入策略”，并且不要把它当成 Brain ingest receipt 去查询。

多 URL 消息会在 `data.queue` 返回同步队列快照。当前语义是受限并行队列：同一条消息里的 URL 会并发调用 `tools/run_link_job.py`，默认最多 2 个并行 runner，可通过 `LUCAS_LINK_HANDLER_MAX_PARALLEL_JOBS` 调整到 1-4。单个条目用三层状态表达：

- `queue_status`：`pending | in_progress | completed | failed`，表示 runner 队列状态；
- `card_status`：`formal_card_created | card_generation_failed | source_material_card | temporary_card | failure_card | not_created`，表示卡片结果；
- `write_status`：`written | skipped | failed | unknown`，表示 SiYuan 写入结果；修订意图队列可使用 `pending_policy`，表示尚未进入受控写入策略。

多 URL 返回时，顶层 `job_id` 不代表单个任务，会保持为空；所有子任务的 `job_id` 在 `data.queue.items[].job_id` 和 `data.job_ids` 中。

## 接入自研聊天软件

自研聊天软件只需要把消息 POST 到 HTTP API，或者在 Python 内部直接调用 adapter：

```python
from adapters.http_chat_adapter import handle_chat_payload

response = handle_chat_payload({
    "channel": "my_chat_app",
    "conversation_id": "test-room",
    "sender_id": "lucas",
    "text": "https://v.douyin.com/xxx/"
})
```

## HTTP API

启动：

```powershell
python -m uvicorn server.chat_api:app --host 127.0.0.1 --port 3963
```

发送 dry-run 请求：

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:3963/api/chat/messages `
  -ContentType application/json `
  -Body '{"channel":"my_chat_app","conversation_id":"test-room","sender_id":"lucas","text":"https://v.douyin.com/xxx/","dry_run":true}'
```

同步真实处理请求不带 `dry_run`。当前 V1 是同步调用 `run_link_job.py`，可通过 query 或 JSON 传 `timeout_sec`。后续建议改成异步 job：HTTP 入口创建 job 后立即返回 `job_id`，后台 worker 再调用 `run_link_job.py`。

查询 job：

```powershell
Invoke-RestMethod http://127.0.0.1:3963/api/jobs/<job_id>
```

## Debug CLI

```powershell
python -m adapters.debug_cli_adapter --text "https://v.douyin.com/xxx/" --dry-run
```

debug CLI 会打印标准 `MessageEvent` 和 `HandlerResponse`。`--dry-run` 不调用 `run_link_job.py`，不会写入 SiYuan。

## 微信桥兼容

主页面微信按钮现在启动 Lucas Chat Gateway 微信桥：

```powershell
.\wechat-bridge\scripts\start-lucas-wechat-gateway-bridge.ps1
```

该桥把普通微信文本和链接都发送到 `POST /api/chat/messages/async`。普通文本进入 fallback / RAG / 数据库问答；链接进入异步入库队列并轮询最终回执。

旧入口仍保留用于兼容测试：

```powershell
python tools/wechat_link_entry.py --message "https://v.douyin.com/xxx/" --extract-only --json
```

现在它只做三件事：读取微信桥传入文本，转换为 `MessageEvent`，调用 `chat_gateway.message_router` 并输出回执。

## 回滚

如需回滚入口抽象，可用 git 恢复本轮新增目录和 `tools/wechat_link_entry.py`：

```powershell
git checkout -- tools/wechat_link_entry.py
git clean -fd chat_gateway adapters server tests docs/CHAT_GATEWAY_ABSTRACTION.md
```

回滚不会影响 `tools/run_link_job.py`、转写、OCR、评论、模型写卡、质量门禁或 SiYuan writer，因为本轮没有改这些文件。

## 当前限制

- 多 URL 当前是同步受限并行队列，不是后台异步队列。HTTP 请求会等本批次处理结束后返回 `data.queue`；dry-run 时所有条目显示为 `pending`。
- HTTP API 当前是同步调用，长视频真实处理可能超过请求等待时间。
- `/api/jobs/{job_id}` 只读取本地 `runtime/jobs` 下的 `status.json` 和 `result.json`。
- command handler 只提供 `/help`、`/start`、`/ping` 占位。
- image/file 类型已进入协议，但 V1 还没有对应 handler。

## 后续升级

- WebSocket：聊天软件建立长连接，服务端推送 job 状态变化。
- SSE：适合只需要服务端单向推送进度的页面。
- Async job：HTTP POST 创建 gateway job，后台 worker 调固定 `run_link_job.py`，状态写入 gateway job store。
- 数据库：把 `MessageEvent`、`HandlerResponse`、job 状态和最终卡片索引持久化，替换当前本地文件查询。
