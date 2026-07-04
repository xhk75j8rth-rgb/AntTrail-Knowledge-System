from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


KNOWN_MESSAGE_TYPES = {"text", "image", "file", "link", "unknown"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clean_message_type(value: Any) -> str:
    message_type = str(value or "text").strip().lower()
    return message_type if message_type in KNOWN_MESSAGE_TYPES else "unknown"


@dataclass(slots=True)
class MessageEvent:
    message_id: str
    channel: str
    conversation_id: str
    sender_id: str
    sender_name: str
    message_type: str
    text: str
    raw_payload: dict[str, Any] = field(default_factory=dict)
    received_at: str = field(default_factory=now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any], default_channel: str = "debug_cli") -> "MessageEvent":
        raw_payload = dict(payload or {})
        metadata = raw_payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return cls(
            message_id=str(raw_payload.get("message_id") or uuid4().hex),
            channel=str(raw_payload.get("channel") or default_channel),
            conversation_id=str(raw_payload.get("conversation_id") or "default"),
            sender_id=str(raw_payload.get("sender_id") or "unknown"),
            sender_name=str(raw_payload.get("sender_name") or raw_payload.get("sender_id") or "unknown"),
            message_type=_clean_message_type(raw_payload.get("message_type")),
            text=str(raw_payload.get("text") or ""),
            raw_payload=raw_payload,
            received_at=str(raw_payload.get("received_at") or now_iso()),
            metadata=metadata,
        )

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        channel: str,
        conversation_id: str = "default",
        sender_id: str = "unknown",
        sender_name: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> "MessageEvent":
        payload = {
            "channel": channel,
            "conversation_id": conversation_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "message_type": "text",
            "text": text,
            "metadata": metadata or {},
        }
        return cls.from_dict(payload, default_channel=channel)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
