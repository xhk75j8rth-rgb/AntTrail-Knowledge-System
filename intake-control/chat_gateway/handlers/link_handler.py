from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

from chat_gateway.link_extractor import LinkExtractionResult, extract_context_text
from chat_gateway.message_schema import MessageEvent
from chat_gateway.response_schema import HandlerResponse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNNER = PROJECT_ROOT / "tools" / "run_link_job.py"
MAX_PARALLEL_ENV = "LUCAS_LINK_HANDLER_MAX_PARALLEL_JOBS"
MIN_SOURCE_TEXT_CHARS = 8
SECRET_PATTERNS = (
    re.compile(r"(?i)(token[\"'\s:=]+)([A-Za-z0-9_\-./+=]{8,})"),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)([A-Za-z0-9_\-./+=]{8,})"),
)


def _mask_secrets(text: str) -> str:
    output = text or ""
    for pattern in SECRET_PATTERNS:
        output = pattern.sub(r"\1***", output)
    return output


def _parse_runner_stdout(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
    return {}


def _read_result_json(result: dict[str, Any]) -> dict[str, Any]:
    job_dir = result.get("job_dir")
    if not job_dir:
        return result
    path = Path(str(job_dir)) / "result.json"
    if not path.exists():
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return result


def _read_job_json(result: dict[str, Any], filename: str) -> dict[str, Any]:
    job_dir = result.get("job_dir")
    if not job_dir:
        return {}
    path = Path(str(job_dir)) / filename
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _runner_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _source_text_from_event(event: MessageEvent) -> str:
    text = _mask_secrets(extract_context_text(event.text))
    return text if len(text) >= MIN_SOURCE_TEXT_CHARS else ""


def _write_source_text_file(source_text: str) -> Path | None:
    if not source_text:
        return None
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        errors="replace",
        suffix=".json",
        prefix="lucas-link-source-",
        delete=False,
    )
    path = Path(handle.name)
    with handle:
        json.dump(
            {
                "schema_name": "UserSuppliedSourceTextV1",
                "schema_version": 1,
                "source": "message_text",
                "source_text": source_text,
            },
            handle,
            ensure_ascii=False,
        )
    return path


def _remove_temp_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _max_parallel_jobs(total: int) -> int:
    try:
        configured = int(os.environ.get(MAX_PARALLEL_ENV, "2"))
    except ValueError:
        configured = 2
    return max(1, min(total, configured, 4))


def _run_link_job(
    url: str,
    timeout_sec: int,
    runner_env: Mapping[str, str] | None,
    source_text: str = "",
) -> tuple[int | None, dict[str, Any], str]:
    cmd = [sys.executable, str(RUNNER), "--url", url]
    source_text_path = _write_source_text_file(source_text)
    if source_text_path is not None:
        cmd.extend(["--source-text-file", str(source_text_path)])
    env = dict(runner_env) if runner_env is not None else os.environ.copy()
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_sec,
            creationflags=_runner_creationflags(),
        )
    except subprocess.TimeoutExpired as exc:
        return None, {
            "ok": False,
            "url": url,
            "final_status": "timeout",
            "error": f"run_link_job.py timed out after {timeout_sec}s",
            "stdout_tail": _mask_secrets((exc.stdout or "")[-1000:] if isinstance(exc.stdout, str) else ""),
            "stderr_tail": _mask_secrets((exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else ""),
            "used_mcp": False,
        }, ""
    finally:
        _remove_temp_file(source_text_path)

    result = _parse_runner_stdout(completed.stdout)
    result = _read_result_json(result)
    if not result:
        result = {
            "ok": False,
            "url": url,
            "final_status": "runner_invalid_output",
            "error": "run_link_job.py did not return parseable JSON.",
            "stdout_tail": _mask_secrets(completed.stdout[-1000:]),
            "stderr_tail": _mask_secrets(completed.stderr[-1000:]),
            "used_mcp": False,
        }
    return completed.returncode, result, _mask_secrets(completed.stderr or "")


def _run_queue_item(
    index: int,
    url: str,
    timeout_sec: int,
    runner_env: Mapping[str, str] | None,
    source_text: str,
) -> dict[str, Any]:
    try:
        exit_code, result, stderr = _run_link_job(url, timeout_sec, runner_env, source_text)
    except Exception as exc:
        exit_code = None
        stderr = ""
        result = {
            "ok": False,
            "url": url,
            "final_status": "runner_exception",
            "error": f"{type(exc).__name__}: {exc}",
            "used_mcp": False,
        }
    return _completed_queue_item(index, url, exit_code, result, stderr)


def _run_link_jobs_parallel(
    urls: list[str],
    timeout_sec: int,
    runner_env: Mapping[str, str] | None,
    source_text: str,
) -> list[dict[str, Any]]:
    workers = _max_parallel_jobs(len(urls))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_run_queue_item, index, queued_url, timeout_sec, runner_env, source_text)
            for index, queued_url in enumerate(urls, start=1)
        ]
        return [future.result() for future in futures]


