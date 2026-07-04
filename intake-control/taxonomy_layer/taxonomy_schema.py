from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any


TAXONOMY_TREE_SCHEMA_NAME = "TaxonomyTreeV1"
TAXONOMY_DECISION_SCHEMA_NAME = "TaxonomyDecisionV1"
CATEGORY_CREATE_PROPOSAL_SCHEMA_NAME = "CategoryCreateProposalV1"


SEGMENT_SLUGS = {
    "AI": "ai",
    "Agent": "agent",
    "记忆系统": "memory",
    "上下文管理": "context-management",
    "工具调用": "tool-use",
    "多Agent协作": "multi-agent-collaboration",
    "多 Agent 协作": "multi-agent-collaboration",
    "AgentOS 架构": "agentos-architecture",
    "知识库": "knowledge-base",
    "结构化卡片": "structured-cards",
    "知识图谱": "knowledge-graph",
    "向量检索": "vector-retrieval",
    "数据库适配": "database-adapters",
    "内容生产": "content-production",
    "AI写作": "ai-writing",
    "AI 写作": "ai-writing",
    "短视频工作流": "short-video-workflow",
    "图像 / 视频生成": "image-video-generation",
    "模型与 Provider": "models-providers",
    "DeepSeek": "deepseek",
    "OpenAI": "openai",
    "Claude": "claude",
    "多模型路由": "multi-model-routing",
    "工程化": "engineering",
    "任务队列": "task-queue",
    "质量门禁": "quality-gate",
    "存储层": "storage-layer",
    "自动化流水线": "automation-pipeline",
    "软件工程": "software-engineering",
    "前端开发": "frontend-development",
    "动画与交互": "animation-interaction",
    "UI 工程": "ui-engineering",
    "工具链": "toolchain",
    "AI 辅助前端开发": "ai-assisted-frontend-development",
    "项目": "project",
    "Lucas-Knowledge-DB-Lab": "lucas-knowledge-db-lab",
    "Lucas-AgentOS": "lucas-agentos",
    "Lucas-SiYuan-Codex": "lucas-siyuan-codex",
    "AIGC 视频工作台": "aigc-video-workbench",
    "Inbox": "inbox",
    "待分类": "unclassified",
}


def clean_segment(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_path(path: list[Any] | tuple[Any, ...]) -> list[str]:
    return [segment for segment in (clean_segment(item) for item in path) if segment]


def slugify_segment(segment: str) -> str:
    segment = clean_segment(segment)
    if segment in SEGMENT_SLUGS:
        return SEGMENT_SLUGS[segment]
    ascii_source = re.sub(r"[\u4e00-\u9fff]+", " ", segment.casefold())
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_source).strip("-")
    if slug:
        return slug[:48]
    digest = hashlib.sha1(segment.encode("utf-8")).hexdigest()[:10]
    return f"u{digest}"


def path_to_id(path: list[str]) -> str:
    return "/".join(slugify_segment(segment) for segment in normalize_path(path))


def path_to_display(path: list[str]) -> str:
    return " / ".join(normalize_path(path))


@dataclass(slots=True)
class TaxonomyPath:
    path: list[str]
    path_id: str = ""
    display_name: str = ""

    def __post_init__(self) -> None:
        self.path = normalize_path(self.path)
        if not self.path_id:
            self.path_id = path_to_id(self.path)
        if not self.display_name:
            self.display_name = path_to_display(self.path)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SimilarCategory:
    path: list[str]
    score: float
    reason: str
    path_id: str = ""
    display_name: str = ""

    def __post_init__(self) -> None:
        taxonomy_path = TaxonomyPath(self.path, self.path_id, self.display_name)
        self.path = taxonomy_path.path
        self.path_id = taxonomy_path.path_id
        self.display_name = taxonomy_path.display_name
        self.score = round(float(self.score), 4)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CategoryCreateProposalV1:
    proposed_path: list[str]
    reason: str
    risk: str
    needs_user_review: bool = True
    schema_name: str = CATEGORY_CREATE_PROPOSAL_SCHEMA_NAME
    path_id: str = ""
    display_name: str = ""

    def __post_init__(self) -> None:
        taxonomy_path = TaxonomyPath(self.proposed_path, self.path_id, self.display_name)
        self.proposed_path = taxonomy_path.path
        self.path_id = taxonomy_path.path_id
        self.display_name = taxonomy_path.display_name

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaxonomyDecisionV1:
    recommended_path: list[str]
    path_id: str
    confidence: str
    matched_existing_category: bool
    should_create_category: bool
    similar_categories: list[dict[str, Any]]
    reason: str
    card_id: str | None = None
    fallback: str | None = None
    create_proposal: dict[str, Any] | None = None
    schema_name: str = TAXONOMY_DECISION_SCHEMA_NAME

    def __post_init__(self) -> None:
        taxonomy_path = TaxonomyPath(self.recommended_path, self.path_id)
        self.recommended_path = taxonomy_path.path
        self.path_id = taxonomy_path.path_id

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_name"] = TAXONOMY_DECISION_SCHEMA_NAME
        return payload


def taxonomy_path_from_dict(data: dict[str, Any]) -> TaxonomyPath:
    return TaxonomyPath(
        path=normalize_path(data.get("path") or []),
        path_id=str(data.get("path_id") or ""),
        display_name=str(data.get("display_name") or ""),
    )


def decision_to_dict(decision: TaxonomyDecisionV1 | dict[str, Any]) -> dict[str, Any]:
    if isinstance(decision, TaxonomyDecisionV1):
        return decision.to_dict()
    return dict(decision)
