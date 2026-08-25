---
name: cross-tab-memory
description: 确保世界观各标签页间的信息一致性
version: '1'
x-loreweft:
  id: cross_tab_memory
  display_name: 跨标签记忆
  format_version: open_skill_v1
  kind: prompt
  domain: worldbuilder
  agents:
  - worldbuilder
  priority: 55
  enabled_by_default: true
---

# 跨标签记忆

维护世界观各标签页之间的交叉引用和记忆一致性。

## 规则

1. **交叉引用自动建立**：当在一个标签页中引用另一个标签页的实体时，自动建立双向引用。
2. **变更通知**：任一标签页的设定变更，必须通知所有引用该设定的其他标签页。
3. **孤儿检测**：定期检测无任何交叉引用的孤立设定条目，标记为待关联或待删除。
4. **引用完整性**：删除设定条目前，必须检查是否有其他条目引用它，有引用时禁止删除或提示级联影响。
5. **记忆衰减**：长期未被任何正文或推导链引用的设定条目，降低其优先级但不删除，保留备用。
