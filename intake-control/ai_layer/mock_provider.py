from __future__ import annotations

import json
import time
from typing import Any

from ai_layer.provider_schema import ModelResult


class MockProvider:
    name = "mock"

    def _result(self, *, payload: Any, kind: str, model_name: str = "mock-model") -> ModelResult:
        started = time.perf_counter()
        if kind == "json":
            json_data = payload if isinstance(payload, dict) else {"payload": payload}
            text = json.dumps(json_data, ensure_ascii=False)
        elif kind == "markdown":
            text = str(payload)
            json_data = None
        else:
            text = str(payload)
            json_data = None
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ModelResult(
            ok=True,
            text=text,
            json_data=json_data,
            model_provider=self.name,
            model_name=model_name,
            error="",
            raw_usage={},
            latency_ms=latency_ms,
        )

    def generate_text(self, prompt: str, system_prompt: str | None = None, metadata: dict[str, Any] | None = None) -> ModelResult:
        return self._result(payload=f"mock:text:{prompt[:60]}", kind="text")

    def generate_json(
        self,
        prompt: str,
        schema_name: str | None = None,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult:
        payload = {
            "intent": "normal_chat",
            "confidence": 0.5,
            "reason": "mock provider",
            "requires_tool": False,
            "schema_name": schema_name or "mock",
            "echo": prompt[:80],
        }
        return self._result(payload=payload, kind="json")

    def generate_markdown(self, prompt: str, system_prompt: str | None = None, metadata: dict[str, Any] | None = None) -> ModelResult:
        return self._result(payload=f"# mock markdown\n\n{prompt[:120]}", kind="markdown")
