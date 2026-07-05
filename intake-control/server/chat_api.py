from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.http_chat_adapter import event_from_payload, handle_chat_payload  # noqa: E402
from ai_layer.anthropic_provider import AnthropicProvider  # noqa: E402
from ai_layer.model_catalog import fetch_model_catalog  # noqa: E402
from ai_layer.openai_compatible_provider import OpenAICompatibleProvider  # noqa: E402
from ai_layer.provider_config import build_runtime_config_from_payload, get_public_ai_config, provider_presets_public, save_ai_config  # noqa: E402
from chat_gateway.config_preflight import attach_config_preflight_to_payload, build_config_preflight  # noqa: E402
from chat_gateway.handlers import link_handler  # noqa: E402
from chat_gateway.link_extractor import extract_links  # noqa: E402
from intake_platforms import open_authorization, platform_catalog, test_authorization  # noqa: E402
from storage_config import get_public_storage_config, provider_presets_public as storage_presets_public, save_storage_config, test_storage_connection  # noqa: E402
from submit_card import submit_to_lucas_database  # noqa: E402
from tools.run_link_job import redact_url  # noqa: E402


app = FastAPI(title="Lucas Chat Gateway API", version="0.1.0")
UI_PATH = PROJECT_ROOT / "ui" / "minimal_chat" / "index.html"
UI_ASSET_DIR = PROJECT_ROOT / "ui" / "minimal_chat" / "assets"
REPO_ROOT = PROJECT_ROOT.parent
WECHAT_BRIDGE_SCRIPT = REPO_ROOT / "wechat-bridge" / "scripts" / "start-lucas-wechat-gateway-bridge.ps1"
ALLOWED_OCR_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MEDIA_CACHE_ROOT_NAMES = (
    "LucasTranscribe",
    "LucasVideoOCR",
    "LucasOCRTest",
    "LucasTranscribeWhisperTest",
    "LucasWebImageOCR",
    "LucasCommentBrowser",
    "LucasDouyinVideoSource",
    "LucasTranscribeSetup",
)
MEDIA_CACHE_ROOT_PREFIXES = (
    "LucasToutiaoArticle-",
    "LucasXiaohongshuNote-",
)
ASYNC_BATCH_STAGES = ["内容抓取", "转写 / OCR", "生成知识卡", "写入数据库"]
ASYNC_BATCHES: dict[str, dict[str, Any]] = {}
ASYNC_BATCH_LOCK = threading.RLock()
ASYNC_BATCH_LIMIT = 24
WECHAT_BRIDGE_LOCK = threading.RLock()
WECHAT_BRIDGE_PROCESS: subprocess.Popen[str] | None = None
WECHAT_BRIDGE_STARTED_AT_MS: int | None = None
WECHAT_BRIDGE_LAST_EXIT_CODE: int | None = None
WECHAT_BRIDGE_LOG: deque[str] = deque(maxlen=240)
WECHAT_BRIDGE_CREDENTIALS_FILE = Path.home() / ".cli-bridge" / "account.json"
WECHAT_QR_URL_RE = re.compile(r"(?:Open this QR code URL in a browser:\s*)?(https://liteapp\.weixin\.qq\.com/q/[^\s]+)", re.IGNORECASE)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_safely(path: Path) -> dict[str, Any]:
    try:
        data = _read_json(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _runtime_config_path() -> Path:
    configured = os.environ.get("LUCAS_LINK_PIPELINE_CONFIG_PATH")
    if configured:
        return Path(configured)
    config_path = PROJECT_ROOT / "config" / "link_pipeline.json"
    if not config_path.exists():
        config_path = PROJECT_ROOT / "config" / "link_pipeline.example.json"
    return config_path


def _runtime_dir() -> Path:
    config_path = _runtime_config_path()
    config = _read_json(config_path)
    runtime_dir = Path(config.get("runtime_dir") or "runtime/jobs")
    if not runtime_dir.is_absolute():
        runtime_dir = PROJECT_ROOT / runtime_dir
    return runtime_dir


def _validate_job_id(job_id: str) -> None:
    if not job_id or any(part in job_id for part in ("..", "/", "\\")):
        raise HTTPException(status_code=400, detail="Invalid job_id")


def _validate_asset_name(asset_name: str, *, detail: str = "Invalid asset name") -> None:
    if not asset_name or asset_name.startswith(".") or "/" in asset_name or "\\" in asset_name:
        raise HTTPException(status_code=400, detail=detail)


def _ocr_image_candidates(job_dir: Path) -> list[Path]:
    material = _read_json(job_dir / "ocr_material.json")
    candidates: list[Path] = []

    def add_path(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        path = Path(text)
        if path.suffix.casefold() in ALLOWED_OCR_IMAGE_SUFFIXES:
            candidates.append(path)

    sampling = material.get("sampling") if isinstance(material.get("sampling"), dict) else {}
    frames = sampling.get("frames") if isinstance(sampling.get("frames"), list) else []
    for frame in frames:
        if isinstance(frame, dict):
            add_path(frame.get("path"))

    evidence_items = material.get("evidence_items") if isinstance(material.get("evidence_items"), list) else []
    for item in evidence_items:
        if isinstance(item, dict):
            add_path(item.get("frame_path") or item.get("frame"))

    return candidates


def _resolve_ocr_image(job_dir: Path, image_name: str) -> Path | None:
    for candidate in _ocr_image_candidates(job_dir):
        if candidate.name == image_name and candidate.exists() and candidate.is_file():
            return candidate
    return None


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{int(size)} B"


def _cache_item_stats(path: Path, *, max_files: int = 500) -> dict[str, int | bool]:
    if path.is_file():
        return {"files": 1, "bytes": path.stat().st_size, "truncated": False}
    files = 0
    bytes_total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    files += 1
                    try:
                        bytes_total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    if files >= max_files:
                        return {"files": files, "bytes": bytes_total, "truncated": True}
        except OSError:
            continue
    return {"files": files, "bytes": bytes_total, "truncated": False}


def _media_cache_roots() -> list[Path]:
    temp_root = Path(tempfile.gettempdir()).resolve()
    roots = [temp_root / name for name in MEDIA_CACHE_ROOT_NAMES]
    for item in temp_root.iterdir():
        if item.is_dir() and any(item.name.startswith(prefix) for prefix in MEDIA_CACHE_ROOT_PREFIXES):
            roots.append(item)
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve() if root.exists() else root).casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def _ensure_temp_child(path: Path) -> Path:
    temp_root = Path(tempfile.gettempdir()).resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(temp_root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"Refusing to clean non-TEMP path: {resolved}") from exc
    if resolved == temp_root:
        raise HTTPException(status_code=500, detail="Refusing to clean TEMP root")
    return resolved


def _clear_media_cache(*, dry_run: bool = False, older_than_hours: int | None = None) -> dict[str, Any]:
    if older_than_hours is not None and older_than_hours < 0:
        raise HTTPException(status_code=400, detail="older_than_hours must be >= 0")
    cutoff = time.time() - older_than_hours * 3600 if older_than_hours else None
    roots: list[dict[str, Any]] = []
    total_files = 0
    total_bytes = 0
    deleted_items = 0

    for root in _media_cache_roots():
        root_payload: dict[str, Any] = {
            "path": str(root),
            "exists": root.exists(),
            "deleted_items": 0,
            "files": 0,
            "bytes": 0,
            "bytes_label": "0 B",
            "approximate": False,
            "errors": [],
        }
        if not root.exists():
            roots.append(root_payload)
            continue
        root_resolved = _ensure_temp_child(root)
        candidates = []
        for item in root_resolved.iterdir():
            if cutoff is not None:
                try:
                    if item.stat().st_mtime > cutoff:
                        continue
                except OSError as exc:
                    root_payload["errors"].append(f"{item.name}: {exc}")
                    continue
            candidates.append(item)

        for item in candidates:
            try:
                item_resolved = _ensure_temp_child(item)
                item_resolved.relative_to(root_resolved)
            except (HTTPException, ValueError) as exc:
                root_payload["errors"].append(f"{item.name}: refused ({exc})")
                continue
            try:
                stats = _cache_item_stats(item)
                root_payload["files"] += int(stats["files"])
                root_payload["bytes"] += int(stats["bytes"])
                root_payload["approximate"] = bool(root_payload["approximate"] or stats.get("truncated"))
                if not dry_run:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                root_payload["deleted_items"] += 1
            except Exception as exc:  # noqa: BLE001 - return per-item cleanup failures to the UI.
                root_payload["errors"].append(f"{item.name}: {type(exc).__name__}: {exc}")

        root_payload["bytes_label"] = f">= {_format_bytes(int(root_payload['bytes']))}" if root_payload["approximate"] else _format_bytes(int(root_payload["bytes"]))
        total_files += int(root_payload["files"])
        total_bytes += int(root_payload["bytes"])
        deleted_items += int(root_payload["deleted_items"])
        roots.append(root_payload)

    return {
        "ok": True,
        "status": "dry_run" if dry_run else "cleared",
        "dry_run": dry_run,
        "older_than_hours": older_than_hours,
        "deleted_items": deleted_items,
        "files": total_files,
        "bytes": total_bytes,
        "bytes_label": f">= {_format_bytes(total_bytes)}" if any(root.get("approximate") for root in roots) else _format_bytes(total_bytes),
        "approximate": any(root.get("approximate") for root in roots),
        "roots": roots,
        "used_mcp": False,
    }


def _now_ms() -> int:
    return int(time.time() * 1000)


def _metadata_value(payload: dict[str, Any], key: str) -> Any:
    metadata = payload.get("metadata") if isinstance(payload, dict) else None
    if isinstance(metadata, dict) and key in metadata:
        return metadata.get(key)
    return None


def _effective_dry_run(payload: dict[str, Any], dry_run: bool | None) -> bool:
    payload_value = payload.get("dry_run") if isinstance(payload, dict) else None
    metadata_value = _metadata_value(payload, "dry_run")
    return bool(dry_run if dry_run is not None else (payload_value if payload_value is not None else metadata_value))


def _effective_timeout(payload: dict[str, Any], timeout_sec: int | None) -> int:
    payload_value = payload.get("timeout_sec") if isinstance(payload, dict) else None
    metadata_value = _metadata_value(payload, "timeout_sec")
    return int(timeout_sec if timeout_sec is not None else (payload_value if payload_value is not None else metadata_value or 3600))


def _copy_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _mask_wechat_bridge_log(text: str) -> str:
    masked = str(text or "")
    for pattern in (
        r"(?i)(token|api[_-]?key|authorization|cookie|secret|password)\s*[:=]\s*[^\s]+",
        r"(?i)(LUCAS_DB_API_KEY|SIYUAN_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*=\s*[^\s]+",
    ):
        masked = re.sub(pattern, r"\1=<hidden>", masked)
    return masked


def _append_wechat_bridge_log(line: str) -> None:
    text = _mask_wechat_bridge_log(line).rstrip()
    if text:
        with WECHAT_BRIDGE_LOCK:
            WECHAT_BRIDGE_LOG.append(text)


def _wechat_bridge_qr_code_url_from_log(lines: list[str]) -> str | None:
    for line in reversed(lines):
        match = WECHAT_QR_URL_RE.search(str(line or ""))
        if match:
            return match.group(1).strip()
    return None


def _wechat_bridge_qr_image_url(qr_url: str | None) -> str | None:
    if not qr_url:
        return None
    from urllib.parse import quote

    return f"https://api.qrserver.com/v1/create-qr-code/?size=260x260&margin=12&data={quote(qr_url, safe='')}"


def _drain_wechat_bridge_stream(stream: Any, prefix: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            _append_wechat_bridge_log(f"{prefix}{line}")
    except Exception as exc:
        _append_wechat_bridge_log(f"{prefix}log read failed: {type(exc).__name__}: {exc}")


def _wechat_bridge_running_locked() -> bool:
    return WECHAT_BRIDGE_PROCESS is not None and WECHAT_BRIDGE_PROCESS.poll() is None


def _wechat_bridge_gateway_url() -> str:
    return os.environ.get("LUCAS_CHAT_GATEWAY_URL") or "http://127.0.0.1:3963/api/chat/messages/async"


def _wechat_bridge_start_command(*, force_relogin: bool = False) -> list[str]:
    powershell = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(WECHAT_BRIDGE_SCRIPT),
        "-Cwd",
        str(REPO_ROOT),
        "-GatewayUrl",
        _wechat_bridge_gateway_url(),
    ]
    if force_relogin:
        command.append("-ForceRelogin")
    return command


def _wechat_bridge_status_payload() -> dict[str, Any]:
    global WECHAT_BRIDGE_LAST_EXIT_CODE
    with WECHAT_BRIDGE_LOCK:
        running = _wechat_bridge_running_locked()
        pid = WECHAT_BRIDGE_PROCESS.pid if running and WECHAT_BRIDGE_PROCESS is not None else None
        if WECHAT_BRIDGE_PROCESS is not None and not running:
            WECHAT_BRIDGE_LAST_EXIT_CODE = WECHAT_BRIDGE_PROCESS.poll()
        log_tail = list(WECHAT_BRIDGE_LOG)[-80:]
        qr_code_url = _wechat_bridge_qr_code_url_from_log(log_tail)
        return {
            "ok": True,
            "status": "running" if running else "stopped",
            "running": running,
            "pid": pid,
            "mode": "chat_gateway",
            "mode_label": "微信 Chat Gateway 桥",
            "runtime": "lucas_chat_gateway",
            "command_available": bool(shutil.which("node")),
            "gateway_url": _wechat_bridge_gateway_url(),
            "saved_login_present": WECHAT_BRIDGE_CREDENTIALS_FILE.exists(),
            "credentials_file_present": WECHAT_BRIDGE_CREDENTIALS_FILE.exists(),
            "started_at_ms": WECHAT_BRIDGE_STARTED_AT_MS,
            "last_exit_code": WECHAT_BRIDGE_LAST_EXIT_CODE,
            "script_path": str(WECHAT_BRIDGE_SCRIPT),
            "script_exists": WECHAT_BRIDGE_SCRIPT.exists(),
            "intake_project_root": str(PROJECT_ROOT),
            "repo_root": str(REPO_ROOT),
            "log_tail": log_tail,
            "qr_code_url": qr_code_url,
            "qr_image_url": _wechat_bridge_qr_image_url(qr_code_url),
            "scan_login": True,
            "keep_running_required": True,
            "handles_links": True,
            "handles_plain_text": True,
            "force_relogin_supported": True,
            "used_mcp": False,
        }


def _start_wechat_bridge(*, force_relogin: bool = False) -> dict[str, Any]:
    global WECHAT_BRIDGE_PROCESS, WECHAT_BRIDGE_STARTED_AT_MS, WECHAT_BRIDGE_LAST_EXIT_CODE
    if not WECHAT_BRIDGE_SCRIPT.exists():
        raise HTTPException(status_code=404, detail="WeChat bridge start script not found.")

    with WECHAT_BRIDGE_LOCK:
        if _wechat_bridge_running_locked():
            return _wechat_bridge_status_payload()

        WECHAT_BRIDGE_LOG.clear()
        WECHAT_BRIDGE_LAST_EXIT_CODE = None
        command = _wechat_bridge_start_command(force_relogin=force_relogin)
        env = os.environ.copy()
        env["LUCAS_LINK_PROJECT_ROOT"] = str(PROJECT_ROOT)
        env["LUCAS_CHAT_GATEWAY_URL"] = _wechat_bridge_gateway_url()
        env["LUCAS_WECHAT_FORCE_RELOGIN"] = "1" if force_relogin else "0"
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            WECHAT_BRIDGE_PROCESS = subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                env=env,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=f"PowerShell unavailable: {exc}") from exc
        WECHAT_BRIDGE_STARTED_AT_MS = _now_ms()
        _append_wechat_bridge_log(f"Started Lucas WeChat Chat Gateway bridge pid={WECHAT_BRIDGE_PROCESS.pid}")
        if WECHAT_BRIDGE_PROCESS.stdout is not None:
            threading.Thread(
                target=_drain_wechat_bridge_stream,
                args=(WECHAT_BRIDGE_PROCESS.stdout, ""),
                daemon=True,
            ).start()
        if WECHAT_BRIDGE_PROCESS.stderr is not None:
            threading.Thread(
                target=_drain_wechat_bridge_stream,
                args=(WECHAT_BRIDGE_PROCESS.stderr, "stderr: "),
                daemon=True,
            ).start()
    return _wechat_bridge_status_payload()


