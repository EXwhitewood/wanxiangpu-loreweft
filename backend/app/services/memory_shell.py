import json
import uuid

from datetime import datetime, timezone
from sqlalchemy import select, text, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import async_session, DetailSeed, Chapter, Project
from app.services.core_entity_resolver import normalize_entity_key


def _seed_dict(seed: DetailSeed) -> dict:
    return {
        "id": str(seed.id),
        "project_id": str(seed.project_id),
        "chapter_id": str(seed.chapter_id),
        "scene_number": seed.scene_number,
        "entity_id": seed.entity_id,
        "fact": seed.fact,
        "narrative_time": seed.narrative_time,
        "tier": seed.tier,
        "source_text": seed.source_text,
        "entity_type": seed.entity_type,
        "source_kind": seed.source_kind,
        "source_observation_id": str(seed.source_observation_id) if seed.source_observation_id else None,
        "metadata": seed.metadata_json,
        "created_at": seed.created_at.isoformat() if seed.created_at else "",
    }


def _bigrams(text: str) -> set[str]:
    text = normalize_entity_key(text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[index:index + 2] for index in range(len(text) - 1)}


class ShellMemoryService:
    async def store_detail_seed(self, project_id: str, seed_data: dict) -> dict:
        async with async_session() as session:
            chapter_id = seed_data.get("chapter_id")
            if chapter_id:
                chapter_uuid = uuid.UUID(str(chapter_id))
            else:
                chapter_number = seed_data.get("chapter_number", seed_data.get("source_chapter"))
                if not chapter_number:
                    raise ValueError("detail seed requires chapter_id or chapter_number")
                result = await session.execute(
                    select(Chapter).where(
                        Chapter.project_id == uuid.UUID(project_id),
                        Chapter.chapter_number == int(chapter_number),
                    )
                )
                chapter = result.scalar_one_or_none()
                if not chapter:
                    raise ValueError(f"chapter {chapter_number} must exist before storing detail seeds")
                chapter_uuid = chapter.id

            metadata = seed_data.get("metadata", {}) or {}
            entity_id = (
                seed_data.get("entity_id")
                or metadata.get("name")
                or seed_data.get("type")
                or seed_data.get("entity_type")
            )
            fact = seed_data.get("fact") or seed_data.get("content")
            if not entity_id or not fact:
                raise ValueError("detail seed requires entity_id and fact")

            seed_id = seed_data.get("id")
            if seed_id:
                seed_uuid = uuid.UUID(str(seed_id))
                existing = await session.get(DetailSeed, seed_uuid)
                if existing:
                    payload = _seed_dict(existing)
                    from app.services.sqlite_fts_service import SqliteFtsService
                    await SqliteFtsService().index_detail_seed(payload)
                    return payload
            else:
                seed_uuid = None

            tier = seed_data.get("tier")
            if not tier:
                entity_type = seed_data.get("entity_type", "")
                if entity_type == "foreshadowing":
                    tier = "T1"
                elif entity_type == "character_state":
                    tier = "T2"
                else:
                    tier = "T3"

            seed = DetailSeed(
                id=seed_uuid,
                project_id=uuid.UUID(project_id),
                chapter_id=chapter_uuid,
                scene_number=seed_data.get("scene_number", 1),
                entity_id=str(entity_id),
                fact=str(fact),
                embedding=seed_data.get("embedding"),
                narrative_time=seed_data.get("narrative_time"),
                tier=tier,
                source_text=seed_data.get("source_text", ""),
                entity_type=seed_data.get("entity_type", ""),
                source_kind=seed_data.get("source_kind", "harvester"),
                source_observation_id=uuid.UUID(str(seed_data["source_observation_id"])) if seed_data.get("source_observation_id") else None,
                metadata_json=seed_data.get("metadata", {}) or {},
            )
            session.add(seed)
            await session.commit()
            await session.refresh(seed)
            payload = _seed_dict(seed)
            from app.services.sqlite_fts_service import SqliteFtsService
            await SqliteFtsService().index_detail_seed(payload)
            return payload

    async def search_details(
        self,
        project_id: str,
        query: str,
        entity_id: str | None = None,
        narrative_time: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        fts_rows: list[dict] = []
        if query and query.strip():
            try:
                from app.services.sqlite_fts_service import SqliteFtsService
                fts_rows = await SqliteFtsService().search_text(
                    project_id,
                    query,
                    limit=max(limit * 4, 20),
                    filters={"entity_id": entity_id} if entity_id else None,
                )
            except Exception:
                fts_rows = []
        fts_ids = {str(row.get("id")): index for index, row in enumerate(fts_rows)}
        async with async_session() as session:
            conditions = [DetailSeed.project_id == uuid.UUID(project_id)]
            if entity_id:
                conditions.append(DetailSeed.entity_id == entity_id)
            if narrative_time:
                conditions.append(DetailSeed.narrative_time == narrative_time)

            stmt = select(DetailSeed).where(*conditions).order_by(DetailSeed.created_at.desc()).limit(250)
            result = await session.execute(stmt)
            seeds = list(result.scalars().all())
            additional = []
            if query and query.strip():
                exact_stmt = (
                    select(DetailSeed)
                    .where(
                        *conditions,
                        or_(
                            DetailSeed.entity_id.contains(query),
                            DetailSeed.fact.contains(query),
                            DetailSeed.source_text.contains(query),
                        ),
                    )
                    .order_by(DetailSeed.created_at.desc())
                    .limit(100)
                )
                additional.extend((await session.execute(exact_stmt)).scalars().all())
            fts_uuid_ids = []
            for row in fts_rows:
                try:
                    fts_uuid_ids.append(uuid.UUID(str(row.get("id"))))
                except (TypeError, ValueError, AttributeError):
                    continue
            if fts_uuid_ids:
                fts_stmt = select(DetailSeed).where(
                    *conditions,
                    DetailSeed.id.in_(fts_uuid_ids),
                )
                additional.extend((await session.execute(fts_stmt)).scalars().all())
            seen_seed_ids = {str(seed.id) for seed in seeds}
            for seed in additional:
                if str(seed.id) not in seen_seed_ids:
                    seeds.append(seed)
                    seen_seed_ids.add(str(seed.id))
            query_key = normalize_entity_key(query)
            query_bigrams = _bigrams(query)
            ranked = []
            tier_bonus = {"T1": 12.0, "T2": 7.0, "T3": 2.0}
            for recency_index, seed in enumerate(seeds):
                payload = _seed_dict(seed)
                haystack = normalize_entity_key(" ".join((
                    seed.entity_id or "", seed.fact or "", seed.source_text or "",
                    str(seed.metadata_json or ""),
                )))
                score = tier_bonus.get(str(seed.tier), 0.0)
                reasons = []
                if not query_key:
                    score += max(0.0, 10.0 - recency_index * 0.05)
                    reasons.append("recent_fallback_for_empty_query")
                else:
                    entity_key = normalize_entity_key(seed.entity_id)
                    if query_key == entity_key:
                        score += 120
                        reasons.append("exact_entity_match")
                    if query_key in haystack:
                        score += 75
                        reasons.append("exact_substring_match")
                    overlap = query_bigrams & _bigrams(haystack)
                    if query_bigrams:
                        ratio = len(overlap) / len(query_bigrams)
                        score += ratio * 45
                        if ratio:
                            reasons.append("cjk_bigram_overlap")
                    if str(seed.id) in fts_ids:
                        score += max(8.0, 30.0 - fts_ids[str(seed.id)])
                        reasons.append("fts5_match")
                    if not reasons:
                        continue
                payload["_score"] = round(score, 4)
                payload["retrieval_reason"] = reasons
                ranked.append(payload)
            ranked.sort(key=lambda item: (-float(item.get("_score") or 0), item.get("created_at", ""), item["id"]))
            return ranked[:limit]

    async def store_chapter_summary(self, project_id: str, chapter_id: str, summary: str) -> None:
        async with async_session() as session:
            result = await session.execute(
                select(Chapter).where(Chapter.id == uuid.UUID(chapter_id))
            )
            chapter = result.scalar_one_or_none()
            if chapter:
                core_data = chapter.__dict__.get("core_data", {}) or {}
                if not hasattr(chapter, "summary"):
                    pass
                await session.commit()

    async def search_chapters(
        self,
        project_id: str,
        query: str,
        limit: int = 5,
    ) -> list[dict]:
        async with async_session() as session:
            stmt = select(Chapter).where(Chapter.project_id == uuid.UUID(project_id))
            result = await session.execute(stmt)
            chapters = result.scalars().all()

            rows = [
                {
                    "id": str(c.id),
                    "chapter_number": c.chapter_number,
                    "title": c.title,
                    "content": c.content[:500] if c.content else "",
                    "status": c.status,
                    "_score": (
                        100 if normalize_entity_key(query) and normalize_entity_key(query) in normalize_entity_key(f"{c.title} {c.content}")
                        else 0
                    ),
                }
                for c in chapters
            ]
            rows.sort(key=lambda item: (-item["_score"], -item["chapter_number"]))
            if query and not any(item["_score"] for item in rows):
                return []
            return rows[:limit]

    async def check_promotion_candidates(self, project_id: str) -> list[dict]:
        async with async_session() as session:
            stmt = select(DetailSeed).where(
                DetailSeed.project_id == uuid.UUID(project_id)
            )
            result = await session.execute(stmt)
            seeds = result.scalars().all()

            entity_counts: dict[str, int] = {}
            entity_facts: dict[str, str] = {}
            entity_seed_ids: dict[str, str] = {}
            for s in seeds:
                if s.tier not in ("T1", "T2"):
                    continue
                eid = s.entity_id
                entity_counts[eid] = entity_counts.get(eid, 0) + 1
                if eid not in entity_facts:
                    entity_facts[eid] = s.fact
                    entity_seed_ids[eid] = str(s.id)

            candidates = []
            for eid, count in entity_counts.items():
                if count >= 3:
                    candidates.append({
                        "seed_id": entity_seed_ids[eid],
                        "content": entity_facts[eid],
                        "reference_count": count,
                    })

            candidates.sort(key=lambda x: x["reference_count"], reverse=True)
            return candidates

    async def update_seed_tier(self, project_id: str, seed_id: str, tier: str) -> dict | None:
        async with async_session() as session:
            stmt = select(DetailSeed).where(
                DetailSeed.project_id == uuid.UUID(project_id),
                DetailSeed.id == uuid.UUID(seed_id),
            )
            result = await session.execute(stmt)
            seed = result.scalar_one_or_none()
            if not seed:
                return None
            seed.tier = tier
            await session.commit()
            return {"seed_id": seed_id, "tier": tier}

    async def get_seeds_by_tier(self, project_id: str, tier: str | None = None) -> list[dict]:
        async with async_session() as session:
            conditions = [DetailSeed.project_id == uuid.UUID(project_id)]
            if tier:
                conditions.append(DetailSeed.tier == tier)

            stmt = select(DetailSeed).where(*conditions).order_by(DetailSeed.created_at.desc())
            result = await session.execute(stmt)
            seeds = result.scalars().all()

            return [
                {
                    "id": str(s.id),
                    "entity_id": s.entity_id,
                    "fact": s.fact,
                    "narrative_time": s.narrative_time,
                    "tier": s.tier,
                    "source_text": s.source_text,
                    "scene_number": s.scene_number,
                    "entity_type": s.entity_type,
                    "source_kind": s.source_kind,
                    "source_observation_id": str(s.source_observation_id) if s.source_observation_id else None,
                    "metadata": s.metadata_json,
                }
                for s in seeds
            ]

    async def compress_volume(self, project_id: str, volume_end_chapter: int) -> dict:
        """方案 10 Part D：统一数据源为 DetailSeed 表。

        旧实现从 project.shell_data["detail_seeds"] 读取，与 store_detail_seed
        写入的 DetailSeed 表不一致（双轨存储）。修改为从 DetailSeed 表读取，
        压缩后写回 DetailSeed 表（删除旧 T3，新增 volume_summary）。
        """
        async with async_session() as session:
            result = await session.execute(
                select(Project).where(Project.id == uuid.UUID(project_id))
            )
            project = result.scalar_one_or_none()
            if not project:
                return {"compressed": 0}

            # 方案 10 Part D：从 DetailSeed 表读取（与 store_detail_seed 数据源一致）
            stmt = select(DetailSeed).where(
                DetailSeed.project_id == uuid.UUID(project_id),
            )
            seed_result = await session.execute(stmt)
            all_seeds = seed_result.scalars().all()

            # 旧 T3 且章节距离足够远的，视为可压缩
            old_t3 = [
                s for s in all_seeds
                if s.tier == "T3"
                and (s.scene_number or 0) < volume_end_chapter - 5
            ]

            # 删除旧 T3
            for seed in old_t3:
                await session.delete(seed)

            # 新增卷摘要种子
            summary_seed = DetailSeed(
                id=uuid.uuid4(),
                project_id=uuid.UUID(project_id),
                chapter_id=all_seeds[0].chapter_id if all_seeds else None,
                scene_number=0,
                entity_id="volume_summary",
                fact=f"第{volume_end_chapter // 10 + 1}卷摘要：包含{len(old_t3)}条已压缩的补充细节",
                tier="T3",
                source_text="",
                entity_type="volume_summary",
                source_kind="compress_volume",
                metadata_json={
                    "compressed": True,
                    "original_count": len(old_t3),
                    "volume_end_chapter": volume_end_chapter,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            session.add(summary_seed)
            await session.commit()

            remaining_count = len(all_seeds) - len(old_t3) + 1

            return {
                "compressed": len(old_t3),
                "remaining": remaining_count,
                "volume_end_chapter": volume_end_chapter,
            }

    async def iterative_search(self, project_id: str, query: str, max_rounds: int = 3) -> list[dict]:
        all_results = []
        seen_ids = set()
        current_query = query

        for round_num in range(max_rounds):
            results = await self.search_details(project_id, current_query, limit=5)

            new_results = [r for r in results if r.get("id") not in seen_ids]
            if not new_results:
                break

            for r in new_results:
                seen_ids.add(r.get("id"))
                r["search_round"] = round_num + 1
                all_results.append(r)

            if len(all_results) >= 10:
                break

            facts = [r.get("fact", "") for r in new_results]
            current_query = f"基于以下线索进一步搜索：{'；'.join(facts[:3])}"

        return all_results[:15]
