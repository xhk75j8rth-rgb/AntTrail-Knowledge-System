from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ai_layer.composed_card_schema import COMPOSED_CARD_SCHEMA_NAME, COMPOSED_CARD_SCHEMA_VERSION, safe_filename_part
from storage_config import resolve_lucas_database_runtime
from tools.write_lucas_database import api_post, clean_text, extract_response_id


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def short_text(value: Any, limit: int) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    text = clean_text(value)
    if not text:
        return []
    parts = [item.strip(" -\t") for item in re.split(r"[\n；;]+", text)]
    return [item for item in parts if item]


def pick_title(payload: dict[str, Any]) -> str:
    for key in ("title", "display_title", "source_title", "subject", "name"):
        value = clean_text(payload.get(key))
        if value:
            return short_text(value, 80)
    body = clean_text(payload.get("content") or payload.get("text") or payload.get("message") or "")
    return short_text(body, 40) or "手动文本暂存卡"


def build_form_card(payload: dict[str, Any]) -> dict[str, Any]:
    title = pick_title(payload)
    body = clean_text(payload.get("content") or payload.get("text") or payload.get("message") or payload.get("description") or "")
    summary = clean_text(payload.get("summary") or payload.get("one_sentence_summary") or short_text(body or title, 120))
    tags = as_list(payload.get("tags")) or ["手动文本"]
    core_points = as_list(payload.get("core_points"))
    if not core_points:
        core_points = [summary] if summary else [title]
    if len(core_points) < 3:
        for fallback in (body, payload.get("category"), payload.get("source_url")):
            text = short_text(fallback, 120)
            if text and text not in core_points:
                core_points.append(text)
            if len(core_points) >= 3:
                break
    while len(core_points) < 3:
        core_points.append(f"来自表单字段的补充材料 {len(core_points) + 1}")

    knowledge_blocks = []
    for index, point in enumerate(core_points[:3]):
        knowledge_blocks.append({
            "concept": short_text(point, 24),
            "explanation": point,
            "evidence": short_text(body or point, 160),
            "reusable_value": "把这条手动输入沉淀为可检索、可复盘的临时知识材料。",
        })

    return {
        "schema_name": COMPOSED_CARD_SCHEMA_NAME,
        "schema_version": COMPOSED_CARD_SCHEMA_VERSION,
        "card_type": "temporary_card",
        "content_level": "Level 1 手动文本暂存级",
        "quality_level": "low",
        "source_title": title,
        "display_title": title,
        "safe_filename_title": f"{datetime.now().strftime('%Y-%m-%d')}_{safe_filename_part(title, 36)}",
        "one_sentence_summary": summary,
        "original_summary": short_text(body or summary or title, 260),
        "core_points": core_points[:6],
        "knowledge_blocks": knowledge_blocks[:3],
        "methodology": as_list(payload.get("methodology")),
        "application_suggestions": as_list(payload.get("application_suggestions")) or ["后续可根据这条记录补充来源、证据和行动结果。"],
        "follow_up_actions": as_list(payload.get("follow_up_actions")) or ["在 Lucas Database 中补充关联主题和后续状态。"],
        "comment_signals": {"comments_hash": ""},
        "reusable_value": as_list(payload.get("reusable_value")) or ["把零散手动输入转成统一 ComposedCardV1，供数据库索引和复用。"],
        "risks": as_list(payload.get("risks")) or ["手动文本未经过外部来源读取、模型写卡和正式质量门禁，只能代表提交者输入。"],
        "tags": tags[:8],
        "evidence_quotes": as_list(payload.get("evidence_quotes")) or ([short_text(body, 120)] if body else [title]),
        "appendix_transcript_excerpt": "",
        "comments_job_id": "",
        "comments_video_id": "",
        "comments_hash": "",
        "composer_input_comments_hash": "",
        "composed_card_comments_hash": "",
        "composer_status": "not_run",
        "composer_error": "",
        "model_used": "manual_text_adapter",
        "model_provider": "local_backend",
    }


