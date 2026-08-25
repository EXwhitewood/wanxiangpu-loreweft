---
name: pacing-repair
description: 修复商业节奏、爽点推进和章节钩子问题
version: '1'
x-loreweft:
  id: pacing_repair
  display_name: 节奏修复
  format_version: open_skill_v1
  kind: prompt
  domain: fbi
  agents:
  - fbi_pacing_repair
  priority: 75
  enabled_by_default: true
  triggers:
    issue_types:
    - rhythm_uniformity
    - pacing_too_slow
    - pacing_too_fast
    - missing_hook
---

# 节奏修复

针对叙事节奏问题（节奏单一、过慢、过快、缺少钩子）进行定向修复。

## 规则

1. **节奏诊断先行**：修复前必须先诊断节奏问题的具体类型和位置，不得盲目调整。
2. **手段匹配问题**：节奏过慢用压缩冗余、加速叙事解决；节奏过快用补充细节、增加过渡解决；节奏单一用句式变化、段落长短交替解决。
3. **钩子补充**：检测到缺少钩子时，在场景结尾补充悬念、疑问或未解决的紧张感，不得使用廉价悬念。
4. **全局视角**：节奏修复必须考虑前后文的节奏分布，避免局部修复导致全局节奏失衡。
5. **修复后验证**：修复后必须重新评估节奏指标，确认问题已解决且未引入新问题。
