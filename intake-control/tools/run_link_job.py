#!/usr/bin/env python
"""
V1 link job runner.

Douyin URLs follow the highest reachable automated path without MCP:
page metadata -> DryRun + transcription -> conditional OCR -> comments check
-> formal card when transcript is valid, otherwise a temporary/failure card.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "link_pipeline.json"
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "link_pipeline.example.json"
MAX_USER_SUPPLIED_TEXT_CHARS = 30000

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from taxonomy_layer import TaxonomyRouter
from intake_platforms import classify_url
from storage_config import resolve_lucas_database_runtimes


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_config(path: Path) -> tuple[dict[str, Any], Path, str | None]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), path, None
    if path == DEFAULT_CONFIG and EXAMPLE_CONFIG.exists():
        return json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8")), EXAMPLE_CONFIG, (
            "config/link_pipeline.json not found; using example config."
        )
    raise FileNotFoundError(f"Config not found: {path}")


def make_job_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def append_log(job_dir: Path, message: str) -> None:
    with (job_dir / "job.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{now_iso()} {message}\n")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def emit_json(data: dict[str, Any]) -> None:
    try:
        print(json.dumps(data, ensure_ascii=True, indent=2))
    except OSError:
        pass


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_ocr_bundle(job_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    ocr = read_json(job_dir / "ocr.json")
    material = read_json(job_dir / "ocr_material.json")
    if not material:
        return ocr, {}
    bundled = dict(ocr)
    bundled.setdefault("ok", material.get("ok"))
    bundled.setdefault("status", material.get("status"))
    bundled.setdefault("merged_text", material.get("merged_text") or "")
    bundled.setdefault("text_items", material.get("evidence_items") or [])
    bundled.setdefault("confidence", material.get("confidence") or "low")
    source = material.get("source") if isinstance(material.get("source"), dict) else {}
    sampling = material.get("sampling") if isinstance(material.get("sampling"), dict) else {}
    bundled.setdefault("input_path", source.get("input_path") or "")
    bundled.setdefault("frames_dir", sampling.get("frames_dir") or "")
    bundled.setdefault("frame_count", sampling.get("frame_count") or 0)
    bundled["ocr_material_schema"] = material.get("schema_name") or ""
    bundled["ocr_material_path"] = str(job_dir / "ocr_material.json")
    bundled["sampling_strategy"] = sampling.get("strategy") or ""
    return bundled, material


def attach_ocr_decision_to_material(job_dir: Path, material: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    if not material:
        return {}
    policy = material.get("policy") if isinstance(material.get("policy"), dict) else {}
    policy["decision"] = decision
    material["policy"] = policy
    write_json(job_dir / "ocr_material.json", material)
    return material


def update_status(job_dir: Path, status: str, extra: dict[str, Any] | None = None) -> None:
    payload = {"status": status, "updated_at": now_iso()}
    if extra:
        payload.update(extra)
    write_json(job_dir / "status.json", payload)
    append_log(job_dir, f"status={status}")


def is_douyin_url(url: str) -> bool:
    text = url.casefold()
    return any(marker in text for marker in ("v.douyin.com", "m.douyin.com", "www.douyin.com", "iesdouyin.com"))


SENSITIVE_QUERY_MARKERS = ("token", "secret", "key", "auth", "session", "cookie", "sid")


def redact_url(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return url
    query_items = []
    redacted = False
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if any(marker in key.casefold() for marker in SENSITIVE_QUERY_MARKERS):
            query_items.append((key, "<redacted>"))
            redacted = True
        else:
            query_items.append((key, value))
    query = urlencode(query_items, doseq=True) if query_items else ""
    return urlunparse(parsed._replace(query=query, fragment="" if redacted else parsed.fragment))


def redact_command_arg(value: Any) -> str:
    text = str(value)
    return redact_url(text) if text.startswith(("http://", "https://")) else text


def classify_link(url: str) -> dict[str, Any]:
    try:
        classification = classify_url(url)
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "route_id": "basic_webpage",
            "platform_id": "webpage",
            "source_type": "webpage",
            "route_basis": "classification_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "used_mcp": False,
        }
    if not classification.get("ok"):
        classification.setdefault("route_id", "basic_webpage")
        classification.setdefault("platform_id", "webpage")
        classification.setdefault("source_type", "webpage")
    return classification


def classification_for_artifact(classification: dict[str, Any]) -> dict[str, Any]:
    safe = dict(classification)
    if safe.get("url"):
        safe["url"] = redact_url(str(safe.get("url") or ""))
    return safe


def detect_source_type(url: str) -> str:
    classification = classify_link(url)
    source_type = str(classification.get("source_type") or "").strip()
    return source_type or ("video/douyin" if is_douyin_url(url) else "webpage")


def is_xiaohongshu_route(classification: dict[str, Any]) -> bool:
    return classification.get("platform_id") == "xiaohongshu" and classification.get("route_id") == "hybrid_text_video_ocr"


def is_toutiao_article_route(classification: dict[str, Any]) -> bool:
    return classification.get("platform_id") == "toutiao" and classification.get("route_id") == "text_extract_then_analyze"


def is_basic_webpage_route(classification: dict[str, Any]) -> bool:
    return classification.get("route_id") == "basic_webpage"


def is_invalid_url(url: str) -> tuple[bool, str | None]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return True, "URL must include http(s) scheme and host."
    if parsed.hostname and parsed.hostname.casefold().endswith(".invalid"):
        return True, "Reserved .invalid domain; treated as unreachable in V1."
    return False, None


def summarize_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or "unknown-link"
    tail = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""
    raw = f"{host}_{tail}" if tail else host
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in raw)
    return safe[:48].strip("_") or "link"


def short_text(text: str, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


EMBEDDED_URL_RE = re.compile(r"https?://[^\s<>\"'“”\]\)]+", re.IGNORECASE)
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)(token[\"'\s:=]+)([A-Za-z0-9_\-./+=]{8,})"),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)([A-Za-z0-9_\-./+=]{8,})"),
    re.compile(r"(?i)(cookie[\"'\s:=]+)([^\s,;]{8,})"),
)


def normalize_user_supplied_text(text: str, limit: int = MAX_USER_SUPPLIED_TEXT_CHARS) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    normalized = "\n".join(line for line in lines if line).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip()


def mask_sensitive_source_text(text: str) -> str:
    output = text or ""
    output = EMBEDDED_URL_RE.sub(lambda match: redact_url(match.group(0)), output)
    for pattern in SENSITIVE_TEXT_PATTERNS:
        output = pattern.sub(r"\1***", output)
    return output


def read_user_supplied_source_text(args: argparse.Namespace) -> str:
    parts: list[str] = []
    inline_text = str(getattr(args, "source_text_inline", "") or "")
    if inline_text:
        parts.append(inline_text)
    source_text_file = str(getattr(args, "source_text_file", "") or "").strip()
    if source_text_file:
        path = Path(source_text_file)
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            if isinstance(payload, dict):
                value = payload.get("source_text") or payload.get("text") or payload.get("message_text") or ""
                parts.append(str(value or ""))
            else:
                parts.append(str(payload or ""))
        except OSError:
            parts.append("")
    return normalize_user_supplied_text(mask_sensitive_source_text("\n".join(part for part in parts if part)))


def attach_user_supplied_text(content: dict[str, Any], source_text: str) -> dict[str, Any]:
    text = normalize_user_supplied_text(source_text)
    if not text:
        return content
    merged = dict(content)
    merged["user_supplied_text"] = text
    merged["user_supplied_text_length"] = len(text)
    merged["user_supplied_text_source"] = "message_text"
    sources = merged.get("source_text_sources")
    if not isinstance(sources, list):
        sources = []
    if "message_text" not in sources:
        sources.append("message_text")
    merged["source_text_sources"] = sources
    return merged


def attach_user_supplied_text_to_content_file(job_dir: Path, content: dict[str, Any], source_text: str) -> dict[str, Any]:
    merged = attach_user_supplied_text(content, source_text)
    if merged != content:
        write_json(job_dir / "content.json", merged)
    return merged


def merge_douyin_page_metadata(job_dir: Path, content: dict[str, Any]) -> dict[str, Any]:
    page = read_json(job_dir / "douyin_page_playwright.json")
    if not page:
        return content
    merged = dict(content)
    for key in (
        "final_url",
        "title",
        "author",
        "published_at",
        "description",
        "visible_text",
        "like_count",
        "comment_count",
        "collect_count",
        "share_count",
        "metrics_source",
        "metrics_status",
    ):
        value = page.get(key)
        if value not in (None, "", [], {}):
            if key in {"like_count", "comment_count", "collect_count", "share_count", "metrics_source", "metrics_status"}:
                merged[key] = value
            elif key == "final_url" or not merged.get(key) or str(merged.get(key) or "").strip() == "抖音":
                merged[key] = value
    samples = page.get("engagement_samples")
    if isinstance(samples, list) and samples:
        merged["engagement_samples"] = samples[:8]
    if page.get("ok") and merged.get("status") in (None, "", "page_fetch_failed"):
        merged["status"] = "page_fetched"
    if page.get("ok"):
        merged["douyin_page_metadata_status"] = page.get("metrics_status") or "page_fetched"
    else:
        merged["douyin_page_metadata_status"] = page.get("status") or "unavailable"
        if page.get("error"):
            merged["douyin_page_metadata_error"] = short_text(str(page.get("error") or ""), 240)
    if merged != content:
        write_json(job_dir / "content.json", merged)
    return merged


def format_count(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未读取到"
    try:
        number = int(float(text))
    except (TypeError, ValueError):
        return text
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    if number >= 10_000:
        return f"{number / 10_000:.1f}万"
    return str(number)


def engagement_signal_lines(source: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    try:
        like_count = int(float(source.get("like_count") or 0))
    except (TypeError, ValueError):
        like_count = 0
    try:
        comment_count = int(float(source.get("comment_count") or 0))
    except (TypeError, ValueError):
        comment_count = 0
    try:
        collect_count = int(float(source.get("collect_count") or 0))
    except (TypeError, ValueError):
        collect_count = 0
    try:
        share_count = int(float(source.get("share_count") or 0))
    except (TypeError, ValueError):
        share_count = 0

    if like_count:
        signals.append("点赞数可提示选题、表达或创意吸引力，数值越高越值得关注，但不能单独证明内容正确。")
    if comment_count:
        signals.append("评论数量可作为争议、困惑或讨论需求的弱信号，需要结合评论样本判断。")
    if collect_count:
        signals.append("收藏数量更接近实用性和可复用价值信号，适合优先检查方法步骤。")
    if share_count:
        signals.append("分享 / 转发数量可提示传播性，但仍需区分娱乐传播和知识价值。")
    return signals or ["未读取到足够互动指标，暂不判断传播、争议或实用性。"]


def explain_failure(code: str | None) -> str:
    mapping = {
        "script_missing": "脚本不存在",
        "dyt_missing": "dyt 不存在",
        "whisper_cli_missing": "whisper-cli 不存在",
        "model_missing": "模型不存在",
        "ffmpeg_unavailable": "ffmpeg 不可用",
        "url_resolution_failed": "链接解析失败",
        "media_fetch_failed": "音频 / 视频获取失败",
        "whisper_failed": "whisper 转写失败",
        "dry_run_failed": "DryRun 失败",
        "timeout": "转写超时",
        "no_speech_detected": "transcript 只有音乐或未检测到有效口播",
        "empty_transcript": "transcript 为空",
        "transcribe_failed": "转写失败",
    }
    if not code:
        return "内容不足"
    return mapping.get(str(code), str(code))


def bulletize_from_transcript(transcript: str, max_items: int = 6) -> list[str]:
    chunks = re.split(r"[。！？!?；;\n]+", transcript or "")
    items = []
    for chunk in chunks:
        chunk = short_text(chunk, 120)
        if len(chunk) < 8:
            continue
        items.append(chunk)
        if len(items) >= max_items:
            break
    return items or [short_text(transcript, 180)] if transcript else []


def call_tool(job_dir: Path, label: str, cmd: list[str], timeout: int | None = None) -> tuple[int, dict[str, Any]]:
    command_preview = " ".join(redact_command_arg(arg) for arg in cmd[:4])
    append_log(job_dir, f"run {label}: {command_preview} ...")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    completed = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    append_log(job_dir, f"{label} exit_code={completed.returncode}")
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if stderr:
        append_log(job_dir, f"{label} stderr_tail={stderr[-800:]}")
    try:
        data = json.loads(stdout.strip() or "{}")
    except json.JSONDecodeError:
        data = {"ok": False, "status": f"{label}_invalid_json", "stdout_tail": stdout[-1000:], "used_mcp": False}
    return completed.returncode, data


def call_writer(config_path: Path, title: str, card_path: Path, job_dir: Path) -> tuple[int, dict[str, Any]]:
    return call_tool(job_dir, "write_siyuan.py", [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "write_siyuan.py"),
        "--config",
        str(config_path),
        "--title",
        title,
        "--markdown",
        str(card_path),
        "--job-dir",
        str(job_dir),
    ])


def safe_artifact_part(value: Any, fallback: str = "target") -> str:
    text = str(value or fallback).strip().casefold().replace("-", "_")
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in text)
    safe = "_".join(part for part in safe.split("_") if part)
    return safe[:48] or fallback


def call_lucas_database_writer(
    job_dir: Path,
    timeout: int,
    *,
    allow_non_formal: bool = False,
    target_id: str = "",
) -> tuple[int, dict[str, Any]]:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "write_lucas_database.py"),
        "--job-dir",
        str(job_dir),
        "--timeout-sec",
        str(timeout),
    ]
    if allow_non_formal:
        cmd.append("--allow-non-formal")
    if target_id:
        suffix = safe_artifact_part(target_id, "database")
        cmd.extend([
            "--target-id",
            target_id,
            "--result-filename",
            f"write_lucas_database_{suffix}_result.json",
            "--request-filename",
            f"lucas_database_{suffix}_ingest_request.json",
        ])
    return call_tool(job_dir, "write_lucas_database.py", cmd, timeout=timeout + 30)


def _truthy_config(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _resolve_project_path(value: Any, fallback: str) -> Path:
    raw = str(value or fallback).strip() or fallback
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def refresh_taxonomy_tree_snapshot(config_path: Path | None, config: dict[str, Any], job_dir: Path) -> None:
    if not _truthy_config(config, "taxonomy_refresh_from_siyuan", False):
        return
    output_path = _resolve_project_path(config.get("taxonomy_tree_path"), "runtime/taxonomy_tree.json")
    timeout = config_int(config, "taxonomy_refresh_timeout_sec", 30)
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "export_siyuan_taxonomy_tree.py"),
        "--config",
        str(config_path or DEFAULT_CONFIG),
        "--output",
        str(output_path),
        "--max-depth",
        str(config_int(config, "taxonomy_tree_max_depth", 4)),
    ]
    if "taxonomy_export_root_path" in config:
        root = str(config.get("taxonomy_export_root_path") or "/").strip()
    elif "siyuan_taxonomy_root_path" in config:
        root = str(config.get("siyuan_taxonomy_root_path") or "/").strip()
    else:
        root = str(config.get("knowledge_card_root_path") or "").strip()
    if root:
        cmd.extend(["--root-path", root])
    exit_code, data = call_tool(job_dir, "export_siyuan_taxonomy_tree.py", cmd, timeout=timeout + 10)
    if exit_code != 0 or not data.get("ok"):
        append_log(job_dir, f"taxonomy_tree_refresh_failed exit_code={exit_code} stage={data.get('stage')} error={short_text(str(data.get('error') or ''), 240)}")
        return
    append_log(job_dir, f"taxonomy_tree_refreshed output={data.get('output')} category_count={data.get('category_count')}")


def call_composer(
    job_dir: Path,
    composed_json: Path,
    composed_markdown: Path,
    timeout: int,
    max_attempts: int = 3,
) -> tuple[int, dict[str, Any]]:
    max_attempts = max(1, int(max_attempts or 1))
    process_timeout = (timeout * max_attempts) + 30
    return call_tool(job_dir, "card_composer.py", [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "card_composer.py"),
        "--job-dir",
        str(job_dir),
        "--output-json",
        str(composed_json),
        "--output-markdown",
        str(composed_markdown),
        "--timeout-sec",
        str(timeout),
        "--max-attempts",
        str(max_attempts),
    ], timeout=process_timeout)


def call_quality_gate(job_dir: Path, composed_json: Path, composed_markdown: Path, quality_gate_path: Path) -> tuple[int, dict[str, Any]]:
    return call_tool(job_dir, "card_quality_gate.py", [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "card_quality_gate.py"),
        "--job-dir",
        str(job_dir),
        "--composed-json",
        str(composed_json),
        "--composed-markdown",
        str(composed_markdown),
        "--output",
        str(quality_gate_path),
    ])


def route_taxonomy_for_formal_card(
    job_dir: Path,
    card_type: str,
    quality_gate: dict[str, Any],
    config: dict[str, Any] | None = None,
    config_path: Path | None = None,
) -> tuple[dict[str, Any], str]:
    if card_type != "formal_summary" or not quality_gate.get("quality_gate_passed"):
        return {}, ""

    refresh_taxonomy_tree_snapshot(config_path, config or {}, job_dir)

    composed_json = job_dir / "composed_card.json"
    if not composed_json.exists():
        error = "composed_card_json_missing"
        append_log(job_dir, f"taxonomy_route_failed error={error}")
        return {}, error

    try:
        decision = TaxonomyRouter.from_config(config or {}).route_card(read_json(composed_json))
        write_json(job_dir / "taxonomy_decision.json", decision)
        append_log(
            job_dir,
            "taxonomy_routed "
            f"path={decision.get('recommended_path')} "
            f"confidence={decision.get('confidence')} "
            f"source={decision.get('taxonomy_source') or 'seed'}",
        )
        return decision, ""
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        append_log(job_dir, f"taxonomy_route_failed error={error}")
        return {}, error


OCR_VISUAL_KEYWORDS = ("ppt", "代码", "流程图", "屏幕", "录屏", "字幕", "工具演示", "界面", "看板", "表格", "白板")


def normalize_ocr_policy(config: dict[str, Any]) -> str:
    policy = str(config.get("ocr_policy") or "conditional").strip().casefold()
    if policy in {"always", "force", "force_all"}:
        return "always"
    if policy in {"disabled", "disable", "off", "never", "false", "none"}:
        return "disabled"
    return "conditional"


def config_int(config: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(config.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def config_bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def normalize_storage_targets(config: dict[str, Any]) -> list[str]:
    if "enable_storage_write" in config and not config_bool(config, "enable_storage_write", True):
        return []
    if "storage_write_enabled" in config and not config_bool(config, "storage_write_enabled", True):
        return []
    raw = config.get("storage_targets", config.get("storage_target"))
    if raw is None:
        targets = ["lucas_database"]
        if config_bool(config, "enable_lucas_database_write", False):
            targets = ["lucas_database"]
        return targets
    if isinstance(raw, str):
        if raw.strip().casefold() in {"", "none", "off", "disabled", "local_only", "local-only"}:
            return []
        items = [item.strip().casefold() for item in re.split(r"[,，\s]+", raw) if item.strip()]
    elif isinstance(raw, list):
        items = [str(item).strip().casefold() for item in raw if str(item).strip()]
    else:
        items = []
    targets: list[str] = []
    aliases = {
        "brain": "lucas_database",
        "lucas-db": "lucas_database",
        "lucas_db": "lucas_database",
        "database": "lucas_database",
        "db": "lucas_database",
        "siyuan": "siyuan",
        "思源": "siyuan",
        "none": "none",
        "off": "none",
        "disabled": "none",
        "local_only": "none",
        "local-only": "none",
    }
    for item in items:
        if item == "both":
            normalized_items = ["siyuan", "lucas_database"]
        elif aliases.get(item, item) == "none":
            normalized_items = []
        else:
            normalized_items = [aliases.get(item, item)]
        for normalized in normalized_items:
            if normalized in {"siyuan", "lucas_database"} and normalized not in targets:
                targets.append(normalized)
    return targets


def storage_target_enabled(config: dict[str, Any], target: str) -> bool:
    return target in normalize_storage_targets(config)


def lucas_database_write_policy(config: dict[str, Any]) -> str:
    policy = str(config.get("lucas_database_write_policy") or "").strip().casefold()
    if policy in {"formal_only", "formal-only", "strict", "quality_gate", "quality-gate"}:
        return "formal_only"
    if policy in {"all_cards", "all-cards", "all", "testing", "test"}:
        return "all_cards"
    return "all_cards" if storage_target_enabled(config, "lucas_database") else "formal_only"


def call_writer_if_enabled(config_path: Path, title: str, card_path: Path, job_dir: Path, config: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    if not storage_target_enabled(config, "siyuan"):
        return None, {
            "ok": False,
            "stage": "storage_skipped",
            "skipped": True,
            "skipped_reason": "disabled_by_storage_targets",
            "used_mcp": False,
        }
    return call_writer(config_path, title, card_path, job_dir)


def apply_lucas_database_write_policy(
    result: dict[str, Any],
    config: dict[str, Any],
    job_dir: Path,
    write_result: dict[str, Any],
    card_type: str,
    quality_gate: dict[str, Any],
) -> dict[str, Any]:
    enabled = storage_target_enabled(config, "lucas_database")
    policy = lucas_database_write_policy(config)
    formal_ready = card_type == "formal_summary" and bool(quality_gate.get("quality_gate_passed"))
    allow_non_formal = policy == "all_cards" and not formal_ready
    result.update({
        "storage_targets": normalize_storage_targets(config),
        "lucas_database_write_enabled": enabled,
        "lucas_database_write_policy": policy,
        "lucas_database_allow_non_formal": allow_non_formal,
    })
    if not enabled:
        result.update({
            "lucas_database_write_result": {},
            "lucas_database_writer_exit_code": None,
            "lucas_database_write_ok": False,
            "lucas_database_write_skipped_reason": "disabled_by_storage_targets",
        })
        return result
    if not formal_ready and policy != "all_cards":
        result.update({
            "lucas_database_write_result": {},
            "lucas_database_writer_exit_code": None,
            "lucas_database_write_ok": False,
            "lucas_database_write_skipped_reason": "requires_formal_summary_and_quality_gate_passed",
        })
        return result

    try:
        runtimes = resolve_lucas_database_runtimes()
    except Exception as exc:
        result.update({
            "lucas_database_write_result": {},
            "lucas_database_write_results": [],
            "lucas_database_writer_exit_code": None,
            "lucas_database_write_ok": False,
            "lucas_database_write_skipped_reason": f"database_targets_unavailable: {type(exc).__name__}: {exc}",
        })
        return result
    if not runtimes:
        result.update({
            "lucas_database_write_result": {},
            "lucas_database_write_results": [],
            "lucas_database_writer_exit_code": None,
            "lucas_database_write_ok": False,
            "lucas_database_write_skipped_reason": "no_database_targets_selected",
        })
        return result

    write_json(job_dir / "result.json", result)
    update_status(job_dir, "writing_lucas_database", {
        "siyuan_write_ok": bool(write_result.get("ok")),
        "write_stage": write_result.get("stage"),
        "lucas_database_write_policy": policy,
        "allow_non_formal": allow_non_formal,
        "database_targets": [runtime.get("target_id") for runtime in runtimes],
    })
    lucas_database_timeout = config_int(config, "lucas_database_timeout_sec", 30)
    per_target_results: list[dict[str, Any]] = []
    for runtime in runtimes:
        target_id = str(runtime.get("target_id") or "")
        target_label = str(runtime.get("label") or target_id or "Lucas Database")
        lucas_database_exit, lucas_database_result = call_lucas_database_writer(
            job_dir,
            lucas_database_timeout,
            allow_non_formal=allow_non_formal,
            target_id=target_id,
        )
        per_target_results.append({
            "target_id": target_id,
            "label": target_label,
            "base_url": runtime.get("base_url"),
            "exit_code": lucas_database_exit,
            "ok": bool(lucas_database_result.get("ok")),
            "result": lucas_database_result,
        })
    primary = per_target_results[0] if per_target_results else {}
    primary_result = primary.get("result") if isinstance(primary.get("result"), dict) else {}
    all_ok = bool(per_target_results) and all(item.get("ok") for item in per_target_results)
    any_ok = any(item.get("ok") for item in per_target_results)
    result.update({
        "lucas_database_write_result": primary_result,
        "lucas_database_write_results": per_target_results,
        "lucas_database_writer_exit_code": primary.get("exit_code"),
        "lucas_database_write_ok": all_ok,
        "lucas_database_write_partial_ok": any_ok and not all_ok,
        "lucas_database_write_skipped_reason": "" if any_ok else "all_database_targets_failed",
    })
    return result


def ocr_content_text(content: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "description", "visible_text", "summary", "text"):
        value = content.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
        elif value:
            parts.append(str(value))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def is_video_ocr_source(content: dict[str, Any], transcript: dict[str, Any], source_type: str = "") -> bool:
    source_bits = [
        source_type,
        str(content.get("source_type") or ""),
        str(transcript.get("source_type") or ""),
        str(content.get("original_url") or ""),
        str(content.get("final_url") or ""),
        str(content.get("url") or ""),
        str(transcript.get("url") or ""),
    ]
    text = " ".join(source_bits).casefold()
    return text.startswith("video") or "video/" in text or "douyin" in text


def decide_ocr_policy(
    content: dict[str, Any],
    transcript: dict[str, Any],
    config: dict[str, Any],
    *,
    source_type: str = "",
    force_ocr: bool = False,
) -> dict[str, Any]:
    policy = normalize_ocr_policy(config)
    content_text = ocr_content_text(content)
    content_text_lower = content_text.casefold()
    transcript_text = str(transcript.get("transcript") or "")
    transcript_status = str(transcript.get("status") or "")
    confidence = str(transcript.get("confidence") or "").casefold()
    has_video_path = bool(str(transcript.get("video_path") or "").strip())
    video_source = is_video_ocr_source(content, transcript, source_type)
    sparse_threshold = config_int(config, "ocr_sparse_content_threshold", 80)
    short_transcript_threshold = config_int(config, "ocr_short_transcript_threshold", 40)
    sparse_content = len(content_text) < sparse_threshold
    weak_transcript = (
        not bool(transcript.get("has_speech"))
        or transcript_status not in {"transcribed", "ok"}
        or confidence == "low"
        or len(transcript_text.strip()) < short_transcript_threshold
    )
    keyword_hits = [keyword for keyword in OCR_VISUAL_KEYWORDS if keyword.casefold() in content_text_lower]
    triggered_by: list[str] = []
    blocked_by: list[str] = []

    if video_source and not has_video_path:
        blocked_by.append("video_path_missing")

    enabled = bool(config.get("enable_ocr", True))
    decision = {
        "should_run_ocr": False,
        "policy": policy,
        "enabled": enabled,
        "source_type": source_type or ("video" if video_source else "webpage"),
        "has_video_path": has_video_path,
        "reason": "",
        "triggered_by": triggered_by,
        "blocked_by": blocked_by,
        "content_text_length": len(content_text),
        "transcript_text_length": len(transcript_text.strip()),
        "transcript_status": transcript_status,
        "transcript_confidence": confidence,
    }

    if force_ocr:
        triggered_by.append("force_ocr")
        if not enabled:
            triggered_by.append("enable_ocr_false_overridden_by_force")
        if policy == "disabled":
            triggered_by.append("ocr_policy_disabled_overridden_by_force")
    elif not enabled:
        blocked_by.append("enable_ocr_false")
        decision["reason"] = "ocr_disabled_by_config"
        return decision

    if not force_ocr and policy == "disabled":
        blocked_by.append("ocr_policy_disabled")
        decision["reason"] = "ocr_policy_disabled"
        return decision

    if policy == "always" and not force_ocr:
        triggered_by.append("ocr_policy_always")
    elif not force_ocr:
        if content.get("need_ocr"):
            triggered_by.append("content_need_ocr")
        if transcript.get("need_ocr"):
            triggered_by.append("transcript_need_ocr")
        if keyword_hits:
            triggered_by.append(f"visual_keyword:{keyword_hits[0]}")
        if video_source and has_video_path:
            triggered_by.append("video_source_with_local_media")
        if video_source and sparse_content:
            triggered_by.append("video_source_sparse_page_material")
        if video_source and weak_transcript:
            triggered_by.append("video_source_weak_transcript")

    if triggered_by:
        decision["should_run_ocr"] = True
        decision["reason"] = (
            "ocr_requested_but_video_path_missing"
            if "video_path_missing" in blocked_by
            else f"ocr_requested_by_{triggered_by[0]}"
        )
    else:
        decision["reason"] = "conditional_ocr_policy_did_not_match"
    return decision


def should_run_ocr(content: dict[str, Any], transcript: dict[str, Any], config: dict[str, Any]) -> bool:
    return bool(decide_ocr_policy(content, transcript, config).get("should_run_ocr"))


def content_level(transcript: dict[str, Any], ocr: dict[str, Any], comments: dict[str, Any]) -> str:
    level = "Level 2 页面可见内容级"
    if transcript.get("has_speech"):
        level = "Level 3 视频口播转写级"
    if ocr.get("merged_text"):
        level = "Level 4 画面 OCR 增强级"
    if comments.get("comments") or comments.get("signals"):
        level = "Level 5 评论与互动增强级"
    return level


def format_comment_signals(comments: dict[str, Any]) -> str:
    signals = comments.get("signals") or []
    if not signals:
        return "未读取到可用评论信号。"
    lines = []
    for signal in signals[:5]:
        category = signal.get("category") or "评论信号"
        count = signal.get("count") or 0
        examples = "；".join(short_text(str(item), 80) for item in (signal.get("examples") or [])[:2])
        suffix = f"；例：{examples}" if examples else ""
        lines.append(f"- {category}：{count} 条{suffix}")
    return "\n".join(lines)


def format_comment_samples(comments: dict[str, Any]) -> str:
    items = comments.get("comments") or []
    if not items:
        return "未读取到评论样本。"
    lines = []
    for item in items[:5]:
        author = item.get("author") or "匿名用户"
        likes = item.get("like_count") or 0
        lines.append(f"- {author}：{short_text(str(item.get('text') or ''), 120)}（赞 {likes}）")
    return "\n".join(lines)


def source_title(url: str, content: dict[str, Any]) -> str:
    title = str(content.get("title") or "").strip()
    if title:
        title = re.sub(r"\s*-\s*抖音\s*$", "", title)
        title = re.sub(r"\s*[-_]\s*(小红书|今日头条).*$", "", title)
        return short_text(title, 60)
    return summarize_url(url)


def resolve_written_title(url: str, content: dict[str, Any], composed_card: dict[str, Any], composer_result: dict[str, Any]) -> str:
    title = str(
        composed_card.get("display_title")
        or composer_result.get("display_title")
        or ""
    ).strip()
    return title or source_title(url, content)


def build_formal_card(url: str, job_id: str, content: dict[str, Any], transcript: dict[str, Any], ocr: dict[str, Any], comments: dict[str, Any]) -> tuple[str, str, str]:
    title_base = source_title(url, content)
    title = f"{today_text()}_{title_base}"
    transcript_text = transcript.get("transcript") or ""
    points = bulletize_from_transcript(transcript_text)
    level = content_level(transcript, ocr, comments)
    final_url = content.get("final_url") or url
    ocr_status = ocr.get("status") or "not_run"
    comments_status = comments.get("status") or "not_run"
    markdown = f"""# {title}