def _terminate_wechat_bridge_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except Exception as exc:
            _append_wechat_bridge_log(f"Process tree stop failed, falling back to terminate: {type(exc).__name__}: {exc}")
    process.terminate()


def _stop_wechat_bridge() -> dict[str, Any]:
    global WECHAT_BRIDGE_LAST_EXIT_CODE
    with WECHAT_BRIDGE_LOCK:
        process = WECHAT_BRIDGE_PROCESS
        if process is None or process.poll() is not None:
            if process is not None:
                WECHAT_BRIDGE_LAST_EXIT_CODE = process.poll()
            return _wechat_bridge_status_payload()
        _terminate_wechat_bridge_process_tree(process)
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    with WECHAT_BRIDGE_LOCK:
        WECHAT_BRIDGE_LAST_EXIT_CODE = process.returncode
        _append_wechat_bridge_log(f"Stopped Lucas WeChat Chat Gateway bridge exit_code={process.returncode}")
    return _wechat_bridge_status_payload()


def _async_stage_from_status(status: str) -> tuple[int, int, str]:
    text = (status or "").casefold()
    if not text:
        return 0, 8, "任务已接收"
    if text in {"pending"}:
        return -1, 0, "排队中"
    if text in {"accepted"}:
        return 0, 10, "任务已接收"
    if "write" in text or "siyuan" in text or "lucas_database" in text:
        return 3, 92, "写入数据库"
    if text.startswith("completed") or text in {"done", "finished"}:
        return 3, 100, "已完成"
    if "failed" in text or "timeout" in text:
        return 3, 100, "处理失败"
    if "taxonomy" in text:
        return 2, 88, "分类路由"
    if "card" in text or "compose" in text or "quality_gate" in text:
        return 2, 78, "生成知识卡"
    if "transcrib" in text:
        return 1, 45, "转写中"
    if "ocr" in text:
        return 1, 58, "OCR 识别中"
    if "comment" in text:
        return 1, 66, "评论增强检查"
    if "material_quality" in text:
        return 1, 70, "材料质量检查"
    if "fetch" in text or "page" in text or "reader" in text or "source" in text:
        return 0, 24, "内容抓取"
    return 0, 18, status


