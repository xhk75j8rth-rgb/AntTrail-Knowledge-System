from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from tools.write_lucas_database import (
    REQUEST_FILENAME,
    RESULT_FILENAME,
    build_ingest_payload,
    validate_ready_for_ingest,
)
from taxonomy_layer import TaxonomyRouter


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_job(job_dir: Path, *, gate_passed: bool = True) -> None:
    card = {
        "schema_name": "ComposedCardV1",
        "schema_version": "1",
        "card_type": "formal_summary",
        "content_level": "Level 5 评论与互动增强级",
        "quality_level": "high",
        "source_title": "source title",
        "display_title": "AI 写作实践复盘",
        "safe_filename_title": "2026-06-28_AI写作实践复盘",
        "one_sentence_summary": "summary",
        "original_summary": "original summary",
        "core_points": ["point one", "point two", "point three"],
        "knowledge_blocks": [
            {"concept": "concept one", "explanation": "explanation one", "evidence": "evidence one", "reusable_value": "reusable one"},
            {"concept": "concept two", "explanation": "explanation two", "evidence": "evidence two", "reusable_value": "reusable two"},
        ],
        "methodology": ["method"],
        "application_suggestions": ["suggestion"],
        "follow_up_actions": ["action"],
        "comment_signals": {"comments_hash": "hash1"},
        "reusable_value": ["value"],
        "risks": ["risk"],
        "tags": ["AI写作"],
        "evidence_quotes": ["quote"],
        "appendix_transcript_excerpt": "excerpt",
        "comments_job_id": "job1",
        "comments_video_id": "video1",
        "comments_hash": "hash1",
        "composer_input_comments_hash": "hash1",
        "composed_card_comments_hash": "hash1",
        "composer_status": "success",
        "composer_error": "",
        "model_used": "deepseek-chat",
        "model_provider": "deepseek_compatible",
    }
    write_json(job_dir / "composed_card.json", card)
    write_json(job_dir / "quality_gate.json", {
        "schema_name": "ComposedCardV1",
        "schema_version": "1",
        "quality_gate_passed": gate_passed,
        "failed_checks": [] if gate_passed else ["bad_card"],
        "quality_level": "high" if gate_passed else "low",
        "comments_hash": "hash1",
    })
    write_json(job_dir / "taxonomy_decision.json", TaxonomyRouter().route_card(card))
    write_json(job_dir / "composer_input.json", {
        "source": {
            "source_url": "https://v.douyin.com/abc/",
            "final_url": "https://www.douyin.com/video/1",
            "source_title": "source title",
            "author": "author",
            "publish_time": "unknown",
            "video_id": "video1",
            "job_id": "job1",
        },
        "transcript": {"raw_transcript": "transcript text", "transcript_length": 15, "has_speech": True, "status": "transcribed"},
        "comments": {
            "comments_count": 1,
            "comment_items": [{"id": "c1", "author": "a", "text": "comment", "like_count": 1}],
            "comments_hash": "hash1",
        },
        "ocr": {"ocr_status": "not_run", "merged_text": ""},
    })
    write_json(job_dir / "content.json", {"source_type": "video/douyin", "title": "source title"})
    write_json(job_dir / "result.json", {
        "job_id": "job1",
        "url": "https://v.douyin.com/abc/",
        "source_type": "video/douyin",
        "written_card_source": "composed_card.md",
    })
    (job_dir / "composed_card.md").write_text("# AI 写作实践复盘\n\nsummary", encoding="utf-8")


def make_temporary_job(job_dir: Path, *, card_type: str = "temporary_card") -> None:
    markdown_name = "extracted_source_card.md" if card_type == "extracted_source_card" else "card.md"
    title = "短图文来源材料"
    write_json(job_dir / "content.json", {
        "source_type": "mixed/xiaohongshu",
        "title": title,
        "visible_text": "这是一次材料不足的测试卡，只保留来源材料和处理状态。",
        "main_text": "这是一次材料不足的测试卡，只保留来源材料和处理状态。",
        "status": "source_material_incomplete",
    })
    write_json(job_dir / "result.json", {
        "job_id": "job-temp-1",
        "url": "https://www.xiaohongshu.com/explore/abc",
        "source_type": "mixed/xiaohongshu",
        "card_type": card_type,
        "title": title,
        "content_level": "Level 1/2 临时材料级",
        "written_card_source": markdown_name,
        "quality_gate": {
            "quality_gate_passed": False,
            "failed_checks": ["primary_material_too_short"],
            "downgrade_reason": "primary_material_too_short",
            "final_card_type": card_type,
            "used_mcp": False,
        },
    })
    (job_dir / markdown_name).write_text(
        f"# {title}\n\n> 卡片类型：{card_type}\n\n来源材料不足，等待补 OCR 或人工材料。",
        encoding="utf-8",
    )


