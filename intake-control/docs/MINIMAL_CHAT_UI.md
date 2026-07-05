# Minimal Chat UI

## Path

- Browser route: `http://127.0.0.1:3963/ui`
- Source file: `ui/minimal_chat/index.html`

## Start API

```powershell
python -m uvicorn server.chat_api:app --host 127.0.0.1 --port 3963
```

The current server also exposes `/health` and `/api/chat/messages`.

## Open UI

Open `http://127.0.0.1:3963/ui` in the browser. The page is served from the same origin as the API, so no CORS setup is needed for the default flow.

## Request Format

The page sends:

```json
{
  "channel": "my_chat_app",
  "conversation_id": "local-ui-test",
  "sender_id": "lucas",
  "sender_name": "Lucas",
  "message_type": "text",
  "text": "user message",
  "metadata": {
    "source": "minimal_chat_ui",
    "dry_run": true
  }
}
```

`metadata.dry_run=true` is supported by the backend and prevents `run_link_job.py` from being invoked.

When the composer's “知识搜索” toggle is enabled, the page also sends:

```json
{
  "retrieval_mode": "knowledge_search",
  "force_retrieval": true,
  "retrieval_query": "ai"
}
```

This forces the fallback chat path to call the local AntTrail / Lucas Database `POST /api/agent/retrieve`, which is needed for short queries such as `ai` that are otherwise ordinary chat text.

## Model Config Panel

The same page includes a “模型配置” panel. It calls:

```text
GET /api/ai/providers
GET /api/ai/config
POST /api/ai/config
```

Selecting a provider auto-fills its default Base URL and model name. You can edit these fields for a relay service or self-hosted OpenAI-compatible gateway.

The API key field is write-only from the browser perspective: after saving, the UI only shows whether a key is present and the last four characters. The full key is not returned by the API.

Current presets include DeepSeek, OpenAI, Gemini OpenAI compatibility, Anthropic Claude, DashScope, Moonshot, SiliconFlow, OpenRouter, and a custom OpenAI-compatible relay.

For relay providers, use “获取模型列表” after filling Base URL and API key. The backend calls `POST /api/ai/models`, which requests `<Base URL>/models` and fills the model-name suggestions. You can still type a model name manually if the relay does not expose a model list.

## Dry-run vs Real-run

- Dry-run: the gateway routes the message, extracts links, and returns the handler response without calling `run_link_job.py`.
- Real-run: the gateway can call `run_link_job.py`; if the job reaches model composition and quality gate success, it can write to SiYuan through the existing writer.

The UI shows a warning state by default through the dry-run checkbox.

## Confirming SiYuan Write

For a real run, inspect the returned `job_id`, `status`, and the local `runtime/jobs/<job_id>/result.json`.

If the result includes `siyuan_write_ok=true`, the run reached the current storage sink. If `write_result.path` is present, it points to the written note path reported by the writer.

## Common Errors

- API not started: check `http://127.0.0.1:3963/health`.
- CORS: avoid opening the HTML file directly from `file://`; use `/ui` so the page is same-origin with the API.
- SiYuan not started: real writes may fail even if the chat API is healthy.
- `SIYUAN_TOKEN` missing: real writes may fail or degrade.
- Long job timeout: Douyin flows may exceed the request timeout.
- DeepSeek unavailable: model composer may fall back to a temporary review card.
- Wrong model provider URL: check the Base URL auto-filled in “模型配置”.
- Model list cannot be fetched: the relay may not support `/models`, the Base URL may be missing `/v1`, or the API key may not have model-list permission.
- Vision model mismatch: current composer is text-first; image recognition needs a provider/model that explicitly supports vision plus a future multimodal reader path.