def _status_label(result: dict[str, Any]) -> str:
    write_result = result.get("write_result") or {}
    if not result.get("ok"):
        return "失败"
    if result.get("siyuan_write_ok") or write_result.get("ok"):
        return "降级" if result.get("card_type") == "temporary_card" else "成功"
    return "写入失败"


def _card_status(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return "not_created"

    quality_gate = result.get("quality_gate") or {}
    composer = result.get("composer") or {}
    card_type = str(result.get("card_type") or result.get("final_card_type") or "")
    final_status = str(result.get("final_status") or "")
    composer_status = str(result.get("composer_status") or composer.get("composer_status") or "")
    quality_gate_passed = bool(result.get("quality_gate_passed") or quality_gate.get("quality_gate_passed"))

    if card_type == "formal_summary" and quality_gate_passed:
        return "formal_card_created"
    if card_type == "temporary_review_card" or final_status == "completed_needs_model_review" or composer_status == "failed":
        return "card_generation_failed"
    if card_type == "extracted_source_card":
        return "source_material_card"
    if card_type == "temporary_card":
        return "temporary_card"
    if card_type == "failure_card":
        return "failure_card"
    return card_type or "unknown"


def _write_status(result: dict[str, Any]) -> str:
    locations = _storage_locations(result)
    if any(item.get("ok") for item in locations):
        return "written"
    write_result = result.get("write_result") or {}
    if write_result.get("skipped") or result.get("siyuan_write_skipped_reason"):
        return "skipped"
    if locations or write_result:
        return "failed"
    return "unknown"


def _clean_inline_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _short_text(value: Any, limit: int = 180) -> str:
    text = _clean_inline_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _text_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean_inline_text(item) for item in value if _clean_inline_text(item)]
    text = _clean_inline_text(value)
    return [text] if text else []


def _first_text(*values: Any) -> str:
    for value in values:
        for item in _text_items(value):
            if item:
                return item
    return ""


def _looks_garbled(value: Any) -> bool:
    text = str(value or "")
    return "\ufffd" in text or "�" in text


def _path_folder(path: Any) -> str:
    text = _clean_inline_text(path).replace("\\", "/")
    if not text or _looks_garbled(text):
        return ""
    text = text.rstrip("/")
    if "/" not in text:
        return ""
    folder = text.rsplit("/", 1)[0].strip()
    return folder or "/"


def _load_composed_card(result: dict[str, Any]) -> dict[str, Any]:
    embedded = result.get("composed_card")
    if isinstance(embedded, dict):
        return embedded
    return _read_job_json(result, "composed_card.json")


def _load_taxonomy_decision(result: dict[str, Any]) -> dict[str, Any]:
    taxonomy_decision = result.get("taxonomy_decision")
    if isinstance(taxonomy_decision, dict) and taxonomy_decision:
        return taxonomy_decision
    write_result = result.get("write_result") or {}
    taxonomy_decision = write_result.get("taxonomy_decision")
    if isinstance(taxonomy_decision, dict) and taxonomy_decision:
        return taxonomy_decision
    return _read_job_json(result, "taxonomy_decision.json")


def _reply_title(result: dict[str, Any], composed_card: dict[str, Any]) -> str:
    write_result = result.get("write_result") or {}
    return _first_text(
        composed_card.get("display_title"),
        result.get("title"),
        write_result.get("title"),
        "未命名",
    )


def _reply_summary(result: dict[str, Any], composed_card: dict[str, Any]) -> str:
    content = result.get("content") or {}
    return _first_text(
        composed_card.get("one_sentence_summary"),
        result.get("one_sentence_summary"),
        composed_card.get("original_summary"),
        content.get("summary"),
        content.get("description"),
        content.get("main_text"),
        "已完成读取，但未返回可展示摘要。",
    )


