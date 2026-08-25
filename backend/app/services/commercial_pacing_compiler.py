from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.commercial_pacing import CommercialPacingContract


class CompiledCommercialPacing(BaseModel):
    schema_version: int = 1
    commercial_pacing_contract: CommercialPacingContract
    quality_extensions_patch: dict = Field(default_factory=dict)
    compiler_warnings: list[str] = Field(default_factory=list)


class CommercialPacingCompiler:
    """Compile scene contracts into commercial reader-retention targets."""

    def compile(self, context: dict) -> CompiledCommercialPacing:
        scene_contract = context.get("scene_contract") if isinstance(context.get("scene_contract"), dict) else {}
        scene_beat = context.get("scene_beat") if isinstance(context.get("scene_beat"), dict) else {}
        chapter_number = int(context.get("chapter_number") or scene_contract.get("chapter_number") or 0)
        scene_index = int(context.get("scene_index") or scene_contract.get("scene_index") or 0)
        profile_id = str(context.get("commercial_pacing_profile_id") or "general")
        position = _scene_position(scene_index, context.get("scene_count"))

        goal = _first_text(
            scene_contract.get("goal"),
            scene_contract.get("source_of_truth", {}).get("goal") if isinstance(scene_contract.get("source_of_truth"), dict) else "",
            scene_beat.get("goal"),
        )
        conflict = _first_text(
            scene_contract.get("conflict"),
            scene_contract.get("source_of_truth", {}).get("conflict") if isinstance(scene_contract.get("source_of_truth"), dict) else "",
            scene_beat.get("conflict"),
        )
        hook = _first_text(scene_contract.get("hook"), scene_beat.get("hook"), scene_contract.get("ending_state"))
        must_show = _as_list(scene_contract.get("must_show"))
        forbidden = _as_list(scene_contract.get("forbidden"))
        withheld = _as_list((scene_contract.get("experience_contract") or {}).get("withheld_information")) if isinstance(scene_contract.get("experience_contract"), dict) else []

        target_metrics = _target_metrics(profile_id, chapter_number, position)
        reader_hook = hook or conflict or goal
        reader_question = _reader_question(reader_hook, conflict, position)
        end_hook = scene_contract.get("ending_state") or hook or reader_question

        contract = CommercialPacingContract(
            project_id=str(context.get("project_id") or ""),
            chapter_number=chapter_number,
            scene_index=scene_index,
            pacing_mode_id=profile_id,
            scene_position=position,
            reader_hook=reader_hook,
            reader_question=reader_question,
            promise_to_payoff=_promise_to_payoff(goal, must_show),
            conflict_driver=conflict,
            pressure_ramp=_pressure_ramp(position),
            reversal_plan=_reversal_plan(scene_contract, scene_beat),
            micro_payoffs=_micro_payoffs(must_show, hook),
            withheld_cards=(withheld or forbidden)[:5],
            chapter_end_hook=end_hook,
            target_metrics=target_metrics,
            guardrails=[
                "商业节奏目标不得覆盖事实合同、视角锁定和禁止项。",
                "爽点必须由当前场景动作、发现、代价或关系变化承载，不能空降解释。",
                "本层只提高留读驱动力，不要求模仿任何具体作品或作者。",
            ],
            # 方案 30：从大纲层提取语义判定辅助字段
            conflict_beat=_conflict_beat(scene_contract, scene_beat),
            hook_design=_hook_design(hook, end_hook),
            abstract_explanation_allowlist=_abstract_allowlist(scene_contract),
        )
        return CompiledCommercialPacing(
            commercial_pacing_contract=contract,
            quality_extensions_patch=self._quality_extensions_patch(contract),
        )

    @staticmethod
    def _quality_extensions_patch(contract: CommercialPacingContract) -> dict:
        guidance: list[str] = []
        if contract.reader_hook:
            guidance.append(f"开场尽早给出可感知钩子：{contract.reader_hook}")
        if contract.reader_question:
            guidance.append(f"让读者带着问题阅读：{contract.reader_question}")
        if contract.conflict_driver:
            guidance.append(f"冲突必须现场化，不只解释：{contract.conflict_driver}")
        if contract.pressure_ramp:
            guidance.append(f"压力曲线：{contract.pressure_ramp}")
        if contract.reversal_plan:
            guidance.append("安排至少一次认知变化或局势转折：" + "；".join(contract.reversal_plan[:2]))
        if contract.micro_payoffs:
            guidance.append("给出局部兑现，避免纯铺垫：" + "；".join(contract.micro_payoffs[:2]))
        if contract.chapter_end_hook:
            guidance.append(f"结尾停在新问题、代价、危险或身份变化上：{contract.chapter_end_hook}")

        return {
            "commercial_pacing_guidance": guidance[:7],
            "commercial_pacing_targets": {
                "pacing_mode_id": contract.pacing_mode_id,
                "scene_position": contract.scene_position,
                "target_metrics": contract.target_metrics,
            },
        }


