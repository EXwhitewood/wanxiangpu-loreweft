---
name: scene-restructure
description: 处理局部补丁无法修复的场景结构问题
version: '1'
x-loreweft:
  id: scene_restructure
  display_name: 场景重构
  format_version: open_skill_v1
  kind: prompt
  domain: fbi
  agents:
  - fbi_scene_restructure
  priority: 60
  enabled_by_default: false
  triggers:
    issue_types:
    - scene_structure_broken
    - information_order_wrong
---

# 场景重构

针对场景结构问题（场景结构破碎、信息顺序错误）进行重构级修复。

## 规则

1. **重构为最后手段**：场景重构是最高级别的修复操作，仅在场景结构严重问题时启用，默认关闭。
2. **结构诊断**：重构前必须完成完整的结构诊断，明确哪些部分需要重组、哪些需要删除、哪些需要新增。
3. **信息保留**：重构操作必须保留原场景中的所有关键信息，不得因重组而丢失信息。
4. **重构蓝图**：执行重构前必须生成重构蓝图（新的场景结构方案），经确认后方可执行。
5. **重构后验证**：重构完成后必须通过场景工艺技能的验证，确保重构后的场景满足三要素要求。
