---
name: fact-repair
description: 修复事实、因果和设定冲突
version: '1'
x-loreweft:
  id: fact_repair
  display_name: 事实修复
  format_version: open_skill_v1
  kind: prompt
  domain: fbi
  agents:
  - fbi_fact_repair
  priority: 90
  enabled_by_default: true
  triggers:
    issue_types:
    - fact_violation
    - state_inconsistency
    - causal_error
---

# 事实修复

针对事实层面的问题（设定违反、状态不一致、因果错误）进行定向修复。

## 规则

1. **事实优先**：事实修复的优先级高于所有其他修复类型，发现事实错误必须立即处理。
2. **权威数据源**：修复事实错误时，以世界观设定库和状态存储为权威数据源，不得凭推测修复。
3. **最小改动原则**：事实修复应采用最小改动策略，优先修改错误陈述而非重写整段。
4. **因果链修复**：修复因果错误时，必须验证修复后的因果链完整闭合，不得留下新的因果断裂。
5. **状态同步**：事实修复完成后，必须同步更新相关的状态记录，确保状态存储与正文一致。
