from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


COMPOSED_CARD_SCHEMA_NAME = "ComposedCardV1"
COMPOSED_CARD_SCHEMA_VERSION = "1"
ALLOWED_CARD_TYPES = {"formal_summary", "temporary_review_card", "temporary_card", "failure_card", "extracted_source_card"}
MAX_EVIDENCE_QUOTE_LEN = 120
MAX_APPENDIX_EXCERPT_LEN = 120


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def short_text(text: str, limit: int) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def safe_filename_part(text: str, limit: int = 40) -> str:
    text = re.sub(r'[\\/:*?"<>|#@]+', "_", clean_text(text))
    text = re.sub(r"_+", "_", text).strip("._ ")
    return text[:limit].strip("._ ") or "待复核知识卡"


def today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _as_str(value: Any) -> str:
    return clean_text(str(value)) if value is not None else ""


def _parse_literal_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text and text[0] in "{[":
            try:
                return ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return value
    return value


def looks_like_serialized_object_text(value: Any) -> bool:
    text = _as_str(value)
    if not text:
        return False
    if text.startswith("{") or text.startswith("["):
        markers = ("point", "concept", "summary", "content", "evidence", "reusable_value", "explanation", "quotes")
        return any(marker in text for marker in markers) or "..." in text
    return False


def _extract_text_value(value: Any, preferred_keys: tuple[str, ...] = ("text", "point", "summary", "content", "concept", "explanation", "evidence", "reusable_value")) -> str:
    parsed = _parse_literal_value(value)
    if isinstance(parsed, dict):
        for key in preferred_keys:
            text = _as_str(parsed.get(key))
            if text:
                return text
        for nested in parsed.values():
            text = _extract_text_value(nested, preferred_keys=preferred_keys)
            if text:
                return text
        return clean_text(str(parsed))
    if isinstance(parsed, (list, tuple, set)):
        parts: list[str] = []
        for item in parsed:
            text = _extract_text_value(item, preferred_keys=preferred_keys)
            if text:
                parts.append(text)
        return "；".join(parts)
    return _as_str(parsed)


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    parsed = _parse_literal_value(value)
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, tuple):
        items = list(parsed)
    elif isinstance(parsed, set):
        items = list(parsed)
    else:
        items = [parsed]
    result: list[str] = []
    for item in items:
        text = _extract_text_value(item)
        if text:
            result.append(text)
    return result


def _as_knowledge_blocks(value: Any) -> list[dict[str, str]]:
    if not value:
        return []
    parsed = _parse_literal_value(value)
    if isinstance(parsed, dict):
        items = [parsed]
    elif isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, tuple):
        items = list(parsed)
    elif isinstance(parsed, set):
        items = list(parsed)
    else:
        items = [parsed]
    blocks: list[dict[str, str]] = []
    for item in items:
        parsed_item = _parse_literal_value(item)
        if isinstance(parsed_item, dict):
            point_text = _extract_text_value(
                parsed_item.get("point") or parsed_item.get("summary") or parsed_item.get("text") or parsed_item.get("content")
            )
            concept = _extract_text_value(parsed_item.get("concept"))
            explanation = _extract_text_value(parsed_item.get("explanation"))
            evidence = _extract_text_value(parsed_item.get("evidence"))
            reusable_value = _extract_text_value(parsed_item.get("reusable_value"))
            quotes = _as_str_list(parsed_item.get("evidence_quotes") or parsed_item.get("quotes"))
            if not concept or looks_like_serialized_object_text(parsed_item.get("concept")):
                concept = short_text(point_text or explanation or evidence, 24)
            if not explanation or looks_like_serialized_object_text(parsed_item.get("explanation")):
                explanation = point_text or concept
            if not evidence or looks_like_serialized_object_text(parsed_item.get("evidence")):
                evidence = quotes[0] if quotes else (explanation or concept)
            if not reusable_value or looks_like_serialized_object_text(parsed_item.get("reusable_value")):
                reusable_value = explanation or point_text or concept
            if concept or explanation or evidence or reusable_value:
                blocks.append({
                    "concept": concept,
                    "explanation": explanation,
                    "evidence": evidence,
                    "reusable_value": reusable_value,
                })
            continue
        text = _extract_text_value(item)
        if text:
            blocks.append({
                "concept": text,
                "explanation": "",
                "evidence": "",
                "reusable_value": "",
            })
    return blocks


