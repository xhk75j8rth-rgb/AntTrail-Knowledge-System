from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from chat_gateway.link_extractor import extract_urls
from chat_gateway.message_schema import MessageEvent


JOB_RE = re.compile(r"(?i)\b(job|job_id|任务|状态)\b")
NOTE_REVISION_RE = re.compile(
    r"(笔记|知识卡|卡片|入库内容|摘要|标题|标签|分类).{0,16}"
    r"(不满意|不对|不准|有问题|需要调整|想调整|要调整|调整|修改|改一下|修一下|重写|重新生成|更新|修正)"
    r"|"
    r"(不满意|不对|不准|有问题|需要调整|想调整|要调整|调整|修改|改一下|修一下|重写|重新生成|更新|修正).{0,16}"
    r"(笔记|知识卡|卡片|入库内容|摘要|标题|标签|分类)"
)
NOTE_REVISION_CONTEXT_RE = re.compile(
    r"(这条|那条|刚才|上面|上一条|刚入库|已入库).{0,16}"
    r"(不满意|不对|不准|有问题|需要调整|想调整|要调整|调整|修改|改一下|修一下|重写|重新生成|更新|修正)"
)


@dataclass(slots=True)
class IntentResult:
    intent: str
    confidence: float
    url: str = ""
    reason: str = ""
    requires_tool: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IntentClassifier:
    def classify(self, message: MessageEvent | str) -> IntentResult:
        text = message.text if isinstance(message, MessageEvent) else str(message or "")
        urls = extract_urls(text)
        if urls:
            return IntentResult(
                intent="link_intake",
                confidence=0.99,
                url=urls[0],
                reason="deterministic url match",
                requires_tool=True,
            )
        if NOTE_REVISION_RE.search(text) or NOTE_REVISION_CONTEXT_RE.search(text):
            return IntentResult(
                intent="note_revision_request",
                confidence=0.88,
                url="",
                reason="user wants to discuss changes to an existing note/card",
                requires_tool=False,
            )
        if JOB_RE.search(text):
            return IntentResult(
                intent="job_status",
                confidence=0.84,
                url="",
                reason="contains job/status keywords",
                requires_tool=False,
            )
        if text.strip():
            return IntentResult(
                intent="normal_chat",
                confidence=0.72,
                url="",
                reason="no url and not a job query",
                requires_tool=False,
            )
        return IntentResult(
            intent="unknown",
            confidence=0.1,
            url="",
            reason="empty text",
            requires_tool=False,
        )


def classify_intent(message: MessageEvent | str) -> dict[str, Any]:
    return IntentClassifier().classify(message).to_dict()
