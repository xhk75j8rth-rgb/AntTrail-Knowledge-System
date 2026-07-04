from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from taxonomy_layer import TaxonomyRouter
from tools.run_link_job import route_taxonomy_for_formal_card


def make_card(**overrides):
    card = {
        "schema_name": "ComposedCardV1",
        "display_title": "",
        "source_title": "",
        "one_sentence_summary": "",
        "core_points": [],
        "knowledge_blocks": [],
        "tags": [],
        "reusable_value": [],
    }
    card.update(overrides)
    return card


class TaxonomyRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = TaxonomyRouter()

    def test_agentos_memory_article_routes_to_agent_memory(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="提高 AgentOS 记忆能力的实践",
            one_sentence_summary="文章讨论如何让个人 AgentOS 具备长期记忆和用户偏好沉淀能力。",
            core_points=["Agent 需要把用户记忆、项目记忆和上下文摘要分层保存。"],
            tags=["AgentOS", "记忆系统", "AI Agent"],
        ))

        self.assertEqual(decision["schema_name"], "TaxonomyDecisionV1")
        self.assertEqual(decision["recommended_path"], ["AI", "Agent", "记忆系统"])
        self.assertEqual(decision["path_id"], "ai/agent/memory")
        self.assertEqual(decision["confidence"], "high")
        self.assertTrue(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])

    def test_ai_novel_writing_routes_to_ai_writing(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="AI 写小说工作流复盘",
            one_sentence_summary="作者用 AI 辅助小说人物设定、情节推演和文案生成。",
            core_points=["AI 写作适合辅助网文设定，但长篇质量仍需要人工复核。"],
            tags=["AI写作", "小说", "内容创作"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "内容生产", "AI 写作"])
        self.assertTrue(decision["matched_existing_category"])

    def test_vector_database_article_routes_to_vector_retrieval(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="向量数据库与 RAG 检索实践",
            one_sentence_summary="内容讲解 embedding、向量检索和语义召回如何服务个人知识库。",
            knowledge_blocks=[
                {"concept": "向量检索", "explanation": "用 embedding 做相似度检索。"},
                {"concept": "RAG", "explanation": "把召回结果交给模型生成回答。"},
            ],
            tags=["向量数据库", "RAG", "知识库"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "知识库", "向量检索"])
        self.assertEqual(decision["path_id"], "ai/knowledge-base/vector-retrieval")
        self.assertTrue(decision["matched_existing_category"])

    def test_unclear_card_routes_to_inbox(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="随手记录",
            one_sentence_summary="今天看到一个值得以后再想想的东西。",
            core_points=["暂时没有明确主题。"],
            tags=[],
        ))

        self.assertEqual(decision["recommended_path"], ["Inbox", "待分类"])
        self.assertEqual(decision["path_id"], "inbox/unclassified")
        self.assertEqual(decision["confidence"], "low")
        self.assertFalse(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])

    def test_similar_existing_category_is_recommended_without_creation(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="Function calling 与外部 API 编排",
            one_sentence_summary="文章讨论 Agent 如何选择工具、调用 API，并把工具返回结果写回任务状态。",
            core_points=["工具调用链需要 schema、权限和错误恢复策略。"],
            tags=["function calling", "API 调用", "Agent"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "Agent", "工具调用"])
        self.assertTrue(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])
        self.assertTrue(decision["similar_categories"])

    def test_paper_sample_1_agentos_long_memory(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="如何提高 AgentOS 的长期记忆能力",
            one_sentence_summary="文章讨论 Agent 在长任务中如何保留上下文、形成长期记忆、避免每次任务重新开始。",
            tags=["AgentOS", "长期记忆", "上下文", "知识库", "任务状态"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "Agent", "记忆系统"])
        self.assertEqual(decision["confidence"], "high")
        self.assertTrue(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])
        self.assertTrue(any(item["path"] == ["AI", "Agent", "上下文管理"] for item in decision["similar_categories"]))

    def test_paper_sample_2_mirrorfish_claude_code_novel(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="用 MirrorFish + Claude Code 写小说",
            one_sentence_summary="内容展示如何用 AI 工具辅助小说人物设定、剧情推演和对话生成。",
            tags=["AI 写作", "Claude Code", "小说创作", "内容生产"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "内容生产", "AI 写作"])
        self.assertTrue(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])
        self.assertTrue(any(item["path"] == ["AI", "模型与 Provider", "Claude"] for item in decision["similar_categories"]))

    def test_paper_sample_3_vector_database_vs_sql(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="向量数据库和普通数据库有什么区别",
            one_sentence_summary="解释向量检索、相似度搜索、embedding、RAG 与传统 SQL 查询的差异。",
            tags=["向量数据库", "embedding", "RAG", "知识库"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "知识库", "向量检索"])
        self.assertEqual(decision["confidence"], "high")
        self.assertFalse(decision["should_create_category"])

    def test_paper_sample_4_siyuan_database_api_routes_to_database_adapters(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="如何把 SiYuan 替换成自己的数据库 API",
            one_sentence_summary="讨论 SiYuan 只是 Markdown sink，真正主数据应是 ComposedCardV1，未来需要 database sink。",
            tags=["SiYuan", "数据库适配", "Storage Layer", "ComposedCardV1"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "知识库", "数据库适配"])
        self.assertTrue(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])
        self.assertTrue(any(item["path"] == ["AI", "工程化", "存储层"] for item in decision["similar_categories"]))

    def test_paper_sample_5_mcp_python_service_deployment_boundary(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="Agent 使用 MCP 和 Python 脚本的部署边界",
            one_sentence_summary="讨论上线时 Python Service 应作为主链路，MCP 只作为 Agent 适配层。",
            tags=["MCP", "Python Service", "Agent 工具", "部署架构"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "工程化", "自动化流水线"])
        self.assertIn(decision["confidence"], {"medium", "high"})
        self.assertTrue(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])
        self.assertTrue(any(item["path"] == ["AI", "Agent", "工具调用"] for item in decision["similar_categories"]))

    def test_paper_sample_6_video_ocr_source_material_routes_to_structured_cards(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="抖音视频 OCR 如何保存关键画面文字",
            one_sentence_summary="讨论视频抽帧、OCRMaterialV1、画面文字证据、评论树和 source material 入库。",
            tags=["OCR", "视频抽帧", "source material", "知识证据"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "知识库", "结构化卡片"])
        self.assertIn(decision["confidence"], {"medium", "high"})
        self.assertTrue(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])

    def test_paper_sample_7_ecommerce_customer_service_goes_to_inbox_with_proposal(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="如何设计电商平台的客服自动化",
            one_sentence_summary="讨论商品咨询、订单状态、售后问题和客服工作流自动化。",
            tags=["电商", "客服", "工作流", "自动化"],
        ))

        self.assertEqual(decision["recommended_path"], ["Inbox", "待分类"])
        self.assertEqual(decision["confidence"], "low")
        self.assertFalse(decision["matched_existing_category"])
        self.assertTrue(decision["should_create_category"])
        self.assertEqual(decision["create_proposal"]["proposed_path"], ["AI", "行业应用", "客服自动化"])

    def test_paper_sample_8_fragment_goes_to_inbox_without_proposal(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="一个无法判断主题的碎片想法",
            one_sentence_summary="以后也许可以试试这个方法。",
            tags=[],
        ))

        self.assertEqual(decision["recommended_path"], ["Inbox", "待分类"])
        self.assertEqual(decision["confidence"], "low")
        self.assertFalse(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])
        self.assertEqual(decision["similar_categories"], [])

    def test_real_ai_coding_engineering_card_routes_to_quality_gate_with_pipeline_similarity(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="AI Coding 从代码生成走向稳定交付的工程挑战",
            one_sentence_summary="材料讨论 AI Coding 指标、Harness、代码质量、测试、架构规范和企业级稳定交付。",
            core_points=[
                "AI Coding 不能只看代码生成量，而要进入研发流程、代码审查、测试和稳定交付机制。",
                "Harness 是围绕具体工程场景的验证闭环，不只是某个 agent 框架。",
            ],
            tags=["AI Coding", "软件工程", "代码质量", "Harness", "工程化落地", "组织协作"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "工程化", "质量门禁"])
        self.assertIn(decision["confidence"], {"medium", "high"})
        self.assertTrue(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])
        self.assertTrue(any(item["path"] == ["AI", "工程化", "自动化流水线"] for item in decision["similar_categories"]))

    def test_gsap_skills_routes_to_frontend_animation_not_gsap_tag_fallback(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="GSAP Skills：官方动画知识包进入 AI 编程工作流",
            one_sentence_summary=(
                "材料介绍 GSAP 官方开源 gsap-skills，将核心 API、时间轴、ScrollTrigger、"
                "插件、React/Vue/Svelte 与性能用法整理成 AI Skills，使 Claude Code、Codex、"
                "Cursor 等工具更容易生成符合官方用法的动画代码。"
            ),
            core_points=[
                "GSAP Skills 的核心变化是把官方动画用法转化为 AI 编程代理可读取的技能文件。",
                "内容重点仍是前端动画、交互动效和 ScrollTrigger 等 GSAP 使用场景。",
            ],
            tags=["GSAP", "AI编程", "前端开发", "动画工程", "Agent Skills", "ScrollTrigger"],
        ))

        self.assertEqual(decision["recommended_path"], ["软件工程", "前端开发", "动画与交互"])
        self.assertEqual(decision["path_id"], "software-engineering/frontend-development/animation-interaction")
        self.assertEqual(decision["confidence"], "high")
        self.assertTrue(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])
        self.assertTrue(any(
            item["path"] == ["软件工程", "前端开发", "AI 辅助前端开发"]
            for item in decision["similar_categories"]
        ))

    def test_ai_frontend_workflow_routes_to_ai_assisted_frontend(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="用 Cursor 和 Claude Code 生成 React 页面组件",
            one_sentence_summary=(
                "内容讨论如何让 AI 编程工具根据设计稿生成 React 组件、CSS 布局和前端页面，"
                "并通过人工 review 修正响应式问题。"
            ),
            core_points=["这是一套面向前端页面和组件开发的 AI 辅助工作流。"],
            tags=["AI 辅助前端", "Cursor", "Claude Code", "React", "前端开发"],
        ))

        self.assertEqual(decision["recommended_path"], ["软件工程", "前端开发", "AI 辅助前端开发"])
        self.assertEqual(decision["path_id"], "software-engineering/frontend-development/ai-assisted-frontend-development")
        self.assertEqual(decision["confidence"], "high")
        self.assertTrue(decision["matched_existing_category"])
        self.assertFalse(decision["should_create_category"])
        self.assertNotEqual(decision["recommended_path"], ["AI", "工程化", "自动化流水线"])

    def test_configured_taxonomy_tree_prefers_real_existing_gsap_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree_path = Path(tmp) / "taxonomy_tree.json"
            tree_path.write_text(json.dumps({
                "schema_name": "TaxonomyTreeV1",
                "categories": [
                    {"path": ["知识卡", "AI编程"]},
                    {"path": ["知识卡", "前端开发"]},
                    {"path": ["知识卡", "GSAP"]},
                    {"path": ["知识卡", "Inbox", "待分类"]},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            router = TaxonomyRouter.from_config({"taxonomy_tree_path": str(tree_path)})

            decision = router.route_card(make_card(
                display_title="GSAP ScrollTrigger 时间轴动画实践",
                one_sentence_summary="材料整理 GSAP、ScrollTrigger、timeline 和前端动画 API 的使用方式。",
                tags=["GSAP", "ScrollTrigger"],
            ))

            self.assertEqual(decision["recommended_path"], ["GSAP"])
            self.assertEqual(decision["path_id"], "gsap")
            self.assertTrue(decision["matched_existing_category"])
            self.assertEqual(decision["taxonomy_source"], "file")
            self.assertEqual(Path(decision["taxonomy_source_path"]), tree_path.resolve())

    def test_lifestyle_fashion_auto_routes_to_lifestyle_not_ai_content_production(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree_path = Path(tmp) / "taxonomy_tree.json"
            tree_path.write_text(json.dumps({
                "schema_name": "TaxonomyTreeV1",
                "categories": [
                    {"path": ["知识卡", "AI", "内容生产", "穿搭"]},
                    {"path": ["知识卡", "Inbox", "待分类"]},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            router = TaxonomyRouter.from_config({
                "taxonomy_tree_path": str(tree_path),
                "taxonomy_auto_create_from_proposal": True,
            })

            decision = router.route_card(make_card(
                display_title="夏季通勤穿搭：小个子显高的衬衫和半裙搭配",
                one_sentence_summary="内容整理夏季通勤场景下的显高穿搭、配色和单品选择。",
                core_points=["高腰半裙和短款衬衫能优化比例。", "低饱和配色更适合办公室。"],
                tags=["穿搭", "通勤", "服装搭配"],
            ))

            self.assertEqual(decision["recommended_path"], ["生活方式", "穿搭"])
            self.assertEqual(decision["confidence"], "medium")
            self.assertFalse(decision["matched_existing_category"])
            self.assertTrue(decision["should_create_category"])
            self.assertEqual(decision["create_proposal"]["proposed_path"], ["生活方式", "穿搭"])
            self.assertFalse(any(item["path"] == ["AI", "内容生产", "穿搭"] for item in decision["similar_categories"]))

    def test_lifestyle_sunscreen_auto_routes_to_lifestyle_sunscreen(self) -> None:
        router = TaxonomyRouter.from_config({"taxonomy_auto_create_from_proposal": True})

        decision = router.route_card(make_card(
            display_title="超平价防晒小物推荐",
            one_sentence_summary="小红书用户分享防晒衣、防晒口罩、墨镜等平价防晒小物，适合日常穿搭和防晒需求。",
            core_points=["笔记聚焦平价防晒单品。", "话题标签覆盖穿搭、防晒和墨镜。"],
            tags=["防晒", "平价好物", "穿搭", "小红书种草"],
        ))

        self.assertEqual(decision["recommended_path"], ["生活方式", "防晒"])
        self.assertEqual(decision["confidence"], "medium")
        self.assertFalse(decision["matched_existing_category"])
        self.assertEqual(decision["create_proposal"]["proposed_path"], ["生活方式", "防晒"])

    def test_ai_fashion_generation_can_use_ai_content_production_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree_path = Path(tmp) / "taxonomy_tree.json"
            tree_path.write_text(json.dumps({
                "schema_name": "TaxonomyTreeV1",
                "categories": [
                    {"path": ["知识卡", "AI", "内容生产", "穿搭"]},
                    {"path": ["知识卡", "Inbox", "待分类"]},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            router = TaxonomyRouter.from_config({
                "taxonomy_tree_path": str(tree_path),
                "taxonomy_auto_create_from_proposal": True,
            })

            decision = router.route_card(make_card(
                display_title="AI 穿搭视频生成工作流",
                one_sentence_summary="内容讨论如何用 AI 图片生成和视频生成工具批量生成穿搭造型素材。",
                core_points=["AI 生成图像可以先固定模特、场景和服装风格。"],
                tags=["AI视频", "穿搭", "图像生成"],
            ))

            self.assertEqual(decision["recommended_path"], ["AI", "内容生产", "穿搭"])
            self.assertFalse(decision["matched_existing_category"])
            self.assertTrue(decision["should_create_category"])
            self.assertEqual(decision["create_proposal"]["proposed_path"], ["AI", "内容生产", "穿搭"])

    def test_ai_ppt_video_uses_proposal_despite_weak_ai_tree_similarities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree_path = Path(tmp) / "taxonomy_tree.json"
            tree_path.write_text(json.dumps({
                "schema_name": "TaxonomyTreeV1",
                "categories": [
                    {"path": ["知识卡", "AI", "内容生产", "穿搭"]},
                    {"path": ["知识卡", "AI", "内容生产", "分发策略", "codex 5.5破甲"]},
                    {"path": ["知识卡", "AI", "内容生产", "图像"]},
                    {"path": ["知识卡", "Inbox", "待分类"]},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            router = TaxonomyRouter.from_config({
                "taxonomy_tree_path": str(tree_path),
                "taxonomy_auto_create_from_proposal": True,
            })

            decision = router.route_card(make_card(
                display_title="火柴人PPT科普视频，纯AI生成内容一个多月获得大量播放",
                one_sentence_summary="材料分析火柴人 PPT 加解说的 AI 科普视频起号方式和内容生产流程。",
                core_points=["这种内容依靠 AI 生成脚本、PPT 视觉和解说形成批量科普视频。"],
                tags=["AI视频", "科普内容", "YouTube起号", "自动化工作流", "火柴人PPT"],
            ))

            self.assertEqual(decision["recommended_path"], ["AI", "内容生产", "PPT演示"])
            self.assertEqual(decision["confidence"], "medium")
            self.assertFalse(decision["matched_existing_category"])
            self.assertTrue(decision["should_create_category"])
            self.assertIsNone(decision["fallback"])

    def test_ai_programmer_career_impact_routes_to_career_not_inbox(self) -> None:
        router = TaxonomyRouter.from_config({"taxonomy_auto_create_from_proposal": True})

        decision = router.route_card(make_card(
            display_title="几秒钟就破了我十年功力，程序员该如何破局",
            one_sentence_summary="内容讨论 AI 工具对程序员经验壁垒的冲击，以及开发者如何更新技能。",
            core_points=["AI 正在压缩部分编码经验的优势。", "程序员需要调整学习路径和技能组合。"],
            tags=["AI", "程序员", "职业焦虑", "效率工具", "技能更新"],
        ))

        self.assertEqual(decision["recommended_path"], ["职业发展", "程序员"])
        self.assertEqual(decision["confidence"], "medium")
        self.assertFalse(decision["matched_existing_category"])
        self.assertEqual(decision["create_proposal"]["proposed_path"], ["职业发展", "程序员"])

    def test_auto_create_from_proposal_can_route_new_business_category(self) -> None:
        router = TaxonomyRouter.from_config({"taxonomy_auto_create_from_proposal": True})

        decision = router.route_card(make_card(
            display_title="如何设计电商平台的客服自动化",
            one_sentence_summary="讨论商品咨询、订单状态、售后问题和客服工作流自动化。",
            tags=["电商", "客服", "工作流", "自动化"],
        ))

        self.assertEqual(decision["recommended_path"], ["AI", "行业应用", "客服自动化"])
        self.assertEqual(decision["confidence"], "medium")
        self.assertFalse(decision["matched_existing_category"])
        self.assertTrue(decision["should_create_category"])
        self.assertIsNone(decision["fallback"])

    def test_auto_create_from_proposal_routes_legal_compliance_category(self) -> None:
        router = TaxonomyRouter.from_config({"taxonomy_auto_create_from_proposal": True})

        decision = router.route_card(make_card(
            display_title="搭梯子行为的刑事风险边界",
            one_sentence_summary="内容讨论 VPN 代理、非法经营罪、刑法第285条和技术中立的法律风险。",
            tags=["VPN法律风险", "刑法第285条", "非法经营罪", "技术中立"],
        ))

        self.assertEqual(decision["recommended_path"], ["法律", "VPN法律风险"])
        self.assertEqual(decision["confidence"], "medium")
        self.assertTrue(decision["should_create_category"])

    def test_clear_workplace_topic_uses_proposal_when_auto_create_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree_path = Path(tmp) / "taxonomy_tree.json"
            tree_path.write_text(json.dumps({
                "schema_name": "TaxonomyTreeV1",
                "categories": [
                    {"path": ["知识卡", "Inbox", "待分类"]},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            router = TaxonomyRouter.from_config({
                "taxonomy_tree_path": str(tree_path),
                "taxonomy_auto_create_from_proposal": True,
            })

            decision = router.route_card(make_card(
                display_title="上海人入职说真话：真诚在职场横着走",
                one_sentence_summary="小红书笔记以幽默方式呈现上海人入职时直言不讳的职场态度，强调真诚在职场中的价值。",
                core_points=[
                    "真诚在职场中被视为一种可以横着走的资本。",
                    "幽默与真实结合使严肃职场话题更易传播。",
                ],
                knowledge_blocks=[
                    {
                        "concept": "职场真诚策略",
                        "explanation": "在职场中保持真诚能减少沟通内耗。",
                    }
                ],
                tags=["职场", "真诚", "地域文化", "幽默", "小红书"],
            ))

        self.assertEqual(decision["recommended_path"], ["职场"])
        self.assertEqual(decision["confidence"], "medium")
        self.assertFalse(decision["matched_existing_category"])
        self.assertTrue(decision["should_create_category"])
        self.assertIsNone(decision["fallback"])

    def test_clear_workplace_topic_stays_inbox_without_auto_create(self) -> None:
        decision = self.router.route_card(make_card(
            display_title="上海人入职说真话：真诚在职场横着走",
            one_sentence_summary="小红书笔记以幽默方式呈现上海人入职时直言不讳的职场态度，强调真诚在职场中的价值。",
            tags=["职场", "真诚", "小红书"],
        ))

        self.assertEqual(decision["recommended_path"], ["Inbox", "待分类"])
        self.assertEqual(decision["confidence"], "low")
        self.assertTrue(decision["should_create_category"])
        self.assertEqual(decision["create_proposal"]["proposed_path"], ["职场"])

    def test_run_link_job_helper_writes_taxonomy_decision_for_formal_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            tree_path = Path(tmp) / "taxonomy_tree.json"
            tree_path.write_text(json.dumps({
                "schema_name": "TaxonomyTreeV1",
                "categories": [
                    {"path": ["知识卡", "软件工程", "前端开发", "动画与交互"]},
                    {"path": ["知识卡", "软件工程", "前端开发", "AI 辅助前端开发"]},
                    {"path": ["知识卡", "Inbox", "待分类"]},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            card = make_card(
                display_title="GSAP Skills：官方动画知识包进入 AI 编程工作流",
                one_sentence_summary="GSAP 官方开源 gsap-skills，辅助 AI 工具生成前端动画代码。",
                core_points=["内容重点是 GSAP、ScrollTrigger、时间轴和前端交互动效。"],
                tags=["GSAP", "前端开发", "动画工程", "ScrollTrigger"],
            )
            (job_dir / "composed_card.json").write_text(
                json.dumps(card, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            decision, error = route_taxonomy_for_formal_card(
                job_dir,
                "formal_summary",
                {"quality_gate_passed": True},
                {"taxonomy_tree_path": str(tree_path)},
            )

            self.assertEqual(error, "")
            self.assertEqual(decision["recommended_path"], ["软件工程", "前端开发", "动画与交互"])
            self.assertTrue((job_dir / "taxonomy_decision.json").exists())
            persisted = json.loads((job_dir / "taxonomy_decision.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_name"], "TaxonomyDecisionV1")

    def test_run_link_job_helper_uses_configured_taxonomy_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "job"
            job_dir.mkdir()
            tree_path = root / "taxonomy_tree.json"
            tree_path.write_text(json.dumps({
                "schema_name": "TaxonomyTreeV1",
                "categories": [
                    {"path": ["知识卡", "GSAP"]},
                    {"path": ["知识卡", "Inbox", "待分类"]},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            card = make_card(
                display_title="GSAP Skills：官方动画知识包进入 AI 编程工作流",
                one_sentence_summary="GSAP 和 ScrollTrigger 动画 API 被整理成 AI Skills。",
                tags=["GSAP", "ScrollTrigger"],
            )
            (job_dir / "composed_card.json").write_text(
                json.dumps(card, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            decision, error = route_taxonomy_for_formal_card(
                job_dir,
                "formal_summary",
                {"quality_gate_passed": True},
                {"taxonomy_tree_path": str(tree_path)},
            )

            self.assertEqual(error, "")
            self.assertEqual(decision["recommended_path"], ["GSAP"])
            self.assertEqual(decision["taxonomy_source"], "file")

    def test_run_link_job_helper_skips_non_formal_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            decision, error = route_taxonomy_for_formal_card(
                job_dir,
                "temporary_review_card",
                {"quality_gate_passed": False},
            )

            self.assertEqual(decision, {})
            self.assertEqual(error, "")
            self.assertFalse((job_dir / "taxonomy_decision.json").exists())


if __name__ == "__main__":
    unittest.main()
