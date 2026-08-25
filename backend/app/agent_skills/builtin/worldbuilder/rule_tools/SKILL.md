---
name: worldbuilder-rule-tools
description: 辅助规则架构师读取和更新世界规则
version: '1'
x-loreweft:
  id: worldbuilder_rule_tools
  display_name: 世界观规则工具
  format_version: open_skill_v1
  kind: tool
  domain: worldbuilder
  category: utility
  agents:
  - worldbuilder
  priority: 75
  enabled_by_default: true
  triggers:
    tabs:
    - rules
  runtime:
    tool_names:
    - list_world_rules
    - create_world_rule
    - update_world_rule
    min_permission: project_write
---

## 工具使用原则

- 只有在用户确认或候选建议明确时才写入。
- 修改已有规则前必须检查冲突。
- 新规则必须通过一致性检查。
