---
name: style-experience-resolution
description: 协调风格层与体验层的冲突，生成场景级体验合同
version: '1'
x-loreweft:
  id: style_experience_resolution
  display_name: 风格体验决策
  format_version: open_skill_v1
  kind: prompt
  domain: editor
  agents:
  - editor_in_chief
  priority: 70
  enabled_by_default: true
---

# 风格体验解析

解析用户对风格的体验偏好，将其转化为可执行的生成约束。

## 规则

1. **偏好提取**：从用户的风格描述中提取可量化的风格维度（正式度/密度/节奏/情感浓度等）。
2. **示例驱动**：当用户提供风格参考文本时，从参考文本中提取风格特征，优先于文字描述。
3. **冲突检测**：当用户的多个风格偏好互相矛盾时（如"简洁"与"细腻"），标记冲突并请求用户澄清。
4. **渐进调整**：风格约束的调整应渐进式进行，每次调整幅度不超过一个维度级别。
5. **可验证性**：转化后的风格约束必须可通过文本分析工具验证，禁止无法验证的主观描述。