def _derive_knowledge_blocks(core_points: list[str], reusable_value: list[str]) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    fallback_reusable = reusable_value[0] if reusable_value else ""
    for point in core_points:
        text = _as_str(point)
        if not text:
            continue
        blocks.append({
            "concept": short_text(text, 24),
            "explanation": text,
            "evidence": text,
            "reusable_value": fallback_reusable or text,
        })
    return blocks


def _derive_original_summary(
    core_points: list[str],
    knowledge_blocks: list[dict[str, str]],
    transcript: dict[str, Any],
    source_title: str,
    source_material_text: str = "",
) -> str:
    fragments: list[str] = []
    for point in core_points[:2]:
        text = _as_str(point)
        if text:
            fragments.append(short_text(text, 80))
    if fragments:
        return "；".join(fragments)
    for block in knowledge_blocks[:2]:
        if not isinstance(block, dict):
            continue
        text = _as_str(block.get("explanation") or block.get("evidence") or block.get("concept"))
        if text:
            fragments.append(short_text(text, 80))
    if fragments:
        return "；".join(fragments)
    raw_transcript = _as_str(transcript.get("raw_transcript") or transcript.get("transcript"))
    if raw_transcript:
        return short_text(raw_transcript, 160)
    if source_material_text:
        return short_text(source_material_text, 160)
    return short_text(source_title, 160)


def _dedupe_str_list(values: Any, *, min_unique_len: int = 0) -> list[str]:
    items = _as_str_list(values)
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = re.sub(r"\s+", "", item)
        if not key:
            continue
        if min_unique_len and len(key) < min_unique_len:
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _short_text_list(values: Any, limit: int) -> list[str]:
    return _dedupe_str_list([short_text(item, limit) for item in _as_str_list(values)])


def _collect_nested_evidence_quotes(value: Any) -> list[str]:
    parsed = _parse_literal_value(value)
    if isinstance(parsed, dict):
        return _as_str_list(parsed.get("evidence_quotes") or parsed.get("quotes") or parsed.get("evidence"))
    if isinstance(parsed, (list, tuple, set)):
        collected: list[str] = []
        for item in parsed:
            parsed_item = _parse_literal_value(item)
            if isinstance(parsed_item, dict):
                collected.extend(_as_str_list(parsed_item.get("evidence_quotes") or parsed_item.get("quotes") or parsed_item.get("evidence")))
        return _dedupe_str_list(collected)
    return []


def _dedupe_knowledge_blocks(blocks: Any) -> list[dict[str, str]]:
    items = _as_knowledge_blocks(blocks)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for block in items:
        concept = _as_str(block.get("concept"))
        explanation = _as_str(block.get("explanation"))
        evidence = _as_str(block.get("evidence"))
        reusable_value = _as_str(block.get("reusable_value"))
        key = (re.sub(r"\s+", "", concept), re.sub(r"\s+", "", explanation))
        if not any(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append({
            "concept": concept,
            "explanation": explanation,
            "evidence": evidence,
            "reusable_value": reusable_value,
        })
    return deduped


def _strip_date_prefix(text: str) -> str:
    value = _as_str(text)
    value = re.sub(r"^\d{4}-\d{2}-\d{2}[_\s-]+", "", value)
    return value


def _derive_topic_tags(source_title: str) -> list[str]:
    title = _as_str(source_title)
    if not title:
        return []
    title_cf = title.casefold()
    tags: list[str] = []
    keyword_map = (
        ("claude code", "Claude Code"),
        ("mirrorfish", "MirrorFish"),
        ("mirofish", "MiroFish"),
        ("ai小说", "AI小说"),
        ("ai写作", "AI写作"),
        ("写小说", "写小说"),
        ("写作", "写作"),
        ("小说", "小说"),
        ("agent", "Agent"),
        ("知识图谱", "知识图谱"),
        ("评论反馈", "评论反馈"),
        ("评论", "评论反馈"),
    )
    for needle, tag in keyword_map:
        if needle in title_cf and tag not in tags:
            tags.append(tag)
    for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9+_.-]*", title):
        token = token.strip()
        if len(token) < 2:
            continue
        token_cf = token.casefold()
        if token_cf in {"douyin", "video", "md"}:
            continue
        if token in tags:
            continue
        tags.append(token)
        if len(tags) >= 5:
            break
    return _dedupe_str_list(tags)[:5]


