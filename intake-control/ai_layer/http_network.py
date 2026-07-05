from __future__ import annotations

import http.client
import os
import re
import socket
import ssl
import urllib.error
from typing import Any


def _error_text(exc: BaseException) -> str:
    parts = [str(exc)]
    reason = getattr(exc, "reason", None)
    if reason is not None and reason is not exc:
        parts.append(str(reason))
    return " ".join(part for part in parts if part)


def sanitize_error_text(text: Any) -> str:
    value = str(text or "")
    value = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", value)
    value = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", value)
    value = re.sub(r"(?i)(api[_-]?key|authorization|token|secret)\s*[:=]\s*[^\s,;]+", r"\1=***", value)
    return re.sub(r"\s+", " ", value).strip()


def transient_network_error_code(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
        return "timeout"
    if isinstance(exc, socket.timeout) or isinstance(reason, socket.timeout):
        return "timeout"
    if isinstance(exc, ssl.SSLEOFError) or isinstance(reason, ssl.SSLEOFError):
        return "ssl_unexpected_eof"
    if isinstance(exc, http.client.RemoteDisconnected) or isinstance(reason, http.client.RemoteDisconnected):
        return "remote_disconnected"
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return "connection_closed"
    if isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return "connection_closed"

    text = _error_text(exc).casefold()
    if "unexpected_eof_while_reading" in text or "eof occurred in violation of protocol" in text:
        return "ssl_unexpected_eof"
    if "remote end closed connection without response" in text or "remotedisconnected" in text:
        return "remote_disconnected"
    if "connection reset" in text or "connection aborted" in text or "broken pipe" in text:
        return "connection_closed"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "temporary failure in name resolution" in text:
        return "dns_temporary_failure"
    return ""


def is_transient_network_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return False
    return bool(transient_network_error_code(exc))


def transient_network_error_detail(exc: BaseException, *, attempts: int) -> str:
    code = transient_network_error_code(exc) or "network_error"
    detail = sanitize_error_text(_error_text(exc))[:260]
    suffix = f"attempts={max(1, int(attempts or 1))}"
    return f"transient_network_error: {code}: {detail} ({suffix})"


def retry_attempt_count(metadata: dict[str, Any] | None = None, *, default: int = 2, maximum: int = 3) -> int:
    raw = (metadata or {}).get("network_retry_attempts") or os.environ.get("AI_LAYER_HTTP_RETRY_ATTEMPTS") or default
    try:
        value = int(raw)
    except Exception:
        value = default
    return max(1, min(value, maximum))
