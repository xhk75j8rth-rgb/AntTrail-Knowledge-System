from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from taxonomy_layer.taxonomy_schema import SimilarCategory, TaxonomyPath


CATEGORY_ALIASES: dict[str, list[str]] = {
    "ai/agent/memory": [
        "AgentOS",
        "记忆",
        "记忆系统",
        "长期记忆",
        "memory",
        "agent memory",
        "用户记忆",
        "知识记忆",
    ],
    "ai/agent/context-management": [
        "上下文",
        "上下文管理",
        "上下文工程",
        "context",
        "context window",
        "long context",
        "窗口管理",
        "会话状态",
    ],
    "ai/agent/tool-use": [
        "工具调用",
        "function calling",
        "tool use",
        "tools",
        "MCP",
        "插件调用",
        "API 调用",
    ],
    "ai/agent/multi-agent-collaboration": [
        "多Agent",
        "多 Agent",
        "multi-agent",
        "multi agent",
        "Agent协作",
        "协作网络",
        "子 Agent",
    ],
    "ai/agent/agentos-architecture": [
        "AgentOS 架构",
        "AgentOS",
        "系统架构",
        "部署架构",
        "主链路",
        "运行时",
        "编排架构",
    ],
    "ai/knowledge-base/structured-cards": [
        "知识卡",
        "结构化卡片",
        "ComposedCard",
        "ComposedCardV1",
        "卡片 schema",
        "知识入库",
        "结构化笔记",
        "source material",
        "source materials",
        "OCRMaterialV1",
        "知识证据",
        "画面文字证据",
    ],
    "ai/knowledge-base/knowledge-graph": [
        "知识图谱",
        "knowledge graph",
        "graph",
        "实体关系",
        "关系抽取",
    ],
    "ai/knowledge-base/vector-retrieval": [
        "向量",
        "向量检索",
        "向量数据库",
        "embedding",
        "embeddings",
        "RAG",
        "semantic search",
        "相似度检索",
        "召回",
    ],
    "ai/knowledge-base/database-adapters": [
        "数据库适配",
        "database sink",
        "database api",
        "Lucas Database",
        "Storage Layer",
        "存储适配",
        "SiYuan",
        "思源",
        "Markdown sink",
        "ComposedCardV1",
        "主数据",
    ],
    "ai/content-production/ai-writing": [
        "AI写作",
        "AI 写作",
        "写作",
        "小说",
        "写小说",
        "网文",
        "文案",
        "内容创作",
        "ai writing",
        "novel",
    ],
    "ai/content-production/short-video-workflow": [
        "短视频工作流",
        "短视频生产",
        "视频工作流",
        "口播脚本",
        "剪辑",
        "字幕",
        "发布流程",
        "内容分发",
    ],
    "ai/content-production/image-video-generation": [
        "图像生成",
        "视频生成",
        "图像 / 视频生成",
        "AIGC",
        "文生图",
        "文生视频",
        "image generation",
        "video generation",
    ],
    "ai/models-providers/deepseek": [
        "DeepSeek",
        "deepseek-chat",
        "deepseek-reasoner",
    ],
    "ai/models-providers/openai": [
        "OpenAI",
        "GPT",
        "GPT-4",
        "GPT-5",
        "Responses API",
    ],
    "ai/models-providers/claude": [
        "Claude",
        "Claude Code",
        "Anthropic",
    ],
    "ai/models-providers/multi-model-routing": [
        "多模型路由",
        "model router",
        "provider routing",
        "模型切换",
        "模型选择",
        "模型调度",
    ],
    "ai/engineering/task-queue": [
        "任务队列",
        "queue",
        "异步任务",
        "worker",
        "后台任务",
        "SSE",
        "WebSocket",
    ],
    "ai/engineering/quality-gate": [
        "质量门禁",
        "quality gate",
        "quality_gate",
        "门禁",
        "校验",
        "复核",
        "代码质量",
        "测试",
        "代码审查",
        "code review",
        "合入检查",
    ],
    "ai/engineering/storage-layer": [
        "存储层",
        "Storage Layer",
        "Storage Sink",
        "sink",
        "write_siyuan",
        "write_lucas_database",
        "写入层",
    ],
    "ai/engineering/automation-pipeline": [
        "自动化流水线",
        "生产主链路",
        "Python Service",
        "Python服务",
        "部署边界",
        "MCP",
        "主流程",
        "pipeline",
        "自动处理",
        "脚本部署",
        "AI Coding",
        "AI Code",
        "ai coding",
        "代码生成",
        "代码生产",
        "稳定交付",
        "研发流程",
        "工程化落地",
        "Harness",
        "harness",
        "企业级交付",
    ],
    "software-engineering/frontend-development/animation-interaction": [
        "GSAP",
        "GSAP Skills",
        "gsap-skills",
        "GreenSock",
        "ScrollTrigger",
        "timeline",
        "timelines",
        "motion",
        "web animation",
        "动画",
        "动效",
        "交互动画",
        "前端动画",
        "滚动动画",
        "路径动画",
        "时间轴",
        "动画工程",
    ],
    "software-engineering/frontend-development/ui-engineering": [
        "UI",
        "界面",
        "组件",
        "组件库",
        "React",
        "Vue",
        "Svelte",
        "CSS",
        "布局",
        "响应式",
        "responsive",
        "前端组件",
    ],
    "software-engineering/frontend-development/toolchain": [
        "Vite",
        "Webpack",
        "Rollup",
        "npm",
        "pnpm",
        "yarn",
        "构建",
        "打包",
        "前端工具链",
        "构建工具",
    ],
    "software-engineering/frontend-development/ai-assisted-frontend-development": [
        "AI 辅助前端",
        "AI 前端",
        "AI 编程工具",
        "AI 编程工作流",
        "AI Skills",
        "Agent Skills",
        "Claude Code",
        "Codex",
        "Cursor",
        "Windsurf",
        "Copilot",
        "design to code",
        "组件生成",
        "页面生成",
        "生成 React 组件",
        "生成前端页面",
        "AI 生成代码",
    ],
    "project/lucas-knowledge-db-lab": [
        "Lucas-Knowledge-DB-Lab",
        "Knowledge DB Lab",
        "知识库入库",
        "run_link_job",
        "SiYuan",
        "思源",
        "Storage Layer",
        "ComposedCardV1",
    ],
    "project/lucas-agentos": [
        "Lucas-AgentOS",
        "AgentOS",
        "个人 AgentOS",
        "操作系统 Agent",
    ],
    "project/lucas-siyuan-codex": [
        "Lucas-SiYuan-Codex",
        "SiYuan Codex",
        "思源 Codex",
    ],
    "project/aigc-video-workbench": [
        "AIGC 视频工作台",
        "视频工作台",
        "AIGC 工作台",
    ],
}


