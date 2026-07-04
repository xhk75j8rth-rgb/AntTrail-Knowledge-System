from __future__ import annotations

import json
import subprocess
import threading
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tools.card_composer import build_input, render_markdown
from tools.fetch_comments import node_cdp_script as comments_cdp_script
from tools.fetch_douyin_page_playwright import (
    extract_visible_douyin_metadata,
    node_cdp_script as douyin_page_cdp_script,
)
from tools.fetch_webpage import node_cdp_script as webpage_cdp_script
from tools.fetch_xiaohongshu_note import node_cdp_script as xiaohongshu_note_cdp_script
from tools.ocr_media import (
    OCR_MATERIAL_SCHEMA_NAME,
    build_material,
    dedupe_items,
    empty_sampling,
)
from tools.ocr_web_images import node_cdp_script as web_image_ocr_cdp_script
from tools.run_link_job import decide_ocr_policy, merge_douyin_page_metadata
from tools.transcribe_media import (
    dyt_fallback_reason,
    download_audio_fallback,
    download_video_fallback,
    find_video_file,
    load_douyin_audio_sources,
    load_douyin_video_sources,
    local_whisper_fallback,
    local_whisper_from_video,
    parse_dyt_output,
    run_dyt_direct,
    safe_audio_source,
    safe_video_source,
    speech_fallback_policy,
)