> 本卡为 {level} 正式视频知识卡。
> 依据：页面读取 + dyt + whisper.cpp 本地转写。以下内容只基于已读取页面信息、转写文本、OCR/评论结构化结果，不编造未读取内容。

## 来源信息

- 原始 URL：{url}
- 最终 URL：{final_url}
- 标题：{content.get("title") or title_base}
- 作者：{content.get("author") or "未读取到"}
- 发布时间：{content.get("published_at") or "未读取到"}
- 点赞数：{format_count(content.get("like_count"))}
- 评论数：{format_count(content.get("comment_count"))}
- 收藏数：{format_count(content.get("collect_count"))}
- 分享 / 转发数：{format_count(content.get("share_count"))}
- 互动信号：{"；".join(engagement_signal_lines(content))}
- 内容等级：{level}
- transcript 来源：dyt + whisper.cpp 本地转写
- job_id：{job_id}

## 一句话总结

{short_text(points[0] if points else transcript_text, 160)}

## 核心观点

{chr(10).join(f"- {p}" for p in points)}

## 关键知识块

### [[短视频自动化生产流程]]

- 可复用价值：把选题参考、文案提取、文案改写、标题话题、音频、视频、字幕、配乐和多平台发布串成一条工作流。
- 依据片段：{short_text(transcript_text, 260)}

