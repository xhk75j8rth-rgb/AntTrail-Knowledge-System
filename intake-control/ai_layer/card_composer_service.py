from __future__ import annotations

import json
from typing import Any

from ai_layer.model_router import get_router
from ai_layer.prompt_registry import card_composer_system_prompt
from ai_layer.composed_card_schema import COMPOSED_CARD_SCHEMA_NAME


def _build_prompt(input_payload: dict[str, Any]) -> str:
    return "\n".join([
        "请根据以下材料生成符合 ComposedCardV1 的 JSON。",
        "要求：",
        "1. 只能基于已读取材料写作，不要编造。",
        "2. one_sentence_summary、core_points、knowledge_blocks、reusable_value 不要互相复读。",
        "3. core_points 至少三条，knowledge_blocks 至少两条。",
        "4. evidence_quotes 必须尽量使用 source_material、transcript、OCR 或 comments 中的短原话；每条最多 80 字，不要连续复制 transcript。",
        "5. application_suggestions 和 follow_up_actions 要具体可执行。",
        "6. 标题不要带日期前缀，不要直接使用 URL slug。",
        "7. 不要写“对 Lucas 项目的启发”。",
        "8. 如果 comments.comment_items 非空，comment_signals 必须提炼评论里的需求/质疑/障碍/认可和增量价值，不能只填 hash。",
        "9. methodology、reusable_value、tags、appendix_transcript_excerpt 不允许留空；appendix_transcript_excerpt 只能放 80 字以内关键摘录或边界说明，不能粘贴整段 transcript；没有 transcript 时用 source_material 的关键摘录。",
        "10. OCR 未运行或跳过时，要在 risks 中说明视觉/画面信息未进入模型，不要声称已经识别图片。",
        "11. 如果 material_quality.reasons 包含 visual_collection_context_sufficient，只能围绕标题、正文、公开元信息、图片数量和 OCR 文字做内容策略分析，不要描述未被读取到的图片细节。",
        "12. source 中的点赞、评论、收藏、分享/转发只能作为弱互动信号：点赞可提示吸引力或创意，评论可提示争议/困惑/讨论需求，收藏可提示实用或可复用价值，分享可提示传播性；不要把指标当作内容真实性证明。",
        "",
        "materials:",
        json.dumps(input_payload, ensure_ascii=False, indent=2),
    ])


def compose_card(input_payload: dict[str, Any], timeout_sec: int = 120) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    router = get_router()
    prompt = _build_prompt(input_payload)
    result = router.generate_json(
        "card_composer",
        prompt,
        schema_name=COMPOSED_CARD_SCHEMA_NAME,
        system_prompt=card_composer_system_prompt(),
        metadata={"timeout_sec": timeout_sec},
    )
    json_data = result.json_data
    if json_data is None and result.ok and result.text.strip():
        try:
            parsed = json.loads(result.text)
            json_data = parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            json_data = None
    meta = {
        "ok": result.ok,
        "error": result.error,
        "model_provider": result.model_provider,
        "model_name": result.model_name,
        "raw_usage": result.raw_usage,
        "latency_ms": result.latency_ms,
    }
    return json_data, meta