def _reply_learning_value(result: dict[str, Any], composed_card: dict[str, Any]) -> str:
    blocks = composed_card.get("knowledge_blocks") or []
    block_values: list[str] = []
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict):
                reusable_value = _clean_inline_text(block.get("reusable_value"))
                if reusable_value:
                    block_values.append(reusable_value)
    return _first_text(
        composed_card.get("reusable_value"),
        composed_card.get("application_suggestions"),
        block_values,
        composed_card.get("core_points"),
        (result.get("material_quality") or {}).get("reasons"),
        "先保留来源材料，等补齐模型摘要后再提炼可学习点。",
    )


def _reply_file_tree(result: dict[str, Any]) -> str:
    write_result = result.get("write_result") or {}
    taxonomy_decision = _load_taxonomy_decision(result)
    recommended_path = taxonomy_decision.get("recommended_path")
    if isinstance(recommended_path, list) and recommended_path:
        segments = [_clean_inline_text(item) for item in recommended_path if _clean_inline_text(item)]
        if segments:
            return "/" + "/".join(["知识卡", *segments])

    target_folder = _clean_inline_text(write_result.get("taxonomy_target_folder"))
    if target_folder and not _looks_garbled(target_folder):
        return "/" + target_folder.strip("/")

    write_folder = _path_folder(write_result.get("path"))
    if write_folder:
        return write_folder

    database_result = result.get("lucas_database_write_result")
    if not isinstance(database_result, dict) or not database_result:
        database_result = _read_job_json(result, "write_lucas_database_result.json")
    database_folder = _path_folder(database_result.get("path") if isinstance(database_result, dict) else "")
    if database_folder:
        return database_folder

    return "未返回"


def _storage_locations(result: dict[str, Any]) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []
    write_result = result.get("write_result") if isinstance(result.get("write_result"), dict) else {}
    siyuan_disabled = write_result.get("skipped") and write_result.get("skipped_reason") == "disabled_by_storage_targets"
    configured_targets = result.get("storage_targets")
    siyuan_configured = not isinstance(configured_targets, list) or "siyuan" in configured_targets
    if write_result and not siyuan_disabled and siyuan_configured:
        path = _clean_inline_text(write_result.get("path"))
        locations.append({
            "target": "siyuan",
            "target_id": "siyuan",
            "target_label": "SiYuan",
            "ok": bool(result.get("siyuan_write_ok") or write_result.get("ok")),
            "path": path,
            "layer": _path_folder(path),
            "doc_id": _clean_inline_text(write_result.get("doc_id")),
            "card_id": "",
            "node_id": "",
            "error": _clean_inline_text(write_result.get("error")),
            "stage": _clean_inline_text(write_result.get("stage")),
        })

    database_results = result.get("lucas_database_write_results")
    if isinstance(database_results, list) and database_results:
        for item in database_results:
            if not isinstance(item, dict):
                continue
            nested = item.get("result") if isinstance(item.get("result"), dict) else {}
            target_id = _clean_inline_text(item.get("target_id") or nested.get("target_id") or "lucas_database")
            label = _clean_inline_text(item.get("label") or nested.get("target_label") or "Lucas Database / Brain")
            path = _clean_inline_text(nested.get("path") or item.get("path"))
            locations.append({
                "target": "lucas_database",
                "target_id": target_id,
                "target_label": label,
                "ok": bool(item.get("ok") or nested.get("ok")),
                "path": path,
                "layer": _path_folder(path),
                "doc_id": "",
                "card_id": _clean_inline_text(nested.get("card_id")),
                "node_id": _clean_inline_text(nested.get("node_id")),
                "error": _clean_inline_text(nested.get("error") or item.get("error")),
                "stage": _clean_inline_text(nested.get("stage") or item.get("stage")),
            })
        return locations

    database_result = result.get("lucas_database_write_result")
    if not isinstance(database_result, dict) or not database_result:
        database_result = _read_job_json(result, "write_lucas_database_result.json")
    if isinstance(database_result, dict) and database_result:
        path = _clean_inline_text(database_result.get("path"))
        locations.append({
            "target": "lucas_database",
            "target_id": _clean_inline_text(database_result.get("target_id") or "lucas_database"),
            "target_label": _clean_inline_text(database_result.get("target_label") or "Lucas Database / Brain"),
            "ok": bool(database_result.get("ok")),
            "path": path,
            "layer": _path_folder(path),
            "doc_id": "",
            "card_id": _clean_inline_text(database_result.get("card_id")),
            "node_id": _clean_inline_text(database_result.get("node_id")),
            "error": _clean_inline_text(database_result.get("error")),
            "stage": _clean_inline_text(database_result.get("stage")),
        })
    return locations


