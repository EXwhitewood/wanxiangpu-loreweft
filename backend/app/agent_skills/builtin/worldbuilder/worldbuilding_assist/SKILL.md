---
name: worldbuilding-assist
description: 辅助作者构建世界规则、人物、地点和伏笔
version: '1'
x-loreweft:
  id: worldbuilding_assist
  display_name: 世界观构建
  format_version: open_skill_v1
  kind: prompt
  domain: worldbuilder
  agents:
  - worldbuilder
  priority: 80
  enabled_by_default: true
  triggers:
    tabs:
    - overview
    - rules
    - characters
    - locations
    - foreshadowing
---

# 世界观辅助

为世界观构建提供辅助指导，确保设定体系的完整性和深度。

## 规则

1. **设定层次化**：世界观设定分为核心规则（不可违反）、衍生规则（由核心推导）、表面现象（读者可见）三个层次，必须明确标注。
2. **内部自洽**：所有设定条目之间不得存在逻辑矛盾，新增设定必须通过一致性检查。
3. **冰山原则**：作者掌握的设定深度应远大于展示给读者的部分，确保世界有探索空间。
4. **设定服务叙事**：每条世界观设定必须服务于至少一个叙事功能（制造冲突/限制选择/提供伏笔），纯装饰性设定应压缩。
5. **跨标签关联**：角色、地点、规则之间的关联必须显式标注，避免孤立设定。
