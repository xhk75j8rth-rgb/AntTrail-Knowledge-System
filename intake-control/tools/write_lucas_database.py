#!/usr/bin/env python
"""
Lucas Database API writer.

This script is an independent Storage Sink. It reads an existing link job,
keeps ComposedCardV1 as the canonical card object, verifies the recorded
quality gate, builds rendered views, and posts the ingest request to
POST /api/cards/ingest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_layer.composed_card_schema import extract_composed_card_payload
from storage_config import resolve_lucas_database_runtime


RESULT_FILENAME = "write_lucas_database_result.json"
REQUEST_FILENAME = "lucas_database_ingest_request.json"
DEFAULT_ASSET_BASE_URL = "http://127.0.0.1:3963"
ASSET_BASE_URL_ENV = "LUCAS_ASSET_BASE_URL"
ALLOWED_OCR_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_INGEST_CARD_TYPES = {
    "formal_summary",
    "temporary_review_card",
    "temporary_card",
    "failure_card",
    "extracted_source_card",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def sha256_text(text: Any) -> str:
    value = clean_text(text)
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_link_pipeline_config() -> dict[str, Any]:
    config_path = PROJECT_ROOT / "config" / "link_pipeline.json"
    if not config_path.exists():
        config_path = PROJECT_ROOT / "config" / "link_pipeline.example.json"
    config = read_json(config_path)
    return config if isinstance(config, dict) else {}


def resolve_asset_base_url() -> str:
    env_value = clean_text(os.environ.get(ASSET_BASE_URL_ENV))
    if env_value:
        return env_value.rstrip("/")
    config = load_link_pipeline_config()
    configured = clean_text(
        config.get("asset_base_url")
        or config.get("chat_gateway_base_url")
        or config.get("public_base_url")
    )
    return (configured or DEFAULT_ASSET_BASE_URL).rstrip("/")


def normalize_local_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def quote_url_part(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def local_ocr_image_asset_url(job_dir: Path, image_path: Any, asset_base_url: str) -> str:
    normalized = normalize_local_path(image_path)
    if not normalized:
        return ""
    image_name = Path(normalized).name
    if Path(image_name).suffix.casefold() not in ALLOWED_OCR_IMAGE_SUFFIXES:
        return ""
    return (
        f"{asset_base_url.rstrip('/')}/api/jobs/"
        f"{quote_url_part(job_dir.name)}/ocr-images/{quote_url_part(image_name)}"
    )


def safe_path_part(text: Any, fallback: str = "未分类") -> str:
    value = clean_text(text)
    value = re.sub(r'[\\/:*?"<>|#@\r\n\t]+', "_", value)
    value = re.sub(r"_+", "_", value).strip("._ ")
    return value[:60].strip("._ ") or fallback


def strip_date_prefix(text: str) -> str:
    return re.sub(r"^\d{4}-\d{2}-\d{2}[_\s-]+", "", clean_text(text))


def date_prefix_from_job(job_dir: Path) -> str:
    match = re.match(r"^(\d{4})(\d{2})(\d{2})", job_dir.name)
    if match:
        return "-".join(match.groups())
    return datetime.now().strftime("%Y-%m-%d")


def markdown_heading(markdown: str) -> str:
    for line in (markdown or "").splitlines():
        text = clean_text(line)
        if text.startswith("#"):
            return clean_text(text.lstrip("#").strip())
    return ""


def normalize_card_type(value: Any) -> str:
    text = clean_text(value)
    if text in ALLOWED_INGEST_CARD_TYPES:
        return text
    return "temporary_card"


def infer_job_title(job_dir: Path, result: dict[str, Any], content: dict[str, Any], markdown: str) -> str:
    title = clean_text(
        result.get("title")
        or content.get("title")
        or content.get("raw_title")
        or markdown_heading(markdown)
    )
    if title:
        return title
    source_url = clean_text(result.get("url") or (read_json(job_dir / "input.json") or {}).get("url"))
    return source_url or job_dir.name


def non_formal_summary(card_type: str) -> str:
    mapping = {
        "temporary_review_card": "临时复核卡：模型写卡或质量门禁未完成，不能当作正式知识卡。",
        "temporary_card": "临时卡：来源材料不足或低置信，已保留以便后续补充。",
        "failure_card": "失败记录卡：自动处理链路失败，保留错误和后续排查入口。",
        "extracted_source_card": "来源材料卡：已读取到部分公开材料，但尚未形成正式知识卡。",
    }
    return mapping.get(card_type, "非正式卡：测试阶段写入 Brain，用于保留材料和复盘状态。")


def build_non_formal_card(job_dir: Path, artifacts: dict[str, Any], source_type: str, markdown: str) -> dict[str, Any]:
    result = artifacts["result"] if isinstance(artifacts.get("result"), dict) else {}
    content = artifacts["content"] if isinstance(artifacts.get("content"), dict) else {}
    quality_gate = artifacts["quality_gate"] if isinstance(artifacts.get("quality_gate"), dict) else {}
    card_type = normalize_card_type(result.get("card_type") or quality_gate.get("final_card_type"))
    title = infer_job_title(job_dir, result, content, markdown)
    summary = non_formal_summary(card_type)
    safe_title = f"{date_prefix_from_job(job_dir)}_{safe_path_part(strip_date_prefix(title), '待复核知识卡')}"
    failed_checks = quality_gate.get("failed_checks") or result.get("failed_checks") or []
    if not isinstance(failed_checks, list):
        failed_checks = [str(failed_checks)]
    blocker_text = "；".join(clean_text(item) for item in failed_checks if clean_text(item))
    if not blocker_text:
        blocker_text = clean_text(quality_gate.get("downgrade_reason") or result.get("final_status") or "non_formal_card")
    core_points = [
        summary,
        f"当前卡片类型为 {card_type}，quality_gate.passed=false，不能冒充 formal_summary。",
        f"原始材料和处理状态保存在 runtime job：{job_dir.name}。",
    ]
    if blocker_text:
        core_points.append(f"降级或失败原因：{blocker_text}")
    source_text = clean_text(content.get("visible_text") or content.get("main_text") or content.get("description") or "")
    if source_text:
        core_points.append(f"来源材料摘录：{source_text[:160]}")
    knowledge_blocks = [
        {
            "concept": "卡片状态",
            "explanation": summary,
            "evidence": clean_text(result.get("final_status") or card_type),
            "reusable_value": "把非正式卡也纳入 Brain，方便测试阶段追踪每个 intake job 的结果。",
        },
        {
            "concept": "质量边界",
            "explanation": "这张卡没有通过正式质量门禁，后续检索和展示必须保留低置信标识。",
            "evidence": blocker_text or "quality_gate_not_passed",
            "reusable_value": "避免为了写入数据库而把临时材料伪装成正式知识卡。",
        },
        {
            "concept": "后续处理",
            "explanation": "后续可补充文本、OCR、转写、评论或人工材料后重新走 composer 与 quality gate。",
            "evidence": source_type,
            "reusable_value": "让 Storage Sink 只负责保留状态，正式知识生产仍交给 composer 和 quality gate。",
        },
    ]
    tags = ["临时卡"]
    if card_type == "failure_card":
        tags = ["失败记录", "临时卡"]
    elif card_type == "extracted_source_card":
        tags = ["来源材料", "临时卡"]
    elif card_type == "temporary_review_card":
        tags = ["待模型复核", "临时卡"]
    return {
        "schema_name": "ComposedCardV1",
        "schema_version": "1",
        "card_type": card_type,
        "content_level": clean_text(result.get("content_level") or ("Level 1 失败记录级" if card_type == "failure_card" else "Level 1/2 临时材料级")),
        "quality_level": "low",
        "source_title": strip_date_prefix(title),
        "display_title": strip_date_prefix(title),
        "safe_filename_title": safe_title,
        "one_sentence_summary": summary,
        "original_summary": clean_text(source_text or markdown)[:260],
        "core_points": core_points[:6],
        "knowledge_blocks": knowledge_blocks,
        "methodology": [],
        "application_suggestions": ["后续补齐来源材料后重新进入模型写卡和质量门禁。"],
        "follow_up_actions": ["检查 runtime job 产物、补充缺失材料，并重新提交 intake。"],
        "comment_signals": {"comments_hash": ""},
        "reusable_value": ["测试阶段每个 intake 结果都可被 Brain 检索，但必须保留非正式卡边界。"],
        "risks": ["这不是正式知识卡，不能直接代表来源的完整观点或可靠结论。"],
        "tags": tags,
        "evidence_quotes": [clean_text(source_text or markdown)[:160]] if clean_text(source_text or markdown) else [],
        "appendix_transcript_excerpt": "",
        "comments_job_id": clean_text(result.get("job_id") or job_dir.name),
        "comments_video_id": "",
        "comments_hash": "",
        "composer_input_comments_hash": "",
        "composed_card_comments_hash": "",
        "composer_status": clean_text(result.get("composer_status") or "not_run"),
        "composer_error": clean_text(result.get("composer", {}).get("composer_error") if isinstance(result.get("composer"), dict) else ""),
        "model_used": clean_text(result.get("model_used") or ""),
        "model_provider": clean_text(result.get("model_provider") or ""),
    }


def infer_source_type(job_dir: Path, result: dict[str, Any], content: dict[str, Any], source_url: str) -> str:
    explicit = clean_text(result.get("source_type") or content.get("source_type") or content.get("content_type"))
    if explicit:
        return explicit
    if "douyin" in source_url.casefold():
        return "video/douyin"
    return "webpage"


def source_type_label(source_type: str) -> str:
    mapping = {
        "video/douyin": "视频",
        "webpage": "网页",
        "text": "文本",
    }
    return mapping.get(source_type, safe_path_part(source_type, "未分类"))


def load_job_artifacts(job_dir: Path) -> dict[str, Any]:
    return {
        "card": read_json(job_dir / "composed_card.json"),
        "taxonomy_decision": read_json(job_dir / "taxonomy_decision.json"),
        "quality_gate": read_json(job_dir / "quality_gate.json"),
        "composer_input": read_json(job_dir / "composer_input.json"),
        "content": read_json(job_dir / "content.json"),
        "transcript": read_json(job_dir / "transcript.json"),
        "ocr": read_json(job_dir / "ocr.json"),
        "comments": read_json(job_dir / "comments.json"),
        "input": read_json(job_dir / "input.json"),
        "result": read_json(job_dir / "result.json"),
    }


def select_markdown_path(job_dir: Path, result: dict[str, Any]) -> Path | None:
    candidates: list[Path] = []
    written_source = clean_text(result.get("written_card_source"))
    written_path = clean_text(result.get("written_card_path") or result.get("card_path"))
    if written_source:
        candidates.append(job_dir / written_source)
    if written_path:
        candidates.append(Path(written_path))
    candidates.extend([
        job_dir / "composed_card.md",
        job_dir / "improved_card.md",
        job_dir / "temporary_review_card.md",
        job_dir / "card.md",
    ])
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate if candidate.is_absolute() else job_dir / candidate
        try:
            resolved = resolved.resolve()
        except OSError:
            pass
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def build_plain_text(card: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("display_title", "one_sentence_summary", "original_summary"):
        value = clean_text(card.get(key))
        if value:
            parts.append(value)
    for label, key in (
        ("核心观点", "core_points"),
        ("方法论 / 流程", "methodology"),
        ("可复用价值", "reusable_value"),
        ("应用建议", "application_suggestions"),
        ("后续动作", "follow_up_actions"),
        ("风险与不确定性", "risks"),
    ):
        values = card.get(key) or []
        if isinstance(values, list) and values:
            parts.append(label)
            parts.extend(f"- {clean_text(item)}" for item in values if clean_text(item))
    blocks = card.get("knowledge_blocks") or []
    if isinstance(blocks, list) and blocks:
        parts.append("关键知识块")
        for block in blocks:
            if not isinstance(block, dict):
                continue
            concept = clean_text(block.get("concept"))
            explanation = clean_text(block.get("explanation"))
            evidence = clean_text(block.get("evidence"))
            reusable = clean_text(block.get("reusable_value"))
            line = "；".join(item for item in (concept, explanation, evidence, reusable) if item)
            if line:
                parts.append(f"- {line}")
    return "\n".join(parts)


def normalize_comments(composer_input: dict[str, Any], comments_json: dict[str, Any]) -> list[dict[str, Any]]:
    comments = composer_input.get("comments") if isinstance(composer_input, dict) else {}
    items = comments.get("comment_items") if isinstance(comments, dict) else None
    if not items:
        items = comments_json.get("comments") if isinstance(comments_json, dict) else []
    normalized: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return normalized
    for item in items[:50]:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "id": item.get("id"),
            "author": item.get("author"),
            "text": item.get("text"),
            "like_count": item.get("like_count"),
            "reply_count": item.get("reply_count"),
            "create_time": item.get("create_time"),
        })
    return normalized


def normalize_ocr_evidence_items(composer_input: dict[str, Any], ocr_json: dict[str, Any]) -> list[dict[str, Any]]:
    ocr_input = composer_input.get("ocr") if isinstance(composer_input.get("ocr"), dict) else {}
    if "evidence_entries" in ocr_input:
        items = ocr_input.get("evidence_entries") or []
    elif "evidence_items" in ocr_input:
        items = ocr_input.get("evidence_items") or []
    else:
        items = ocr_json.get("text_items") or []
    normalized: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return normalized
    for item in items[:50]:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "text": item.get("text"),
            "frame_index": item.get("frame_index"),
            "frame_path": item.get("frame_path") or item.get("frame"),
            "timestamp_sec": item.get("timestamp_sec"),
            "score": item.get("score"),
            "image_worth_saving": bool(item.get("image_worth_saving")),
            "visual_value_reason": item.get("visual_value_reason") or "",
        })
    return normalized


def collect_ocr_image_paths(job_dir: Path, artifacts: dict[str, Any]) -> list[str]:
    paths: list[str] = []

    def add_path(value: Any) -> None:
        normalized = normalize_local_path(value)
        if not normalized:
            return
        if Path(normalized).suffix.casefold() not in ALLOWED_OCR_IMAGE_SUFFIXES:
            return
        paths.append(normalized)

    ocr_material = read_json(job_dir / "ocr_material.json")
    if isinstance(ocr_material, dict):
        sampling = ocr_material.get("sampling") if isinstance(ocr_material.get("sampling"), dict) else {}
        frames = sampling.get("frames") if isinstance(sampling.get("frames"), list) else []
        for frame in frames:
            if isinstance(frame, dict):
                add_path(frame.get("path"))

        evidence_items = ocr_material.get("evidence_items") if isinstance(ocr_material.get("evidence_items"), list) else []
        for item in evidence_items:
            if isinstance(item, dict):
                add_path(item.get("frame_path") or item.get("frame"))

    composer_input = artifacts.get("composer_input") if isinstance(artifacts.get("composer_input"), dict) else {}
    ocr_input = composer_input.get("ocr") if isinstance(composer_input.get("ocr"), dict) else {}
    for key in ("evidence_entries", "evidence_items", "text_items"):
        items = ocr_input.get(key) if isinstance(ocr_input.get(key), list) else []
        for item in items:
            if isinstance(item, dict):
                add_path(item.get("frame_path") or item.get("frame") or item.get("path"))

    seen: set[str] = set()
    deduped: list[str] = []
    for path in paths:
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def ocr_image_url_map(job_dir: Path, artifacts: dict[str, Any], asset_base_url: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in collect_ocr_image_paths(job_dir, artifacts):
        url = local_ocr_image_asset_url(job_dir, path, asset_base_url)
        if not url:
            continue
        mapping[path] = url
        mapping[path.replace("/", "\\")] = url
    return mapping


def rewrite_ocr_image_links(markdown: str, job_dir: Path, artifacts: dict[str, Any], asset_base_url: str) -> str:
    if not markdown:
        return markdown
    rewritten = markdown
    for local_path, asset_url in ocr_image_url_map(job_dir, artifacts, asset_base_url).items():
        rewritten = rewritten.replace(local_path, asset_url)
    return rewritten


def attach_ocr_asset_urls(source_material: dict[str, Any], job_dir: Path, asset_base_url: str) -> None:
    items = source_material.get("ocr_evidence_items")
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        asset_url = local_ocr_image_asset_url(job_dir, item.get("frame_path"), asset_base_url)
        if asset_url:
            item["asset_url"] = asset_url


def build_source_material(job_dir: Path, artifacts: dict[str, Any], source_type: str) -> dict[str, Any]:
    composer_input = artifacts["composer_input"] if isinstance(artifacts.get("composer_input"), dict) else {}
    source = composer_input.get("source") if isinstance(composer_input.get("source"), dict) else {}
    transcript_input = composer_input.get("transcript") if isinstance(composer_input.get("transcript"), dict) else {}
    comments_input = composer_input.get("comments") if isinstance(composer_input.get("comments"), dict) else {}
    ocr_input = composer_input.get("ocr") if isinstance(composer_input.get("ocr"), dict) else {}
    content = artifacts["content"] if isinstance(artifacts.get("content"), dict) else {}
    transcript = artifacts["transcript"] if isinstance(artifacts.get("transcript"), dict) else {}
    comments = artifacts["comments"] if isinstance(artifacts.get("comments"), dict) else {}
    ocr = artifacts["ocr"] if isinstance(artifacts.get("ocr"), dict) else {}
    input_data = artifacts["input"] if isinstance(artifacts.get("input"), dict) else {}
    result = artifacts["result"] if isinstance(artifacts.get("result"), dict) else {}

    source_url = clean_text(
        source.get("source_url")
        or result.get("url")
        or input_data.get("url")
        or content.get("original_url")
        or comments.get("url")
    )
    final_url = clean_text(source.get("final_url") or content.get("final_url") or comments.get("final_url") or source_url)
    raw_title = clean_text(source.get("source_title") or content.get("title") or comments.get("title") or result.get("title"))
    raw_text = clean_text(content.get("visible_text") or content.get("description") or "")
    transcript_text = clean_text(transcript_input.get("raw_transcript") or transcript.get("transcript") or "")
    ocr_text = clean_text(ocr_input.get("merged_text") or ocr.get("merged_text") or "")
    comment_items = normalize_comments(composer_input, comments)
    ocr_evidence_items = normalize_ocr_evidence_items(composer_input, ocr)
    engagement = {
        "like_count": source.get("like_count") or content.get("like_count") or "",
        "comment_count": source.get("comment_count") or content.get("comment_count") or "",
        "collect_count": source.get("collect_count") or content.get("collect_count") or "",
        "share_count": source.get("share_count") or content.get("share_count") or "",
        "metrics_source": source.get("metrics_source") or content.get("metrics_source") or "",
        "metrics_status": source.get("metrics_status") or content.get("metrics_status") or "",
        "engagement_samples": source.get("engagement_samples") or content.get("engagement_samples") or [],
        "engagement_interpretation": source.get("engagement_interpretation") or [],
    }

    return {
        "source_url": source_url,
        "source_type": source_type,
        "raw_title": raw_title,
        "raw_text": raw_text,
        "transcript": transcript_text,
        "ocr_text": ocr_text,
        "ocr_evidence_items": ocr_evidence_items,
        "comments": comment_items,
        "engagement": engagement,
        "like_count": engagement["like_count"],
        "comment_count": engagement["comment_count"],
        "collect_count": engagement["collect_count"],
        "share_count": engagement["share_count"],
        "metrics_source": engagement["metrics_source"],
        "metrics_status": engagement["metrics_status"],
        "metadata": {
            "job_id": clean_text(source.get("job_id") or result.get("job_id") or input_data.get("job_id") or job_dir.name),
            "job_dir": str(job_dir),
            "final_url": final_url,
            "author": clean_text(source.get("author") or content.get("author")),
            "publish_time": clean_text(source.get("publish_time") or content.get("published_at")),
            "video_id": clean_text(source.get("video_id") or transcript.get("video_id")),
            "transcript_status": clean_text(transcript_input.get("status") or transcript.get("status")),
            "transcript_length": transcript_input.get("transcript_length") or len(transcript_text),
            "ocr_status": clean_text(ocr_input.get("ocr_status") or ocr.get("status")),
            "comments_count": comments_input.get("comments_count") or comments.get("comment_count") or len(comment_items),
            "comments_hash": clean_text(comments_input.get("comments_hash") or comments.get("comments_hash")),
            "platform_engagement": engagement,
            "like_count": engagement["like_count"],
            "platform_comment_count": engagement["comment_count"],
            "collect_count": engagement["collect_count"],
            "share_count": engagement["share_count"],
            "metrics_source": engagement["metrics_source"],
            "metrics_status": engagement["metrics_status"],
            "used_mcp": bool(
                content.get("used_mcp")
                or transcript.get("used_mcp")
                or comments.get("used_mcp")
                or ocr.get("used_mcp")
                or result.get("used_mcp")
            ),
        },
    }


def normalize_dedupe_url(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return text
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return text
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query_items = [
        (key, val)
        for key, val in query_items
        if key.casefold() not in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm"}
    ]
    query = urllib.parse.urlencode(sorted(query_items), doseq=True)
    return urllib.parse.urlunsplit((
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        parsed.path.rstrip("/"),
        query,
        "",
    ))


def build_dedupe_payload(job_dir: Path, card: dict[str, Any], source_material: dict[str, Any]) -> dict[str, Any]:
    metadata = source_material.get("metadata") if isinstance(source_material.get("metadata"), dict) else {}
    source_url = normalize_dedupe_url(source_material.get("source_url"))
    final_url = normalize_dedupe_url(metadata.get("final_url"))
    video_id = clean_text(metadata.get("video_id"))
    source_identity = final_url or source_url or video_id
    content_basis = "\n".join(
        clean_text(value)
        for value in (
            card.get("display_title"),
            card.get("source_title"),
            card.get("one_sentence_summary"),
            " ".join(clean_text(item) for item in (card.get("core_points") or [])) if isinstance(card.get("core_points"), list) else "",
            source_material.get("raw_text"),
            source_material.get("transcript"),
            source_material.get("ocr_text"),
        )
        if clean_text(value)
    )
    return {
        "schema_name": "DedupeDecisionV1",
        "policy": "prefer_existing_source_then_content_fingerprint",
        "source_url": source_url,
        "final_url": final_url,
        "video_id": video_id,
        "source_identity_hash": sha256_text(source_identity),
        "content_fingerprint": sha256_text(content_basis[:8000]),
        "title_fingerprint": sha256_text(card.get("display_title") or card.get("source_title")),
        "on_exact_source_match": "prefer_existing_or_update_existing_record",
        "on_near_content_match": "keep_existing_and_mark_candidate_for_review",
        "job_id": job_dir.name,
    }


def build_quality_gate_payload(quality_gate: dict[str, Any]) -> dict[str, Any]:
    failed = quality_gate.get("failed_checks") or quality_gate.get("errors") or []
    if not isinstance(failed, list):
        failed = [str(failed)]
    passed = bool(quality_gate.get("quality_gate_passed") or quality_gate.get("passed"))
    warnings: list[str] = []
    downgrade_reason = clean_text(quality_gate.get("downgrade_reason"))
    if downgrade_reason:
        warnings.append(downgrade_reason)
    return {
        "passed": passed,
        "quality_level": clean_text(quality_gate.get("quality_level") or ("high" if passed and not failed else "low")),
        "errors": failed,
        "warnings": warnings,
        "raw_json": quality_gate,
    }


def build_target_path(card: dict[str, Any], source_type: str) -> str:
    card_type = clean_text(card.get("card_type"))
    if card_type == "extracted_source_card":
        title = clean_text(card.get("safe_filename_title") or card.get("display_title") or card.get("source_title"))
        title = strip_date_prefix(title)
        return f"/知识卡/Inbox/来源材料/{safe_path_part(title, '来源材料卡')}"
    if card_type == "failure_card":
        title = clean_text(card.get("safe_filename_title") or card.get("display_title") or card.get("source_title"))
        title = strip_date_prefix(title)
        return f"/知识卡/Inbox/失败记录/{safe_path_part(title, '失败记录卡')}"
    if card_type in {"temporary_card", "temporary_review_card"}:
        title = clean_text(card.get("safe_filename_title") or card.get("display_title") or card.get("source_title"))
        title = strip_date_prefix(title)
        return f"/知识卡/Inbox/临时卡/{safe_path_part(title, '待复核知识卡')}"
    tags = card.get("tags") if isinstance(card.get("tags"), list) else []
    category = clean_text(tags[0]) if tags else source_type_label(source_type)
    title = clean_text(card.get("safe_filename_title") or card.get("display_title") or card.get("source_title"))
    title = strip_date_prefix(title)
    return f"/知识卡/{safe_path_part(category)}/{safe_path_part(title, '待复核知识卡')}"


def build_taxonomy_target_path(card: dict[str, Any], taxonomy_decision: dict[str, Any]) -> str:
    if clean_text(card.get("card_type")) != "formal_summary":
        return ""
    path = taxonomy_decision.get("recommended_path") if isinstance(taxonomy_decision, dict) else []
    if not isinstance(path, list) or not path:
        return ""
    title = clean_text(card.get("safe_filename_title") or card.get("display_title") or card.get("source_title"))
    title = strip_date_prefix(title)
    path_parts = [safe_path_part(item) for item in path if clean_text(item)]
    if not path_parts:
        return ""
    return "/".join(["", "知识卡", *path_parts, safe_path_part(title, "待复核知识卡")])


def build_ingest_payload(job_dir: Path, *, actor: str = "agent", allow_non_formal: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    artifacts = load_job_artifacts(job_dir)
    raw_card = artifacts.get("card")
    card = extract_composed_card_payload(raw_card if isinstance(raw_card, dict) else {})
    quality_gate_raw = artifacts.get("quality_gate")
    result = artifacts["result"] if isinstance(artifacts.get("result"), dict) else {}
    quality_gate = quality_gate_raw if isinstance(quality_gate_raw, dict) and quality_gate_raw else {}
    if not quality_gate and isinstance(result.get("quality_gate"), dict):
        quality_gate = result["quality_gate"]
    result = artifacts["result"] if isinstance(artifacts.get("result"), dict) else {}
    content = artifacts["content"] if isinstance(artifacts.get("content"), dict) else {}

    input_data = artifacts.get("input") if isinstance(artifacts.get("input"), dict) else {}
    source_url = clean_text(result.get("url") or input_data.get("url"))
    if not source_url and isinstance(artifacts.get("composer_input"), dict):
        source_url = clean_text((artifacts["composer_input"].get("source") or {}).get("source_url"))
    source_type = infer_source_type(job_dir, result, content, source_url)
    markdown_path = select_markdown_path(job_dir, result)
    markdown = markdown_path.read_text(encoding="utf-8") if markdown_path else ""
    asset_base_url = resolve_asset_base_url()
    markdown = rewrite_ocr_image_links(markdown, job_dir, artifacts, asset_base_url)
    synthetic_card = False
    final_card_type = normalize_card_type(result.get("card_type") or quality_gate.get("final_card_type") or card.get("card_type"))
    if allow_non_formal and final_card_type != "formal_summary":
        artifacts = dict(artifacts)
        artifacts["quality_gate"] = quality_gate
        card = build_non_formal_card(job_dir, artifacts, source_type, markdown)
        synthetic_card = True
    elif allow_non_formal and card.get("schema_name") != "ComposedCardV1":
        artifacts = dict(artifacts)
        artifacts["quality_gate"] = quality_gate
        card = build_non_formal_card(job_dir, artifacts, source_type, markdown)
        synthetic_card = True
    taxonomy_decision = artifacts.get("taxonomy_decision") if isinstance(artifacts.get("taxonomy_decision"), dict) else {}
    target_path = build_taxonomy_target_path(card, taxonomy_decision) or build_target_path(card, source_type)

    source_material = build_source_material(job_dir, artifacts, source_type)
    attach_ocr_asset_urls(source_material, job_dir, asset_base_url)

    payload = {
        "card": card,
        "taxonomy_decision": taxonomy_decision,
        "dedupe": build_dedupe_payload(job_dir, card, source_material),
        "source_material": source_material,
        "quality_gate": build_quality_gate_payload(quality_gate),
        "rendered_views": {
            "markdown": markdown,
            "plain_text": build_plain_text(card),
        },
        "relations": [],
        "target_path": target_path,
        "actor": actor,
    }
    diagnostics = {
        "card_path": str(job_dir / "composed_card.json"),
        "quality_gate_path": str(job_dir / "quality_gate.json"),
        "markdown_path": str(markdown_path) if markdown_path else "",
        "source_type": source_type,
        "synthetic_card": synthetic_card,
        "allow_non_formal": allow_non_formal,
        "asset_base_url": asset_base_url,
    }
    return payload, diagnostics


def validate_ready_for_ingest(payload: dict[str, Any], *, allow_non_formal: bool = False) -> list[str]:
    errors: list[str] = []
    card = payload.get("card") if isinstance(payload.get("card"), dict) else {}
    gate = payload.get("quality_gate") if isinstance(payload.get("quality_gate"), dict) else {}
    if card.get("schema_name") != "ComposedCardV1":
        errors.append("composed_card_schema_not_ComposedCardV1")
    if card.get("schema_version") != "1":
        errors.append("composed_card_schema_version_not_1")
    if not payload.get("rendered_views", {}).get("markdown"):
        errors.append("rendered_markdown_missing")
    if allow_non_formal:
        if card.get("card_type") not in ALLOWED_INGEST_CARD_TYPES:
            errors.append("card_type_not_supported")
        if not clean_text(card.get("display_title") or card.get("source_title")):
            errors.append("card_title_missing")
        return errors
    if card.get("composer_status") != "success":
        errors.append("composer_status_not_success")
    if card.get("card_type") != "formal_summary":
        errors.append("card_type_not_formal_summary")
    if not gate.get("passed"):
        errors.append("quality_gate_not_passed")
    return errors


def save_result(job_dir: Path, result: dict[str, Any], filename: str = RESULT_FILENAME) -> None:
    write_json(job_dir / filename, result)


def save_request(job_dir: Path, payload: dict[str, Any], filename: str = REQUEST_FILENAME) -> None:
    write_json(job_dir / filename, payload)


def api_post(base_url: str, endpoint: str, token: str, payload: dict[str, Any], timeout: int) -> tuple[int, dict[str, Any], str]:
    url = base_url.rstrip("/") + "/" + endpoint.lstrip("/")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-API-Token": token,
            "User-Agent": "Lucas-Knowledge-DB-Lab/0.1",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        status_code = int(response.status)
    if not raw.strip():
        return status_code, {}, ""
    try:
        return status_code, json.loads(raw), raw
    except json.JSONDecodeError:
        return status_code, {}, raw


def extract_response_id(response_json: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = response_json.get(key)
        if isinstance(value, str) and value:
            return value
    data = response_json.get("data")
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        card = data.get("card")
        if isinstance(card, dict):
            for key in keys:
                value = card.get(key)
                if isinstance(value, str) and value:
                    return value
    return ""


def base_result(job_dir: Path, base_url: str, endpoint: str, payload: dict[str, Any] | None, diagnostics: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": "initializing",
        "base_url": base_url.rstrip("/"),
        "endpoint": "/" + endpoint.lstrip("/"),
        "job_dir": str(job_dir),
        "card_id": "",
        "node_id": "",
        "path": payload.get("target_path") if payload else "",
        "status_code": None,
        "error": "",
        "token_present": False,
        "used_mcp": False,
        "created_at": now_iso(),
        "diagnostics": diagnostics or {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a link job card to Lucas Database API.")
    parser.add_argument("--job-dir", required=True, help="Existing runtime job directory.")
    parser.add_argument("--base-url", default="", help="Temporarily override the configured Lucas Database Base URL.")
    parser.add_argument("--endpoint", default="", help="Temporarily override the configured ingest endpoint.")
    parser.add_argument("--token", default="", help="Temporarily override the configured API key for this run only.")
    parser.add_argument("--target-id", default="", help="Use a configured Lucas Database target id.")
    parser.add_argument("--result-filename", default=RESULT_FILENAME, help="Job-local result filename.")
    parser.add_argument("--request-filename", default=REQUEST_FILENAME, help="Job-local request filename.")
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--actor", default="agent")
    parser.add_argument("--dry-run", action="store_true", help="Build and save the ingest request without calling the API.")
    parser.add_argument(
        "--allow-non-formal",
        action="store_true",
        help="Testing mode: allow temporary, source-material, and failure cards without marking them formal.",
    )
    args = parser.parse_args()

    runtime = resolve_lucas_database_runtime(args.target_id or None)
    job_dir = Path(args.job_dir).resolve()
    base_url = str(args.base_url or runtime["base_url"]).rstrip("/")
    endpoint = str(args.endpoint or runtime["endpoint"])
    token = str(args.token or runtime["api_key"])
    api_key_env = str(runtime["api_key_env"])

    try:
        payload, diagnostics = build_ingest_payload(job_dir, actor=args.actor, allow_non_formal=args.allow_non_formal)
    except Exception as exc:
        result = base_result(job_dir, base_url, endpoint, None, None)
        result.update({
            "stage": "build_payload",
            "error": str(exc),
            "token_present": bool(token),
            "api_key_env": api_key_env,
            "target_id": runtime.get("target_id"),
            "target_label": runtime.get("label"),
        })
        save_result(job_dir, result, args.result_filename)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    save_request(job_dir, payload, args.request_filename)
    result = base_result(job_dir, base_url, endpoint, payload, diagnostics)
    result["token_present"] = bool(token)
    result["api_key_env"] = api_key_env
    result["allow_non_formal"] = bool(args.allow_non_formal)
    result["target_id"] = runtime.get("target_id")
    result["target_label"] = runtime.get("label")

    validation_errors = validate_ready_for_ingest(payload, allow_non_formal=args.allow_non_formal)
    if validation_errors:
        result.update({
            "stage": "quality_gate",
            "error": "; ".join(validation_errors),
            "validation_errors": validation_errors,
        })
        save_result(job_dir, result, args.result_filename)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    if args.dry_run:
        result.update({
            "ok": True,
            "stage": "dry_run",
            "error": "",
            "request_path": str(job_dir / args.request_filename),
        })
        save_result(job_dir, result, args.result_filename)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not token:
        result.update({
            "stage": "auth",
            "error": f"Missing Lucas Database API key in storage configuration ({api_key_env}). Save it in the storage settings UI or pass --token for a one-off run.",
        })
        save_result(job_dir, result, args.result_filename)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    try:
        status_code, response_json, raw_response = api_post(base_url, endpoint, token, payload, args.timeout_sec)
        response_ok = 200 <= status_code < 300 and response_json.get("ok", True) is not False
        error = ""
        if not response_ok:
            error = clean_text(response_json.get("error") or response_json.get("message") or raw_response or f"HTTP {status_code}")
        result.update({
            "ok": response_ok,
            "stage": "completed" if response_ok else "api_response",
            "status_code": status_code,
            "card_id": extract_response_id(response_json, "card_id", "id"),
            "node_id": extract_response_id(response_json, "node_id"),
            "path": extract_response_id(response_json, "path") or payload.get("target_path"),
            "error": error,
            "response": response_json if response_json else {"raw": raw_response[:1000]},
        })
        save_result(job_dir, result, args.result_filename)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if response_ok else 1
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
        result.update({
            "stage": "connect",
            "error": f"Lucas Database API unavailable: {exc.reason}",
        })
    except TimeoutError:
        result.update({
            "stage": "connect",
            "error": "Lucas Database API request timed out.",
        })
    except Exception as exc:
        result.update({
            "stage": "connect",
            "error": str(exc),
        })

    save_result(job_dir, result, args.result_filename)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
