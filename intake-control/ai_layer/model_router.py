from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ai_layer.model_provider import build_provider, provider_name_for_task
from ai_layer.provider_config import resolve_provider_config
from ai_layer.provider_schema import ModelProvider, ModelResult


@dataclass(slots=True)
class ModelRouter:
    default_provider: str = "deepseek_compatible"

    def get_provider(self, task_type: str) -> ModelProvider:
        provider_name = provider_name_for_task(task_type)
        return build_provider(provider_name)

    def _failure(self, task_type: str, error: str) -> ModelResult:
        config = resolve_provider_config()
        return ModelResult(
            ok=False,
            text="",
            json_data=None,
            model_provider=config.provider_id or self.default_provider,
            model_name=config.model,
            error=error,
            raw_usage={},
            latency_ms=0,
        )

    def generate_text(self, task_type: str, prompt: str, system_prompt: str | None = None, metadata: dict[str, Any] | None = None) -> ModelResult:
        try:
            provider = self.get_provider(task_type)
            return provider.generate_text(prompt, system_prompt=system_prompt, metadata=metadata)
        except Exception as exc:
            return self._failure(task_type, str(exc))

    def generate_json(self, task_type: str, prompt: str, schema_name: str | None = None, system_prompt: str | None = None, metadata: dict[str, Any] | None = None) -> ModelResult:
        try:
            provider = self.get_provider(task_type)
            return provider.generate_json(prompt, schema_name=schema_name, system_prompt=system_prompt, metadata=metadata)
        except Exception as exc:
            return self._failure(task_type, str(exc))

    def generate_markdown(self, task_type: str, prompt: str, system_prompt: str | None = None, metadata: dict[str, Any] | None = None) -> ModelResult:
        try:
            provider = self.get_provider(task_type)
            return provider.generate_markdown(prompt, system_prompt=system_prompt, metadata=metadata)
        except Exception as exc:
            return self._failure(task_type, str(exc))


@lru_cache(maxsize=1)
def get_router() -> ModelRouter:
    return ModelRouter()
