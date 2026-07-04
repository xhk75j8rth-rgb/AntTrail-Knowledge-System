from __future__ import annotations

import re
from dataclasses import dataclass

from taxonomy_layer.similarity_matcher import CardMaterial, compact_text, normalize_for_match
from taxonomy_layer.taxonomy_schema import CategoryCreateProposalV1, SimilarCategory, TaxonomyPath


LIFESTYLE_TOPIC_MARKERS = (
    "穿搭",
    "服装搭配",
    "通勤搭配",
    "小个子",
    "显高",
    "配色",
    "防晒",
    "防晒衣",
    "防晒口罩",
    "墨镜",
    "妆容",
    "美妆",
    "护肤",
    "发型",
    "家居",
    "旅行",
    "美食",
)

GENERIC_AUTO_CREATE_TOPICS = {
    "小红书",
    "抖音",
    "视频",
    "内容",
    "幽默",
    "搞笑",
    "日常",
    "真实",
    "人间真实",
    "真诚",
    "方法",
    "建议",
    "经验",
    "思考",
    "观点",
    "案例",
}

NON_PROTECTED_AUTO_CREATE_ROOTS = {
    "生活方式",
    "职场",
    "职业发展",
    "法律",
    "项目",
}


@dataclass(slots=True)
class CategoryPolicy:
    high_threshold: float = 0.72
    medium_threshold: float = 0.48
    similar_threshold: float = 0.34
    max_similar_categories: int = 5

    def inbox_path(self) -> TaxonomyPath:
        return TaxonomyPath(["Inbox", "待分类"])

    def confidence_for_score(self, score: float) -> str:
        if score >= self.high_threshold:
            return "high"
        if score >= self.medium_threshold:
            return "medium"
        return "low"

    def accepts_existing_category(self, score: float | SimilarCategory, material: CardMaterial | None = None) -> bool:
        if isinstance(score, SimilarCategory):
            return score.score >= self.medium_threshold or self._is_exact_non_protected_topic_match(score, material)
        return score >= self.medium_threshold

    def allows_category_match(self, material: CardMaterial, match: SimilarCategory) -> bool:
        if not match.path or match.path[0] != "AI":
            return True
        return self._allows_ai_path(material, match.path)

    def _is_exact_non_protected_topic_match(self, match: SimilarCategory, material: CardMaterial | None) -> bool:
        if material is None or not match.path or match.path[0] == "AI" or match.score < 0.44:
            return False
        topic = compact_text(match.path[-1])
        if not topic or topic in GENERIC_AUTO_CREATE_TOPICS:
            return False
        tag_topics = {compact_text(tag) for tag in material.tags}
        return topic in tag_topics or topic in compact_text(material.title)

    def filter_similar(self, matches: list[SimilarCategory]) -> list[dict]:
        return [
            match.to_dict()
            for match in matches
            if match.score >= self.similar_threshold
        ][: self.max_similar_categories]

    def should_propose_category(self, material: CardMaterial, similar_categories: list[dict]) -> bool:
        compact = compact_text(material.text)
        if self._looks_like_business_application(compact):
            return True
        if self._looks_like_ai_content_production(material):
            return True
        if self._looks_like_lifestyle_topic(compact):
            return True
        if self._looks_like_career_topic(compact):
            return True
        if similar_categories:
            return False
        if len(compact) < 24:
            return False
        useful_tags = [tag for tag in material.tags if compact_text(tag) not in {"ai", "agent", "知识卡", "抖音", "口播转写"}]
        topic_markers = ("ai", "agent", "知识库", "数据库", "系统", "产品", "工作流", "模型", "检索")
        return bool(useful_tags) or any(marker in compact for marker in topic_markers)

    def build_create_proposal(self, material: CardMaterial) -> CategoryCreateProposalV1 | None:
        compact = compact_text(material.text)
        topic = self._proposal_topic(material)
        if not topic:
            return None
        if self._looks_like_business_application(compact):
            proposed_path = ["AI", "行业应用", topic]
            return CategoryCreateProposalV1(
                proposed_path=proposed_path,
                reason="当前分类树缺少明确行业应用分类，仅生成待人工确认的新分类建议。",
                risk="可能需要先确认是否引入 AI / 行业应用 大类；V1 不会自动创建该分类。",
                needs_user_review=True,
            )
        if self._looks_like_ai_content_production(material):
            proposed_path = ["AI", "内容生产", topic]
            return CategoryCreateProposalV1(
                proposed_path=proposed_path,
                reason="材料有明确 AI 内容生产证据，仅生成待人工确认的新分类建议。",
                risk="可能与已有 AI 内容生产分类语义重叠；V1 不会自动创建该分类，除非配置显式允许。",
                needs_user_review=True,
            )
        if self._looks_like_lifestyle_topic(compact):
            proposed_path = ["生活方式", topic]
            return CategoryCreateProposalV1(
                proposed_path=proposed_path,
                reason="材料主语是生活方式/消费决策，不具备 AI 主领域证据，仅生成待人工确认的新分类建议。",
                risk="可能需要后续按真实知识树细分生活方式子类；V1 不会写入 AI 内容生产路径。",
                needs_user_review=True,
            )
        if self._looks_like_career_topic(compact):
            proposed_path = ["职业发展", topic]
            return CategoryCreateProposalV1(
                proposed_path=proposed_path,
                reason="材料主语是职业发展或岗位技能更新，不具备 AI 工程分类证据，仅生成待人工确认的新分类建议。",
                risk="可能需要后续按真实知识树细分职业发展子类；V1 不会仅因出现 AI 标签写入 AI 工程路径。",
                needs_user_review=True,
            )
        if "agent" in compact or "智能体" in compact:
            proposed_path = ["AI", "Agent", topic]
        elif "知识库" in compact or "检索" in compact or "数据库" in compact:
            proposed_path = ["AI", "知识库", topic]
        elif "法律" in compact or "刑法" in compact or "犯罪" in compact or "合规" in compact:
            proposed_path = ["法律", topic]
        elif "lucas" in compact or "项目" in compact:
            proposed_path = ["项目", topic]
        else:
            proposed_path = [topic]
        return CategoryCreateProposalV1(
            proposed_path=proposed_path,
            reason="现有分类未达到可自动推荐阈值，仅生成待人工确认的新分类建议。",
            risk="可能与已有分类语义重叠；V1 不会自动创建该分类。",
            needs_user_review=True,
        )

    def allows_auto_create_from_proposal(self, material: CardMaterial, proposal: CategoryCreateProposalV1) -> bool:
        path = proposal.proposed_path
        if not path:
            return False
        if path[:2] == ["AI", "内容生产"]:
            return self._looks_like_ai_content_production(material)
        if path[0] == "AI":
            return self._allows_ai_path(material, path) or self._looks_like_business_application(compact_text(material.text))
        if path[0] in NON_PROTECTED_AUTO_CREATE_ROOTS:
            return True
        if len(path) == 1:
            return self._allows_general_topic_auto_create(material, path[0])
        return False

    def _allows_general_topic_auto_create(self, material: CardMaterial, topic: str) -> bool:
        compact = compact_text(material.text)
        topic_compact = compact_text(topic)
        if not topic_compact or topic_compact in GENERIC_AUTO_CREATE_TOPICS:
            return False
        if len(topic_compact) < 2 or len(topic_compact) > 24:
            return False
        tag_hits = [compact_text(tag) for tag in material.tags if compact_text(tag) == topic_compact]
        if not tag_hits and topic_compact not in compact_text(material.title):
            return False
        return compact.count(topic_compact) >= 1 and len(compact) >= 40

    def _proposal_topic(self, material: CardMaterial) -> str:
        compact = compact_text(material.text)
        if "防晒" in compact:
            return "防晒"
        if "穿搭" in compact or "服装搭配" in compact:
            return "穿搭"
        if "ppt" in compact or "幻灯片" in compact or "演示" in compact:
            return "PPT演示"
        if "程序员" in compact or "开发者" in compact:
            return "程序员"
        if "客服" in compact:
            return "客服自动化"
        if "电商" in compact:
            return "电商"
        for tag in material.tags:
            value = tag.strip()
            if value and compact_text(value) not in {"ai", "agent", "知识卡", "抖音", "口播转写"}:
                return value[:24]
        title = material.title.strip()
        if 2 <= len(title) <= 24:
            return title
        return ""

    @staticmethod
    def _looks_like_business_application(compact: str) -> bool:
        business_markers = ("电商", "客服", "售后", "订单", "商品咨询")
        return any(marker in compact for marker in business_markers)

    @staticmethod
    def _looks_like_lifestyle_topic(compact: str) -> bool:
        return any(marker in compact for marker in LIFESTYLE_TOPIC_MARKERS)

    @staticmethod
    def _looks_like_career_topic(compact: str) -> bool:
        career_markers = ("程序员", "开发者", "职业焦虑", "职业发展", "技能更新")
        return any(marker in compact for marker in career_markers)

    def _allows_ai_path(self, material: CardMaterial, path: list[str]) -> bool:
        compact = compact_text(material.text)
        normalized = normalize_for_match(material.text)
        if self._has_ai_domain_signal(normalized, compact):
            return True
        if len(path) >= 2 and path[1] == "Agent":
            return any(marker in compact for marker in ("agent", "智能体", "agentos", "工具调用", "上下文", "记忆系统"))
        if len(path) >= 2 and path[1] == "知识库":
            markers = (
                "知识库",
                "知识卡",
                "composedcard",
                "siyuan",
                "思源",
                "storagelayer",
                "storagesink",
                "databasesink",
                "向量检索",
                "向量数据库",
                "embedding",
                "rag",
                "ocrmaterial",
                "sourcematerial",
            )
            return any(marker in compact for marker in markers)
        if len(path) >= 2 and path[1] == "工程化":
            markers = (
                "质量门禁",
                "qualitygate",
                "任务队列",
                "生产主链路",
                "自动化流水线",
                "pythonservice",
                "mcp",
                "runlinkjob",
                "writesiyuan",
                "writelucasdatabase",
                "storage",
                "sse",
                "websocket",
                "aicoding",
                "代码生成",
                "稳定交付",
                "研发流程",
            )
            return any(marker in compact for marker in markers)
        if len(path) >= 2 and path[1] == "模型与 Provider":
            markers = ("openai", "deepseek", "claude", "gpt", "provider", "llm", "大模型", "多模型")
            return any(marker in compact for marker in markers)
        if len(path) >= 2 and path[1] == "行业应用":
            return self._looks_like_business_application(compact) and any(marker in compact for marker in ("自动化", "流程", "客服"))
        return False

    def _looks_like_ai_content_production(self, material: CardMaterial) -> bool:
        compact = compact_text(material.text)
        normalized = normalize_for_match(material.text)
        if not self._has_ai_domain_signal(normalized, compact):
            return False
        production_markers = (
            "写作",
            "小说",
            "文案",
            "内容创作",
            "内容生产",
            "短视频",
            "视频生成",
            "图像生成",
            "文生图",
            "文生视频",
            "aigc",
            "剪辑",
            "口播脚本",
            "分发策略",
            "穿搭",
        )
        return any(marker in compact for marker in production_markers)

    @staticmethod
    def _has_ai_domain_signal(normalized: str, compact: str) -> bool:
        ascii_markers = (
            "ai",
            "aigc",
            "llm",
            "gpt",
            "chatgpt",
            "claude",
            "codex",
            "cursor",
            "deepseek",
            "openai",
            "rag",
            "embedding",
            "prompt",
            "copilot",
            "gemini",
            "midjourney",
        )
        if any(re.search(rf"(?<![a-z0-9]){marker}(?![a-z0-9])", normalized) for marker in ascii_markers):
            return True
        chinese_markers = (
            "人工智能",
            "大模型",
            "语言模型",
            "生成式ai",
            "智能体",
            "提示词",
            "文生图",
            "文生视频",
            "ai写作",
            "ai绘画",
            "ai编程",
            "ai视频",
            "ai图片",
        )
        return any(marker in compact for marker in chinese_markers)
