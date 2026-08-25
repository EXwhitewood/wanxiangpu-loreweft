"""方法论适配器服务。

将外部写作方法论转换为万象谱可控的结构化策略卡。
生成/FBI/质量层按需检索策略卡，不直接注入长篇原文。
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import settings as app_settings
from app.utils.atomic_file import atomic_write_text
from app.models.methodology import (
    MethodologyCard,
    MethodologyCardQuery,
    MethodologyGuidanceResult,
    MethodologyRule,
    MethodologySource,
    MethodologySourceResponse,
)

logger = logging.getLogger(__name__)

_STORAGE_DIR = Path(app_settings.data_dir) / "methodology"


class MethodologyAdapter:
    """方法论适配器"""

    def __init__(self, storage_dir: str | Path | None = None):
        self._storage_dir = Path(storage_dir) if storage_dir is not None else _STORAGE_DIR
        self._sources: dict[str, MethodologySource] = {}
        self._cards: dict[str, MethodologyCard] = {}
        self._rules: dict[str, MethodologyRule] = {}
        self._load_from_disk()

    def _load_from_disk(self):
        """从磁盘加载已保存的方法论数据"""
        sources_file = self._storage_dir / "sources.json"
        cards_file = self._storage_dir / "cards.json"
        rules_file = self._storage_dir / "rules.json"

        if sources_file.exists():
            try:
                data = json.loads(sources_file.read_text(encoding="utf-8"))
                for item in data:
                    src = MethodologySource(**item)
                    self._sources[src.id] = src
            except Exception as e:
                logger.warning(f"加载方法论来源失败: {e}")

        if cards_file.exists():
            try:
                data = json.loads(cards_file.read_text(encoding="utf-8"))
                for item in data:
                    card = MethodologyCard(**item)
                    self._cards[card.id] = card
            except Exception as e:
                logger.warning(f"加载策略卡失败: {e}")

        if rules_file.exists():
            try:
                data = json.loads(rules_file.read_text(encoding="utf-8"))
                for item in data:
                    rule = MethodologyRule(**item)
                    self._rules[rule.id] = rule
            except Exception as e:
                logger.warning(f"加载规则失败: {e}")

    def _save_to_disk(self):
        """保存方法论数据到磁盘"""
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        sources_file = self._storage_dir / "sources.json"
        cards_file = self._storage_dir / "cards.json"
        rules_file = self._storage_dir / "rules.json"

        atomic_write_text(
            sources_file,
            json.dumps([s.model_dump() for s in self._sources.values()], ensure_ascii=False, indent=2),
        )
        atomic_write_text(
            cards_file,
            json.dumps([c.model_dump() for c in self._cards.values()], ensure_ascii=False, indent=2),
        )
        atomic_write_text(
            rules_file,
            json.dumps([r.model_dump() for r in self._rules.values()], ensure_ascii=False, indent=2),
        )

    # ---- 来源管理 ----

    def register_source(self, source: MethodologySource) -> MethodologySource:
        """注册方法论来源"""
        if not source.id:
            source.id = str(uuid.uuid4())[:8]
        if not source.imported_at:
            source.imported_at = datetime.now().isoformat()
        self._sources[source.id] = source
        self._save_to_disk()
        logger.info(f"注册方法论来源: {source.name} (ID: {source.id})")
        return source

    def list_sources(self) -> list[MethodologySourceResponse]:
        """列出所有来源"""
        results = []
        for src in self._sources.values():
            card_count = sum(1 for c in self._cards.values() if c.source_id == src.id)
            rule_count = sum(1 for r in self._rules.values() if r.card_id in [c.id for c in self._cards.values() if c.source_id == src.id])
            results.append(MethodologySourceResponse(source=src, card_count=card_count, rule_count=rule_count))
        return results

    def get_source(self, source_id: str) -> Optional[MethodologySource]:
        return self._sources.get(source_id)

    def toggle_source(self, source_id: str, enabled: bool) -> Optional[MethodologySource]:
        """启用/禁用来源"""
        src = self._sources.get(source_id)
        if src:
            src.enabled = enabled
            self._save_to_disk()
        return src

    def delete_source(self, source_id: str) -> bool:
        """删除来源及其关联的策略卡和规则"""
        if source_id not in self._sources:
            return False
        # 删除关联的规则和策略卡
        card_ids_to_remove = [c.id for c in self._cards.values() if c.source_id == source_id]
        for cid in card_ids_to_remove:
            rule_ids_to_remove = [r.id for r in self._rules.values() if r.card_id == cid]
            for rid in rule_ids_to_remove:
                self._rules.pop(rid, None)
            self._cards.pop(cid, None)
        del self._sources[source_id]
        self._save_to_disk()
        return True

    # ---- 策略卡管理 ----

    def add_card(self, card: MethodologyCard) -> MethodologyCard:
        """添加策略卡"""
        if not card.id:
            card.id = str(uuid.uuid4())[:8]
        self._cards[card.id] = card
        self._save_to_disk()
        return card

    def query_cards(self, query: MethodologyCardQuery) -> list[MethodologyCard]:
        """查询策略卡"""
        results = []
        for card in self._cards.values():
            # 检查来源是否启用
            if query.enabled_only:
                src = self._sources.get(card.source_id)
                if src and not src.enabled:
                    continue
            if query.category and card.category != query.category:
                continue
            if query.source_id and card.source_id != query.source_id:
                continue
            results.append(card)
        return results[query.offset:query.offset + query.limit]

    # ---- 规则管理 ----

    def add_rule(self, rule: MethodologyRule) -> MethodologyRule:
        """添加规则"""
        if not rule.id:
            rule.id = str(uuid.uuid4())[:8]
        self._rules[rule.id] = rule
        self._save_to_disk()
        return rule

    def get_rules_for_card(self, card_id: str) -> list[MethodologyRule]:
        """获取策略卡的规则"""
        return [r for r in self._rules.values() if r.card_id == card_id]

    # ---- 生成时检索 ----

    def build_guidance(
        self,
        categories: list[str] | None = None,
        max_token_budget: int = 2000,
    ) -> MethodologyGuidanceResult:
        """构建生成时指导。

        只返回短指导，不直接注入长篇原文。
        """
        cards = []
        rules = []
        total_tokens = 0

        for card in self._cards.values():
            # 检查来源是否启用
            src = self._sources.get(card.source_id)
            if not src or not src.enabled:
                continue
            if categories and card.category not in categories:
                continue

            if total_tokens + card.token_cost_estimate > max_token_budget:
                continue

            cards.append(card)
            total_tokens += card.token_cost_estimate

            # 获取该卡的 guidance 类型规则
            card_rules = [r for r in self._rules.values() if r.card_id == card.id and r.rule_type == "guidance"]
            rules.extend(card_rules)

        return MethodologyGuidanceResult(
            cards=cards,
            rules=rules,
            total_token_estimate=total_tokens,
        )


# 全局单例
_methodology_adapter: MethodologyAdapter | None = None


def get_methodology_adapter() -> MethodologyAdapter:
    global _methodology_adapter
    if _methodology_adapter is None:
        _methodology_adapter = MethodologyAdapter()
    return _methodology_adapter
