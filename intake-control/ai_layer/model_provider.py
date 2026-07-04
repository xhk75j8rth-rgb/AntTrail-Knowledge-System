from __future__ import annotations

import os

from ai_layer.anthropic_provider import AnthropicProvider
from ai_layer.deepseek_provider import DeepSeekProvider
from ai_layer.mock_provider import MockProvider
from ai_layer.openai_compatible_provider import OpenAICompatibleProvider
from ai_layer.provider_config import resolve_provider_config
from ai_layer.provider_schema import ModelProvider


def build_provider(
    provider_name: str | None = None,
    *,
    model_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> ModelProvider:
    env_provider = (os.environ.get("AI_LAYER_PROVIDER") or "").strip().lower()
    name = (provider_name or env_provider or "").strip().lower().replace("-", "_")
    if name in ("mock", "mock_provider"):
        return MockProvider()

    config = resolve_provider_config(name or None)
    resolved_api_key = api_key if api_key is not None else config.api_key
    resolved_base_url = base_url if base_url is not None else config.base_url
    resolved_model = model_name if model_name is not None else config.model

    if config.provider_id == "deepseek_compatible":
        return DeepSeekProvider(api_key=resolved_api_key, base_url=resolved_base_url, model_name=resolved_model)
    if config.protocol == "anthropic_messages":
        return AnthropicProvider(api_key=resolved_api_key or "", base_url=resolved_base_url or "", model_name=resolved_model or "")
    return OpenAICompatibleProvider(
        provider_name=config.provider_id,
        api_key=resolved_api_key or "",
        base_url=resolved_base_url or "",
        model_name=resolved_model or "",
        supports_json_mode=config.supports_json_mode,
    )


def provider_name_for_task(task_type: str) -> str:
    env_provider = (os.environ.get("AI_LAYER_PROVIDER") or "").strip().lower().replace("-", "_")
    if env_provider in ("mock", "mock_provider"):
        return "mock"
    return resolve_provider_config().provider_id
