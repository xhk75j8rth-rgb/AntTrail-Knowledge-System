from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from ai_layer.http_network import is_transient_network_error, retry_attempt_count, transient_network_error_detail
from ai_layer.provider_schema import ModelResult


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        provider_name: str,
        api_key: str,
        base_url: str,
        model_name: str,
        supports_json_mode: bool = True,
    ) -> None:
        self.name = provider_name
        self.api_key = api_key or ""
        self.base_url = (base_url or "").rstrip("/")
        self.model_name = model_name or ""
        self.supports_json_mode = supports_json_mode

    def _parse_json(self, text: str) -> dict[str, Any] | None:
        content = (text or "").strip()
        if not content:
            return None
        if content.startswith("```"):
            content = content.strip("`").strip()
            if content.startswith("json"):
                content = content[4:].strip()
        if not content.startswith("{"):
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                content = content[start : end + 1]
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            return None

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

    def _http_error_detail(self, exc: urllib.error.HTTPError) -> str:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        reason = str(getattr(exc, "reason", "") or "").strip()
        detail = ""
        if raw.strip():
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    error_obj = payload.get("error")
                    if isinstance(error_obj, dict):
                        detail = str(error_obj.get("message") or error_obj.get("type") or "")
                    elif isinstance(error_obj, str):
                        detail = error_obj
                    if not detail:
                        detail = str(payload.get("message") or payload.get("detail") or "")
                else:
                    detail = str(payload)
            except Exception:
                detail = raw
        detail = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", detail)
        detail = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", detail)
        detail = re.sub(r"\s+", " ", detail).strip()
        parts = [f"http_error_{exc.code}"]
        if reason:
            parts.append(reason)
        if detail:
            parts.append(detail[:500])
        return ": ".join(parts)

    def _call(
        self,
        prompt: str,
        system_prompt: str | None,
        metadata: dict[str, Any] | None,
        *,
        wants_json: bool = False,
    ) -> ModelResult:
        started = time.perf_counter()
        if not self.api_key:
            return self._failure(started, "api_key_missing")
        if not self.base_url:
            return self._failure(started, "base_url_missing")
        if not self.model_name:
            return self._failure(started, "model_missing")

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt or ""},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": int((metadata or {}).get("max_tokens") or 4096),
        }
        if wants_json and self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        timeout_sec = int((metadata or {}).get("timeout_sec") or 60)
        max_attempts = retry_attempt_count(metadata)
        last_exc: BaseException | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout_sec) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                return self._failure(started, self._http_error_detail(exc))
            except Exception as exc:
                last_exc = exc
                if not is_transient_network_error(exc) or attempt >= max_attempts:
                    return self._failure(started, transient_network_error_detail(exc, attempts=attempt) if is_transient_network_error(exc) else str(exc))
                time.sleep(min(0.8, 0.2 * attempt))
        else:
            return self._failure(started, transient_network_error_detail(last_exc or RuntimeError("network_error"), attempts=max_attempts))
        try:
            content = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            return ModelResult(
                ok=True,
                text=content,
                json_data=self._parse_json(content) if wants_json else None,
                model_provider=self.name,
                model_name=self.model_name,
                error="",
                raw_usage=body.get("usage") or {},
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return self._failure(started, str(exc))

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult:
        return self._call(prompt, system_prompt, metadata)

    def generate_json(
        self,
        prompt: str,
        schema_name: str | None = None,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult:
        return self._call(prompt, system_prompt, metadata, wants_json=True)

    def generate_markdown(
        self,
        prompt: str,
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelResult:
        return self._call(prompt, system_prompt, metadata)
