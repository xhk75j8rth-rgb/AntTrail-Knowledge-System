from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

from storage_config import resolve_lucas_database_runtime


DEFAULT_AGENT_RETRIEVE_ENDPOINT = "/api/agent/retrieve"


@dataclass(slots=True)
class RetrievalResult:
    attempted: bool
    ok: bool
    status: str
    can_answer: bool
    query: str
    context_text: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    answerability: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["context_text_present"] = bool(self.context_text)
        data["context_text_preview"] = self.context_text[:1200]
        data["sources"] = [_compact_source(item) for item in self.sources[:8]]
        data["citations"] = [_compact_citation(item) for item in self.citations[:8]]
        return data


def _clip(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _clean_endpoint(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_AGENT_RETRIEVE_ENDPOINT
    return "/" + text.lstrip("/")


def _compact_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_index": source.get("source_index"),
        "citation_label": source.get("citation_label"),
        "source_type": source.get("source_type"),
        "source_id": source.get("source_id"),
        "title": source.get("title"),
        "path": source.get("path"),
        "chunk_ids": source.get("chunk_ids") if isinstance(source.get("chunk_ids"), list) else [],
        "best_score": source.get("best_score"),
    }


def _compact_citation(citation: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_index": citation.get("source_index"),
        "citation_label": citation.get("citation_label"),
        "chunk_id": citation.get("chunk_id"),
        "title": citation.get("title"),
        "path": citation.get("path"),
        "score": citation.get("score"),
        "text_preview": _clip(citation.get("text"), 500),
        "signals": citation.get("signals") if isinstance(citation.get("signals"), dict) else {},
    }


def _read_response_body(response: Any) -> tuple[dict[str, Any], str]:
    raw = response.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}, ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}, raw
    return parsed if isinstance(parsed, dict) else {}, raw


def _compact_error(data: dict[str, Any], raw: str) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        code = _clip(error.get("code"), 80)
        message = _clip(error.get("message"), 220)
        return ": ".join(item for item in (code, message) if item) or _clip(raw, 240)
    if isinstance(error, str) and error.strip():
        return _clip(error, 240)
    for key in ("detail", "message", "msg"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value, 240)
    return _clip(raw, 240)


class LucasDatabaseRetriever:
    def _runtime(self) -> dict[str, Any]:
        runtime = resolve_lucas_database_runtime()
        base_url = str(os.environ.get("LUCAS_AGENT_RETRIEVE_BASE_URL") or runtime.get("base_url") or "").strip()
        endpoint = _clean_endpoint(os.environ.get("LUCAS_AGENT_RETRIEVE_ENDPOINT") or DEFAULT_AGENT_RETRIEVE_ENDPOINT)
        api_key = str(os.environ.get("LUCAS_AGENT_RETRIEVE_API_KEY") or runtime.get("api_key") or "").strip()
        return {
            "target_id": runtime.get("target_id") or "main",
            "label": runtime.get("label") or "Lucas Database / Brain",
            "base_url": base_url.rstrip("/"),
            "endpoint": endpoint,
            "api_key": api_key,
            "api_key_present": bool(api_key),
            "api_key_env": os.environ.get("LUCAS_AGENT_RETRIEVE_API_KEY_ENV") or runtime.get("api_key_env") or "LUCAS_DB_API_KEY",
        }

    def retrieve(
        self,
        query: str,
        *,
        timeout_sec: int = 45,
        limit: int = 8,
        token_budget: int = 1800,
        max_chunks_per_source: int = 2,
        agent: str = "chat_gateway",
    ) -> RetrievalResult:
        runtime = self._runtime()
        target = {
            "target_id": runtime["target_id"],
            "label": runtime["label"],
            "base_url": runtime["base_url"],
            "endpoint": runtime["endpoint"],
            "api_key_present": runtime["api_key_present"],
            "api_key_env": runtime["api_key_env"],
        }
        clean_query = str(query or "").strip()
        if not clean_query:
            return RetrievalResult(
                attempted=False,
                ok=False,
                status="skipped_empty_query",
                can_answer=False,
                query="",
                target=target,
                error="empty_query",
            )
        if not runtime["base_url"]:
            return RetrievalResult(
                attempted=True,
                ok=False,
                status="retrieval_not_configured",
                can_answer=False,
                query=clean_query,
                target=target,
                error="Lucas Database base_url is not configured.",
            )

        payload = {
            "query": clean_query,
            "limit": max(1, min(int(limit or 8), 50)),
            "token_budget": max(200, min(int(token_budget or 1800), 12000)),
            "max_chunks_per_source": max(1, min(int(max_chunks_per_source or 2), 10)),
            "agent": agent,
            "response_format": "messages",
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Lucas-Chat-Gateway/0.1",
        }
        if runtime["api_key"]:
            headers["Authorization"] = f"Bearer {runtime['api_key']}"

        started = time.perf_counter()
        request = urllib.request.Request(
            runtime["base_url"] + runtime["endpoint"],
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=max(1, int(timeout_sec or 45))) as response:
                data, raw = _read_response_body(response)
                status_code = int(response.status)
        except urllib.error.HTTPError as exc:
            data, raw = _read_response_body(exc)
            return RetrievalResult(
                attempted=True,
                ok=False,
                status="http_error",
                can_answer=False,
                query=clean_query,
                target=target,
                latency_ms=int((time.perf_counter() - started) * 1000),
                status_code=int(exc.code),
                error=_compact_error(data, raw),
            )
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            return RetrievalResult(
                attempted=True,
                ok=False,
                status="connection_error",
                can_answer=False,
                query=clean_query,
                target=target,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=_clip(str(exc), 240),
            )

        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        answerability = data.get("answerability") if isinstance(data.get("answerability"), dict) else {}
        confidence = data.get("confidence") if isinstance(data.get("confidence"), dict) else {}
        sources = data.get("sources") if isinstance(data.get("sources"), list) else []
        citations = data.get("citations") if isinstance(data.get("citations"), list) else []
        warnings = [str(item) for item in data.get("warnings", []) if str(item).strip()] if isinstance(data.get("warnings"), list) else []
        context_text = str(context.get("text") or "")
        can_answer = bool(data.get("ok")) and bool(answerability.get("can_answer")) and bool(context_text.strip())
        return RetrievalResult(
            attempted=True,
            ok=bool(data.get("ok")),
            status=str(data.get("status") or ("ready" if can_answer else "low_confidence")),
            can_answer=can_answer,
            query=clean_query,
            context_text=context_text,
            context=context,
            answerability=answerability,
            confidence=confidence,
            sources=[item for item in sources if isinstance(item, dict)],
            citations=[item for item in citations if isinstance(item, dict)],
            warnings=warnings,
            error=_compact_error(data, raw) if not data.get("ok") else "",
            target=target,
            latency_ms=int((time.perf_counter() - started) * 1000),
            status_code=status_code,
        )