### [[AI 自媒体获客系统]]

- 可复用价值：把内容批量生产与获客目标连接起来，但仍需要人工复核文案质量、平台规则和商业承诺。

## 方法论 / 流程

- 找到同领域表现较好的参考视频。
- 提取参考视频文案并生成改写版本。
- 自动生成标题、话题、声音、视频、字幕、背景音乐和封面。
- 将成片发布到多个短视频平台，并按需要挂商品、团购或获客链接。

## 可复用判断

- 适合复用在标准化程度高、素材结构稳定的自媒体批量生产场景。
- 不适合直接无审核发布；转写中存在错字，视频效果、合规性和平台表现仍需人工验证。

## 风险与不确定性

- 页面信息读取状态：{content.get("status")}
- 转写状态：{transcript.get("status")}
- whisper 转写可能存在错字，需要后续人工复核关键术语。
- 本卡未基于人工观看完整视频画面，只使用自动化可达内容。

## OCR 补充情况

- OCR 状态：{ocr_status}
- OCR 文字：{short_text(ocr.get("merged_text") or "未触发或未识别到有效画面文字。", 500)}

## 评论补充情况

- 评论状态：{comments_status}
- 评论数量：{comments.get("comment_count") or 0}

{format_comment_signals(comments)}

### 评论样本

{format_comment_samples(comments)}

## 标签

[[AI]]
[[持续成长]]
[[Agent]]
[[个人知识库]]
[[视频学习]]
"""
    return title, markdown, level


def build_temporary_card(url: str, job_id: str, source_type: str, content: dict[str, Any], transcript: dict[str, Any], ocr: dict[str, Any], comments: dict[str, Any]) -> tuple[str, str, str]:
    title = f"{today_text()}_待处理_{source_title(url, content)}"
    failed_level = (
        explain_failure(transcript.get("failed_reason"))
        if transcript.get("failed_reason")
        else explain_failure(transcript.get("status"))
    ) or content.get("error") or "内容不足"
    level_done = content.get("status") or "accepted"
    markdown = f"""# {title}

> 状态：待处理
> 置信度：低
> 原因：已尝试页面读取和口播转写，但内容不足以生成正式摘要。
> 禁止：以下内容不是正式摘要，不能当作来源真实观点。

## 来源信息

- 原始链接：{url}
- 最终链接：{content.get("final_url") or "未读取到"}
- 来源类型：{source_type}
- 作者：{content.get("author") or "未读取到"}
- 发布时间：{content.get("published_at") or "未读取到"}
- 点赞数：{format_count(content.get("like_count"))}
- 评论数：{format_count(content.get("comment_count"))}
- 收藏数：{format_count(content.get("collect_count"))}
- 分享 / 转发数：{format_count(content.get("share_count"))}
- 互动信号：{"；".join(engagement_signal_lines(content))}
- job_id：{job_id}
- 推荐目录：00_Inbox / 临时收集箱
- 分类置信度：low

## 降级说明