class LucasDatabaseSinkTests(unittest.TestCase):
    def test_build_ingest_payload_uses_composed_card_gate_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_job(job_dir)

            payload, diagnostics = build_ingest_payload(job_dir)

            self.assertEqual(payload["card"]["schema_name"], "ComposedCardV1")
            self.assertTrue(payload["quality_gate"]["passed"])
            self.assertIn("# AI 写作实践复盘", payload["rendered_views"]["markdown"])
            self.assertEqual(payload["source_material"]["source_type"], "video/douyin")
            self.assertEqual(payload["taxonomy_decision"]["schema_name"], "TaxonomyDecisionV1")
            self.assertEqual(payload["dedupe"]["schema_name"], "DedupeDecisionV1")
            self.assertEqual(payload["dedupe"]["policy"], "prefer_existing_source_then_content_fingerprint")
            self.assertTrue(payload["dedupe"]["source_identity_hash"])
            self.assertTrue(payload["dedupe"]["content_fingerprint"])
            self.assertEqual(payload["relations"], [])
            self.assertEqual(payload["target_path"], "/知识卡/AI/内容生产/AI 写作/AI写作实践复盘")
            self.assertTrue(diagnostics["markdown_path"].endswith("composed_card.md"))

    def test_source_material_preserves_platform_engagement_when_comment_samples_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_job(job_dir)
            composer_input = read_json(job_dir / "composer_input.json")
            composer_input["source"].update({
                "like_count": "16000",
                "comment_count": "724",
                "collect_count": "1831",
                "share_count": "8953",
                "metrics_source": "xiaohongshu_note_cdp",
                "metrics_status": "partial",
            })
            composer_input["comments"] = {
                "comments_count": 0,
                "comment_items": [],
                "comments_hash": "hash1",
            }
            write_json(job_dir / "composer_input.json", composer_input)

            payload, _ = build_ingest_payload(job_dir)
            material = payload["source_material"]
            metadata = material["metadata"]

            self.assertEqual(material["engagement"]["like_count"], "16000")
            self.assertEqual(material["engagement"]["comment_count"], "724")
            self.assertEqual(material["engagement"]["collect_count"], "1831")
            self.assertEqual(material["engagement"]["share_count"], "8953")
            self.assertEqual(material["comment_count"], "724")
            self.assertEqual(metadata["comments_count"], 0)
            self.assertEqual(metadata["platform_comment_count"], "724")
            self.assertEqual(metadata["metrics_source"], "xiaohongshu_note_cdp")

    def test_temporary_card_requires_allow_non_formal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_temporary_job(job_dir, card_type="temporary_card")

            strict_payload, strict_diagnostics = build_ingest_payload(job_dir)
            strict_errors = validate_ready_for_ingest(strict_payload)
            relaxed_payload, relaxed_diagnostics = build_ingest_payload(job_dir, allow_non_formal=True)
            relaxed_errors = validate_ready_for_ingest(relaxed_payload, allow_non_formal=True)

            self.assertIn("composed_card_schema_not_ComposedCardV1", strict_errors)
            self.assertEqual(relaxed_errors, [])
            self.assertEqual(relaxed_payload["card"]["card_type"], "temporary_card")
            self.assertFalse(relaxed_payload["quality_gate"]["passed"])
            self.assertTrue(relaxed_payload["target_path"].startswith("/知识卡/Inbox/临时卡/"))
            self.assertFalse(strict_diagnostics["synthetic_card"])
            self.assertTrue(relaxed_diagnostics["synthetic_card"])

    def test_extracted_source_card_keeps_non_formal_type_and_inbox_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_temporary_job(job_dir, card_type="extracted_source_card")

            payload, diagnostics = build_ingest_payload(job_dir, allow_non_formal=True)
            errors = validate_ready_for_ingest(payload, allow_non_formal=True)

            self.assertEqual(errors, [])
            self.assertEqual(payload["card"]["card_type"], "extracted_source_card")
            self.assertFalse(payload["quality_gate"]["passed"])
            self.assertEqual(payload["target_path"], "/知识卡/Inbox/来源材料/短图文来源材料")
            self.assertTrue(diagnostics["markdown_path"].endswith("extracted_source_card.md"))

    def test_build_ingest_payload_uses_taxonomy_for_gsap_instead_of_tag_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_job(job_dir)
            card = read_json(job_dir / "composed_card.json")
            card.update({
                "display_title": "GSAP Skills：官方动画知识包进入 AI 编程工作流",
                "safe_filename_title": "2026-07-01_GSAP_Skills_AI动画工作流",
                "one_sentence_summary": (
                    "材料介绍 GSAP 官方开源 gsap-skills，将核心 API、时间轴、ScrollTrigger、"
                    "插件和前端框架用法整理成 AI Skills。"
                ),
                "core_points": [
                    "GSAP Skills 把官方动画用法转化为 AI 编程代理可读取的技能文件。",
                    "内容重点仍是前端动画、交互动效和 ScrollTrigger 等 GSAP 使用场景。",
                    "AI 工具只是前端动画开发工作流中的辅助层。",
                ],
                "tags": ["GSAP", "AI编程", "前端开发", "动画工程", "Agent Skills", "ScrollTrigger"],
            })
            write_json(job_dir / "composed_card.json", card)
            write_json(job_dir / "taxonomy_decision.json", TaxonomyRouter().route_card(card))

            payload, _ = build_ingest_payload(job_dir)

            self.assertEqual(
                payload["taxonomy_decision"]["recommended_path"],
                ["软件工程", "前端开发", "动画与交互"],
            )
            self.assertEqual(
                payload["target_path"],
                "/知识卡/软件工程/前端开发/动画与交互/GSAP_Skills_AI动画工作流",
            )
            self.assertNotIn("/知识卡/GSAP/", payload["target_path"])

    def test_ocr_payload_uses_selected_visual_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_job(job_dir)
            write_json(job_dir / "composer_input.json", {
                "source": {
                    "source_url": "https://v.douyin.com/abc/",
                    "final_url": "https://www.douyin.com/video/1",
                    "source_title": "source title",
                    "job_id": "job1",
                    "video_id": "video1",
                },
                "transcript": {"raw_transcript": "transcript text", "transcript_length": 15, "has_speech": True, "status": "transcribed"},
                "comments": {"comments_count": 0, "comment_items": [], "comments_hash": "hash1"},
                "ocr": {
                    "ocr_status": "ocr_done",
                    "merged_text": "字幕一\n关键数字 30万",
                    "evidence_entries": [
                        {
                            "text": "关键数字 30万",
                            "frame_index": 0,
                            "frame_path": r"C:\Temp\frame_001.jpg",
                            "timestamp_sec": 0,
                            "score": 0.98,
                            "image_worth_saving": True,
                            "visual_value_reason": "contains_numeric_or_business_value_signal",
                        }
                    ],
                    "evidence_items": [
                        {"text": "字幕一", "frame_index": 1, "frame_path": r"C:\Temp\frame_002.jpg", "score": 0.99}
                    ],
                },
            })

            payload, _ = build_ingest_payload(job_dir)
            items = payload["source_material"]["ocr_evidence_items"]

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["text"], "关键数字 30万")
            self.assertTrue(items[0]["image_worth_saving"])
            self.assertEqual(items[0]["visual_value_reason"], "contains_numeric_or_business_value_signal")

    def test_ocr_markdown_local_frame_paths_are_rewritten_to_asset_urls(self) -> None:
        old_asset_base = os.environ.get("LUCAS_ASSET_BASE_URL")
        try:
            os.environ["LUCAS_ASSET_BASE_URL"] = "http://127.0.0.1:3963"
            with tempfile.TemporaryDirectory() as tmp:
                job_dir = Path(tmp)
                make_job(job_dir)
                frames_dir = job_dir / "frames"
                frames_dir.mkdir()
                frame_path = frames_dir / "frame_001.jpg"
                frame_path.write_bytes(b"fake-jpeg")
                local_markdown_path = str(frame_path).replace("\\", "/")
                (job_dir / "composed_card.md").write_text(
                    f"# AI 写作实践复盘\n\n![OCR sampled image 1](<{local_markdown_path}>)",
                    encoding="utf-8",
                )
                write_json(job_dir / "ocr_material.json", {
                    "schema_name": "OCRMaterialV1",
                    "schema_version": "1",
                    "sampling": {
                        "frames": [
                            {"frame_index": 0, "path": str(frame_path)},
                        ],
                    },
                    "evidence_items": [],
                })

                payload, diagnostics = build_ingest_payload(job_dir)

                markdown = payload["rendered_views"]["markdown"]
                self.assertNotIn(local_markdown_path, markdown)
                self.assertIn(
                    f"http://127.0.0.1:3963/api/jobs/{job_dir.name}/ocr-images/frame_001.jpg",
                    markdown,
                )
                self.assertEqual(diagnostics["asset_base_url"], "http://127.0.0.1:3963")
        finally:
            if old_asset_base is None:
                os.environ.pop("LUCAS_ASSET_BASE_URL", None)
            else:
                os.environ["LUCAS_ASSET_BASE_URL"] = old_asset_base

    def test_ocr_payload_does_not_fallback_to_raw_items_when_selection_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_job(job_dir)
            write_json(job_dir / "composer_input.json", {
                "source": {"source_url": "https://v.douyin.com/abc/", "job_id": "job1", "video_id": "video1"},
                "transcript": {"raw_transcript": "transcript text", "transcript_length": 15, "has_speech": True, "status": "transcribed"},
                "comments": {"comments_count": 0, "comment_items": [], "comments_hash": "hash1"},
                "ocr": {
                    "ocr_status": "ocr_done",
                    "merged_text": "普通口播字幕",
                    "evidence_entries": [],
                    "evidence_items": [
                        {"text": "普通口播字幕", "frame_index": 1, "frame_path": r"C:\Temp\frame_002.jpg", "score": 0.99}
                    ],
                },
            })

            payload, _ = build_ingest_payload(job_dir)

            self.assertEqual(payload["source_material"]["ocr_evidence_items"], [])

    def test_validate_rejects_failed_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_job(job_dir, gate_passed=False)

            payload, _ = build_ingest_payload(job_dir)
            errors = validate_ready_for_ingest(payload)

            self.assertIn("quality_gate_not_passed", errors)

    def test_cli_missing_token_writes_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            make_job(job_dir)
            env = os.environ.copy()
            env["LUCAS_DB_API_KEY"] = ""
            env["LUCAS_DB_API_TOKEN"] = ""
            env["LUCAS_STORAGE_CONFIG_PATH"] = str(Path(tmp) / "missing_storage.local.json")
            env["LUCAS_STORAGE_ENV_PATH"] = str(Path(tmp) / "missing.env")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "tools" / "write_lucas_database.py"),
                    "--job-dir",
                    str(job_dir),
                ],
                cwd=str(PROJECT_ROOT),
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=15,
                env=env,
            )

            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            result = json.loads((job_dir / RESULT_FILENAME).read_text(encoding="utf-8"))
            self.assertFalse(result["ok"])
            self.assertEqual(result["stage"], "auth")
            self.assertIn("storage configuration", result["error"])
            self.assertTrue((job_dir / REQUEST_FILENAME).exists())

    def test_cli_reads_url_and_key_from_storage_config(self) -> None:
        requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "json": json.loads(body),
                })
                payload = json.dumps({"ok": True, "card_id": "card_job_1"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                job_dir = root / "job"
                job_dir.mkdir()
                make_job(job_dir)
                storage_path = root / "storage.local.json"
                env_path = root / ".env"
                storage_path.write_text(json.dumps({
                    "active_provider": "lucas_database",
                    "providers": {
                        "lucas_database": {
                            "base_url": f"http://127.0.0.1:{server.server_port}",
                            "endpoint": "/api/cards/ingest",
                        },
                    },
                }, ensure_ascii=False), encoding="utf-8")
                env_path.write_text("LUCAS_DB_API_KEY=configured-storage-key\n", encoding="utf-8")
                env = os.environ.copy()
                env["LUCAS_STORAGE_CONFIG_PATH"] = str(storage_path)
                env["LUCAS_STORAGE_ENV_PATH"] = str(env_path)

                completed = subprocess.run(
                    [
                        sys.executable,
                        str(PROJECT_ROOT / "tools" / "write_lucas_database.py"),
                        "--job-dir",
                        str(job_dir),
                    ],
                    cwd=str(PROJECT_ROOT),
                    shell=False,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=15,
                    env=env,
                )

                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]["path"], "/api/cards/ingest")
                self.assertEqual(requests[0]["authorization"], "Bearer configured-storage-key")
                self.assertEqual(requests[0]["json"]["card"]["schema_name"], "ComposedCardV1")
                result = json.loads((job_dir / RESULT_FILENAME).read_text(encoding="utf-8"))
                self.assertTrue(result["ok"])
                self.assertEqual(result["card_id"], "card_job_1")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
