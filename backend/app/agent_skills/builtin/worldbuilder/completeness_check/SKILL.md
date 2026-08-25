---
name: completeness-check
description: 检查世界观构建的完整度
version: '1'
x-loreweft:
  id: completeness_check
  display_name: 完整性检查
  format_version: open_skill_v1
  kind: prompt
  domain: worldbuilder
  agents:
  - worldbuilder
  priority: 60
  enabled_by_default: true
  triggers:
    tabs:
    - overview
---

# 完整性检查

检查世界观设定的覆盖完整性，识别缺失的关键设定。

## 规则

1. **必填项检查**：每个世界观类别（力量体系/社会结构/地理环境/历史背景）必须有至少一条核心设定。
2. **关联完整性**：每条角色设定必须关联至少一个地点和组织；每条规则必须关联至少一个角色或事件。
3. **空白领域识别**：识别已提及但未详细设定的领域（如正文中提到某城市但无设定条目），标记为待补充。
4. **深度评估**：对每条设定评估其展开深度（仅名称/有描述/有规则/有案例），浅层设定建议深化。
5. **优先级排序**：缺失设定按叙事紧迫性排序——即将出场 > 近期伏笔 > 远期背景。