- 已经成功做到：{level_done}
- 失败或不足环节：{failed_level}
- 后续补法：重新检查链接、转写依赖、网络访问，必要时补充人工字幕或截图 OCR。

## 内容读取状态

- 页面读取：{content.get("status")}
- 视频下载 / 音频转写：{transcript.get("status")}
- OCR：{ocr.get("status")}
- 评论区：{comments.get("status")}

## 转写摘录

> {short_text(transcript.get("transcript") or "未获得有效口播转写。", 500)}

## 画面文字 / OCR

{short_text(ocr.get("merged_text") or "- 未触发或未识别到有效画面文字。", 500)}

## 评论区信号

{format_comment_signals(comments)}

## 关联标签

[[待处理]]
[[低置信度]]
[[临时收集]]
"""
    return title, markdown, "Level 1/2 临时卡"


def build_temporary_review_card(
    url: str,
    job_id: str,
    job_dir: Path,
    content: dict[str, Any],
    transcript: dict[str, Any],
    ocr: dict[str, Any],
    comments: dict[str, Any],
    composer: dict[str, Any],
    quality_gate: dict[str, Any],
) -> tuple[str, str, str]:
    title = f"{today_text()}_待模型复核_{source_title(url, content)}"
    transcript_excerpt = short_text(transcript.get("transcript") or "未获得有效口播转写。", 700)
    failed_checks = quality_gate.get("failed_checks") or []
    composer_error = composer.get("composer_error") or composer.get("error") or ""
    markdown = f"""# {title}

> 状态：needs_model_composer_or_human_review
> 卡片类型：temporary_review_card
> 质量等级：low
> 禁止：以下内容不是正式知识卡，不能标记为 Level 5，也不能当作已提炼结论。

## 来源信息

- 原始 URL：{url}
- 最终 URL：{content.get("final_url") or comments.get("final_url") or "未读取到"}
- 页面标题：{content.get("title") or comments.get("title") or source_title(url, content)}
- 作者：{content.get("author") or "未读取到"}
- 发布时间：{content.get("published_at") or "未读取到"}
- 点赞数：{format_count(content.get("like_count"))}
- 评论数：{format_count(content.get("comment_count"))}
- 收藏数：{format_count(content.get("collect_count"))}
- 分享 / 转发数：{format_count(content.get("share_count"))}
- 互动信号：{"；".join(engagement_signal_lines(content))}
- job_id：{job_id}
- 本地 job：{job_dir}

## 降级原因

- composer_status：{composer.get("composer_status") or "unknown"}
- model_provider：{composer.get("model_provider") or "none"}
- model_used：{composer.get("model_used") or "none"}
- composer_error：{short_text(str(composer_error), 260)}
- quality_gate_passed：{quality_gate.get("quality_gate_passed")}
- failed_checks：{", ".join(str(item) for item in failed_checks) if failed_checks else "未运行或未返回"}

## 内容读取状态

- 页面读取：{content.get("status")}
- 转写：{transcript.get("status")}；has_speech={transcript.get("has_speech")}
- OCR：{ocr.get("status")}
- 评论：{comments.get("status")}；数量={comments.get("comment_count") or 0}

## 转写摘录

> {transcript_excerpt}

## 评论样本

{format_comment_samples(comments)}

## 后续动作

- 配置可用模型通道后重新运行 card_composer；
- 或人工基于 transcript / comments 复核并改写正式知识卡；
- 通过 quality_gate 后再允许写正式卡。

## 标签

[[待复核]]
[[临时转写卡]]
[[模型写卡失败]]
"""
    return title, markdown, "raw_transcript_with_comments"


def source_material_text(content: dict[str, Any], limit: int = 6000, *, include_user_supplied: bool = True) -> str:
    candidates: list[str] = []
    if include_user_supplied:
        user_text = content.get("user_supplied_text")
        if user_text:
            candidates.append(str(user_text))
    for key in ("main_text", "visible_text", "text", "description"):
        value = content.get(key)
        if isinstance(value, list):
            candidates.extend(str(item) for item in value if item)
        elif value:
            candidates.append(str(value))
    seen: set[str] = set()
    lines: list[str] = []
    for raw in "\n".join(candidates).splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    text = "\n".join(lines).strip()
    return short_text(text, limit) if text else ""


def chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def latin_word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z0-9'_-]*", text or ""))


OCR_UI_STOPWORDS = {
    "+",
    "x",
    "red",
    "live",
    "首页",
    "发现",
    "发布",
    "通知",
    "消息",
    "我",
    "更多",
    "三更多",
    "关于我们",
    "直播",
    "点点ai",
}


def normalize_ocr_noise_key(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "").casefold()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", compact)


def ocr_candidate_texts(ocr: dict[str, Any]) -> list[str]:
    items = ocr.get("text_items") or []
    if isinstance(items, list) and items:
        values = []
        for item in items:
            if isinstance(item, dict):
                values.append(str(item.get("text") or ""))
            elif item:
                values.append(str(item))
        return values
    return str(ocr.get("merged_text") or "").splitlines()


def meaningful_ocr_text_lines(ocr: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in ocr_candidate_texts(ocr):
        line = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not line:
            continue
        key = normalize_ocr_noise_key(line)
        if not key or key in seen:
            continue
        cn_count = chinese_char_count(line)
        ascii_count = len(re.findall(r"[A-Za-z0-9]", line))
        if key in OCR_UI_STOPWORDS:
            continue
        if len(key) <= 1:
            continue
        if cn_count == 0 and ascii_count < 4:
            continue
        if cn_count <= 1 and ascii_count == 0:
            continue
        seen.add(key)
        lines.append(line)
    return lines


def visual_collection_context_sufficient(
    content: dict[str, Any],
    source_len: int,
    source_cn: int,
    ocr: dict[str, Any],
    config: dict[str, Any],
) -> bool:
    try:
        image_count = int(content.get("image_count") or 0)
    except (TypeError, ValueError):
        image_count = 0
    min_images = config_int(config, "material_min_visual_image_count", 6)
    min_source_chars = config_int(config, "material_min_visual_source_chars", 50)
    min_source_cn = config_int(config, "material_min_visual_source_chinese_chars", 40)
    ocr_status = str(ocr.get("status") or "")
    ocr_attempted = ocr_status in {"ocr_done", "ocr_failed", "web_image_ocr_no_images", "image_capture_failed", "cdp_failed"}
    return (
        image_count >= min_images
        and source_len >= min_source_chars
        and source_cn >= min_source_cn
        and ocr_attempted
    )


def default_web_image_ocr_max_images(content: dict[str, Any], config: dict[str, Any]) -> int:
    if "web_image_ocr_max_images" in config:
        return min(max(1, config_int(config, "web_image_ocr_max_images", 16)), 24)
    try:
        image_count = int(content.get("image_count") or 0)
    except (TypeError, ValueError):
        image_count = 0
    if image_count >= 12:
        return 18
    if image_count >= 6:
        return 12
    return 8


def assess_material_quality(
    content: dict[str, Any],
    transcript: dict[str, Any],
    ocr: dict[str, Any],
    comments: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    source_text = source_material_text(content, limit=30000)
    platform_source_text = source_material_text(content, limit=30000, include_user_supplied=False)
    user_supplied_text = normalize_user_supplied_text(str(content.get("user_supplied_text") or ""))
    transcript_text = str(transcript.get("transcript") or transcript.get("raw_transcript") or "")
    ocr_text = str(ocr.get("merged_text") or "")
    meaningful_ocr_text = "\n".join(meaningful_ocr_text_lines(ocr))
    source_len = len(source_text.strip())
    source_cn = chinese_char_count(source_text)
    source_words = latin_word_count(source_text)
    platform_source_len = len(platform_source_text.strip())
    platform_source_cn = chinese_char_count(platform_source_text)
    platform_source_words = latin_word_count(platform_source_text)
    user_text_len = len(user_supplied_text.strip())
    user_text_cn = chinese_char_count(user_supplied_text)
    user_text_words = latin_word_count(user_supplied_text)
    transcript_len = len(transcript_text.strip())
    transcript_cn = chinese_char_count(transcript_text)
    transcript_words = latin_word_count(transcript_text)
    ocr_len = len(ocr_text.strip())
    ocr_cn = chinese_char_count(ocr_text)
    ocr_words = latin_word_count(ocr_text)
    meaningful_ocr_len = len(meaningful_ocr_text.strip())
    meaningful_ocr_cn = chinese_char_count(meaningful_ocr_text)
    meaningful_ocr_words = latin_word_count(meaningful_ocr_text)
    comments_count = int(comments.get("comment_count") or len(comments.get("comments") or comments.get("comment_items") or []) or 0)

    min_source_chars = config_int(config, "material_min_source_chars", 180)
    min_source_cn = config_int(config, "material_min_source_chinese_chars", 80)
    min_source_words = config_int(config, "material_min_source_latin_words", 90)
    min_transcript_chars = config_int(config, "material_min_transcript_chars", 120)
    min_ocr_chars = config_int(config, "material_min_ocr_chars", 80)

    primary_sources: list[str] = []
    reasons: list[str] = []
    blockers: list[str] = []
    if user_text_len >= min_source_chars and (user_text_cn >= min_source_cn or user_text_words >= min_source_words):
        primary_sources.append("user_supplied_text")
        reasons.append("user_supplied_text_sufficient")
    if platform_source_len >= min_source_chars and (platform_source_cn >= min_source_cn or platform_source_words >= min_source_words):
        primary_sources.append("source_text")
        reasons.append("source_text_sufficient")
    elif (
        not primary_sources
        and source_len >= min_source_chars
        and (source_cn >= min_source_cn or source_words >= min_source_words)
    ):
        primary_sources.append("source_text")
        reasons.append("combined_source_text_sufficient")
    if transcript.get("has_speech") and transcript_len >= min_transcript_chars:
        primary_sources.append("transcript")
        reasons.append("transcript_sufficient")
    if meaningful_ocr_len >= min_ocr_chars and (
        meaningful_ocr_cn >= max(20, min_ocr_chars // 2)
        or meaningful_ocr_words >= max(40, min_ocr_chars // 2)
    ):
        primary_sources.append("ocr")
        reasons.append("ocr_text_sufficient")
    if visual_collection_context_sufficient(content, source_len, source_cn, ocr, config):
        if "source_text" not in primary_sources:
            primary_sources.append("source_text")
        primary_sources.append("media_metadata")
        reasons.append("visual_collection_context_sufficient")

    needs_visual = bool(content.get("need_ocr") or content.get("image_count") or content.get("has_video") or content.get("video_source_count"))
    has_primary = bool(primary_sources)
    if not has_primary:
        blockers.append("primary_material_too_short")
    if needs_visual and not (primary_sources or meaningful_ocr_text.strip()):
        blockers.append("visual_or_media_note_needs_ocr_or_transcription")
    if needs_visual and ocr_text.strip() and not meaningful_ocr_text.strip() and not has_primary:
        blockers.append("ocr_text_only_ui_or_noise")
    if str(content.get("status") or "") == "login_or_verification_wall":
        blockers.append("authorization_or_verification_required")
    if not content.get("ok") and not transcript.get("has_speech"):
        blockers.append("reader_failed_without_transcript")

    can_compose = has_primary and "authorization_or_verification_required" not in blockers
    return {
        "schema_name": "MaterialQualityV1",
        "schema_version": 1,
        "can_compose_formal": can_compose,
        "quality_level": "sufficient" if can_compose else "insufficient",
        "primary_sources": primary_sources,
        "reasons": reasons,
        "blockers": blockers,
        "source_text_length": source_len,
        "source_chinese_char_count": source_cn,
        "source_latin_word_count": source_words,
        "platform_source_text_length": platform_source_len,
        "platform_source_chinese_char_count": platform_source_cn,
        "platform_source_latin_word_count": platform_source_words,
        "user_supplied_text_length": user_text_len,
        "user_supplied_chinese_char_count": user_text_cn,
        "user_supplied_latin_word_count": user_text_words,
        "transcript_length": transcript_len,
        "transcript_chinese_char_count": transcript_cn,
        "transcript_latin_word_count": transcript_words,
        "ocr_text_length": ocr_len,
        "ocr_chinese_char_count": ocr_cn,
        "ocr_latin_word_count": ocr_words,
        "ocr_meaningful_text_length": meaningful_ocr_len,
        "ocr_meaningful_chinese_char_count": meaningful_ocr_cn,
        "ocr_meaningful_latin_word_count": meaningful_ocr_words,
        "ocr_noise_filtered_count": max(0, len(ocr_candidate_texts(ocr)) - len(meaningful_ocr_text_lines(ocr))),
        "comments_count": comments_count,
        "needs_visual_enrichment": needs_visual,
        "content_status": content.get("status") or "",
        "transcript_status": transcript.get("status") or "",
        "ocr_status": ocr.get("status") or "",
        "used_mcp": False,
    }


def should_run_web_image_ocr(content: dict[str, Any], material_quality: dict[str, Any], config: dict[str, Any]) -> bool:
    if not config_bool(config, "enable_web_image_ocr", True):
        return False
    if material_quality.get("can_compose_formal"):
        return False
    if not material_quality.get("needs_visual_enrichment"):
        return False
    try:
        image_count = int(content.get("image_count") or 0)
    except (TypeError, ValueError):
        image_count = 0
    return image_count > 0


def has_visual_capture_candidate(content: dict[str, Any]) -> bool:
    try:
        image_count = int(content.get("image_count") or 0)
    except (TypeError, ValueError):
        image_count = 0
    try:
        video_source_count = int(content.get("video_source_count") or 0)
    except (TypeError, ValueError):
        video_source_count = 0
    return bool(content.get("has_video") or image_count > 0 or video_source_count > 0)


def should_run_visual_platform_web_image_ocr(
    content: dict[str, Any],
    classification: dict[str, Any],
    material_quality: dict[str, Any],
    config: dict[str, Any],
) -> bool:
    if not config_bool(config, "enable_web_image_ocr", True):
        return False
    platform_id = str(classification.get("platform_id") or content.get("platform_id") or "").strip()
    source_type = str(content.get("source_type") or classification.get("source_type") or "").strip()
    visual_platform = platform_id in {"xiaohongshu", "douyin"} or source_type.startswith(("mixed/", "video/"))
    if not visual_platform:
        return False
    if material_quality.get("can_compose_formal"):
        return bool(
            config_bool(config, "visual_platform_web_image_ocr_when_requested", True)
            and content.get("need_ocr")
            and has_visual_capture_candidate(content)
        )
    if not config_bool(config, "visual_platform_web_image_ocr_on_low_material", True):
        return False
    platform_text = source_material_text(content, limit=30000, include_user_supplied=False)
    status = str(content.get("status") or "")
    reader_failed = status in {"cdp_failed", "source_reader_failed", "source_extract_incomplete", "page_fetch_failed"}
    return bool(reader_failed or not platform_text.strip() or material_quality.get("needs_visual_enrichment"))


def should_run_douyin_web_image_ocr(
    content: dict[str, Any],
    transcript: dict[str, Any],
    ocr: dict[str, Any],
    material_quality: dict[str, Any],
    config: dict[str, Any],
) -> bool:
    if not config_bool(config, "enable_web_image_ocr", True):
        return False
    if not config_bool(config, "douyin_web_image_ocr_on_low_material", True):
        return False
    if material_quality.get("can_compose_formal"):
        return False
    if meaningful_ocr_text_lines(ocr):
        return False
    if not (
        str(content.get("source_type") or "").startswith("video/douyin")
        or is_douyin_url(str(content.get("final_url") or content.get("original_url") or ""))
    ):
        return False
    ocr_status = str(ocr.get("status") or "")
    no_video_input = not transcript.get("video_path") or ocr_status in {
        "skipped_no_video",
        "skipped_no_input",
        "skipped_not_needed",
        "input_missing",
        "ocr_failed",
    }
    return bool(no_video_input or material_quality.get("needs_visual_enrichment"))


def format_media_signals(content: dict[str, Any]) -> str:
    fields = [
        ("笔记类型", content.get("note_type") or "未判断"),
        ("是否检测到视频", "是" if content.get("has_video") else "否"),
        ("图片数量", content.get("image_count") if content.get("image_count") not in (None, "") else "未读取到"),
        ("视频源数量", content.get("video_source_count") if content.get("video_source_count") not in (None, "") else "未读取到"),
        ("OCR 建议", "需要后续 OCR/视频增强" if content.get("need_ocr") else "暂不需要"),
    ]
    return "\n".join(f"- {label}：{value}" for label, value in fields)


def format_ocr_image_evidence(job_dir: Path, limit: int = 6) -> str:
    material = read_json(job_dir / "ocr_material.json")
    if not material:
        return "未捕获到可展示图片。"
    candidates: list[str] = []

    def add_path(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        suffix = Path(text).suffix.casefold()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            return
        if text not in candidates:
            candidates.append(text)

    sampling = material.get("sampling") if isinstance(material.get("sampling"), dict) else {}
    frames = sampling.get("frames") if isinstance(sampling.get("frames"), list) else []
    for frame in frames:
        if isinstance(frame, dict):
            add_path(frame.get("path"))
    evidence_items = material.get("evidence_items") if isinstance(material.get("evidence_items"), list) else []
    for item in evidence_items:
        if isinstance(item, dict):
            add_path(item.get("frame_path") or item.get("frame") or item.get("path"))
    if not candidates:
        return "未捕获到可展示图片。"
    lines = []
    for index, image_path in enumerate(candidates[:limit], start=1):
        markdown_path = Path(image_path).as_posix()
        lines.append(f"![图片证据 {index}]({markdown_path})")
    return "\n\n".join(lines)


def build_extracted_source_card(
    url: str,
    job_id: str,
    job_dir: Path,
    source_type: str,
    content: dict[str, Any],
    classification: dict[str, Any],
    material_quality: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    title = f"{today_text()}_来源材料_{source_title(url, content)}"
    material = source_material_text(content)
    material_status = content.get("status") or "unknown"
    user_supplied_text = normalize_user_supplied_text(str(content.get("user_supplied_text") or ""))
    route_label = classification.get("route_label") or classification.get("route_id") or "basic_webpage"
    route_basis = classification.get("route_basis") or "unknown"
    platform_label = classification.get("platform_label") or classification.get("platform_id") or "unknown"
    level = "Level 2 页面可见材料级"
    if source_type.startswith("mixed/"):
        level = "Level 2 混合笔记可见材料级"
    elif source_type.startswith("text/"):
        level = "Level 2 正文材料级"
    material_quality = material_quality or {}
    blockers = material_quality.get("blockers") or []
    reasons = material_quality.get("reasons") or []
    material_block = material or "未读取到可用正文；可能遇到登录墙、验证页、页面结构变化或链接失效。"
    image_evidence = format_ocr_image_evidence(job_dir)
    markdown = f"""# {title}

