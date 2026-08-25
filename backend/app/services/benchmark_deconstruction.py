"""对标拆解中心服务。

对标拆解是"项目引用视图"，不是全局经验库。
默认只给结构化策略，不给原文 few-shot。
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
from app.models.benchmark import (
    BenchmarkBook,
    BenchmarkChapter,
    BenchmarkDeconstructionRequest,
    BenchmarkDeconstructionResult,
    BenchmarkStrategyCard,
    BenchmarkStyleProfile,
)

logger = logging.getLogger(__name__)


_STORAGE_DIR = Path(app_settings.data_dir) / "benchmark"


class BenchmarkDeconstructionService:
    """对标拆解中心服务"""

    def __init__(self, storage_dir: str | Path | None = None):
        self._storage_dir = Path(storage_dir) if storage_dir is not None else _STORAGE_DIR
        self._books: dict[str, BenchmarkBook] = {}
        self._chapters: dict[str, list[BenchmarkChapter]] = {}
        self._style_profiles: dict[str, BenchmarkStyleProfile] = {}
        self._strategy_cards: dict[str, list[BenchmarkStrategyCard]] = {}
        self._deconstruction_results: dict[str, BenchmarkDeconstructionResult] = {}
        self._load_from_disk()

    def _load_from_disk(self):
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        books_file = self._storage_dir / "books.json"
        if books_file.exists():
            try:
                data = json.loads(books_file.read_text(encoding="utf-8"))
                for item in data:
                    book = BenchmarkBook(**item)
                    self._books[book.id] = book
            except Exception as e:
                logger.warning(f"加载对标书失败: {e}")

        chapters_file = self._storage_dir / "chapters.json"
        if chapters_file.exists():
            try:
                data = json.loads(chapters_file.read_text(encoding="utf-8"))
                for book_id, chapters in data.items():
                    self._chapters[book_id] = [BenchmarkChapter(**c) for c in chapters]
            except Exception as e:
                logger.warning(f"加载对标章节失败: {e}")

        for filename, model_cls, target in (
            ("style_profiles.json", BenchmarkStyleProfile, self._style_profiles),
            ("deconstruction_results.json", BenchmarkDeconstructionResult, self._deconstruction_results),
        ):
            path = self._storage_dir / filename
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                for key, value in payload.items():
                    target[key] = model_cls(**value)
            except Exception as e:
                logger.warning("加载对标拆解产物 %s 失败: %s", filename, e)

        strategy_path = self._storage_dir / "strategy_cards.json"
        if strategy_path.exists():
            try:
                payload = json.loads(strategy_path.read_text(encoding="utf-8"))
                self._strategy_cards = {
                    book_id: [BenchmarkStrategyCard(**item) for item in items]
                    for book_id, items in payload.items()
                }
            except Exception as e:
                logger.warning("加载对标策略卡失败: %s", e)

    def _save_to_disk(self):
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self._storage_dir / "books.json",
            json.dumps([b.model_dump() for b in self._books.values()], ensure_ascii=False, indent=2),
        )
        chapters_data = {
            bid: [c.model_dump() for c in chapters]
            for bid, chapters in self._chapters.items()
        }
        atomic_write_text(
            self._storage_dir / "chapters.json",
            json.dumps(chapters_data, ensure_ascii=False, indent=2),
        )
        atomic_write_text(
            self._storage_dir / "style_profiles.json",
            json.dumps(
                {key: value.model_dump() for key, value in self._style_profiles.items()},
                ensure_ascii=False,
                indent=2,
            ),
        )
        atomic_write_text(
            self._storage_dir / "strategy_cards.json",
            json.dumps(
                {
                    key: [item.model_dump() for item in items]
                    for key, items in self._strategy_cards.items()
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        atomic_write_text(
            self._storage_dir / "deconstruction_results.json",
            json.dumps(
                {key: value.model_dump() for key, value in self._deconstruction_results.items()},
                ensure_ascii=False,
                indent=2,
            ),
        )

    # ---- 对标书管理 ----

    def register_book(self, book: BenchmarkBook) -> BenchmarkBook:
        if not book.id:
            book.id = str(uuid.uuid4())[:8]
        if not book.imported_at:
            book.imported_at = datetime.now().isoformat()
        self._books[book.id] = book
        self._save_to_disk()
        return book

    def list_books(self) -> list[BenchmarkBook]:
        return list(self._books.values())

    def get_book(self, book_id: str) -> Optional[BenchmarkBook]:
        return self._books.get(book_id)

    def delete_book(self, book_id: str) -> bool:
        if book_id not in self._books:
            return False
        del self._books[book_id]
        self._chapters.pop(book_id, None)
        self._style_profiles.pop(book_id, None)
        self._strategy_cards.pop(book_id, None)
        self._deconstruction_results.pop(book_id, None)
        self._save_to_disk()
        return True

    def bind_to_project(self, book_id: str, project_id: str) -> Optional[BenchmarkBook]:
        book = self._books.get(book_id)
        if book and project_id not in book.project_bindings:
            book.project_bindings.append(project_id)
            self._save_to_disk()
        return book

    def unbind_from_project(self, book_id: str, project_id: str) -> Optional[BenchmarkBook]:
        book = self._books.get(book_id)
        if book and project_id in book.project_bindings:
            book.project_bindings.remove(project_id)
            self._save_to_disk()
        return book

    def get_project_books(self, project_id: str) -> list[BenchmarkBook]:
        return [b for b in self._books.values() if project_id in b.project_bindings]

    def set_chapters(
        self,
        book_id: str,
        chapters: list[BenchmarkChapter],
    ) -> list[BenchmarkChapter] | None:
        if book_id not in self._books:
            return None
        normalized = []
        for chapter in chapters:
            if not chapter.id:
                chapter.id = str(uuid.uuid4())[:8]
            chapter.book_id = book_id
            normalized.append(chapter)
        normalized.sort(key=lambda item: item.chapter_number)
        self._chapters[book_id] = normalized
        self._save_to_disk()
        return normalized

    def purge_project_binding(self, project_id: str) -> int:
        changed = 0
        for book in self._books.values():
            if project_id in book.project_bindings:
                book.project_bindings = [value for value in book.project_bindings if value != project_id]
                changed += 1
        if changed:
            self._save_to_disk()
        return changed

    # ---- 拆解 ----

    async def start_deconstruction(self, request: BenchmarkDeconstructionRequest) -> BenchmarkDeconstructionResult:
        """启动对标拆解"""
        book = self._books.get(request.book_id)
        if not book:
            return BenchmarkDeconstructionResult(
                book_id=request.book_id, status="failed", error="对标书不存在"
            )

        result = BenchmarkDeconstructionResult(
            book_id=request.book_id, status="in_progress", progress=0.0
        )
        self._deconstruction_results[request.book_id] = result

        # 使用 Chapter Extractor 进行拆解
        try:
            from app.services.chapter_extractor import get_chapter_extractor
            from app.models.chapter_structure import ChapterExtractionInput

            extractor = get_chapter_extractor()
            chapters = self._chapters.get(request.book_id, [])

            max_ch = min(len(chapters), request.max_chapters)
            if request.focus == "golden_three":
                max_ch = min(3, max_ch)
            if max_ch <= 0:
                result.status = "failed"
                result.error = "对标书没有可拆解章节"
                self._save_to_disk()
                return result

            for i, chapter in enumerate(chapters[:max_ch]):
                extract_input = ChapterExtractionInput(
                    chapter_text=chapter.summary or "",
                    chapter_number=chapter.chapter_number,
                    extract_mode="benchmark",
                )
                extract_result = await extractor.extract(extract_input)

                # 更新章节信息
                chapter.plot_events = [e.description for e in extract_result.plot_events]
                chapter.character_mentions = [m.name for m in extract_result.character_mentions]
                chapter.hook_type = ", ".join(h.hook_type for h in extract_result.hook_points)
                chapter.emotion_arc = " -> ".join(extract_result.emotion_curve[:5]) if extract_result.emotion_curve else ""

                result.progress = (i + 1) / max_ch
                result.chapters.append(chapter)

            # 生成策略卡
            result.strategy_cards = self._generate_strategy_cards(request.book_id, result.chapters)
            self._strategy_cards[request.book_id] = result.strategy_cards

            result.status = "completed"
            result.progress = 1.0

        except Exception as e:
            logger.error(f"对标拆解失败: {e}")
            result.status = "failed"
            result.error = str(e)

        self._save_to_disk()
        return result

    def _generate_strategy_cards(self, book_id: str, chapters: list[BenchmarkChapter]) -> list[BenchmarkStrategyCard]:
        """从拆解结果生成策略卡"""
        cards = []
        # 黄金三章策略
        if len(chapters) >= 3:
            cards.append(BenchmarkStrategyCard(
                id=str(uuid.uuid4())[:8],
                book_id=book_id,
                category="opening_design",
                title="黄金三章开篇策略",
                strategy=f"前三章建立核心冲突和角色动机，章末设置悬念钩子",
                evidence=f"第1章钩子: {chapters[0].hook_type}; 第3章钩子: {chapters[2].hook_type}",
            ))

        # 章节钩子策略
        hook_types = [c.hook_type for c in chapters if c.hook_type]
        if hook_types:
            cards.append(BenchmarkStrategyCard(
                id=str(uuid.uuid4())[:8],
                book_id=book_id,
                category="chapter_hook",
                title="章节钩子策略",
                strategy=f"使用多样化钩子类型维持读者兴趣",
                evidence=f"钩子类型分布: {', '.join(set(hook_types))}",
            ))

        return cards

    def get_deconstruction_result(self, book_id: str) -> Optional[BenchmarkDeconstructionResult]:
        return self._deconstruction_results.get(book_id)

    def get_strategy_cards(self, book_id: str) -> list[BenchmarkStrategyCard]:
        return self._strategy_cards.get(book_id, [])

    def get_project_strategy_cards(self, project_id: str) -> list[BenchmarkStrategyCard]:
        """获取项目绑定的所有策略卡"""
        cards = []
        for book in self.get_project_books(project_id):
            cards.extend(self._strategy_cards.get(book.id, []))
        return cards


# 全局单例
_benchmark_service: BenchmarkDeconstructionService | None = None

def get_benchmark_service() -> BenchmarkDeconstructionService:
    global _benchmark_service
    if _benchmark_service is None:
        _benchmark_service = BenchmarkDeconstructionService()
    return _benchmark_service