def _apply_stage_fields(item: dict[str, Any], status: str, *, status_payload: dict[str, Any] | None = None) -> None:
    stage_index, progress, label = _async_stage_from_status(status)
    item["stage"] = status or item.get("stage") or ""
    item["stage_index"] = stage_index
    item["progress"] = progress
    item["stage_label"] = label
    item["stages"] = ASYNC_BATCH_STAGES
    if status_payload:
        item["stage_updated_at"] = status_payload.get("updated_at")
        if status_payload.get("error"):
            item["error"] = status_payload.get("error")


def _initial_async_item(index: int, url: str, *, in_progress: bool, batch_id: str) -> dict[str, Any]:
    item = link_handler._pending_queue_item(index, url)
    item.update({
        "title": f"链接处理 #{index:02d}",
        "detail": url,
        "item_type": "url",
        "batch_id": batch_id,
        "request_id": batch_id,
        "queue_status": "in_progress" if in_progress else "pending",
        "final_status": "accepted" if in_progress else "pending",
    })
    _apply_stage_fields(item, "accepted" if in_progress else "pending")
    return item


def _prune_async_batches_locked() -> None:
    if len(ASYNC_BATCHES) <= ASYNC_BATCH_LIMIT:
        return
    ordered = sorted(ASYNC_BATCHES.items(), key=lambda entry: int(entry[1].get("created_at_ms") or 0))
    for batch_id, batch in ordered:
        queue = link_handler._build_queue(batch.get("items") or [], mode=str(batch.get("mode") or "single"))
        if queue.get("unfinished", 0) == 0 and len(ASYNC_BATCHES) > ASYNC_BATCH_LIMIT:
            ASYNC_BATCHES.pop(batch_id, None)
    while len(ASYNC_BATCHES) > ASYNC_BATCH_LIMIT:
        oldest_id = min(ASYNC_BATCHES.items(), key=lambda entry: int(entry[1].get("created_at_ms") or 0))[0]
        ASYNC_BATCHES.pop(oldest_id, None)