> 状态：source_material_extracted
> 卡片类型：extracted_source_card
> 质量等级：source_material
> 禁止：以下内容只是自动提取到的来源材料，不是正式知识卡，也不能标记为 Level 5。

## 来源信息

- 原始链接：{redact_url(url)}
- 最终链接：{redact_url(str(content.get("final_url") or "")) or "未读取到"}
- 来源类型：{source_type}
- 链接路线：{route_label}
- 路由依据：{route_basis}
- 平台上下文：{platform_label}
- 标题：{content.get("title") or source_title(url, content)}
- 作者：{content.get("author") or "未读取到"}
- 发布时间：{content.get("published_at") or "未读取到"}
- 点赞数：{format_count(content.get("like_count"))}
- 评论数：{format_count(content.get("comment_count"))}
- 收藏数：{format_count(content.get("collect_count"))}
- 分享 / 转发数：{format_count(content.get("share_count"))}
- job_id：{job_id}
- 本地 job：{job_dir}

## 读取状态

- 页面/笔记读取：{material_status}
- 正文长度：{content.get("text_length") or len(material)}
- 中文字符数：{content.get("chinese_char_count") or "未统计"}
- 提取来源：{content.get("extraction_source") or "unknown"}
- 用户随链接文本：{"有" if user_supplied_text else "无"}；长度={len(user_supplied_text)}
- 错误摘要：{short_text(str(content.get("error") or ""), 260) or "无"}
- 读取提示：{short_text(str(content.get("error_hint") or ""), 260) or "无"}

## 材料门禁

- 是否足够正式写卡：{"是" if material_quality.get("can_compose_formal") else "否"}
- 主材料来源：{", ".join(str(item) for item in (material_quality.get("primary_sources") or [])) or "无"}
- 阻断原因：{", ".join(str(item) for item in blockers) if blockers else "无"}
- 通过理由：{", ".join(str(item) for item in reasons) if reasons else "无"}
- 正文长度：{material_quality.get("source_text_length") if material_quality else content.get("text_length") or len(material)}
- 转写长度：{material_quality.get("transcript_length") if material_quality else "未统计"}
- OCR 文字长度：{material_quality.get("ocr_text_length") if material_quality else "未统计"}

## 媒体信号

{format_media_signals(content)}

## 图片证据

{image_evidence}

## 可见正文 / 来源材料

{material_block}

## 后续动作

- 如果正文材料足够，下一步接文本/混合内容 composer，生成正式知识卡并通过 quality_gate；
- 如果是视频笔记或关键信息在图中，后续补图片/视频下载与 OCR，再把 OCRMaterialV1 纳入 composer；
- 如果遇到登录墙或验证页，先在“链接路由与授权”面板重新授权，再重新跑该链接。

## 标签

[[来源材料]]
[[临时复核]]
[[{platform_label}]]
"""
    return title, markdown, level


def select_written_card(
    url: str,
    job_id: str,
    job_dir: Path,
    content: dict[str, Any],
    transcript: dict[str, Any],
    ocr: dict[str, Any],
    comments: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str, Path, str, str, str, dict[str, Any], dict[str, Any], str]:
    composer_result: dict[str, Any] = {}
    quality_gate: dict[str, Any] = {}
    material_quality = assess_material_quality(content, transcript, ocr, comments, config)
    write_json(job_dir / "material_quality.json", material_quality)
    if material_quality.get("can_compose_formal"):
        composed_json = job_dir / "composed_card.json"
        composed_markdown = job_dir / "composed_card.md"
        quality_gate_path = job_dir / "quality_gate.json"
        if config.get("enable_ai_composer", True):
            composer_timeout = int(config.get("composer_timeout_sec", 120))
            compose_exit, composer_result = call_composer(job_dir, composed_json, composed_markdown, composer_timeout)
            gate_exit, quality_gate = call_quality_gate(job_dir, composed_json, composed_markdown, quality_gate_path)
            if (
                compose_exit == 0
                and gate_exit == 0
                and composer_result.get("ok")
                and quality_gate.get("quality_gate_passed")
                and composed_markdown.exists()
            ):
                composed_card = read_json(composed_json)
                improved_path = job_dir / "improved_card.md"
                improved_path.write_text(composed_markdown.read_text(encoding="utf-8"), encoding="utf-8")
                title = resolve_written_title(url, content, composed_card, composer_result)
                level = str(composed_card.get("content_level") or "Level 3 视频口播转写级")
                return title, improved_path, level, "formal_summary", "completed_formal", composer_result, quality_gate, "improved_card.md"
        else:
            composer_result = {
                "ok": False,
                "composer_status": "disabled",
                "composer_error": "enable_ai_composer is false; strict mode forbids formal fallback cards.",
                "used_mcp": False,
            }
            quality_gate = {
                "quality_gate_passed": False,
                "failed_checks": ["composer_disabled"],
                "downgrade_reason": "enable_ai_composer is false",
                "final_card_type": "temporary_review_card",
                "used_mcp": False,
            }
        title, markdown, level = build_temporary_review_card(url, job_id, job_dir, content, transcript, ocr, comments, composer_result, quality_gate)
        review_path = job_dir / "temporary_review_card.md"
        review_path.write_text(markdown, encoding="utf-8")
        return title, review_path, level, "temporary_review_card", "completed_needs_model_review", composer_result, quality_gate, "temporary_review_card.md"

    source_type = str(content.get("source_type") or detect_source_type(url))
    title, markdown, level = build_temporary_card(url, job_id, source_type, content, transcript, ocr, comments)
    card_path = job_dir / "card.md"
    card_path.write_text(markdown, encoding="utf-8")
    quality_gate = {
        "quality_gate_passed": False,
        "failed_checks": material_quality.get("blockers") or ["material_insufficient"],
        "downgrade_reason": "; ".join(str(item) for item in (material_quality.get("blockers") or [])) or "material_insufficient",
        "final_card_type": "temporary_card",
        "material_quality": material_quality,
        "used_mcp": False,
    }
    return title, card_path, level, "temporary_card", "completed_low_confidence", composer_result, quality_gate, "card.md"


def build_failure_card(url: str, job_id: str, job_dir: Path, stage: str, error: str) -> tuple[str, str]:
    title = f"{today_text()}_处理失败_{summarize_url(url)}"
    markdown = f"""# {title}

