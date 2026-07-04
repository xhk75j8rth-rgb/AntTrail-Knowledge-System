from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from io import BytesIO
from unittest.mock import patch

from ai_layer.card_composer_service import compose_card
from ai_layer.composed_card_schema import build_failure_composed_card_v1, normalize_composed_card_v1
from ai_layer.chat_responder import ChatResponder
from ai_layer.intent_classifier import IntentClassifier
from ai_layer.lucas_retrieval_client import RetrievalResult
from ai_layer.mock_provider import MockProvider
from ai_layer.model_provider import build_provider
from ai_layer.model_router import ModelRouter
from ai_layer.openai_compatible_provider import OpenAICompatibleProvider
from ai_layer.provider_config import get_public_ai_config, save_ai_config
from ai_layer.provider_schema import ModelResult
from chat_gateway.message_schema import MessageEvent
from tools.card_composer import call_model, render_markdown
from tools.run_link_job import resolve_written_title


class AILayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_provider = os.environ.get("AI_LAYER_PROVIDER")
        self._old_config_path = os.environ.get("AI_LAYER_CONFIG_PATH")
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["AI_LAYER_CONFIG_PATH"] = str(Path(self._tmpdir.name) / "ai_layer.local.json")
        os.environ["DEEPSEEK_API_KEY"] = ""
        os.environ["DEEPSEEK_BASE_URL"] = ""
        os.environ["DEEPSEEK_MODEL"] = ""
        os.environ["AI_LAYER_PROVIDER"] = "deepseek_compatible"

    def tearDown(self) -> None:
        if self._old_provider is None:
            os.environ.pop("AI_LAYER_PROVIDER", None)
        else:
            os.environ["AI_LAYER_PROVIDER"] = self._old_provider
        if self._old_config_path is None:
            os.environ.pop("AI_LAYER_CONFIG_PATH", None)
        else:
            os.environ["AI_LAYER_CONFIG_PATH"] = self._old_config_path
        self._tmpdir.cleanup()

    def test_mock_provider_generate_text(self) -> None:
        provider = MockProvider()
        result = provider.generate_text("hello")
        self.assertTrue(result.ok)
        self.assertIn("mock:text:", result.text)
        self.assertEqual(result.model_provider, "mock")

    def test_mock_provider_generate_json(self) -> None:
        provider = MockProvider()
        result = provider.generate_json("hello", schema_name="demo")
        self.assertTrue(result.ok)
        self.assertIsInstance(result.json_data, dict)
        self.assertEqual(result.json_data["schema_name"], "demo")

    def test_intent_classifier_link_intake(self) -> None:
        intent = IntentClassifier().classify("https://v.douyin.com/abc/")
        self.assertEqual(intent.intent, "link_intake")
        self.assertTrue(intent.requires_tool)

    def test_intent_classifier_normal_chat(self) -> None:
        intent = IntentClassifier().classify("你好")
        self.assertEqual(intent.intent, "normal_chat")
        self.assertFalse(intent.requires_tool)

    def test_intent_classifier_job_status(self) -> None:
        intent = IntentClassifier().classify("查询 job 123")
        self.assertEqual(intent.intent, "job_status")
        self.assertFalse(intent.requires_tool)

    def test_intent_classifier_note_revision_request(self) -> None:
        intent = IntentClassifier().classify("我需要调整笔记")

        self.assertEqual(intent.intent, "note_revision_request")
        self.assertFalse(intent.requires_tool)

        with_job_id = IntentClassifier().classify("job_id 是 20260702，摘要不满意，需要修改")
        self.assertEqual(with_job_id.intent, "note_revision_request")

    def test_chat_responder_normal_reply(self) -> None:
        class FakeRouter:
            def __init__(self) -> None:
                self.last_prompt = ""

            def generate_text(self, task_type, prompt, system_prompt=None, metadata=None):  # noqa: ANN001
                self.last_prompt = prompt
                return ModelResult(
                    ok=True,
                    text="这是模型生成的 agent 回复。",
                    model_provider="mock",
                    model_name="mock-model",
                )

        router = FakeRouter()
        response = ChatResponder(router=router).respond(
            "你好",
            conversation_history=[{"role": "user", "text": "上一轮问题"}],
        )
        self.assertTrue(response.ok)
        self.assertEqual(response.intent, "normal_chat")
        self.assertIn("模型生成", response.reply_text)
        self.assertEqual(response.data["model_provider"], "mock")
        self.assertEqual(response.data["history_messages_used"], 1)
        self.assertIn("上一轮问题", router.last_prompt)
        self.assertFalse(response.data["retrieval"]["attempted"])

    def test_chat_responder_injects_database_retrieval_context(self) -> None:
        class FakeRouter:
            def __init__(self) -> None:
                self.last_prompt = ""

            def generate_text(self, task_type, prompt, system_prompt=None, metadata=None):  # noqa: ANN001
                self.last_prompt = prompt
                return ModelResult(
                    ok=True,
                    text="上架 iPhone 应用要先准备账号、元数据和审核材料。[Source 1]",
                    model_provider="mock",
                    model_name="mock-model",
                )

        class FakeRetriever:
            def __init__(self) -> None:
                self.last_query = ""

            def retrieve(self, query, **kwargs):  # noqa: ANN001, ANN003
                self.last_query = query
                return RetrievalResult(
                    attempted=True,
                    ok=True,
                    status="ready",
                    can_answer=True,
                    query=query,
                    context_text="[Source 1] AI 生成 iPhone App 后如何准备上架 App Store\n需要准备开发者账号、App Store Connect 元数据、截图和隐私信息。",
                    context={"text": "context", "source_count": 1, "block_count": 1},
                    answerability={"can_answer": True},
                    confidence={"low_confidence": False},
                    sources=[{
                        "source_index": 1,
                        "citation_label": "[Source 1]",
                        "source_type": "card",
                        "source_id": "card_app_store",
                        "title": "AI 生成 iPhone App 后如何准备上架 App Store",
                        "path": "/知识卡/AI/工程化/质量门禁/iPhone_App上架准备流程",
                        "chunk_ids": ["chunk_1"],
                        "best_score": 1,
                    }],
                    citations=[{
                        "source_index": 1,
                        "citation_label": "[Source 1]",
                        "chunk_id": "chunk_1",
                        "title": "AI 生成 iPhone App 后如何准备上架 App Store",
                        "path": "/知识卡/AI/工程化/质量门禁/iPhone_App上架准备流程",
                        "text": "需要准备开发者账号、App Store Connect 元数据、截图和隐私信息。",
                        "score": 1,
                    }],
                )

        router = FakeRouter()
        retriever = FakeRetriever()
        response = ChatResponder(router=router, retriever=retriever).respond(
            "App Store Connect 上架 iPhone 应用需要准备什么",
            conversation_history=[{"role": "user", "text": "我想做一个 iPhone app"}],
        )

        self.assertTrue(response.ok)
        self.assertTrue(response.data["retrieval"]["attempted"])
        self.assertTrue(response.data["retrieval"]["can_answer"])
        self.assertEqual(response.data["retrieval"]["sources"][0]["title"], "AI 生成 iPhone App 后如何准备上架 App Store")
        self.assertIn("Lucas Database 检索结果", router.last_prompt)
        self.assertIn("[Source 1] AI 生成 iPhone App", router.last_prompt)
        self.assertIn("App Store Connect 上架", retriever.last_query)
        self.assertIn("[Source 1]", response.reply_text)

    def test_chat_responder_normalizes_short_topic_followup_for_retrieval(self) -> None:
        class FakeRouter:
            def __init__(self) -> None:
                self.last_prompt = ""

            def generate_text(self, task_type, prompt, system_prompt=None, metadata=None):  # noqa: ANN001
                self.last_prompt = prompt
                return ModelResult(
                    ok=True,
                    text="知识库里有软件工程相关来源。[Source 1]",
                    model_provider="mock",
                    model_name="mock-model",
                )

        class CapturingRetriever:
            def __init__(self) -> None:
                self.last_query = ""

            def retrieve(self, query, **kwargs):  # noqa: ANN001, ANN003
                self.last_query = query
                return RetrievalResult(
                    attempted=True,
                    ok=True,
                    status="ready",
                    can_answer=True,
                    query=query,
                    context_text="[Source 1] 软件工程\n软件工程相关知识卡。",
                    context={"text": "context", "source_count": 1, "block_count": 1},
                    answerability={"can_answer": True},
                    confidence={"low_confidence": False},
                    sources=[{
                        "source_index": 1,
                        "citation_label": "[Source 1]",
                        "source_type": "node",
                        "source_id": "node_software_engineering",
                        "title": "软件工程",
                        "path": "/知识卡/软件工程",
                        "chunk_ids": ["chunk_software_engineering"],
                        "best_score": 1,
                    }],
                    citations=[{
                        "source_index": 1,
                        "citation_label": "[Source 1]",
                        "chunk_id": "chunk_software_engineering",
                        "title": "软件工程",
                        "path": "/知识卡/软件工程",
                        "text": "软件工程相关知识卡。",
                        "score": 1,
                    }],
                )

        router = FakeRouter()
        retriever = CapturingRetriever()
        response = ChatResponder(router=router, retriever=retriever).respond(
            "软件工程呢",
            conversation_history=[
                {"role": "user", "text": "AI 写作呢"},
                {"role": "assistant", "text": "知识库主要有 AI 写作和前端动画开发。"},
            ],
        )

        self.assertTrue(response.ok)
        self.assertTrue(response.data["retrieval"]["attempted"])
        self.assertTrue(response.data["retrieval"]["can_answer"])
        self.assertEqual(retriever.last_query, "软件工程")
        self.assertEqual(response.data["retrieval"]["sources"][0]["title"], "软件工程")
        self.assertIn("不能回答“没有直接相关内容”", router.last_prompt)
        self.assertIn("[Source 1] 软件工程", router.last_prompt)

    def test_chat_responder_plans_topic_queries_across_user_actions(self) -> None:
        class FakeRouter:
            def __init__(self) -> None:
                self.prompts: list[str] = []

            def generate_text(self, task_type, prompt, system_prompt=None, metadata=None):  # noqa: ANN001
                self.prompts.append(prompt)
                return ModelResult(
                    ok=True,
                    text="已按核心主题回答。[Source 1]",
                    model_provider="mock",
                    model_name="mock-model",
                )

        class TopicAwareRetriever:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def retrieve(self, query, **kwargs):  # noqa: ANN001, ANN003
                self.queries.append(query)
                topic = str(query)
                return RetrievalResult(
                    attempted=True,
                    ok=True,
                    status="ready",
                    can_answer=True,
                    query=topic,
                    context_text=f"[Source 1] {topic}\n{topic} 主题内容。",
                    context={"text": "context", "source_count": 1, "block_count": 1},
                    answerability={"can_answer": True},
                    confidence={"low_confidence": False},
                    sources=[{
                        "source_index": 1,
                        "citation_label": "[Source 1]",
                        "source_type": "node",
                        "source_id": f"node_{topic}",
                        "title": topic,
                        "path": f"/知识卡/{topic}",
                        "chunk_ids": [f"chunk_{topic}"],
                        "best_score": 1,
                    }],
                    citations=[{
                        "source_index": 1,
                        "citation_label": "[Source 1]",
                        "chunk_id": f"chunk_{topic}",
                        "title": topic,
                        "path": f"/知识卡/{topic}",
                        "text": f"{topic} 主题内容。",
                        "score": 1,
                    }],
                )

        cases = [
            ("总结一下穿搭", "summarize", "穿搭"),
            ("讲讲软件工程", "explain", "软件工程"),
            ("有没有外贸从零到复盘", "search", "外贸从零到复盘"),
            ("AI编程呢", "lookup", "AI编程"),
            ("查一下 GSAP Skills", "search", "GSAP Skills"),
            ("整理一下 App Store Connect", "summarize", "App Store Connect"),
        ]
        router = FakeRouter()
        retriever = TopicAwareRetriever()
        responder = ChatResponder(router=router, retriever=retriever)
        for text, expected_action, expected_topic in cases:
            response = responder.respond(
                text,
                conversation_history=[
                    {"role": "user", "text": "MirrorFish 呢"},
                    {"role": "assistant", "text": "上一个主题是 AI 写作，不应污染下一条检索。"},
                ],
            )
            self.assertTrue(response.ok)
            self.assertEqual(response.data["retrieval_plan"]["action"], expected_action)
            self.assertEqual(response.data["retrieval_plan"]["topic"], expected_topic)
            self.assertEqual(response.data["retrieval_plan"]["query"], expected_topic)
            self.assertFalse(response.data["retrieval_plan"]["used_history"])
            self.assertTrue(response.data["retrieval"]["can_answer"])

        self.assertEqual(retriever.queries, [case[2] for case in cases])
        for prompt, (_, _, expected_topic) in zip(router.prompts, cases):
            self.assertIn(f"核心主题={expected_topic}", prompt)

    def test_chat_responder_blocks_topic_mismatch_instead_of_answering_weak_sources(self) -> None:
        class FakeRouter:
            def __init__(self) -> None:
                self.last_prompt = ""

            def generate_text(self, task_type, prompt, system_prompt=None, metadata=None):  # noqa: ANN001
                self.last_prompt = prompt
                return ModelResult(
                    ok=True,
                    text="没有找到穿搭的可靠命中；需要更具体线索。",
                    model_provider="mock",
                    model_name="mock-model",
                )

        class MismatchedRetriever:
            def retrieve(self, query, **kwargs):  # noqa: ANN001, ANN003
                return RetrievalResult(
                    attempted=True,
                    ok=True,
                    status="ready",
                    can_answer=True,
                    query=query,
                    context_text="[Source 1] GSAP Skills\n前端动画开发内容。",
                    context={"text": "context", "source_count": 1, "block_count": 1},
                    answerability={"can_answer": True},
                    confidence={"low_confidence": False},
                    sources=[{
                        "source_index": 1,
                        "citation_label": "[Source 1]",
                        "source_type": "node",
                        "source_id": "node_gsap",
                        "title": "GSAP Skills",
                        "path": "/知识卡/前端开发/GSAP Skills",
                        "chunk_ids": ["chunk_gsap"],
                        "best_score": 0.9,
                    }],
                    citations=[{
                        "source_index": 1,
                        "citation_label": "[Source 1]",
                        "chunk_id": "chunk_gsap",
                        "title": "GSAP Skills",
                        "path": "/知识卡/前端开发/GSAP Skills",
                        "text": "前端动画开发内容。",
                        "score": 0.9,
                    }],
                )

        router = FakeRouter()
        response = ChatResponder(router=router, retriever=MismatchedRetriever()).respond(
            "总结一下穿搭",
            conversation_history=[
                {"role": "assistant", "text": "可用知识集中在 AI 写作、GSAP Skills 和外贸。"},
            ],
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.data["retrieval_plan"]["query"], "穿搭")
        self.assertEqual(response.data["retrieval"]["status"], "topic_mismatch")
        self.assertFalse(response.data["retrieval"]["can_answer"])
        self.assertIn("topic_mismatch", response.data["retrieval"]["warnings"])
        self.assertIn("不要拿其它主题的来源凑答案", router.last_prompt)

    def test_chat_responder_uses_bge_friendly_retrieval_timeout(self) -> None:
        class FakeRouter:
            def generate_text(self, task_type, prompt, system_prompt=None, metadata=None):  # noqa: ANN001
                return ModelResult(
                    ok=True,
                    text="我会先查本地知识库，再基于可靠证据回答。",
                    model_provider="mock",
                    model_name="mock-model",
                )

        class CapturingRetriever:
            def __init__(self) -> None:
                self.last_kwargs = {}

            def retrieve(self, query, **kwargs):  # noqa: ANN001, ANN003
                self.last_kwargs = dict(kwargs)
                return RetrievalResult(
                    attempted=True,
                    ok=True,
                    status="low_confidence",
                    can_answer=False,
                    query=query,
                    answerability={"can_answer": False, "reason": "low_confidence"},
                    confidence={"low_confidence": True},
                )

        retriever = CapturingRetriever()
        with patch.dict(os.environ, {"LUCAS_CHAT_RAG_TIMEOUT_SEC": ""}, clear=False):
            response = ChatResponder(router=FakeRouter(), retriever=retriever).respond(
                "App Store Connect 上架 iPhone 应用需要准备什么",
                timeout_sec=60,
            )

        self.assertTrue(response.data["retrieval"]["attempted"])
        self.assertEqual(retriever.last_kwargs["timeout_sec"], 45)

        override_retriever = CapturingRetriever()
        with patch.dict(os.environ, {"LUCAS_CHAT_RAG_TIMEOUT_SEC": "90"}, clear=False):
            ChatResponder(router=FakeRouter(), retriever=override_retriever).respond(
                "App Store Connect 上架 iPhone 应用需要准备什么",
                timeout_sec=60,
            )

        self.assertEqual(override_retriever.last_kwargs["timeout_sec"], 90)

    def test_chat_responder_marks_low_confidence_retrieval(self) -> None:
        class FakeRouter:
            def __init__(self) -> None:
                self.last_prompt = ""

            def generate_text(self, task_type, prompt, system_prompt=None, metadata=None):  # noqa: ANN001
                self.last_prompt = prompt
                return ModelResult(
                    ok=True,
                    text="我没有从本地知识库找到可靠命中；你可以补充更具体的关键词。",
                    model_provider="mock",
                    model_name="mock-model",
                )

        class LowConfidenceRetriever:
            def retrieve(self, query, **kwargs):  # noqa: ANN001, ANN003
                return RetrievalResult(
                    attempted=True,
                    ok=True,
                    status="low_confidence",
                    can_answer=False,
                    query=query,
                    context_text="",
                    answerability={"can_answer": False, "reason": "low_confidence"},
                    confidence={"low_confidence": True, "reason": "supporting_signal_low_confidence"},
                    warnings=["low_confidence", "empty_context"],
                )

        router = FakeRouter()
        response = ChatResponder(router=router, retriever=LowConfidenceRetriever()).respond(
            "之前有没有火星土豆水循环温室系统资料"
        )

        self.assertTrue(response.ok)
        self.assertTrue(response.data["retrieval"]["attempted"])
        self.assertFalse(response.data["retrieval"]["can_answer"])
        self.assertEqual(response.data["retrieval"]["status"], "low_confidence")
        self.assertIn("未找到可靠命中", router.last_prompt)
        self.assertIn("不要声称已经从数据库查到了答案", router.last_prompt)

    def test_chat_responder_storage_question_uses_config_without_model_guessing(self) -> None:
        class ExplodingRouter:
            def generate_text(self, *args, **kwargs):  # noqa: ANN002, ANN003
                raise AssertionError("storage config questions must not call the model")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage_path = root / "storage.local.json"
            env_path = root / ".env"
            pipeline_path = root / "link_pipeline.json"
            storage_path.write_text(json.dumps({
                "active_provider": "lucas_database",
                "providers": {
                    "lucas_database": {"base_url": "http://127.0.0.1:8765", "endpoint": "/api/cards/ingest"},
                    "siyuan": {"base_url": "http://127.0.0.1:6806", "endpoint": "/api/filetree/createDocWithMd"},
                },
            }, ensure_ascii=False), encoding="utf-8")
            env_path.write_text("LUCAS_DB_API_KEY=db-key\nSIYUAN_TOKEN=siyuan-key\n", encoding="utf-8")
            pipeline_path.write_text(json.dumps({
                "storage_targets": ["siyuan", "lucas_database"],
                "lucas_database_write_policy": "all_cards",
            }, ensure_ascii=False), encoding="utf-8")

            with patch.dict(os.environ, {
                "LUCAS_STORAGE_CONFIG_PATH": str(storage_path),
                "LUCAS_STORAGE_ENV_PATH": str(env_path),
                "LUCAS_LINK_PIPELINE_CONFIG_PATH": str(pipeline_path),
            }, clear=False):
                response = ChatResponder(router=ExplodingRouter()).respond("我现在可以写入的知识库有哪些")

        self.assertTrue(response.ok)
        self.assertEqual(response.intent, "storage_config_query")
        self.assertFalse(response.data["model_called"])
        self.assertIn("SiYuan", response.reply_text)
        self.assertIn("AntTrail Database", response.reply_text)
        self.assertIn("现在实际可写是", response.reply_text)
        self.assertLessEqual(len(response.reply_text.splitlines()), 3)
        self.assertNotIn("Endpoint", response.reply_text)
        self.assertNotIn("通常", response.reply_text)
        self.assertEqual(
            {item["target_id"] for item in response.data["storage"]["writable_targets"]},
            {"siyuan", "lucas_database"},
        )

    def test_chat_responder_storage_connection_question_reads_config(self) -> None:
        class ExplodingRouter:
            def generate_text(self, *args, **kwargs):  # noqa: ANN002, ANN003
                raise AssertionError("storage connection questions must not call the model")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage_path = root / "storage.local.json"
            env_path = root / ".env"
            pipeline_path = root / "link_pipeline.json"
            storage_path.write_text(json.dumps({
                "active_provider": "lucas_database",
                "providers": {
                    "lucas_database": {"base_url": "http://127.0.0.1:8765", "endpoint": "/api/cards/ingest"},
                    "siyuan": {"base_url": "http://127.0.0.1:6806", "endpoint": "/api/filetree/createDocWithMd"},
                },
            }, ensure_ascii=False), encoding="utf-8")
            env_path.write_text("SIYUAN_TOKEN=siyuan-key\n", encoding="utf-8")
            pipeline_path.write_text(json.dumps({
                "storage_targets": ["siyuan", "lucas_database"],
            }, ensure_ascii=False), encoding="utf-8")

            with patch.dict(os.environ, {
                "LUCAS_STORAGE_CONFIG_PATH": str(storage_path),
                "LUCAS_STORAGE_ENV_PATH": str(env_path),
                "LUCAS_LINK_PIPELINE_CONFIG_PATH": str(pipeline_path),
                "LUCAS_DB_API_KEY": "",
            }, clear=False):
                response = ChatResponder(router=ExplodingRouter()).respond("我有哪些知识库接入")
                detail_response = ChatResponder(router=ExplodingRouter()).respond("展开配置")

        self.assertTrue(response.ok)
        self.assertEqual(response.intent, "storage_config_query")
        self.assertFalse(response.data["model_called"])
        self.assertIn("现在实际可写是 SiYuan", response.reply_text)
        self.assertIn("AntTrail Database", response.reply_text)
        self.assertNotIn("通常", response.reply_text)
        self.assertIn("Endpoint /api/cards/ingest", detail_response.reply_text)
        self.assertIn("缺少 LUCAS_DB_API_KEY", detail_response.reply_text)

    def test_chat_responder_status_followup_is_short_and_does_not_guess(self) -> None:
        class ExplodingRouter:
            def generate_text(self, *args, **kwargs):  # noqa: ANN002, ANN003
                raise AssertionError("status follow-up without job facts must not call the model")

        response = ChatResponder(router=ExplodingRouter()).respond(
            "现在好了吗",
            conversation_history=[{"role": "user", "text": "我有哪些知识库接入"}],
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.intent, "status_followup")
        self.assertFalse(response.data["model_called"])
        self.assertLessEqual(len(response.reply_text.splitlines()), 2)
        self.assertNotIn("正在查询", response.reply_text)
        self.assertNotIn("建议下一步", response.reply_text)
        self.assertNotIn("网络连接已经恢复", response.reply_text)

    def test_chat_responder_ai_config_question_uses_saved_model_config(self) -> None:
        class ExplodingRouter:
            def generate_text(self, *args, **kwargs):  # noqa: ANN002, ANN003
                raise AssertionError("AI config questions must not call the model")

        os.environ.pop("AI_LAYER_PROVIDER", None)
        save_ai_config({
            "provider_id": "openai_compatible",
            "base_url": "https://relay.example.com/v1",
            "model": "custom-chat-model",
            "api_key": "ai-secret-7788",
        })

        response = ChatResponder(router=ExplodingRouter()).respond("现在用的是哪个模型提供商")

        self.assertTrue(response.ok)
        self.assertEqual(response.intent, "ai_config_query")
        self.assertFalse(response.data["model_called"])
        self.assertIn("自定义 OpenAI 兼容中转站", response.reply_text)
        self.assertIn("custom-chat-model", response.reply_text)
        self.assertNotIn("ai-secret-7788", response.reply_text)

    def test_chat_responder_note_revision_request_uses_model_reply(self) -> None:
        class FakeRouter:
            def generate_text(self, task_type, prompt, system_prompt=None, metadata=None):  # noqa: ANN001
                self.last_prompt = prompt
                return ModelResult(
                    ok=True,
                    text="可以，我会把这条意见理解成删除不准确的获客成功率表述，并先整理成一版修订草稿。",
                    model_provider="mock",
                    model_name="mock-model",
                )

        router = FakeRouter()
        response = ChatResponder(router=router).respond(
            "我需要调整笔记",
            conversation_history=[
                {
                    "role": "assistant",
                    "text": "入库卡片：\n标题：高短调影调：AI视频中的亮浅柔美学\n文件树：/知识卡/AI/内容生产\n路径：/��/AI/坏路径.md",
                },
            ],
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.intent, "note_revision_request")
        self.assertTrue(response.data["model_called"])
        self.assertFalse(response.data["fallback_reply_used"])
        self.assertTrue(response.data["revision_request"])
        self.assertEqual(response.data["candidate_title"], "高短调影调：AI视频中的亮浅柔美学")
        self.assertIn("修订草稿", response.reply_text)
        self.assertIn("候选笔记标题：高短调影调：AI视频中的亮浅柔美学", router.last_prompt)
        self.assertNotIn("路径：", response.reply_text)
        self.assertNotIn("文件树：", response.reply_text)
        self.assertNotIn("�", response.reply_text)

    def test_chat_responder_revision_followup_uses_recent_card_context(self) -> None:
        class FakeRouter:
            def generate_text(self, task_type, prompt, system_prompt=None, metadata=None):  # noqa: ANN001
                return ModelResult(
                    ok=True,
                    text="好，我会把这条卡片里的不准确营销话术删掉，保留更稳的业务价值表述。",
                    model_provider="mock",
                    model_name="mock-model",
                )

        response = ChatResponder(router=FakeRouter()).respond(
            "你真的可以重新提交吗，如果可以帮我修改Crow5复刻Claude璀璨星动画的就好",
            conversation_history=[
                {
                    "role": "assistant",
                    "text": "入库卡片：\n标题：Crow5复刻Claude璀璨星动画\n一句话摘要：这是一个动画复刻案例。",
                },
            ],
        )

        self.assertTrue(response.ok)
        self.assertEqual(response.intent, "note_revision_request")
        self.assertTrue(response.data["model_called"])
        self.assertEqual(response.data["candidate_title"], "Crow5复刻Claude璀璨星动画")
        self.assertIn("业务价值表述", response.reply_text)
        self.assertIn("重新提交", response.data["current_request"])

    def test_chat_responder_revision_model_refusal_uses_short_fallback(self) -> None:
        class RefusalRouter:
            def generate_text(self, task_type, prompt, system_prompt=None, metadata=None):  # noqa: ANN001
                return ModelResult(
                    ok=True,
                    text="我确实不能写入知识库，也不能调用后端接口。请你手动编辑。",
                    model_provider="mock",
                    model_name="mock-model",
                )

        response = ChatResponder(router=RefusalRouter()).respond(
            "帮我修改Crow5复刻Claude璀璨星动画的就好",
            conversation_history=[
                {"role": "assistant", "text": "入库卡片：\n标题：Crow5复刻Claude璀璨星动画"},
            ],
        )

        self.assertTrue(response.data["fallback_reply_used"])
        self.assertIn("受控修订", response.reply_text)
        self.assertNotIn("不能调用后端", response.reply_text)

    def test_chat_responder_reports_model_config_failure_without_key(self) -> None:
        response = ChatResponder().respond("你好", timeout_sec=1)

        self.assertFalse(response.ok)
        self.assertEqual(response.intent, "normal_chat")
        self.assertEqual(response.error, "api_key_missing")
        self.assertIn("模型配置", response.reply_text)
        self.assertTrue(response.data["model_called"])

    def test_model_router_failure_is_structured(self) -> None:
        class BrokenRouter(ModelRouter):
            def get_provider(self, task_type: str):  # type: ignore[override]
                raise RuntimeError("boom")

        result = BrokenRouter().generate_text("chat_response", "hi")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "boom")

    def test_provider_config_masks_key_and_autofills_provider_defaults(self) -> None:
        os.environ.pop("AI_LAYER_PROVIDER", None)
        saved = save_ai_config({
            "provider_id": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "deepseek/deepseek-chat",
            "api_key": "sk-test-123456",
        })
        provider = saved["provider"]
        self.assertEqual(saved["active_provider"], "openrouter")
        self.assertTrue(provider["api_key_present"])
        self.assertEqual(provider["api_key_last4"], "3456")
        self.assertNotIn("sk-test-123456", json.dumps(saved, ensure_ascii=False))
        self.assertTrue(any(item["id"] == "gemini_openai" for item in saved["presets"]))

    def test_build_provider_uses_saved_openai_compatible_config(self) -> None:
        os.environ.pop("AI_LAYER_PROVIDER", None)
        save_ai_config({
            "provider_id": "siliconflow",
            "base_url": "https://api.siliconflow.cn/v1",
            "model": "deepseek-ai/DeepSeek-V3",
            "api_key": "sf-key-abcdef",
        })
        provider = build_provider()
        self.assertEqual(provider.name, "siliconflow")
        self.assertEqual(provider.base_url, "https://api.siliconflow.cn/v1")
        self.assertEqual(provider.model_name, "deepseek-ai/DeepSeek-V3")

    def test_openai_compatible_http_error_keeps_sanitized_detail(self) -> None:
        provider = OpenAICompatibleProvider(
            provider_name="openai_compatible",
            api_key="secret",
            base_url="https://relay.example/v1",
            model_name="demo-model",
        )
        body = b'{"error":{"message":"upstream timeout for sk-1234567890 using Bearer abcdefghi"}}'
        error = urllib.error.HTTPError(
            url="https://relay.example/v1/chat/completions",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=BytesIO(body),
        )

        with patch("urllib.request.urlopen", side_effect=error):
            result = provider.generate_json("hello", metadata={"timeout_sec": 1})

        self.assertFalse(result.ok)
        self.assertIn("http_error_500", result.error)
        self.assertIn("upstream timeout", result.error)
        self.assertNotIn("sk-1234567890", result.error)
        self.assertNotIn("abcdefghi", result.error)

    def test_public_ai_config_defaults_to_deepseek_without_mock(self) -> None:
        os.environ.pop("AI_LAYER_PROVIDER", None)
        config = get_public_ai_config()
        self.assertEqual(config["active_provider"], "deepseek_compatible")
        self.assertEqual(config["provider"]["base_url"], "https://api.deepseek.com")
        self.assertFalse(config["provider"]["api_key_present"])

    def test_compose_card_with_real_provider_path_is_structured_failure_without_key(self) -> None:
        input_payload = {
            "source": {"source_url": "https://example.com", "final_url": "https://example.com", "source_title": "demo", "author": "a", "publish_time": "b", "video_id": "c", "job_id": "job1"},
            "transcript": {"raw_transcript": "hello", "has_speech": False, "status": "ok", "confidence": 1.0},
            "comments": {"comments_hash": "abc", "comments_count": 0, "comment_source": "none"},
            "ocr": {"ocr_status": "not_run"},
        }
        card, meta = compose_card(input_payload, timeout_sec=5)
        self.assertIsNone(card)
        self.assertFalse(meta["ok"])
        self.assertEqual(meta["model_provider"], "deepseek_compatible")

    def test_card_composer_retries_model_until_success(self) -> None:
        input_payload = {
            "source": {"source_url": "https://example.com", "source_title": "demo"},
            "transcript": {"raw_transcript": "hello"},
            "comments": {"comments_hash": "abc"},
            "ocr": {"ocr_status": "not_run"},
        }

        with patch("tools.card_composer.compose_card") as compose_mock:
            compose_mock.side_effect = [
                (None, {"model_provider": "openai_compatible", "model_name": "gpt-5.5", "error": "http_error_500: unexpected EOF"}),
                (None, {"model_provider": "openai_compatible", "model_name": "gpt-5.5", "error": "http_error_500: unexpected EOF"}),
                ({"display_title": "ok"}, {"model_provider": "openai_compatible", "model_name": "gpt-5.5", "error": ""}),
            ]

            card, meta = call_model(input_payload, timeout_sec=5, max_attempts=3)

        self.assertEqual(card, {"display_title": "ok"})
        self.assertEqual(compose_mock.call_count, 3)
        self.assertEqual(meta["attempt_count"], 3)
        self.assertEqual(meta["max_attempts"], 3)
        self.assertEqual(len(meta["attempts"]), 3)
        self.assertEqual(meta["provider"], "openai_compatible")
        self.assertEqual(meta["model"], "gpt-5.5")

    def test_card_composer_returns_last_error_after_all_retries_fail(self) -> None:
        input_payload = {
            "source": {"source_url": "https://example.com", "source_title": "demo"},
            "transcript": {"raw_transcript": "hello"},
            "comments": {"comments_hash": "abc"},
            "ocr": {"ocr_status": "not_run"},
        }

        with patch("tools.card_composer.compose_card") as compose_mock:
            compose_mock.side_effect = [
                (None, {"model_provider": "openai_compatible", "model_name": "gpt-5.5", "error": "first failure"}),
                (None, {"model_provider": "openai_compatible", "model_name": "gpt-5.5", "error": "second failure"}),
            ]

            card, meta = call_model(input_payload, timeout_sec=5, max_attempts=2)

        self.assertIsNone(card)
        self.assertEqual(compose_mock.call_count, 2)
        self.assertEqual(meta["attempt_count"], 2)
        self.assertEqual(meta["error"], "second failure")
        self.assertEqual([item["error"] for item in meta["attempts"]], ["first failure", "second failure"])

    def test_composed_card_v1_normalize_sets_schema_fields(self) -> None:
        input_payload = {
            "source": {"source_url": "https://example.com", "final_url": "https://example.com", "source_title": "demo", "author": "a", "publish_time": "b", "video_id": "c", "job_id": "job1"},
            "transcript": {"raw_transcript": "hello world sample transcript", "has_speech": True, "status": "ok", "confidence": 1.0},
            "comments": {"comments_hash": "abc", "comments_count": 3, "comment_source": "none"},
            "ocr": {"ocr_status": "not_run"},
        }
        raw_card = {
            "card_type": "formal_summary",
            "display_title": "2026-06-28_demo title",
            "safe_filename_title": "2026-06-28_demo title",
            "one_sentence_summary": "summary",
            "core_points": [
                "{'point': 'a', 'evidence_quotes': ['quote-a']}",
                "{'point': 'b', 'evidence_quotes': ['quote-b']}",
                "{'point': 'c', 'evidence_quotes': ['quote-c']}",
            ],
            "knowledge_blocks": [
                "{'point': 'a', 'evidence_quotes': ['quote-a']}",
                "{'point': 'b', 'evidence_quotes': ['quote-b']}",
            ],
            "methodology": ["m"],
            "reusable_value": ["r"],
            "application_suggestions": ["s"],
            "follow_up_actions": ["f"],
            "evidence_quotes": ["quote", "quote"],
        }
        card = normalize_composed_card_v1(raw_card, input_payload, model_meta={"model_name": "deepseek-chat", "model_provider": "deepseek_compatible"})
        self.assertEqual(card["schema_name"], "ComposedCardV1")
        self.assertEqual(card["schema_version"], "1")
        self.assertEqual(card["model_provider"], "deepseek_compatible")
        self.assertGreaterEqual(len(card["knowledge_blocks"]), 2)
        self.assertEqual(card["display_title"], "demo title")
        self.assertTrue(card["safe_filename_title"].startswith("2026-06-28_"))
        self.assertEqual(card["card_type"], "formal_summary")
        self.assertTrue(card["original_summary"])
        self.assertTrue(card["appendix_transcript_excerpt"])
        self.assertTrue(card["tags"])
        self.assertTrue(all(not str(item).startswith("{") for item in card["core_points"]))
        self.assertTrue(all(not str(block.get("concept")).startswith("{") for block in card["knowledge_blocks"]))
        self.assertGreaterEqual(len(card["evidence_quotes"]), 3)

    def test_composed_card_v1_clamps_long_model_excerpts(self) -> None:
        long_transcript = "这是一段被模型直接复制的原始转写内容，" * 20
        input_payload = {
            "source": {"source_url": "https://example.com", "final_url": "https://example.com", "source_title": "demo", "author": "a", "publish_time": "b", "video_id": "c", "job_id": "job1"},
            "transcript": {"raw_transcript": long_transcript, "has_speech": True, "status": "ok", "confidence": 1.0},
            "comments": {"comments_hash": "abc", "comments_count": 0, "comment_source": "none"},
            "ocr": {"ocr_status": "not_run"},
        }
        raw_card = {
            "display_title": "demo title",
            "one_sentence_summary": "summary",
            "core_points": ["point one", "point two", "point three"],
            "knowledge_blocks": [
                {"concept": "concept one", "explanation": "explanation one", "evidence": "evidence one", "reusable_value": "把这个判断沉淀为复核清单。"},
                {"concept": "concept two", "explanation": "explanation two", "evidence": "evidence two", "reusable_value": "把这个观点转成小实验。"},
            ],
            "methodology": ["m"],
            "reusable_value": ["r"],
            "application_suggestions": ["s"],
            "follow_up_actions": ["f"],
            "evidence_quotes": [long_transcript],
            "appendix_transcript_excerpt": long_transcript,
        }

        card = normalize_composed_card_v1(raw_card, input_payload, model_meta={"model_name": "deepseek-chat", "model_provider": "deepseek_compatible"})

        self.assertLessEqual(len(card["appendix_transcript_excerpt"]), 123)
        self.assertTrue(card["appendix_transcript_excerpt"].endswith("..."))
        self.assertTrue(all(len(item) <= 123 for item in card["evidence_quotes"]))

    def test_composed_card_v1_derives_comment_signals_and_ocr_boundary(self) -> None:
        input_payload = {
            "source": {
                "source_url": "https://v.douyin.com/abc/",
                "final_url": "https://www.douyin.com/video/1",
                "source_title": "mirofish+Claude code写小说",
                "author": "未读取到",
                "publish_time": "未读取到",
                "video_id": "1",
                "job_id": "job1",
            },
            "transcript": {
                "raw_transcript": "我用MirrorFish正在做一个小说的专写，每个点是人物和情节，他们在自动对话，把很多东西往下推演，我用的是Cloud Code。",
                "has_speech": True,
                "status": "ok",
            },
            "comments": {
                "comments_hash": "hash1",
                "comments_count": 4,
                "comment_items": [
                    {"text": "放心，目前任何一个ai都无法独自完成一部百万字以上的小说，如果有人说可以，他唯一目的就是想卖课"},
                    {"text": "AI永远写不出好的小说。不管怎么训练都没用。"},
                    {"text": "这种的做做知识图谱还是可以看的，ai现在推演不出内容的，写小说出来的内容太拉了"},
                    {"text": "来，发来看看什么效果！"},
                ],
            },
            "ocr": {"ocr_status": "skipped_not_needed", "merged_text": "", "text_items": []},
        }
        raw_card = {
            "display_title": "用 MiroFish 与 Claude Code 推演小说创作的争议",
            "one_sentence_summary": "作者展示了用 MiroFish 和 Claude Code 做小说推演的尝试。",
            "core_points": ["AI 可以辅助情节推演。", "评论质疑长篇小说质量。", "工具更适合作为辅助。"],
            "knowledge_blocks": [
                {"concept": "AI 写作辅助", "explanation": "用于生成情节和对话。", "evidence": "人物和情节自动对话", "reusable_value": "把 AI 写作定位为辅助推演，而非无人值守成稿。"},
                {"concept": "评论边界", "explanation": "评论集中质疑质量。", "evidence": "AI永远写不出好的小说", "reusable_value": "把评论质疑作为工具评估的反向证据。"},
            ],
            "methodology": [],
            "comment_signals": {"comments_hash": "hash1"},
            "reusable_value": [],
            "risks": [],
        }
        card = normalize_composed_card_v1(raw_card, input_payload, model_meta={"model_name": "gpt-5.5", "model_provider": "openai_compatible"})
        self.assertTrue(card["methodology"])
        self.assertTrue(card["reusable_value"])
        self.assertTrue(card["comment_signals"]["doubts_or_objections"])
        self.assertTrue(card["comment_signals"]["implementation_barriers"])
        self.assertTrue(card["comment_signals"]["incremental_value"])
        self.assertTrue(any("OCR 按条件策略跳过" in item for item in card["risks"]))

    def test_composed_card_v1_regrounds_model_comment_signals_in_current_comments(self) -> None:
        input_payload = {
            "source": {
                "source_url": "https://v.douyin.com/abc/",
                "final_url": "https://www.douyin.com/video/1",
                "source_title": "干外贸从0到1我花了三个月",
                "author": "未读取到",
                "publish_time": "未读取到",
                "video_id": "1",
                "job_id": "job1",
            },
            "transcript": {
                "raw_transcript": "外贸从零到一，关键在赛道选择和后端转化承接。",
                "has_speech": True,
                "status": "ok",
            },
            "comments": {
                "comments_hash": "hash1",
                "comments_count": 4,
                "comment_items": [
                    {"text": "可以跟着学吗，比较迷茫。"},
                    {"text": "你只用TK获客吗"},
                    {"text": "做什么类目呢"},
                    {"text": "实实在在的经验经过踩坑过来的"},
                ],
            },
            "ocr": {"ocr_status": "ocr_done", "merged_text": "上个月做了差不多30万", "text_items": []},
        }
        raw_card = {
            "display_title": "外贸从零到一：赛道选择与转化承接",
            "one_sentence_summary": "这段材料讨论外贸从零到一的赛道和转化承接。",
            "core_points": ["赛道选择优先。", "后端转化承接关键。", "评论区关注学习路径。"],
            "knowledge_blocks": [
                {"concept": "赛道选择", "explanation": "赛道决定天花板。", "evidence": "最重要的是赛道。", "reusable_value": "把赛道筛选表作为前置判断工具。"},
                {"concept": "转化承接", "explanation": "获客之后要转化。", "evidence": "前端获客容易，一到转化不行。", "reusable_value": "把获客和转化拆成两张看板。"},
            ],
            "comment_signals": {
                "comments_hash": "hash1",
                "doubts_or_objections": ["能不能带带我"],
                "incremental_value": "评论补充了对 AI 长篇创作质量、重复循环和情感深度的反驳",
            },
        }

        card = normalize_composed_card_v1(raw_card, input_payload, model_meta={"model_name": "gpt-5.5", "model_provider": "openai_compatible"})

        signals = card["comment_signals"]
        self.assertIn("可以跟着学吗，比较迷茫。", signals["demand_or_resource_requests"])
        self.assertIn("你只用TK获客吗", signals["demand_or_resource_requests"])
        self.assertIn("做什么类目呢", signals["demand_or_resource_requests"])
        self.assertEqual(signals["doubts_or_objections"], [])
        self.assertNotIn("AI 长篇创作", signals["incremental_value"])
        self.assertIn("学习", signals["incremental_value"])

    def test_render_markdown_shows_original_summary_appendix_and_tags(self) -> None:
        input_payload = {
            "source": {
                "source_url": "https://v.douyin.com/abc/",
                "final_url": "https://www.douyin.com/video/1",
                "source_title": "MirrorFish + Claude Code 写小说",
                "author": "a",
                "publish_time": "b",
                "video_id": "1",
                "job_id": "job1",
            },
            "transcript": {"raw_transcript": "原始转写内容示例", "has_speech": True, "status": "ok", "confidence": 1.0},
            "comments": {"comments_hash": "abc", "comments_count": 1, "comment_source": "none"},
            "ocr": {"ocr_status": "not_run"},
        }
        card = {
            "display_title": "MirrorFish + Claude Code 写小说：AI 推演与评论反馈",
            "one_sentence_summary": "提炼后的总结",
            "original_summary": "原始摘要内容",
            "core_points": ["要点一", "要点二", "要点三"],
            "knowledge_blocks": [
                {"concept": "概念一", "explanation": "解释一", "evidence": "证据一", "reusable_value": "价值一"},
                {"concept": "概念二", "explanation": "解释二", "evidence": "证据二", "reusable_value": "价值二"},
            ],
            "application_suggestions": ["建议一"],
            "follow_up_actions": ["动作一"],
            "methodology": ["流程一"],
            "comment_signals": {"comments_hash": "abc"},
            "reusable_value": ["可复用价值一"],
            "risks": ["风险一"],
            "tags": [],
            "evidence_quotes": ["quote one"],
            "appendix_transcript_excerpt": "",
            "content_level": "Level 5 评论与互动增强级",
            "quality_level": "high",
            "model_provider": "deepseek_compatible",
            "model_used": "deepseek-chat",
        }
        markdown = render_markdown(card, input_payload)
        self.assertIn("## 原始摘要", markdown)
        self.assertIn("原始摘要内容", markdown)
        self.assertIn("## 原始材料摘录 / 附录", markdown)
        self.assertIn("未提供原始材料摘录", markdown)
        self.assertIn("## 标签", markdown)
        self.assertIn("[[知识卡]]", markdown)

    def test_resolve_written_title_prefers_display_title(self) -> None:
        title = resolve_written_title(
            "https://v.douyin.com/abc/",
            {"title": "source title"},
            {"display_title": "MirrorFish + Claude Code 写小说：AI 推演与评论反馈", "safe_filename_title": "2026-06-28_demo"},
            {"display_title": "fallback"},
        )
        self.assertEqual(title, "MirrorFish + Claude Code 写小说：AI 推演与评论反馈")

    def test_composed_card_v1_failure_card_has_required_fields(self) -> None:
        input_payload = {
            "source": {"source_url": "https://example.com", "source_title": "demo", "job_id": "job1"},
            "transcript": {"raw_transcript": "hello"},
            "comments": {"comments_hash": "abc"},
            "ocr": {"ocr_status": "not_run"},
        }
        card = build_failure_composed_card_v1(input_payload, model_meta={"model_name": "deepseek-chat", "model_provider": "deepseek_compatible"})
        self.assertEqual(card["schema_name"], "ComposedCardV1")
        self.assertEqual(card["card_type"], "temporary_review_card")
        self.assertEqual(card["composer_status"], "failed")

    def test_quality_gate_rejects_duplicate_and_empty_evidence(self) -> None:
        import tools.card_quality_gate as card_quality_gate
        import json
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "transcript.json").write_text(json.dumps({"transcript": "这是一个很长的转写文本，用于触发重复检测。"}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "content.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "comments.json").write_text(json.dumps({"comments_hash": "abc", "title": "demo"}, ensure_ascii=False), encoding="utf-8")
            card = {
                "schema_name": "ComposedCardV1",
                "schema_version": "1",
                "card_type": "formal_summary",
                "content_level": "Level 5 评论与互动增强级",
                "quality_level": "high",
                "source_title": "demo",
                "display_title": "2026-06-28_demo",
                "safe_filename_title": "2026-06-28_demo",
                "one_sentence_summary": "重复句子",
                "core_points": ["重复句子", "重复句子", "第三点"],
                "knowledge_blocks": [
                    {"concept": "重复句子", "explanation": "重复句子", "evidence": "", "reusable_value": "r"},
                    {"concept": "重复句子", "explanation": "重复句子", "evidence": "", "reusable_value": "r"},
                ],
                "methodology": [],
                "application_suggestions": ["建议一", "建议一"],
                "follow_up_actions": ["动作一", "动作一"],
                "comment_signals": {"comments_hash": "abc"},
                "reusable_value": ["r"],
                "risks": [],
                "tags": [],
                "evidence_quotes": [],
                "appendix_transcript_excerpt": "",
                "comments_job_id": "job1",
                "comments_video_id": "video1",
                "comments_hash": "abc",
                "composer_input_comments_hash": "abc",
                "composed_card_comments_hash": "abc",
                "composer_status": "success",
                "composer_error": "",
                "model_used": "deepseek-chat",
                "model_provider": "deepseek_compatible",
            }
            (job_dir / "composed_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
            (job_dir / "composed_card.md").write_text("# demo\n\n> 完全不同的转写文本，避免触发 transcript 重复。", encoding="utf-8")
            result = card_quality_gate.gate(job_dir, job_dir / "composed_card.json", job_dir / "composed_card.md")
            self.assertFalse(result["quality_gate_passed"])
            self.assertIn("evidence_quotes_missing", result["failed_checks"])
            self.assertIn("summary_repeats_core_point", result["failed_checks"])
            self.assertIn("core_points_duplicate_text", result["failed_checks"])
            self.assertIn("knowledge_blocks_duplicate_text", result["failed_checks"])
            self.assertIn("knowledge_block_reusable_value_missing_or_too_short", result["failed_checks"])

    def test_quality_gate_rejects_serialized_object_text(self) -> None:
        import tools.card_quality_gate as card_quality_gate
        import json
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "transcript.json").write_text(json.dumps({"transcript": "hello"}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "content.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "comments.json").write_text(json.dumps({"comments_hash": "abc", "title": "demo"}, ensure_ascii=False), encoding="utf-8")
            card = {
                "schema_name": "ComposedCardV1",
                "schema_version": "1",
                "card_type": "formal_summary",
                "content_level": "Level 5 评论与互动增强级",
                "quality_level": "high",
                "source_title": "demo",
                "display_title": "demo",
                "safe_filename_title": "demo",
                "one_sentence_summary": "summary",
                "core_points": ["{'point': 'a', 'evidence_quotes': ['x']}", "{'point': 'b', 'evidence_quotes': ['y']}", "{'point': 'c', 'evidence_quotes': ['z']}"],
                "knowledge_blocks": [
                    {"concept": "{'point': 'a', 'evidence_quotes': ['x']}", "explanation": "{'point': 'a'}", "evidence": "{'point': 'a'}", "reusable_value": "{'point': 'a'}"},
                    {"concept": "{'point': 'b', 'evidence_quotes': ['y']}", "explanation": "{'point': 'b'}", "evidence": "{'point': 'b'}", "reusable_value": "r"},
                ],
                "methodology": [],
                "application_suggestions": ["建议一"],
                "follow_up_actions": ["动作一"],
                "comment_signals": {"comments_hash": "abc"},
                "reusable_value": ["r"],
                "risks": [],
                "tags": [],
                "evidence_quotes": ["x", "y"],
                "appendix_transcript_excerpt": "",
                "comments_job_id": "job1",
                "comments_video_id": "video1",
                "comments_hash": "abc",
                "composer_input_comments_hash": "abc",
                "composed_card_comments_hash": "abc",
                "composer_status": "success",
                "composer_error": "",
                "model_used": "deepseek-chat",
                "model_provider": "deepseek_compatible",
            }
            (job_dir / "composed_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
            (job_dir / "composed_card.md").write_text("# demo", encoding="utf-8")
            result = card_quality_gate.gate(job_dir, job_dir / "composed_card.json", job_dir / "composed_card.md")
            self.assertFalse(result["quality_gate_passed"])
            self.assertIn("core_points_serialized_object_text", result["failed_checks"])
            self.assertIn("knowledge_blocks_serialized_object_text", result["failed_checks"])

    def test_quality_gate_rejects_reusable_value_that_repeats_explanation(self) -> None:
        import tools.card_quality_gate as card_quality_gate
        import hashlib
        import json
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "transcript.json").write_text(json.dumps({"transcript": "这是一段用于测试门禁的转写内容。"}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "content.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "comments.json").write_text(json.dumps({"comments": [{"text": "comment"}], "title": "demo"}, ensure_ascii=False), encoding="utf-8")
            comments_hash = hashlib.sha256((job_dir / "comments.json").read_bytes()).hexdigest()
            card = {
                "schema_name": "ComposedCardV1",
                "schema_version": "1",
                "card_type": "formal_summary",
                "content_level": "Level 5 评论与互动增强级",
                "quality_level": "high",
                "source_title": "demo",
                "display_title": "demo",
                "safe_filename_title": "demo",
                "one_sentence_summary": "summary one",
                "core_points": ["point one", "point two", "point three"],
                "knowledge_blocks": [
                    {
                        "concept": "concept one",
                        "explanation": "AI在长篇内容中容易重复情节和对话，导致故事缺乏进展。",
                        "evidence": "evidence one",
                        "reusable_value": "AI在长篇内容中容易重复情节和对话，导致故事缺乏进展。",
                    },
                    {
                        "concept": "concept two",
                        "explanation": "human writers add emotional judgment",
                        "evidence": "evidence two",
                        "reusable_value": "用于评估AI写作工具时，把情绪判断和逻辑连贯性列为人工复核清单。",
                    },
                ],
                "methodology": ["m1"],
                "application_suggestions": ["建议一"],
                "follow_up_actions": ["动作一"],
                "comment_signals": {"comments_hash": comments_hash},
                "reusable_value": ["r"],
                "risks": [],
                "tags": [],
                "evidence_quotes": ["quote one", "quote two"],
                "appendix_transcript_excerpt": "",
                "comments_job_id": "job1",
                "comments_video_id": "video1",
                "comments_hash": comments_hash,
                "composer_input_comments_hash": comments_hash,
                "composed_card_comments_hash": comments_hash,
                "composer_status": "success",
                "composer_error": "",
                "model_used": "deepseek-chat",
                "model_provider": "deepseek_compatible",
            }
            (job_dir / "composed_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
            (job_dir / "composed_card.md").write_text("# demo\n\n- point one\n- point two\n- point three", encoding="utf-8")
            result = card_quality_gate.gate(job_dir, job_dir / "composed_card.json", job_dir / "composed_card.md")
            self.assertFalse(result["quality_gate_passed"])
            self.assertIn("knowledge_block_reusable_value_repeats_source", result["failed_checks"])

    def test_quality_gate_ignores_long_transcript_only_in_appendix(self) -> None:
        import tools.card_quality_gate as card_quality_gate
        import hashlib
        import json
        from pathlib import Path
        import tempfile

        long_transcript = "这是原始转写的一部分，用来确认附录里的长摘录不会误伤正式卡。" * 12
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "transcript.json").write_text(json.dumps({"transcript": long_transcript}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "content.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "comments.json").write_text(json.dumps({"comments": [{"text": "comment"}], "title": "demo"}, ensure_ascii=False), encoding="utf-8")
            comments_hash = hashlib.sha256((job_dir / "comments.json").read_bytes()).hexdigest()
            card = {
                "schema_name": "ComposedCardV1",
                "schema_version": "1",
                "card_type": "formal_summary",
                "content_level": "Level 5 评论与互动增强级",
                "quality_level": "high",
                "source_title": "demo",
                "display_title": "demo",
                "safe_filename_title": "demo",
                "one_sentence_summary": "summary one",
                "core_points": ["point one", "point two", "point three"],
                "knowledge_blocks": [
                    {"concept": "concept one", "explanation": "explanation one", "evidence": "evidence one", "reusable_value": "把这个判断沉淀为内容入库时的复核清单。"},
                    {"concept": "concept two", "explanation": "explanation two", "evidence": "evidence two", "reusable_value": "用于设计后续行动时，把该观点转成可测试的小实验。"},
                ],
                "methodology": ["m1"],
                "application_suggestions": ["建议一"],
                "follow_up_actions": ["动作一"],
                "comment_signals": {"comments_hash": comments_hash, "incremental_value": "评论提供了弱反馈。"},
                "reusable_value": ["r"],
                "risks": [],
                "tags": [],
                "evidence_quotes": ["quote one", "quote two"],
                "appendix_transcript_excerpt": long_transcript,
                "comments_job_id": "job1",
                "comments_video_id": "video1",
                "comments_hash": comments_hash,
                "composer_input_comments_hash": comments_hash,
                "composed_card_comments_hash": comments_hash,
                "composer_status": "success",
                "composer_error": "",
                "model_used": "deepseek-chat",
                "model_provider": "deepseek_compatible",
            }
            (job_dir / "composed_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
            (job_dir / "composed_card.md").write_text(
                f"# demo\n\n## 一句话总结\n\nsummary one\n\n## 原始材料摘录 / 附录\n\n> {long_transcript}\n\n## 标签\n\n[[demo]]",
                encoding="utf-8",
            )
            result = card_quality_gate.gate(job_dir, job_dir / "composed_card.json", job_dir / "composed_card.md")
            self.assertTrue(result["quality_gate_passed"])
            self.assertIn("large_transcript_repeated_in_appendix_ignored", result["warnings"])

            (job_dir / "composed_card.md").write_text(
                f"# demo\n\n## 一句话总结\n\n{long_transcript}\n\n## 标签\n\n[[demo]]",
                encoding="utf-8",
            )
            result = card_quality_gate.gate(job_dir, job_dir / "composed_card.json", job_dir / "composed_card.md")
            self.assertFalse(result["quality_gate_passed"])
            self.assertIn("large_transcript_repeated_in_markdown", result["failed_checks"])

    def test_quality_gate_passes_with_formal_summary_type(self) -> None:
        import tools.card_quality_gate as card_quality_gate
        import json
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "transcript.json").write_text(json.dumps({"transcript": "这是一段足够长的转写内容，用于通过质量门禁。"}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "content.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "comments.json").write_text(json.dumps({"comments_hash": "abc", "title": "demo"}, ensure_ascii=False), encoding="utf-8")
            import hashlib
            comments_hash = hashlib.sha256((job_dir / "comments.json").read_bytes()).hexdigest()
            card = {
                "schema_name": "ComposedCardV1",
                "schema_version": "1",
                "card_type": "formal_summary",
                "content_level": "Level 5 评论与互动增强级",
                "quality_level": "high",
                "source_title": "demo",
                "display_title": "demo",
                "safe_filename_title": "demo",
                "one_sentence_summary": "summary one",
                "core_points": ["point one", "point two", "point three"],
                "knowledge_blocks": [
                    {"concept": "concept one", "explanation": "explanation one", "evidence": "evidence one", "reusable_value": "把这个判断沉淀为内容入库时的复核清单，避免只保存表层结论。"},
                    {"concept": "concept two", "explanation": "explanation two", "evidence": "evidence two", "reusable_value": "用于设计后续行动时，把该观点转成可测试的小实验。"},
                ],
                "methodology": ["m1"],
                "application_suggestions": ["建议一"],
                "follow_up_actions": ["动作一"],
                "comment_signals": {"comments_hash": comments_hash},
                "reusable_value": ["r"],
                "risks": [],
                "tags": [],
                "evidence_quotes": ["quote one", "quote two"],
                "appendix_transcript_excerpt": "",
                "comments_job_id": "job1",
                "comments_video_id": "video1",
                "comments_hash": comments_hash,
                "composer_input_comments_hash": comments_hash,
                "composed_card_comments_hash": comments_hash,
                "composer_status": "success",
                "composer_error": "",
                "model_used": "deepseek-chat",
                "model_provider": "deepseek_compatible",
            }
            (job_dir / "composed_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
            (job_dir / "composed_card.md").write_text("# demo\n\n- point one\n- point two\n- point three", encoding="utf-8")
            result = card_quality_gate.gate(job_dir, job_dir / "composed_card.json", job_dir / "composed_card.md")
            self.assertTrue(result["quality_gate_passed"])
            self.assertEqual(result["final_card_type"], "formal_summary")

    def test_quality_gate_ignores_domain_only_sibling_titles(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        import tools.card_quality_gate as card_quality_gate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            domain_sibling = root / "domain-sibling"
            real_sibling = root / "real-sibling"
            current.mkdir()
            domain_sibling.mkdir()
            real_sibling.mkdir()
            (domain_sibling / "comments.json").write_text(
                json.dumps({"title": "www.douyin.com"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (real_sibling / "comments.json").write_text(
                json.dumps({"title": "一个真实的相机工具推荐标题"}, ensure_ascii=False),
                encoding="utf-8",
            )

            titles = card_quality_gate.sibling_titles(current)

        self.assertIn("一个真实的相机工具推荐标题", titles)
        self.assertNotIn("www.douyin.com", titles)


if __name__ == "__main__":
    unittest.main()