GENERIC_TAGS = {
    "ai",
    "agent",
    "知识卡",
    "抖音",
    "口播转写",
    "ocr",
    "视频学习",
    "待处理",
    "待复核",
}


@dataclass(slots=True)
class CardMaterial:
    text: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)

    @property
    def normalized_text(self) -> str:
        return normalize_for_match(self.text)


def normalize_for_match(text: Any) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).casefold().strip()
    return value


def compact_text(text: Any) -> str:
    return re.sub(r"[\s\-_/|:：,，.。;；!！?？()（）\[\]【】\"'“”]+", "", normalize_for_match(text))


def flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = re.sub(r"\s+", " ", value).strip()
        return [text] if text else []
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            parts.extend(flatten_text(item))
        return parts
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            parts.extend(flatten_text(item))
        return parts
    text = str(value).strip()
    return [text] if text else []


def build_card_material(card: dict[str, Any]) -> CardMaterial:
    fields = (
        "display_title",
        "one_sentence_summary",
        "core_points",
        "knowledge_blocks",
        "tags",
        "reusable_value",
        "source_title",
    )
    parts: list[str] = []
    for field_name in fields:
        parts.extend(flatten_text(card.get(field_name)))
    title = " ".join(flatten_text(card.get("display_title") or card.get("source_title")))[:240]
    tags = [str(item).strip() for item in card.get("tags") or [] if str(item).strip()] if isinstance(card.get("tags"), list) else []
    material = CardMaterial(text=" ".join(parts), title=title, tags=tags)
    material.tokens = extract_tokens(material.text, tags=tags)
    return material