def _queue_snapshot_from_batch(batch: dict[str, Any]) -> dict[str, Any]:
    items = _copy_jsonable(batch.get("items") or [])
    queue = link_handler._build_queue(items, mode=str(batch.get("mode") or "single"))
    queue["batch_id"] = batch.get("batch_id")
    queue["source"] = "async_poll"
    queue["created_at"] = batch.get("created_at_ms")
    queue["updated_at"] = batch.get("updated_at_ms")
    return queue


def _build_async_batch_reply(queue: dict[str, Any]) -> str:
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    if len(items) == 1 and not queue.get("unfinished"):
        reply_text = str(items[0].get("reply_text") or "").strip()
        if reply_text:
            return reply_text
    return link_handler._build_queue_reply(queue, dry_run=False)


def _batch_response(batch_id: str) -> dict[str, Any]:
    with ASYNC_BATCH_LOCK:
        batch = ASYNC_BATCHES.get(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        queue = _queue_snapshot_from_batch(batch)
        items = queue.get("items") or []
        single_job_id = items[0].get("job_id") if len(items) == 1 else None
        errors = [str(item.get("error")) for item in items if item.get("error")]
        status = link_handler._queue_status_to_response_status(str(queue.get("status") or ""))
        ok = queue.get("failed", 0) == 0
        reply_text = batch.get("reply_text") or _build_async_batch_reply(queue)
        result = items[0].get("result") if len(items) == 1 and isinstance(items[0].get("result"), dict) else {}
        response = {
            "ok": ok,
            "reply_text": reply_text,
            "job_id": single_job_id,
            "status": status,
            "handled_by": "link_handler",
            "error": errors[0] if errors else None,
            "data": {
                "async": True,
                "batch_id": batch_id,
                "poll_url": f"/api/chat/batches/{batch_id}",
                "queue": queue,
                "job_ids": [item.get("job_id") for item in items if item.get("job_id")],
                "result": result,
                "runner_called": True,
                "source_text_present": bool(batch.get("source_text_present") or batch.get("source_text")),
                "source_text_length": int(batch.get("source_text_length") or len(str(batch.get("source_text") or ""))),
                "used_mcp": False,
            },
        }
        return attach_config_preflight_to_payload(response, include_ai=True, include_storage=True)


def _set_batch_item(batch_id: str, item_index: int, updates: dict[str, Any]) -> None:
    with ASYNC_BATCH_LOCK:
        batch = ASYNC_BATCHES.get(batch_id)
        if not batch:
            return
        for item in batch.get("items") or []:
            if item.get("index") == item_index:
                item.update(updates)
                break
        batch["updated_at_ms"] = _now_ms()


def _cancelled_item_indexes_locked(batch: dict[str, Any]) -> set[int]:
    raw_indexes = batch.setdefault("cancelled_item_indexes", set())
    if isinstance(raw_indexes, set):
        return raw_indexes
    indexes: set[int] = set()
    if isinstance(raw_indexes, list):
        for value in raw_indexes:
            try:
                indexes.add(int(value))
            except (TypeError, ValueError):
                continue
    batch["cancelled_item_indexes"] = indexes
    return indexes


def _is_async_batch_item_cancelled(batch_id: str, item_index: int) -> bool:
    with ASYNC_BATCH_LOCK:
        batch = ASYNC_BATCHES.get(batch_id)
        if not batch:
            return True
        return item_index in _cancelled_item_indexes_locked(batch)


def _remember_async_process(batch_id: str, item_index: int, process: subprocess.Popen[str]) -> None:
    with ASYNC_BATCH_LOCK:
        batch = ASYNC_BATCHES.get(batch_id)
        if not batch:
            return
        processes = batch.setdefault("processes", {})
        if isinstance(processes, dict):
            processes[item_index] = process


def _forget_async_process(batch_id: str, item_index: int) -> None:
    with ASYNC_BATCH_LOCK:
        batch = ASYNC_BATCHES.get(batch_id)
        if not batch:
            return
        processes = batch.get("processes")
        if isinstance(processes, dict):
            processes.pop(item_index, None)


def _terminate_async_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=8,
                check=False,
            )
            if process.poll() is not None:
                return
        except Exception:
            pass
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _delete_async_batch_item(batch_id: str, item_index: int) -> dict[str, Any]:
    _validate_job_id(batch_id)
    if item_index < 1:
        raise HTTPException(status_code=400, detail="Invalid item_index")

    process: subprocess.Popen[str] | None = None
    with ASYNC_BATCH_LOCK:
        batch = ASYNC_BATCHES.get(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        _cancelled_item_indexes_locked(batch).add(item_index)
        processes = batch.get("processes")
        if isinstance(processes, dict):
            process = processes.pop(item_index, None)
        items = batch.get("items") if isinstance(batch.get("items"), list) else []
        kept_items = [item for item in items if int(item.get("index") or 0) != item_index]
        if len(kept_items) == len(items) and process is None:
            raise HTTPException(status_code=404, detail="Batch item not found")
        batch["items"] = kept_items
        batch["updated_at_ms"] = _now_ms()

    if process is not None:
        _terminate_async_process_tree(process)
    return _batch_response(batch_id)


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _discover_job_dir_for_url(batch_id: str, url: str, started_at: float) -> Path | None:
    target_url = redact_url(url)
    claimed = set()
    with ASYNC_BATCH_LOCK:
        batch = ASYNC_BATCHES.get(batch_id) or {}
        claimed = set(batch.get("claimed_job_ids") or [])

    candidates: list[tuple[float, Path, str]] = []
    runtime_dir = _runtime_dir()
    if not runtime_dir.exists():
        return None
    for job_dir in runtime_dir.iterdir():
        if not job_dir.is_dir():
            continue
        input_path = job_dir / "input.json"
        status_path = job_dir / "status.json"
        if not input_path.exists() and not status_path.exists():
            continue
        try:
            newest_mtime = max(
                input_path.stat().st_mtime if input_path.exists() else 0,
                status_path.stat().st_mtime if status_path.exists() else 0,
            )
        except OSError:
            continue
        if newest_mtime < started_at - 3:
            continue
        input_payload = _read_json_safely(input_path)
        status_payload = _read_json_safely(status_path)
        job_id = str(input_payload.get("job_id") or status_payload.get("job_id") or job_dir.name)
        if job_id in claimed:
            continue
        job_url = str(input_payload.get("url_redacted") or input_payload.get("url") or status_payload.get("url_redacted") or status_payload.get("url") or "")
        if job_url and job_url != target_url:
            continue
        candidates.append((newest_mtime, job_dir, job_id))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, job_dir, job_id = candidates[0]
    with ASYNC_BATCH_LOCK:
        batch = ASYNC_BATCHES.get(batch_id)
        if not batch:
            return None
        claimed = set(batch.get("claimed_job_ids") or [])
        if job_id in claimed:
            return None
        claimed.add(job_id)
        batch["claimed_job_ids"] = sorted(claimed)
    return job_dir


def _poll_job_status_into_item(batch_id: str, item_index: int, job_dir: Path, url: str) -> None:
    status_payload = _read_json_safely(job_dir / "status.json")
    status = str(status_payload.get("status") or "accepted")
    updates: dict[str, Any] = {
        "job_id": status_payload.get("job_id") or job_dir.name,
        "job_dir": str(job_dir),
        "queue_status": "in_progress",
        "runner_called": True,
        "final_status": status,
        "url": url,
    }
    _apply_stage_fields(updates, status, status_payload=status_payload)
    _set_batch_item(batch_id, item_index, updates)


def _run_async_queue_item(batch_id: str, item_index: int, url: str, timeout_sec: int, source_text: str) -> None:
    if _is_async_batch_item_cancelled(batch_id, item_index):
        return
    started_at = time.time()
    stdout_path = Path(tempfile.gettempdir()) / f"lucas-link-{batch_id}-{item_index}.stdout.json"
    stderr_path = Path(tempfile.gettempdir()) / f"lucas-link-{batch_id}-{item_index}.stderr.log"
    source_text_path: Path | None = None
    process: subprocess.Popen[str] | None = None
    job_dir: Path | None = None
    timed_out = False

    _set_batch_item(batch_id, item_index, {
        "queue_status": "in_progress",
        "runner_called": True,
        "final_status": "accepted",
    })

    try:
        cmd = [sys.executable, str(link_handler.RUNNER), "--url", url]
        source_text_path = link_handler._write_source_text_file(source_text)
        if source_text_path is not None:
            cmd.extend(["--source-text-file", str(source_text_path)])
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout_fh, stderr_path.open("w", encoding="utf-8", errors="replace") as stderr_fh:
            process = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                env=env,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=stdout_fh,
                stderr=stderr_fh,
                creationflags=link_handler._runner_creationflags(),
            )
            _remember_async_process(batch_id, item_index, process)
            while process.poll() is None:
                if _is_async_batch_item_cancelled(batch_id, item_index):
                    _terminate_async_process_tree(process)
                    return
                if time.time() - started_at > timeout_sec:
                    timed_out = True
                    process.kill()
                    break
                if job_dir is None:
                    job_dir = _discover_job_dir_for_url(batch_id, url, started_at)
                if job_dir is not None:
                    _poll_job_status_into_item(batch_id, item_index, job_dir, url)
                time.sleep(0.8)
            process.wait(timeout=5)
    except Exception as exc:
        result = {
            "ok": False,
            "url": url,
            "final_status": "runner_exception",
            "error": f"{type(exc).__name__}: {exc}",
            "used_mcp": False,
        }
        completed = link_handler._completed_queue_item(item_index, url, None, result, "")
        completed.update({"batch_id": batch_id, "request_id": batch_id, "detail": url, "item_type": "url"})
        _apply_stage_fields(completed, "runner_exception")
        _set_batch_item(batch_id, item_index, completed)
        return
    finally:
        _forget_async_process(batch_id, item_index)
        link_handler._remove_temp_file(source_text_path)

    stdout = _read_text_file(stdout_path)
    stderr = link_handler._mask_secrets(_read_text_file(stderr_path))
    try:
        stdout_path.unlink(missing_ok=True)
        stderr_path.unlink(missing_ok=True)
    except Exception:
        pass

    if timed_out:
        result = {
            "ok": False,
            "url": url,
            "final_status": "timeout",
            "error": f"run_link_job.py timed out after {timeout_sec}s",
            "used_mcp": False,
        }
        exit_code = None
    else:
        result = link_handler._parse_runner_stdout(stdout)
        result = link_handler._read_result_json(result)
        exit_code = process.returncode if process is not None else None
        if not result:
            result = {
                "ok": False,
                "url": url,
                "final_status": "runner_invalid_output",
                "error": "run_link_job.py did not return parseable JSON.",
                "stdout_tail": link_handler._mask_secrets(stdout[-1000:]),
                "stderr_tail": stderr[-1000:],
                "used_mcp": False,
            }

    completed = link_handler._completed_queue_item(item_index, url, exit_code, result, stderr)
    completed.update({
        "batch_id": batch_id,
        "request_id": batch_id,
        "detail": url,
        "item_type": "url",
        "reply_text": link_handler._build_reply(result),
    })
    _apply_stage_fields(completed, str(completed.get("final_status") or result.get("final_status") or "completed"))
    completed["progress"] = 100
    _set_batch_item(batch_id, item_index, completed)


