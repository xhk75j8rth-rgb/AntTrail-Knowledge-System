from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server.chat_api as chat_api
from fastapi.testclient import TestClient

from chat_gateway.link_extractor import extract_context_text, extract_urls
from chat_gateway.message_router import route_message
from chat_gateway.message_schema import MessageEvent
from intake_platforms import authorization_status, classify_url, open_authorization, test_authorization
from server.chat_api import app
from tools.fetch_webpage import (
    build_content_payload as build_webpage_content_payload,
    extract_html_material,
    merge_static_and_rendered_material,
    should_try_rendered_fallback,
)
from tools.fetch_xiaohongshu_note import build_content_payload, is_xiaohongshu_url, note_id_from_url
from tools.run_link_job import (
    assess_material_quality,
    build_extracted_source_card,
    detect_source_type,
    format_ocr_image_evidence,
    lucas_database_write_policy,
    normalize_storage_targets,
    should_run_douyin_web_image_ocr,
    should_run_visual_platform_web_image_ocr,
    source_material_text,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ChatGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_keys = [
            "AI_LAYER_CONFIG_PATH",
            "AI_LAYER_PROVIDER",
            "AI_LAYER_API_KEY",
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_COMPATIBLE_API_KEY",
            "DEEPSEEK_BASE_URL",
            "DEEPSEEK_MODEL",
            "LUCAS_STORAGE_CONFIG_PATH",
            "LUCAS_STORAGE_ENV_PATH",
            "LUCAS_LINK_PIPELINE_CONFIG_PATH",
            "SIYUAN_TOKEN",
            "LUCAS_DB_API_KEY",
        ]
        self._old_env = {key: os.environ.get(key) for key in self._env_keys}
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["AI_LAYER_CONFIG_PATH"] = str(Path(self._tmpdir.name) / "ai_layer.local.json")
        os.environ.pop("AI_LAYER_PROVIDER", None)

    def tearDown(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmpdir.cleanup()

    def _configure_missing_preflight_env(self, targets: list[str]) -> None:
        root = Path(self._tmpdir.name)
        storage_path = root / "storage.local.json"
        storage_env_path = root / ".env"
        pipeline_path = root / "link_pipeline.json"
        storage_path.write_text(json.dumps({
            "active_provider": "lucas_database",
            "providers": {
                "lucas_database": {
                    "base_url": "http://127.0.0.1:8765",
                    "endpoint": "/api/cards/ingest",
                },
                "siyuan": {
                    "base_url": "http://127.0.0.1:6806",
                    "endpoint": "/api/filetree/createDocWithMd",
                },
            },
        }, ensure_ascii=False), encoding="utf-8")
        pipeline_path.write_text(json.dumps({
            "runtime_dir": "runtime/jobs",
            "storage_targets": targets,
            "siyuan_base_url": "http://127.0.0.1:6806",
        }, ensure_ascii=False), encoding="utf-8")
        storage_env_path.write_text("", encoding="utf-8")
        os.environ["AI_LAYER_PROVIDER"] = "deepseek_compatible"
        os.environ["AI_LAYER_API_KEY"] = ""
        os.environ["DEEPSEEK_API_KEY"] = ""
        os.environ["DEEPSEEK_COMPATIBLE_API_KEY"] = ""
        os.environ["LUCAS_STORAGE_CONFIG_PATH"] = str(storage_path)
        os.environ["LUCAS_STORAGE_ENV_PATH"] = str(storage_env_path)
        os.environ["LUCAS_LINK_PIPELINE_CONFIG_PATH"] = str(pipeline_path)
        os.environ.pop("SIYUAN_TOKEN", None)
        os.environ.pop("LUCAS_DB_API_KEY", None)

    def test_plain_text_without_url_goes_to_fallback(self) -> None:
        os.environ["AI_LAYER_PROVIDER"] = "mock"
        event = MessageEvent.from_text(
            "你好",
            channel="debug_cli",
            conversation_id="test-room",
            sender_id="lucas",
        )
        response = route_message(event, dry_run=True)

        self.assertTrue(response.ok)
        self.assertEqual(response.handled_by, "fallback_handler")
        self.assertEqual(response.status, "normal_chat")
        self.assertIn("mock:text:", response.reply_text)
        self.assertEqual(response.data["agent"]["model_provider"], "mock")
        self.assertTrue(response.data["agent"]["model_called"])

    def test_storage_config_question_reads_config_without_model_guessing(self) -> None:
        storage_path = Path(self._tmpdir.name) / "storage.local.json"
        storage_env_path = Path(self._tmpdir.name) / ".env"
        pipeline_path = Path(self._tmpdir.name) / "link_pipeline.json"
        storage_path.write_text(json.dumps({
            "active_provider": "lucas_database",
            "providers": {
                "lucas_database": {"base_url": "http://127.0.0.1:8765", "endpoint": "/api/cards/ingest"},
                "siyuan": {"base_url": "http://127.0.0.1:6806", "endpoint": "/api/filetree/createDocWithMd"},
            },
        }, ensure_ascii=False), encoding="utf-8")
        storage_env_path.write_text("LUCAS_DB_API_KEY=db-key\nSIYUAN_TOKEN=siyuan-key\n", encoding="utf-8")
        pipeline_path.write_text(json.dumps({
            "storage_targets": ["siyuan", "lucas_database"],
            "lucas_database_write_policy": "all_cards",
        }, ensure_ascii=False), encoding="utf-8")
        event = MessageEvent.from_text(
            "我现在可以写入的知识库有哪些",
            channel="debug_cli",
            conversation_id="test-room",
            sender_id="lucas",
        )

        with patch.dict(os.environ, {
            "LUCAS_STORAGE_CONFIG_PATH": str(storage_path),
            "LUCAS_STORAGE_ENV_PATH": str(storage_env_path),
            "LUCAS_LINK_PIPELINE_CONFIG_PATH": str(pipeline_path),
            "AI_LAYER_PROVIDER": "mock",
        }, clear=False):
            response = route_message(event, dry_run=True)

        self.assertTrue(response.ok)
        self.assertEqual(response.handled_by, "fallback_handler")
        self.assertEqual(response.status, "storage_config_query")
        self.assertFalse(response.data["agent"]["model_called"])
        self.assertIn("现在实际可写是 SiYuan、AntTrail Database", response.reply_text)
        self.assertLessEqual(len(response.reply_text.splitlines()), 3)
        writable = {item["target_id"] for item in response.data["storage"]["writable_targets"]}
        self.assertEqual(writable, {"siyuan", "lucas_database"})

    def test_status_followup_reply_is_concise_without_guessing(self) -> None:
        os.environ["AI_LAYER_PROVIDER"] = "mock"
        event = MessageEvent.from_text(
            "现在好了吗",
            channel="debug_cli",
            conversation_id="test-room",
            sender_id="lucas",
            metadata={"conversation_history": [{"role": "user", "text": "我有哪些知识库接入"}]},
        )

        response = route_message(event, dry_run=True)

        self.assertTrue(response.ok)
        self.assertEqual(response.handled_by, "fallback_handler")
        self.assertEqual(response.status, "status_followup")
        self.assertFalse(response.data["agent"]["model_called"])
        self.assertLessEqual(len(response.reply_text.splitlines()), 2)
        self.assertNotIn("正在查询", response.reply_text)
        self.assertNotIn("建议下一步", response.reply_text)

    def test_note_revision_request_goes_to_model_backed_discussion(self) -> None:
        os.environ["AI_LAYER_PROVIDER"] = "mock"
        event = MessageEvent.from_text(
            "我需要调整笔记",
            channel="debug_cli",
            conversation_id="test-room",
            sender_id="lucas",
            metadata={
                "conversation_history": [
                    {
                        "role": "assistant",
                        "text": "入库卡片：\n标题：高短调影调：AI视频中的亮浅柔美学\n路径：/��/AI/坏路径.md",
                    },
                ],
            },
        )
        with patch("chat_gateway.handlers.fallback_handler.REVISION_RUNTIME_DIR", Path(self._tmpdir.name) / "jobs"):
            response = route_message(event, dry_run=True)

        self.assertTrue(response.ok)
        self.assertEqual(response.handled_by, "fallback_handler")
        self.assertEqual(response.status, "note_revision_request")
        self.assertTrue(str(response.job_id).startswith("revision-"))
        self.assertTrue(response.data["agent"]["model_called"])
        self.assertTrue(response.data["revision"]["requested"])
        self.assertEqual(response.data["revision"]["candidate_title"], "高短调影调：AI视频中的亮浅柔美学")
        self.assertEqual(response.data["queue"]["items"][0]["item_type"], "note_revision")
        self.assertEqual(response.data["queue"]["items"][0]["queue_status"], "in_progress")
        self.assertEqual(response.data["queue"]["items"][0]["write_status"], "pending_policy")
        self.assertEqual(response.data["revision_job"]["status"], "pending_quality_gate")
        self.assertNotIn("请告诉我三件事", response.reply_text)
        self.assertNotIn("路径：", response.reply_text)
        self.assertNotIn("�", response.reply_text)

    def test_revision_followup_with_recent_card_context_uses_model_backed_discussion(self) -> None:
        os.environ["AI_LAYER_PROVIDER"] = "mock"
        event = MessageEvent.from_text(
            "你真的可以重新提交吗，如果可以帮我修改Crow5复刻Claude璀璨星动画的就好",
            channel="debug_cli",
            conversation_id="test-room",
            sender_id="lucas",
            metadata={
                "conversation_history": [
                    {
                        "role": "assistant",
                        "text": "入库卡片：\n标题：Crow5复刻Claude璀璨星动画\n一句话摘要：这是一个动画复刻案例。",
                    },
                ],
            },
        )
        with patch("chat_gateway.handlers.fallback_handler.REVISION_RUNTIME_DIR", Path(self._tmpdir.name) / "jobs"):
            response = route_message(event, dry_run=True)

        self.assertTrue(response.ok)
        self.assertEqual(response.handled_by, "fallback_handler")
        self.assertEqual(response.status, "note_revision_request")
        self.assertTrue(str(response.job_id).startswith("revision-"))
        self.assertTrue(response.data["agent"]["model_called"])
        self.assertEqual(response.data["revision"]["candidate_title"], "Crow5复刻Claude璀璨星动画")
        self.assertEqual(response.data["queue"]["items"][0]["item_type"], "note_revision")
        self.assertEqual(response.data["queue"]["items"][0]["stages"], ["修订草稿", "质量门禁", "写入策略"])
        self.assertNotIn("请告诉我三件事", response.reply_text)

    def test_storage_targets_support_testing_default_both_sinks(self) -> None:
        config = {
            "storage_targets": ["siyuan", "brain"],
            "lucas_database_write_policy": "all_cards",
        }

        self.assertEqual(normalize_storage_targets(config), ["siyuan", "lucas_database"])
        self.assertEqual(lucas_database_write_policy(config), "all_cards")

    def test_storage_write_switch_can_disable_all_sinks(self) -> None:
        config = {
            "enable_storage_write": False,
            "storage_targets": ["siyuan", "lucas_database"],
        }

        self.assertEqual(normalize_storage_targets(config), [])
        self.assertEqual(lucas_database_write_policy(config), "formal_only")

    def test_storage_targets_can_disable_external_sinks(self) -> None:
        self.assertEqual(normalize_storage_targets({"storage_targets": []}), [])
        self.assertEqual(normalize_storage_targets({"storage_targets": "none"}), [])

    def test_link_reply_includes_config_warnings_before_user_reply(self) -> None:
        self._configure_missing_preflight_env(["siyuan", "lucas_database"])
        event = MessageEvent.from_text(
            "https://v.douyin.com/needs-config/",
            channel="debug_cli",
            conversation_id="test-room",
            sender_id="lucas",
        )

        response = route_message(event, dry_run=True)

        self.assertTrue(response.ok)
        self.assertTrue(response.reply_text.startswith("配置提醒："))
        warnings = response.data["config_warnings"]
        codes = {item["code"] for item in warnings}
        self.assertIn("ai_api_key_missing", codes)
        self.assertIn("siyuan_token_missing", codes)
        self.assertIn("lucas_database_api_key_missing", codes)
        self.assertEqual(response.data["config_preflight"]["status"], "degraded")
        serialized = json.dumps(response.to_dict(), ensure_ascii=False)
        self.assertNotIn("secret", serialized.lower())
        self.assertNotIn("sk-", serialized.lower())

    def test_system_status_reports_config_warnings_without_live_probe(self) -> None:
        self._configure_missing_preflight_env(["siyuan", "lucas_database"])
        client = TestClient(app)

        response = client.get("/api/system/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["ai"]["configured"])
        self.assertEqual(payload["storage"]["storage_targets"], ["siyuan", "lucas_database"])
        self.assertEqual(payload["runtime"]["project_root"], str(PROJECT_ROOT))
        self.assertTrue(Path(payload["runtime"]["runtime_dir"]).match("*/runtime/jobs"))
        self.assertTrue(payload["runtime"]["config_path"].endswith("link_pipeline.json"))
        codes = {item["code"] for item in payload["config_warnings"]}
        self.assertIn("ai_api_key_missing", codes)
        self.assertIn("siyuan_token_missing", codes)
        self.assertIn("lucas_database_api_key_missing", codes)
        self.assertFalse(payload["used_mcp"])

    def test_plain_douyin_url_is_extracted_from_markdown_link(self) -> None:
        urls = extract_urls("[https://v.douyin.com/xxx/](https://v.douyin.com/xxx/)")

        self.assertEqual(urls, ["https://v.douyin.com/xxx/"])

    def test_douyin_share_text_extracts_first_url(self) -> None:
        text = "8.25 uSl:/ :6pm 02/24 a@A.Ty mirofish https://v.douyin.com/WIHTDF17W3Q/ 复制此链接，打开Dou音搜索。"
        urls = extract_urls(text)

        self.assertEqual(urls[0], "https://v.douyin.com/WIHTDF17W3Q/")

    def test_link_context_text_keeps_non_url_material(self) -> None:
        text = (
            "这段分享文本本身有价值：要先判断来源材料、再写知识卡，"
            "并且记录后续动作。https://example.com/article?token=secret-value "
            "复制此链接。"
        )

        context = extract_context_text(text)

        self.assertIn("这段分享文本本身有价值", context)
        self.assertIn("复制此链接", context)
        self.assertNotIn("https://example.com", context)

    def test_multiple_urls_are_reported_as_pending_queue_in_dry_run(self) -> None:
        event = MessageEvent.from_text(
            "第一个 https://v.douyin.com/one/ 第二个 https://example.com/two",
            channel="debug_cli",
            conversation_id="test-room",
            sender_id="lucas",
        )
        response = route_message(event, dry_run=True)

        self.assertTrue(response.ok)
        self.assertEqual(response.handled_by, "link_handler")
        self.assertEqual(response.data["url"], "https://v.douyin.com/one/")
        self.assertEqual(response.data["extra_url_count"], 1)
        self.assertIn("并行队列", response.reply_text)
        self.assertEqual(response.data["queue"]["status"], "pending")
        self.assertEqual(response.data["queue"]["mode"], "parallel")
        self.assertEqual(response.data["queue"]["total"], 2)
        self.assertEqual(response.data["queue"]["pending"], 2)
        self.assertFalse(response.data["queue"]["items"][0]["runner_called"])

    def test_multiple_urls_are_processed_in_parallel_with_card_status(self) -> None:
        event = MessageEvent.from_text(
            "第一个 https://v.douyin.com/one/ 第二个 https://example.com/two",
            channel="debug_cli",
            conversation_id="test-room",
            sender_id="lucas",
        )
        result_one = {
            "ok": True,
            "job_id": "job-one",
            "job_dir": "runtime/jobs/job-one",
            "url": "https://v.douyin.com/one/",
            "final_status": "completed_formal",
            "card_type": "formal_summary",
            "quality_gate_passed": True,
            "title": "正式卡",
            "siyuan_write_ok": True,
            "write_result": {"ok": True, "path": "/知识卡/正式卡.md", "doc_id": "doc-one"},
            "used_mcp": False,
        }
        result_two = {
            "ok": True,
            "job_id": "job-two",
            "job_dir": "runtime/jobs/job-two",
            "url": "https://example.com/two",
            "final_status": "completed_needs_model_review",
            "card_type": "temporary_review_card",
            "composer_status": "failed",
            "quality_gate_passed": False,
            "title": "待模型复核",
            "siyuan_write_ok": True,
            "write_result": {"ok": True, "path": "/00_Inbox/待模型复核.md", "doc_id": "doc-two"},
            "used_mcp": False,
        }

        results_by_url = {
            "https://v.douyin.com/one/": result_one,
            "https://example.com/two": result_two,
        }
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_runner(url: str, timeout_sec: int, runner_env: object, source_text: str = "") -> tuple[int, dict[str, object], str]:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return 0, results_by_url[url], ""

        with patch("chat_gateway.handlers.link_handler._run_link_job", side_effect=fake_runner) as runner:
            response = route_message(event, dry_run=False, timeout_sec=5)

        self.assertEqual({call.args[0] for call in runner.call_args_list}, {
            "https://v.douyin.com/one/",
            "https://example.com/two",
        })
        self.assertGreaterEqual(max_active, 2)
        self.assertTrue(response.ok)
        self.assertIsNone(response.job_id)
        self.assertEqual(response.status, "batch_completed_with_card_failures")
        self.assertEqual(response.data["job_ids"], ["job-one", "job-two"])
        queue = response.data["queue"]
        self.assertEqual(queue["mode"], "parallel")
        self.assertEqual(queue["completed"], 2)
        self.assertEqual(queue["pending"], 0)
        self.assertEqual(queue["card_failed"], 1)
        self.assertEqual(queue["items"][0]["card_status"], "formal_card_created")
        self.assertEqual(queue["items"][1]["card_status"], "card_generation_failed")
        self.assertIn("完成但卡片生成失败", response.reply_text)

    def test_single_link_runner_receives_user_supplied_source_text(self) -> None:
        event = MessageEvent.from_text(
            "这是一段用户随链接提供的长文本材料，应该进入来源材料，而不是被入口丢掉。"
            "它包含摘要、判断标准和后续动作。https://example.com/page",
            channel="debug_cli",
            conversation_id="test-room",
            sender_id="lucas",
        )
        captured_source_text: list[str] = []
        result = {
            "ok": True,
            "job_id": "job-source-text",
            "job_dir": "runtime/jobs/job-source-text",
            "url": "https://example.com/page",
            "final_status": "completed_source_material_written",
            "card_type": "extracted_source_card",
            "quality_gate_passed": False,
            "title": "来源材料",
            "siyuan_write_ok": True,
            "write_result": {"ok": True, "path": "/知识卡/Inbox/来源材料.md", "doc_id": "doc-source"},
            "used_mcp": False,
        }

        def fake_runner(url: str, timeout_sec: int, runner_env: object, source_text: str = "") -> tuple[int, dict[str, object], str]:
            captured_source_text.append(source_text)
            return 0, result, ""

        with patch("chat_gateway.handlers.link_handler._run_link_job", side_effect=fake_runner):
            response = route_message(event, dry_run=False, timeout_sec=5)

        self.assertTrue(response.ok)
        self.assertTrue(response.data["source_text_present"])
        self.assertIn("用户随链接提供的长文本材料", captured_source_text[0])
        self.assertNotIn("https://example.com/page", captured_source_text[0])

    def test_single_link_reply_shows_summary_learning_and_storage_card(self) -> None:
        event = MessageEvent.from_text(
            "https://v.douyin.com/visual/",
            channel="debug_cli",
            conversation_id="test-room",
            sender_id="lucas",
        )
        job_dir = Path(self._tmpdir.name) / "job-reply-card"
        job_dir.mkdir()
        (job_dir / "composed_card.json").write_text(json.dumps({
            "display_title": "高短调影调：AI视频中的亮浅柔美学",
            "one_sentence_summary": "高短调是一种明亮、柔和、低反差的影调风格，可用于产品广告和AI视频画面设计。",
            "reusable_value": [
                "把影调当成可控变量，先定明亮柔和低反差，再用颜色、构图和道具补回层次感。",
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (job_dir / "taxonomy_decision.json").write_text(json.dumps({
            "schema_name": "TaxonomyDecisionV1",
            "recommended_path": ["AI", "内容生产", "图像 / 视频生成"],
        }, ensure_ascii=False), encoding="utf-8")
        result = {
            "ok": True,
            "job_id": "job-reply-card",
            "job_dir": str(job_dir),
            "url": "https://v.douyin.com/visual/",
            "final_status": "completed_formal",
            "card_type": "formal_summary",
            "quality_gate_passed": True,
            "title": "旧标题",
            "content_level": "Level 5 评论与互动增强级",
            "siyuan_write_ok": True,
            "write_result": {
                "ok": True,
                "path": "/知识卡/AI/内容生产/图像 / 视频生成/高短调影调：AI视频中的亮浅柔美学.md",
                "doc_id": "doc-reply-card",
            },
            "storage_targets": ["siyuan", "lucas_database"],
            "lucas_database_write_result": {
                "ok": True,
                "stage": "completed",
                "target_id": "main",
                "target_label": "主数据库",
                "path": "/知识卡/AI/内容生产/图像 / 视频生成/高短调影调AI视频中的亮浅柔美学",
                "card_id": "card-reply-card",
                "node_id": "node-reply-card",
            },
            "lucas_database_write_ok": True,
            "comments": {"comment_count": 10},
            "used_mcp": False,
        }

        with patch("chat_gateway.handlers.link_handler._run_link_job", return_value=(0, result, "")):
            response = route_message(event, dry_run=False, timeout_sec=5)

        self.assertTrue(response.ok)
        self.assertIn("摘要：高短调是一种明亮、柔和、低反差的影调风格", response.reply_text)
        self.assertIn("值得学习：把影调当成可控变量", response.reply_text)
        self.assertIn("接入结果：是｜标题：高短调影调：AI视频中的亮浅柔美学", response.reply_text)
        self.assertIn("写入目标：SiYuan、主数据库", response.reply_text)
        self.assertIn("写入层级：SiYuan：/知识卡/AI/内容生产/图像 / 视频生成；主数据库：/知识卡/AI/内容生产/图像 / 视频生成", response.reply_text)
        self.assertIn("文件树：/知识卡/AI/内容生产/图像 / 视频生成", response.reply_text)
        self.assertNotIn("入库卡片：", response.reply_text)
        self.assertNotIn("一句话摘要：", response.reply_text)
        self.assertEqual(response.reply_text.count("高短调是一种明亮、柔和、低反差的影调风格"), 1)
        self.assertNotIn("等级：", response.reply_text)
        self.assertNotIn("评论：已读取", response.reply_text)
        reply_card = response.data["result"]["reply_card"]
        self.assertEqual(reply_card["file_tree"], "/知识卡/AI/内容生产/图像 / 视频生成")
        self.assertEqual(reply_card["storage_targets"], "SiYuan、主数据库")
        self.assertTrue(reply_card["access_ok"])

    def test_link_runner_hides_child_python_console_on_windows(self) -> None:
        from chat_gateway.handlers import link_handler

        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"ok": True, "used_mcp": False}),
            stderr="",
        )

        with patch("chat_gateway.handlers.link_handler.os.name", "nt"), patch(
            "chat_gateway.handlers.link_handler.subprocess.CREATE_NO_WINDOW",
            0x08000000,
            create=True,
        ), patch("chat_gateway.handlers.link_handler.subprocess.run", return_value=completed) as runner:
            exit_code, result, stderr = link_handler._run_link_job(
                "https://example.com/page",
                timeout_sec=5,
                runner_env={},
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(stderr, "")
        self.assertEqual(runner.call_args.kwargs["creationflags"], 0x08000000)

    def test_link_runner_passes_source_text_file_to_child_process(self) -> None:
        from chat_gateway.handlers import link_handler

        captured_path: list[Path] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            self.assertIn("--source-text-file", cmd)
            source_path = Path(cmd[cmd.index("--source-text-file") + 1])
            captured_path.append(source_path)
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["source"], "message_text")
            self.assertIn("真正有价值的正文", payload["source_text"])
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps({"ok": True, "used_mcp": False}),
                stderr="",
            )

        with patch("chat_gateway.handlers.link_handler.subprocess.run", side_effect=fake_run):
            exit_code, result, stderr = link_handler._run_link_job(
                "https://example.com/page",
                timeout_sec=5,
                runner_env={},
                source_text="真正有价值的正文，应该传给 run_link_job。",
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(stderr, "")
        self.assertFalse(captured_path[0].exists())

    def test_http_api_returns_handler_response(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/chat/messages",
            json={
                "channel": "my_chat_app",
                "conversation_id": "test-room",
                "sender_id": "lucas",
                "text": "https://v.douyin.com/xxx/",
                "dry_run": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["handled_by"], "link_handler")
        self.assertEqual(payload["status"], "dry_run")
        self.assertFalse(payload["data"]["runner_called"])

    def test_http_api_async_link_returns_realtime_batch_without_running_real_task(self) -> None:
        client = TestClient(app)
        runner_called = threading.Event()

        def fake_run_async_batch(*args: object) -> None:
            runner_called.set()

        with patch("server.chat_api._run_async_batch", side_effect=fake_run_async_batch) as runner:
            response = client.post(
                "/api/chat/messages/async",
                json={
                    "channel": "my_chat_app",
                    "conversation_id": "test-room",
                    "sender_id": "lucas",
                    "text": "这段用户文字应该被异步队列保留，用来生成来源材料。https://v.douyin.com/xxx/",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "batch_running")
        self.assertTrue(payload["data"]["async"])
        self.assertTrue(payload["data"]["batch_id"].startswith("batch-"))
        self.assertTrue(payload["data"]["source_text_present"])
        self.assertGreater(payload["data"]["source_text_length"], 10)
        self.assertEqual(payload["data"]["queue"]["items"][0]["stage_label"], "任务已接收")
        self.assertEqual(payload["data"]["queue"]["items"][0]["stages"], ["内容抓取", "转写 / OCR", "生成知识卡", "写入数据库"])
        self.assertTrue(runner_called.wait(1))
        runner.assert_called_once()

        poll_response = client.get(f"/api/chat/batches/{payload['data']['batch_id']}")
        self.assertEqual(poll_response.status_code, 200)
        poll_payload = poll_response.json()
        self.assertEqual(poll_payload["data"]["batch_id"], payload["data"]["batch_id"])
        self.assertEqual(poll_payload["data"]["queue"]["unfinished"], 1)

    def test_async_single_link_completed_reply_uses_result_card_not_queue_template(self) -> None:
        job_dir = Path(self._tmpdir.name) / "job-async-reply-card"
        job_dir.mkdir()
        (job_dir / "composed_card.json").write_text(json.dumps({
            "display_title": "分发才是护城河：先验证需求再开发产品",
            "one_sentence_summary": "Flame 团队用多个 TikTok 账号高频测试内容切口，强调先验证分发和需求，再投入产品开发。",
            "reusable_value": ["先用真实平台分发信号验证需求，再决定是否做产品。"],
        }, ensure_ascii=False), encoding="utf-8")
        (job_dir / "taxonomy_decision.json").write_text(json.dumps({
            "schema_name": "TaxonomyDecisionV1",
            "recommended_path": ["AI", "内容生产", "分发策略"],
        }, ensure_ascii=False), encoding="utf-8")
        result = {
            "ok": True,
            "job_id": "job-async-reply-card",
            "job_dir": str(job_dir),
            "url": "https://v.douyin.com/distribution/",
            "final_status": "completed_formal",
            "card_type": "formal_summary",
            "quality_gate_passed": True,
            "title": "分发才是护城河",
            "siyuan_write_ok": True,
            "write_result": {
                "ok": True,
                "path": "/AI/内容生产/分发策略/分发才是护城河：先验证需求再开发产品.md",
                "doc_id": "doc-async-reply-card",
            },
            "lucas_database_write_results": [
                {
                    "target_id": "main",
                    "label": "主数据库",
                    "ok": True,
                    "result": {
                        "ok": True,
                        "target_id": "main",
                        "target_label": "主数据库",
                        "path": "/知识卡/AI/内容生产/分发策略/分发才是护城河先验证需求再开发产品",
                        "card_id": "card-async-reply-card",
                        "node_id": "node-async-reply-card",
                    },
                },
            ],
            "used_mcp": False,
        }
        completed = chat_api.link_handler._completed_queue_item(
            1,
            "https://v.douyin.com/distribution/",
            0,
            result,
            "",
        )
        completed["reply_text"] = chat_api.link_handler._build_reply(result)
        queue = chat_api.link_handler._build_queue([completed], mode="single")

        reply = chat_api._build_async_batch_reply(queue)

        self.assertIn("摘要：Flame 团队用多个 TikTok 账号高频测试内容切口", reply)
        self.assertIn("写入目标：SiYuan、主数据库", reply)
        self.assertIn("写入层级：SiYuan：/AI/内容生产/分发策略；主数据库：/知识卡/AI/内容生产/分发策略", reply)
        self.assertNotIn("已检测到 1 个链接", reply)
        self.assertNotIn("队列状态：", reply)

    def test_http_api_serves_job_ocr_image_from_material_allowlist(self) -> None:
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            job_dir = runtime_dir / "job-ocr-image"
            frames_dir = job_dir / "frames"
            frames_dir.mkdir(parents=True)
            frame_path = frames_dir / "frame_001.jpg"
            frame_path.write_bytes(b"fake-jpeg")
            (job_dir / "ocr_material.json").write_text(json.dumps({
                "schema_name": "OCRMaterialV1",
                "schema_version": "1",
                "sampling": {
                    "frames": [
                        {"frame_index": 0, "path": str(frame_path)},
                    ],
                },
                "evidence_items": [],
            }, ensure_ascii=False), encoding="utf-8")

            with patch("server.chat_api._runtime_dir", return_value=runtime_dir):
                response = client.get("/api/jobs/job-ocr-image/ocr-images/frame_001.jpg")
                blocked = client.get("/api/jobs/job-ocr-image/ocr-images/not_allowlisted.jpg")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"fake-jpeg")
            self.assertEqual(blocked.status_code, 404)

    def test_intake_platform_classifier_routes_known_sources(self) -> None:
        douyin = classify_url("https://v.douyin.com/xxx/")
        toutiao = classify_url("https://www.toutiao.com/article/7657582749842211368/")
        xhs = classify_url("https://www.xiaohongshu.com/explore/abc")
        webpage = classify_url("https://example.com/article")

        self.assertEqual(douyin["source_type"], "video/douyin")
        self.assertEqual(douyin["route_level"], "highest_available")
        self.assertEqual(douyin["route_id"], "video_highest_available")
        self.assertEqual(toutiao["source_type"], "text/toutiao")
        self.assertEqual(toutiao["route_level"], "text_extract_then_analyze")
        self.assertEqual(toutiao["route_id"], "text_extract_then_analyze")
        self.assertEqual(xhs["source_type"], "mixed/xiaohongshu")
        self.assertEqual(xhs["route_level"], "hybrid_text_video_ocr")
        self.assertEqual(xhs["route_id"], "hybrid_text_video_ocr")
        self.assertEqual(xhs["platform_id"], "xiaohongshu")
        self.assertEqual(webpage["source_type"], "webpage")
        self.assertEqual(webpage["route_id"], "basic_webpage")
        self.assertIn("材料门禁", webpage["strategy"])

    def test_runner_uses_link_first_route_for_xiaohongshu(self) -> None:
        url = "https://www.xiaohongshu.com/explore/6a4482da00000000160279b0?xsec_token=secret-value"

        self.assertEqual(detect_source_type(url), "mixed/xiaohongshu")

    def test_generic_webpage_reader_payload_shape(self) -> None:
        url = "https://example.com/post?session_token=secret-value"
        material = extract_html_material(
            """
            <html>
              <head>
                <title>普通网页标题 - Example</title>
                <meta name="description" content="普通网页描述">
                <meta name="author" content="作者A">
              </head>
              <body>
                <nav>首页</nav>
                <article>
                  <h1>普通网页标题</h1>
                  <p>这是一段普通网页正文材料，应该进入统一来源材料审查，而不是直接低置信打回。</p>
                  <p>材料足够时可以交给模型写卡，材料不足时才降级为来源材料卡。</p>
                  <img src="demo.jpg">
                </article>
              </body>
            </html>
            """,
            url,
        )
        payload = build_webpage_content_payload(url, material)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source_type"], "webpage")
        self.assertEqual(payload["content_kind"], "webpage")
        self.assertEqual(payload["status"], "webpage_extracted")
        self.assertGreaterEqual(payload["chinese_char_count"], 40)
        self.assertEqual(payload["image_count"], 1)
        self.assertNotIn("secret-value", json.dumps(payload, ensure_ascii=False))

    def test_generic_webpage_reader_detects_static_webapp_shell_for_rendered_fallback(self) -> None:
        url = "https://example.com/share/abc"
        html = """
        <html>
          <head><title>来看看这段聊天</title><script>window.__NEXT_DATA__ = {}</script></head>
          <body>
            <div id="__next">
              <nav>Skip to content</nav>
              <button>Log in</button>
              <button>Sign up for free</button>
              <span>Settings</span>
              <span>Privacy Policy</span>
            </div>
          </body>
        </html>
        """
        material = extract_html_material(html, url)

        self.assertTrue(should_try_rendered_fallback(html, material))

    def test_generic_webpage_reader_prefers_richer_rendered_text(self) -> None:
        url = "https://example.com/share/abc?session_token=secret-value"
        static = extract_html_material(
            "<html><head><title>来看看这段聊天</title></head><body>Skip to content\nLog in\nSign up</body></html>",
            url,
        )
        rendered = {
            "ok": True,
            "status": "webpage_rendered_extracted",
            "final_url": url,
            "title": "来看看这段聊天",
            "main_text": "蒸馏能力与流程\n" + "这一段是真正的分享对话正文，包含可复用的步骤、判断标准和后续动作。" * 8,
            "image_count": 0,
            "video_source_count": 0,
            "audio_source_count": 0,
            "extraction_source": "message_dom",
        }

        merged = merge_static_and_rendered_material(static, rendered, url)
        payload = build_webpage_content_payload(url, merged)

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["rendered_fallback_used"])
        self.assertIn("html_parser+message_dom", payload["extraction_source"])
        self.assertGreater(payload["chinese_char_count"], static["chinese_char_count"])
        self.assertNotIn("secret-value", json.dumps(payload, ensure_ascii=False))

    def test_extracted_source_card_redacts_token_like_url_params(self) -> None:
        url = "https://www.xiaohongshu.com/explore/6a4482da00000000160279b0?xsec_token=secret-value"
        content = {
            "ok": True,
            "status": "note_extracted",
            "source_type": "mixed/xiaohongshu",
            "final_url": url,
            "title": "真实小红书笔记标题",
            "author": "作者A",
            "main_text": "这是一段已经提取到的小红书正文材料。",
            "text_length": 20,
            "chinese_char_count": 20,
            "note_type": "image_text_note",
            "image_count": 3,
            "need_ocr": True,
            "extraction_source": "fixture",
        }

        title, markdown, level = build_extracted_source_card(
            url,
            "fixture-job",
            Path(self._tmpdir.name),
            "mixed/xiaohongshu",
            content,
            classify_url(url),
        )

        self.assertIn("来源材料", title)
        self.assertEqual(level, "Level 2 混合笔记可见材料级")
        self.assertIn("extracted_source_card", markdown)
        self.assertIn("这是一段已经提取到的小红书正文材料", markdown)
        self.assertNotIn("secret-value", markdown)

    def test_xiaohongshu_reader_content_payload_shape(self) -> None:
        url = "https://www.xiaohongshu.com/explore/6a4482da00000000160279b0"
        payload = build_content_payload(url, {
            "ok": True,
            "status": "note_extracted",
            "title": "笔记标题",
            "main_text": "正文",
            "image_count": 2,
            "video_source_count": 1,
            "has_video": True,
        })

        self.assertTrue(is_xiaohongshu_url(url))
        self.assertEqual(note_id_from_url(url), "6a4482da00000000160279b0")
        self.assertEqual(payload["source_type"], "mixed/xiaohongshu")
        self.assertEqual(payload["content_kind"], "mixed_note")
        self.assertTrue(payload["need_ocr"])
        self.assertTrue(payload["should_continue_transcribe"])

    def test_material_quality_gate_blocks_short_visual_material_without_platform_special_case(self) -> None:
        content = {
            "ok": True,
            "status": "note_extracted",
            "source_type": "mixed/xiaohongshu",
            "main_text": "这是一段很短的穿搭描述。",
            "need_ocr": True,
            "image_count": 12,
        }

        quality = assess_material_quality(content, {}, {}, {}, {})

        self.assertFalse(quality["can_compose_formal"])
        self.assertIn("primary_material_too_short", quality["blockers"])
        self.assertIn("visual_or_media_note_needs_ocr_or_transcription", quality["blockers"])

    def test_material_quality_gate_allows_sufficient_text_regardless_of_platform(self) -> None:
        long_text = "这是一个通用网页正文材料。" * 30
        content = {
            "ok": True,
            "status": "article_extracted",
            "source_type": "text/example",
            "main_text": long_text,
        }

        quality = assess_material_quality(content, {}, {}, {}, {})

        self.assertTrue(quality["can_compose_formal"])
        self.assertIn("source_text", quality["primary_sources"])

    def test_material_quality_gate_allows_user_supplied_text_as_primary_material(self) -> None:
        user_text = "用户随链接提供了很长的一段材料，包含背景、关键判断、可复用价值和后续动作。" * 12
        content = {
            "ok": False,
            "status": "page_fetch_failed",
            "source_type": "video/douyin",
            "main_text": "Please wait",
            "user_supplied_text": user_text,
            "user_supplied_text_source": "message_text",
        }

        quality = assess_material_quality(content, {}, {}, {}, {})
        combined = source_material_text(content)

        self.assertTrue(quality["can_compose_formal"])
        self.assertIn("user_supplied_text", quality["primary_sources"])
        self.assertIn("user_supplied_text_sufficient", quality["reasons"])
        self.assertNotIn("primary_material_too_short", quality["blockers"])
        self.assertIn("用户随链接提供了很长的一段材料", combined)

    def test_material_quality_gate_allows_sufficient_english_text(self) -> None:
        long_text = " ".join(
            [
                "This article explains a reusable intake pipeline with source reading,",
                "material quality checks, model composition, quality gates, storage sinks,",
                "runtime job artifacts, fallback cards, and operational review loops.",
            ]
            * 12
        )
        content = {
            "ok": True,
            "status": "webpage_rendered_extracted",
            "source_type": "webpage",
            "main_text": long_text,
        }

        quality = assess_material_quality(content, {}, {}, {}, {})

        self.assertTrue(quality["can_compose_formal"])
        self.assertGreaterEqual(quality["source_latin_word_count"], 90)
        self.assertIn("source_text", quality["primary_sources"])

    def test_material_quality_gate_allows_visual_collection_context_after_ocr_attempt(self) -> None:
        content = {
            "ok": True,
            "status": "note_extracted",
            "source_type": "mixed/example",
            "main_text": "超越你就算什么都不干只分享每天ootd都会火的，长得好看身材好又会穿，这几套衣服全部种草。",
            "description": "有我这个小可爱呀",
            "need_ocr": True,
            "image_count": 18,
        }
        ocr = {
            "ok": True,
            "status": "ocr_done",
            "merged_text": "首页\n发布\n通知\n关于我们",
            "text_items": [
                {"text": "首页"},
                {"text": "发布"},
                {"text": "通知"},
                {"text": "关于我们"},
            ],
        }

        quality = assess_material_quality(content, {}, ocr, {}, {})

        self.assertTrue(quality["can_compose_formal"])
        self.assertIn("media_metadata", quality["primary_sources"])
        self.assertIn("visual_collection_context_sufficient", quality["reasons"])
        self.assertEqual(quality["ocr_meaningful_text_length"], 0)
        self.assertNotIn("primary_material_too_short", quality["blockers"])

    def test_material_quality_gate_does_not_count_ui_ocr_noise_as_primary_text(self) -> None:
        content = {
            "ok": True,
            "status": "note_extracted",
            "source_type": "mixed/example",
            "main_text": "短图文。",
            "need_ocr": True,
            "image_count": 2,
        }
        ocr = {
            "ok": True,
            "status": "ocr_done",
            "merged_text": "首页\n发布\n通知\nLIVE\n+",
            "text_items": [
                {"text": "首页"},
                {"text": "发布"},
                {"text": "通知"},
                {"text": "LIVE"},
                {"text": "+"},
            ],
        }

        quality = assess_material_quality(content, {}, ocr, {}, {})

        self.assertFalse(quality["can_compose_formal"])
        self.assertEqual(quality["ocr_meaningful_text_length"], 0)
        self.assertIn("primary_material_too_short", quality["blockers"])
        self.assertIn("ocr_text_only_ui_or_noise", quality["blockers"])

    def test_douyin_low_material_can_trigger_web_image_ocr_fallback(self) -> None:
        content = {
            "ok": True,
            "status": "page_fetched",
            "source_type": "video/douyin",
            "original_url": "https://v.douyin.com/static/",
            "visible_text": "抖音",
            "has_video": True,
            "need_ocr": True,
        }
        transcript = {"ok": False, "status": "transcribe_failed", "has_speech": False, "video_path": ""}
        ocr = {"ok": True, "status": "skipped_no_input", "merged_text": ""}
        quality = assess_material_quality(content, transcript, ocr, {}, {})

        self.assertTrue(should_run_douyin_web_image_ocr(content, transcript, ocr, quality, {}))

    def test_visual_platform_reader_failure_triggers_web_image_ocr_without_image_count(self) -> None:
        content = {
            "ok": False,
            "status": "cdp_failed",
            "source_type": "mixed/xiaohongshu",
            "main_text": "",
            "visible_text": "",
            "image_count": 0,
        }
        classification = {"platform_id": "xiaohongshu", "source_type": "mixed/xiaohongshu"}
        material_quality = {
            "can_compose_formal": False,
            "needs_visual_enrichment": False,
            "blockers": ["primary_material_too_short", "reader_failed_without_transcript"],
        }

        self.assertTrue(
            should_run_visual_platform_web_image_ocr(content, classification, material_quality, {})
        )

    def test_visual_platform_requested_ocr_runs_even_when_material_is_formal_ready(self) -> None:
        content = {
            "ok": True,
            "status": "note_extracted",
            "source_type": "mixed/xiaohongshu",
            "main_text": "真诚的人在职场可以横着走！#职场生存有感 #人间真实 #入职",
            "visible_text": "真诚的人在职场可以横着走！#职场生存有感 #人间真实 #入职",
            "need_ocr": True,
            "has_video": True,
            "image_count": 180,
            "video_source_count": 3,
        }
        classification = {"platform_id": "xiaohongshu", "source_type": "mixed/xiaohongshu"}
        material_quality = {
            "can_compose_formal": True,
            "needs_visual_enrichment": True,
            "primary_sources": ["source_text"],
            "reasons": ["combined_source_text_sufficient"],
            "blockers": [],
        }

        self.assertTrue(
            should_run_visual_platform_web_image_ocr(content, classification, material_quality, {})
        )
        self.assertFalse(
            should_run_visual_platform_web_image_ocr(
                content,
                classification,
                material_quality,
                {"visual_platform_web_image_ocr_when_requested": False},
            )
        )

    def test_format_ocr_image_evidence_uses_captured_frame_paths(self) -> None:
        job_dir = Path(self._tmpdir.name) / "ocr-evidence-job"
        job_dir.mkdir()
        (job_dir / "ocr_material.json").write_text(json.dumps({
            "sampling": {
                "frames": [
                    {"path": "C:\\Temp\\LucasWebImageOCR\\frame_001.jpg"},
                    {"path": "C:\\Temp\\LucasWebImageOCR\\frame_002.png"},
                ],
            },
        }), encoding="utf-8")

        evidence = format_ocr_image_evidence(job_dir)

        self.assertIn("![图片证据 1](C:/Temp/LucasWebImageOCR/frame_001.jpg)", evidence)
        self.assertIn("![图片证据 2](C:/Temp/LucasWebImageOCR/frame_002.png)", evidence)

    def test_intake_platform_api_returns_status_without_cookie_material(self) -> None:
        client = TestClient(app)
        response = client.get("/api/intake/platforms", params={"url": "https://www.xiaohongshu.com/explore/abc"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["classification"]["source_type"], "mixed/xiaohongshu")
        self.assertEqual(payload["classification"]["route_id"], "hybrid_text_video_ocr")
        self.assertIn("routes", payload)
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        self.assertNotIn("cookie", serialized)
        self.assertNotIn("token", serialized)

    def test_intake_authorize_dry_run_does_not_launch_browser(self) -> None:
        client = TestClient(app)
        response = client.post("/api/intake/authorize/xiaohongshu", params={"dry_run": True}, json={})

        self.assertIn(response.status_code, {200, 503})
        payload = response.json()
        if response.status_code == 200:
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "dry_run")
            self.assertIn("authorization", payload)
        else:
            self.assertIn("Browser unavailable", payload["detail"])

    def test_reauthorize_force_reopen_closes_profile_browser_before_launch(self) -> None:
        temp_root = Path(self._tmpdir.name)
        fake_browser = temp_root / "msedge.exe"
        fake_browser.write_text("", encoding="utf-8")

        with patch.dict(os.environ, {"LOCALAPPDATA": str(temp_root)}, clear=False), \
            patch("intake_platforms.SESSION_DIR", temp_root / "sessions"), \
            patch("intake_platforms.close_profile_browser_processes", return_value={
                "ok": True,
                "matched_processes": 1,
                "closed_processes": 1,
            }) as cleanup, \
            patch("intake_platforms.start_authorization_monitor", return_value={
                "enabled": True,
                "status": "watching",
                "timeout_sec": 90,
                "poll_sec": 3,
                "close_on": "authorized_or_timeout",
                "monitor_id": "test-monitor",
            }) as monitor, \
            patch("intake_platforms.subprocess.Popen") as popen:
            payload = open_authorization(
                "xiaohongshu",
                browser_path=str(fake_browser),
                force_reopen=True,
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "browser_opened")
        self.assertTrue(payload["force_reopen"])
        self.assertEqual(payload["profile_browser_cleanup"]["closed_processes"], 1)
        cleanup.assert_called_once_with("xiaohongshu")
        popen.assert_called_once()
        monitor.assert_called_once()
        command = popen.call_args.args[0]
        self.assertIn("--new-window", command)
        self.assertTrue(any(item.startswith("--user-data-dir=") for item in command))
        self.assertTrue(payload["auto_close"]["enabled"])
        self.assertEqual(payload["auto_close"]["timeout_sec"], 90)

    def test_authorization_status_ignores_stale_random_debug_port(self) -> None:
        temp_root = Path(self._tmpdir.name)
        session_dir = temp_root / "sessions"
        session_dir.mkdir()
        (session_dir / "xiaohongshu_session.json").write_text(json.dumps({
            "status": "browser_opened",
            "debug_port": 13199,
            "debug_url": "http://127.0.0.1:13199",
        }), encoding="utf-8")
        profile = temp_root / "LucasKnowledgeDB" / "browser_profiles" / "xiaohongshu"
        profile.mkdir(parents=True)
        (profile / "Preferences").write_text("{}", encoding="utf-8")

        def fake_port_ready(port: int | None) -> bool:
            return int(port or 0) == 13199

        with patch.dict(os.environ, {"LOCALAPPDATA": str(temp_root)}, clear=False), \
            patch("intake_platforms.SESSION_DIR", session_dir), \
            patch("intake_platforms.cdp_port_ready", side_effect=fake_port_ready):
            status = authorization_status("xiaohongshu")

        self.assertEqual(status["debug_port"], 11443)
        self.assertFalse(status["debug_port_ready"])

    def test_authorization_test_does_not_spawn_probe_when_profile_process_occupies_without_fixed_cdp(self) -> None:
        temp_root = Path(self._tmpdir.name)
        fake_browser = temp_root / "msedge.exe"
        fake_browser.write_text("", encoding="utf-8")
        profile = temp_root / "LucasKnowledgeDB" / "browser_profiles" / "xiaohongshu"
        profile.mkdir(parents=True)
        (profile / "Preferences").write_text("{}", encoding="utf-8")

        with patch.dict(os.environ, {"LOCALAPPDATA": str(temp_root)}, clear=False), \
            patch("intake_platforms.SESSION_DIR", temp_root / "sessions"), \
            patch("intake_platforms.cdp_port_ready", return_value=False), \
            patch("intake_platforms.profile_browser_process_summary", return_value={
                "ok": True,
                "matched_processes": 1,
                "debug_ports": [],
                "headless_processes": 0,
            }), \
            patch("intake_platforms.subprocess.run") as run_probe:
            payload = test_authorization("xiaohongshu", browser_path=str(fake_browser), timeout_sec=4)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "cdp_unavailable")
        self.assertFalse(payload["auth_ok"])
        run_probe.assert_not_called()

    def test_intake_authorization_test_not_required_does_not_launch_browser(self) -> None:
        client = TestClient(app)
        response = client.post("/api/intake/authorize/webpage/test", json={})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "not_required")
        self.assertTrue(payload["auth_ok"])
        self.assertFalse(payload["used_mcp"])
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        self.assertNotIn("cookie", serialized)
        self.assertNotIn("token", serialized)

    def test_xiaohongshu_authorization_test_endpoint_returns_probe_result(self) -> None:
        client = TestClient(app)
        fake_result = {
            "ok": True,
            "used_mcp": False,
            "platform_id": "xiaohongshu",
            "platform_label": "小红书",
            "status": "not_authorized",
            "label": "未登录",
            "auth_ok": False,
            "page_access_ok": False,
            "probe_completed": True,
            "write_attempted": False,
            "next_step": "请点击重新授权。",
        }

        with patch("server.chat_api.test_authorization", return_value=fake_result) as probe:
            response = client.post(
                "/api/intake/authorize/xiaohongshu/test",
                json={"url": "https://www.xiaohongshu.com/explore/abc"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["platform_id"], "xiaohongshu")
        self.assertEqual(payload["status"], "not_authorized")
        self.assertTrue(payload["probe_completed"])
        self.assertFalse(payload["write_attempted"])
        probe.assert_called_once()
        self.assertEqual(probe.call_args.kwargs["url"], "https://www.xiaohongshu.com/explore/abc")
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        self.assertNotIn("cookie", serialized)
        self.assertNotIn("token", serialized)

    def test_http_api_accepts_metadata_dry_run(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/chat/messages",
            json={
                "channel": "my_chat_app",
                "conversation_id": "test-room",
                "sender_id": "lucas",
                "text": "https://v.douyin.com/xxx/",
                "metadata": {"dry_run": True},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "dry_run")
        self.assertFalse(payload["data"]["runner_called"])

    def test_ui_route_returns_minimal_chat_page(self) -> None:
        client = TestClient(app)
        response = client.get("/ui")

        self.assertEqual(response.status_code, 200)
        self.assertIn("AntTrail / 蚁迹", response.text)
        self.assertIn("API 地址", response.text)
        self.assertIn("模型配置", response.text)
        self.assertIn("配置接口地址", response.text)
        self.assertIn("测试连接", response.text)
        self.assertIn("获取模型列表", response.text)
        self.assertIn("modelOptions", response.text)
        self.assertIn("链接路由与授权", response.text)
        self.assertIn("测试授权", response.text)
        self.assertIn("authorizationTestEndpoint", response.text)
        self.assertIn("data-platform-test", response.text)
        self.assertIn("authorizationPollTimers", response.text)
        self.assertIn("授权窗口已自动关闭", response.text)
        self.assertIn("微信桥", response.text)
        self.assertIn("微信链接入库桥", response.text)
        self.assertIn("wechatBridgeBtn", response.text)
        self.assertIn("/api/wechat-bridge", response.text)
        self.assertIn("/api/storage/test", response.text)
        self.assertIn("selectedStorageTargets", response.text)
        self.assertIn("testBrainStorageBtn", response.text)
        self.assertIn("conversation_history", response.text)
        self.assertIn("agent_timeout_sec", response.text)
        self.assertIn("Agent配置", response.text)
        self.assertIn("agent_system_prompt", response.text)
        self.assertIn("添加数据库", response.text)
        self.assertIn("AntTrail Database", response.text)
        self.assertIn("打开页面", response.text)
        self.assertIn("Web URL", response.text)
        self.assertIn("能力：链接入库 + 数据库问答", response.text)
        self.assertIn("重新扫码登录", response.text)
        self.assertIn("wechatBridgeQrBox", response.text)
        self.assertIn("清除缓存", response.text)
        self.assertIn("/api/system/cache/clear", response.text)
        self.assertIn("clearMediaCache", response.text)
        self.assertNotIn("底层", response.text)
        self.assertNotIn("codex", response.text.lower())
        self.assertNotIn('data-db-field="api_key"', response.text)
        self.assertNotIn("dry-run", response.text)
        self.assertNotIn("dryRun", response.text)

    def test_cache_clear_api_only_removes_lucas_temp_cache(self) -> None:
        client = TestClient(app)
        temp_root = Path(self._tmpdir.name) / "temp"
        media_root = temp_root / "LucasVideoOCR"
        item_dir = media_root / "job-1"
        item_dir.mkdir(parents=True)
        frame_path = item_dir / "frame_001.jpg"
        frame_path.write_bytes(b"cache")
        non_lucas = temp_root / "OtherCache"
        non_lucas.mkdir()
        keep_path = non_lucas / "keep.jpg"
        keep_path.write_bytes(b"keep")

        with patch("server.chat_api.tempfile.gettempdir", return_value=str(temp_root)):
            dry_run = client.post("/api/system/cache/clear", json={"dry_run": True, "older_than_hours": 0})
            self.assertEqual(dry_run.status_code, 200)
            dry_payload = dry_run.json()
            self.assertTrue(dry_payload["ok"])
            self.assertEqual(dry_payload["status"], "dry_run")
            self.assertEqual(dry_payload["deleted_items"], 1)
            self.assertTrue(frame_path.exists())

            response = client.post("/api/system/cache/clear", json={"older_than_hours": 0})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "cleared")
        self.assertEqual(payload["deleted_items"], 1)
        self.assertFalse(item_dir.exists())
        self.assertTrue(keep_path.exists())
        self.assertFalse(payload["used_mcp"])

    def test_wechat_bridge_api_status_and_dry_run_do_not_start_process(self) -> None:
        client = TestClient(app)

        status_response = client.get("/api/wechat-bridge/status")
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.json()
        self.assertTrue(status_payload["ok"])
        self.assertIn(status_payload["status"], {"running", "stopped"})
        self.assertEqual(status_payload["mode"], "chat_gateway")
        self.assertTrue(status_payload["handles_links"])
        self.assertTrue(status_payload["handles_plain_text"])
        self.assertIn("saved_login_present", status_payload)
        self.assertFalse(status_payload["used_mcp"])
        self.assertTrue(status_payload["script_path"].endswith("start-lucas-wechat-gateway-bridge.ps1"))

        dry_run_response = client.post(
            "/api/wechat-bridge/start",
            params={"dry_run": True},
            json={},
        )
        self.assertEqual(dry_run_response.status_code, 200)
        dry_run_payload = dry_run_response.json()
        self.assertEqual(dry_run_payload["status"], "dry_run")
        self.assertTrue(dry_run_payload["would_start"])
        self.assertIn("-File", dry_run_payload["command"])
        self.assertNotIn("-Adapter", dry_run_payload["command"])
        self.assertNotIn("codex", " ".join(dry_run_payload["command"]).lower())

        force_response = client.post(
            "/api/wechat-bridge/start",
            params={"dry_run": True},
            json={"force_relogin": True},
        )
        self.assertEqual(force_response.status_code, 200)
        force_payload = force_response.json()
        self.assertTrue(force_payload["force_relogin"])
        self.assertIn("-ForceRelogin", force_payload["command"])

    def test_wechat_bridge_start_ignores_legacy_adapter_payload(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/wechat-bridge/start",
            params={"dry_run": True},
            json={"adapter": "shell"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("-Adapter", payload["command"])
        self.assertEqual(payload["mode"], "chat_gateway")

    def test_wechat_bridge_status_extracts_qr_url_from_log(self) -> None:
        client = TestClient(app)
        chat_api.WECHAT_BRIDGE_LOG.clear()
        chat_api.WECHAT_BRIDGE_LOG.append(
            "Open this QR code URL in a browser: https://liteapp.weixin.qq.com/q/test?qrcode=abc&bot_type=3"
        )

        response = client.get("/api/wechat-bridge/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["qr_code_url"], "https://liteapp.weixin.qq.com/q/test?qrcode=abc&bot_type=3")
        self.assertIn("api.qrserver.com", payload["qr_image_url"])

    def test_ui_task_queue_uses_per_item_progress_and_prunes_completed_items(self) -> None:
        client = TestClient(app)
        response = client.get("/ui")

        self.assertEqual(response.status_code, 200)
        self.assertIn("queueItemShouldRemain", response.text)
        self.assertIn("queueItemProgressValue", response.text)
        self.assertIn("task-item-progress", response.text)
        self.assertIn("task-item-stages", response.text)
        self.assertIn("renderMessageMarkdown", response.text)
        self.assertIn("renderInlineMarkdown", response.text)
        self.assertIn("note_revision", response.text)
        self.assertIn("pending_policy", response.text)
        self.assertIn("Array.isArray(item?.stages)", response.text)
        self.assertIn("item?.queue_status !== 'completed'", response.text)
        self.assertIn("textHasKnowledgeIntakeIntent", response.text)
        self.assertIn("kind: 'text_intake'", response.text)
        self.assertIn("/api/chat/messages/async", response.text)
        self.assertIn("/api/chat/batches/", response.text)
        self.assertIn("pollRealtimeBatch", response.text)
        self.assertIn("stage_label", response.text)
        self.assertNotIn("kind: 'message'", response.text)
        self.assertNotIn("taskStageList.innerHTML = stages.map", response.text)
        self.assertNotIn("已完成 ·", response.text)

    def test_ai_config_api_masks_key_and_lists_presets(self) -> None:
        client = TestClient(app)
        save_response = client.post(
            "/api/ai/config",
            json={
                "provider_id": "openai_compatible",
                "base_url": "https://relay.example.com/v1",
                "model": "custom-model",
                "api_key": "relay-secret-7788",
            },
        )

        self.assertEqual(save_response.status_code, 200)
        saved = save_response.json()
        self.assertTrue(saved["provider"]["api_key_present"])
        self.assertEqual(saved["provider"]["api_key_last4"], "7788")
        self.assertNotIn("relay-secret-7788", json.dumps(saved, ensure_ascii=False))

        providers_response = client.get("/api/ai/providers")
        self.assertEqual(providers_response.status_code, 200)
        providers = providers_response.json()["providers"]
        provider_ids = {item["id"] for item in providers}
        self.assertIn("deepseek_compatible", provider_ids)
        self.assertIn("openai", provider_ids)
        self.assertIn("openai_compatible", provider_ids)

    def test_ai_test_api_returns_structured_failure_without_key(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/ai/test",
            json={
                "provider_id": "openai_compatible",
                "base_url": "https://relay.example.com/v1",
                "model": "custom-model",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "api_key_missing")
        self.assertFalse(payload["provider"]["api_key_present"])

    def test_ai_models_api_returns_structured_failure_without_key(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/ai/models",
            json={
                "provider_id": "openai_compatible",
                "base_url": "https://relay.example.com/v1",
                "model": "custom-model",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "api_key_missing")
        self.assertEqual(payload["models"], [])
        self.assertFalse(payload["provider"]["api_key_present"])

    def test_wechat_legacy_entry_uses_gateway_in_extract_only_mode(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "wechat_link_entry.py"),
                "--message",
                "https://v.douyin.com/xxx/",
                "--extract-only",
                "--json",
            ],
            cwd=str(PROJECT_ROOT),
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=15,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["handled_by"], "link_handler")
        self.assertEqual(payload["status"], "dry_run")
        self.assertFalse(payload["data"]["runner_called"])


if __name__ == "__main__":
    unittest.main()
