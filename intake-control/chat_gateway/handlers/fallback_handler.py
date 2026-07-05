from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ai_layer.chat_responder import ChatResponder
from chat_gateway.message_schema import MessageEvent
from chat_gateway.response_schema import HandlerResponse


RESPONDER = ChatResponder()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
REVISION_RUNTIME_DIR = PROJECT_ROOT / "runtime" / "jobs"


def _metadata_int(metadata: dict, key: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(metadata.get(key) or default)
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _safe_slug(value: str, limit: int = 32) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", str(value or "")).strip("-")
    return (text or "note-revision")[:limit].strip("-") or "note-revision"


def _create_revision_job(event: MessageEvent, reply) -> dict:
    title = str(reply.data.get("candidate_title") or "未确定笔记")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    job_id = f"revision-{timestamp}-{_safe_slug(title, 24)}"
    job_dir = REVISION_RUNTIME_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_name": "RevisionIntentV1",
        "schema_version": "1",
        "job_id": job_id,
        "job_dir": str(job_dir),
        "conversation_id": event.conversation_id,
        "message_id": event.message_id,
        "candidate_title": title,
        "current_request": reply.data.get("current_request", ""),
        "revision_mode": reply.data.get("revision_mode", "discuss_then_modify"),
        "status": "pending_quality_gate",
        "write_policy": "not_started",
        "note": "This is a reviewable revision intent. It has not overwritten, moved, deleted, or written an existing note.",
        "model": {
            "called": reply.data.get("model_called", False),
            "ok": reply.data.get("model_result_ok"),
            "provider": reply.data.get("model_provider", ""),
            "name": reply.data.get("model_name", ""),
            "fallback_reply_used": reply.data.get("fallback_reply_used", False),
        },
    }
    (job_dir / "revision_intent.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (job_dir / "status.json").write_text(json.dumps({
        "ok": True,
        "job_id": job_id,
        "status": "pending_quality_gate",
        "final_status": "revision_intent_recorded",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (job_dir / "result.json").write_text(json.dumps({
        "ok": True,
        "job_id": job_id,
        "job_dir": str(job_dir),
        "final_status": "revision_intent_recorded",
        "card_type": "revision_intent",
        "title": title,
        "revision_intent": payload,
        "siyuan_write_ok": False,
        "lucas_database_write_ok": False,
        "used_mcp": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _revision_queue(revision_job: dict) -> dict:
    item = {
        "index": 1,
        "url": "",
        "title": f"修订：{revision_job.get('candidate_title') or '未确定笔记'}",
        "detail": revision_job.get("current_request") or "已记录修订意图",
        "item_type": "note_revision",
        "queue_status": "in_progress",
        "card_status": "revision_intent_recorded",
        "write_status": "pending_policy",
        "final_status": "pending_quality_gate",
        "job_id": revision_job.get("job_id"),
        "job_dir": revision_job.get("job_dir"),
        "progress": 35,
        "stages": ["修订草稿", "质量门禁", "写入策略"],
    }
    return {
        "mode": "single",
        "status": "pending",
        "total": 1,
        "completed": 0,
        "pending": 0,
        "in_progress": 1,
        "unfinished": 1,
        "failed": 0,
        "card_failed": 0,
        "write_failed": 0,
        "items": [item],
    }


def handle(event: MessageEvent) -> HandlerResponse:
    reply = RESPONDER.respond(
        event.text,
        conversation_history=event.metadata.get("conversation_history"),
        timeout_sec=_metadata_int(event.metadata, "agent_timeout_sec", 60, minimum=1, maximum=180),
        max_tokens=_metadata_int(event.metadata, "agent_max_tokens", 1200, minimum=64, maximum=4096),
    )
    revision_job = None
    revision_queue = None
    if reply.ok and reply.intent == "note_revision_request":
        revision_job = _create_revision_job(event, reply)
        revision_queue = _revision_queue(revision_job)

    data = {
        "message_id": event.message_id,
        "channel": event.channel,
        "conversation_id": event.conversation_id,
        "intent": reply.intent,
        "confidence": reply.confidence,
        "requires_tool": reply.data.get("requires_tool", False),
        "revision": {
            "requested": reply.data.get("revision_request", False),
            "mode": reply.data.get("revision_mode", ""),
            "candidate_title": reply.data.get("candidate_title", ""),
            "current_request": reply.data.get("current_request", ""),
            "requested_fields": reply.data.get("requested_fields", []),
        },
        "agent": {
            "model_called": reply.data.get("model_called", False),
            "model_result_ok": reply.data.get("model_result_ok"),
            "model_provider": reply.data.get("model_provider", ""),
            "model_name": reply.data.get("model_name", ""),
            "latency_ms": reply.data.get("latency_ms", 0),
            "history_messages_used": reply.data.get("history_messages_used", 0),
        },
    }
    if revision_job:
        data["revision_job"] = revision_job
        data["queue"] = revision_queue
    if "storage" in reply.data:
        data["storage"] = reply.data["storage"]
    if "ai_config" in reply.data:
        data["ai_config"] = reply.data["ai_config"]
    if "retrieval" in reply.data:
        data["retrieval"] = reply.data["retrieval"]
    if "retrieval_plan" in reply.data:
        data["retrieval_plan"] = reply.data["retrieval_plan"]
    if "database_first_enforced" in reply.data:
        data["database_first_enforced"] = reply.data["database_first_enforced"]

    return HandlerResponse(
        ok=reply.ok,
        reply_text=reply.reply_text,
        job_id=revision_job.get("job_id") if revision_job else None,
        status=reply.intent if reply.ok else f"{reply.intent}_model_failed",
        handled_by="fallback_handler",
        error=reply.error or None,
        data=data,
    )
