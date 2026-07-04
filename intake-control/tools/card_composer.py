#!/usr/bin/env python
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_layer.card_composer_service import compose_card
from ai_layer.composed_card_schema import (
    build_failure_composed_card_v1,
    extract_composed_card_payload,
    normalize_composed_card_v1,
)


DEFAULT_MODEL_COMPOSE_MAX_ATTEMPTS = 3


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def short_text(text: str, limit: int) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def normalize_multiline_material(*values: Any, limit: int = 30000) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for value in values:
        if isinstance(value, list):
            raw_values = value
        else:
            raw_values = [value]
        for raw_value in raw_values:
            for raw_line in str(raw_value or "").splitlines():
                line = clean_text(raw_line)
                if not line or line in seen:
                    continue
                seen.add(line)
                lines.append(line)
    text = "\n".join(lines).strip()
    return text[:limit].rstrip() if len(text) > limit else text


def chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def count_int(value: Any) -> int:
    try:
        return int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return 0


def format_count(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未读取到"
    number = count_int(text)
    if number <= 0:
        return text
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    if number >= 10_000:
        return f"{number / 10_000:.1f}万"
    return str(number)


def engagement_signal_lines(source: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    if count_int(source.get("like_count")):
        signals.append("点赞数可提示选题、表达或创意吸引力，数值越高越值得关注，但不能单独证明内容正确。")
    if count_int(source.get("comment_count")):
        signals.append("评论数量可作为争议、困惑或讨论需求的弱信号，需要结合评论样本判断。")
    if count_int(source.get("collect_count")):
        signals.append("收藏数量更接近实用性和可复用价值信号，适合优先检查方法步骤。")
    if count_int(source.get("share_count")):
        signals.append("分享 / 转发数量可提示传播性，但仍需区分娱乐传播和知识价值。")
    return signals or ["未读取到足够互动指标，暂不判断传播、争议或实用性。"]


def format_engagement_lines(source: dict[str, Any]) -> str:
    lines = [
        f"- 点赞数：{format_count(source.get('like_count'))}",
        f"- 评论数：{format_count(source.get('comment_count'))}",
        f"- 收藏数：{format_count(source.get('collect_count'))}",
        f"- 分享 / 转发数：{format_count(source.get('share_count'))}",
    ]
    status = str(source.get("metrics_status") or "").strip()
    if status:
        lines.append(f"- 互动指标读取状态：{status}")
    lines.append("- 互动信号：" + "；".join(engagement_signal_lines(source)))
    return "\n".join(lines)


def today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def strip_douyin_suffix(title: str) -> str:
    title = clean_text(title)
    return re.sub(r"\s*-\s*抖音\s*$", "", title)


def safe_filename_part(text: str, limit: int = 40) -> str:
    text = re.sub(r"[\\/:*?\"<>|#@]+", "_", clean_text(text))
    text = re.sub(r"_+", "_", text).strip("._ ")
    return text[:limit].strip("._ ") or "待复核视频卡"


def extract_video_id(*values: str) -> str:
    for value in values:
        match = re.search(r"/video/(\d+)", value or "")
        if match:
            return match.group(1)
    return ""


def summarize_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or "unknown-link"
    tail = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""
    return f"{host}_{tail}" if tail else host


def comments_hash(job_dir: Path) -> str:
    path = job_dir / "comments.json"
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_ocr_material(job_dir: Path, ocr: dict[str, Any]) -> dict[str, Any]:
    material = read_json(job_dir / "ocr_material.json")
    if material:
        return material
    return {
        "schema_name": "",
        "schema_version": "",
        "status": ocr.get("status") or "not_run",
        "merged_text": ocr.get("merged_text") or "",
        "evidence_items": ocr.get("text_items") or [],
        "sampling": {},
        "dedupe": {},
        "source": {"input_path": ocr.get("input_path") or ""},
    }


def ocr_score(item: dict[str, Any]) -> float | None:
    try:
        return float(item.get("score"))
    except (TypeError, ValueError):
        return None


OCR_VISUAL_EVIDENCE_LIMIT = 2
OCR_VISUAL_SAMPLE_LIMIT = 4
OCR_VISUAL_MIN_SCORE = 0.85
OCR_VISUAL_KEYWORDS = (
    "标题", "按钮", "步骤", "流程", "图", "表格", "代码", "界面", "页面", "工具",
    "看板", "截图", "清单", "公式", "架构", "矩阵", "方案", "对比", "案例", "列表", "目录",
    "仓库", "开源", "官方", "技能", "插件", "时间轴", "滚动", "动画", "质量",
)
OCR_DOMAIN_KEYWORDS = (
    "gsap", "greensock", "scrolltrigger", "claude", "codex", "cursor", "github",
    "react", "vue", "svelte", "npm", "agent", "agents", "skills",
)
OCR_VISUAL_VALUE_PATTERN = re.compile(r"\d|[0-9０-９]|[%％$￥]|美元|美金|万元|万|亿")
OCR_LOW_VALUE_NOISE_PATTERN = re.compile(r"\b(showreel|work about|contact|index|copyright)\b", re.IGNORECASE)


def has_meaningful_ocr_text(text: str, score: float | None = None) -> bool:
    text = clean_text(text)
    if not text:
        return False
    if re.search(r"[\u4e00-\u9fff]", text):
        return True
    return len(text) >= 3 and (score is None or score >= 0.85)


def compact_for_compare(text: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:()（）【】\[\]《》<>\"'“”‘’_-]+", "", text or "").casefold()


def latin_ratio(text: str) -> float:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0.0
    latin = sum(1 for char in compact if char.isascii() and char.isalpha())
    return latin / max(1, len(compact))


def has_visual_value_signal(text: str) -> bool:
    if OCR_VISUAL_VALUE_PATTERN.search(text):
        return True
    if any(keyword in text.casefold() for keyword in OCR_DOMAIN_KEYWORDS):
        return True
    return any(keyword in text for keyword in OCR_VISUAL_KEYWORDS)


def chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def domain_keyword_hits(text: str) -> int:
    lowered = (text or "").casefold()
    return sum(1 for keyword in OCR_DOMAIN_KEYWORDS if keyword in lowered)


def visual_keyword_hits(text: str) -> int:
    return sum(1 for keyword in OCR_VISUAL_KEYWORDS if keyword in (text or ""))


def is_low_value_numeric_noise(text: str) -> bool:
    text = clean_text(text)
    if not text:
        return True
    if chinese_char_count(text):
        return False
    lowered = text.casefold()
    if OCR_LOW_VALUE_NOISE_PATTERN.search(lowered) and OCR_VISUAL_VALUE_PATTERN.search(text):
        return True
    return bool(re.fullmatch(r"[a-z\s]*\d{2,4}[a-z0-9\s.-]*", lowered))


def looks_like_transcript_duplicate(text: str, transcript_text: str) -> bool:
    compact_text = compact_for_compare(text)
    compact_transcript = compact_for_compare(transcript_text)
    if not compact_text or len(compact_text) < 5 or not compact_transcript:
        return False
    if compact_text in compact_transcript:
        return True
    window = max(12, len(compact_text) + 4)
    for start in range(0, max(1, len(compact_transcript) - window + 1), max(4, len(compact_text) // 2)):
        segment = compact_transcript[start:start + window]
        if difflib.SequenceMatcher(None, compact_text, segment).ratio() >= 0.82:
            return True
    return False


def ocr_visual_value_reason(text: str, score: float | None, transcript_text: str) -> str:
    text = clean_text(text)
    if not text:
        return ""
    if score is not None and score < OCR_VISUAL_MIN_SCORE:
        return ""
    compact = compact_for_compare(text)
    if len(compact) < 4 and not OCR_VISUAL_VALUE_PATTERN.search(text):
        return ""
    if latin_ratio(text) > 0.35 and not OCR_VISUAL_VALUE_PATTERN.search(text) and not domain_keyword_hits(text):
        return ""
    if looks_like_transcript_duplicate(text, transcript_text):
        return ""
    if not has_visual_value_signal(text):
        return ""
    if OCR_VISUAL_VALUE_PATTERN.search(text):
        return "contains_numeric_or_business_value_signal"
    if domain_keyword_hits(text):
        return "contains_domain_or_tool_signal"
    return "contains_visual_structure_keyword"


def ocr_visual_priority_score(text: str, score: float | None, reason: str) -> float:
    text = clean_text(text)
    base = float(score or 0.0)
    priority = base
    priority += min(chinese_char_count(text), 12) * 0.08
    priority += domain_keyword_hits(text) * 1.4
    priority += visual_keyword_hits(text) * 0.9
    if "contains_domain_or_tool_signal" in reason:
        priority += 1.2
    if "contains_visual_structure_keyword" in reason:
        priority += 0.8
    if "contains_numeric_or_business_value_signal" in reason:
        priority += 0.35
    if is_low_value_numeric_noise(text):
        priority -= 2.5
    if latin_ratio(text) > 0.65 and not domain_keyword_hits(text):
        priority -= 0.8
    return priority


def sampling_frame_map(ocr_material: dict[str, Any]) -> dict[int, dict[str, Any]]:
    sampling = ocr_material.get("sampling") if isinstance(ocr_material.get("sampling"), dict) else {}
    frames = sampling.get("frames") if isinstance(sampling.get("frames"), list) else []
    result: dict[int, dict[str, Any]] = {}
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        try:
            index = int(frame.get("frame_index"))
        except (TypeError, ValueError):
            continue
        result[index] = frame
    return result


def ocr_evidence_entries(
    ocr_material: dict[str, Any],
    transcript_text: str = "",
    limit: int = OCR_VISUAL_EVIDENCE_LIMIT,
) -> list[dict[str, Any]]:
    items = ocr_material.get("evidence_items") or []
    frame_map = sampling_frame_map(ocr_material)
    candidates: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        text = short_text(str(item.get("text") or ""), 120)
        score = ocr_score(item)
        if not text:
            continue
        reason = ocr_visual_value_reason(text, score, transcript_text)
        if not reason:
            continue
        frame_index: int | None = None
        try:
            frame_index = int(item.get("frame_index"))
        except (TypeError, ValueError):
            frame_index = None
        frame_info = frame_map.get(frame_index) if frame_index is not None else {}
        entry = {
            "text": text,
            "frame_index": frame_index,
            "frame_path": item.get("frame_path") or item.get("frame") or frame_info.get("path") or "",
            "timestamp_sec": frame_info.get("timestamp_sec"),
            "score": score,
            "image_worth_saving": True,
            "visual_value_reason": reason,
            "_priority_score": ocr_visual_priority_score(text, score, reason),
        }
        candidates.append(entry)

    best_by_frame: dict[int | None, dict[str, Any]] = {}
    for entry in candidates:
        frame_key = entry.get("frame_index")
        existing = best_by_frame.get(frame_key)
        if existing is None or float(entry.get("_priority_score") or 0.0) > float(existing.get("_priority_score") or 0.0):
            best_by_frame[frame_key] = entry

    ranked = sorted(
        best_by_frame.values(),
        key=lambda entry: (
            float(entry.get("_priority_score") or 0.0),
            float(entry.get("score") or 0.0),
        ),
        reverse=True,
    )
    entries = ranked[:limit]
    for entry in entries:
        entry.pop("_priority_score", None)
    return entries


def ocr_evidence_texts(ocr_material: dict[str, Any], limit: int = 6) -> list[str]:
    return [entry["text"] for entry in ocr_evidence_entries(ocr_material, limit=limit) if entry.get("text")]


def source_title(url: str, content: dict[str, Any], comments: dict[str, Any], result: dict[str, Any]) -> str:
    title = strip_douyin_suffix(str(content.get("title") or comments.get("title") or ""))
    if title:
        return title
    result_title = str(result.get("title") or "").strip()
    if result_title and "v.douyin.com_" not in result_title:
        return result_title
    return summarize_url(url)


def build_input(job_dir: Path) -> dict[str, Any]:
    content = read_json(job_dir / "content.json")
    transcript = read_json(job_dir / "transcript.json")
    comments = read_json(job_dir / "comments.json")
    ocr = read_json(job_dir / "ocr.json")
    ocr_material = load_ocr_material(job_dir, ocr)
    material_quality = read_json(job_dir / "material_quality.json")
    result = read_json(job_dir / "result.json")
    input_data = read_json(job_dir / "input.json")
    source_url = str(result.get("url") or input_data.get("url") or content.get("original_url") or comments.get("url") or "")
    final_url = str(content.get("final_url") or comments.get("final_url") or source_url)
    video_id = str(transcript.get("video_id") or extract_video_id(final_url, comments.get("final_url") or ""))
    raw_transcript = str(transcript.get("transcript") or "")
    source_material = normalize_multiline_material(
        content.get("user_supplied_text"),
        content.get("main_text"),
        content.get("visible_text"),
        content.get("description"),
        content.get("summary"),
        content.get("text"),
    )
    ocr_entries = ocr_evidence_entries(ocr_material, transcript_text=raw_transcript)
    comment_items = []
    for item in (comments.get("comments") or [])[:30]:
        comment_items.append({
            "id": item.get("id"),
            "author": item.get("author"),
            "text": item.get("text"),
            "like_count": item.get("like_count"),
            "reply_count": item.get("reply_count"),
            "create_time": item.get("create_time"),
        })
    chash = comments_hash(job_dir)
    return {
        "source": {
            "source_url": source_url,
            "final_url": final_url,
            "source_title": source_title(source_url, content, comments, result),
            "author": content.get("author") or "未读取到",
            "publish_time": content.get("published_at") or "未读取到",
            "like_count": content.get("like_count") or "",
            "comment_count": content.get("comment_count") or "",
            "collect_count": content.get("collect_count") or "",
            "share_count": content.get("share_count") or "",
            "metrics_source": content.get("metrics_source") or content.get("douyin_page_metadata_status") or "",
            "metrics_status": content.get("metrics_status") or content.get("douyin_page_metadata_status") or "",
            "engagement_samples": (content.get("engagement_samples") or [])[:8] if isinstance(content.get("engagement_samples"), list) else [],
            "engagement_interpretation": engagement_signal_lines(content),
            "video_id": video_id,
            "job_id": result.get("job_id") or input_data.get("job_id") or job_dir.name,
        },
        "transcript": {
            "raw_transcript": raw_transcript,
            "transcript_length": len(raw_transcript),
            "has_speech": bool(transcript.get("has_speech")),
            "transcript_source": "dyt + whisper.cpp local transcription",
            "known_asr_risks": [
                "自动转写存在错字、同音误识别和断句错误。",
                "关键术语需要保留不确定性，不能把 ASR 垃圾词当事实。",
            ],
            "status": transcript.get("status"),
            "confidence": transcript.get("confidence"),
        },
        "source_material": {
            "primary_text": source_material,
            "text_length": len(source_material),
            "chinese_char_count": chinese_char_count(source_material),
            "content_status": content.get("status"),
            "content_kind": content.get("content_kind") or "",
            "source_type": content.get("source_type") or "",
            "extraction_source": content.get("extraction_source") or "",
            "user_supplied_text_length": content.get("user_supplied_text_length") or 0,
            "source_text_sources": content.get("source_text_sources") or [],
            "need_ocr": bool(content.get("need_ocr")),
            "has_video": bool(content.get("has_video")),
            "image_count": content.get("image_count") or 0,
            "video_source_count": content.get("video_source_count") or 0,
            "material_quality": material_quality,
            "known_reader_risks": [
                "页面正文可能只包含公开可见部分，登录墙、折叠文本、图片文字或视频画面可能没有完整进入材料。",
                "source_material 可以作为正式写卡主材料，但不能据此编造图片、视频或评论中未读取到的信息。",
            ],
        },
        "comments": {
            "comments_count": comments.get("comment_count") or len(comment_items),
            "comment_items": comment_items,
            "comment_source": comments.get("source") or ("platform_metrics_only" if content.get("comment_count") else "unknown"),
            "platform_comment_count": content.get("comment_count") or "",
            "comments_job_id": result.get("job_id") or input_data.get("job_id") or job_dir.name,
            "comments_video_id": video_id,
            "comments_hash": chash,
            "signals": comments.get("signals") or [],
        },
        "ocr": {
            "ocr_status": ocr_material.get("status") or ocr.get("status") or "not_run",
            "merged_text": ocr_material.get("merged_text") or ocr.get("merged_text") or "",
            "text_items": ocr_material.get("evidence_items") or ocr.get("text_items") or [],
            "material_schema": ocr_material.get("schema_name") or "",
            "material_version": ocr_material.get("schema_version") or "",
            "sampling": ocr_material.get("sampling") or {},
            "dedupe": ocr_material.get("dedupe") or {},
            "source": ocr_material.get("source") or {},
            "evidence_items": ocr_material.get("evidence_items") or [],
            "evidence_entries": ocr_entries,
            "evidence_texts": [entry["text"] for entry in ocr_entries if entry.get("text")],
            "evidence_selection": {
                "policy": "visual_value_only",
                "max_images": OCR_VISUAL_EVIDENCE_LIMIT,
                "min_score": OCR_VISUAL_MIN_SCORE,
                "rules": [
                    "skip ordinary spoken-subtitle duplicates already covered by transcript",
                    "skip short noise, low-confidence OCR, and mostly-latin garbage",
                    "keep only frames with numeric/business value or visual-structure signals",
                ],
            },
        },
        "constraints": {
            "must_use_only_read_content": True,
            "do_not_invent_author_publish_time_or_metrics": True,
            "engagement_metrics_are_weak_signals_not_facts": True,
            "do_not_copy_transcript_into_body": True,
            "comments_must_match_hash": chash,
            "title_rules": [
                "display_title 必须是清洗后的知识卡标题，不要直接使用 URL slug 或一串 hashtag。",
                "safe_filename_title 必须短、安全、可读，不超过 40 个中文字符。",
            ],
        },
    }


def normalize_model_meta(model_meta: dict[str, Any], *, attempt: int, ok: bool) -> dict[str, Any]:
    provider = model_meta.get("model_provider") or model_meta.get("provider") or ("unknown" if ok else "none")
    model = model_meta.get("model_name") or model_meta.get("model") or ""
    return {
        "ok": ok,
        "attempt": attempt,
        "provider": provider,
        "model": model,
        "model_provider": provider,
        "model_name": model,
        "error": short_text(str(model_meta.get("error") or ""), 500),
    }


def call_model(
    input_payload: dict[str, Any],
    timeout_sec: int,
    max_attempts: int = DEFAULT_MODEL_COMPOSE_MAX_ATTEMPTS,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    max_attempts = max(1, int(max_attempts or 1))
    attempts: list[dict[str, Any]] = []
    last_meta: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        model_card, raw_meta = compose_card(input_payload, timeout_sec=timeout_sec)
        model_meta = normalize_model_meta(raw_meta, attempt=attempt, ok=model_card is not None)
        attempts.append(model_meta)
        last_meta = model_meta
        if model_card is not None:
            return model_card, {
                **model_meta,
                "attempt_count": attempt,
                "max_attempts": max_attempts,
                "attempts": attempts,
            }

    return None, {
        **last_meta,
        "error": last_meta.get("error") or "No model provider configured.",
        "attempt_count": len(attempts),
        "max_attempts": max_attempts,
        "attempts": attempts,
    }


def stamp_metadata(card: dict[str, Any], input_payload: dict[str, Any], model_meta: dict[str, str]) -> dict[str, Any]:
    normalized = normalize_composed_card_v1(extract_composed_card_payload(card), input_payload, model_meta=model_meta)
    if normalized.get("content_level") == "raw_transcript_with_comments" and input_payload["comments"].get("comments_count"):
        normalized["content_level"] = "Level 5 评论与互动增强级"
    elif normalized.get("content_level") == "raw_transcript_with_comments" and (
        input_payload["ocr"].get("merged_text") or input_payload["ocr"].get("evidence_items")
    ):
        normalized["content_level"] = "Level 4 画面 OCR 增强级"
    elif normalized.get("content_level") == "raw_transcript_with_comments" and input_payload["transcript"].get("has_speech"):
        normalized["content_level"] = "Level 3 视频口播转写级"
    return normalized


def markdown_path(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return text.replace("\\", "/")


def format_timestamp(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{seconds:.0f}s"


def render_ocr_visual_evidence(ocr: dict[str, Any]) -> str:
    entries = ocr.get("evidence_entries") if isinstance(ocr.get("evidence_entries"), list) else []
    sampling = ocr.get("sampling") if isinstance(ocr.get("sampling"), dict) else {}
    if not entries:
        status = str(ocr.get("ocr_status") or "")
        raw_items = ocr.get("evidence_items") if isinstance(ocr.get("evidence_items"), list) else []
        if status == "ocr_done" and raw_items:
            return "- OCR 已运行，但未发现值得保存为图片证据的关键画面文字；已跳过普通口播字幕帧、低价值噪声和转写重复内容。"
        lines = ["- 未提取到画面文字证据。"]
        samples = render_ocr_sampled_images(sampling)
        if samples:
            lines.extend(["", *samples])
        return "\n".join(lines)

    lines: list[str] = []
    strategy = sampling.get("strategy")
    frame_count = sampling.get("frame_count")
    if strategy or frame_count:
        lines.append(f"- 抽帧策略：{strategy or 'unknown'}；候选抽帧数：{frame_count or 0}；展示关键帧数：{len(entries)}")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = short_text(str(entry.get("text") or ""), 120)
        if not text:
            continue
        meta: list[str] = []
        frame_index = entry.get("frame_index")
        if frame_index is not None:
            meta.append(f"frame {int(frame_index) + 1}")
        timestamp = format_timestamp(entry.get("timestamp_sec"))
        if timestamp:
            meta.append(timestamp)
        score = entry.get("score")
        if isinstance(score, (int, float)):
            meta.append(f"score {score:.2f}")
        reason = str(entry.get("visual_value_reason") or "").strip()
        if reason:
            meta.append(reason)
        suffix = f"（{'；'.join(meta)}）" if meta else ""
        lines.append(f"- {text}{suffix}")
        frame_path = markdown_path(entry.get("frame_path"))
        if frame_path:
            alt = f"OCR frame {int(frame_index) + 1}" if frame_index is not None else "OCR frame"
            lines.append(f"  ![{alt}](<{frame_path}>)")
    return "\n".join(lines) if lines else "- 未提取到画面文字证据。"


def frame_image_ref(frame: dict[str, Any]) -> str:
    source_url = str(frame.get("source_url") or "").strip()
    if source_url.startswith(("http://", "https://")):
        return source_url
    return markdown_path(frame.get("path"))


def render_ocr_sampled_images(
    sampling: dict[str, Any],
    *,
    used_entries: list[dict[str, Any]] | None = None,
    limit: int = OCR_VISUAL_SAMPLE_LIMIT,
) -> list[str]:
    frames = sampling.get("frames") if isinstance(sampling.get("frames"), list) else []
    if not frames:
        return []
    used_indexes: set[int] = set()
    for entry in used_entries or []:
        try:
            used_indexes.add(int(entry.get("frame_index")))
        except (TypeError, ValueError):
            continue
    selected: list[dict[str, Any]] = []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        try:
            frame_index = int(frame.get("frame_index"))
        except (TypeError, ValueError):
            frame_index = len(selected)
        if frame_index in used_indexes:
            continue
        image_ref = frame_image_ref(frame)
        if not image_ref:
            continue
        selected.append(frame)
        if len(selected) >= limit:
            break
    if not selected:
        return []

    strategy = str(sampling.get("strategy") or "unknown")
    frame_count = sampling.get("frame_count") or len(frames)
    lines = [
        "### OCR 抽样图片",
        "",
        f"- 抽样策略：{strategy}；实际抽样图片数：{frame_count}；以下图片用于证明已采样，不代表模型已完成图片语义理解。",
    ]
    for frame in selected:
        try:
            frame_index = int(frame.get("frame_index"))
        except (TypeError, ValueError):
            frame_index = len(lines)
        image_ref = frame_image_ref(frame)
        capture_method = str(frame.get("capture_method") or "").strip()
        method_text = f"；{capture_method}" if capture_method else ""
        lines.append(f"- sample {frame_index + 1}{method_text}")
        lines.append(f"  ![OCR sampled image {frame_index + 1}](<{image_ref}>)")
    return lines


def render_markdown(card: dict[str, Any], input_payload: dict[str, Any]) -> str:
    source = input_payload["source"]
    comments = input_payload["comments"]
    ocr = input_payload["ocr"]
    transcript = input_payload.get("transcript") or {}
    source_material = input_payload.get("source_material") or {}
    blocks = []
    for block in card.get("knowledge_blocks") or []:
        blocks.append(
            f"### {block.get('concept') or '未命名概念'}\n\n"
            f"- 说明：{block.get('explanation') or '未提供'}\n"
            f"- 依据片段：{block.get('evidence') or '未提供'}\n"
            f"- 可复用价值：{block.get('reusable_value') or '未提供'}"
        )
    signals = card.get("comment_signals") or {}
    evidence_quotes = list(card.get("evidence_quotes") or [])
    if not evidence_quotes:
        for item in (comments.get("comment_items") or [])[:3]:
            text = short_text(str(item.get("text") or ""), 120)
            if text:
                evidence_quotes.append(text)
        for text in ocr.get("evidence_texts") or []:
            evidence_quotes.append(short_text(str(text), 120))
        if not (ocr.get("evidence_texts") or []) and ocr.get("merged_text"):
            evidence_quotes.append(short_text(str(ocr.get("merged_text") or ""), 120))
        transcript_text = short_text(str(transcript.get("raw_transcript") or ""), 120)
        if transcript_text:
            evidence_quotes.append(transcript_text)
        material_text = short_text(str(source_material.get("primary_text") or ""), 120)
        if material_text:
            evidence_quotes.append(material_text)
    evidence_quotes = [short_text(str(item), 120) for item in evidence_quotes if str(item).strip()]
    original_summary = card.get("original_summary") or card.get("one_sentence_summary") or ""
    appendix_excerpt = short_text(str(card.get("appendix_transcript_excerpt") or ""), 120)
    tags = list(card.get("tags") or [])
    if not tags:
        tags = ["知识卡"]
    signal_lines = []
    for label, key in (
        ("需求 / 求资源", "demand_or_resource_requests"),
        ("质疑 / 反驳", "doubts_or_objections"),
        ("落地障碍", "implementation_barriers"),
        ("共鸣 / 认可", "resonance_or_agreement"),
    ):
        values = signals.get(key) or []
        signal_lines.append(f"- {label}：" + ("；".join(str(v) for v in values[:4]) if values else "未提取到明确样本"))
    signal_lines.append(f"- 评论增量价值：{signals.get('incremental_value') or '未提取到'}")
    signal_lines.append(f"- 评论 hash：{signals.get('comments_hash') or card.get('comments_hash') or ''}")
    return f"""# {card.get('display_title') or '待复核知识卡'}

> 本卡为 {card.get('content_level')} 知识卡。
> 质量等级：{card.get('quality_level')}
> 模型：{card.get('model_provider')} / {card.get('model_used')}

## 来源信息

- 原始 URL：{source.get('source_url')}
- 最终 URL：{source.get('final_url')}
- 原始页面标题：{source.get('source_title')}
- 作者：{source.get('author')}
- 发布时间：{source.get('publish_time')}
{format_engagement_lines(source)}
- video_id：{source.get('video_id')}
- job_id：{source.get('job_id')}

## 原始摘要

{original_summary}

## 一句话总结

{card.get('one_sentence_summary') or ''}

## 核心观点

{chr(10).join(f"- {item}" for item in (card.get('core_points') or []))}

## 关键知识块

{chr(10).join(blocks)}

## 应用建议

{chr(10).join(f"- {item}" for item in (card.get('application_suggestions') or []))}

## 后续动作

{chr(10).join(f"- {item}" for item in (card.get('follow_up_actions') or []))}

## 方法论 / 流程

{chr(10).join(f"- {item}" for item in (card.get('methodology') or []))}

## 评论与互动信号

- 评论样本数量：{comments.get('comments_count')}
- 评论样本来源：{comments.get('comment_source')}
- 平台互动指标：点赞 {format_count(source.get('like_count'))}；评论 {format_count(source.get('comment_count'))}；收藏 {format_count(source.get('collect_count'))}；分享 / 转发 {format_count(source.get('share_count'))}
- 平台互动解读：{"；".join(engagement_signal_lines(source))}
{chr(10).join(signal_lines)}

## 可复用价值

{chr(10).join(f"- {item}" for item in (card.get('reusable_value') or []))}

## 风险与不确定性

{chr(10).join(f"- {item}" for item in (card.get('risks') or []))}
- OCR 状态：{ocr.get('ocr_status')}

## 证据片段

{chr(10).join(f"- {item}" for item in evidence_quotes)}

## 画面文字证据

{render_ocr_visual_evidence(ocr)}

## 原始材料摘录 / 附录

> {appendix_excerpt or '未提供原始材料摘录。'}

## 标签

{chr(10).join(f"[[{tag}]]" for tag in tags)}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict model-only card composer without MCP.")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-markdown", default="")
    parser.add_argument("--input-json", default="")
    parser.add_argument("--timeout-sec", type=int, default=120)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MODEL_COMPOSE_MAX_ATTEMPTS)
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    output_json = Path(args.output_json).resolve() if args.output_json else job_dir / "composed_card.json"
    output_md = Path(args.output_markdown).resolve() if args.output_markdown else job_dir / "composed_card.md"
    input_json = Path(args.input_json).resolve() if args.input_json else job_dir / "composer_input.json"

    input_payload = build_input(job_dir)
    write_json(input_json, input_payload)
    model_card, model_meta = call_model(input_payload, args.timeout_sec, max_attempts=args.max_attempts)
    if model_card is None:
        card = build_failure_composed_card_v1(input_payload, model_meta=model_meta, composer_error=model_meta.get("error") or "model unavailable")
        write_json(output_json, card)
        print(json.dumps({
            "ok": False,
            "used_mcp": False,
            "composer_status": "failed",
            "composer_error": card["composer_error"],
            "model_provider": card["model_provider"],
            "model_used": card["model_used"],
            "attempt_count": model_meta.get("attempt_count"),
            "max_attempts": model_meta.get("max_attempts"),
            "attempts": model_meta.get("attempts") or [],
            "composed_card_json": str(output_json),
            "composed_card_markdown": "",
        }, ensure_ascii=False, indent=2))
        return 2

    card = stamp_metadata(model_card, input_payload, model_meta)
    markdown = render_markdown(card, input_payload)
    write_json(output_json, card)
    output_md.write_text(markdown, encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "used_mcp": False,
        "composer_status": "success",
        "model_provider": card.get("model_provider"),
        "model_used": card.get("model_used"),
        "display_title": card.get("display_title"),
        "safe_filename_title": card.get("safe_filename_title"),
        "content_level": card.get("content_level"),
        "attempt_count": model_meta.get("attempt_count"),
        "max_attempts": model_meta.get("max_attempts"),
        "attempts": model_meta.get("attempts") or [],
        "composed_card_json": str(output_json),
        "composed_card_markdown": str(output_md),
        "comments_hash": card.get("comments_hash"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
