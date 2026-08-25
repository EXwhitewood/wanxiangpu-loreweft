---
name: thread-planning
description: 规划主线、支线和伏笔的埋设与回收
version: '1'
x-loreweft:
  id: thread_planning
  display_name: 线索规划
  format_version: open_skill_v1
  kind: prompt
  domain: outline
  agents:
  - outline_architect
  priority: 65
  enabled_by_default: false
---

# 线索规划

规划和管理多线索叙事的交织与收束。

## 规则

1. **线索注册**：每条叙事线索必须在大纲中注册，包含线索名称、起始章节、预期收束章节、当前状态。
2. **交织节奏**：多线索交替推进时，同一线索的两次出现间隔不超过 3 章，避免读者遗忘。
3. **收束规划**：所有线索必须在故事结束前收束，收束方式（合并/独立/悬置）需提前规划。
4. **线索优先级**：当线索数量超过 5 条时，必须区分主线和支线，支线不得占用超过 30% 的叙事篇幅。
5. **默认关闭**：此技能默认关闭，适用于多线索复杂叙事或用户明确要求线索规划时启用。
