---
name: derivation-chain-validation
description: 校验世界观规则的推导链是否自洽
version: '1'
x-loreweft:
  id: derivation_chain_validation
  display_name: 推导链校验
  format_version: open_skill_v1
  kind: prompt
  domain: worldbuilder
  agents:
  - worldbuilder
  priority: 65
  enabled_by_default: true
  triggers:
    tabs:
    - rules
---

# 推导链验证

验证世界观规则之间的推导链是否逻辑自洽、无循环依赖。

## 规则

1. **推导链可追溯**：每条衍生规则必须能追溯到其依赖的核心规则，推导路径必须显式记录。
2. **禁止循环依赖**：规则A依赖规则B，规则B不得反过来依赖规则A，检测并打破循环依赖。
3. **推导步骤验证**：每一步推导必须逻辑有效，不得跳跃推理或引入未声明的假设。
4. **核心规则不可推导**：标记为核心规则的条目不得由其他规则推导得出，必须作为公理存在。
5. **变更影响分析**：修改任一规则时，自动标记所有受影响的下游推导链，提示需要重新验证。
