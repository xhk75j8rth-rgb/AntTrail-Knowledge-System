from __future__ import annotations

import copy
import json
from typing import Any
from pathlib import Path

from taxonomy_layer.taxonomy_schema import TAXONOMY_TREE_SCHEMA_NAME, TaxonomyPath


SEED_TAXONOMY_TREE: dict[str, Any] = {
    "schema_name": TAXONOMY_TREE_SCHEMA_NAME,
    "categories": [
        {
            "path": ["AI"],
            "children": [
                {
                    "path": ["AI", "Agent"],
                    "children": [
                        {"path": ["AI", "Agent", "记忆系统"]},
                        {"path": ["AI", "Agent", "上下文管理"]},
                        {"path": ["AI", "Agent", "工具调用"]},
                        {"path": ["AI", "Agent", "多 Agent 协作"]},
                        {"path": ["AI", "Agent", "AgentOS 架构"]},
                    ],
                },
                {
                    "path": ["AI", "知识库"],
                    "children": [
                        {"path": ["AI", "知识库", "结构化卡片"]},
                        {"path": ["AI", "知识库", "知识图谱"]},
                        {"path": ["AI", "知识库", "向量检索"]},
                        {"path": ["AI", "知识库", "数据库适配"]},
                    ],
                },
                {
                    "path": ["AI", "内容生产"],
                    "children": [
                        {"path": ["AI", "内容生产", "AI 写作"]},
                        {"path": ["AI", "内容生产", "短视频工作流"]},
                        {"path": ["AI", "内容生产", "图像 / 视频生成"]},
                    ],
                },
                {
                    "path": ["AI", "模型与 Provider"],
                    "children": [
                        {"path": ["AI", "模型与 Provider", "DeepSeek"]},
                        {"path": ["AI", "模型与 Provider", "OpenAI"]},
                        {"path": ["AI", "模型与 Provider", "Claude"]},
                        {"path": ["AI", "模型与 Provider", "多模型路由"]},
                    ],
                },
                {
                    "path": ["AI", "工程化"],
                    "children": [
                        {"path": ["AI", "工程化", "任务队列"]},
                        {"path": ["AI", "工程化", "质量门禁"]},
                        {"path": ["AI", "工程化", "存储层"]},
                        {"path": ["AI", "工程化", "自动化流水线"]},
                    ],
                },
            ],
        },
        {
            "path": ["项目"],
            "children": [
                {"path": ["项目", "Lucas-Knowledge-DB-Lab"]},
                {"path": ["项目", "Lucas-AgentOS"]},
                {"path": ["项目", "Lucas-SiYuan-Codex"]},
                {"path": ["项目", "AIGC 视频工作台"]},
            ],
        },
        {
            "path": ["软件工程"],
            "children": [
                {
                    "path": ["软件工程", "前端开发"],
                    "children": [
                        {"path": ["软件工程", "前端开发", "动画与交互"]},
                        {"path": ["软件工程", "前端开发", "AI 辅助前端开发"]},
                        {"path": ["软件工程", "前端开发", "UI 工程"]},
                        {"path": ["软件工程", "前端开发", "工具链"]},
                    ],
                },
            ],
        },
        {
            "path": ["Inbox", "待分类"],
        },
    ],
}


TAXONOMY_ROOT_SEGMENTS = {"知识卡", "知识卡片", "Knowledge Cards", "knowledge cards"}


def _clean_path_parts(path: Any) -> list[str]:
    if isinstance(path, str):
        parts = [part for part in path.strip().strip("/").split("/") if part]
    elif isinstance(path, (list, tuple)):
        parts = [str(part) for part in path]
    else:
        parts = []
    cleaned = [part.strip() for part in parts if str(part).strip()]
    while cleaned and cleaned[0] in TAXONOMY_ROOT_SEGMENTS:
        cleaned = cleaned[1:]
    return cleaned


def _node_paths(nodes: Any) -> list[list[str]]:
    paths: list[list[str]] = []
    if not isinstance(nodes, list):
        return paths
    for node in nodes:
        if isinstance(node, dict):
            path = _clean_path_parts(node.get("path") or node.get("segments") or node.get("name") or [])
            if path:
                paths.append(path)
            paths.extend(_node_paths(node.get("children") or node.get("categories") or []))
            continue
        path = _clean_path_parts(node)
        if path:
            paths.append(path)
    return paths


def tree_paths(tree: dict[str, Any]) -> list[list[str]]:
    if not isinstance(tree, dict):
        return []
    raw_paths = tree.get("paths")
    paths = _node_paths(raw_paths) if isinstance(raw_paths, list) else []
    paths.extend(_node_paths(tree.get("categories") or []))
    return [path for path in paths if path]