def _reply_storage_targets(result: dict[str, Any]) -> str:
    locations = _storage_locations(result)
    written = [item["target_label"] for item in locations if item.get("ok")]
    failed = [
        f"{item['target_label']} 未写入"
        + (f"（{item.get('error') or item.get('stage')}）" if item.get("error") or item.get("stage") else "")
        for item in locations
        if not item.get("ok")
    ]
    parts: list[str] = []
    if written:
        parts.append("、".join(written))
    if failed:
        parts.append("；".join(failed))
    return "；".join(parts) if parts else "未返回写入目标"


def _reply_storage_layers(result: dict[str, Any]) -> str:
    locations = _storage_locations(result)
    layers = [
        f"{item['target_label']}：{item.get('layer') or item.get('path') or '未返回'}"
        for item in locations
        if item.get("ok")
    ]
    return "；".join(layers) if layers else "未返回写入层级"


def _access_status_label(result: dict[str, Any]) -> str:
    storage_ok = any(item.get("ok") for item in _storage_locations(result))
    if not storage_ok:
        return "否"

    card_type = str(result.get("card_type") or result.get("final_card_type") or "")
    quality_gate = result.get("quality_gate") or {}
    quality_gate_passed = bool(result.get("quality_gate_passed") or quality_gate.get("quality_gate_passed"))
    if card_type == "formal_summary" and quality_gate_passed:
        return "是"
    if card_type in {"temporary_card", "temporary_review_card", "extracted_source_card", "failure_card"}:
        return "是（临时卡，待复核）"
    return "是"


def _build_reply_card(result: dict[str, Any]) -> dict[str, Any]:
    composed_card = _load_composed_card(result)
    summary = _reply_summary(result, composed_card)
    learning_value = _reply_learning_value(result, composed_card)
    storage_locations = _storage_locations(result)
    return {
        "title": _short_text(_reply_title(result, composed_card), 80),
        "one_sentence_summary": _short_text(summary, 140),
        "learning_value": _short_text(learning_value, 120),
        "file_tree": _reply_file_tree(result),
        "storage_targets": _short_text(_reply_storage_targets(result), 180),
        "storage_layers": _short_text(_reply_storage_layers(result), 220),
        "storage_locations": storage_locations,
        "access_status": _access_status_label(result),
        "access_ok": _access_status_label(result).startswith("是"),
    }


def _build_reply(result: dict[str, Any], extra_url_count: int = 0) -> str:
    write_result = result.get("write_result") or {}
    ok = bool(result.get("ok") and _access_status_label(result).startswith("是"))
    if ok:
        reply_card = _build_reply_card(result)
        lines = [
            f"摘要：{reply_card['one_sentence_summary']}",
            f"值得学习：{reply_card['learning_value']}",
            "",
            f"接入结果：{reply_card['access_status']}｜标题：{reply_card['title']}",
            f"写入目标：{reply_card['storage_targets']}",
            f"写入层级：{reply_card['storage_layers']}",
            f"文件树：{reply_card['file_tree']}",
        ]
    else:
        write_stage = write_result.get("stage")
        failure_stage = result.get("final_status") or write_stage or result.get("stage") or "unknown"
        reply_card = _build_reply_card(result)
        lines = [
            "这次没有成功接入。",
            f"标题：{reply_card['title']}",
            f"失败阶段：{failure_stage}",
            f"错误：{result.get('error') or write_result.get('error') or '未返回错误详情'}",
        ]

    if extra_url_count:
        lines.append(f"提示：检测到多个链接，本次只处理了第一个，剩余 {extra_url_count} 个未处理。")
    return "\n".join(lines)


