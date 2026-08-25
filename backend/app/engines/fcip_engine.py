import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any


META_TERMS = ("伏笔", "埋设", "线索", "暗示", "读者", "叙事需求", "回收", "揭示任务")

logger = logging.getLogger(__name__)


COGNITIVE_LEVEL_CONSTRAINTS = {
    "fully_blind": [
        "当前视角角色不能主动注意、猜测或谈论该细节。",
        "不要写成突如其来的直觉、预感或无来源的不安。",
    ],
    "vague_unease": [
        "当前视角角色只可出现短暂迟疑、身体反应或注意力偏移。",
        "不能把这种反应归因到具体原因。",
    ],
    "partial_clue": [
        "当前视角角色只可记录可感知事实，不进行串联推理。",
        "不要使用“难道、莫非、会不会、这意味着”等推理句式。",
    ],
    "high_suspicion": [
        "当前视角角色可以试探，但结论必须保留偏差和不确定性。",
    ],
    "fully_aware": [
        "当前视角角色可以按已知事实行动，但不要用内心独白解释设定。",
    ],
    "misled": [
        "当前视角角色可以沿错误方向理解，并据此行动。",
        "不要让角色突然怀疑自己被误导。",
    ],
}


@dataclass
class FCIPContext:
    project_id: uuid.UUID
    chapter_number: int
    scene_index: int = 0
    pov_character: str | None = None
    active_foreshadowing: list[dict] = field(default_factory=list)
    character_cognitive_map: dict[str, list[dict]] = field(default_factory=dict)