def _run_async_batch(batch_id: str, timeout_sec: int) -> None:
    with ASYNC_BATCH_LOCK:
        batch = ASYNC_BATCHES.get(batch_id)
        if not batch:
            return
        items = _copy_jsonable(batch.get("items") or [])
        source_text = str(batch.get("source_text") or "")
    workers = link_handler._max_parallel_jobs(len(items))
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_run_async_queue_item, batch_id, int(item["index"]), str(item["url"]), timeout_sec, source_text)
                for item in items
            ]
            for future in futures:
                future.result()
    finally:
        with ASYNC_BATCH_LOCK:
            batch = ASYNC_BATCHES.get(batch_id)
            if batch:
                queue = _queue_snapshot_from_batch(batch)
                batch["reply_text"] = _build_async_batch_reply(queue)
                batch["updated_at_ms"] = _now_ms()


def _create_async_batch(payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    event = event_from_payload(payload)
    links = extract_links(event.text)
    if not links.primary_url:
        response = handle_chat_payload(payload, dry_run=False, timeout_sec=timeout_sec)
        result = response.to_dict()
        result.setdefault("data", {})["async"] = False
        return result

    batch_id = f"batch-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    source_text = link_handler._source_text_from_event(event)
    workers = link_handler._max_parallel_jobs(len(links.urls))
    items = [
        _initial_async_item(index, url, in_progress=index <= workers, batch_id=batch_id)
        for index, url in enumerate(links.urls, start=1)
    ]
    batch = {
        "batch_id": batch_id,
        "message_id": event.message_id,
        "mode": "parallel" if len(items) > 1 else "single",
        "created_at_ms": _now_ms(),
        "updated_at_ms": _now_ms(),
        "items": items,
        "claimed_job_ids": [],
        "source_text": source_text,
        "source_text_present": bool(source_text),
        "source_text_length": len(source_text),
        "reply_text": "",
    }
    with ASYNC_BATCH_LOCK:
        ASYNC_BATCHES[batch_id] = batch
        _prune_async_batches_locked()

    thread = threading.Thread(target=_run_async_batch, args=(batch_id, timeout_sec), daemon=True)
    thread.start()
    response = _batch_response(batch_id)
    response["status"] = "batch_running"
    response["reply_text"] = "已创建后台处理队列，前端将实时刷新阶段进度。"
    response["data"]["url"] = links.primary_url
    response["data"]["urls"] = links.urls
    response["data"]["extra_url_count"] = links.extra_url_count
    response["data"]["source_text_present"] = bool(source_text)
    response["data"]["source_text_length"] = len(source_text)
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "chat_gateway", "used_mcp": False}