def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    write_result = result.get("write_result") or {}
    content = result.get("content") or {}
    comments = result.get("comments") or {}
    quality_gate = result.get("quality_gate") or {}
    composer = result.get("composer") or {}
    return {
        "ok": result.get("ok"),
        "job_id": result.get("job_id"),
        "job_dir": result.get("job_dir"),
        "card_type": result.get("card_type"),
        "card_status": _card_status(result),
        "content_level": result.get("content_level"),
        "source_url": result.get("url"),
        "final_url": content.get("final_url") or comments.get("final_url"),
        "title": result.get("title"),
        "composer_status": result.get("composer_status") or composer.get("composer_status"),
        "quality_gate_passed": result.get("quality_gate_passed") or quality_gate.get("quality_gate_passed"),
        "failed_checks": result.get("failed_checks") or quality_gate.get("failed_checks") or [],
        "final_card_type": result.get("final_card_type") or quality_gate.get("final_card_type"),
        "write_ok": result.get("siyuan_write_ok") or write_result.get("ok"),
        "write_status": _write_status(result),
        "write_path": write_result.get("path"),
        "doc_id": write_result.get("doc_id"),
        "lucas_database_write_ok": result.get("lucas_database_write_ok"),
        "reply_card": _build_reply_card(result),
        "failure_stage": result.get("final_status") or write_result.get("stage"),
        "error": result.get("error") or write_result.get("error"),
        "used_mcp": result.get("used_mcp", False),
    }


def _pending_queue_item(index: int, url: str) -> dict[str, Any]:
    return {
        "index": index,
        "url": url,
        "queue_status": "pending",
        "runner_called": False,
        "runner_exit_code": None,
        "job_id": None,
        "job_dir": None,
        "final_status": "pending",
        "card_status": "not_started",
        "write_status": "not_started",
        "title": None,
        "error": None,
        "result": {},
        "stderr_tail": "",
    }


def _completed_queue_item(
    index: int,
    url: str,
    exit_code: int | None,
    result: dict[str, Any],
    stderr: str,
) -> dict[str, Any]:
    summary = _summarize_result(result)
    queue_status = "completed" if result.get("ok") else "failed"
    return {
        "index": index,
        "url": url,
        "queue_status": queue_status,
        "runner_called": True,
        "runner_exit_code": exit_code,
        "job_id": summary.get("job_id"),
        "job_dir": summary.get("job_dir"),
        "final_status": result.get("final_status") or ("completed" if result.get("ok") else "failed"),
        "card_status": summary.get("card_status"),
        "write_status": summary.get("write_status"),
        "title": summary.get("title"),
        "error": summary.get("error"),
        "result": summary,
        "stderr_tail": stderr[-1000:],
    }


