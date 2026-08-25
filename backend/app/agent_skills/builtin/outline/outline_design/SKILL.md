---
name: outline-design
description: 设计完整小说大纲，包括章节结构和场景节拍
version: '1'
x-loreweft:
  id: outline_design
  display_name: 大纲设计
  format_version: open_skill_v1
  kind: prompt
  domain: outline
  agents:
  - outline_architect
  priority: 80
  enabled_by_default: true
---

# 大纲设计

指导大纲的整体设计，确保故事结构合理、节奏得当。

## 规则

1. **三幕结构基础**：大纲必须遵循建置-对抗-解决的基本三幕结构，允许变体但不得缺失核心阶段。
2. **转折点锚定**：每个幕的转折点必须明确标注，转折点之间至少间隔 2 个场景。
3. **主线清晰**：大纲必须有一条可追溯的主线，所有支线最终回归主线或明确收束。
4. **伏笔-回收配对**：大纲中每条伏笔必须标注其预期回收位置，未配对的伏笔不得进入大纲。
5. **弹性预留**：大纲应预留 15-20% 的弹性空间，允许生成过程中的即兴调整。
