---
name: pacing-structure
description: 规划章节节奏和压力曲线
version: '1'
x-loreweft:
  id: pacing_structure
  display_name: 节奏结构
  format_version: open_skill_v1
  kind: prompt
  domain: outline
  agents:
  - outline_architect
  priority: 60
  enabled_by_default: false
---

# 节奏结构

从宏观层面规划故事的节奏结构，确保张弛有度。

## 规则

1. **张弛交替**：大纲层面的节奏必须遵循紧张-舒缓的交替模式，连续紧张段落后必须有缓冲。
2. **高潮递进**：故事的高潮点应逐级递进，后续高潮的强度不得低于前一个。
3. **低谷功能**：节奏低谷不是浪费篇幅，必须承担信息铺垫、角色深化或伏笔埋设的功能。
4. **节奏可视化**：大纲应附带节奏曲线图，直观展示紧张度随章节的变化趋势。
5. **默认关闭**：此技能默认关闭，适用于长篇叙事或用户对节奏有明确要求时启用。