@app.get("/api/system/status")
def system_status() -> dict[str, Any]:
    config_path = _runtime_config_path()
    runtime_dir = _runtime_dir()
    runtime_readable = runtime_dir.exists() and runtime_dir.is_dir()
    latest_job_count = 0
    if runtime_readable:
        try:
            latest_job_count = sum(1 for item in runtime_dir.iterdir() if item.is_dir())
        except OSError:
            runtime_readable = False
    preflight = build_config_preflight(include_ai=True, include_storage=True)
    runtime_ok = runtime_readable and link_handler.RUNNER.exists()
    ok = runtime_ok and bool(preflight.get("ok"))
    return {
        "ok": ok,
        "status": "ok" if ok else "degraded",
        "service": "chat_gateway",
        "api": {
            "version": app.version,
            "health_ok": True,
        },
        "runtime": {
            "project_root": str(PROJECT_ROOT),
            "config_path": str(config_path),
            "runtime_dir": str(runtime_dir),
            "ui_path": str(UI_PATH),
            "jobs_dir_configured": True,
            "jobs_dir_readable": runtime_readable,
            "runner_available": link_handler.RUNNER.exists(),
            "latest_job_count": latest_job_count,
        },
        "ai": preflight.get("ai", {}),
        "storage": preflight.get("storage", {}),
        "config_warnings": preflight.get("warnings", []),
        "capabilities": {
            "chat_gateway": True,
            "link_intake": True,
            "douyin_level3_transcript": True,
            "conditional_ocr": True,
            "comments_enhancement": "opportunistic",
            "formal_card_requires_composer_and_quality_gate": True,
            "mcp_required_for_production": False,
        },
        "used_mcp": False,
    }


