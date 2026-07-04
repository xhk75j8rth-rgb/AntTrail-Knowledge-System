#!/usr/bin/env python
"""
V0 SiYuan HTTP writer.

This script intentionally does not call MCP. It reads the SiYuan token from an
environment variable, writes a Markdown document through SiYuan HTTP API, and
always saves a structured write_result.json in the job directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "link_pipeline.json"
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "link_pipeline.example.json"


def load_config(path: Path) -> tuple[dict[str, Any], Path, str | None]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), path, None
    if path == DEFAULT_CONFIG and EXAMPLE_CONFIG.exists():
        return json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8")), EXAMPLE_CONFIG, (
            "config/link_pipeline.json not found; using example config. "
            "Set default_notebook before production use."
        )
    raise FileNotFoundError(f"Config not found: {path}")


def token_last4(token: str | None) -> str | None:
    if not token:
        return None
    return token[-4:]


def write_result(job_dir: Path, result: dict[str, Any]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "write_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def config_bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def api_post(base_url: str, token: str, endpoint: str, payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    url = base_url.rstrip("/") + endpoint
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Token {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    return json.loads(raw)


def normalize_doc_path(folder: str, title: str) -> str:
    safe_title = "".join(ch if ch not in '<>:"\\|?*' else "_" for ch in title).strip()
    safe_title = safe_title or "Untitled"
    folder = (folder or "").strip().strip("/")
    if folder:
        return f"/{folder}/{safe_title}.md"
    return f"/{safe_title}.md"


def safe_folder_segment(value: Any) -> str:
    text = clean_text(value)
    text = "".join(ch if ch not in '<>:"\\|?*#@\r\n\t' else "_" for ch in text)
    return text.strip(" ./_")


def normalize_folder_path(parts: list[Any]) -> str:
    return "/".join(segment for segment in (safe_folder_segment(part) for part in parts) if segment)


def job_has_formal_taxonomy_target(job_dir: Path) -> bool:
    quality_gate = read_json(job_dir / "quality_gate.json")
    if quality_gate and not bool(quality_gate.get("quality_gate_passed") or quality_gate.get("passed")):
        return False
    card = read_json(job_dir / "composed_card.json")
    card_type = clean_text(card.get("card_type"))
    if card_type and card_type != "formal_summary":
        return False
    return True


def taxonomy_folder_from_decision(config: dict[str, Any], job_dir: Path) -> tuple[str, dict[str, Any]]:
    if not config_bool(config, "siyuan_use_taxonomy_path", True):
        return "", {}
    if not job_has_formal_taxonomy_target(job_dir):
        return "", {}
    decision = read_json(job_dir / "taxonomy_decision.json")
    if decision.get("schema_name") != "TaxonomyDecisionV1":
        return "", {}
    path = decision.get("recommended_path")
    if not isinstance(path, list) or not path:
        return "", decision
    if "siyuan_taxonomy_root_path" in config:
        root = clean_text(config.get("siyuan_taxonomy_root_path"))
    elif "knowledge_card_root_path" in config:
        root = clean_text(config.get("knowledge_card_root_path"))
    else:
        root = "知识卡"
    folder = normalize_folder_path(([root] if root else []) + path)
    return folder, decision


def resolve_target_folder(config: dict[str, Any], job_dir: Path) -> tuple[str, dict[str, Any]]:
    taxonomy_folder, decision = taxonomy_folder_from_decision(config, job_dir)
    if taxonomy_folder:
        return taxonomy_folder, decision
    return str(config.get("inbox_path") or config.get("fallback_path") or "00_Inbox").strip(), decision


def looks_like_duplicate_path_response(response: dict[str, Any]) -> bool:
    text = f"{response.get('msg') or ''} {response.get('error') or ''}".casefold()
    return any(marker in text for marker in ("exist", "already", "duplicate", "已存在", "重复"))


def extract_notebook_id(notebook: dict[str, Any]) -> str | None:
    for key in ("id", "notebook", "box"):
        value = notebook.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def notebook_matches(notebook: dict[str, Any], configured: str) -> bool:
    configured_lower = configured.casefold()
    for key in ("id", "name", "notebook", "box"):
        value = notebook.get(key)
        if isinstance(value, str) and value.casefold() == configured_lower:
            return True
    return False


def choose_notebook(config: dict[str, Any], notebooks: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    configured = str(config.get("default_notebook") or "").strip()
    if configured and configured != "__CONFIGURE_ME__":
        for notebook in notebooks:
            if notebook_matches(notebook, configured):
                return extract_notebook_id(notebook), None
        return None, f"Configured notebook was not found: {configured}"

    if notebooks:
        notebook_id = extract_notebook_id(notebooks[0])
        return notebook_id, "default_notebook is not configured; selected the first notebook returned by SiYuan."
    return None, "No notebook returned by SiYuan."


def build_base_result(args: argparse.Namespace, config: dict[str, Any], config_path: Path, warning: str | None, token: str | None) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": "initializing",
        "notebook": None,
        "path": None,
        "title": args.title,
        "doc_id": None,
        "error": None,
        "token_present": bool(token),
        "token_last4": token_last4(token),
        "used_mcp": False,
        "config_path": str(config_path),
        "config_warning": warning,
        "siyuan_base_url": config.get("siyuan_base_url"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Write Markdown to SiYuan through HTTP API without MCP.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config JSON.")
    parser.add_argument("--title", required=True, help="Document title.")
    parser.add_argument("--markdown", required=True, help="Markdown file path.")
    parser.add_argument("--job-dir", required=True, help="Job directory for write_result.json.")
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    markdown_path = Path(args.markdown).resolve()

    try:
        config, config_path, warning = load_config(Path(args.config).resolve())
    except Exception as exc:
        result = {
            "ok": False,
            "stage": "config",
            "notebook": None,
            "path": None,
            "title": args.title,
            "doc_id": None,
            "error": str(exc),
            "token_present": False,
            "token_last4": None,
            "used_mcp": False,
        }
        write_result(job_dir, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    token_env_name = str(config.get("siyuan_token_env_name") or "SIYUAN_TOKEN")
    token = os.environ.get(token_env_name)
    result = build_base_result(args, config, config_path, warning, token)

    if not markdown_path.exists():
        result.update({"stage": "markdown", "error": f"Markdown file not found: {markdown_path}"})
        write_result(job_dir, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    markdown = markdown_path.read_text(encoding="utf-8")

    if not token:
        result.update({"stage": "auth", "error": f"Missing SiYuan token environment variable: {token_env_name}"})
        write_result(job_dir, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    base_url = str(config.get("siyuan_base_url") or "http://127.0.0.1:6806").rstrip("/")

    try:
        result["stage"] = "list_notebooks"
        notebook_response = api_post(base_url, token, "/api/notebook/lsNotebooks", {})
        notebooks = notebook_response.get("data", {}).get("notebooks", [])
        if not isinstance(notebooks, list):
            notebooks = []

        notebook_id, notebook_warning = choose_notebook(config, notebooks)
        result["notebook"] = notebook_id
        if notebook_warning:
            result["notebook_warning"] = notebook_warning

        if not notebook_id:
            result.update({"stage": "select_notebook", "error": notebook_warning or "Unable to select notebook."})
            write_result(job_dir, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

        target_folder, taxonomy_decision = resolve_target_folder(config, job_dir)
        doc_path = normalize_doc_path(target_folder, args.title)
        result["path"] = doc_path
        if taxonomy_decision:
            result["taxonomy_decision"] = taxonomy_decision
            result["taxonomy_target_folder"] = target_folder

        result["stage"] = "create_doc"
        create_response = api_post(
            base_url,
            token,
            "/api/filetree/createDocWithMd",
            {
                "notebook": notebook_id,
                "path": doc_path,
                "markdown": markdown,
            },
        )
        code = create_response.get("code")
        if code not in (0, None):
            if str(config.get("siyuan_duplicate_policy") or "skip_exact_path").strip().casefold() == "skip_exact_path" and looks_like_duplicate_path_response(create_response):
                result.update({
                    "ok": True,
                    "stage": "duplicate_skipped",
                    "doc_id": None,
                    "duplicate": {
                        "policy": "skip_exact_path",
                        "matched_by": "target_path",
                        "path": doc_path,
                    },
                    "response": create_response,
                })
                write_result(job_dir, result)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            result.update({"error": create_response.get("msg") or f"SiYuan returned code {code}", "response": create_response})
            write_result(job_dir, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

        data = create_response.get("data")
        doc_id = data if isinstance(data, str) else None
        if isinstance(data, dict):
            doc_id = data.get("id") or data.get("doc_id") or data.get("path")

        result.update({
            "ok": True,
            "stage": "completed",
            "doc_id": doc_id,
            "response": create_response,
        })
        write_result(job_dir, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except urllib.error.URLError as exc:
        result.update({"ok": False, "error": f"SiYuan HTTP API unavailable: {exc.reason}"})
    except json.JSONDecodeError as exc:
        result.update({"ok": False, "error": f"Invalid JSON response from SiYuan: {exc}"})
    except Exception as exc:
        result.update({"ok": False, "error": str(exc)})

    write_result(job_dir, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