class FCIPEngine:
    """Foreshadowing cognitive isolation compiler."""

    def __init__(self, foreshadowing_service):
        self.foreshadowing_service = foreshadowing_service

    async def build_context(
        self,
        project_id: str | uuid.UUID,
        chapter_number: int,
        db,
        pov_character: str | None = None,
        scene_index: int = 0,
    ) -> FCIPContext:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
        ctx = FCIPContext(
            project_id=pid,
            chapter_number=chapter_number,
            scene_index=scene_index,
            pov_character=pov_character,
        )
        ctx.active_foreshadowing = await self.foreshadowing_service.list_actionable_for_scene(
            pid, chapter_number, db
        )
        if pov_character:
            ctx.character_cognitive_map[pov_character] = (
                await self.foreshadowing_service.get_character_cognitive_states(
                    pid, pov_character, db, chapter_number=chapter_number
                )
            )
        return ctx

    def compile_for_scene(self, ctx: FCIPContext, raw_instructions: list[dict] | None = None) -> list[dict]:
        compiled: list[dict] = []
        seen = set()
        for item in ctx.active_foreshadowing:
            key = str(item.get("id") or item.get("name"))
            seen.add(item.get("name"))
            hint = self._compile_item(ctx, item)
            if hint:
                compiled.append(hint)

        for item in raw_instructions or []:
            if item.get("name") in seen:
                continue
            seen.add(item.get("name"))
            hint = self._compile_legacy_instruction(ctx, item)
            if hint:
                compiled.append(hint)
        return compiled

    def _compile_item(self, ctx: FCIPContext, item: dict[str, Any]) -> dict | None:
        level = self._cognitive_level(ctx, item)
        constraints = COGNITIVE_LEVEL_CONSTRAINTS.get(level, COGNITIVE_LEVEL_CONSTRAINTS["fully_blind"])
        detail = self._safe_detail_text(item)
        action = item.get("action") or "maintain"
        lines = []
        if action == "reveal":
            lines.append("让一个既有细节以外部事实、证物、对话停顿或人物行动的方式重新进入场景。")
        else:
            lines.append("加入一个日常可观察的细节，让它自然存在于环境、动作或对话节奏中。")
        if detail:
            lines.append(f"可见内容：{detail}")
        lines.extend(constraints)
        text = self._sanitize_compiled_text("\n".join(f"- {line}" for line in lines if line))
        return {
            "name": item.get("name", ""),
            "action": action,
            "constraint_level": level,
            "compiled_hint": text,
        }

    def _compile_legacy_instruction(self, ctx: FCIPContext, item: dict[str, Any]) -> dict | None:
        action = item.get("action", "maintain")
        lines = []
        if action == "reveal":
            lines.append("让相关信息通过外部事件或可验证事实进入场景。")
        else:
            lines.append("将该内容改写为角色当下可感知的环境、动作或对话细节。")
        lines.append("不要直接写出原始说明中的结论性身份、动机、因果或世界规则。")
        lines.extend(COGNITIVE_LEVEL_CONSTRAINTS["fully_blind"])
        text = self._sanitize_compiled_text("\n".join(f"- {line}" for line in lines if line))
        return {
            "name": item.get("name", ""),
            "action": action,
            "constraint_level": "fully_blind",
            "compiled_hint": text,
        }

    def _cognitive_level(self, ctx: FCIPContext, item: dict[str, Any]) -> str:
        if not ctx.pov_character:
            return "fully_blind"
        states = ctx.character_cognitive_map.get(ctx.pov_character, [])
        item_id = item.get("id")
        for state in states:
            if state.get("foreshadowing_line_id") == item_id or str(state.get("foreshadowing_line_id")) == str(item_id):
                return state.get("cognitive_level") or "fully_blind"
        return "fully_blind"

    def _safe_detail_text(self, item: dict[str, Any]) -> str:
        detail = item.get("reader_allowed_interpretations") or ""
        if isinstance(detail, list):
            detail = "、".join(str(x) for x in detail[:3])
        if not detail:
            return ""
        return self._sanitize_compiled_text(str(detail))

    def _sanitize_compiled_text(self, text: str) -> str:
        sanitized = text
        replacements = {
            "伏笔": "后续信息",
            "埋设": "加入",
            "线索": "细节",
            "暗示": "含蓄呈现",
            "读者": "阅读体验",
            "叙事需求": "场景要求",
            "回收": "回应",
            "揭示任务": "信息进入场景",
        }
        for src, dst in replacements.items():
            sanitized = sanitized.replace(src, dst)
        # Remove accidental exact secret-like labels in brackets if any leaked from legacy names.
        sanitized = re.sub(r"「[^」]{1,40}」", "", sanitized)
        return sanitized.strip()

    async def check_budget(
        self,
        ctx: FCIPContext,
        db,
    ) -> dict:
        clue_service = _lazy_clue_service()
        active_count = len(ctx.active_foreshadowing)
        chapter_clue_count = 0

        for fs in ctx.active_foreshadowing:
            fs_id = fs.get("id")
            if not fs_id:
                continue
            try:
                pool = await clue_service.get_evidence_pool(fs_id, db)
                for c in pool.get("clues", []):
                    if c.get("chapter_number") == ctx.chapter_number:
                        chapter_clue_count += 1
            except Exception as exc:
                logger.warning("[FCIP] 加载证据池失败 fs_id=%s: %s", fs_id, exc)

        clue_density = chapter_clue_count / max(active_count, 1) if active_count > 0 else 0.0

        warnings: list[str] = []
        budget_config = _get_budget_config()
        if active_count > budget_config["max_active_count"]:
            warnings.append(f"活跃伏笔数 {active_count} 超过推荐上限 {budget_config['max_active_count']}，考虑推进部分伏笔进入 dormant")
        if clue_density > budget_config["clue_density_max"]:
            warnings.append(f"线索密度 {clue_density:.2f} 超过推荐上限 {budget_config['clue_density_max']}，考虑减少本场景埋设")

        return {
            "active_count": active_count,
            "chapter_clue_count": chapter_clue_count,
            "clue_density": round(clue_density, 3),
            "within_budget": len(warnings) == 0,
            "warnings": warnings,
        }

    async def post_gen_writeback(
        self,
        project_id: str | uuid.UUID,
        chapter_number: int,
        generated_text: str,
        db,
        llm_client=None,
        apply_writeback: bool = True,
    ) -> dict:
        from app.engines.fcip_detector import run_deterministic_checks, rule_fh04_l1_conclusion_deviation, rule_fh10_reader_over_obscuring

        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
        clue_service = _lazy_clue_service()

        actionable = await self.foreshadowing_service.list_actionable_for_scene(pid, chapter_number, db)

        foreshadowing_items: list[dict] = []
        for fs in actionable:
            fs_id = fs.get("id")
            if not fs_id:
                continue
            char_states = await self.foreshadowing_service.list_cognitive_states_for_foreshadowing(
                pid, fs_id, db, chapter_number=chapter_number,
            )
            pool = await clue_service.get_evidence_pool(fs_id, db)
            recent_texts = [
                c.get("clue_text", "")
                for c in pool.get("clues", [])
                if c.get("chapter_number") is not None
                and c["chapter_number"] != chapter_number
                and abs(c["chapter_number"] - chapter_number) < (fs.get("repetition_distance") or 2)
            ]
            foreshadowing_items.append({
                **fs,
                "character_states": char_states,
                "recent_clue_texts": recent_texts,
            })

        all_violations = run_deterministic_checks(generated_text, foreshadowing_items, chapter_number)

        if llm_client:
            for fs in actionable:
                char_states = await self.foreshadowing_service.list_cognitive_states_for_foreshadowing(
                    pid, fs.get("id"), db, chapter_number=chapter_number,
                )
                for cs in char_states:
                    if cs.get("cognitive_level") == "high_suspicion":
                        try:
                            v = await rule_fh04_l1_conclusion_deviation(
                                generated_text, cs["character_name"], llm_client, chapter_number,
                            )
                            all_violations.extend(v)
                        except Exception as exc:
                            logger.warning("[FCIP] FH04 L1 结论偏差检测失败 char=%s: %s", cs.get("character_name"), exc)

            revealed_secrets = [
                fs.get("secret_canonical_statement", "")
                for fs in actionable
                if fs.get("status") == "resolved"
            ]
            if revealed_secrets:
                try:
                    v = await rule_fh10_reader_over_obscuring(
                        generated_text, revealed_secrets, llm_client, chapter_number,
                    )
                    all_violations.extend(v)
                except Exception as exc:
                    logger.warning("[FCIP] FH10 读者过度遮蔽检测失败: %s", exc)

        if apply_writeback:
            # 方案 11 Part D Bug 2：post_gen_writeback 必须推进伏笔状态。
            # 原逻辑只更新角色认知状态，不推伏笔状态机，导致 revealing 永远停在 revealing。
            state_advancements: list[dict] = []
            for fs in actionable:
                fs_id = fs.get("id")
                if not fs_id:
                    continue
                current_status = fs.get("status")
                reveal_start = fs.get("reveal_window_start")
                reveal_end = fs.get("reveal_window_end")
                in_reveal_window = (
                    reveal_start is not None
                    and reveal_end is not None
                    and reveal_start <= chapter_number <= reveal_end
                )

                # 推进 revealing → resolved（在揭示窗口内且本章有 reveal 动作）
                if current_status == "revealing" and in_reveal_window:
                    try:
                        await self.foreshadowing_service.transition_state(
                            pid, fs_id, "resolved", "fcip_engine.post_gen_writeback", db,
                        )
                        state_advancements.append({
                            "foreshadowing_id": str(fs_id),
                            "name": fs.get("name", ""),
                            "from": "revealing",
                            "to": "resolved",
                            "chapter": chapter_number,
                        })
                    except Exception as exc:
                        logger.warning("[FCIP] 推进 revealing→resolved 失败 fs_id=%s: %s", fs_id, exc)

                # 推进 active/dormant → revealing（在揭示窗口开始时）
                elif current_status in ("active", "dormant") and reveal_start == chapter_number:
                    try:
                        await self.foreshadowing_service.transition_state(
                            pid, fs_id, "revealing", "fcip_engine.post_gen_writeback", db,
                        )
                        state_advancements.append({
                            "foreshadowing_id": str(fs_id),
                            "name": fs.get("name", ""),
                            "from": current_status,
                            "to": "revealing",
                            "chapter": chapter_number,
                        })
                    except Exception as exc:
                        logger.warning("[FCIP] 推进 %s→revealing 失败 fs_id=%s: %s", current_status, fs_id, exc)

                # 推进 planned → active（在埋设窗口内）
                elif current_status == "planned":
                    bury_start = fs.get("bury_window_start")
                    bury_end = fs.get("bury_window_end")
                    if (
                        bury_start is not None
                        and bury_end is not None
                        and bury_start <= chapter_number <= bury_end
                    ):
                        try:
                            await self.foreshadowing_service.transition_state(
                                pid, fs_id, "active", "fcip_engine.post_gen_writeback", db,
                            )
                            state_advancements.append({
                                "foreshadowing_id": str(fs_id),
                                "name": fs.get("name", ""),
                                "from": "planned",
                                "to": "active",
                                "chapter": chapter_number,
                            })
                        except Exception as exc:
                            logger.warning("[FCIP] 推进 planned→active 失败 fs_id=%s: %s", fs_id, exc)

                # revealing 状态下更新角色认知状态（保留原逻辑）
                if fs.get("status") == "revealing" or any(
                    a["foreshadowing_id"] == str(fs_id) and a["to"] == "resolved"
                    for a in state_advancements
                ):
                    spoiler_chars = (fs.get("secret_spoiler_scope") or {}).get("characters", [])
                    for char_name in spoiler_chars:
                        try:
                            await self.foreshadowing_service.set_character_cognitive_state(
                                pid, fs_id, char_name, "informed", chapter_number, db,
                                cognitive_level="fully_aware",
                                event=f"第{chapter_number}章揭示",
                            )
                        except Exception as exc:
                            logger.warning("[FCIP] 更新角色认知状态失败 char=%s: %s", char_name, exc)

        return {
            "violations_found": len(all_violations),
            "violations": [v.to_dict() for v in all_violations],
            "critical_count": sum(1 for v in all_violations if v.severity == "Critical"),
            "high_count": sum(1 for v in all_violations if v.severity == "High"),
            # 方案 11 Part D Bug 2：返回状态推进记录，便于审计
            "state_advancements": state_advancements if apply_writeback else [],
        }

    @staticmethod
    def contains_meta_terms(text: str) -> bool:
        return any(term in text for term in META_TERMS)


def _lazy_clue_service():
    from app.services.foreshadowing_clue_service import ForeshadowingClueService
    return ForeshadowingClueService()


def _get_budget_config():
    try:
        from app.engines.foreshadowing_config import FORESHADOWING_CONFIG
        return FORESHADOWING_CONFIG["budget"]
    except Exception:
        return {"max_active_count": 3, "clue_density_min": 0.02, "clue_density_max": 0.12}
