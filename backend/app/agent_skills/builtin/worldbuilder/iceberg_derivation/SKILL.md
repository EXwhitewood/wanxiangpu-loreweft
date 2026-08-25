---
name: iceberg-derivation
description: 从表面信息推导深层世界观设定
version: '1'
x-loreweft:
  id: iceberg_derivation
  display_name: 冰山推导
  format_version: open_skill_v1
  kind: prompt
  domain: worldbuilder
  agents:
  - worldbuilder
  priority: 85
  enabled_by_default: true
  triggers:
    tabs:
    - rules
    - overview
---

# 冰山推导

从核心设定出发，自动推导出深层世界观内容，实现冰山模型。

## 规则

1. **核心到表面**：从核心规则出发，逐层推导出衍生规则和表面现象，确保表面现象有深层逻辑支撑。
2. **推导深度控制**：默认推导深度为 3 层（核心→衍生→表面），超过 3 层需人工确认。
3. **多路径推导**：同一核心规则可通过不同路径推导出不同的表面现象，鼓励多路径推导以丰富世界观。
4. **一致性约束**：所有推导路径的结果必须互相兼容，不得产生矛盾。
5. **读者视角标注**：推导结果必须标注"读者可见"或"仅作者可见"，确保信息释放的可控性。
