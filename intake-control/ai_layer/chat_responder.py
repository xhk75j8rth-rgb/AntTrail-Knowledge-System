from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from ai_layer.intent_classifier import IntentClassifier
from ai_layer.lucas_retrieval_client import LucasDatabaseRetriever, RetrievalResult
from ai_layer.model_router import ModelRouter, get_router
from ai_layer.prompt_registry import chat_responder_system_prompt
from ai_layer.provider_config import get_public_ai_config


@dataclass(slots=True)
class ChatResponse:
    ok: bool
    reply_text: str
    intent: str
    confidence: float
    data: dict[str, Any]
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RetrievalPlan:
    should_retrieve: bool
    query: str = ""
    topic: str = ""
    action: str = ""
    used_history: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChatResponder:
    def __init__(self, router: ModelRouter | None = None, retriever: LucasDatabaseRetriever | None = None) -> None:
        self.classifier = IntentClassifier()
        self.router = router or get_router()
        self.retriever = retriever or LucasDatabaseRetriever()

    def _clip(self, text: str, limit: int) -> str:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + "..."

    def _looks_garbled(self, text: str) -> bool:
        return "\ufffd" in str(text or "")

    def _strip_unreliable_history_lines(self, text: str) -> str:
        clean_lines: list[str] = []
        for line in str(text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                clean_lines.append(line)
                continue
            if self._looks_garbled(stripped):
                continue
            if re.match(r"^(路径|文件树|Brain 路径|SiYuan path|write_path)\s*[:：]", stripped, re.I):
                continue
            clean_lines.append(line)
        return "\n".join(clean_lines).strip()

    def _normalize_history(self, history: Any, *, limit: int = 12) -> list[dict[str, Any]]:
        if not isinstance(history, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in history[-limit:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = self._strip_unreliable_history_lines(str(item.get("text") or item.get("content") or ""))
            content = self._clip(content, 900)
            if not content:
                continue
            history_item: dict[str, Any] = {"role": role, "text": content}
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            if data:
                history_item["data"] = data
            for key in ("retrieval_plan", "retrieval"):
                value = item.get(key)
                if isinstance(value, dict):
                    history_item[key] = value
            normalized.append(history_item)
        return normalized

    def _latest_note_title(self, history: list[dict[str, Any]]) -> str:
        title_re = re.compile(r"^标题\s*[:：]\s*(.+)$")
        for item in reversed(history):
            if item["role"] != "assistant":
                continue
            for line in item["text"].splitlines():
                match = title_re.match(line.strip())
                if not match:
                    continue
                title = self._clip(match.group(1), 80)
                if title and not self._looks_garbled(title):
                    return title
        return ""

    def _title_from_revision_text(self, text: str) -> str:
        patterns = [
            r"帮我(?:修改|改|重写|修正)\s*([^，。！？\n]+?)(?:的?就好|这条|这张|这篇|$)",
            r"(?:修改|改一下|修一下|重写|修正)\s*([^，。！？\n]+?)(?:的?就好|这条|这张|这篇|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, str(text or ""))
            if not match:
                continue
            title = re.sub(r"^(?:一下|下|这条|这张|这个)\s*", "", match.group(1)).strip()
            title = self._clip(title, 80)
            if title and not self._looks_garbled(title):
                return title
        return ""

    def _has_recent_card_context(self, history: list[dict[str, Any]]) -> bool:
        if self._latest_note_title(history):
            return True
        markers = ("入库卡片", "知识卡", "这条卡片", "是否成功接入", "一句话摘要", "值得学习")
        for item in history[-6:]:
            if item["role"] == "assistant" and any(marker in item["text"] for marker in markers):
                return True
        return False

    def _looks_like_revision_followup(self, text: str, history: list[dict[str, Any]]) -> bool:
        if not self._has_recent_card_context(history):
            return False
        return bool(re.search(
            r"(重新提交|重新入库|重提|帮我(?:修改|改|重写|修正)|"
            r"可以(?:帮我)?(?:修改|改|重写|修正)|修改一下|改一下|修一下|"
            r"改成|改为|替换成|补充|删掉|删除|更短|更准|更落地)",
            str(text or ""),
        ))

    def _note_revision_reply(self, history: list[dict[str, Any]], request_text: str = "") -> tuple[str, dict[str, Any]]:
        candidate_title = self._title_from_revision_text(request_text) or self._latest_note_title(history)
        current_request = self._strip_unreliable_history_lines(request_text)
        current_request = self._clip(current_request, 160) if current_request else ""
        reply = self._revision_fallback_reply(candidate_title, current_request)
        return reply, {
            "revision_request": True,
            "revision_mode": "discuss_then_modify",
            "candidate_title": candidate_title,
            "current_request": current_request,
            "requested_fields": ["note_identity", "dissatisfaction", "desired_change"],
        }

    def _revision_fallback_reply(self, candidate_title: str, current_request: str = "") -> str:
        target = f"「{candidate_title}」" if candidate_title else "这条笔记"
        if current_request:
            return (
                f"可以，我先按 {target} 处理这个修改意图：{current_request}\n"
                "这里会走受控修订：先生成修订版，再过质量门禁和写入策略；你可以继续直接说要删哪句、替换成什么或补哪一点。"
            )
        return (
            f"可以，我先按 {target} 进入受控修订。"
            "你直接说要删掉、替换或补充的内容就行，我会把它整理成修订意图，后续再走修订版和质量门禁。"
        )

    def _build_revision_prompt(self, text: str, history: list[dict[str, Any]], revision_data: dict[str, Any]) -> str:
        parts = [
            "你正在回复 Lucas 知识库控制台里的笔记修订对话。",
            "这不是普通闲聊，也不是实际写入动作；当前目标是自然地承接用户的修改意见。",
            f"候选笔记标题：{revision_data.get('candidate_title') or '未确定'}",
            f"用户当前修改要求：{revision_data.get('current_request') or self._clip(text, 800)}",
            "",
            "回复要求：",
            "- 用自然中文回复，像在协作，不要像表单或模板。",
            "- 如果用户已经说了具体要删/改的内容，直接确认你理解到的修改点，不要再问一整套 checklist。",
            "- 可以简短说明这是受控修订：先出修订版，再走质量门禁和写入策略。",
            "- 不要说“我不能执行命令/不能调用后端/不能写入知识库”这类拒绝式话术。",
            "- 不要声称已经完成写入、覆盖、删除或移动旧笔记。",
            "- 最多 4 句话。",
        ]
        if history:
            parts.append("")
            parts.append("最近对话摘要（只供理解，不要复述路径）：")
            for item in history[-4:]:
                speaker = "用户" if item["role"] == "user" else "助手"
                parts.append(f"{speaker}: {self._clip(item['text'], 300)}")
        return "\n".join(parts)

    def _revision_system_prompt(self) -> str:
        return (
            "你是 Lucas 个人知识库的修订对话 agent。"
            "你的任务是把用户对已入库笔记的不满意自然承接成可执行的修订意图。"
            "不要输出模板化三问清单，不要把用户推回手动编辑，不要说不能调用后端。"
            "你不能声称已经写入或覆盖旧笔记；可以说明会走受控修订、质量门禁和写入策略。"
        )

    def _revision_reply_looks_unhelpful(self, text: str) -> bool:
        value = str(text or "")
        blocked = ("不能执行命令", "无法执行命令", "不能调用后端", "无法调用后端", "我确实不能写入知识库", "不能写入知识库")
        return any(item in value for item in blocked)

    def _public_ai_facts(self) -> dict[str, Any]:
        try:
            return get_public_ai_config()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _public_storage_facts(self) -> dict[str, Any]:
        try:
            from storage_config import get_public_storage_config

            return get_public_storage_config()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _config_context_lines(self) -> list[str]:
        lines = ["只读配置事实（涉及当前模型、存储、可写目标时必须以这里为准；没有事实就说明未配置，不要猜）："]
        ai = self._public_ai_facts()
        ai_provider = ai.get("provider") if isinstance(ai.get("provider"), dict) else {}
        if ai.get("ok") and ai_provider:
            lines.append(
                "AI配置："
                f"active_provider={ai.get('active_provider') or ai_provider.get('provider_id') or 'unknown'}；"
                f"label={ai_provider.get('label') or 'unknown'}；"
                f"model={ai_provider.get('model') or 'unknown'}；"
                f"base_url={ai_provider.get('base_url') or 'unknown'}；"
                f"api_key_present={'yes' if ai_provider.get('api_key_present') else 'no'}。"
            )
        else:
            lines.append(f"AI配置：读取失败（{ai.get('error') or 'unknown'}）。")

        storage = self._public_storage_facts()
        storage_provider = storage.get("provider") if isinstance(storage.get("provider"), dict) else {}
        if storage.get("ok"):
            targets = storage.get("storage_targets") if isinstance(storage.get("storage_targets"), list) else []
            writable = storage.get("writable_targets") if isinstance(storage.get("writable_targets"), list) else []
            writable_labels = [str(item.get("label") or item.get("target_id")) for item in writable if isinstance(item, dict)]
            lines.append(
                "存储配置："
                f"active_provider={storage.get('active_provider') or storage_provider.get('provider_id') or 'unknown'}；"
                f"label={storage_provider.get('label') or 'unknown'}；"
                f"auto_ingest_enabled={'yes' if storage.get('auto_ingest_enabled') else 'no'}；"
                f"storage_targets={','.join(targets) if targets else 'none'}；"
                f"writable_targets={','.join(writable_labels) if writable_labels else 'none'}；"
                f"lucas_database_write_policy={storage.get('lucas_database_write_policy') or 'unknown'}。"
            )
            for target in storage.get("target_options") or []:
                if not isinstance(target, dict):
                    continue
                lines.append(
                    "存储目标："
                    f"{target.get('label') or target.get('target_id')}；"
                    f"enabled={'yes' if target.get('enabled') else 'no'}；"
                    f"can_write={'yes' if target.get('can_write') else 'no'}；"
                    f"status={target.get('status') or 'unknown'}；"
                    f"provider={target.get('provider_label') or target.get('provider_id') or 'unknown'}；"
                    f"base_url={target.get('base_url') or 'unknown'}；"
                    f"endpoint={target.get('endpoint') or 'unknown'}；"
                    f"key={'present' if target.get('api_key_present') else 'missing'}({target.get('api_key_env') or 'env/local'})。"
                )
        else:
            lines.append(f"存储配置：读取失败（{storage.get('error') or 'unknown'}）。")
        return lines

    def _looks_like_storage_config_question(self, text: str) -> bool:
        value = str(text or "").strip().casefold()
        if not value:
            return False
        has_storage_subject = any(word in value for word in ("知识库", "数据库", "brain", "siyuan", "思源", "存储", "入库"))
        has_ai_subject = any(word in value for word in ("模型", "model", "provider", "提供商", "ai配置", "ai 配置"))
        if not has_ai_subject and re.search(r"(展开|详细|详情|明细|完整).*(配置|存储)", value):
            return True
        if not has_storage_subject:
            return False
        patterns = [
            r"(现在|当前|目前).*(可以|能|可).*(写入|入库)",
            r"(可以|能|可).*(写入|入库).*(哪些|哪里|什么|知识库|数据库)",
            r"(写入|入库).*(目标|配置|勾选|哪些|哪里|什么)",
            r"(存储配置|自动入库|storage_targets|数据库目标|可写目标)",
            r"(勾选|已选).*(siyuan|思源|brain|数据库|知识库)",
            r"(有哪些|哪些|什么).*(知识库|数据库).*(接入|连接|配置|目标)",
            r"(知识库|数据库).*(接入|连接).*(有哪些|哪些|什么|配置|目标)",
        ]
        return any(re.search(pattern, value) for pattern in patterns)

    def _looks_like_ai_config_question(self, text: str) -> bool:
        value = str(text or "").strip().casefold()
        if not value:
            return False
        has_ai_subject = any(word in value for word in ("模型", "model", "provider", "提供商", "ai配置", "ai 配置"))
        if not has_ai_subject:
            return False
        return any(word in value for word in ("现在", "当前", "目前", "配置", "用的", "使用", "哪个", "哪一个", "是什么"))

    def _wants_detail(self, text: str) -> bool:
        value = str(text or "").strip().casefold()
        return bool(re.search(r"(详细|详情|展开|完整|明细|每个|全部|具体|base url|endpoint|key|token|状态)", value))

    def _looks_like_status_followup(self, text: str) -> bool:
        value = re.sub(r"\s+", "", str(text or "").strip().casefold())
        if not value or len(value) > 24:
            return False
        return bool(re.match(
            r"^(现在)?(好了(吗|么)?|好了没|可以了(吗|么)?|可以吗|行了(吗|么)?|"
            r"能用了(吗|么)?|能用了吗|恢复了(吗|么)?|能试了(吗|么)?|ok了(吗|么)?)"
            r"[?？!！。]*$",
            value,
        ))

    def _status_followup_reply(self) -> ChatResponse:
        return ChatResponse(
            ok=True,
            reply_text=(
                "可以继续试。配置类问题我会按本地配置回答；没有 job_id 或真实任务结果时，"
                "我不会编造网络、查询或入库状态。"
            ),
            intent="status_followup",
            confidence=0.9,
            data={"model_called": False},
        )

    def _rag_enabled(self) -> bool:
        value = str(os.environ.get("LUCAS_CHAT_RAG_ENABLED", "true")).strip().casefold()
        return value not in {"0", "false", "no", "off", "disabled"}

    def _looks_like_simple_greeting(self, text: str) -> bool:
        value = re.sub(r"\s+", "", str(text or "").strip().casefold())
        if not value or len(value) > 14:
            return False
        return bool(re.match(r"^(你好|您好|嗨|哈喽|hello|hi|在吗|早|早上好|晚上好)[?？!！。]*$", value))

    def _looks_like_simple_nonretrieval(self, text: str) -> bool:
        value = re.sub(r"\s+", "", str(text or "").strip().casefold())
        if not value:
            return True
        return value in {
            "谢谢", "多谢", "感谢", "好的", "好", "可以", "行", "嗯", "嗯嗯", "收到", "明白",
            "ok", "okay", "yes", "no", "不用", "算了",
        }

    def _strip_outer_quotes(self, text: str) -> str:
        value = str(text or "").strip()
        return value.strip(" \t\r\n\"'“”‘’《》「」『』")

    def _clean_topic_candidate(self, text: str) -> str:
        value = self._strip_outer_quotes(text)
        value = value.replace("芝士包", "知识包")
        value = re.sub(r"^[：:，,。.\s]+", "", value)
        value = re.sub(r"[?？!！。.,，;；:：]+$", "", value).strip()
        value = re.sub(r"^(?:一下|下|关于|有关|围绕|针对|对|把)\s*", "", value)
        value = re.sub(r"(?:的)?(?:相关|有关)?(?:的)?(?:内容|资料|材料|信息|事情|情况|笔记|卡片|知识|主题|方向|这一块|这块|这方面|这个主题)$", "", value).strip()
        value = re.sub(r"(?:的)?(?:相关|有关)(?:的)?$", "", value).strip()
        value = re.sub(r"的$", "", value).strip()
        return self._strip_outer_quotes(value)

    def _looks_like_topic_phrase(self, text: str) -> bool:
        value = self._clean_topic_candidate(text)
        compact = re.sub(r"\s+", "", value.casefold())
        if self._looks_like_simple_nonretrieval(compact):
            return False
        if self._looks_like_detail_followup(value):
            return False
        if not re.search(r"[\w\u4e00-\u9fff]", value, re.U):
            return False
        if len(compact) < 2 or len(value) > 80:
            return False
        if re.search(r"https?://", value, re.I):
            return False
        if re.search(r"(我|你|他|她|它|这个|那个|怎么|为什么|如何|什么|哪些|哪个|是否|有没有|可以|帮我|请|需要)", compact):
            return False
        return True

    def _looks_like_explicit_retrieval_candidate(self, text: str) -> bool:
        value = self._clean_topic_candidate(text)
        compact = re.sub(r"\s+", "", value.casefold())
        if self._looks_like_simple_nonretrieval(compact):
            return False
        if not re.search(r"[\w\u4e00-\u9fff]", value, re.U):
            return False
        if len(compact) < 2 or len(value) > 160:
            return False
        if re.search(r"https?://", value, re.I):
            return False
        if re.fullmatch(r"(?:什么|哪个|哪些|怎么|如何|为什么|有没有|是否|可以|帮我|请|需要|办法|方法|建议|推荐)", compact):
            return False
        return True

    def _action_topic_patterns(self) -> list[tuple[str, str]]:
        return [
            (
                "search",
                r"^(?:请|麻烦|帮我|给我|你帮我|能不能|可以|可不可以|我想|想|想要)?\s*"
                r"(?:(?:从|在)?(?:我的|当前|本地)?(?:知识库|数据库|资料库|笔记|卡片|anttrail|brain)(?:里|中|里面)?\s*)?"
                r"(?:查一下|查下|查询一下|查询|搜索一下|搜索|检索一下|检索|找一下|找下|找找|查找)\s*"
                r"(?:关于|有关)?\s*(?:资料|材料|信息|内容|笔记|卡片)?\s*[：:，,]?\s*",
            ),
            (
                "search",
                r"^(?:请|麻烦|帮我|给我|你帮我|能不能|可以|可不可以|我想|想|想要)?\s*"
                r"(?:查|查询|搜索|检索|找)\s*(?:一下|下)?\s*(?:关于|有关)?\s*"
                r"(?:资料|材料|信息|内容|笔记|卡片)\s*[：:，,]?\s*",
            ),
            (
                "search",
                r"^(?:(?:从|在)?(?:我的|当前|本地)?(?:知识库|数据库|资料库|笔记|卡片|anttrail|brain)(?:里|中|里面)?\s*)"
                r"(?:有没有|有无|是否有|查|查询|搜索|检索|找)\s*",
            ),
            (
                "search",
                r"^(?=.*(?:资料|材料|笔记|卡片|知识库|数据库|库里|历史|之前|以前))"
                r"(?:之前|以前|历史里|库里)?\s*(?:有没有|有无|是否有)\s*(?:关于|有关)?\s*",
            ),
        ]

    def _extract_action_topic(self, text: str) -> tuple[str, str]:
        value = self._strip_outer_quotes(text)
        value = value.replace("芝士包", "知识包")
        value = re.sub(r"^[：:，,。.\s]+", "", value)
        value = re.sub(r"[?？!！。.,，;；:：]+$", "", value).strip()
        value = re.sub(r"^(?:一下|下)\s*", "", value)
        if not value:
            return "", ""

        for action, pattern in self._action_topic_patterns():
            raw_candidate = re.sub(pattern, "", value, count=1, flags=re.I).strip()
            if raw_candidate == value:
                continue
            candidate = self._clean_topic_candidate(raw_candidate)
            if self._looks_like_explicit_retrieval_candidate(candidate):
                return action, candidate

        return "", ""

    def _looks_like_detail_followup(self, text: str) -> bool:
        value = re.sub(r"\s+", "", str(text or "").strip().casefold())
        if not value or len(value) > 24:
            return False
        value = re.sub(r"[?？!！。.,，;；:：]+$", "", value)
        return bool(re.match(
            r"^(?:"
            r"(?:具体|详细|仔细|深入)?(?:展开|展开说说|展开讲讲|说说|讲讲|聊聊|解释下|解释一下|介绍下|介绍一下)"
            r"|(?:具体|详细|仔细|深入)(?:说说|讲讲|聊聊|解释下|解释一下)"
            r"|(?:继续|接着)(?:说|讲|聊|展开)?"
            r"|(?:再)?(?:具体|详细)(?:一点|点|些)"
            r"|多说(?:一点|点|些)"
            r")(?:吧|呢|呀|啊|可以吗)?$",
            value,
        ))

    def _looks_like_explicit_retrieval_followup(self, text: str) -> bool:
        value = re.sub(r"\s+", "", str(text or "").strip().casefold())
        if not value or len(value) > 32:
            return False
        value = re.sub(r"[?？!！。.,，;；:：]+$", "", value)
        return bool(re.match(
            r"^(?:(?:继续|再|接着|重新)?(?:查|查询|搜索|检索|找|找找)|"
            r"(?:从|在)?(?:知识库|数据库|资料库|笔记|卡片|库里)(?:继续|再)?(?:查|查询|搜索|检索|找|找找|看看|看下|看一下))"
            r"(?:一下|下|资料|材料|信息|内容|笔记|卡片|相关资料|相关内容|吧|呢|呀|啊|可以吗)*$",
            value,
        ))

    def _history_data(self, item: dict[str, Any]) -> dict[str, Any]:
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        return data

    def _history_retrieval_plan(self, item: dict[str, Any]) -> dict[str, Any]:
        direct = item.get("retrieval_plan") if isinstance(item.get("retrieval_plan"), dict) else {}
        if direct:
            return direct
        data = self._history_data(item)
        return data.get("retrieval_plan") if isinstance(data.get("retrieval_plan"), dict) else {}

    def _history_retrieval(self, item: dict[str, Any]) -> dict[str, Any]:
        direct = item.get("retrieval") if isinstance(item.get("retrieval"), dict) else {}
        if direct:
            return direct
        data = self._history_data(item)
        return data.get("retrieval") if isinstance(data.get("retrieval"), dict) else {}

    def _retrieval_history_can_answer(self, retrieval: dict[str, Any]) -> bool:
        if not retrieval:
            return False
        if retrieval.get("can_answer") is False:
            return False
        if retrieval.get("ok") is False:
            return False
        status = str(retrieval.get("status") or "").casefold()
        if status in {"low_confidence", "topic_mismatch", "retrieval_client_exception", "skipped"}:
            return False
        confidence = retrieval.get("confidence") if isinstance(retrieval.get("confidence"), dict) else {}
        return confidence.get("low_confidence") is not True

    def _latest_retrieval_topic(self, history: list[dict[str, Any]]) -> str:
        for item in reversed(history):
            if item.get("role") != "assistant":
                continue
            retrieval = self._history_retrieval(item)
            if retrieval and not self._retrieval_history_can_answer(retrieval):
                continue
            plan = self._history_retrieval_plan(item)
            action = str(plan.get("action") or "").strip().casefold()
            if action not in {"search", "expand"}:
                continue
            topic = str(plan.get("topic") or plan.get("query") or "").strip()
            topic = self._clean_topic_candidate(topic)
            if topic and self._looks_like_topic_phrase(topic):
                return topic
        return ""

    def _latest_user_topic(self, history: list[dict[str, Any]]) -> str:
        for item in reversed(history):
            if item.get("role") != "user":
                continue
            _action, topic = self._extract_action_topic(item.get("text") or "")
            if topic:
                return topic
        return ""

    def _latest_followup_topic(self, history: list[dict[str, Any]]) -> str:
        return self._latest_retrieval_topic(history) or self._latest_user_topic(history)

    def _build_forced_retrieval_plan(self, text: str, query: str = "") -> RetrievalPlan:
        topic = self._clean_topic_candidate(query or text)
        if not self._looks_like_explicit_retrieval_candidate(topic):
            return RetrievalPlan(False, reason="forced_retrieval_empty_query")
        return RetrievalPlan(True, query=topic, topic=topic, action="search", reason="forced_retrieval")

    def _build_retrieval_plan(self, text: str, history: list[dict[str, Any]]) -> RetrievalPlan:
        value = str(text or "").strip()
        if not self._rag_enabled():
            return RetrievalPlan(False, reason="rag_disabled")
        if (
            self._looks_like_simple_greeting(value)
            or self._looks_like_status_followup(value)
            or self._looks_like_simple_nonretrieval(value)
        ):
            return RetrievalPlan(False, reason="smalltalk")
        if not re.search(r"[\w\u4e00-\u9fff]", value, re.U):
            return RetrievalPlan(False, reason="no_searchable_text")
        if self._looks_like_explicit_retrieval_followup(value):
            topic = self._latest_followup_topic(history)
            if topic:
                return RetrievalPlan(True, query=topic, topic=topic, action="search", used_history=True, reason="explicit_retrieval_followup_from_history")
        action, topic = self._extract_action_topic(value)
        if topic:
            return RetrievalPlan(True, query=topic, topic=topic, action=action or "lookup", reason="topic_extracted")
        return RetrievalPlan(False, reason="no_explicit_retrieval_intent")

    def _needs_history_for_retrieval_query(self, text: str) -> bool:
        value = re.sub(r"\s+", "", str(text or "").strip().casefold())
        if len(value) <= 28:
            return True
        return bool(re.search(r"(这个|这个呢|它|上面|刚才|前面|上一条|这件事|继续|那条|那张|那篇)", value))

    def _normalize_topic_followup(self, text: str) -> str:
        value = str(text or "").strip()
        without_punctuation = re.sub(r"[?？!！。.,，;；:：]+$", "", value).strip()
        normalized = re.sub(r"(呢|吗|嘛|吧|呀|啊)$", "", without_punctuation).strip()
        if normalized == without_punctuation:
            return ""
        compact = re.sub(r"\s+", "", normalized)
        if compact and not re.search(r"(这个|它|上面|刚才|前面|上一条|这件事|继续|那条|那张|那篇)", compact):
            return normalized
        return ""

    def _build_retrieval_query(self, text: str, history: list[dict[str, Any]]) -> str:
        return self._build_retrieval_plan(text, history).query

    def _env_int(self, name: str, default: int, *, minimum: int, maximum: int) -> int:
        try:
            value = int(os.environ.get(name, "") or default)
        except Exception:
            value = default
        return max(minimum, min(value, maximum))

    def _normalize_match_text(self, text: str) -> str:
        value = str(text or "").casefold()
        for src, dst in (("零", "0"), ("一", "1"), ("二", "2"), ("三", "3"), ("四", "4"), ("五", "5")):
            value = value.replace(src, dst)
        return re.sub(r"[\s_\-/\\|:：#【】\[\]（）()《》「」『』,，.。!！?？]+", "", value)

    def _topic_matches_text(self, topic: str, text: str) -> bool:
        topic_norm = self._normalize_match_text(topic)
        text_norm = self._normalize_match_text(text)
        if not topic_norm or not text_norm:
            return False
        if topic_norm in text_norm:
            return True
        if len(topic_norm) >= 4 and text_norm in topic_norm:
            return True
        if len(topic_norm) <= 3:
            return False
        bigrams = {topic_norm[index:index + 2] for index in range(max(0, len(topic_norm) - 1))}
        if not bigrams:
            return False
        hits = sum(1 for item in bigrams if item in text_norm)
        return hits >= max(1, min(3, len(bigrams) // 2))

    def _retrieval_has_topic_match(self, retrieval: RetrievalResult, topic: str) -> bool:
        for source in retrieval.sources:
            if self._topic_matches_text(topic, f"{source.get('title') or ''} {source.get('path') or ''}"):
                return True
        for citation in retrieval.citations:
            if self._topic_matches_text(
                topic,
                f"{citation.get('title') or ''} {citation.get('path') or ''} {citation.get('text_preview') or citation.get('text') or ''}",
            ):
                return True
        return False

    def _enforce_topic_guard(self, retrieval: RetrievalResult, plan: RetrievalPlan) -> RetrievalResult:
        if not plan.topic or not retrieval.attempted or not retrieval.ok or not retrieval.can_answer:
            return retrieval
        if self._retrieval_has_topic_match(retrieval, plan.topic):
            return retrieval
        return RetrievalResult(
            attempted=True,
            ok=True,
            status="topic_mismatch",
            can_answer=False,
            query=retrieval.query,
            context_text="",
            context=retrieval.context,
            answerability={"can_answer": False, "reason": "topic_mismatch"},
            confidence={**retrieval.confidence, "low_confidence": True, "reason": "topic_mismatch"},
            sources=retrieval.sources,
            citations=retrieval.citations,
            warnings=[*retrieval.warnings, "topic_mismatch"],
            target=retrieval.target,
            latency_ms=retrieval.latency_ms,
            status_code=retrieval.status_code,
        )

    def _retrieve_for_chat(self, plan: RetrievalPlan, *, timeout_sec: int) -> RetrievalResult | None:
        if not plan.should_retrieve:
            return None
        rag_timeout = self._env_int("LUCAS_CHAT_RAG_TIMEOUT_SEC", min(max(int(timeout_sec or 60), 2), 45), minimum=1, maximum=120)
        try:
            retrieval = self.retriever.retrieve(
                plan.query,
                timeout_sec=rag_timeout,
                limit=self._env_int("LUCAS_CHAT_RAG_LIMIT", 8, minimum=1, maximum=50),
                token_budget=self._env_int("LUCAS_CHAT_RAG_TOKEN_BUDGET", 1800, minimum=200, maximum=12000),
                max_chunks_per_source=self._env_int("LUCAS_CHAT_RAG_MAX_CHUNKS_PER_SOURCE", 2, minimum=1, maximum=10),
                agent="chat_gateway",
            )
            return self._enforce_topic_guard(retrieval, plan)
        except Exception as exc:
            return RetrievalResult(
                attempted=True,
                ok=False,
                status="retrieval_client_exception",
                can_answer=False,
                query=plan.query,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _direct_source_labels(self, retrieval: RetrievalResult, topic: str) -> list[str]:
        labels: list[str] = []
        if not topic:
            return labels
        for source in retrieval.sources:
            title = str(source.get("title") or "")
            path = str(source.get("path") or "")
            if self._topic_matches_text(topic, f"{title} {path}"):
                label = str(source.get("citation_label") or f"[Source {source.get('source_index')}]")
                labels.append(f"{label} {title}".strip())
        return labels[:5]

    def _retrieval_prompt_block(self, retrieval: RetrievalResult | None, plan: RetrievalPlan | None = None) -> list[str]:
        if retrieval is None or not retrieval.attempted:
            return []
        lines: list[str] = []
        if plan and plan.should_retrieve:
            lines.append(
                "检索规划："
                f"用户动作={plan.action or 'ask'}；"
                f"核心主题={plan.topic or '(未抽取，按完整问题检索)'}；"
                f"实际检索query={plan.query}；"
                f"是否使用历史={'yes' if plan.used_history else 'no'}。"
            )
        if retrieval.ok and retrieval.can_answer and retrieval.context_text.strip():
            if plan and plan.topic:
                direct_sources = self._direct_source_labels(retrieval, plan.topic)
                if direct_sources:
                    lines.append(f"强相关来源（标题/路径直接匹配核心主题）：{'; '.join(direct_sources)}。")
            lines.extend([
                "Lucas Database 检索结果（已先查本地知识库；优先依据这些证据回答，但只能围绕核心主题，不要用其它弱相关来源替代回答）：",
                self._clip(retrieval.context_text, 9000),
                "回答要求：使用检索证据时用 [Source n] 标注来源；不要输出内部路径；如果来源标题或路径与当前问题核心词直接匹配，必须承认这是直接相关来源，不能回答“没有直接相关内容”；如果只有分类/标题而正文不足，要说“有这个节点/分类，但详细内容不足”；不要列举与核心主题无关的其它可用知识。",
            ])
            return lines
        reason = retrieval.error or retrieval.answerability.get("reason") or ",".join(retrieval.warnings) or retrieval.status
        lines.extend([
            f"Lucas Database 检索状态：未找到可靠命中或检索不可用（{self._clip(reason, 260)}）。",
            "回答要求：自然承接用户问题；不要声称已经从数据库查到答案。可以说明本轮库内证据不足，然后继续用当前模型的判断协助讨论，语气要短、自然、少模板。",
        ])
        return lines

    def _target_line(self, target: dict[str, Any]) -> str:
        enabled = "已勾选" if target.get("enabled") else "未勾选"
        key = "key 已配置" if target.get("api_key_present") else f"缺少 {target.get('api_key_env') or 'API Key'}"
        if target.get("can_write"):
            state = "可写"
        elif target.get("status") == "missing_key":
            state = f"不可写：{key}"
        elif target.get("status") == "missing_base_url":
            state = "不可写：缺少 Base URL"
        elif not target.get("enabled"):
            state = "不可写：未勾选"
        else:
            state = f"不可写：{target.get('reason') or target.get('status') or '未就绪'}"
        details = []
        if target.get("status") != "missing_key":
            details.append(key)
        details.append(f"Base URL {target.get('base_url') or '未配置'}")
        details.append(f"Endpoint {target.get('endpoint') or '未配置'}")
        return (
            f"- {target.get('label') or target.get('target_id')}：{enabled}，{state}，"
            f"{'，'.join(details)}。"
        )

    def _storage_config_reply(self, request_text: str = "") -> ChatResponse:
        storage = self._public_storage_facts()
        if not storage.get("ok"):
            return ChatResponse(
                ok=False,
                reply_text=f"我读取存储配置失败：{storage.get('error') or 'unknown'}",
                intent="storage_config_query",
                confidence=0.95,
                data={"model_called": False, "storage": storage},
                error=str(storage.get("error") or "storage_config_unavailable"),
            )
        provider = storage.get("provider") if isinstance(storage.get("provider"), dict) else {}
        targets = storage.get("target_options") if isinstance(storage.get("target_options"), list) else []
        enabled = [target for target in targets if isinstance(target, dict) and target.get("enabled")]
        writable = [target for target in targets if isinstance(target, dict) and target.get("can_write")]
        enabled_names = "、".join(str(item.get("label") or item.get("target_id")) for item in enabled) or "无"
        writable_names = "、".join(str(item.get("label") or item.get("target_id")) for item in writable) or "无"
        provider_label = provider.get("label") or storage.get("active_provider") or "unknown"
        auto_state = "已开启" if storage.get("auto_ingest_enabled") else "已关闭"
        if not self._wants_detail(request_text):
            lines = [
                f"按当前配置：自动入库{auto_state}，目标是 {enabled_names}；现在实际可写是 {writable_names}。",
                f"存储面板当前选中：{provider_label}。正式链接入库仍会先经过模型写卡和质量门禁。",
                "需要每个目标的接口、key 和状态明细时，说“展开配置”。",
            ]
        else:
            lines = [
                "我按当前存储配置看到：",
                f"- 存储配置面板当前选中：{provider_label}。",
                f"- 自动入库：{auto_state}。",
                f"- 自动入库目标：{enabled_names}。",
                f"- 现在实际可写入：{writable_names}。",
            ]
            lines.extend(self._target_line(target) for target in targets if isinstance(target, dict))
            policy = storage.get("lucas_database_write_policy")
            if policy:
                lines.append(f"- Brain 写入策略：{policy}。")
            lines.append("正式链接入库仍会先经过模型写卡和质量门禁；这里列的是配置层面能写到哪里。")
        return ChatResponse(
            ok=True,
            reply_text="\n".join(lines),
            intent="storage_config_query",
            confidence=0.96,
            data={"model_called": False, "storage": storage},
        )

    def _ai_config_reply(self) -> ChatResponse:
        ai = self._public_ai_facts()
        if not ai.get("ok"):
            return ChatResponse(
                ok=False,
                reply_text=f"我读取模型配置失败：{ai.get('error') or 'unknown'}",
                intent="ai_config_query",
                confidence=0.95,
                data={"model_called": False, "ai_config": ai},
                error=str(ai.get("error") or "ai_config_unavailable"),
            )
        provider = ai.get("provider") if isinstance(ai.get("provider"), dict) else {}
        key = "已配置" if provider.get("api_key_present") else f"缺少 {provider.get('api_key_env') or 'API Key'}"
        reply = (
            "我按当前模型配置看到：\n"
            f"- 提供商：{provider.get('label') or ai.get('active_provider') or 'unknown'}。\n"
            f"- 模型：{provider.get('model') or '未配置'}。\n"
            f"- Base URL：{provider.get('base_url') or '未配置'}。\n"
            f"- Key 状态：{key}。"
        )
        return ChatResponse(
            ok=True,
            reply_text=reply,
            intent="ai_config_query",
            confidence=0.95,
            data={"model_called": False, "ai_config": ai},
        )

    def _build_prompt(
        self,
        text: str,
        history: list[dict[str, Any]],
        retrieval: RetrievalResult | None = None,
        retrieval_plan: RetrievalPlan | None = None,
    ) -> str:
        parts = ["你正在回复 Lucas 知识库控制台里的当前对话。"]
        parts.extend(self._config_context_lines())
        parts.extend(self._retrieval_prompt_block(retrieval, retrieval_plan))
        if history:
            parts.append("最近对话上下文（从旧到新）：")
            for item in history:
                speaker = "用户" if item["role"] == "user" else "助手"
                parts.append(f"{speaker}: {item['text']}")
        else:
            parts.append("最近对话上下文：无")
        parts.append("当前用户消息：")
        parts.append(self._clip(text, 6000))
        return "\n".join(parts)

    def _model_failure_reply(self, error: str) -> str:
        if error == "api_key_missing":
            return "模型配置还没准备好：当前模型缺少 API Key。请在设置里的「模型配置」保存 key，或确认对应环境变量已配置。"
        if error == "base_url_missing":
            return "模型配置还没准备好：当前模型缺少 Base URL。请在设置里的「模型配置」补全接口地址。"
        if error == "model_missing":
            return "模型配置还没准备好：当前模型名为空。请在设置里的「模型配置」选择或填写模型名。"
        if error == "empty_model_response":
            return "模型已返回，但内容为空。可以重试一次，或在设置里测试当前模型连接。"
        return f"模型暂时没有返回有效回复：{error}"

    def respond(
        self,
        text: str,
        *,
        conversation_history: Any = None,
        timeout_sec: int = 60,
        max_tokens: int = 1200,
        force_retrieval: bool = False,
        retrieval_query: str = "",
    ) -> ChatResponse:
        intent = self.classifier.classify(text)
        if not str(text or "").strip():
            return ChatResponse(
                ok=False,
                reply_text="请输入要发送给 agent 的内容。",
                intent=intent.intent,
                confidence=intent.confidence,
                data={**intent.to_dict(), "model_called": False},
                error="empty_message",
            )

        history = self._normalize_history(conversation_history)
        if self._looks_like_storage_config_question(text):
            return self._storage_config_reply(text)
        if self._looks_like_ai_config_question(text):
            return self._ai_config_reply()
        if self._looks_like_status_followup(text):
            return self._status_followup_reply()

        is_revision_request = (
            intent.intent == "note_revision_request"
            or self._looks_like_revision_followup(text, history)
        )
        if is_revision_request:
            fallback_reply, revision_data = self._note_revision_reply(history, text)
            intent_name = "note_revision_request"
            model_result = self.router.generate_text(
                "chat_response",
                self._build_revision_prompt(text, history, revision_data),
                system_prompt=self._revision_system_prompt(),
                metadata={
                    "timeout_sec": max(1, min(int(timeout_sec or 60), 180)),
                    "max_tokens": max(64, min(int(max_tokens or 900), 1600)),
                },
            )
            model_data = {
                "model_called": True,
                "model_result_ok": model_result.ok,
                "model_provider": model_result.model_provider,
                "model_name": model_result.model_name,
                "model_error": model_result.error,
                "latency_ms": model_result.latency_ms,
                "raw_usage": model_result.raw_usage,
                "history_messages_used": len(history),
            }
            model_text = model_result.text.strip() if model_result.ok else ""
            reply_text = model_text if model_text and not self._revision_reply_looks_unhelpful(model_text) else fallback_reply
            return ChatResponse(
                ok=True,
                reply_text=reply_text,
                intent=intent_name,
                confidence=max(intent.confidence, 0.82),
                data={
                    **intent.to_dict(),
                    "intent": intent_name,
                    "confidence": max(intent.confidence, 0.82),
                    **revision_data,
                    **model_data,
                    "fallback_reply_used": reply_text == fallback_reply,
                },
            )

        retrieval_plan = self._build_forced_retrieval_plan(text, retrieval_query) if force_retrieval else self._build_retrieval_plan(text, history)
        retrieval = self._retrieve_for_chat(retrieval_plan, timeout_sec=timeout_sec)
        retrieval_data = retrieval.to_dict() if retrieval is not None else {
            "attempted": False,
            "ok": False,
            "status": "skipped",
            "can_answer": False,
            "query": "",
        }

        model_result = self.router.generate_text(
            "chat_response",
            self._build_prompt(text, history, retrieval, retrieval_plan),
            system_prompt=chat_responder_system_prompt(),
            metadata={
                "timeout_sec": max(1, min(int(timeout_sec or 60), 180)),
                "max_tokens": max(64, min(int(max_tokens or 1200), 4096)),
            },
        )
        model_data = {
            "model_called": True,
            "model_result_ok": model_result.ok,
            "model_provider": model_result.model_provider,
            "model_name": model_result.model_name,
            "model_error": model_result.error,
            "latency_ms": model_result.latency_ms,
            "raw_usage": model_result.raw_usage,
            "history_messages_used": len(history),
        }
        if model_result.ok and model_result.text.strip():
            return ChatResponse(
                ok=True,
                reply_text=model_result.text.strip(),
                intent=intent.intent,
                confidence=intent.confidence,
                data={**intent.to_dict(), **model_data, "retrieval_plan": retrieval_plan.to_dict(), "retrieval": retrieval_data},
            )

        error = model_result.error or "empty_model_response"
        return ChatResponse(
            ok=False,
            reply_text=self._model_failure_reply(error),
            intent=intent.intent,
            confidence=intent.confidence,
            data={**intent.to_dict(), **model_data, "retrieval_plan": retrieval_plan.to_dict(), "retrieval": retrieval_data},
            error=error,
        )
