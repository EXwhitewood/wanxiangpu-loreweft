import hashlib
import json


class SceneBudgetService:
    MINIMUM_SCENE_COUNT = 2
    MAX_RECOMMENDED_SCENE_COUNT = 5
    HIGH_IMPACT_HOOK_KEYWORDS = {
        "揭示", "反转", "危机", "回收", "真相",
        "背叛", "牺牲", "死亡", "发现", "暴露",
    }
    # must_show 条目超过此阈值时，信息复杂度加分
    MUST_SHOW_INFO_THRESHOLD = 4

    def calculate(self, chapter_spine_item: dict) -> dict:
        item = chapter_spine_item if isinstance(chapter_spine_item, dict) else {}
        reasons = []
        score = 0

        chapter_function = item.get("function")
        if chapter_function in {"transition", "recovery"}:
            score -= 1
            reasons.append(f"章节功能为 {chapter_function}")
        elif chapter_function in {"reversal", "payoff"}:
            score += 1
            reasons.append(f"章节功能为 {chapter_function}")

        conflict = item.get("core_conflict")
        conflict = conflict if isinstance(conflict, dict) else {}
        if all(conflict.get(field) for field in ("desire", "obstacle", "action", "turn")):
            score += 1
            reasons.append("核心冲突四元组完整")

        value_shift = item.get("value_shift")
        value_shift = value_shift if isinstance(value_shift, dict) else {}
        if value_shift.get("from") and value_shift.get("to") and value_shift["from"] != value_shift["to"]:
            score += 1
            reasons.append("价值状态发生明显转变")

        thread_ops = item.get("thread_ops")
        thread_ops = thread_ops if isinstance(thread_ops, list) else []
        if len(thread_ops) == 2:
            score += 1
            reasons.append("存在 2 个线索操作")
        elif len(thread_ops) >= 3:
            score += 2
            reasons.append(f"存在 {len(thread_ops)} 个线索操作")

        hook = item.get("hook")
        hook = hook if isinstance(hook, str) else ""
        if any(keyword in hook for keyword in self.HIGH_IMPACT_HOOK_KEYWORDS):
            score += 1
            reasons.append("章节钩子包含高影响事件")

        depends_on = item.get("depends_on")
        depends_on = depends_on if isinstance(depends_on, list) else []
        if len(depends_on) >= 2:
            score += 1
            reasons.append(f"依赖 {len(depends_on)} 个前置节点")

        # 信息复杂度维度：must_show 条目过多时增加复杂度评分
        # 这会导致推荐场景数增加，从而将信息分散到更多场景中
        # 只统计明确的 must_show，不 fallback 到 scenes（场景数 != 信息条目数）
        must_show_count = 0
        must_show_items = item.get("must_show")
        if isinstance(must_show_items, list):
            must_show_count = len(must_show_items)
        else:
            # 没有顶层 must_show 时，汇总各 scene 的 must_show
            scenes = item.get("scenes", [])
            if isinstance(scenes, list):
                for scene in scenes:
                    if isinstance(scene, dict):
                        scene_ms = scene.get("must_show", [])
                        if isinstance(scene_ms, list):
                            must_show_count += len(scene_ms)
        if must_show_count >= self.MUST_SHOW_INFO_THRESHOLD + 3:
            score += 2
            reasons.append(f"must_show 包含 {must_show_count} 条，信息复杂度极高")
        elif must_show_count >= self.MUST_SHOW_INFO_THRESHOLD:
            score += 1
            reasons.append(f"must_show 包含 {must_show_count} 条，信息复杂度偏高")

        if score <= 0:
            recommended = self.MINIMUM_SCENE_COUNT
        elif score <= 2:
            recommended = 3
        elif score <= 4:
            recommended = 4
        else:
            recommended = self.MAX_RECOMMENDED_SCENE_COUNT

        return {
            "minimum_scene_count": self.MINIMUM_SCENE_COUNT,
            "recommended_scene_count": recommended,
            "complexity_score": score,
            "scene_budget_reasons": reasons,
            "scene_budget_signature": self._compute_signature(item),
        }

    def _compute_signature(self, item: dict) -> str:
        payload = json.dumps(
            {
                "function": item.get("function", ""),
                "core_conflict": item.get("core_conflict", ""),
                "value_shift": item.get("value_shift", ""),
                "thread_ops": item.get("thread_ops", []),
                "hook": item.get("hook", ""),
                "depends_on": item.get("depends_on", []),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]
