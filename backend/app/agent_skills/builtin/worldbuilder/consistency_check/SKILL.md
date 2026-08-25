---
name: consistency-check
description: 检查世界观设定间的一致性
version: '1'
x-loreweft:
  id: consistency_check
  display_name: 一致性检查
  format_version: open_skill_v1
  kind: prompt
  domain: worldbuilder
  agents:
  - worldbuilder
  priority: 70
  enabled_by_default: true
  triggers:
    tabs:
    - rules
---

# 一致性检查

验证世界观设定内部及设定与正文之间的一致性。

## 规则

1. **规则冲突检测**：扫描所有规则条目，标记直接矛盾或间接冲突的规则对。
2. **正文-设定对照**：检查正文中涉及世界观描述的段落，与设定库进行对照，标记不一致处。
3. **时间线一致性**：验证设定中的历史事件时间线无矛盾，因果关系成立。
4. **能力边界验证**：确保角色在正文中的行为未超越其设定能力范围。
5. **报告格式**：一致性检查结果按严重程度排列——致命矛盾（阻断发布）、潜在冲突（需人工确认）、建议优化（可选调整）。
