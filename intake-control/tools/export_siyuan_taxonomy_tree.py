#!/usr/bin/env python
"""
Export the current SiYuan knowledge-card folder tree as TaxonomyTreeV1.

The script only reads through the SiYuan HTTP API. It does not inspect or
modify the SiYuan workspace files directly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "runtime" / "taxonomy_tree.json"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from taxonomy_layer.taxonomy_store import tree_from_paths  # noqa: E402
from tools.write_siyuan import api_post, choose_notebook, load_config  # noqa: E402


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_doc_name(value: Any) -> str:
    text = str(value or "").strip().strip("/")
    for suffix in (".sy", ".md"):
        if text.casefold().endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip()


def doc_display_name(item: dict[str, Any]) -> str:
    for key in ("name", "title", "hName"):
        value = clean_doc_name(item.get(key))
        if value:
            return value
    path = clean_doc_name(item.get("path") or item.get("hPath"))
    return Path(path).stem if path else ""


def doc_path(item: dict[str, Any], parent_path: str, name: str) -> str:
    human_path = str(item.get("hPath") or "").strip()
    if human_path:
        return "/" + human_path.strip("/")
    raw = str(item.get("path") or "").strip()
    if raw:
        raw = raw.strip("/")
        if raw.casefold().endswith(".sy"):
            return "/" + raw
        if raw.casefold().endswith(".md"):
            return "/" + raw
        return "/" + raw
    return "/" + "/".join(part for part in (parent_path.strip("/"), name) if part)


def child_count(item: dict[str, Any]) -> int:
    for key in ("subFileCount", "sub_file_count", "count"):
        try:
            return int(item.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return 0


def extract_files(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("files", "docs", "items"):
            items = data.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def list_docs(base_url: str, token: str, notebook: str, path: str, max_list_count: int) -> list[dict[str, Any]]:
    response = api_post(
        base_url,
        token,
        "/api/filetree/listDocsByPath",
        {
            "notebook": notebook,
            "path": "/" + path.strip("/"),
            "maxListCount": max_list_count,
            "sort": 0,
        },
    )
    code = response.get("code")
    if code not in (0, None):
        raise RuntimeError(response.get("msg") or f"SiYuan returned code {code}")
    return extract_files(response)


def collect_paths(
    *,
    base_url: str,
    token: str,
    notebook: str,
    root_path: str,
    max_depth: int,
    max_list_count: int,
    include_leaf_docs: bool,
) -> list[list[str]]:
    paths: list[list[str]] = []

    def walk(parent_path: str, relative_parts: list[str], depth: int) -> None:
        if depth > max_depth:
            return
        for item in list_docs(base_url, token, notebook, parent_path, max_list_count):
            name = doc_display_name(item)
            if not name:
                continue
            sub_count = child_count(item)
            next_parts = [*relative_parts, name]
            if sub_count > 0 or include_leaf_docs:
                paths.append(next_parts)
            if sub_count > 0 and depth < max_depth:
                walk(doc_path(item, parent_path, name), next_parts, depth + 1)

    walk(root_path, [], 1)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Export SiYuan knowledge-card folder tree as TaxonomyTreeV1.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "link_pipeline.json"))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--root-path", default="", help="SiYuan doc path that contains knowledge-card categories.")
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--max-list-count", type=int, default=512)
    parser.add_argument("--include-leaf-docs", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config, config_path, warning = load_config(Path(args.config).resolve())
    token_env_name = str(config.get("siyuan_token_env_name") or "SIYUAN_TOKEN")
    token = os.environ.get(token_env_name)
    if not token:
        print(json.dumps({
            "ok": False,
            "stage": "auth",
            "error": f"Missing SiYuan token environment variable: {token_env_name}",
            "config_path": str(config_path),
            "config_warning": warning,
        }, ensure_ascii=False, indent=2))
        return 1

    base_url = str(config.get("siyuan_base_url") or "http://127.0.0.1:6806").rstrip("/")
    notebooks_response = api_post(base_url, token, "/api/notebook/lsNotebooks", {})
    notebooks = notebooks_response.get("data", {}).get("notebooks", [])
    notebooks = notebooks if isinstance(notebooks, list) else []
    notebook_id, notebook_warning = choose_notebook(config, notebooks)
    if not notebook_id:
        print(json.dumps({
            "ok": False,
            "stage": "select_notebook",
            "error": notebook_warning or "Unable to select notebook.",
            "config_path": str(config_path),
            "config_warning": warning,
        }, ensure_ascii=False, indent=2))
        return 1

    if args.root_path:
        root_path = str(args.root_path)
    elif "taxonomy_export_root_path" in config:
        root_path = str(config.get("taxonomy_export_root_path") or "/")
    elif "siyuan_taxonomy_root_path" in config:
        root_path = str(config.get("siyuan_taxonomy_root_path") or "/")
    elif "knowledge_card_root_path" in config:
        root_path = str(config.get("knowledge_card_root_path") or "/")
    else:
        root_path = "知识卡"
    paths = collect_paths(
        base_url=base_url,
        token=token,
        notebook=notebook_id,
        root_path=root_path,
        max_depth=max(1, args.max_depth),
        max_list_count=max(1, args.max_list_count),
        include_leaf_docs=bool(args.include_leaf_docs),
    )
    tree = tree_from_paths(paths)
    tree.update({
        "source": "siyuan_http_api",
        "root_path": root_path,
        "category_count": len(paths),
    })
    result = {
        "ok": True,
        "stage": "dry_run" if args.dry_run else "completed",
        "output": str(Path(args.output).resolve()),
        "root_path": root_path,
        "category_count": len(paths),
        "notebook": notebook_id,
        "config_path": str(config_path),
        "config_warning": warning,
        "notebook_warning": notebook_warning,
        "tree": tree if args.dry_run else None,
    }
    if not args.dry_run:
        write_json(Path(args.output).resolve(), tree)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
