from __future__ import annotations

from typing import Any

from chat_gateway.message_router import route_message
from chat_gateway.message_schema import MessageEvent
from chat_gateway.response_schema import HandlerResponse


def event_from_payload(payload: dict[str, Any]) -> MessageEvent:
    return MessageEvent.from_dict(payload or {}, default_channel="my_chat_app")


def _metadata_flag(payload: dict[str, Any], key: str) -> Any:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and key in metadata:
        return metadata.get(key)
    return None


def handle_chat_payload(
    payload: dict[str, Any],
    *,
    dry_run: bool | None = None,
    timeout_sec: int | None = None,
) -> HandlerResponse:
    event = event_from_payload(payload)
    payload_dry_run = payload.get("dry_run") if isinstance(payload, dict) else None
    payload_timeout = payload.get("timeout_sec") if isinstance(payload, dict) else None
    metadata_dry_run = _metadata_flag(payload, "dry_run")
    metadata_timeout = _metadata_flag(payload, "timeout_sec")
    effective_dry_run = bool(dry_run if dry_run is not None else (payload_dry_run if payload_dry_run is not None else metadata_dry_run))
    effective_timeout = int(timeout_sec if timeout_sec is not None else (payload_timeout if payload_timeout is not None else metadata_timeout or 3600))
    return route_message(event, dry_run=effective_dry_run, timeout_sec=effective_timeout)
