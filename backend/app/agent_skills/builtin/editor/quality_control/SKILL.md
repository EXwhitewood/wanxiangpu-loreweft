---
name: quality-control
description: 评估生成质量，决定是否需要重试或调整参数
version: '1'
x-loreweft:
  id: quality_control
  display_name: 质量控制
  format_version: open_skill_v1
  kind: prompt
  domain: editor
  agents:
  - editor_in_chief
  priority: 80
  enabled_by_default: true
---

# 质量控制

主编级别的文本质量把关，确保输出文本达到发布标准。

## 规则

1. **多维评估**：质量控制必须从文学性、一致性、可读性、AI痕迹四个维度进行评估，不得遗漏。
2. **问题分级**：发现的问题分为阻断级（必须修复才能发布）、建议级（推荐修复）、参考级（可选优化）。
3. **修复优先级**：阻断级问题按 事实错误 > 逻辑矛盾 > AI痕迹 > 文学性不足 的顺序修复。
4. **修复不引入新问题**：每次修复操作后必须验证未引入新的问题，避免修复链式反应。
5. **质量门控**：所有阻断级问题清零后，文本方可通过质量门控进入下一流程。
