from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from ai_layer.openai_compatible_provider import OpenAICompatibleProvider
from ai_layer.provider_schema import ModelResult


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str, base_url: str, model_name: str) -> None:
        self.api_key = api_key or ""
        self.base_url = (base_url or "").rstrip("/")
        self.model_name = model_name or ""
        self._json_parser = OpenAICompatibleProvider(
            provider_name=self.name,
            api_key="parser-only",
            base_url="http://parser-only",
            model_name=self.model_name,
        )

    def _failure(self, started: float, error: str) -> ModelResult:
        return ModelResult(
            ok=False,
            text="",
            json_data=None,
            model_provider=self.name,
            model_name=self.model_name,
            error=error,
            raw_usage={},
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _call(self, prompt: str, system_prompt: str | None, metadata: dict[str, Any] | None, *, wants_json: bool = False) -> ModelResult:
        started = time.perf_counter()
        if not self.api_key:
            return self._failure(started, "api_key_missing")
        if not self.base_url:
            return self._failure(started, "base_url_missing")
        if not self.model_name:
            return self._failure(started, "model_missing")

        payload: dict[str, Any] = {
            "model": self.model_name,
            "system": system_prompt or "",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": int((metadata or {}).get("max_tokens") or 4096),
        }
        req = urllib.request.Request(
            f"{self.base_url}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=int((metadata or {}).get("timeout_sec") or 60)) as response:
                body = json.loads(response.read().decode("utf-8"))
            content_parts = body.get("content") or []
            text_parts = []
            for part in content_parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(str(part.get("text") or ""))
            content = "\n".join(text_parts).strip()
            return ModelResult(
                ok=True,
                text=content,
                json_data=self._json_parser._parse_json(content) if wants_json else None,
                model_provider=self.name,
                model_name=self.model_name,
                error="",
                raw_usage=body.get("usage") or {},
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except urllib.error.HTTPError as exc:
            return self._failure(started, f"http_error_{exc.code}")
        except Exception as exc:
            return self._failure(started, str(exc))

    def generate_text(self, prompt: str, system_prompt: str | None = None, metadata: dict[str, Any] | None = None) -> ModelResult:
        return self._call(prompt, system_prompt, metadata)

    def generate_json(
        self,
        prompt: str,
        schema_name: str | None = None,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult:
        json_system = (system_prompt or "") + "\n请只输出一个合法 JSON 对象，不要输出 Markdown 代码块。"
        return self._call(prompt, json_system, metadata, wants_json=True)

    def generate_markdown(self, prompt: str, system_prompt: str | None = None, metadata: dict[str, Any] | None = None) -> ModelResult:
        return self._call(prompt, system_prompt, metadata)
