from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from taxonomy_layer.category_policy import CategoryPolicy
from taxonomy_layer.similarity_matcher import SimilarityMatcher, build_card_material
from taxonomy_layer.taxonomy_schema import TaxonomyDecisionV1
from taxonomy_layer.taxonomy_store import InMemoryTaxonomyStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAXONOMY_TREE_PATH = PROJECT_ROOT / "runtime" / "taxonomy_tree.json"
TAXONOMY_TREE_PATH_ENV = "LUCAS_TAXONOMY_TREE_PATH"


def _config_bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _resolve_tree_path(config: dict[str, Any]) -> Path | None:
    configured = str(config.get("taxonomy_tree_path") or "").strip()
    env_value = str(os.environ.get(TAXONOMY_TREE_PATH_ENV) or "").strip()
    raw_path = configured or env_value
    if raw_path:
        path = Path(raw_path).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path
    return DEFAULT_TAXONOMY_TREE_PATH if DEFAULT_TAXONOMY_TREE_PATH.exists() else None


class TaxonomyRouter:
    """Route a ComposedCardV1-shaped dict to a stable taxonomy decision."""

    def __init__(
        self,
        *,
        store: InMemoryTaxonomyStore | None = None,
        matcher: SimilarityMatcher | None = None,
        policy: CategoryPolicy | None = None,
        auto_create_from_proposal: bool = False,
    ) -> None:
        self.store = store or InMemoryTaxonomyStore()
        self.matcher = matcher or SimilarityMatcher()
        self.policy = policy or CategoryPolicy()
        self.auto_create_from_proposal = auto_create_from_proposal

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "TaxonomyRouter":
        config = config or {}
        path = _resolve_tree_path(config)
        merge_seed = _config_bool(config, "taxonomy_tree_merge_seed", False)
        store = InMemoryTaxonomyStore()
        if path and path.exists():
            store = InMemoryTaxonomyStore.from_file(path, merge_seed=merge_seed)
        return cls(
            store=store,
            auto_create_from_proposal=_config_bool(config, "taxonomy_auto_create_from_proposal", False),
        )

    def route_card(self, card: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(card, "to_dict"):
            card = card.to_dict()
        if not isinstance(card, dict):
            raise TypeError("TaxonomyRouter.route_card expects a ComposedCardV1-like dict")

        material = build_card_material(card)
        categories = self.store.list_leaf_categories(include_inbox=False)
        matches = self.matcher.match(material, categories)
        matches = [match for match in matches if self.policy.allows_category_match(material, match)]
        top_match = matches[0] if matches else None
        similar_categories = self.policy.filter_similar(matches)
        card_id = card.get("card_id") or card.get("id")

        if top_match and self.policy.accepts_existing_category(top_match, material):
            confidence = self.policy.confidence_for_score(top_match.score)
            if confidence == "low":
                confidence = "medium"
            decision = TaxonomyDecisionV1(
                card_id=str(card_id) if card_id else None,
                recommended_path=top_match.path,
                path_id=top_match.path_id,
                confidence=confidence,
                matched_existing_category=True,
                should_create_category=False,
                similar_categories=similar_categories,
                reason=(
                    f"内容与已有分类 {top_match.display_name} 匹配；"
                    f"{top_match.reason}。V1 只推荐既有分类，不自动创建目录。"
                ),
                fallback=None,
                create_proposal=None,
            )
            payload = decision.to_dict()
            payload["taxonomy_source"] = self.store.source
            payload["taxonomy_source_path"] = self.store.source_path
            return payload

        inbox = self.policy.inbox_path()
        should_propose = self.policy.should_propose_category(material, similar_categories)
        proposal = self.policy.build_create_proposal(material) if should_propose else None
        if proposal and self.auto_create_from_proposal and self.policy.allows_auto_create_from_proposal(material, proposal):
            decision = TaxonomyDecisionV1(
                card_id=str(card_id) if card_id else None,
                recommended_path=proposal.proposed_path,
                path_id=proposal.path_id,
                confidence="medium",
                matched_existing_category=False,
                should_create_category=True,
                similar_categories=similar_categories,
                reason=(
                    "没有既有分类达到推荐阈值；已按配置启用自动新建分类建议，"
                    f"使用 {proposal.display_name} 作为目标路径。"
                ),
                fallback=None,
                create_proposal=proposal.to_dict(),
            )
            payload = decision.to_dict()
            payload["taxonomy_source"] = self.store.source
            payload["taxonomy_source_path"] = self.store.source_path
            return payload

        decision = TaxonomyDecisionV1(
            card_id=str(card_id) if card_id else None,
            recommended_path=inbox.path,
            path_id=inbox.path_id,
            confidence="low",
            matched_existing_category=False,
            should_create_category=bool(proposal),
            similar_categories=similar_categories,
            reason="没有既有分类达到推荐阈值，按保守策略进入 Inbox / 待分类。",
            fallback="Inbox / 待分类",
            create_proposal=proposal.to_dict() if proposal else None,
        )
        payload = decision.to_dict()
        payload["taxonomy_source"] = self.store.source
        payload["taxonomy_source_path"] = self.store.source_path
        return payload
