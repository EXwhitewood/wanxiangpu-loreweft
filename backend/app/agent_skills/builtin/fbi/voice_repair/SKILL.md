---
name: voice-repair
description: 修复角色说话方式和视角认知不一致
version: '1'
x-loreweft:
  id: voice_repair
  display_name: 角色声音修复
  format_version: open_skill_v1
  kind: prompt
  domain: fbi
  agents:
  - fbi_voice_repair
  priority: 75
  enabled_by_default: true
  triggers:
    issue_types:
    - character_voice_flat
    - pov_cognition_leak
---

# 声音修复

针对角色声音问题（角色声音扁平、视角认知泄漏）进行定向修复。

## 规则

1. **声音特征库**：每个角色必须有明确的声音特征定义（用词习惯/句式偏好/知识范围/情感表达方式）。
2. **对话归属可辨**：修复后的对话应能仅凭语言风格辨识说话角色，无需依赖对话标签。
3. **视角认知边界**：修复视角泄漏时，必须将叙事内容限制在当前视角角色的认知范围内，删除超出认知的描述。
4. **一致性维护**：同一角色在不同场景中的声音特征必须一致，不得因修复导致角色声音突变。
5. **群体区分**：不同角色的声音特征必须有显著区分度，避免所有角色说话方式趋同。
