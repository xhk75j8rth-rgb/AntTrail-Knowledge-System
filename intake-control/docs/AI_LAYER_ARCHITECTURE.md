# AI Layer Architecture

## Why

The project needs a model layer that can survive without Codex as the runtime intelligence layer. Codex can remain a developer tool, but production should route through a provider abstraction that can use DeepSeek/OpenAI-compatible backends or a mock provider for tests.

## Codex Roles

1. Development tool: edit code, inspect files, run tests.
2. Exploration tool: help diagnose workflow and architecture.
3. Runtime fallback only in development contexts, not a production dependency.

## Replacement Targets

- Replace direct runtime dependence on Codex-style execution.
- Replace prompt fragments scattered across scripts with a prompt registry.
- Replace ad hoc model calling code with a provider/router layer.

## Structure

```text
ai_layer/
  provider_schema.py
  model_provider.py
  model_router.py
  prompt_registry.py
  intent_classifier.py
  chat_responder.py
  card_composer_service.py
  tool_request_schema.py
  mock_provider.py
  openai_compatible_provider.py
  deepseek_provider.py
  anthropic_provider.py
  provider_config.py
```

## Provider Interface

Providers expose:

- `generate_text(prompt, system_prompt=None, metadata=None)`
- `generate_json(prompt, schema_name=None, system_prompt=None, metadata=None)`
- `generate_markdown(prompt, system_prompt=None, metadata=None)`

Provider results include:

- `ok`
- `text`
- `json_data`
- `model_provider`
- `model_name`
- `error`
- `raw_usage`
- `latency_ms`

## Router Behavior

`model_router.py` chooses providers by `task_type`:

- `card_composer`
- `chat_response`
- `intent_classification`
- `quality_judge`

The router returns structured errors instead of crashing when a provider is unavailable.

## Prompt Registry

Prompt text lives in one place so card composition, chat replies, and intent classification share the same policy:

- only use already-read material
- do not invent facts
- do not paste raw transcript as summary
- use reusable value, application suggestions, and follow-up actions

## Intent / Chat / Card

- `intent_classifier.py` performs lightweight routing decisions.
- `chat_responder.py` returns short natural-language replies for ordinary chat.
- `card_composer_service.py` packages the existing formal-card workflow behind the AI Layer.

## DeepSeek

`DeepSeekProvider` is now a thin preset over the OpenAI-compatible provider. It uses environment variables or safe local configuration:

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`

The key is never logged in full.

## Provider Config

The minimal chat UI exposes a model configuration panel. It talks to:

- `GET /api/ai/providers`
- `GET /api/ai/config`
- `POST /api/ai/config`

The local config file is `config/ai_layer.local.json`, which is ignored by git. API keys may be saved locally, but API responses only expose `api_key_present` and `api_key_last4`.

Built-in presets include DeepSeek, OpenAI, Gemini OpenAI compatibility, Anthropic Claude, DashScope, Moonshot, SiliconFlow, OpenRouter, and custom OpenAI-compatible relay endpoints.

See `docs/AI_PROVIDER_CONFIG.md` for the concrete provider list and UI workflow.

## Vision Boundary

DeepSeek should not be treated as a general image-recognition layer. The current card composer is text-first: it receives material already extracted by page readers, transcription, OCR, and comments. Image or screenshot understanding should be added as a separate multimodal source-reader capability and routed to a provider that explicitly supports vision.

## Future Providers

- Native OpenAI multimodal provider
- Native Gemini provider
- More Claude multimodal coverage
- CodexExecProvider as a developer-only option, not a default runtime dependency

## Safety Boundaries

- AI Layer does not execute arbitrary shell commands.
- AI Layer does not access SiYuan directly.
- AI Layer does not download video.
- AI Layer does not call MCP.
- AI Layer only returns structured intent or text.
- Any external side effect must be handled by an allowlisted handler.

## Testing

Use the mock provider only for isolated unit tests:

```powershell
python -m unittest discover -s tests -v
```

The production runtime should default to the DeepSeek-compatible provider path. Mock is a test fixture, not the runtime default.

## Next Steps

- Move more prompt text into the prompt registry.
- Add an OpenAI-compatible provider when needed.
- Keep Codex out of runtime dependencies.