def _scene_position(scene_index: int, scene_count) -> str:
    try:
        count = int(scene_count or 0)
    except (TypeError, ValueError):
        count = 0
    if scene_index <= 0:
        return "opening"
    if count and scene_index >= count - 1:
        return "outro"
    if count and scene_index >= max(1, int(count * 0.65)):
        return "climax"
    return "middle"


def _target_metrics(profile_id: str, chapter_number: int, position: str) -> dict:
    base = {
        "opening_hook": 7.0,
        "event_density": 6.5,
        "conflict_density": 6.5,
        "pressure_ramp": 6.5,
        "curiosity_engine": 6.5,
        "reversal_density": 5.5,
        "payoff_delivery": 5.8,
        "chapter_end_hook": 6.5,
        "protagonist_drive": 6.5,
        "reader_retention": 6.8,
    }
    if profile_id in {"commercial_web", "suspense_web", "xianxia_web"}:
        base.update({
            "opening_hook": 7.5,
            "conflict_density": 7.0,
            "curiosity_engine": 7.0,
            "chapter_end_hook": 7.2,
            "reader_retention": 7.2,
        })
    if chapter_number == 1 and position == "opening":
        base["opening_hook"] = max(base["opening_hook"], 8.0)
        base["reader_retention"] = max(base["reader_retention"], 7.5)
    if position == "outro":
        base["chapter_end_hook"] = max(base["chapter_end_hook"], 7.5)
    return base


def _reader_question(reader_hook: str, conflict: str, position: str) -> str:
    seed = reader_hook or conflict
    if not seed:
        return "这个场景会把局势推向什么不可逆变化？"
    if position == "opening":
        return f"{seed}背后真正的问题是什么？"
    return f"{seed}会带来什么代价或反转？"


def _promise_to_payoff(goal: str, must_show: list[str]) -> str:
    if must_show:
        return f"至少兑现一个可见信息点：{must_show[0]}"
    if goal:
        return f"让目标产生可见进展或明确受阻：{goal}"
    return "本场必须产生一个可感知进展，不能只铺设气氛。"


def _pressure_ramp(position: str) -> str:
    if position == "opening":
        return "先给异常或目标，再抬出阻碍，结尾留下更大的问题。"
    if position == "outro":
        return "持续收紧压力，结尾停在代价、危险、身份或选择上。"
    if position == "climax":
        return "让阻碍升级，迫使角色做出不可回退的行动。"
    return "每段都应推动目标、阻碍、发现或代价之一。"


def _reversal_plan(scene_contract: dict, scene_beat: dict) -> list[str]:
    candidates = []
    for key in ("hook", "ending_state", "conflict"):
        value = scene_contract.get(key) or scene_beat.get(key)
        if value:
            candidates.append(f"围绕“{value}”制造认知变化")
    return candidates[:3]


def _micro_payoffs(must_show: list[str], hook: str) -> list[str]:
    payoffs = [f"让“{item}”以动作、物件或对话被看见" for item in must_show[:3]]
    if hook:
        payoffs.append(f"对“{hook}”给出一个碎片式兑现")
    return payoffs[:4]


def _first_text(*values) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            return str(value[0]).strip()
    return ""


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    if isinstance(value, dict):
        return [str(item) for item in value.values() if item is not None and str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


# 方案 30：语义判定辅助字段编译
def _conflict_beat(scene_contract: dict, scene_beat: dict) -> list[str]:
    """从大纲层提取压力升级点列表，供 LLM 判定参考预期冲突设计。"""
    beats: list[str] = []
    for key in ("conflict", "turn", "resolution", "pressure"):
        value = scene_contract.get(key) or scene_beat.get(key)
        if value:
            beats.append(str(value).strip())
    return beats[:5]


def _hook_design(hook: str, end_hook: str) -> dict[str, str]:
    """构建钩子设计 dict，供 LLM 判定参考预期开头/结尾钩子。"""
    design: dict[str, str] = {}
    if hook:
        design["opening"] = hook.strip()
    if end_hook:
        design["ending"] = end_hook.strip()
    return design


def _abstract_allowlist(scene_contract: dict) -> list[str]:
    """提取允许的抽象表达（如世界观设定的必要抽象词），避免误判。"""
    allowlist = scene_contract.get("abstract_explanation_allowlist") or []
    if isinstance(allowlist, list):
        return [str(item).strip() for item in allowlist if item and str(item).strip()]
    return []