def _build_queue(items: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    total = len(items)
    pending = sum(1 for item in items if item.get("queue_status") == "pending")
    in_progress = sum(1 for item in items if item.get("queue_status") == "in_progress")
    completed = sum(1 for item in items if item.get("queue_status") == "completed")
    failed = sum(1 for item in items if item.get("queue_status") == "failed")
    card_failed = sum(1 for item in items if item.get("card_status") == "card_generation_failed")
    write_failed = sum(1 for item in items if item.get("write_status") == "failed")

    if pending or in_progress:
        status = "pending"
    elif failed:
        status = "failed" if failed == total else "partial_failed"
    elif card_failed:
        status = "completed_with_card_failures"
    elif write_failed:
        status = "completed_with_write_failures"
    else:
        status = "completed"

    return {
        "mode": mode,
        "status": status,
        "total": total,
        "completed": completed,
        "pending": pending,
        "in_progress": in_progress,
        "unfinished": pending + in_progress,
        "failed": failed,
        "card_failed": card_failed,
        "write_failed": write_failed,
        "items": items,
    }


def _queue_item_label(item: dict[str, Any]) -> str:
    if item.get("queue_status") == "pending":
        return "未完成"
    if item.get("queue_status") == "in_progress":
        return "处理中"
    if item.get("queue_status") == "failed":
        return "处理失败"
    if item.get("card_status") == "card_generation_failed":
        return "完成但卡片生成失败"
    if item.get("write_status") == "failed":
        return "完成但写入失败"
    if item.get("card_status") == "source_material_card":
        return "已完成（来源材料卡）"
    if item.get("card_status") == "temporary_card":
        return "已完成（临时卡）"
    return "已完成"


def _build_queue_reply(queue: dict[str, Any], *, dry_run: bool) -> str:
    mode = str(queue.get("mode") or "")
    mode_label = "并行队列" if mode == "parallel" else ("单任务" if mode == "single" else "顺序队列")
    lines = [
        f"已检测到 {queue.get('total', 0)} 个链接，执行模式：{mode_label}。",
    ]
    if dry_run:
        lines.append("Dry-run：未调用 run_link_job.py，未写入任何存储目标。")
    lines.append(
        "队列状态："
        f"已完成 {queue.get('completed', 0)} / "
        f"未完成 {queue.get('unfinished', 0)} / "
        f"处理失败 {queue.get('failed', 0)} / "
        f"卡片生成失败 {queue.get('card_failed', 0)} / "
        f"写入失败 {queue.get('write_failed', 0)}"
    )
    for item in queue.get("items", []):
        title = item.get("title") or item.get("url")
        job_id = item.get("job_id") or "未生成"
        error = item.get("error")
        line = f"{item.get('index')}. {_queue_item_label(item)}｜{title}｜job_id：{job_id}"
        if error:
            line += f"｜错误：{error}"
        lines.append(line)
    return "\n".join(lines)


def _queue_status_to_response_status(queue_status: str) -> str:
    if queue_status == "completed":
        return "batch_completed"
    if queue_status == "completed_with_card_failures":
        return "batch_completed_with_card_failures"
    if queue_status == "completed_with_write_failures":
        return "batch_completed_with_write_failures"
    if queue_status == "partial_failed":
        return "batch_partial_failed"
    if queue_status == "failed":
        return "batch_failed"
    return "batch_pending"


def handle(
    event: MessageEvent,
    links: LinkExtractionResult,
    *,
    dry_run: bool,
    timeout_sec: int,
    runner_env: Mapping[str, str] | None = None,
) -> HandlerResponse:
    url = links.primary_url
    if not url:
        return HandlerResponse(
            ok=False,
            reply_text="未检测到链接。",
            status="extract_url_failed",
            handled_by="link_handler",
            error="No URL found in message.",
        )

    if dry_run:
        source_text = _source_text_from_event(event)
        queue = _build_queue(
            [_pending_queue_item(index, queued_url) for index, queued_url in enumerate(links.urls, start=1)],
            mode="parallel" if len(links.urls) > 1 else "single",
        )
        if len(links.urls) == 1:
            reply = f"已检测到链接：{url}\nDry-run：未调用 run_link_job.py，未写入任何存储目标。"
        else:
            reply = _build_queue_reply(queue, dry_run=True)
        return HandlerResponse(
            ok=True,
            reply_text=reply,
            status="dry_run",
            handled_by="link_handler",
            data={
                "message_id": event.message_id,
                "url": url,
                "urls": links.urls,
                "extra_url_count": links.extra_url_count,
                "runner_called": False,
                "write_skipped": True,
                "queue": queue,
                "source_text_present": bool(source_text),
                "source_text_length": len(source_text),
                "used_mcp": False,
            },
        )

    source_text = _source_text_from_event(event)
    if len(links.urls) > 1:
        items = _run_link_jobs_parallel(links.urls, timeout_sec, runner_env, source_text)
        queue = _build_queue(items, mode="parallel")
        reply = _build_queue_reply(queue, dry_run=False)
        first_result = items[0].get("result") if items else {}
        errors = [str(item.get("error")) for item in items if item.get("error")]
        return HandlerResponse(
            ok=queue["failed"] == 0,
            reply_text=reply,
            job_id=None,
            status=_queue_status_to_response_status(str(queue.get("status") or "")),
            handled_by="link_handler",
            error=errors[0] if errors else None,
            data={
                "message_id": event.message_id,
                "url": url,
                "urls": links.urls,
                "extra_url_count": links.extra_url_count,
                "queue": queue,
                "job_ids": [item.get("job_id") for item in items if item.get("job_id")],
                "results": [item.get("result") for item in items],
                "runner_called": True,
                "result": first_result,
                "source_text_present": bool(source_text),
                "source_text_length": len(source_text),
                "used_mcp": False,
            },
        )

    exit_code, result, stderr = _run_link_job(url, timeout_sec, runner_env, source_text)
    reply = _build_reply(result, extra_url_count=links.extra_url_count)
    summary = _summarize_result(result)
    queue = _build_queue([_completed_queue_item(1, url, exit_code, result, stderr)], mode="single")
    status = str(result.get("final_status") or ("completed" if result.get("ok") else "failed"))
    return HandlerResponse(
        ok=bool(result.get("ok")),
        reply_text=reply,
        job_id=result.get("job_id"),
        status=status,
        handled_by="link_handler",
        error=summary.get("error"),
        data={
            "message_id": event.message_id,
            "url": url,
            "urls": links.urls,
            "extra_url_count": links.extra_url_count,
            "runner_exit_code": exit_code,
            "result": summary,
            "queue": queue,
            "stderr_tail": stderr[-1000:],
            "source_text_present": bool(source_text),
            "source_text_length": len(source_text),
            "used_mcp": False,
        },
    )