> 状态：处理失败
> 置信度：无
> 原因：自动处理流程未能完成正式内容读取。
> 禁止：以下内容不是正式摘要。

## 来源信息

- 原始链接：{url}
- 接收时间：{now_iso()}
- job_id：{job_id}
- 失败阶段：{stage}
- 错误摘要：{error}

## 已保留信息

- 本地 job 目录：{job_dir}
- status.json：{job_dir / "status.json"}
- job.log：{job_dir / "job.log"}

## 后续建议

- 检查链接是否可访问；
- 检查网络；
- 检查 SiYuan 是否启动；
- 检查 SIYUAN_TOKEN；
- 后续可重新运行该 job。
"""
    return title, markdown


def write_source_material_placeholders(job_dir: Path, source_type: str, content: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    transcript = {
        "ok": True,
        "used_mcp": False,
        "source_type": source_type,
        "status": "skipped_no_audio_transcription_in_source_material_flow",
        "has_speech": False,
        "confidence": "none",
        "transcript": "",
    }
    ocr = {
        "ok": True,
        "used_mcp": False,
        "source_type": source_type,
        "status": "skipped_pending_media_ocr" if content.get("need_ocr") else "skipped_not_needed",
        "should_run_ocr": bool(content.get("need_ocr")),
        "merged_text": "",
    }
    comments = {
        "ok": True,
        "used_mcp": False,
        "source_type": source_type,
        "status": "skipped_not_implemented_for_source_material_flow",
        "comment_count": 0,
        "comments": [],
        "signals": [],
    }
    write_json(job_dir / "transcript.json", transcript)
    write_json(job_dir / "ocr.json", ocr)
    write_json(job_dir / "comments.json", comments)
    return transcript, ocr, comments


def source_material_final_status(content: dict[str, Any], write_result: dict[str, Any]) -> str:
    if not write_result.get("ok"):
        return "failed_but_recorded"
    status = str(content.get("status") or "")
    material = source_material_text(content, limit=1200)
    if status == "login_or_verification_wall":
        return "completed_source_material_needs_authorization"
    if content.get("ok") and material:
        return "completed_source_material_written"
    return "completed_source_material_incomplete"


def run_source_material_flow(
    args: argparse.Namespace,
    config: dict[str, Any],
    config_path: Path,
    config_warning: str | None,
    job_id: str,
    job_dir: Path,
    classification: dict[str, Any],
    *,
    reader_label: str,
    reader_script: str,
    timeout_key: str,
    default_timeout: int,
) -> int:
    url = args.url
    source_type = str(classification.get("source_type") or detect_source_type(url))
    safe_classification = classification_for_artifact(classification)
    result: dict[str, Any] = {
        "ok": False,
        "job_id": job_id,
        "job_dir": str(job_dir),
        "url": redact_url(url),
        "url_redacted": redact_url(url),
        "source_type": source_type,
        "classification": safe_classification,
        "used_mcp": False,
        "config_path": str(config_path),
        "config_warning": config_warning,
    }
    timeout_sec = min(max(10, config_int(config, timeout_key, default_timeout)), 180)
    update_status(job_dir, f"{reader_label}_fetching", {
        "source_type": source_type,
        "route_id": classification.get("route_id"),
        "route_basis": classification.get("route_basis"),
    })
    reader_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / reader_script),
        "--url",
        url,
        "--job-dir",
        str(job_dir),
        "--timeout-sec",
        str(timeout_sec),
    ]
    platform_id = str(classification.get("platform_id") or "").strip()
    if platform_id and config_bool(config, f"{platform_id}_reader_headed", False):
        reader_cmd.append("--headed")
    if platform_id and config_bool(config, f"{platform_id}_reader_use_temp_profile", False):
        reader_cmd.append("--use-temp-profile")

    try:
        reader_exit, reader_result = call_tool(
            job_dir,
            reader_script,
            reader_cmd,
            timeout=timeout_sec + 45,
        )
    except Exception as exc:
        reader_exit = 1
        reader_result = {
            "ok": False,
            "status": "source_reader_failed",
            "error": f"{type(exc).__name__}: {short_text(str(exc), 260)}",
            "used_mcp": False,
        }
        write_json(job_dir / "content.json", {
            "ok": False,
            "used_mcp": False,
            "stage": reader_label,
            "source_type": source_type,
            "original_url": redact_url(url),
            "final_url": "",
            "title": "",
            "author": "",
            "published_at": "",
            "description": "",
            "visible_text": "",
            "main_text": "",
            "status": "source_reader_failed",
            "error": reader_result["error"],
            "need_ocr": False,
        })

    content = read_json(job_dir / "content.json")
    if not content:
        content = {
            "ok": bool(reader_result.get("ok")),
            "used_mcp": False,
            "stage": reader_label,
            "source_type": source_type,
            "original_url": redact_url(url),
            "final_url": redact_url(str(reader_result.get("final_url") or "")),
            "title": reader_result.get("title") or "",
            "author": reader_result.get("author") or "",
            "published_at": reader_result.get("published_at") or "",
            "description": reader_result.get("description") or "",
            "visible_text": reader_result.get("main_text") or reader_result.get("visible_text") or "",
            "main_text": reader_result.get("main_text") or "",
            "status": reader_result.get("status") or "source_extract_incomplete",
            "error": reader_result.get("error"),
            "need_ocr": False,
        }
    content.setdefault("source_type", source_type)
    content.setdefault("source_url_redacted", redact_url(url))
    content.setdefault("classification", safe_classification)
    content = attach_user_supplied_text_to_content_file(job_dir, content, str(getattr(args, "user_supplied_text", "") or ""))
    write_json(job_dir / "content.json", content)
    update_status(job_dir, f"{reader_label}_done" if content.get("ok") else f"{reader_label}_incomplete", {
        "reader_exit_code": reader_exit,
        "reader_status": content.get("status") or reader_result.get("status"),
        "text_length": content.get("text_length") or len(source_material_text(content)),
        "need_ocr": bool(content.get("need_ocr")),
    })

    transcript, ocr, comments = write_source_material_placeholders(job_dir, source_type, content)
    material_quality = assess_material_quality(content, transcript, ocr, comments, config)
    write_json(job_dir / "material_quality.json", material_quality)
    update_status(job_dir, "material_quality_checked", {
        "can_compose_formal": bool(material_quality.get("can_compose_formal")),
        "quality_level": material_quality.get("quality_level"),
        "blockers": material_quality.get("blockers") or [],
        "primary_sources": material_quality.get("primary_sources") or [],
    })
    run_visual_image_fallback = should_run_visual_platform_web_image_ocr(
        content,
        classification,
        material_quality,
        config,
    )
    if should_run_web_image_ocr(content, material_quality, config) or run_visual_image_fallback:
        web_ocr_timeout = min(max(20, config_int(config, "web_image_ocr_timeout_sec", 90)), 240)
        web_ocr_max_images = min(max(1, default_web_image_ocr_max_images(content, config)), 24)
        if run_visual_image_fallback and material_quality.get("can_compose_formal"):
            web_ocr_reason = "visual_platform_ocr_requested"
        elif run_visual_image_fallback:
            web_ocr_reason = "visual_platform_reader_failed_or_image_count_unknown"
        else:
            web_ocr_reason = "image_count_visual_enrichment"
        update_status(job_dir, "web_image_ocr_running", {
            "max_images": web_ocr_max_images,
            "timeout_sec": web_ocr_timeout,
            "previous_blockers": material_quality.get("blockers") or [],
            "reason": web_ocr_reason,
        })
        try:
            call_tool(job_dir, "ocr_web_images.py", [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "ocr_web_images.py"),
                "--url",
                url,
                "--job-dir",
                str(job_dir),
                "--platform",
                platform_id if platform_id in {"douyin", "toutiao", "xiaohongshu", "webpage"} else "webpage",
                "--max-images",
                str(web_ocr_max_images),
                "--timeout-sec",
                str(web_ocr_timeout),
            ], timeout=web_ocr_timeout + 60)
        except Exception as exc:
            append_log(job_dir, f"web_image_ocr_failed error={type(exc).__name__}: {short_text(str(exc), 240)}")
        ocr, ocr_material = read_ocr_bundle(job_dir)
        material_quality = assess_material_quality(content, transcript, ocr, comments, config)
        write_json(job_dir / "material_quality.json", material_quality)
        update_status(job_dir, "web_image_ocr_done", {
            "ocr_status": ocr.get("status"),
            "ocr_text_length": material_quality.get("ocr_text_length"),
            "ocr_meaningful_text_length": material_quality.get("ocr_meaningful_text_length"),
            "can_compose_formal": bool(material_quality.get("can_compose_formal")),
            "blockers": material_quality.get("blockers") or [],
        })

    update_status(job_dir, "build_card")
    composer_result: dict[str, Any] = {
        "ok": False,
        "composer_status": "skipped_material_insufficient",
        "composer_error": "",
        "used_mcp": False,
    }
    quality_gate: dict[str, Any] = {
        "quality_gate_passed": False,
        "failed_checks": material_quality.get("blockers") or ["material_insufficient"],
        "downgrade_reason": "; ".join(str(item) for item in (material_quality.get("blockers") or [])) or "material_insufficient",
        "final_card_type": "extracted_source_card",
        "material_quality": material_quality,
        "used_mcp": False,
    }
    if material_quality.get("can_compose_formal"):
        title, card_path, level, card_type, final_status, composer_result, quality_gate, written_card_source = select_written_card(
            url, job_id, job_dir, content, transcript, ocr, comments, config
        )
    else:
        title, markdown, level = build_extracted_source_card(
            url,
            job_id,
            job_dir,
            source_type,
            content,
            classification,
            material_quality,
        )
        card_path = job_dir / "extracted_source_card.md"
        card_path.write_text(markdown, encoding="utf-8")
        card_type = "extracted_source_card"
        final_status = source_material_final_status(content, {"ok": True})
        written_card_source = card_path.name

    update_status(job_dir, "card_built", {
        "card_type": card_type,
        "title": title,
        "content_level": level,
        "written_card_source": written_card_source,
        "can_compose_formal": bool(material_quality.get("can_compose_formal")),
    })
    taxonomy_decision, taxonomy_error = route_taxonomy_for_formal_card(job_dir, card_type, quality_gate, config, config_path)
    if taxonomy_decision:
        update_status(job_dir, "taxonomy_routed", {
            "recommended_path": taxonomy_decision.get("recommended_path"),
            "confidence": taxonomy_decision.get("confidence"),
        })
    elif taxonomy_error:
        update_status(job_dir, "taxonomy_route_failed", {"error": taxonomy_error})

    update_status(job_dir, "writing_siyuan" if storage_target_enabled(config, "siyuan") else "siyuan_skipped")
    writer_exit, write_result = call_writer_if_enabled(config_path, title, card_path, job_dir, config)
    if card_type == "extracted_source_card":
        final_status = source_material_final_status(content, write_result)
    elif not write_result.get("ok") and not write_result.get("skipped") and card_type == "formal_summary":
        final_status = "failed_but_recorded"
    result.update({
        "ok": True,
        "title": title,
        "card_type": card_type,
        "content_level": level,
        "card_path": str(card_path),
        "written_card_path": str(card_path),
        "written_card_source": written_card_source,
        "composer_status": composer_result.get("composer_status"),
        "model_used": composer_result.get("model_used"),
        "model_provider": composer_result.get("model_provider"),
        "quality_gate_passed": quality_gate.get("quality_gate_passed"),
        "failed_checks": quality_gate.get("failed_checks") or [],
        "final_card_type": quality_gate.get("final_card_type") or card_type,
        "material_quality": material_quality,
        "content": content,
        "transcript": transcript,
        "ocr": ocr,
        "comments": comments,
        "composer": composer_result,
        "quality_gate": quality_gate,
        "taxonomy_decision": taxonomy_decision,
        "taxonomy_error": taxonomy_error,
        "reader": reader_result,
        "reader_exit_code": reader_exit,
        "write_result": write_result,
        "writer_exit_code": writer_exit,
        "local_card_preserved": card_path.exists(),
        "siyuan_write_ok": bool(write_result.get("ok")),
        "siyuan_write_skipped_reason": write_result.get("skipped_reason") if write_result.get("skipped") else "",
        "final_status": final_status,
        "used_mcp": False,
    })
    result = apply_lucas_database_write_policy(result, config, job_dir, write_result, card_type, quality_gate)
    update_status(job_dir, final_status, {
        "siyuan_write_ok": bool(write_result.get("ok")),
        "write_stage": write_result.get("stage"),
        "lucas_database_write_enabled": result.get("lucas_database_write_enabled"),
        "lucas_database_write_policy": result.get("lucas_database_write_policy"),
        "lucas_database_write_ok": result.get("lucas_database_write_ok"),
    })
    write_json(job_dir / "result.json", result)
    emit_json(result)
    return 0


def run_xiaohongshu_flow(
    args: argparse.Namespace,
    config: dict[str, Any],
    config_path: Path,
    config_warning: str | None,
    job_id: str,
    job_dir: Path,
    classification: dict[str, Any],
) -> int:
    return run_source_material_flow(
        args,
        config,
        config_path,
        config_warning,
        job_id,
        job_dir,
        classification,
        reader_label="xiaohongshu_note",
        reader_script="fetch_xiaohongshu_note.py",
        timeout_key="xiaohongshu_note_timeout_sec",
        default_timeout=45,
    )


def run_toutiao_flow(
    args: argparse.Namespace,
    config: dict[str, Any],
    config_path: Path,
    config_warning: str | None,
    job_id: str,
    job_dir: Path,
    classification: dict[str, Any],
) -> int:
    return run_source_material_flow(
        args,
        config,
        config_path,
        config_warning,
        job_id,
        job_dir,
        classification,
        reader_label="toutiao_article",
        reader_script="fetch_toutiao_article.py",
        timeout_key="toutiao_article_timeout_sec",
        default_timeout=45,
    )


def run_webpage_flow(
    args: argparse.Namespace,
    config: dict[str, Any],
    config_path: Path,
    config_warning: str | None,
    job_id: str,
    job_dir: Path,
    classification: dict[str, Any],
) -> int:
    return run_source_material_flow(
        args,
        config,
        config_path,
        config_warning,
        job_id,
        job_dir,
        classification,
        reader_label="webpage",
        reader_script="fetch_webpage.py",
        timeout_key="webpage_timeout_sec",
        default_timeout=35,
    )


def run_douyin_flow(args: argparse.Namespace, config: dict[str, Any], config_path: Path, config_warning: str | None, job_id: str, job_dir: Path) -> int:
    url = args.url
    result: dict[str, Any] = {
        "ok": False,
        "job_id": job_id,
        "job_dir": str(job_dir),
        "url": url,
        "source_type": "video/douyin",
        "used_mcp": False,
        "config_path": str(config_path),
        "config_warning": config_warning,
    }
    update_status(job_dir, "fetching_page")
    call_tool(job_dir, "fetch_content.py", [
        sys.executable, str(PROJECT_ROOT / "tools" / "fetch_content.py"),
        "--url", url, "--job-dir", str(job_dir),
    ], timeout=30)
    content = read_json(job_dir / "content.json")
    content = attach_user_supplied_text_to_content_file(job_dir, content, str(getattr(args, "user_supplied_text", "") or ""))
    update_status(job_dir, "page_fetched" if content.get("ok") else "page_fetch_failed", {"page_status": content.get("status")})
    update_status(job_dir, "douyin_page_metadata_fetching")
    metadata_timeout = min(max(20, config_int(config, "douyin_page_metadata_timeout_sec", 45)), 120)
    try:
        call_tool(job_dir, "fetch_douyin_page_playwright.py", [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "fetch_douyin_page_playwright.py"),
            "--job-dir",
            str(job_dir),
            "--url",
            url,
            "--timeout-sec",
            str(metadata_timeout),
        ], timeout=metadata_timeout + 30)
    except Exception as exc:
        append_log(job_dir, f"douyin_page_metadata_failed error={type(exc).__name__}: {short_text(str(exc), 240)}")
        content["douyin_page_metadata_status"] = "unavailable"
        content["douyin_page_metadata_error"] = short_text(str(exc), 240)
        write_json(job_dir / "content.json", content)
    content = merge_douyin_page_metadata(job_dir, read_json(job_dir / "content.json"))
    content = attach_user_supplied_text_to_content_file(job_dir, content, str(getattr(args, "user_supplied_text", "") or ""))
    update_status(job_dir, "douyin_page_metadata_done", {
        "metadata_status": content.get("douyin_page_metadata_status") or content.get("metrics_status"),
        "metrics_status": content.get("metrics_status"),
    })

    update_status(job_dir, "transcribe_dry_run")
    update_status(job_dir, "transcribing")
    transcribe_timeout = int(config.get("transcribe_timeout_sec", 300))
    call_tool(job_dir, "transcribe_media.py", [
        sys.executable, str(PROJECT_ROOT / "tools" / "transcribe_media.py"),
        "--job-dir", str(job_dir),
        "--url", url,
        "--timeout-sec", str(transcribe_timeout),
    ], timeout=(transcribe_timeout * 4) + 90)
    transcript = read_json(job_dir / "transcript.json")
    content = merge_douyin_page_metadata(job_dir, read_json(job_dir / "content.json"))
    content = attach_user_supplied_text_to_content_file(job_dir, content, str(getattr(args, "user_supplied_text", "") or ""))
    trans_status = "transcribed" if transcript.get("has_speech") else (transcript.get("status") or "transcribe_failed")
    update_status(job_dir, trans_status, {
        "transcript_status": transcript.get("status"),
        "dry_run_ok": transcript.get("dry_run_ok"),
        "transcribe_source": transcript.get("transcribe_source"),
        "fallback_triggered": transcript.get("fallback_triggered"),
        "fallback_reason": transcript.get("fallback_reason"),
        "fallback_ok": transcript.get("fallback_ok"),
        "fallback_stage": transcript.get("fallback_stage"),
    })

    ocr_decision = decide_ocr_policy(
        content,
        transcript,
        config,
        source_type="video/douyin",
        force_ocr=bool(args.force_ocr),
    )
    run_ocr = bool(ocr_decision.get("should_run_ocr"))
    ocr_input_args: list[str] = []
    if transcript.get("video_path"):
        ocr_input_args = ["--video", str(transcript.get("video_path"))]
    update_status(job_dir, "ocr_running" if run_ocr else "ocr_skipped", {
        "should_run_ocr": run_ocr,
        "force_ocr": bool(args.force_ocr),
        "ocr_decision": ocr_decision,
    })
    call_tool(job_dir, "ocr_media.py", [
        sys.executable, str(PROJECT_ROOT / "tools" / "ocr_media.py"),
        "--job-dir", str(job_dir),
        "--max-frames", str(config.get("max_ocr_frames", 10)),
        "--timeout-sec", str(config.get("ocr_timeout_sec", 180)),
        *ocr_input_args,
        *(["--force-ocr"] if args.force_ocr else (["--should-run-ocr"] if run_ocr else [])),
    ], timeout=int(config.get("ocr_timeout_sec", 180)) + 30)
    ocr, ocr_material = read_ocr_bundle(job_dir)
    if ocr_material:
        ocr_material = attach_ocr_decision_to_material(job_dir, ocr_material, ocr_decision)
        ocr, ocr_material = read_ocr_bundle(job_dir)
    update_status(job_dir, "ocr_done" if ocr.get("status") != "skipped_not_needed" else "ocr_skipped", {
        "ocr_status": ocr.get("status"),
        "ocr_material_schema": ocr_material.get("schema_name") if ocr_material else "",
        "ocr_decision": ocr_decision,
    })
    comments_probe = {"ok": True, "comment_count": 0, "comments": [], "used_mcp": False}
    material_quality_after_media = assess_material_quality(content, transcript, ocr, comments_probe, config)
    write_json(job_dir / "material_quality.json", material_quality_after_media)
    if should_run_douyin_web_image_ocr(content, transcript, ocr, material_quality_after_media, config):
        web_ocr_timeout = min(max(20, config_int(config, "web_image_ocr_timeout_sec", 90)), 240)
        web_ocr_max_images = min(max(1, default_web_image_ocr_max_images(content, config)), 24)
        update_status(job_dir, "web_image_ocr_running", {
            "max_images": web_ocr_max_images,
            "timeout_sec": web_ocr_timeout,
            "previous_blockers": material_quality_after_media.get("blockers") or [],
            "reason": "douyin_low_material_or_no_video_ocr_input",
        })
        try:
            call_tool(job_dir, "ocr_web_images.py", [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "ocr_web_images.py"),
                "--url",
                url,
                "--job-dir",
                str(job_dir),
                "--platform",
                "douyin",
                "--max-images",
                str(web_ocr_max_images),
                "--timeout-sec",
                str(web_ocr_timeout),
            ], timeout=web_ocr_timeout + 60)
        except Exception as exc:
            append_log(job_dir, f"douyin_web_image_ocr_failed error={type(exc).__name__}: {short_text(str(exc), 240)}")
        ocr, ocr_material = read_ocr_bundle(job_dir)
        material_quality_after_media = assess_material_quality(content, transcript, ocr, comments_probe, config)
        write_json(job_dir / "material_quality.json", material_quality_after_media)
        update_status(job_dir, "web_image_ocr_done", {
            "ocr_status": ocr.get("status"),
            "ocr_text_length": material_quality_after_media.get("ocr_text_length"),
            "ocr_meaningful_text_length": material_quality_after_media.get("ocr_meaningful_text_length"),
            "can_compose_formal": bool(material_quality_after_media.get("can_compose_formal")),
            "blockers": material_quality_after_media.get("blockers") or [],
        })

    update_status(job_dir, "comments_checking")
    call_tool(job_dir, "fetch_comments.py", [
        sys.executable, str(PROJECT_ROOT / "tools" / "fetch_comments.py"),
        "--job-dir", str(job_dir),
        "--url", url,
        "--max-comments", str(config.get("max_comments", 30)),
        "--timeout-sec", str(config.get("comments_timeout_sec", 60)),
    ], timeout=int(config.get("comments_timeout_sec", 60)) + 30)
    comments = read_json(job_dir / "comments.json")
    update_status(job_dir, "comments_done" if comments.get("ok") else "comments_skipped", {"comments_status": comments.get("status")})

    update_status(job_dir, "build_card")
    title, written_card_path, level, card_type, final_status, composer_result, quality_gate, written_card_source = select_written_card(
        url, job_id, job_dir, content, transcript, ocr, comments, config
    )
    update_status(job_dir, "card_built", {
        "card_type": card_type,
        "title": title,
        "content_level": level,
        "written_card_source": written_card_source,
    })
    taxonomy_decision, taxonomy_error = route_taxonomy_for_formal_card(job_dir, card_type, quality_gate, config, config_path)
    if taxonomy_decision:
        update_status(job_dir, "taxonomy_routed", {
            "recommended_path": taxonomy_decision.get("recommended_path"),
            "confidence": taxonomy_decision.get("confidence"),
        })
    elif taxonomy_error:
        update_status(job_dir, "taxonomy_route_failed", {"error": taxonomy_error})

    update_status(job_dir, "writing_siyuan" if storage_target_enabled(config, "siyuan") else "siyuan_skipped")
    writer_exit, write_result = call_writer_if_enabled(config_path, title, written_card_path, job_dir, config)
    result.update({
        "ok": True,
        "title": title,
        "card_type": card_type,
        "content_level": level,
        "card_path": str(written_card_path),
        "written_card_path": str(written_card_path),
        "written_card_source": written_card_source,
        "composer_status": composer_result.get("composer_status"),
        "model_used": composer_result.get("model_used"),
        "model_provider": composer_result.get("model_provider"),
        "quality_gate_passed": quality_gate.get("quality_gate_passed"),
        "failed_checks": quality_gate.get("failed_checks") or [],
        "final_card_type": quality_gate.get("final_card_type") or card_type,
        "content": content,
        "transcript": {k: transcript.get(k) for k in ("ok", "dry_run_ok", "status", "has_speech", "confidence", "failed_reason", "temp_dir", "transcribe_source", "transcript_path", "dyt_exit_code", "resolved_url_used", "video_id", "audio_path", "video_path", "video_path_source", "fallback_triggered", "fallback_reason", "fallback_ok", "fallback_stage")},
        "ocr": {k: ocr.get(k) for k in ("ok", "status", "should_run_ocr", "force_ocr", "input_path", "temp_dir", "frames_dir", "frame_count", "merged_text", "ocr_material_schema", "ocr_material_path", "sampling_strategy")},
        "ocr_decision": ocr_decision,
        "ocr_material": ocr_material,
        "comments": comments,
        "composer": composer_result,
        "quality_gate": quality_gate,
        "taxonomy_decision": taxonomy_decision,
        "taxonomy_error": taxonomy_error,
        "write_result": write_result,
        "writer_exit_code": writer_exit,
        "local_card_preserved": written_card_path.exists(),
        "siyuan_write_ok": bool(write_result.get("ok")),
        "siyuan_write_skipped_reason": write_result.get("skipped_reason") if write_result.get("skipped") else "",
        "final_status": final_status,
        "used_mcp": False,
    })
    if not write_result.get("ok") and not write_result.get("skipped"):
        result["final_status"] = "failed_but_recorded" if card_type == "formal_summary" else final_status

    result = apply_lucas_database_write_policy(result, config, job_dir, write_result, card_type, quality_gate)

    update_status(job_dir, result["final_status"], {
        "siyuan_write_ok": bool(write_result.get("ok")),
        "write_stage": write_result.get("stage"),
        "lucas_database_write_enabled": result.get("lucas_database_write_enabled"),
        "lucas_database_write_policy": result.get("lucas_database_write_policy"),
        "lucas_database_write_ok": result.get("lucas_database_write_ok"),
    })
    write_json(job_dir / "result.json", result)
    emit_json(result)
    return 0


def run_basic_flow(args: argparse.Namespace, config: dict[str, Any], config_path: Path, config_warning: str | None, job_id: str, job_dir: Path) -> int:
    url = args.url
    invalid, invalid_reason = is_invalid_url(url)
    if invalid:
        title, markdown = build_failure_card(url, job_id, job_dir, "url_validation", invalid_reason or "Invalid URL")
        card_type = "failure_card"
        final_status = "failed_but_recorded"
        quality_gate = {
            "quality_gate_passed": False,
            "failed_checks": ["url_validation_failed"],
            "downgrade_reason": invalid_reason or "Invalid URL",
            "final_card_type": card_type,
            "used_mcp": False,
        }
    else:
        title, markdown, _ = build_temporary_card(url, job_id, detect_source_type(url), {}, {}, {}, {})
        card_type = "temporary_card"
        final_status = "completed_low_confidence"
        quality_gate = {
            "quality_gate_passed": False,
            "failed_checks": ["basic_flow_temporary_card"],
            "downgrade_reason": "basic_flow_temporary_card",
            "final_card_type": card_type,
            "used_mcp": False,
        }
    card_path = job_dir / "card.md"
    card_path.write_text(markdown, encoding="utf-8")
    write_json(job_dir / "quality_gate.json", quality_gate)
    update_status(job_dir, "card_built", {"card_type": card_type, "title": title})
    update_status(job_dir, "writing_siyuan" if storage_target_enabled(config, "siyuan") else "siyuan_skipped")
    writer_exit, write_result = call_writer_if_enabled(config_path, title, card_path, job_dir, config)
    result = {
        "ok": True,
        "job_id": job_id,
        "job_dir": str(job_dir),
        "url": redact_url(url),
        "url_redacted": redact_url(url),
        "source_type": detect_source_type(url),
        "card_type": card_type,
        "title": title,
        "card_path": str(card_path),
        "written_card_path": str(card_path),
        "written_card_source": card_path.name,
        "quality_gate_passed": False,
        "failed_checks": quality_gate.get("failed_checks") or [],
        "final_card_type": card_type,
        "quality_gate": quality_gate,
        "final_status": final_status,
        "used_mcp": False,
        "config_path": str(config_path),
        "config_warning": config_warning,
        "write_result": write_result,
        "writer_exit_code": writer_exit,
        "local_card_preserved": card_path.exists(),
        "siyuan_write_ok": bool(write_result.get("ok")),
        "siyuan_write_skipped_reason": write_result.get("skipped_reason") if write_result.get("skipped") else "",
    }
    result = apply_lucas_database_write_policy(result, config, job_dir, write_result, card_type, quality_gate)
    update_status(job_dir, final_status, {
        "siyuan_write_ok": bool(write_result.get("ok")),
        "write_stage": write_result.get("stage"),
        "lucas_database_write_enabled": result.get("lucas_database_write_enabled"),
        "lucas_database_write_policy": result.get("lucas_database_write_policy"),
        "lucas_database_write_ok": result.get("lucas_database_write_ok"),
    })
    write_json(job_dir / "result.json", result)
    emit_json(result)
    return 0


def preview_existing_job(args: argparse.Namespace, config: dict[str, Any], config_path: Path, config_warning: str | None) -> int:
    job_dir = Path(args.preview_job_dir).resolve()
    if not job_dir.exists():
        emit_json({
            "ok": False,
            "preview": True,
            "stage": "load_job",
            "job_dir": str(job_dir),
            "error": "preview job directory does not exist",
            "used_mcp": False,
        })
        return 1

    job_id = job_dir.name
    content = read_json(job_dir / "content.json")
    transcript = read_json(job_dir / "transcript.json")
    ocr, ocr_material = read_ocr_bundle(job_dir)
    comments = read_json(job_dir / "comments.json")
    input_payload = read_json(job_dir / "input.json")
    previous_result = read_json(job_dir / "result.json")
    url = (
        args.url
        or input_payload.get("url")
        or previous_result.get("url")
        or content.get("original_url")
        or content.get("final_url")
        or comments.get("url")
        or ""
    )
    if not url:
        emit_json({
            "ok": False,
            "preview": True,
            "stage": "load_url",
            "job_id": job_id,
            "job_dir": str(job_dir),
            "error": "could not determine source URL for preview",
            "used_mcp": False,
        })
        return 1

    append_log(job_dir, "preview_existing_job: selecting card without SiYuan write")
    title, written_card_path, level, card_type, final_status, composer_result, quality_gate, written_card_source = select_written_card(
        url, job_id, job_dir, content, transcript, ocr, comments, config
    )
    result = {
        "ok": True,
        "preview": True,
        "write_skipped": True,
        "job_id": job_id,
        "job_dir": str(job_dir),
        "url": url,
        "source_type": detect_source_type(url),
        "title": title,
        "card_type": card_type,
        "content_level": level,
        "written_card_path": str(written_card_path),
        "written_card_source": written_card_source,
        "composer_status": composer_result.get("composer_status"),
        "model_used": composer_result.get("model_used"),
        "model_provider": composer_result.get("model_provider"),
        "quality_gate_passed": quality_gate.get("quality_gate_passed"),
        "failed_checks": quality_gate.get("failed_checks") or [],
        "final_card_type": quality_gate.get("final_card_type") or card_type,
        "final_status": final_status,
        "ocr_material_schema": ocr_material.get("schema_name") if ocr_material else "",
        "siyuan_write_ok": False,
        "config_path": str(config_path),
        "config_warning": config_warning,
        "used_mcp": False,
    }
    write_json(job_dir / "preview_result.json", result)
    emit_json(result)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a V1 link job without MCP.")
    parser.add_argument("--url")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--force-ocr", action="store_true", help="Run OCR even when the conditional policy would skip it.")
    parser.add_argument("--source-text", dest="source_text_inline", default="", help="User-supplied text that accompanied the link.")
    parser.add_argument("--source-text-file", default="", help="UTF-8 text or JSON file containing user-supplied source_text.")
    parser.add_argument("--preview-job-dir", help="Preview strict composer write-back selection for an existing job without SiYuan write.")
    args = parser.parse_args()

    config, config_path, config_warning = load_config(Path(args.config).resolve())
    if args.preview_job_dir:
        return preview_existing_job(args, config, config_path, config_warning)
    if not args.url:
        parser.error("--url is required unless --preview-job-dir is provided")

    runtime_dir = Path(config.get("runtime_dir") or "runtime/jobs")
    if not runtime_dir.is_absolute():
        runtime_dir = PROJECT_ROOT / runtime_dir

    job_id = make_job_id()
    job_dir = runtime_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    args.user_supplied_text = read_user_supplied_source_text(args)

    try:
        classification = classify_link(args.url)
        safe_classification = classification_for_artifact(classification)
        update_status(job_dir, "accepted", {
            "job_id": job_id,
            "url": redact_url(args.url),
            "url_redacted": redact_url(args.url),
            "source_type": classification.get("source_type"),
            "route_id": classification.get("route_id"),
            "route_basis": classification.get("route_basis"),
            "platform_id": classification.get("platform_id"),
        })
        write_json(job_dir / "input.json", {
            "url": redact_url(args.url),
            "url_redacted": redact_url(args.url),
            "received_at": now_iso(),
            "job_id": job_id,
            "classification": safe_classification,
            "user_supplied_text_present": bool(args.user_supplied_text),
            "user_supplied_text_length": len(args.user_supplied_text),
            "user_supplied_text_source": "message_text" if args.user_supplied_text else "",
            "user_supplied_text": args.user_supplied_text,
        })
        invalid, invalid_reason = is_invalid_url(args.url)
        if invalid:
            return run_basic_flow(args, config, config_path, config_warning, job_id, job_dir)
        if classification.get("source_type") == "video/douyin" or is_douyin_url(args.url):
            return run_douyin_flow(args, config, config_path, config_warning, job_id, job_dir)
        if is_xiaohongshu_route(classification):
            return run_xiaohongshu_flow(args, config, config_path, config_warning, job_id, job_dir, classification)
        if is_toutiao_article_route(classification):
            return run_toutiao_flow(args, config, config_path, config_warning, job_id, job_dir, classification)
        if is_basic_webpage_route(classification):
            return run_webpage_flow(args, config, config_path, config_warning, job_id, job_dir, classification)
        return run_basic_flow(args, config, config_path, config_warning, job_id, job_dir)
    except Exception as exc:
        result = {
            "ok": False,
            "job_id": job_id,
            "job_dir": str(job_dir),
            "url": redact_url(args.url),
            "final_status": "failed_unrecorded",
            "error": str(exc),
            "used_mcp": False,
        }
        try:
            update_status(job_dir, "failed_unrecorded", {"error": str(exc)})
            write_json(job_dir / "result.json", result)
        finally:
            emit_json(result)
        return 1


if __name__ == "__main__":
    sys.exit(main())