def extract_tokens(text: str, *, tags: list[str] | None = None) -> set[str]:
    tags = tags or []
    normalized = normalize_for_match(text)
    compact = compact_text(text)
    tokens = set(re.findall(r"[a-z][a-z0-9+_.-]{1,}", normalized))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        if len(chunk) <= 12:
            tokens.add(chunk)
        max_n = min(5, len(chunk))
        for size in range(2, max_n + 1):
            for index in range(0, len(chunk) - size + 1):
                tokens.add(chunk[index:index + size])
    for tag in tags:
        if tag.casefold() not in GENERIC_TAGS:
            tokens.add(compact_text(tag))
    for aliases in CATEGORY_ALIASES.values():
        for alias in aliases:
            alias_compact = compact_text(alias)
            if alias_compact and alias_compact in compact:
                tokens.add(alias_compact)
    return {token for token in tokens if token}


def category_aliases(category: TaxonomyPath) -> list[str]:
    aliases = list(CATEGORY_ALIASES.get(category.path_id, []))
    aliases.extend(category.path)
    aliases.append(category.display_name)
    seen: set[str] = set()
    result: list[str] = []
    for alias in aliases:
        key = compact_text(alias)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(alias)
    return result


def phrase_in_text(phrase: str, haystack_normalized: str, haystack_compact: str) -> bool:
    phrase_normalized = normalize_for_match(phrase)
    phrase_compact = compact_text(phrase)
    return bool(phrase_normalized and phrase_normalized in haystack_normalized) or bool(phrase_compact and phrase_compact in haystack_compact)


def alias_weight(alias: str) -> float:
    compact = compact_text(alias)
    if compact in {
        "ai",
        "agent",
        "项目",
        "知识库",
        "内容生产",
        "工程化",
        "模型与provider",
        "知识卡",
        "composedcard",
        "composedcardv1",
        "软件工程",
        "前端开发",
    }:
        return 0.04
    if len(compact) <= 2:
        return 0.16
    if len(compact) <= 4:
        return 0.24
    return 0.32


class SimilarityMatcher:
    def match(self, material: CardMaterial, categories: list[TaxonomyPath]) -> list[SimilarCategory]:
        scored = [self.score_category(material, category) for category in categories]
        scored = [item for item in scored if item.score > 0]
        return sorted(scored, key=lambda item: item.score, reverse=True)

    def score_category(self, material: CardMaterial, category: TaxonomyPath) -> SimilarCategory:
        haystack_normalized = material.normalized_text
        haystack_compact = compact_text(material.text)
        aliases = category_aliases(category)
        hits: list[str] = []
        score = 0.0
        for alias in aliases:
            if phrase_in_text(alias, haystack_normalized, haystack_compact):
                hits.append(alias)
                score += alias_weight(alias)

        category_tokens = extract_tokens(" ".join(aliases))
        if category_tokens:
            overlap = len(material.tokens & category_tokens) / max(1, min(len(category_tokens), 8))
            score += min(overlap, 1.0) * 0.28

        fuzzy_source = compact_text(material.title or material.text[:160])
        fuzzy_target = compact_text(category.display_name)
        if fuzzy_source and fuzzy_target:
            ratio = SequenceMatcher(None, fuzzy_source[:180], fuzzy_target).ratio()
            if ratio >= 0.25:
                score += ratio * 0.12

        score += min(len(category.path), 4) * 0.015
        score = min(score, 1.0)
        reason = self._reason(hits, material, category)
        return SimilarCategory(path=category.path, path_id=category.path_id, display_name=category.display_name, score=score, reason=reason)

    def _reason(self, hits: list[str], material: CardMaterial, category: TaxonomyPath) -> str:
        if hits:
            return f"命中分类关键词：{'、'.join(hits[:4])}"
        shared = sorted(material.tokens & extract_tokens(" ".join(category_aliases(category))))[:4]
        if shared:
            return f"主题词重叠：{'、'.join(shared)}"
        return "弱相似度匹配，需保守处理"