def render_markdown(card: dict[str, Any], payload: dict[str, Any]) -> str:
    knowledge_blocks = "\n".join(
        f"### {block['concept']}\n\n"
        f"- 说明：{block['explanation']}\n"
        f"- 依据：{block['evidence']}\n"
        f"- 可复用价值：{block['reusable_value']}"
        for block in card["knowledge_blocks"]
    )
    return f"""# {card["display_title"]}

> 来源：手动文本测试
> Schema：{card["schema_name"]} / {card["schema_version"]}

## 一句话总结

{card["one_sentence_summary"]}

## 原始摘要

{card["original_summary"]}

## 核心观点

{chr(10).join(f"- {item}" for item in card["core_points"])}

## 关键知识块

{knowledge_blocks}

## 应用建议

{chr(10).join(f"- {item}" for item in card["application_suggestions"])}

## 后续动作

{chr(10).join(f"- {item}" for item in card["follow_up_actions"])}

## 风险与不确定性

{chr(10).join(f"- {item}" for item in card["risks"])}

## 标签

{chr(10).join(f"[[{tag}]]" for tag in card["tags"])}
"""


def build_submit_ingest_payload(form_payload: dict[str, Any], *, actor: str = "form") -> dict[str, Any]:
    card = build_form_card(form_payload)
    markdown = render_markdown(card, form_payload)
    source_url = clean_text(form_payload.get("source_url") or form_payload.get("url") or "")
    raw_text = clean_text(form_payload.get("content") or form_payload.get("text") or form_payload.get("message") or "")
    return {
        "card": card,
        "source_material": {
            "source_url": source_url,
            "source_type": clean_text(form_payload.get("source_type") or "manual_text"),
            "raw_title": card["source_title"],
            "raw_text": raw_text,
            "transcript": "",
            "ocr_text": "",
            "comments": [],
            "metadata": {
                "submitted_at": now_iso(),
                "form_fields": {key: value for key, value in form_payload.items() if key not in {"api_key", "token"}},
            },
        },
        "quality_gate": {
            "passed": False,
            "quality_level": card["quality_level"],
            "errors": ["manual_text_bypasses_source_reader_model_composer_quality_gate"],
            "warnings": ["手动文本测试为用户输入材料，未经过网页读取、模型写卡、转写、评论增强或正式质量门禁。"],
            "raw_json": {"source": "manual_text_adapter", "passed": False},
        },
        "rendered_views": {
            "markdown": markdown,
            "plain_text": "\n".join([card["display_title"], card["one_sentence_summary"], *card["core_points"]]),
        },
        "relations": [],
        "target_path": f"/知识卡/{card['tags'][0]}/{card['safe_filename_title']}",
        "actor": actor,
    }


def submit_to_lucas_database(form_payload: dict[str, Any], *, timeout_sec: int = 20) -> dict[str, Any]:
    runtime = resolve_lucas_database_runtime()
    ingest_payload = build_submit_ingest_payload(form_payload, actor=clean_text(form_payload.get("actor") or "form"))
    result: dict[str, Any] = {
        "ok": False,
        "stage": "initializing",
        "base_url": runtime["base_url"],
        "endpoint": runtime["endpoint"],
        "card_id": "",
        "node_id": "",
        "path": ingest_payload["target_path"],
        "status_code": None,
        "error": "",
        "api_key_present": bool(runtime["api_key"]),
        "used_mcp": False,
        "ingest_request": ingest_payload,
    }
    if not runtime["api_key"]:
        result.update({
            "stage": "auth",
            "error": f"Missing Lucas Database API key environment variable: {runtime['api_key_env']}",
        })
        return result

    try:
        status_code, response_json, raw_response = api_post(
            runtime["base_url"],
            runtime["endpoint"],
            runtime["api_key"],
            ingest_payload,
            timeout_sec,
        )
        response_ok = 200 <= status_code < 300 and response_json.get("ok", True) is not False
        result.update({
            "ok": response_ok,
            "stage": "completed" if response_ok else "api_response",
            "status_code": status_code,
            "card_id": extract_response_id(response_json, "card_id", "id"),
            "node_id": extract_response_id(response_json, "node_id"),
            "path": extract_response_id(response_json, "path") or ingest_payload["target_path"],
            "error": "" if response_ok else clean_text(response_json.get("error") or response_json.get("message") or raw_response or f"HTTP {status_code}"),
            "response": response_json if response_json else {"raw": raw_response[:1000]},
        })
        return result
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        try:
            response_json = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            response_json = {}
        result.update({
            "stage": "api_response",
            "status_code": int(exc.code),
            "error": clean_text(response_json.get("error") or response_json.get("message") or raw or exc.reason),
            "response": response_json if response_json else {"raw": raw[:1000]},
        })
    except urllib.error.URLError as exc:
        result.update({"stage": "connect", "error": f"Lucas Database API unavailable: {exc.reason}"})
    except Exception as exc:
        result.update({"stage": "connect", "error": str(exc)})
    return result
