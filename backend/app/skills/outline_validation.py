class OutlineValidationSkill:
    def validate(self, outline_data: dict, core_data: dict | None = None) -> dict:
        issues = []
        chapters = outline_data.get("chapters", [])
        chapter_spine = outline_data.get("chapter_spine")

        issues.extend(self._check_foreshadowing_closure(outline_data))
        issues.extend(self._check_character_arcs(chapters))
        # chapter_spine is the canonical outline representation. Legacy
        # `chapters` is only used when no spine exists.
        if isinstance(chapter_spine, list) and chapter_spine:
            issues.extend(self._check_chapter_sequence(chapter_spine))
        else:
            issues.extend(self._check_timeline(chapters))

        if core_data:
            issues.extend(self._check_character_consistency(chapters, core_data))

        spine_issues = []
        if chapter_spine is not None:
            spine_issues.extend(self._check_conflict_completeness(chapter_spine))
            spine_issues.extend(self._check_value_shift_validity(chapter_spine))
            spine_issues.extend(self._check_thread_ops_references(
                chapter_spine, outline_data.get("thread_plan"),
            ))
            spine_issues.extend(self._check_chapter_dependencies(chapter_spine))
            issues.extend(spine_issues)

        return {
            "valid": len([i for i in issues if i.get("severity") == "error"]) == 0,
            "issues": issues,
            "spine_issues": spine_issues,
            "summary": {
                "total_issues": len(issues),
                "errors": len([i for i in issues if i.get("severity") == "error"]),
                "warnings": len([i for i in issues if i.get("severity") == "warning"]),
            },
        }

    def _check_chapter_sequence(self, chapter_spine: list[dict]) -> list[dict]:
        diagnostics = chapter_sequence_diagnostics({"chapter_spine": chapter_spine})
        if diagnostics["valid"]:
            return []

        issues: list[dict] = []
        if diagnostics["invalid_entries"]:
            issues.append({
                "type": "invalid_chapter_number",
                "severity": "error",
                "message": "章节脊柱中存在无效章节号，章节必须使用从第1章开始的正整数。",
                "details": diagnostics["invalid_entries"],
            })
        if diagnostics["duplicates"]:
            issues.append({
                "type": "duplicate_chapter",
                "severity": "error",
                "message": f"章节号重复：{', '.join(str(n) for n in diagnostics['duplicates'])}。",
                "details": diagnostics["duplicates"],
            })
        if diagnostics["missing"]:
            missing = diagnostics["missing"]
            preview = ", ".join(str(n) for n in missing[:12])
            suffix = "等" if len(missing) > 12 else ""
            issues.append({
                "type": "chapter_gap",
                "severity": "error",
                "message": f"章节号不连续，缺少第{preview}章{suffix}。章节必须从第1章连续规划。",
                "details": {"missing": missing, "max_chapter": diagnostics["max_chapter"]},
            })
        return issues

    def _check_foreshadowing_closure(self, outline_data: dict) -> list[dict]:
        issues = []
        foreshadowing = outline_data.get("foreshadowing", [])

        planted_not_revealed = [
            f for f in foreshadowing
            if f.get("status") == "active" and not f.get("reveal_window_start")
        ]

        for f in planted_not_revealed:
            issues.append({
                "type": "foreshadowing_unclosed",
                "severity": "warning",
                "message": f"伏笔「{f.get('name', '未命名')}」已埋设但未指定揭示章节",
                "item_id": f.get("id"),
            })

        total_chapters = len(outline_data.get("chapters", []))
        for f in foreshadowing:
            reveal_ch = f.get("reveal_window_start")
            if reveal_ch and isinstance(reveal_ch, (int, float)) and reveal_ch > total_chapters:
                issues.append({
                    "type": "foreshadowing_out_of_range",
                    "severity": "error",
                    "message": f"伏笔「{f.get('name', '未命名')}」的揭示章节({int(reveal_ch)})超出大纲范围({total_chapters}章)",
                    "item_id": f.get("id"),
                })

        return issues

    def _check_character_arcs(self, chapters: list[dict]) -> list[dict]:
        issues = []
        character_chapters: dict[str, list[int]] = {}

        for ch in chapters:
            ch_num = ch.get("chapter_number", 0)
            scenes = ch.get("scenes", [])
            for scene in scenes:
                characters = scene.get("characters", [])
                if isinstance(characters, str):
                    characters = [characters]
                for char in characters:
                    if char not in character_chapters:
                        character_chapters[char] = []
                    character_chapters[char].append(ch_num)

        for char, ch_list in character_chapters.items():
            if len(ch_list) >= 2:
                gaps = []
                sorted_ch = sorted(set(ch_list))
                for i in range(1, len(sorted_ch)):
                    gap = sorted_ch[i] - sorted_ch[i-1]
                    if gap > 3:
                        gaps.append((sorted_ch[i-1], sorted_ch[i]))

                for start, end in gaps:
                    issues.append({
                        "type": "character_gap",
                        "severity": "warning",
                        "message": f"角色「{char}」在第{start}章到第{end}章之间缺席超过3章",
                    })

        return issues

    def _check_timeline(self, chapters: list[dict]) -> list[dict]:
        issues = []
        seen_numbers = set()

        for ch in chapters:
            ch_num = ch.get("chapter_number")
            if ch_num is not None:
                if ch_num in seen_numbers:
                    issues.append({
                        "type": "duplicate_chapter",
                        "severity": "error",
                        "message": f"章节号 {ch_num} 重复",
                    })
                seen_numbers.add(ch_num)

        if seen_numbers:
            sorted_nums = sorted(seen_numbers)
            for i in range(1, len(sorted_nums)):
                if sorted_nums[i] - sorted_nums[i-1] != 1:
                    issues.append({
                        "type": "chapter_gap",
                        "severity": "warning",
                        "message": f"章节号不连续：第{sorted_nums[i-1]}章后跳到第{sorted_nums[i]}章",
                    })

        return issues

    def _check_character_consistency(self, chapters: list[dict], core_data: dict) -> list[dict]:
        issues = []
        core_characters = {c.get("name", c.get("id", "")): c for c in core_data.get("characters", [])}

        for ch in chapters:
            scenes = ch.get("scenes", [])
            for scene in scenes:
                characters = scene.get("characters", [])
                if isinstance(characters, str):
                    characters = [characters]
                for char in characters:
                    if char not in core_characters and char.startswith("char_"):
                        issues.append({
                            "type": "unknown_character",
                            "severity": "warning",
                            "message": f"场景中引用了未在人物核心中定义的角色「{char}」",
                        })

        return issues

    def _check_conflict_completeness(self, chapter_spine: list[dict]) -> list[dict]:
        issues = []
        required_keys = ("desire", "obstacle", "action", "turn")

        for idx, spine_item in enumerate(chapter_spine):
            item_id = spine_item.get("id", f"spine_{idx}")
            conflict_text = spine_item.get("conflict_text")
            core_conflict = spine_item.get("core_conflict")

            if conflict_text and str(conflict_text).strip():
                continue

            if core_conflict is None:
                issues.append({
                    "type": "conflict_missing",
                    "severity": "error",
                    "message": f"章节骨架项「{item_id}」缺少 core_conflict 且 conflict_text 为空",
                    "item_id": item_id,
                })
                continue

            if not isinstance(core_conflict, dict):
                issues.append({
                    "type": "conflict_invalid",
                    "severity": "error",
                    "message": f"章节骨架项「{item_id}」的 core_conflict 不是字典类型",
                    "item_id": item_id,
                })
                continue

            missing = [k for k in required_keys if not core_conflict.get(k) or not str(core_conflict.get(k)).strip()]
            if missing:
                issues.append({
                    "type": "conflict_incomplete",
                    "severity": "warning",
                    "message": f"章节骨架项「{item_id}」的 core_conflict 缺少字段: {', '.join(missing)}",
                    "item_id": item_id,
                    "missing_fields": missing,
                })

        return issues

    def _check_value_shift_validity(self, chapter_spine: list[dict]) -> list[dict]:
        issues = []
        synonym_groups = [
            {"信任", "信赖", "相信"},
            {"怀疑", "猜疑", "不信任"},
            {"希望", "期盼", "期待"},
            {"绝望", "失望", "无望"},
            {"爱", "热爱", "钟爱"},
            {"恨", "憎恨", "仇恨"},
            {"勇敢", "无畏", "刚毅"},
            {"恐惧", "害怕", "畏缩"},
            {"自由", "自主", "独立"},
            {"束缚", "囚禁", "受限"},
        ]

        for idx, spine_item in enumerate(chapter_spine):
            item_id = spine_item.get("id", f"spine_{idx}")
            value_shift = spine_item.get("value_shift")
            if value_shift is None:
                continue

            if not isinstance(value_shift, dict):
                continue

            from_val = str(value_shift.get("from", "")).strip()
            to_val = str(value_shift.get("to", "")).strip()

            if not from_val or not to_val:
                issues.append({
                    "type": "value_shift_incomplete",
                    "severity": "warning",
                    "message": f"章节骨架项「{item_id}」的 value_shift 缺少 from 或 to 字段",
                    "item_id": item_id,
                })
                continue

            if from_val == to_val:
                issues.append({
                    "type": "value_shift_no_change",
                    "severity": "warning",
                    "message": f"章节骨架项「{item_id}」的 value_shift 的 from 和 to 相同「{from_val}」",
                    "item_id": item_id,
                })
                continue

            from_group = None
            to_group = None
            for group in synonym_groups:
                if from_val in group:
                    from_group = group
                if to_val in group:
                    to_group = group

            if from_group is not None and to_group is not None and from_group is to_group:
                issues.append({
                    "type": "value_shift_synonymous",
                    "severity": "warning",
                    "message": f"章节骨架项「{item_id}」的 value_shift 的 from「{from_val}」与 to「{to_val}」同义，缺乏实质转变",
                    "item_id": item_id,
                })

        return issues

    def _check_thread_ops_references(self, chapter_spine: list[dict], thread_plan: dict | None) -> list[dict]:
        issues = []

        if thread_plan is None:
            return issues

        valid_thread_ids = set()
        for thread in thread_plan.get("threads", []):
            tid = thread.get("id") or thread.get("thread_id")
            if tid:
                valid_thread_ids.add(tid)

        for idx, spine_item in enumerate(chapter_spine):
            item_id = spine_item.get("id", f"spine_{idx}")
            thread_ops = spine_item.get("thread_ops")
            if thread_ops is None:
                continue

            if isinstance(thread_ops, list):
                ops_list = thread_ops
            elif isinstance(thread_ops, dict):
                ops_list = [thread_ops]
            else:
                continue

            for op in ops_list:
                if not isinstance(op, dict):
                    continue
                ref_id = op.get("thread_id")
                if ref_id and ref_id not in valid_thread_ids:
                    issues.append({
                        "type": "thread_ref_not_found",
                        "severity": "error",
                        "message": f"章节骨架项「{item_id}」的 thread_ops 引用了不存在的 thread_id「{ref_id}」",
                        "item_id": item_id,
                        "thread_id": ref_id,
                    })

        return issues

    def _check_chapter_dependencies(self, chapter_spine: list[dict]) -> list[dict]:
        issues = []

        spine_ids = set()
        for spine_item in chapter_spine:
            item_id = spine_item.get("id")
            if item_id:
                spine_ids.add(item_id)

        for idx, spine_item in enumerate(chapter_spine):
            item_id = spine_item.get("id", f"spine_{idx}")
            depends_on = spine_item.get("depends_on")
            if depends_on is None:
                continue

            if isinstance(depends_on, str):
                depends_on = [depends_on]
            elif not isinstance(depends_on, list):
                continue

            for dep_id in depends_on:
                if dep_id not in spine_ids:
                    issues.append({
                        "type": "depends_on_not_found",
                        "severity": "error",
                        "message": f"章节骨架项「{item_id}」的 depends_on 引用了不存在的项「{dep_id}」",
                        "item_id": item_id,
                        "depends_on": dep_id,
                    })

        adj: dict[str, list[str]] = {}
        node_ids = []
        for spine_item in chapter_spine:
            item_id = spine_item.get("id")
            if not item_id:
                continue
            node_ids.append(item_id)
            adj[item_id] = []

        for spine_item in chapter_spine:
            item_id = spine_item.get("id")
            if not item_id:
                continue
            depends_on = spine_item.get("depends_on")
            if depends_on is None:
                continue
            if isinstance(depends_on, str):
                depends_on = [depends_on]
            elif not isinstance(depends_on, list):
                continue
            for dep_id in depends_on:
                if dep_id in adj:
                    adj[item_id].append(dep_id)

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in node_ids}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for neighbor in adj.get(node, []):
                if color[neighbor] == GRAY:
                    return True
                if color[neighbor] == WHITE and dfs(neighbor):
                    return True
            color[node] = BLACK
            return False

        for nid in node_ids:
            if color[nid] == WHITE:
                if dfs(nid):
                    issues.append({
                        "type": "circular_dependency",
                        "severity": "error",
                        "message": "章节骨架项之间存在循环依赖",
                    })
                    break

        return issues


