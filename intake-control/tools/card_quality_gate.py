#!/usr/bin/env python
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_layer.composed_card_schema import extract_composed_card_payload, looks_like_serialized_object_text


MAX_ONE_SENTENCE_SUMMARY_LEN = 300
MIN_REUSABLE_VALUE_LEN = 8
RAW_MATERIAL_APPENDIX_HEADINGS = (
    "原始材料摘录",
    "原始转写摘录",
)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def comments_hash(job_dir: Path) -> str:
    path = job_dir / "comments.json"
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def similar(a: str, b: str) -> float:
    a = clean_text(a)
    b = clean_text(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def unique_compact_texts(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = clean_text(str(value))
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def signal_values_present(comment_signals: dict[str, Any]) -> bool:
    if str(comment_signals.get("incremental_value") or "").strip():
        return True
    for key in ("demand_or_resource_requests", "doubts_or_objections", "implementation_barriers", "resonance_or_agreement"):
        value = comment_signals.get(key)
        if isinstance(value, list) and any(str(item).strip() for item in value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def transcript_repeated(markdown: str, transcript: str) -> bool:
    compact_md = clean_text(markdown)
    compact_tr = clean_text(transcript)
    if len(compact_tr) < 180:
        return False
    for start in range(0, max(1, len(compact_tr) - 180), 90):
        segment = compact_tr[start:start + 180]
        if len(segment) >= 160 and segment in compact_md:
            return True
    return False


def strip_raw_material_appendix(markdown: str) -> str:
    lines = markdown.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        heading_match = re.match(r"^##\s+(.+?)\s*$", line.strip())
        if heading_match:
            heading = heading_match.group(1)
            if any(raw_heading in heading for raw_heading in RAW_MATERIAL_APPENDIX_HEADINGS):
                skipping = True
                continue
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def sibling_titles(job_dir: Path) -> list[str]:
    root = job_dir.parent
    titles: list[str] = []
    for path in sorted(root.glob("*/comments.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
        if path.parent == job_dir:
            continue
        data = read_json(path)
        title = str(data.get("title") or "").strip()
        title = re.sub(r"\s*-\s*抖音\s*$", "", title)
        if is_cross_job_title_candidate(title):
            titles.append(title[:24])
    return titles


def is_cross_job_title_candidate(title: str) -> bool:
    text = re.sub(r"\s+", " ", title or "").strip()
    if len(text) < 12:
        return False
    lowered = text.casefold().strip("/")
    if lowered in {"www.douyin.com", "v.douyin.com", "m.douyin.com", "douyin.com", "douyin", "抖音"}:
        return False
    if re.search(r"https?://", text, flags=re.I):
        return False
    if re.fullmatch(r"(?:[\w-]+\.)+[a-z]{2,}(?:/.*)?", lowered):
        return False
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = len(re.findall(r"[A-Za-z][A-Za-z0-9'_-]*", text))
    return cjk_count >= 4 or latin_words >= 3


def detect_cross_job(markdown: str, card: dict[str, Any], job_dir: Path, current_material: str) -> list[str]:
    payload = json.dumps(card, ensure_ascii=False) + "\n" + markdown
    issues = []
    for title in sibling_titles(job_dir):
        if title and title in payload and title not in current_material:
            issues.append(f"cross_job_title:{title}")
    return issues


def gate(job_dir: Path, card_path: Path, markdown_path: Path) -> dict[str, Any]:
    card = extract_composed_card_payload(read_json(card_path))
    markdown = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else ""
    transcript = read_json(job_dir / "transcript.json")
    content = read_json(job_dir / "content.json")
    comments = read_json(job_dir / "comments.json")
    current_hash = comments_hash(job_dir)
    failed: list[str] = []
    warnings: list[str] = []

    if card.get("schema_name") != "ComposedCardV1":
        failed.append("schema_name_not_ComposedCardV1")
    if card.get("composer_status") != "success":
        failed.append("composer_status_not_success")
    if not str(card.get("model_used") or "").strip():
        failed.append("model_used_empty")
    summary = str(card.get("one_sentence_summary") or "")
    if len(summary) > MAX_ONE_SENTENCE_SUMMARY_LEN:
        failed.append("summary_too_long")
    raw_transcript = str(transcript.get("transcript") or "")
    if similar(summary, raw_transcript[:120]) >= 0.62:
        failed.append("summary_too_similar_to_transcript")
    core_points = card.get("core_points") or []
    if not isinstance(core_points, list) or len(core_points) < 3:
        failed.append("core_points_less_than_3")
    core_unique = unique_compact_texts([str(item) for item in core_points])
    if len(core_unique) < len(core_points):
        failed.append("core_points_duplicate_text")
    if any(looks_like_serialized_object_text(point) for point in core_points):
        failed.append("core_points_serialized_object_text")
    if summary and any(similar(summary, point) >= 0.78 for point in core_unique):
        failed.append("summary_repeats_core_point")
    forbidden = ("占位", "待人工复核", "当前自动读取材料有限", "提炼占位")
    if any(any(term in str(point) for term in forbidden) for point in core_points):
        failed.append("core_points_placeholder_text")
    blocks = card.get("knowledge_blocks") or []
    if not isinstance(blocks, list) or len(blocks) < 2:
        failed.append("knowledge_blocks_less_than_2")
    block_pairs: list[str] = []
    block_explanations: list[str] = []
    block_evidence_missing = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        concept = clean_text(str(block.get("concept") or ""))
        explanation = clean_text(str(block.get("explanation") or ""))
        evidence = clean_text(str(block.get("evidence") or ""))
        reusable_value = clean_text(str(block.get("reusable_value") or ""))
        if (
            looks_like_serialized_object_text(block.get("concept"))
            or looks_like_serialized_object_text(block.get("explanation"))
            or looks_like_serialized_object_text(block.get("evidence"))
            or looks_like_serialized_object_text(block.get("reusable_value"))
        ):
            failed.append("knowledge_blocks_serialized_object_text")
        if explanation:
            block_explanations.append(explanation)
        if explanation and summary and similar(explanation, summary) >= 0.82:
            failed.append("knowledge_block_repeats_summary")
        if not evidence:
            block_evidence_missing = True
        if not reusable_value or len(reusable_value) < MIN_REUSABLE_VALUE_LEN:
            failed.append("knowledge_block_reusable_value_missing_or_too_short")
        if reusable_value and (
            (explanation and similar(reusable_value, explanation) >= 0.78)
            or (evidence and similar(reusable_value, evidence) >= 0.78)
            or (concept and similar(reusable_value, concept) >= 0.9)
        ):
            failed.append("knowledge_block_reusable_value_repeats_source")
        block_pairs.append(f"{concept}|{explanation}")
    if len(unique_compact_texts(block_pairs)) < len([item for item in block_pairs if item]):
        failed.append("knowledge_blocks_duplicate_text")
    if len(unique_compact_texts(block_explanations)) < len(block_explanations):
        failed.append("knowledge_blocks_duplicate_explanation")
    if block_evidence_missing:
        failed.append("knowledge_block_evidence_missing")
    if card.get("application_suggestions") is None:
        failed.append("application_suggestions_missing")
    if card.get("follow_up_actions") is None:
        failed.append("follow_up_actions_missing")
    if isinstance(card.get("application_suggestions"), list) and len(unique_compact_texts(card.get("application_suggestions") or [])) < len(card.get("application_suggestions") or []):
        failed.append("application_suggestions_duplicate_text")
    if isinstance(card.get("follow_up_actions"), list) and len(unique_compact_texts(card.get("follow_up_actions") or [])) < len(card.get("follow_up_actions") or []):
        failed.append("follow_up_actions_duplicate_text")
    evidence_quotes = card.get("evidence_quotes") or []
    if evidence_quotes:
        if len(unique_compact_texts(evidence_quotes)) < len(evidence_quotes):
            failed.append("evidence_quotes_duplicate_text")
    else:
        failed.append("evidence_quotes_missing")
    if card.get("composer_input_comments_hash") != current_hash:
        failed.append("composer_input_comments_hash_mismatch")
    if card.get("composed_card_comments_hash") != current_hash:
        failed.append("composed_card_comments_hash_mismatch")
    if (comments.get("comment_count") or comments.get("comments")) and "Level 5" not in str(card.get("content_level") or ""):
        failed.append("comments_present_but_not_level5")
    comment_signals = card.get("comment_signals") or {}
    if isinstance(comment_signals, dict) and comment_signals.get("comments_hash") not in (current_hash, None, ""):
        failed.append("comment_signals_hash_mismatch")
    if (comments.get("comment_count") or comments.get("comments")) and (not isinstance(comment_signals, dict) or not signal_values_present(comment_signals)):
        failed.append("comment_signals_empty_with_comments")
    if not card.get("reusable_value"):
        failed.append("top_level_reusable_value_missing")
    body_markdown = strip_raw_material_appendix(markdown)
    if transcript_repeated(body_markdown, raw_transcript):
        failed.append("large_transcript_repeated_in_markdown")
    elif transcript_repeated(markdown, raw_transcript):
        warnings.append("large_transcript_repeated_in_appendix_ignored")
    display_title = str(card.get("display_title") or "").strip()
    if not display_title or "v.douyin.com" in display_title or re.match(r"^\d{4}-\d{2}-\d{2}[_\s-]", display_title) or re.match(r"^\d{4}-\d{2}-\d{2}_?v\.douyin", display_title):
        failed.append("bad_display_title")
    safe_filename_title = str(card.get("safe_filename_title") or "").strip()
    if not safe_filename_title:
        failed.append("safe_filename_title_missing")
    if len(safe_filename_title) > 80:
        failed.append("safe_filename_title_too_long")
    source_title = str(content.get("title") or comments.get("title") or "")
    current_material = "\n".join([
        source_title,
        raw_transcript,
        json.dumps(comments.get("comments") or [], ensure_ascii=False),
    ])
    failed.extend(detect_cross_job(markdown, card, job_dir, current_material))

    passed = not failed
    raw_card_type = str(card.get("card_type") or "").strip()
    final_card_type = raw_card_type if raw_card_type in ("formal_summary", "temporary_review_card", "temporary_card", "failure_card") else "formal_summary"
    return {
        "schema_name": card.get("schema_name") or "ComposedCardV1",
        "schema_version": card.get("schema_version") or "1",
        "quality_gate_passed": passed,
        "failed_checks": failed,
        "warnings": warnings,
        "downgrade_reason": "" if passed else "; ".join(failed),
        "final_card_type": final_card_type if passed else "temporary_review_card",
        "content_level": card.get("content_level") if passed else "raw_transcript_with_comments",
        "quality_level": card.get("quality_level") if passed else "low",
        "comments_hash": current_hash,
        "composer_input_comments_hash": card.get("composer_input_comments_hash"),
        "composed_card_comments_hash": card.get("composed_card_comments_hash"),
        "used_mcp": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict quality gate for composed cards without MCP.")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--composed-json", default="")
    parser.add_argument("--composed-markdown", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    card_path = Path(args.composed_json).resolve() if args.composed_json else job_dir / "composed_card.json"
    markdown_path = Path(args.composed_markdown).resolve() if args.composed_markdown else job_dir / "composed_card.md"
    output_path = Path(args.output).resolve() if args.output else job_dir / "quality_gate.json"
    result = gate(job_dir, card_path, markdown_path)
    write_json(output_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("quality_gate_passed") else 2


if __name__ == "__main__":
    sys.exit(main())
