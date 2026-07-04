from __future__ import annotations

import os

from ai_layer.openai_compatible_provider import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model_name: str | None = None) -> None:
        super().__init__(
            provider_name="deepseek_compatible",
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY") or "",
            base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            model_name=model_name or os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat",
            supports_json_mode=True,
        )