@app.post("/api/system/cache/clear")
def post_system_cache_clear(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    return _clear_media_cache(
        dry_run=bool(data.get("dry_run")) if isinstance(data, dict) else False,
        older_than_hours=data.get("older_than_hours") if isinstance(data, dict) else None,
    )


@app.get("/api/ai/providers")
def get_ai_providers() -> dict[str, Any]:
    return {
        "ok": True,
        "providers": provider_presets_public(),
        "active_provider": get_public_ai_config()["active_provider"],
        "used_mcp": False,
    }


@app.get("/api/ai/config")
def get_ai_config() -> dict[str, Any]:
    payload = get_public_ai_config()
    payload["used_mcp"] = False
    return payload


@app.post("/api/ai/config")
def post_ai_config(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = save_ai_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["used_mcp"] = False
    return result


@app.post("/api/ai/test")
def post_ai_test(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        config = build_runtime_config_from_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    timeout_sec = int(payload.get("timeout_sec") or 20)
    prompt = "请只回复 OK，用于测试模型 API 连接。"
    if config.protocol == "anthropic_messages":
        provider = AnthropicProvider(api_key=config.api_key, base_url=config.base_url, model_name=config.model)
    elif config.protocol == "openai_compatible":
        provider = OpenAICompatibleProvider(
            provider_name=config.provider_id,
            api_key=config.api_key,
            base_url=config.base_url,
            model_name=config.model,
            supports_json_mode=config.supports_json_mode,
        )
    else:
        raise HTTPException(status_code=400, detail=f"unsupported provider protocol: {config.protocol}")

    result = provider.generate_text(
        prompt,
        system_prompt="你是一个 API 连通性测试助手。",
        metadata={"timeout_sec": timeout_sec, "max_tokens": 16},
    )
    return {
        "ok": result.ok,
        "status": "connected" if result.ok else "failed",
        "provider": config.masked(),
        "reply_text": result.text[:200],
        "error": result.error,
        "raw_usage": result.raw_usage,
        "latency_ms": result.latency_ms,
        "used_mcp": False,
    }


@app.post("/api/ai/models")
def post_ai_models(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        config = build_runtime_config_from_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    timeout_sec = int(payload.get("timeout_sec") or 20)
    return fetch_model_catalog(config, timeout_sec=timeout_sec)


@app.get("/api/storage/providers")
def get_storage_providers() -> dict[str, Any]:
    return {
        "ok": True,
        "providers": storage_presets_public(),
        "active_provider": get_public_storage_config()["active_provider"],
        "used_mcp": False,
    }


@app.get("/api/storage/config")
def get_storage_config() -> dict[str, Any]:
    return get_public_storage_config()


@app.get("/api/intake/platforms")
def get_intake_platforms(url: str | None = Query(default=None)) -> dict[str, Any]:
    return platform_catalog(url)


@app.post("/api/intake/authorize/{platform_id}")
def post_intake_authorize(
    platform_id: str,
    payload: dict[str, Any] | None = None,
    dry_run: bool | None = Query(default=False),
) -> dict[str, Any]:
    data = payload or {}
    result = open_authorization(
        platform_id,
        url=data.get("url") if isinstance(data, dict) else None,
        browser_path=data.get("browser_path") if isinstance(data, dict) else None,
        dry_run=bool(dry_run),
        force_reopen=bool(data.get("force_reopen")) if isinstance(data, dict) else False,
    )
    if not result.get("ok") and result.get("status") == "unknown_platform":
        raise HTTPException(status_code=404, detail=result.get("error") or "Unknown platform")
    if not result.get("ok") and result.get("status") == "browser_unavailable":
        raise HTTPException(status_code=503, detail=result.get("error") or "Browser unavailable")
    return result


@app.post("/api/intake/authorize/{platform_id}/test")
def post_intake_authorize_test(
    platform_id: str,
    payload: dict[str, Any] | None = None,
    timeout_sec: int | None = Query(default=12, ge=4, le=30),
) -> dict[str, Any]:
    data = payload or {}
    result = test_authorization(
        platform_id,
        url=data.get("url") if isinstance(data, dict) else None,
        browser_path=data.get("browser_path") if isinstance(data, dict) else None,
        timeout_sec=int(timeout_sec or 12),
    )
    if not result.get("ok") and result.get("status") == "unknown_platform":
        raise HTTPException(status_code=404, detail=result.get("error") or "Unknown platform")
    if not result.get("ok") and result.get("status") == "browser_unavailable":
        raise HTTPException(status_code=503, detail=result.get("error") or "Browser unavailable")
    return result


@app.get("/api/wechat-bridge/status")
def get_wechat_bridge_status() -> dict[str, Any]:
    return _wechat_bridge_status_payload()


@app.post("/api/wechat-bridge/start")
def post_wechat_bridge_start(
    payload: dict[str, Any] | None = None,
    dry_run: bool | None = Query(default=False),
) -> dict[str, Any]:
    data = payload or {}
    force_relogin = bool(data.get("force_relogin") or data.get("forceRelogin")) if isinstance(data, dict) else False
    command = _wechat_bridge_start_command(force_relogin=force_relogin)
    if dry_run or bool(data.get("dry_run")):
        result = _wechat_bridge_status_payload()
        result.update({
            "status": "dry_run",
            "running": False,
            "command": command,
            "would_start": True,
            "force_relogin": force_relogin,
        })
        return result
    if force_relogin:
        _stop_wechat_bridge()
    return _start_wechat_bridge(force_relogin=force_relogin)


@app.post("/api/wechat-bridge/stop")
def post_wechat_bridge_stop() -> dict[str, Any]:
    return _stop_wechat_bridge()


@app.post("/api/storage/config")
def post_storage_config(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return save_storage_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/storage/test")
def post_storage_test(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return test_storage_connection(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/submit")
def post_submit(payload: dict[str, Any], timeout_sec: int | None = Query(default=None, ge=1, le=120)) -> dict[str, Any]:
    result = submit_to_lucas_database(payload, timeout_sec=timeout_sec or int(payload.get("timeout_sec") or 20))
    response = dict(result)
    request_payload = response.pop("ingest_request", None)
    if isinstance(request_payload, dict):
        card = request_payload.get("card") if isinstance(request_payload.get("card"), dict) else {}
        response["card"] = {
            "schema_name": card.get("schema_name"),
            "schema_version": card.get("schema_version"),
            "display_title": card.get("display_title"),
            "card_type": card.get("card_type"),
            "content_level": card.get("content_level"),
            "quality_level": card.get("quality_level"),
        }
    return response


@app.get("/ui", response_class=HTMLResponse)
def ui() -> HTMLResponse:
    if not UI_PATH.exists():
        raise HTTPException(status_code=404, detail="UI not found")
    return HTMLResponse(
        UI_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/ui/minimal_chat/assets/{asset_name}")
def ui_asset(asset_name: str) -> FileResponse:
    _validate_asset_name(asset_name)
    asset_path = UI_ASSET_DIR / asset_name
    if not asset_path.is_file():
        raise HTTPException(status_code=404, detail="UI asset not found")
    return FileResponse(asset_path)


@app.post("/api/chat/messages")
def post_chat_message(
    payload: dict[str, Any],
    dry_run: bool | None = Query(default=None),
    timeout_sec: int | None = Query(default=None, ge=1, le=3600),
) -> dict[str, Any]:
    response = handle_chat_payload(payload, dry_run=dry_run, timeout_sec=timeout_sec)
    return response.to_dict()


@app.post("/api/chat/messages/async")
def post_chat_message_async(
    payload: dict[str, Any],
    dry_run: bool | None = Query(default=None),
    timeout_sec: int | None = Query(default=None, ge=1, le=3600),
) -> dict[str, Any]:
    effective_timeout = _effective_timeout(payload, timeout_sec)
    if _effective_dry_run(payload, dry_run):
        response = handle_chat_payload(payload, dry_run=True, timeout_sec=effective_timeout).to_dict()
        response.setdefault("data", {})["async"] = False
        return response
    return _create_async_batch(payload, effective_timeout)


@app.get("/api/chat/batches/{batch_id}")
def get_chat_batch(batch_id: str) -> dict[str, Any]:
    _validate_job_id(batch_id)
    return _batch_response(batch_id)


@app.delete("/api/chat/batches/{batch_id}/items/{item_index}")
def delete_chat_batch_item(batch_id: str, item_index: int) -> dict[str, Any]:
    return _delete_async_batch_item(batch_id, item_index)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    _validate_job_id(job_id)

    job_dir = _runtime_dir() / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "ok": True,
        "job_id": job_id,
        "job_dir": str(job_dir),
        "status": _read_json(job_dir / "status.json"),
        "result": _read_json(job_dir / "result.json"),
        "used_mcp": False,
    }


@app.get("/api/jobs/{job_id}/ocr-images/{image_name}")
def get_job_ocr_image(job_id: str, image_name: str) -> FileResponse:
    _validate_job_id(job_id)
    _validate_asset_name(image_name, detail="Invalid image name")
    if Path(image_name).suffix.casefold() not in ALLOWED_OCR_IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported image type")

    job_dir = _runtime_dir() / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    image_path = _resolve_ocr_image(job_dir, image_name)
    if image_path is None:
        raise HTTPException(status_code=404, detail="OCR image not found")
    return FileResponse(image_path)


def main() -> None:
    import uvicorn

    uvicorn.run("server.chat_api:app", host="127.0.0.1", port=3963, reload=False)


if __name__ == "__main__":
    main()
