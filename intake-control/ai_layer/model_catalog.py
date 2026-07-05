from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from ai_layer.http_network import is_transient_network_error, retry_attempt_count, transient_network_error_detail
from ai_layer.provider_config import ProviderRuntimeConfig


def _extract_model_ids(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        candidates = payload.get("data") or payload.get("models") or payload.get("model_list") or []
    elif isinstance(payload, list):
        candidates = payload
    else:
        candidates = []

    model_ids: list[str] = []
    for item in candidates:
        if isinstance(item, str):
            model_id = item
        elif isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or item.get("model") or "")
        else:
            model_id = ""
        model_id = model_id.strip()
        if model_id and model_id not in model_ids:
            model_ids.append(model_id)
    return model_ids


def fetch_model_catalog(config: ProviderRuntimeConfig, timeout_sec: int = 20) -> dict[str, Any]:
    started = time.perf_counter()
    if not config.api_key:
        return {
            "ok": False,
            "status": "failed",
            "provider": config.masked(),
            "models": [],
            "error": "api_key_missing",
            "latency_ms": 0,
            "used_mcp": False,
        }
    if not config.base_url:
        return {
            "ok": False,
            "status": "failed",
            "provider": config.masked(),
            "models": [],
            "error": "base_url_missing",
            "latency_ms": 0,
            "used_mcp": False,
        }

    headers = {"Content-Type": "application/json"}
    if config.protocol == "anthropic_messages":
        headers["x-api-key"] = config.api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {config.api_key}"

    req = urllib.request.Request(
        f"{config.base_url.rstrip('/')}/models",
        headers=headers,
        method="GET",
    )
    max_attempts = retry_attempt_count({"network_retry_attempts": 2})
    last_exc: BaseException | None = None
    try:
        for attempt in range(1, max_attempts + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout_sec) as response:
                    body_text = response.read().decode("utf-8", errors="replace")
                break
            except urllib.error.HTTPError:
                raise
            except Exception as exc:
                last_exc = exc
                if not is_transient_network_error(exc) or attempt >= max_attempts:
                    raise
                time.sleep(min(0.8, 0.2 * attempt))
        else:
            raise last_exc or RuntimeError("network_error")
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "status": "failed",
                "provider": config.masked(),
                "models": [],
                "error": "invalid_json_response",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "used_mcp": False,
            }
        models = _extract_model_ids(payload)
        return {
            "ok": True,
            "status": "loaded",
            "provider": config.masked(),
            "models": models,
            "model_count": len(models),
            "error": "" if models else "no_models_found",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "used_mcp": False,
        }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status": "failed",
            "provider": config.masked(),
            "models": [],
            "error": f"http_error_{exc.code}",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "used_mcp": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "failed",
            "provider": config.masked(),
            "models": [],
            "error": transient_network_error_detail(exc, attempts=max_attempts) if is_transient_network_error(exc) else str(exc),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "used_mcp": False,
        }
