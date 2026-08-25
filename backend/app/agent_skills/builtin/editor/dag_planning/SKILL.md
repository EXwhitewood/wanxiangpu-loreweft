---
name: dag-planning
description: 将章节拆解为场景执行计划和依赖关系图
version: '1'
x-loreweft:
  id: dag_planning
  display_name: DAG 规划
  format_version: open_skill_v1
  kind: prompt
  domain: editor
  agents:
  - editor_in_chief
  priority: 90
  enabled_by_default: true
---

# DAG规划

将编辑任务分解为有向无环图（DAG），确定任务执行顺序和依赖关系。

## 规则

1. **依赖显式化**：每个编辑任务必须声明其前置依赖，未声明依赖的任务视为可并行执行。
2. **无环保证**：DAG中不得存在循环依赖，构建时必须进行环检测。
3. **关键路径优先**：位于关键路径上的任务优先调度，非关键路径任务可延迟执行。
4. **任务粒度**：单个任务的粒度以"可独立验证"为标准，过大的任务必须拆分。
5. **失败隔离**：任一任务失败不得阻塞无依赖关系的其他任务，失败任务标记后跳过。
