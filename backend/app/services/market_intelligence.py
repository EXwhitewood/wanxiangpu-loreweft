"""市场情报服务。

情报页只负责市场输入和对标候选，不直接影响正文生成。
影响路径：情报页 -> 选题决策 -> 大纲/章级合同 -> 场景合同 -> 生成
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
from app.models.market_intelligence import (
    MarketSnapshot,
    MarketSnapshotItem,
    MarketTrend,
    TopicDecision,
    TopicDecisionContract,
)

logger = logging.getLogger(__name__)


_STORAGE_DIR = Path(app_settings.data_dir) / "market_intelligence"


class MarketIntelligenceService:
    """市场情报服务"""

    def __init__(self, storage_dir: str | Path | None = None):
        self._storage_dir = Path(storage_dir) if storage_dir is not None else _STORAGE_DIR
        self._snapshots: dict[str, MarketSnapshot] = {}
        self._trends: dict[str, MarketTrend] = {}
        self._topic_decisions: dict[str, TopicDecision] = {}
        self._load_from_disk()

    def _load_from_disk(self):
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        for name, model_cls, attr in [
            ("snapshots", MarketSnapshot, "_snapshots"),
            ("trends", MarketTrend, "_trends"),
            ("topic_decisions", TopicDecision, "_topic_decisions"),
        ]:
            path = self._storage_dir / f"{name}.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    for item in data:
                        obj = model_cls(**item)
                        getattr(self, attr)[obj.id] = obj
                except Exception as e:
                    logger.warning(f"加载 {name} 失败: {e}")

    def _save_to_disk(self):
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        for name, attr in [
            ("snapshots", "_snapshots"),
            ("trends", "_trends"),
            ("topic_decisions", "_topic_decisions"),
        ]:
            data = [obj.model_dump() for obj in getattr(self, attr).values()]
            atomic_write_text(
                self._storage_dir / f"{name}.json",
                json.dumps(data, ensure_ascii=False, indent=2),
            )

    # ---- 快照管理 ----

    def import_snapshot(self, snapshot: MarketSnapshot) -> MarketSnapshot:
        if not snapshot.id:
            snapshot.id = str(uuid.uuid4())[:8]
        if not snapshot.captured_at:
            snapshot.captured_at = datetime.now().isoformat()
        self._snapshots[snapshot.id] = snapshot
        self._save_to_disk()
        return snapshot

    def list_snapshots(self, platform: str = "", limit: int = 50) -> list[MarketSnapshot]:
        results = list(self._snapshots.values())
        if platform:
            results = [s for s in results if s.platform == platform]
        return sorted(results, key=lambda s: s.captured_at, reverse=True)[:limit]

    def get_snapshot(self, snapshot_id: str) -> Optional[MarketSnapshot]:
        return self._snapshots.get(snapshot_id)

    def delete_snapshot(self, snapshot_id: str) -> bool:
        if snapshot_id not in self._snapshots:
            return False
        del self._snapshots[snapshot_id]
        self._save_to_disk()
        return True

    # ---- 趋势分析 ----

    def analyze_trends(self, platform: str = "") -> list[MarketTrend]:
        """从快照中分析趋势"""
        snapshots = list(self._snapshots.values())
        if platform:
            snapshots = [s for s in snapshots if s.platform == platform]

        # 按题材聚合
        genre_items: dict[str, list[MarketSnapshotItem]] = {}
        for snapshot in snapshots:
            for item in snapshot.items:
                if item.genre not in genre_items:
                    genre_items[item.genre] = []
                genre_items[item.genre].append(item)

        trends = []
        for genre, items in genre_items.items():
            # 提取标题模式
            title_patterns = list(set(item.title.split()[0] if item.title else "" for item in items[:20]))[:5]
            trends.append(MarketTrend(
                id=str(uuid.uuid4())[:8],
                platform=platform or "all",
                genre=genre,
                repeated_patterns=[f"共{len(items)}本上榜"],
                title_patterns=[t for t in title_patterns if t],
                reader_signal=f"题材热度: {len(items)}本上榜",
                confidence=min(1.0, len(items) / 30.0),
            ))

        self._trends = {t.id: t for t in trends}
        self._save_to_disk()
        return trends

    def list_trends(self, platform: str = "") -> list[MarketTrend]:
        results = list(self._trends.values())
        if platform:
            results = [t for t in results if t.platform in (platform, "all")]
        return results

    # ---- 选题决策 ----

    def create_topic_decision(self, decision: TopicDecision) -> TopicDecision:
        if not decision.id:
            decision.id = str(uuid.uuid4())[:8]
        if not decision.created_at:
            decision.created_at = datetime.now().isoformat()
        self._topic_decisions[decision.id] = decision
        self._save_to_disk()
        return decision

    def list_topic_decisions(self, project_id: str = "") -> list[TopicDecision]:
        results = list(self._topic_decisions.values())
        if project_id:
            results = [d for d in results if d.project_id == project_id]
        return results

    def confirm_topic_decision(self, decision_id: str) -> Optional[TopicDecision]:
        decision = self._topic_decisions.get(decision_id)
        if decision:
            decision.status = "confirmed"
            self._save_to_disk()
        return decision

    def get_topic_contract(self, project_id: str) -> Optional[TopicDecisionContract]:
        """获取项目当前的选题决策合同（主编可读取的短字段）"""
        for decision in self._topic_decisions.values():
            if decision.project_id == project_id and decision.status in ("confirmed", "active"):
                return TopicDecisionContract(
                    target_platform=decision.target_platform,
                    reader_expectation=f"追求{decision.core_emotion}体验",
                    core_emotion=decision.core_emotion,
                    opening_pressure="高" if decision.candidate_hooks else "中",
                    benchmark_strategy_ids=decision.benchmark_candidates,
                    avoid_competition_risks=decision.risk_notes,
                )
        return None

    def purge_project(self, project_id: str) -> int:
        decision_ids = [
            decision_id
            for decision_id, decision in self._topic_decisions.items()
            if decision.project_id == project_id
        ]
        for decision_id in decision_ids:
            self._topic_decisions.pop(decision_id, None)
        if decision_ids:
            self._save_to_disk()
        return len(decision_ids)


# 全局单例
_market_service: MarketIntelligenceService | None = None

def get_market_intelligence_service() -> MarketIntelligenceService:
    global _market_service
    if _market_service is None:
        _market_service = MarketIntelligenceService()
    return _market_service
