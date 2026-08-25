---
name: chapter-splitting
description: 将大纲拆分为章节和场景节拍
version: '1'
x-loreweft:
  id: chapter_splitting
  display_name: 章节拆分
  format_version: open_skill_v1
  kind: prompt
  domain: outline
  agents:
  - outline_architect
  priority: 75
  enabled_by_default: true
---

# 章节拆分

指导章节的合理拆分，确保每个章节具有独立的叙事价值。

## 规则

1. **章节完整性**：每个章节必须包含至少一个完整的叙事单元（一个场景或一组连续场景），不得在场景中间断章。
2. **章节钩子**：每章结尾必须设置钩子（悬念/疑问/转折），驱动读者进入下一章。
3. **字数指导**：章节字数应在 3000-5000 字范围内，超出范围需评估是否拆分或合并。
4. **节奏分布**：相邻章节的节奏应有变化，避免连续多章同节奏（如连续多章都是高潮或都是铺垫）。
5. **信息量均衡**：每章的信息释放量应大致均衡，避免某章信息过载而另一章信息稀薄。