class OCRMaterialTests(unittest.TestCase):
    def test_cdp_page_scripts_block_media_playback(self) -> None:
        scripts = [
            web_image_ocr_cdp_script(),
            xiaohongshu_note_cdp_script(),
            douyin_page_cdp_script(),
            comments_cdp_script(),
            webpage_cdp_script(),
        ]

        for script in scripts:
            self.assertIn("mediaPlaybackGuardScript", script)
            self.assertIn("Page.addScriptToEvaluateOnNewDocument", script)
            self.assertIn("HTMLMediaElement.prototype, 'play'", script)
            self.assertIn("autoplay-policy=user-gesture-required", script)
            self.assertNotIn("autoplay-policy=no-user-gesture-required", script)
            self.assertNotIn("video.play()", script)
            self.assertNotIn(".play().catch", script)

    def test_reused_browser_extractors_use_temporary_targets_and_close_them(self) -> None:
        scripts = [web_image_ocr_cdp_script(), xiaohongshu_note_cdp_script()]

        for script in scripts:
            self.assertIn("createTemporaryTarget", script)
            self.assertIn("/json/new?", script)
            self.assertIn("method: 'PUT'", script)
            self.assertIn("/json/close/${pageTarget.id}", script)
            self.assertIn("Page.close", script)
            self.assertIn("opened_temporary_target", script)

    def test_web_image_ocr_captures_video_canvas_and_background_candidates(self) -> None:
        script = web_image_ocr_cdp_script()

        self.assertIn("querySelectorAll('video')", script)
        self.assertIn("querySelectorAll('canvas')", script)
        self.assertIn("background_image_download", script)
        self.assertIn("video_element_screenshot", script)
        self.assertIn("canvas_element_screenshot", script)

    def test_dedupe_items_uses_normalized_exact_text_first_seen(self) -> None:
        items = [
            {"frame": r"C:\tmp\frame_001.jpg", "text": "Hello  world!", "score": 0.91},
            {"frame": r"C:\tmp\frame_002.jpg", "text": "hello world", "score": 0.88},
            {"frame": r"C:\tmp\frame_003.jpg", "text": "Next step", "score": 0.93},
        ]

        deduped = dedupe_items(items)

        self.assertEqual([item["text"] for item in deduped], ["Hello world!", "Next step"])
        self.assertEqual(deduped[0]["frame_index"], 0)
        self.assertEqual(deduped[1]["frame_index"], 2)

    def test_build_material_records_schema_sampling_and_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            result = {
                "ok": True,
                "status": "ocr_done",
                "input_path": str(job_dir / "video.mp4"),
                "should_run_ocr": True,
                "force_ocr": False,
                "text_items": [{"text": "画面标题", "frame_index": 0, "score": 0.9}],
                "merged_text": "画面标题",
                "confidence": "medium",
            }
            sampling = empty_sampling(10, 180, job_dir / "frames")
            sampling.update({"strategy": "uniform_by_duration", "frame_count": 1})

            material = build_material(
                job_dir=job_dir,
                result=result,
                input_type="video",
                max_frames=10,
                timeout_sec=180,
                sampling=sampling,
                raw_text_item_count=3,
            )

            self.assertEqual(material["schema_name"], OCR_MATERIAL_SCHEMA_NAME)
            self.assertEqual(material["schema_version"], "1")
            self.assertEqual(material["sampling"]["strategy"], "uniform_by_duration")
            self.assertEqual(material["dedupe"]["raw_text_item_count"], 3)
            self.assertEqual(material["dedupe"]["deduped_text_item_count"], 1)
            self.assertEqual(material["evidence_items"][0]["text"], "画面标题")

    def test_transcribe_video_path_is_detected_from_dyt_output_or_temp_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            explicit_video = run_dir / "downloaded.mp4"
            explicit_video.write_bytes(b"fake mp4")
            output = f"Resolved: https://www.douyin.com/video/1\nVideo downloaded: {explicit_video}\n"

            info = parse_dyt_output(output)

            self.assertEqual(find_video_file(run_dir, info), str(explicit_video.resolve()))
            explicit_video.unlink()

            fallback = run_dir / "nested" / "fallback.webm"
            fallback.parent.mkdir()
            fallback.write_bytes(b"fake webm")
            self.assertEqual(find_video_file(run_dir, {"video_path": ""}), str(fallback.resolve()))

    def test_load_douyin_video_sources_filters_and_dedupes_media_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            video_url = "https://example.com/video.mp4?token=1"
            (job_dir / "douyin_page_playwright.json").write_text(json.dumps({
                "video_sources": [
                    video_url,
                    video_url,
                    "javascript:alert(1)",
                    "https://example.com/page.html",
                    "https://example.com/live.m3u8",
                ]
            }), encoding="utf-8")

            self.assertTrue(safe_video_source(video_url))
            self.assertFalse(safe_video_source("file:///C:/tmp/video.mp4"))
            self.assertEqual(load_douyin_video_sources(job_dir), ["https://example.com/live.m3u8", video_url])

    def test_load_douyin_audio_sources_filters_and_dedupes_media_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            audio_url = "https://example.com/media-audio/?mime_type=audio_mp4"
            (job_dir / "douyin_page_playwright.json").write_text(json.dumps({
                "audio_sources": [
                    audio_url,
                    audio_url,
                    "javascript:alert(1)",
                    "https://example.com/script.js",
                    "https://example.com/audio.m4a",
                ]
            }), encoding="utf-8")

            self.assertTrue(safe_audio_source(audio_url))
            self.assertFalse(safe_audio_source("file:///C:/tmp/audio.m4a"))
            self.assertEqual(load_douyin_audio_sources(job_dir), [audio_url, "https://example.com/audio.m4a"])

    def test_merge_douyin_page_metadata_fills_source_metrics_without_overwriting_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            content = {
                "original_url": "https://v.douyin.com/abc/",
                "final_url": "https://m.douyin.com/share/video/1",
                "title": "旧标题",
                "author": "",
                "published_at": "",
                "status": "page_fetched",
            }
            (job_dir / "content.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            (job_dir / "douyin_page_playwright.json").write_text(json.dumps({
                "ok": True,
                "final_url": "https://www.douyin.com/video/1",
                "title": "新标题不覆盖",
                "author": "作者A",
                "published_at": "2026-07-01T06:00:00.000Z",
                "like_count": "12000",
                "comment_count": "345",
                "collect_count": "678",
                "share_count": "90",
                "engagement_samples": ["点赞 1.2万", "评论 345"],
                "metrics_source": "douyin_page_cdp",
                "metrics_status": "success",
            }, ensure_ascii=False), encoding="utf-8")

            merged = merge_douyin_page_metadata(job_dir, content)

            self.assertEqual(merged["title"], "旧标题")
            self.assertEqual(merged["author"], "作者A")
            self.assertEqual(merged["published_at"], "2026-07-01T06:00:00.000Z")
            self.assertEqual(merged["like_count"], "12000")
            self.assertEqual(merged["comment_count"], "345")
            self.assertEqual(merged["collect_count"], "678")
            self.assertEqual(merged["share_count"], "90")
            self.assertEqual(merged["metrics_status"], "success")
            self.assertEqual(json.loads((job_dir / "content.json").read_text(encoding="utf-8"))["author"], "作者A")

    def test_visible_douyin_metadata_prefers_video_counts_and_publish_line(self) -> None:
        visible_text = "\n".join([
            "展开",
            "实测！Codex复盘智能体如何帮我拆出爆款规律！",
            "74",
            "2",
            "84",
            "7",
            "举报",
            "发布时间：2026-06-29 11:11",
            "亦恒生财（AI版）",
            "粉丝9988获赞6.4万",
            "关注",
        ])
        metadata = extract_visible_douyin_metadata(
            visible_text,
            "亦恒生财（AI版）于20260629发布在抖音，已经收获了6.4万个喜欢",
        )

        self.assertEqual(metadata["author"], "亦恒生财（AI版）")
        self.assertEqual(metadata["published_at"], "2026-06-29 11:11")
        self.assertEqual(metadata["like_count"], "74")
        self.assertEqual(metadata["comment_count"], "2")
        self.assertEqual(metadata["collect_count"], "84")
        self.assertEqual(metadata["share_count"], "7")
        self.assertEqual(metadata["metrics_status"], "success")

    def test_download_video_fallback_writes_video_to_run_temp_dir(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = b"fake mp4 bytes"
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                job_dir = Path(tmp) / "job"
                run_dir = Path(tmp) / "run"
                job_dir.mkdir()
                run_dir.mkdir()
                video_url = f"http://127.0.0.1:{server.server_port}/video.mp4"
                (job_dir / "douyin_page_playwright.json").write_text(json.dumps({
                    "video_sources": [video_url]
                }), encoding="utf-8")

                video_path, source, attempts = download_video_fallback(video_url, job_dir, run_dir, 10, "vid1")

                self.assertEqual(source, "douyin_page_video_source")
                self.assertTrue(Path(video_path).exists())
                self.assertEqual(Path(video_path).read_bytes(), b"fake mp4 bytes")
                self.assertTrue(attempts[0]["ok"])
        finally:
            server.shutdown()
            server.server_close()

    def test_download_audio_fallback_writes_audio_to_run_temp_dir(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = b"fake m4a bytes"
                self.send_response(200)
                self.send_header("Content-Type", "audio/mp4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                job_dir = Path(tmp) / "job"
                run_dir = Path(tmp) / "run"
                job_dir.mkdir()
                run_dir.mkdir()
                audio_url = f"http://127.0.0.1:{server.server_port}/audio.m4a"
                (job_dir / "douyin_page_playwright.json").write_text(json.dumps({
                    "audio_sources": [audio_url]
                }), encoding="utf-8")

                audio_path, attempts = download_audio_fallback(audio_url, job_dir, run_dir, 10, "vid1")

                self.assertTrue(Path(audio_path).exists())
                self.assertEqual(Path(audio_path).read_bytes(), b"fake m4a bytes")
                self.assertTrue(attempts[0]["ok"])
        finally:
            server.shutdown()
            server.server_close()

    def test_local_whisper_from_video_records_audio_and_transcript_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            video = run_dir / "video.mp4"
            video.write_bytes(b"fake mp4")

            with patch("tools.transcribe_media.WHISPER_CLI", video), \
                 patch("tools.transcribe_media.MODEL_PATH", video), \
                 patch("tools.transcribe_media.extract_audio_from_video") as extract_mock, \
                 patch("tools.transcribe_media.transcribe_audio_with_whisper") as whisper_mock:
                extract_mock.return_value = (True, "", 32000)

                def fake_whisper(audio_path: Path, output_base: Path, timeout_sec: int) -> tuple[bool, str, str, str]:
                    transcript_path = output_base.with_suffix(".txt")
                    transcript_path.parent.mkdir(parents=True, exist_ok=True)
                    transcript_path.write_text("本地视频音频转写成功", encoding="utf-8")
                    return True, "本地视频音频转写成功", str(transcript_path), ""

                whisper_mock.side_effect = fake_whisper

                fallback, transcript = local_whisper_from_video(str(video), run_dir, 10)

            self.assertTrue(fallback["ok"])
            self.assertEqual(transcript, "本地视频音频转写成功")
            self.assertEqual(fallback["stage"], "local_whisper_from_video")
            self.assertTrue(fallback["extract_audio"]["ok"])
            self.assertTrue(fallback["whisper"]["ok"])
            self.assertIn("local_whisper_fallback", fallback["audio_path"])

    def test_local_whisper_fallback_tries_audio_source_after_video_has_no_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            run_dir = Path(tmp) / "run"
            job_dir.mkdir()
            run_dir.mkdir()
            video = run_dir / "video.mp4"
            video.write_bytes(b"fake mp4")

            with patch("tools.transcribe_media.local_whisper_from_video") as video_mock, \
                 patch("tools.transcribe_media.download_audio_fallback") as download_audio_mock, \
                 patch("tools.transcribe_media.local_whisper_from_audio") as audio_mock:
                video_mock.return_value = ({"ok": False, "error": "Output file does not contain any stream"}, "")
                audio_file = run_dir / "audio" / "vid1-1.m4a"
                audio_file.parent.mkdir()
                audio_file.write_bytes(b"fake audio")
                download_audio_mock.return_value = (str(audio_file), [{"ok": True, "output_path": str(audio_file)}])
                audio_mock.return_value = ({
                    "ok": True,
                    "audio_path": str(audio_file),
                    "transcript_path": str(run_dir / "local_whisper_fallback" / "audio_source_transcript.txt"),
                }, "独立音频源转写成功")

                fallback, transcript = local_whisper_fallback(
                    video_path=str(video),
                    source_url="https://www.douyin.com/video/1",
                    job_dir=job_dir,
                    run_dir=run_dir,
                    timeout_sec=10,
                    video_id="1",
                )

            self.assertTrue(fallback["ok"])
            self.assertEqual(fallback["source"], "douyin_page_audio_source")
            self.assertEqual(transcript, "独立音频源转写成功")
            self.assertEqual(fallback["video_audio_attempt"]["error"], "Output file does not contain any stream")
            self.assertTrue(fallback["audio_source_attempts"][0]["ok"])

    def test_dyt_fallback_reason_marks_failure_empty_and_music_only_transcript(self) -> None:
        self.assertEqual(dyt_fallback_reason(1, "正常内容"), "dyt_exit_nonzero")
        self.assertEqual(dyt_fallback_reason(0, ""), "dyt_empty_transcript")
        self.assertEqual(dyt_fallback_reason(0, "（音乐）"), "dyt_music_only_transcript")
        self.assertEqual(dyt_fallback_reason(0, "这是一段正常口播"), "")
        self.assertEqual(speech_fallback_policy()["name"], "douyin_speech_degradation_v1")

    def test_run_dyt_direct_uses_local_fallback_when_dyt_returns_empty_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            job_dir.mkdir()
            video_path = str(Path(tmp) / "video.mp4")

            with patch("tools.transcribe_media.run_text") as run_mock, \
                 patch("tools.transcribe_media.download_video_fallback") as video_mock, \
                 patch("tools.transcribe_media.local_whisper_fallback") as fallback_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    args=["dyt"],
                    returncode=0,
                    stdout="",
                    stderr="",
                )
                video_mock.return_value = (video_path, "douyin_page_video_source", [{"ok": True}])
                fallback_mock.return_value = ({
                    "ok": True,
                    "source": "video_embedded_audio",
                    "audio_path": str(Path(tmp) / "audio.wav"),
                    "transcript_path": str(Path(tmp) / "transcript.txt"),
                }, "本地兜底口播")

                attempt, transcript = run_dyt_direct(
                    "https://www.douyin.com/video/123",
                    job_dir,
                    10,
                    "1-original_url",
                )

            self.assertEqual(transcript, "本地兜底口播")
            self.assertTrue(attempt["fallback_triggered"])
            self.assertEqual(attempt["fallback_reason"], "dyt_empty_transcript")
            self.assertTrue(attempt["fallback_ok"])
            self.assertEqual(attempt["fallback_stage"], "video_embedded_audio")
            self.assertEqual(attempt["transcribe_source"], "video_embedded_audio")
            self.assertEqual(attempt["failed_reason"], None)
            fallback_mock.assert_called_once()

    def test_run_dyt_direct_retries_video_download_after_audio_fallback_populates_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            job_dir.mkdir()
            retry_video_path = str(Path(tmp) / "retry-video.mp4")

            with patch("tools.transcribe_media.run_text") as run_mock, \
                 patch("tools.transcribe_media.download_video_fallback") as video_mock, \
                 patch("tools.transcribe_media.local_whisper_fallback") as fallback_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    args=["dyt"],
                    returncode=1,
                    stdout="Resolved: https://www.douyin.com/video/123\n",
                    stderr="downloading audio failed",
                )
                video_mock.side_effect = [
                    ("", "", [{"status": "no_video_sources"}]),
                    (retry_video_path, "douyin_page_video_source", [{"ok": True, "output_path": retry_video_path}]),
                ]
                fallback_mock.return_value = ({
                    "ok": True,
                    "source": "douyin_page_audio_source",
                    "audio_path": str(Path(tmp) / "audio.m4a"),
                    "transcript_path": str(Path(tmp) / "transcript.txt"),
                }, "音频源兜底口播")

                attempt, transcript = run_dyt_direct(
                    "https://v.douyin.com/abc/",
                    job_dir,
                    10,
                    "1-original_url",
                )

            self.assertEqual(transcript, "音频源兜底口播")
            self.assertEqual(video_mock.call_count, 2)
            self.assertEqual(attempt["video_path"], retry_video_path)
            self.assertEqual(attempt["video_path_source"], "douyin_page_video_source+local_whisper_fallback")
            self.assertEqual(attempt["video_download_attempts"], [{"ok": True, "output_path": retry_video_path}])
            self.assertEqual(
                attempt["local_whisper_fallback"]["post_audio_video_download_attempts"],
                [{"ok": True, "output_path": retry_video_path}],
            )

    def test_run_dyt_direct_recovers_video_after_successful_dyt_without_video_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            job_dir.mkdir()
            recovered_video_path = str(Path(tmp) / "recovered-video.mp4")
            transcript_file = Path(tmp) / "transcript.txt"
            transcript_file.write_text("有效口播转写", encoding="utf-8")

            with patch("tools.transcribe_media.run_text") as run_mock, \
                 patch("tools.transcribe_media.find_video_file") as find_video_mock, \
                 patch("tools.transcribe_media.download_video_fallback") as video_mock, \
                 patch("tools.transcribe_media.recover_visual_video_after_speech") as recovery_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    args=["dyt"],
                    returncode=0,
                    stdout="",
                    stderr="Resolved: https://www.douyin.com/video/123\nAudio downloaded: C:\\tmp\\a.mp3\n",
                )
                find_video_mock.return_value = ""
                video_mock.return_value = ("", "", [{"status": "no_video_sources"}])
                recovery_mock.return_value = (
                    recovered_video_path,
                    "douyin_page_video_source",
                    [{"status": "no_video_sources"}, {"ok": True, "output_path": recovered_video_path}],
                    {"ok": True, "reason": "video_downloaded_for_ocr"},
                )

                def transcript_side_effect(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                    output_arg = args[0]
                    output_path = Path(output_arg[output_arg.index("--output") + 1])
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text("有效口播转写", encoding="utf-8")
                    return run_mock.return_value

                run_mock.side_effect = transcript_side_effect

                attempt, transcript = run_dyt_direct(
                    "https://v.douyin.com/abc/",
                    job_dir,
                    10,
                    "1-original_url",
                )

            self.assertEqual(transcript, "有效口播转写")
            self.assertFalse(attempt["fallback_triggered"])
            self.assertEqual(attempt["video_path"], recovered_video_path)
            self.assertEqual(attempt["video_path_source"], "douyin_page_video_source")
            self.assertTrue(attempt["video_recovery_after_dyt_success"]["ok"])
            recovery_mock.assert_called_once()

    def test_douyin_video_with_local_video_raises_ocr_priority(self) -> None:
        decision = decide_ocr_policy(
            content={"title": "普通标题", "description": "", "visible_text": ""},
            transcript={
                "status": "transcribed",
                "has_speech": True,
                "confidence": "medium",
                "transcript": "这是一段足够长的口播转写内容，用来模拟正常的 Level 3 材料。",
                "video_path": r"C:\tmp\video.mp4",
            },
            config={"enable_ocr": True, "ocr_policy": "conditional"},
            source_type="video/douyin",
        )

        self.assertTrue(decision["should_run_ocr"])
        self.assertIn("video_source_with_local_media", decision["triggered_by"])
        self.assertEqual(decision["blocked_by"], [])

    def test_sparse_douyin_material_requests_ocr_even_when_video_path_is_missing(self) -> None:
        decision = decide_ocr_policy(
            content={"title": "", "description": "", "visible_text": ""},
            transcript={
                "status": "transcribed",
                "has_speech": True,
                "confidence": "medium",
                "transcript": "这是一段足够长的口播转写内容，但页面材料为空，所以需要进入 OCR 审计路径。",
                "video_path": "",
            },
            config={"enable_ocr": True, "ocr_policy": "conditional"},
            source_type="video/douyin",
        )

        self.assertTrue(decision["should_run_ocr"])
        self.assertIn("video_source_sparse_page_material", decision["triggered_by"])
        self.assertIn("video_path_missing", decision["blocked_by"])
        self.assertEqual(decision["reason"], "ocr_requested_but_video_path_missing")

    def test_plain_webpage_without_visual_signal_still_skips_ocr(self) -> None:
        decision = decide_ocr_policy(
            content={"title": "普通文章", "description": "文字页面", "visible_text": "这是一篇普通网页文章。"},
            transcript={},
            config={"enable_ocr": True, "ocr_policy": "conditional"},
            source_type="webpage",
        )

        self.assertFalse(decision["should_run_ocr"])
        self.assertEqual(decision["reason"], "conditional_ocr_policy_did_not_match")

    def test_enable_ocr_false_keeps_conditional_ocr_disabled(self) -> None:
        decision = decide_ocr_policy(
            content={"need_ocr": True, "title": "PPT 录屏"},
            transcript={"video_path": r"C:\tmp\video.mp4"},
            config={"enable_ocr": False, "ocr_policy": "always"},
            source_type="video/douyin",
        )

        self.assertFalse(decision["should_run_ocr"])
        self.assertIn("enable_ocr_false", decision["blocked_by"])

    def test_force_ocr_overrides_disabled_config_for_manual_audit(self) -> None:
        decision = decide_ocr_policy(
            content={"title": "普通标题"},
            transcript={"video_path": r"C:\tmp\video.mp4"},
            config={"enable_ocr": False, "ocr_policy": "disabled"},
            source_type="video/douyin",
            force_ocr=True,
        )

        self.assertTrue(decision["should_run_ocr"])
        self.assertIn("force_ocr", decision["triggered_by"])
        self.assertIn("enable_ocr_false_overridden_by_force", decision["triggered_by"])

    def test_card_composer_reads_ocr_material_v1_as_visual_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "input.json").write_text(json.dumps({
                "url": "https://v.douyin.com/abc/",
                "job_id": "job1",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "content.json").write_text(json.dumps({
                "original_url": "https://v.douyin.com/abc/",
                "final_url": "https://www.douyin.com/video/1",
                "title": "测试视频",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "transcript.json").write_text(json.dumps({
                "transcript": "口播内容",
                "has_speech": True,
                "status": "transcribed",
                "video_id": "1",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "comments.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "ocr.json").write_text(json.dumps({
                "status": "ocr_done",
                "merged_text": "旧OCR文本",
                "text_items": [{"text": "旧OCR文本"}],
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "ocr_material.json").write_text(json.dumps({
                "schema_name": "OCRMaterialV1",
                "schema_version": "1",
                "status": "ocr_done",
                "merged_text": "画面标题\n关键按钮",
                "evidence_items": [
                    {
                        "text": "画面标题",
                        "frame_index": 0,
                        "frame_path": str(job_dir / "frames" / "frame_001.jpg"),
                        "score": 0.91,
                    },
                    {
                        "text": "关键按钮",
                        "frame_index": 1,
                        "frame_path": str(job_dir / "frames" / "frame_002.jpg"),
                        "score": 0.88,
                    },
                ],
                "sampling": {
                    "strategy": "uniform_by_duration",
                    "frame_count": 2,
                    "frames": [
                        {"frame_index": 0, "path": str(job_dir / "frames" / "frame_001.jpg"), "timestamp_sec": 0},
                        {"frame_index": 1, "path": str(job_dir / "frames" / "frame_002.jpg"), "timestamp_sec": 12},
                    ],
                },
                "dedupe": {"strategy": "normalized_exact_text_first_seen"},
                "source": {"input_path": str(job_dir / "video.mp4")},
            }, ensure_ascii=False), encoding="utf-8")

            input_payload = build_input(job_dir)

            self.assertEqual(input_payload["ocr"]["material_schema"], "OCRMaterialV1")
            self.assertEqual(input_payload["ocr"]["merged_text"], "画面标题\n关键按钮")
            self.assertEqual(input_payload["ocr"]["evidence_texts"], ["画面标题", "关键按钮"])
            self.assertEqual(input_payload["ocr"]["evidence_entries"][0]["frame_index"], 0)
            self.assertIn("frame_001.jpg", input_payload["ocr"]["evidence_entries"][0]["frame_path"])
            self.assertTrue(input_payload["ocr"]["evidence_entries"][0]["image_worth_saving"])
            self.assertEqual(input_payload["ocr"]["evidence_selection"]["max_images"], 2)
            markdown = render_markdown({
                "display_title": "测试卡",
                "content_level": "Level 4 画面 OCR 增强级",
                "quality_level": "high",
                "model_provider": "mock",
                "model_used": "mock",
                "original_summary": "摘要",
                "one_sentence_summary": "一句话",
                "core_points": ["一", "二", "三"],
                "knowledge_blocks": [],
                "application_suggestions": [],
                "follow_up_actions": [],
                "methodology": [],
                "comment_signals": {},
                "reusable_value": [],
                "risks": [],
                "tags": ["OCR"],
                "evidence_quotes": [],
            }, input_payload)
            self.assertIn("## 画面文字证据", markdown)
            self.assertIn("画面标题", markdown)
            self.assertIn("关键按钮", markdown)
            self.assertIn("![OCR frame 1]", markdown)
            self.assertIn("frame_001.jpg", markdown)
            self.assertIn("展示关键帧数：2", markdown)

    def test_card_composer_includes_source_engagement_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "input.json").write_text(json.dumps({
                "url": "https://v.douyin.com/abc/",
                "job_id": "job-metrics",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "content.json").write_text(json.dumps({
                "original_url": "https://v.douyin.com/abc/",
                "final_url": "https://www.douyin.com/video/1",
                "title": "有互动指标的视频",
                "author": "作者A",
                "published_at": "2026-07-01T06:00:00.000Z",
                "like_count": "12000",
                "comment_count": "345",
                "collect_count": "678",
                "share_count": "90",
                "metrics_source": "douyin_page_cdp",
                "metrics_status": "success",
                "engagement_samples": ["点赞 1.2万", "评论 345"],
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "transcript.json").write_text(json.dumps({
                "transcript": "这是一段有效口播，介绍一个实用技巧。",
                "has_speech": True,
                "status": "transcribed",
                "video_id": "1",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "comments.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "ocr.json").write_text(json.dumps({"status": "skipped_not_needed"}, ensure_ascii=False), encoding="utf-8")

            input_payload = build_input(job_dir)
            markdown = render_markdown({
                "display_title": "测试卡",
                "content_level": "Level 3 视频口播转写级",
                "quality_level": "high",
                "model_provider": "mock",
                "model_used": "mock",
                "original_summary": "摘要",
                "one_sentence_summary": "一句话",
                "core_points": ["一", "二", "三"],
                "knowledge_blocks": [],
                "application_suggestions": [],
                "follow_up_actions": [],
                "methodology": [],
                "comment_signals": {},
                "reusable_value": [],
                "risks": [],
                "tags": ["互动"],
                "evidence_quotes": [],
            }, input_payload)

            self.assertEqual(input_payload["source"]["author"], "作者A")
            self.assertEqual(input_payload["source"]["like_count"], "12000")
            self.assertEqual(input_payload["source"]["collect_count"], "678")
            self.assertIn("点赞数可提示", input_payload["source"]["engagement_interpretation"][0])
            self.assertIn("- 点赞数：1.2万", markdown)
            self.assertIn("- 评论数：345", markdown)
            self.assertIn("- 收藏数：678", markdown)
            self.assertIn("- 分享 / 转发数：90", markdown)
            self.assertIn("收藏数量更接近实用性", markdown)

    def test_card_composer_skips_ordinary_spoken_subtitle_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "input.json").write_text(json.dumps({
                "url": "https://v.douyin.com/abc/",
                "job_id": "job1",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "content.json").write_text(json.dumps({
                "original_url": "https://v.douyin.com/abc/",
                "final_url": "https://www.douyin.com/video/1",
                "title": "口播视频",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "transcript.json").write_text(json.dumps({
                "transcript": "听他们讲跟我分享，就是前端获客很容易，一到转化就是不行。",
                "has_speech": True,
                "status": "transcribed",
                "video_id": "1",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "comments.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "ocr.json").write_text(json.dumps({"status": "ocr_done"}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "ocr_material.json").write_text(json.dumps({
                "schema_name": "OCRMaterialV1",
                "schema_version": "1",
                "status": "ocr_done",
                "merged_text": "听他们讲跟\n就是前端获客很容易\nCBX\n不要听他付害忍悠0",
                "evidence_items": [
                    {"text": "听他们讲跟", "frame_index": 0, "frame_path": str(job_dir / "frames" / "frame_001.jpg"), "score": 0.99},
                    {"text": "就是前端获客很容易", "frame_index": 1, "frame_path": str(job_dir / "frames" / "frame_002.jpg"), "score": 0.97},
                    {"text": "CBX", "frame_index": 2, "frame_path": str(job_dir / "frames" / "frame_003.jpg"), "score": 0.91},
                    {"text": "不要听他付害忍悠0", "frame_index": 3, "frame_path": str(job_dir / "frames" / "frame_004.jpg"), "score": 0.84},
                ],
                "sampling": {
                    "strategy": "uniform_by_duration",
                    "frame_count": 4,
                    "frames": [
                        {"frame_index": 0, "path": str(job_dir / "frames" / "frame_001.jpg"), "timestamp_sec": 0},
                        {"frame_index": 1, "path": str(job_dir / "frames" / "frame_002.jpg"), "timestamp_sec": 10},
                        {"frame_index": 2, "path": str(job_dir / "frames" / "frame_003.jpg"), "timestamp_sec": 20},
                        {"frame_index": 3, "path": str(job_dir / "frames" / "frame_004.jpg"), "timestamp_sec": 30},
                    ],
                },
                "dedupe": {"strategy": "normalized_exact_text_first_seen"},
                "source": {"input_path": str(job_dir / "video.mp4")},
            }, ensure_ascii=False), encoding="utf-8")

            input_payload = build_input(job_dir)

            self.assertEqual(input_payload["ocr"]["evidence_entries"], [])
            markdown = render_markdown({
                "display_title": "测试卡",
                "content_level": "Level 4 画面 OCR 增强级",
                "quality_level": "high",
                "model_provider": "mock",
                "model_used": "mock",
                "original_summary": "摘要",
                "one_sentence_summary": "一句话",
                "core_points": ["一", "二", "三"],
                "knowledge_blocks": [],
                "application_suggestions": [],
                "follow_up_actions": [],
                "methodology": [],
                "comment_signals": {},
                "reusable_value": [],
                "risks": [],
                "tags": ["OCR"],
                "evidence_quotes": [],
            }, input_payload)
            self.assertIn("已跳过普通口播字幕帧", markdown)
            self.assertNotIn("![OCR frame", markdown)

    def test_card_composer_renders_sampled_images_when_ocr_text_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "input.json").write_text(json.dumps({
                "url": "https://www.xiaohongshu.com/explore/demo",
                "job_id": "job-visual-samples",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "content.json").write_text(json.dumps({
                "original_url": "https://www.xiaohongshu.com/explore/demo",
                "final_url": "https://www.xiaohongshu.com/explore/demo",
                "title": "图片笔记",
                "main_text": "这是一段短正文。",
                "image_count": 12,
                "need_ocr": True,
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "transcript.json").write_text(json.dumps({
                "transcript": "",
                "has_speech": False,
                "status": "skipped",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "comments.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "ocr.json").write_text(json.dumps({"status": "ocr_done"}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "ocr_material.json").write_text(json.dumps({
                "schema_name": "OCRMaterialV1",
                "schema_version": "1",
                "status": "ocr_done",
                "merged_text": "",
                "evidence_items": [],
                "sampling": {
                    "strategy": "web_visible_image_sources_then_screenshots",
                    "frame_count": 2,
                    "frames": [
                        {
                            "frame_index": 0,
                            "path": str(job_dir / "frames" / "web_image_001.webp"),
                            "source_url": "https://cdn.example.com/a.webp",
                            "capture_method": "source_image_download",
                        },
                        {
                            "frame_index": 1,
                            "path": str(job_dir / "frames" / "web_image_002.webp"),
                            "source_url": "https://cdn.example.com/b.webp",
                            "capture_method": "source_image_download",
                        },
                    ],
                },
                "dedupe": {"raw_text_item_count": 0, "deduped_text_item_count": 0},
                "source": {"input_path": str(job_dir / "frames")},
            }, ensure_ascii=False), encoding="utf-8")

            input_payload = build_input(job_dir)
            markdown = render_markdown({
                "display_title": "测试卡",
                "content_level": "Level 2 页面可见内容级",
                "quality_level": "high",
                "model_provider": "mock",
                "model_used": "mock",
                "original_summary": "摘要",
                "one_sentence_summary": "一句话",
                "core_points": ["一", "二", "三"],
                "knowledge_blocks": [],
                "application_suggestions": [],
                "follow_up_actions": [],
                "methodology": [],
                "comment_signals": {},
                "reusable_value": [],
                "risks": [],
                "tags": ["OCR"],
                "evidence_quotes": [],
            }, input_payload)

            self.assertIn("OCR 抽样图片", markdown)
            self.assertIn("未提取到画面文字证据", markdown)
            self.assertIn("![OCR sampled image 1]", markdown)
            self.assertIn("https://cdn.example.com/a.webp", markdown)
            self.assertIn("不代表模型已完成图片语义理解", markdown)

    def test_card_composer_prioritizes_semantic_visual_evidence_over_showreel_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            frames_dir = job_dir / "frames"
            (job_dir / "input.json").write_text(json.dumps({
                "url": "https://v.douyin.com/gsap/",
                "job_id": "job-gsap",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "content.json").write_text(json.dumps({
                "original_url": "https://v.douyin.com/gsap/",
                "final_url": "https://www.douyin.com/video/7644217361217719562",
                "title": "GSAP 视频",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "transcript.json").write_text(json.dumps({
                "transcript": "GSAP 官方亲自下场开源专属 AI 技能包。",
                "has_speech": True,
                "status": "transcribed",
                "video_id": "7644217361217719562",
            }, ensure_ascii=False), encoding="utf-8")
            (job_dir / "comments.json").write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "ocr.json").write_text(json.dumps({"status": "ocr_done"}, ensure_ascii=False), encoding="utf-8")
            (job_dir / "ocr_material.json").write_text(json.dumps({
                "schema_name": "OCRMaterialV1",
                "schema_version": "1",
                "status": "ocr_done",
                "merged_text": "\n".join([
                    "024 Showreel 2024 Sho",
                    "Showreel 2024 Showree",
                    "地表最强动画库GSAP官方亲自下场",
                    "greensock / gsap-skills ☆4.7k",
                    "标准的官方级动画代码",
                ]),
                "evidence_items": [
                    {"text": "024 Showreel 2024 Sho", "frame_index": 0, "frame_path": str(frames_dir / "frame_001.jpg"), "score": 0.98522},
                    {"text": "Showreel 2024 Showree", "frame_index": 0, "frame_path": str(frames_dir / "frame_001.jpg"), "score": 0.99764},
                    {"text": "地表最强动画库GSAP官方亲自下场", "frame_index": 1, "frame_path": str(frames_dir / "frame_002.jpg"), "score": 0.99836},
                    {"text": "greensock / gsap-skills ☆4.7k", "frame_index": 2, "frame_path": str(frames_dir / "frame_003.jpg"), "score": 0.95651},
                    {"text": "< > Code", "frame_index": 2, "frame_path": str(frames_dir / "frame_003.jpg"), "score": 0.88681},
                    {"text": "标准的官方级动画代码", "frame_index": 5, "frame_path": str(frames_dir / "frame_006.jpg"), "score": 0.99995},
                ],
                "sampling": {
                    "strategy": "uniform_by_duration",
                    "frame_count": 6,
                    "frames": [
                        {"frame_index": 0, "path": str(frames_dir / "frame_001.jpg"), "timestamp_sec": 0},
                        {"frame_index": 1, "path": str(frames_dir / "frame_002.jpg"), "timestamp_sec": 3},
                        {"frame_index": 2, "path": str(frames_dir / "frame_003.jpg"), "timestamp_sec": 6},
                        {"frame_index": 5, "path": str(frames_dir / "frame_006.jpg"), "timestamp_sec": 15},
                    ],
                },
                "dedupe": {"strategy": "normalized_exact_text_first_seen"},
                "source": {"input_path": str(job_dir / "video.mp4")},
            }, ensure_ascii=False), encoding="utf-8")

            input_payload = build_input(job_dir)

            entries = input_payload["ocr"]["evidence_entries"]
            self.assertEqual(len(entries), 2)
            self.assertEqual(len({entry["frame_index"] for entry in entries}), 2)
            selected_texts = [entry["text"] for entry in entries]
            self.assertNotIn("024 Showreel 2024 Sho", selected_texts)
            self.assertNotIn("Showreel 2024 Showree", selected_texts)
            self.assertIn("地表最强动画库GSAP官方亲自下场", selected_texts)
            self.assertTrue(
                {"greensock / gsap-skills ☆4.7k", "标准的官方级动画代码"}.intersection(selected_texts)
            )


if __name__ == "__main__":
    unittest.main()
