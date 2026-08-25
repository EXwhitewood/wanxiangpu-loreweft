---
name: state-query
description: 查询世界状态和角色状态
version: '1'
x-loreweft:
  id: state_query
  display_name: 状态查询
  format_version: open_skill_v1
  kind: service
  domain: generation
  agents:
  - state_updater
  - consistency_check
  - detail_detective
  - plot_commentator
  - branch_explorer
  priority: 70
  enabled_by_default: true
  runtime:
    service_class: app.skills.state_query.StateQuerySkill
    service_method: execute
---

# 状态查询

查询和验证小说世界的运行时状态（角色位置、关系变化、物品持有等）。

## 规则

1. **时间点明确**：每次状态查询必须指定查询的时间点（当前/指定章节后/指定事件后），默认为当前最新状态。
2. **状态不可伪造**：查询结果必须来自状态存储的权威数据，禁止推测或编造未记录的状态。
3. **变更追踪**：返回状态时附带最近一次变更的章节和原因，便于追溯。
4. **批量查询优化**：支持一次查询多个实体的状态，减少查询轮次。