def chapter_sequence_diagnostics(outline_data: dict) -> dict:
    """Return the canonical chapter-number contract for an outline.

    Partial planning is allowed (for example chapters 1-10 before a later
    extension), but every saved prefix must be contiguous from chapter 1.
    Sparse anchor lists such as 1, 4, 7 are therefore invalid and must never
    be presented as a complete chapter outline.
    """
    spine = outline_data.get("chapter_spine")
    chapters = outline_data.get("chapters")
    source = spine if isinstance(spine, list) and spine else chapters
    if not isinstance(source, list) or not source:
        return {
            "valid": True,
            "source": "none",
            "chapter_count": 0,
            "max_chapter": 0,
            "numbers": [],
            "missing": [],
            "duplicates": [],
            "invalid_entries": [],
        }

    source_name = "chapter_spine" if source is spine else "chapters"
    numbers: list[int] = []
    invalid_entries: list[dict] = []
    for index, item in enumerate(source):
        raw_number = item.get("chapter_number") if isinstance(item, dict) else None
        if isinstance(raw_number, bool) or not isinstance(raw_number, int) or raw_number <= 0:
            invalid_entries.append({"index": index, "value": raw_number})
            continue
        numbers.append(raw_number)

    sorted_numbers = sorted(numbers)
    duplicates = sorted({n for i, n in enumerate(sorted_numbers) if i > 0 and n == sorted_numbers[i - 1]})
    max_chapter = max(sorted_numbers, default=0)
    expected = set(range(1, max_chapter + 1)) if max_chapter else set()
    missing = sorted(expected - set(numbers))
    valid = bool(numbers) and not invalid_entries and not duplicates and not missing and sorted_numbers[0] == 1

    return {
        "valid": valid,
        "source": source_name,
        "chapter_count": len(source),
        "max_chapter": max_chapter,
        "numbers": sorted_numbers,
        "missing": missing,
        "duplicates": duplicates,
        "invalid_entries": invalid_entries,
    }
