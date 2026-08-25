---
name: core-query
description: 查询核心事实和设定数据
version: '1'
x-loreweft:
  id: core_query
  display_name: 核心查询
  format_version: open_skill_v1
  kind: service
  domain: generation
  agents:
  - consistency_check
  - detail_detective
  - plot_commentator
  - branch_explorer
  priority: 70
  enabled_by_default: true
  runtime:
    service_class: app.skills.core_query.CoreQuerySkill
    service_method: execute
---

# 核心查询

提供对小说核心数据（角色、设定、情节线）的结构化查询能力。

## 规则

1. **精确匹配优先**：查询时优先使用精确标识符（角色ID、设定项ID），仅在无标识符时退化为模糊匹配。
2. **查询范围限定**：每次查询必须指定查询域（角色/设定/情节），禁止全库扫描式查询。
3. **结果截断**：单次查询返回结果不超过 5 条，超出部分需分页获取。
4. **缓存友好**：相同查询参数在短时间内的重复调用应返回缓存结果，避免重复计算。
