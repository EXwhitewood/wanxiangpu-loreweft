---
name: prose-repair
description: 修复 AI 味、表达单调和局部文气问题
version: '1'
x-loreweft:
  id: prose_repair
  display_name: 文笔修复
  format_version: open_skill_v1
  kind: prompt
  domain: fbi
  agents:
  - fbi_prose_repair
  priority: 80
  enabled_by_default: true
  triggers:
    issue_types:
    - ai_template_phrase
    - ai_emotion_label
    - monotonous_expression
---

# 文笔修复

针对文笔层面的问题（AI模板短语、情感标签、表达单调）进行定向修复。

## 规则

1. **精准替换**：仅替换被标记的问题片段，不得扩大修改范围。
2. **语境适配**：替换内容必须与上下文语境自然衔接，不得产生风格割裂。
3. **多样性优先**：同一问题类型在不同位置应使用不同的替换方案，避免修复后产生新的模式化。
4. **保留意图**：修复操作不得改变原文的叙事意图和情感走向，只改变表达方式。
5. **修复记录**：每次修复必须记录原文、替换文、修复原因，便于回溯和审计。