def tree_from_paths(paths: list[list[str]]) -> dict[str, Any]:
    root: list[dict[str, Any]] = []

    def find_child(nodes: list[dict[str, Any]], path: list[str]) -> dict[str, Any] | None:
        for node in nodes:
            if node.get("path") == path:
                return node
        return None

    for path in paths:
        cleaned = _clean_path_parts(path)
        if not cleaned:
            continue
        nodes = root
        prefix: list[str] = []
        for segment in cleaned:
            prefix.append(segment)
            child = find_child(nodes, prefix)
            if child is None:
                child = {"path": list(prefix)}
                nodes.append(child)
            nodes = child.setdefault("children", [])

    def prune_empty_children(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            children = node.get("children")
            if isinstance(children, list) and children:
                prune_empty_children(children)
            elif "children" in node:
                node.pop("children", None)

    prune_empty_children(root)
    if ["Inbox", "待分类"] not in [_clean_path_parts(path) for path in paths]:
        root.append({"path": ["Inbox", "待分类"]})
    return {"schema_name": TAXONOMY_TREE_SCHEMA_NAME, "categories": root}


def normalize_taxonomy_tree(tree: dict[str, Any]) -> dict[str, Any]:
    paths = tree_paths(tree)
    if not paths:
        return copy.deepcopy(SEED_TAXONOMY_TREE)
    return tree_from_paths(paths)


def merge_taxonomy_trees(*trees: dict[str, Any]) -> dict[str, Any]:
    paths: list[list[str]] = []
    for tree in trees:
        paths.extend(tree_paths(tree))
    return tree_from_paths(paths)


class InMemoryTaxonomyStore:
    """Read-only V1 taxonomy store backed by an in-process seed tree."""

    def __init__(self, tree: dict[str, Any] | None = None, *, source: str = "seed", source_path: str = "") -> None:
        self._tree = normalize_taxonomy_tree(copy.deepcopy(tree)) if tree else copy.deepcopy(SEED_TAXONOMY_TREE)
        self.source = source
        self.source_path = source_path

    @classmethod
    def from_file(cls, path: str | Path, *, merge_seed: bool = False) -> "InMemoryTaxonomyStore":
        tree_path = Path(path).expanduser().resolve()
        data = json.loads(tree_path.read_text(encoding="utf-8"))
        tree = merge_taxonomy_trees(SEED_TAXONOMY_TREE, data) if merge_seed else normalize_taxonomy_tree(data)
        return cls(tree, source="file", source_path=str(tree_path))

    def tree(self) -> dict[str, Any]:
        return copy.deepcopy(self._tree)

    def list_categories(self, *, include_parents: bool = True, include_inbox: bool = True) -> list[TaxonomyPath]:
        categories: list[TaxonomyPath] = []
        self._walk(self._tree.get("categories") or [], categories, include_parents=include_parents, include_inbox=include_inbox)
        return self._dedupe(categories)

    def list_leaf_categories(self, *, include_inbox: bool = True) -> list[TaxonomyPath]:
        categories: list[TaxonomyPath] = []
        self._walk_leaves(self._tree.get("categories") or [], categories, include_inbox=include_inbox)
        return self._dedupe(categories)

    def inbox_path(self) -> TaxonomyPath:
        return TaxonomyPath(["Inbox", "待分类"])

    def get_by_path_id(self, path_id: str) -> TaxonomyPath | None:
        for category in self.list_categories(include_parents=True, include_inbox=True):
            if category.path_id == path_id:
                return category
        return None

    def _walk(
        self,
        nodes: list[dict[str, Any]],
        categories: list[TaxonomyPath],
        *,
        include_parents: bool,
        include_inbox: bool,
    ) -> None:
        for node in nodes:
            path = TaxonomyPath(list(node.get("path") or []))
            children = list(node.get("children") or [])
            if include_parents or not children:
                if include_inbox or path.path_id != self.inbox_path().path_id:
                    categories.append(path)
            if children:
                self._walk(children, categories, include_parents=include_parents, include_inbox=include_inbox)

    def _walk_leaves(self, nodes: list[dict[str, Any]], categories: list[TaxonomyPath], *, include_inbox: bool) -> None:
        for node in nodes:
            path = TaxonomyPath(list(node.get("path") or []))
            children = list(node.get("children") or [])
            if children:
                self._walk_leaves(children, categories, include_inbox=include_inbox)
                continue
            if include_inbox or path.path_id != self.inbox_path().path_id:
                categories.append(path)

    @staticmethod
    def _dedupe(categories: list[TaxonomyPath]) -> list[TaxonomyPath]:
        seen: set[str] = set()
        result: list[TaxonomyPath] = []
        for category in categories:
            if category.path_id in seen:
                continue
            seen.add(category.path_id)
            result.append(category)
        return result
