"""Tool handlers for the Lucas Database Hermes plugin."""

import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def _clamp_int(value, fallback, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(parsed, maximum))


def _base_url():
    raw = (
        os.getenv("LUCAS_AGENT_RETRIEVE_BASE_URL")
        or os.getenv("LUCAS_DB_API_BASE_URL")
        or DEFAULT_BASE_URL
    )
    return raw.rstrip("/")


def _token():
    return os.getenv("LUCAS_AGENT_RETRIEVE_TOKEN") or os.getenv("LUCAS_DB_API_TOKEN") or ""


def _json_response(payload):
    return json.dumps(payload, ensure_ascii=False)


def lucas_retrieve(args: dict, **kwargs) -> str:
    """Call Lucas Database Agent Retrieve API and always return a JSON string."""
    query = str(args.get("query", "")).strip()
    if not query:
        return _json_response({
            "ok": False,
            "error": {
                "code": "LUCAS_QUERY_REQUIRED",
                "message": "query is required",
            },
        })

    body = {
        "query": query,
        "limit": _clamp_int(args.get("limit"), 6, 1, 50),
        "token_budget": _clamp_int(args.get("token_budget"), 1800, 200, 12000),
        "max_chunks_per_source": _clamp_int(args.get("max_chunks_per_source"), 2, 1, 10),
        "agent": "hermes",
        "response_format": "messages"
        if args.get("response_format") == "messages"
        else "default",
    }
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        f"{_base_url()}/api/agent/retrieve",
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
        try:
            return _json_response(json.loads(raw))
        except json.JSONDecodeError:
            return _json_response({
                "ok": False,
                "error": {
                    "code": "LUCAS_INVALID_JSON",
                    "message": "Lucas API returned non-JSON response",
                    "body": raw,
                },
            })
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body_text)
        except json.JSONDecodeError:
            body = body_text
        return _json_response({
            "ok": False,
            "error": {
                "code": "LUCAS_HTTP_ERROR",
                "status": exc.code,
                "message": exc.reason,
                "body": body,
            },
        })
    except Exception as exc:
        return _json_response({
            "ok": False,
            "error": {
                "code": "LUCAS_RETRIEVE_FAILED",
                "message": str(exc),
            },
        })