def _derive_fallback_tags(source_title: str, source_url: str, transcript: dict[str, Any], comments: dict[str, Any], ocr: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    if "douyin" in _as_str(source_url).casefold():
        tags.append("抖音")
    if transcript.get("has_speech") or _as_str(transcript.get("raw_transcript") or transcript.get("transcript")):
        tags.append("口播转写")
    if _has_ocr_evidence(ocr):
        tags.append("OCR")
    if comments.get("comments_count") or comments.get("comment_items") or comments.get("comments"):
        tags.append("评论反馈")
    tags.extend(_derive_topic_tags(source_title))
    tags = _dedupe_str_list(tags)
    if not tags:
        tags = ["知识卡"]
    return tags[:5]


def _derive_appendix_excerpt(transcript: dict[str, Any], evidence_quotes: list[str], core_points: list[str], source_material_text: str = "") -> str:
    raw_transcript = _as_str(transcript.get("raw_transcript") or transcript.get("transcript"))
    if raw_transcript:
        return short_text(raw_transcript, 120)
    if source_material_text:
        return short_text(source_material_text, 120)
    if evidence_quotes:
        return short_text(evidence_quotes[0], 120)
    if core_points:
        return short_text(core_points[0], 120)
    return ""


def _normalize_card_type(value: Any) -> str:
    text = _as_str(value)
    if text in ALLOWED_CARD_TYPES:
        return text
    return "formal_summary"


def _as_comment_signals(value: Any, comments_hash: str) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    normalized = {
        "comments_hash": comments_hash,
        "demand_or_resource_requests": [],
        "doubts_or_objections": [],
        "implementation_barriers": [],
        "resonance_or_agreement": [],
        "incremental_value": "",
    }
    for key in normalized:
        if key == "comments_hash":
            continue
        if key in raw:
            if key == "incremental_value":
                normalized[key] = _as_str(raw.get(key))
            else:
                normalized[key] = _as_str_list(raw.get(key))
    for key, value_item in raw.items():
        if key not in normalized:
            normalized[key] = value_item
    return normalized


def _comment_texts(comments: dict[str, Any]) -> list[str]:
    items = comments.get("comment_items") or comments.get("comments") or []
    texts: list[str] = []
    if isinstance(items, dict):
        items = [items]
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            text = _as_str(item.get("text") or item.get("content"))
        else:
            text = _as_str(item)
        if text:
            texts.append(text)
    return _dedupe_str_list(texts)


def _signals_have_content(signals: dict[str, Any]) -> bool:
    if _as_str(signals.get("incremental_value")):
        return True
    for key in ("demand_or_resource_requests", "doubts_or_objections", "implementation_barriers", "resonance_or_agreement"):
        if _as_str_list(signals.get(key)):
            return True
    return False


def _match_comment_samples(texts: list[str], pattern: str, limit: int = 4) -> list[str]:
    matched: list[str] = []
    regex = re.compile(pattern, re.IGNORECASE)
    for text in texts:
        if regex.search(text):
            matched.append(short_text(text, 90))
        if len(matched) >= limit:
            break
    return _dedupe_str_list(matched)


def _derive_comment_signals(comments: dict[str, Any], comments_hash: str) -> dict[str, Any]:
    texts = _comment_texts(comments)
    demand = _match_comment_samples(
        texts,
        r"求.*资源|求.*链接|求.*教程|求带|带带|带我|跟着学|可以跟着|能不能带|发来|发一下|给个|有没有|哪里|什么类目|做什么类目|只用.*获客|是什么|看看.*效果|发.*效果",
    )
    doubts = _match_comment_samples(
        texts,
        r"无法|没用|太拉|不现实|卖课|骗子|假|不可信|不可能|忽悠|割韭菜|循环|模仿|量产|写不出|(?<!能)不能(?!带)|唯一目的|抬走|质疑",
    )
    barriers = _match_comment_samples(
        texts,
        r"迷茫|不会|成本|花钱|烧|卡在|怎么做|怎么学|流程|转化|承接|百万字|长篇|循环|推演不出|重复|token|逻辑|情感|灵魂|落地",
    )
    resonance = _match_comment_samples(
        texts,
        r"年轻有为|实实在在|还是可以|可以看|有价值|方向有价值|知识图谱|背景板|想象力|模拟未来|证明.*价值|认可|学到了|厉害|666|比心|真实|支持",
    )
    parts: list[str] = []
    if demand:
        parts.append("评论补充了学习、带学、资源、类目或操作路径追问")
    if doubts:
        parts.append("评论提供了质疑或反向反馈，需要保留不确定性")
    if barriers:
        parts.append("评论暴露了落地障碍或执行困惑，可转化为后续行动清单")
    if resonance:
        parts.append("评论提供了认可或共鸣样本，可作为需求强度的弱信号")
    incremental_value = "；".join(parts) if parts else ("评论已抓取，但未能稳定归纳出明确增量信号。" if texts else "")
    return {
        "comments_hash": comments_hash,
        "demand_or_resource_requests": demand,
        "doubts_or_objections": doubts,
        "implementation_barriers": barriers,
        "resonance_or_agreement": resonance,
        "incremental_value": incremental_value,
    }


def _derive_methodology_steps(source_title: str, transcript: dict[str, Any], core_points: list[str], source_material_text: str = "") -> list[str]:
    raw_transcript = _as_str(transcript.get("raw_transcript") or transcript.get("transcript"))
    material = f"{source_title} {raw_transcript} {source_material_text} {' '.join(core_points)}".casefold()
    if any(token in material for token in ("mirofish", "mirrorfish")) and any(token in material for token in ("claude", "cloud code")):
        return [
            "用 MiroFish/MirrorFish 组织人物、情节或节点关系。",
            "用 Claude Code 推动角色对话和情节推演。",
            "等待生成结果后由人工评估连贯性、重复度和可用性。",
        ]
    if raw_transcript:
        return ["未提取到完整方法流程；当前只保留来源中可确认的操作描述和评论反馈。"]
    return ["未提取到明确方法流程。"]


def _derive_reusable_values(
    source_title: str,
    knowledge_blocks: list[dict[str, str]],
    application_suggestions: list[str],
    follow_up_actions: list[str],
    comment_signals: dict[str, Any],
) -> list[str]:
    values: list[str] = []
    for block in knowledge_blocks:
        if isinstance(block, dict):
            text = _as_str(block.get("reusable_value"))
            if text and not looks_like_serialized_object_text(text):
                values.append(text)
    doubts = _as_str_list(comment_signals.get("doubts_or_objections"))
    barriers = _as_str_list(comment_signals.get("implementation_barriers"))
    title = _as_str(source_title)
    if doubts or barriers or any(token in title.casefold() for token in ("小说", "写作", "claude", "mirofish", "mirrorfish")):
        values.append("评估 AI 创作工具时，把评论区的重复循环、长篇稳定性、情感深度和人工介入成本作为质量门禁，而不是只看演示效果。")
    if application_suggestions:
        values.append(f"把应用建议转成小规模验证：{short_text(application_suggestions[0], 90)}")
    if follow_up_actions:
        values.append(f"把后续动作沉淀为复盘清单：{short_text(follow_up_actions[0], 90)}")
    return _dedupe_str_list(values)[:4]


def _derive_ocr_boundary_risk(ocr: dict[str, Any]) -> str:
    status = _as_str(ocr.get("ocr_status") or ocr.get("status"))
    schema = _as_str(ocr.get("material_schema") or ocr.get("schema_name"))
    sampling = ocr.get("sampling") if isinstance(ocr.get("sampling"), dict) else {}
    if status == "skipped_not_needed":
        return "OCR 按条件策略跳过；本次没有将视频帧、截图或图片输入视觉模型，画面文字未作为证据来源。"
    if status in {"not_run", "no_input", "no_frames", "ocr_unavailable", "skipped_no_video"}:
        return f"OCR 状态为 {status or 'unknown'}；画面信息不可视为已完整读取。"
    if schema == "OCRMaterialV1" and sampling.get("strategy"):
        return f"OCR 证据来自 {sampling.get('strategy')} 抽帧策略；画面文字只代表抽样帧，不等同于完整逐帧读取。"
    return ""


def _has_ocr_evidence(ocr: dict[str, Any]) -> bool:
    if _as_str(ocr.get("merged_text")):
        return True
    for key in ("evidence_items", "text_items"):
        value = ocr.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _derive_content_level(input_payload: dict[str, Any]) -> str:
    comments = input_payload.get("comments") or {}
    ocr = input_payload.get("ocr") or {}
    transcript = input_payload.get("transcript") or {}
    if comments.get("comments_count") or comments.get("comment_items") or comments.get("comments"):
        return "Level 5 评论与互动增强级"
    if _has_ocr_evidence(ocr):
        return "Level 4 画面 OCR 增强级"
    if transcript.get("has_speech"):
        return "Level 3 视频口播转写级"
    return "Level 2 页面可见内容级"


def _unwrap_card_payload(raw_card: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw_card, dict):
        return {}
    nested = raw_card.get("card")
    if isinstance(nested, dict) and any(key in nested for key in ("core_points", "knowledge_blocks", "display_title", "one_sentence_summary")):
        return nested
    return raw_card


def extract_composed_card_payload(raw_card: dict[str, Any] | None) -> dict[str, Any]:
    return _unwrap_card_payload(raw_card)


@dataclass(slots=True)
class ComposedCardV1:
    schema_name: str = COMPOSED_CARD_SCHEMA_NAME
    schema_version: str = COMPOSED_CARD_SCHEMA_VERSION
    card_type: str = "formal_summary"
    content_level: str = ""
    quality_level: str = "high"
    source_title: str = ""
    display_title: str = ""
    safe_filename_title: str = ""
    one_sentence_summary: str = ""
    original_summary: str = ""
    core_points: list[str] = field(default_factory=list)
    knowledge_blocks: list[dict[str, str]] = field(default_factory=list)
    methodology: list[str] = field(default_factory=list)
    application_suggestions: list[str] = field(default_factory=list)
    follow_up_actions: list[str] = field(default_factory=list)
    comment_signals: dict[str, Any] = field(default_factory=dict)
    reusable_value: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    evidence_quotes: list[str] = field(default_factory=list)
    appendix_transcript_excerpt: str = ""
    comments_job_id: str = ""
    comments_video_id: str = ""
    comments_hash: str = ""
    composer_input_comments_hash: str = ""
    composed_card_comments_hash: str = ""
    composer_status: str = "success"
    composer_error: str = ""
    model_used: str = ""
    model_provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_composed_card_v1(
    raw_card: dict[str, Any] | None,
    input_payload: dict[str, Any],
    *,
    model_meta: dict[str, Any] | None = None,
    composer_status: str = "success",
    composer_error: str = "",
) -> dict[str, Any]:
    model_meta = model_meta or {}
    card = _unwrap_card_payload(raw_card)
    source = input_payload.get("source") or {}
    transcript = input_payload.get("transcript") or {}
    source_material = input_payload.get("source_material") or {}
    source_material_text = _as_str(source_material.get("primary_text"))
    comments = input_payload.get("comments") or {}
    ocr = input_payload.get("ocr") or {}
    comments_hash = _as_str(comments.get("comments_hash"))
    source_title = _as_str(card.get("source_title") or source.get("source_title") or comments.get("title") or source.get("source_url"))
    display_title = _as_str(card.get("display_title") or source_title or source.get("source_url") or "待复核知识卡")
    safe_filename_title = _as_str(card.get("safe_filename_title") or display_title or source_title)
    safe_filename_title = safe_filename_part(safe_filename_title, 36)
    if safe_filename_title and not re.match(r"^\d{4}-\d{2}-\d{2}_", safe_filename_title):
        safe_filename_title = f"{today_text()}_{safe_filename_title}"

    normalized = ComposedCardV1(
        schema_name=COMPOSED_CARD_SCHEMA_NAME,
        schema_version=COMPOSED_CARD_SCHEMA_VERSION,
        card_type=_normalize_card_type(card.get("card_type") or "formal_summary"),
        content_level=_as_str(card.get("content_level") or _derive_content_level(input_payload)),
        quality_level=_as_str(card.get("quality_level") or "high"),
        source_title=source_title,
        display_title=display_title,
        safe_filename_title=safe_filename_title,
        one_sentence_summary=_as_str(card.get("one_sentence_summary")),
        original_summary=_as_str(card.get("original_summary")),
        core_points=_as_str_list(card.get("core_points")),
        knowledge_blocks=_as_knowledge_blocks(card.get("knowledge_blocks")),
        methodology=_as_str_list(card.get("methodology")),
        application_suggestions=_as_str_list(card.get("application_suggestions")),
        follow_up_actions=_as_str_list(card.get("follow_up_actions")),
        comment_signals=_as_comment_signals(card.get("comment_signals"), comments_hash),
        reusable_value=_as_str_list(card.get("reusable_value")),
        risks=_as_str_list(card.get("risks")),
        tags=_as_str_list(card.get("tags")),
        evidence_quotes=_as_str_list(card.get("evidence_quotes")),
        appendix_transcript_excerpt=_as_str(card.get("appendix_transcript_excerpt")),
        comments_job_id=_as_str(card.get("comments_job_id") or source.get("job_id") or input_payload.get("job_id")),
        comments_video_id=_as_str(card.get("comments_video_id") or source.get("video_id") or transcript.get("video_id")),
        comments_hash=comments_hash,
        composer_input_comments_hash=comments_hash,
        composed_card_comments_hash=comments_hash,
        composer_status=_as_str(card.get("composer_status") or composer_status or "success"),
        composer_error=_as_str(card.get("composer_error") or composer_error),
        model_used=_as_str(card.get("model_used") or model_meta.get("model_name") or model_meta.get("model") or ""),
        model_provider=_as_str(card.get("model_provider") or model_meta.get("model_provider") or model_meta.get("provider") or ""),
    )
    normalized_dict = normalized.to_dict()
    normalized_dict["display_title"] = _strip_date_prefix(normalized_dict["display_title"])
    normalized_dict["source_title"] = _strip_date_prefix(normalized_dict["source_title"])
    normalized_dict["safe_filename_title"] = safe_filename_part(normalized_dict["safe_filename_title"], 36)
    normalized_dict["core_points"] = _dedupe_str_list(normalized_dict["core_points"])
    normalized_dict["methodology"] = _dedupe_str_list(normalized_dict["methodology"])
    normalized_dict["application_suggestions"] = _dedupe_str_list(normalized_dict["application_suggestions"])
    normalized_dict["follow_up_actions"] = _dedupe_str_list(normalized_dict["follow_up_actions"])
    normalized_dict["reusable_value"] = _dedupe_str_list(normalized_dict["reusable_value"])
    normalized_dict["risks"] = _dedupe_str_list(normalized_dict["risks"])
    normalized_dict["tags"] = _dedupe_str_list(normalized_dict["tags"])
    normalized_dict["original_summary"] = short_text(
        normalized_dict["original_summary"] or _derive_original_summary(
            normalized_dict["core_points"],
            normalized_dict["knowledge_blocks"],
            transcript,
            source_title,
            source_material_text,
        ),
        260,
    )
    nested_evidence_quotes = _collect_nested_evidence_quotes(card.get("core_points"))
    nested_evidence_quotes.extend(_collect_nested_evidence_quotes(card.get("knowledge_blocks")))
    normalized_dict["evidence_quotes"] = _short_text_list([
        *normalized_dict["evidence_quotes"],
        *nested_evidence_quotes,
    ], MAX_EVIDENCE_QUOTE_LEN)
    normalized_dict["knowledge_blocks"] = _dedupe_knowledge_blocks(normalized_dict["knowledge_blocks"])
    if not normalized_dict["one_sentence_summary"] and normalized_dict["core_points"]:
        normalized_dict["one_sentence_summary"] = short_text(normalized_dict["core_points"][0], 80)
    if comments.get("comments_count") or comments.get("comment_items") or comments.get("comments"):
        # Keep comment signals grounded in the current job's raw comments.
        normalized_dict["comment_signals"] = _derive_comment_signals(comments, comments_hash)
    if not normalized_dict["methodology"]:
        normalized_dict["methodology"] = _derive_methodology_steps(
            source_title,
            transcript,
            normalized_dict["core_points"],
            source_material_text,
        )
    for index, block in enumerate(normalized_dict["knowledge_blocks"]):
        if not isinstance(block, dict):
            continue
        point = normalized_dict["core_points"][index] if index < len(normalized_dict["core_points"]) else normalized_dict["one_sentence_summary"]
        point_text = _as_str(point)
        if not _as_str(block.get("concept")):
            block["concept"] = short_text(point_text, 24)
        if not _as_str(block.get("explanation")):
            block["explanation"] = point_text
        if not _as_str(block.get("evidence")):
            block["evidence"] = point_text
        if not _as_str(block.get("reusable_value")):
            fallback = normalized_dict["reusable_value"][0] if normalized_dict["reusable_value"] else point_text
            block["reusable_value"] = fallback
    if len(normalized_dict["knowledge_blocks"]) < 2:
        normalized_dict["knowledge_blocks"] = _derive_knowledge_blocks(
            normalized_dict["core_points"],
            normalized_dict["reusable_value"],
        )
        normalized_dict["knowledge_blocks"] = _dedupe_knowledge_blocks(normalized_dict["knowledge_blocks"])
    if normalized_dict["knowledge_blocks"] and len(normalized_dict["knowledge_blocks"]) < 2 and normalized_dict["one_sentence_summary"]:
        normalized_dict["knowledge_blocks"].append({
            "concept": short_text(normalized_dict["one_sentence_summary"], 24),
            "explanation": normalized_dict["one_sentence_summary"],
            "evidence": normalized_dict["one_sentence_summary"],
            "reusable_value": normalized_dict["reusable_value"][0] if normalized_dict["reusable_value"] else normalized_dict["one_sentence_summary"],
        })
        normalized_dict["knowledge_blocks"] = _dedupe_knowledge_blocks(normalized_dict["knowledge_blocks"])
    if normalized_dict["knowledge_blocks"] and normalized_dict["one_sentence_summary"]:
        first_block = normalized_dict["knowledge_blocks"][0]
        if _as_str(first_block.get("explanation")) == normalized_dict["one_sentence_summary"]:
            first_block["explanation"] = short_text(normalized_dict["core_points"][0] if normalized_dict["core_points"] else normalized_dict["one_sentence_summary"], 120)
        if not _as_str(first_block.get("evidence")):
            first_block["evidence"] = normalized_dict["one_sentence_summary"]
    if not normalized_dict["evidence_quotes"]:
        fallback_quotes: list[str] = []
        for value in normalized_dict["core_points"][:2]:
            text = _as_str(value)
            if text:
                fallback_quotes.append(short_text(text, 120))
        for block in normalized_dict["knowledge_blocks"][:2]:
            if isinstance(block, dict):
                text = _as_str(block.get("evidence") or block.get("explanation"))
                if text:
                    fallback_quotes.append(short_text(text, 120))
        normalized_dict["evidence_quotes"] = _short_text_list(fallback_quotes, MAX_EVIDENCE_QUOTE_LEN)
    if not normalized_dict["appendix_transcript_excerpt"]:
        normalized_dict["appendix_transcript_excerpt"] = _derive_appendix_excerpt(transcript, normalized_dict["evidence_quotes"], normalized_dict["core_points"], source_material_text)
    normalized_dict["appendix_transcript_excerpt"] = short_text(
        normalized_dict["appendix_transcript_excerpt"],
        MAX_APPENDIX_EXCERPT_LEN,
    )
    if not normalized_dict["tags"]:
        normalized_dict["tags"] = _derive_fallback_tags(source_title, _as_str(source.get("source_url")), transcript, comments, ocr)
    if not normalized_dict["reusable_value"]:
        normalized_dict["reusable_value"] = _derive_reusable_values(
            source_title,
            normalized_dict["knowledge_blocks"],
            normalized_dict["application_suggestions"],
            normalized_dict["follow_up_actions"],
            normalized_dict["comment_signals"],
        )
    ocr_risk = _derive_ocr_boundary_risk(ocr)
    if ocr_risk:
        normalized_dict["risks"] = _dedupe_str_list([*normalized_dict["risks"], ocr_risk])
    return normalized_dict


def build_failure_composed_card_v1(
    input_payload: dict[str, Any],
    *,
    model_meta: dict[str, Any] | None = None,
    composer_error: str = "model unavailable",
) -> dict[str, Any]:
    model_meta = model_meta or {}
    source = input_payload.get("source") or {}
    transcript = input_payload.get("transcript") or {}
    comments = input_payload.get("comments") or {}
    ocr = input_payload.get("ocr") or {}
    comments_hash = _as_str(comments.get("comments_hash"))
    source_title = _as_str(source.get("source_title") or comments.get("title") or source.get("source_url") or "待复核知识卡")
    display_title = f"待模型复核：{short_text(source_title, 32)}"
    safe_title = safe_filename_part(source_title, 24)
    safe_filename_title = f"{today_text()}_待模型复核_{safe_title}"
    card = ComposedCardV1(
        schema_name=COMPOSED_CARD_SCHEMA_NAME,
        schema_version=COMPOSED_CARD_SCHEMA_VERSION,
        card_type="temporary_review_card",
        content_level="raw_transcript_with_comments",
        quality_level="low",
        source_title=source_title,
        display_title=display_title,
        safe_filename_title=safe_filename_title,
        one_sentence_summary="",
        original_summary=short_text(
            _as_str(transcript.get("raw_transcript") or transcript.get("transcript") or source_title),
            260,
        ),
        core_points=[],
        knowledge_blocks=[],
        methodology=[],
        application_suggestions=[],
        follow_up_actions=[],
        comment_signals=_as_comment_signals({}, comments_hash),
        reusable_value=[],
        risks=["模型不可用或调用失败，禁止生成正式知识卡。"],
        tags=["待复核", "临时转写卡"],
        evidence_quotes=[],
        appendix_transcript_excerpt=short_text(_as_str(transcript.get("raw_transcript") or transcript.get("transcript")), 500),
        comments_job_id=_as_str(source.get("job_id") or input_payload.get("job_id")),
        comments_video_id=_as_str(source.get("video_id") or transcript.get("video_id")),
        comments_hash=comments_hash,
        composer_input_comments_hash=comments_hash,
        composed_card_comments_hash="",
        composer_status="failed",
        composer_error=_as_str(composer_error or model_meta.get("error") or "model unavailable"),
        model_used=_as_str(model_meta.get("model_name") or ""),
        model_provider=_as_str(model_meta.get("model_provider") or "none"),
    )
    return card.to_dict()
