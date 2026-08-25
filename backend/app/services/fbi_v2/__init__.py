"""FBI V2 — 专业维修部门。

FBI V2 不再直接消费散乱 issue，而是消费 RepairOrder。
每个 RepairOrder 是一个可执行、可验证、可回滚的修复任务。

修复通道：
- deterministic: 确定性修复（不需要 LLM）
- fbi_fact: 事实专员
- fbi_prose: 文体专员
- fbi_pacing: 节奏专员
- fbi_style_guard: 风格守门员
- fbi_scene_restructure: 场景重构专员
- fbi_ending: 结尾补写专员
- manual_only: 仅人工
"""
