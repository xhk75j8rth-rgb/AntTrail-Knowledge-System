#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from taxonomy_layer import TaxonomyRouter

RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "jobs"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_filename_title(title: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in (" ", "-", "_") else "_" for ch in title)
    return f"{datetime.now().strftime('%Y-%m-%d')}_{clean.strip()[:36] or 'taxonomy-test'}"


def build_card(case: dict[str, Any]) -> dict[str, Any]:
    summary = case["summary"]
    title = case["title"]
    tags = case["keywords"]
    core_points = [
        summary,
        f"分类关注点：{', '.join(tags[:4])}",
        f"期望分类：{' / '.join(case['expected_path'])}",
    ]
    return {
        "schema_name": "ComposedCardV1",
        "schema_version": "1",
        "card_type": "formal_summary",
        "content_level": "Level 1 taxonomy mock test",
        "quality_level": "high",
        "source_title": title,
        "display_title": title,
        "safe_filename_title": safe_filename_title(title),
        "one_sentence_summary": summary,
        "original_summary": summary,
        "core_points": core_points,
        "knowledge_blocks": [
            {
                "concept": "分类主题",
                "explanation": summary,
                "evidence": "；".join(tags),
                "reusable_value": "用于验证 TaxonomyRouter 是否能把测试知识卡归入稳定分类路径。",
            },
            {
                "concept": "路由判断",
                "explanation": f"该测试样例期望进入 {' / '.join(case['expected_path'])}。",
                "evidence": title,
                "reusable_value": "作为 Brain 中可视检查分类效果的测试卡。",
            },
        ],
        "methodology": ["构造 mock ComposedCardV1", "运行 TaxonomyRouter.route_card", "导入 Lucas Database / Brain 查看效果"],
        "application_suggestions": ["在 Brain 中检查 target_path、taxonomy_decision 和标题是否符合预期。"],
        "follow_up_actions": ["根据 Brain 展示效果决定是否继续调整 taxonomy seed、别名和阈值。"],
        "comment_signals": {"comments_hash": ""},
        "reusable_value": ["用固定测试数据回归分类路径，避免后续分类规则漂移。"],
        "risks": ["该卡片为本地 mock 测试数据，不代表真实来源读取结果。"],
        "tags": tags,
        "evidence_quotes": [summary, title],
        "appendix_transcript_excerpt": "",
        "comments_job_id": "",
        "comments_video_id": "",
        "comments_hash": "",
        "composer_input_comments_hash": "",
        "composed_card_comments_hash": "",
        "composer_status": "success",
        "composer_error": "",
        "model_used": "taxonomy_mock_generator",
        "model_provider": "local",
    }


def render_markdown(card: dict[str, Any], decision: dict[str, Any], case: dict[str, Any]) -> str:
    core_points = "\n".join(f"- {item}" for item in card["core_points"])
    knowledge_blocks = "\n".join(
        (
            f"### {block['concept']}\n\n"
            f"- 说明：{block['explanation']}\n"
            f"- 依据：{block['evidence']}\n"
            f"- 可复用价值：{block['reusable_value']}"
        )
        for block in card["knowledge_blocks"]
    )
    similar_categories = "\n".join(
        f"- {' / '.join(item['path'])}：{item['score']}，{item['reason']}"
        for item in decision.get("similar_categories") or []
    ) or "- 无"
    tags = "\n".join(f"[[{tag}]]" for tag in card["tags"])
    return f"""# {card['display_title']}

> 测试用途：TaxonomyRouter -> Brain 导入效果检查
> 期望分类：{' / '.join(case['expected_path'])}
> 实际分类：{' / '.join(decision['recommended_path'])}
> 置信度：{decision['confidence']}

## 一句话总结

{card['one_sentence_summary']}

## 分类决策

- schema：{decision['schema_name']}
- recommended_path：{' / '.join(decision['recommended_path'])}
- path_id：{decision['path_id']}
- confidence：{decision['confidence']}
- matched_existing_category：{decision['matched_existing_category']}
- should_create_category：{decision['should_create_category']}
- fallback：{decision.get('fallback') or ''}

## 核心观点

{core_points}

## 关键知识块

{knowledge_blocks}

## 相似分类

{similar_categories}

## 标签

{tags}
"""


TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "agentos-memory",
        "title": "如何提高 AgentOS 的长期记忆能力",
        "summary": "文章讨论 Agent 在长任务中如何保留上下文、形成长期记忆、避免每次任务重新开始。",
        "keywords": ["AgentOS", "长期记忆", "上下文", "知识库", "任务状态"],
        "expected_path": ["AI", "Agent", "记忆系统"],
    },
    {
        "id": "ai-writing-claude",
        "title": "用 MirrorFish + Claude Code 写小说",
        "summary": "内容展示如何用 AI 工具辅助小说人物设定、剧情推演和对话生成。",
        "keywords": ["AI 写作", "Claude Code", "小说创作", "内容生产"],
        "expected_path": ["AI", "内容生产", "AI 写作"],
    },
    {
        "id": "vector-database",
        "title": "向量数据库和普通数据库有什么区别",
        "summary": "解释向量检索、相似度搜索、embedding、RAG 与传统 SQL 查询的差异。",
        "keywords": ["向量数据库", "embedding", "RAG", "知识库"],
        "expected_path": ["AI", "知识库", "向量检索"],
    },
    {
        "id": "database-adapter",
        "title": "如何把 SiYuan 替换成自己的数据库 API",
        "summary": "讨论 SiYuan 只是 Markdown sink，真正主数据应是 ComposedCardV1，未来需要 database sink。",
        "keywords": ["SiYuan", "数据库适配", "Storage Layer", "ComposedCardV1"],
        "expected_path": ["AI", "知识库", "数据库适配"],
    },
    {
        "id": "mcp-python-boundary",
        "title": "Agent 使用 MCP 和 Python 脚本的部署边界",
        "summary": "讨论上线时 Python Service 应作为主链路，MCP 只作为 Agent 适配层。",
        "keywords": ["MCP", "Python Service", "Agent 工具", "部署架构"],
        "expected_path": ["AI", "工程化", "自动化流水线"],
    },
    {
        "id": "ocr-source-material",
        "title": "抖音视频 OCR 如何保存关键画面文字",
        "summary": "讨论视频抽帧、OCRMaterialV1、画面文字证据、评论树和 source material 入库。",
        "keywords": ["OCR", "视频抽帧", "source material", "知识证据"],
        "expected_path": ["AI", "知识库", "结构化卡片"],
    },
    {
        "id": "ecommerce-service",
        "title": "如何设计电商平台的客服自动化",
        "summary": "讨论商品咨询、订单状态、售后问题和客服工作流自动化。",
        "keywords": ["电商", "客服", "工作流", "自动化"],
        "expected_path": ["Inbox", "待分类"],
    },
    {
        "id": "unclear-fragment",
        "title": "一个无法判断主题的碎片想法",
        "summary": "以后也许可以试试这个方法。",
        "keywords": ["无"],
        "expected_path": ["Inbox", "待分类"],
    },
]


def create_job(batch_dir: Path, case: dict[str, Any], router: TaxonomyRouter) -> dict[str, Any]:
    job_dir = batch_dir / case["id"]
    card = build_card(case)
    decision = router.route_card(card)
    markdown = render_markdown(card, decision, case)
    source_url = f"mock://taxonomy-router/{case['id']}"
    job_id = f"{batch_dir.name}/{case['id']}"

    write_json(job_dir / "composed_card.json", card)
    write_json(job_dir / "taxonomy_decision.json", decision)
    write_json(job_dir / "quality_gate.json", {
        "schema_name": "ComposedCardV1",
        "schema_version": "1",
        "quality_gate_passed": True,
        "failed_checks": [],
        "quality_level": "high",
        "comments_hash": "",
    })
    write_json(job_dir / "composer_input.json", {
        "source": {
            "source_url": source_url,
            "final_url": source_url,
            "source_title": case["title"],
            "author": "taxonomy-mock",
            "publish_time": now_iso(),
            "video_id": "",
            "job_id": job_id,
        },
        "transcript": {"raw_transcript": "", "transcript_length": 0, "has_speech": False, "status": "not_run"},
        "comments": {"comments_count": 0, "comment_items": [], "comments_hash": ""},
        "ocr": {"ocr_status": "not_run", "merged_text": ""},
    })
    write_json(job_dir / "content.json", {
        "source_type": "taxonomy/mock",
        "title": case["title"],
        "visible_text": case["summary"],
        "used_mcp": False,
    })
    write_json(job_dir / "input.json", {"url": source_url, "received_at": now_iso(), "job_id": job_id})
    write_json(job_dir / "result.json", {
        "job_id": job_id,
        "url": source_url,
        "source_type": "taxonomy/mock",
        "written_card_source": "composed_card.md",
        "final_status": "taxonomy_mock_ready",
        "used_mcp": False,
    })
    (job_dir / "composed_card.md").write_text(markdown, encoding="utf-8")
    return {
        "case_id": case["id"],
        "job_dir": str(job_dir),
        "title": case["title"],
        "expected_path": case["expected_path"],
        "recommended_path": decision["recommended_path"],
        "confidence": decision["confidence"],
        "matched_expected": decision["recommended_path"] == case["expected_path"],
        "should_create_category": decision["should_create_category"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate taxonomy router mock jobs for Brain import testing.")
    parser.add_argument("--batch-id", default="", help="Optional runtime/jobs child directory name.")
    args = parser.parse_args()

    batch_id = args.batch_id or f"taxonomy-brain-test-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    batch_dir = RUNTIME_ROOT / batch_id
    router = TaxonomyRouter()
    jobs = [create_job(batch_dir, case, router) for case in TEST_CASES]
    summary = {
        "ok": True,
        "batch_id": batch_id,
        "batch_dir": str(batch_dir),
        "created_at": now_iso(),
        "job_count": len(jobs),
        "all_matched_expected": all(item["matched_expected"] for item in jobs),
        "jobs": jobs,
    }
    write_json(batch_dir / "taxonomy_brain_test_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_matched_expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
