---
name: contract-compilation
description: 编译叙事体验与文学质量合同
version: '1'
x-loreweft:
  id: contract_compilation
  display_name: 合同编译
  format_version: open_skill_v1
  kind: prompt
  domain: editor
  agents:
  - editor_in_chief
  priority: 85
  enabled_by_default: true
---

# 契约编译

将编辑意图编译为可执行的修复契约，明确修复的范围、约束和验收标准。

## 规则

1. **契约完整性**：每份修复契约必须包含 修复目标、影响范围、约束条件、验收标准 四个要素。
2. **范围最小化**：修复范围应精确到段落/句子级别，禁止以"整章"为单位划定修复范围。
3. **约束不可违反**：契约中的约束条件（如"不得改变角色对话内容""保留原文节奏"）在修复过程中不得违反。
4. **验收标准可量化**：验收标准必须可量化或可明确判定（如"AI模板短语数量降为0"），禁止模糊表述。
5. **契约不可变**：契约一旦编译完成，在执行过程中不得修改，需要调整时必须重新编译。
